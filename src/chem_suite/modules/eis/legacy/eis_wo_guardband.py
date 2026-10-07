"""Calibrate and independently validate a Wo upper-edge guard band."""

from __future__ import annotations

import argparse
import csv
import json
import math
import warnings
from pathlib import Path

import numpy as np
from impedance.models.circuits import CustomCircuit

from .eis_core import estimate_dataset_scale, fit_circuit, parameter_names
from .eis_identifiability import characteristic_support, frequency_window_stability
from .eis_io import load_eis_file


CIRCUIT = "R0-Wo0"
TARGET_PARAMETERS = ("Wo0_0", "Wo0_1")
TRUTH_DISTANCES_DECADES = (-0.3, 0.0, 0.15, 0.3, 0.5, 0.7, 1.0, 1.3)
CANDIDATE_GUARDS_DECADES = (0.4, 0.6, 0.85, 1.1)
NOISE_FRACTIONS = (0.005, 0.01, 0.02)

CALIBRATION_DESIGN = {
    "seeds": (20260723, 20260724, 20260725),
    "frequency_grids": (
        {"name": "cal_a", "min_frequency": 1e-2, "max_frequency": 10.0, "points": 61},
        {"name": "cal_b", "min_frequency": 3e-2, "max_frequency": 30.0, "points": 51},
    ),
    "parameter_profiles": (
        {"name": "low_scale", "r0": 1.0, "strength": 5.0},
        {"name": "balanced", "r0": 5.0, "strength": 20.0},
        {"name": "high_scale", "r0": 20.0, "strength": 80.0},
    ),
}

HOLDOUT_DESIGN = {
    "seeds": (20260823, 20260824, 20260825),
    "frequency_grids": (
        {"name": "holdout_a", "min_frequency": 1e-1, "max_frequency": 100.0, "points": 41},
        {"name": "holdout_b", "min_frequency": 5e-3, "max_frequency": 5.0, "points": 81},
    ),
    "parameter_profiles": (
        {"name": "weak_contrast", "r0": 10.0, "strength": 5.0},
        {"name": "strong_contrast", "r0": 2.0, "strength": 30.0},
        {"name": "large_scale", "r0": 50.0, "strength": 150.0},
    ),
}

SELECTION_CRITERIA = {
    "minimum_completed": 1.0,
    "minimum_ineligible": 59,
    "minimum_eligible": 50,
    "maximum_false_passes": 0,
    "minimum_retention": 0.90,
    "minimum_accurate_retention": 0.90,
    "maximum_truth_fold_error": 1.5,
}


def _guard_key(value: float) -> str:
    return f"{float(value):g}"


def upper_edge_distance_decades(
    characteristic_frequency: float, max_frequency: float
) -> float:
    """Positive values lie inside the band; negative values lie above it."""
    if characteristic_frequency <= 0 or max_frequency <= 0:
        raise ValueError("frequencies must be positive")
    return math.log10(float(max_frequency) / float(characteristic_frequency))


def split_design(split: str) -> dict:
    if split == "calibration":
        return CALIBRATION_DESIGN
    if split == "holdout":
        return HOLDOUT_DESIGN
    raise ValueError(f"Unknown split: {split}")


def wo_guardband_scenarios(
    split: str,
    *,
    distances=TRUTH_DISTANCES_DECADES,
    noise_fractions=NOISE_FRACTIONS,
) -> list[dict]:
    """Return the predeclared factorial design for one independent split."""
    design = split_design(split)
    scenarios = []
    for grid in design["frequency_grids"]:
        for profile in design["parameter_profiles"]:
            for distance in distances:
                characteristic = float(grid["max_frequency"]) / (10.0 ** float(distance))
                tau = 1.0 / (2.0 * math.pi * characteristic)
                for noise_fraction in noise_fractions:
                    for seed in design["seeds"]:
                        scenarios.append({
                            "split": split,
                            "grid_name": grid["name"],
                            "min_frequency": float(grid["min_frequency"]),
                            "max_frequency": float(grid["max_frequency"]),
                            "points": int(grid["points"]),
                            "profile_name": profile["name"],
                            "r0": float(profile["r0"]),
                            "strength": float(profile["strength"]),
                            "truth_upper_distance_decades": float(distance),
                            "characteristic_frequency_hz": characteristic,
                            "parameters": [float(profile["r0"]), float(profile["strength"]), tau],
                            "noise_fraction": float(noise_fraction),
                            "seed": int(seed),
                        })
    return scenarios


def generate_wo_guardband_corpus(output_dir: str | Path, *, split: str) -> Path:
    """Generate a frozen calibration or holdout corpus."""
    output = Path(output_dir)
    spectra_dir = output / "spectra"
    spectra_dir.mkdir(parents=True, exist_ok=True)
    truth_path = output / "truth.jsonl"
    with truth_path.open("w", encoding="utf-8") as manifest:
        for index, scenario in enumerate(wo_guardband_scenarios(split), start=1):
            frequencies = np.logspace(
                math.log10(scenario["max_frequency"]),
                math.log10(scenario["min_frequency"]),
                scenario["points"],
            )
            predicted = np.asarray(
                CustomCircuit(
                    CIRCUIT, initial_guess=scenario["parameters"]
                ).predict(frequencies, use_initial=True),
                dtype=complex,
            )
            rng = np.random.default_rng(scenario["seed"])
            sigma = (
                scenario["noise_fraction"]
                * np.maximum(np.abs(predicted), 1e-30)
                / math.sqrt(2.0)
            )
            measured = predicted + sigma * (
                rng.standard_normal(len(predicted))
                + 1j * rng.standard_normal(len(predicted))
            )
            file_name = (
                f"{index:04d}_{split}_{scenario['grid_name']}_"
                f"{scenario['profile_name']}_d{scenario['truth_upper_distance_decades']:+.2f}_"
                f"n{scenario['noise_fraction']:.4f}_s{scenario['seed']}.csv"
            )
            with (spectra_dir / file_name).open(
                "w", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.writer(handle)
                writer.writerow(("frequency", "real", "imag"))
                writer.writerows(zip(frequencies, measured.real, measured.imag))
            truth = {
                "schema_version": 1,
                "file_name": file_name,
                "circuit": CIRCUIT,
                "parameter_names": parameter_names(CIRCUIT),
                "target_parameters": list(TARGET_PARAMETERS),
                "outlier_fraction": 0.0,
                **scenario,
            }
            manifest.write(
                json.dumps(truth, ensure_ascii=False, allow_nan=False) + "\n"
            )
    return truth_path


def _zero_event_upper_bound(events: int, confidence: float = 0.95) -> float | None:
    if events <= 0:
        return None
    return 1.0 - (1.0 - float(confidence)) ** (1.0 / int(events))


def _candidate_metrics(rows: list[dict], guard: float) -> dict:
    key = _guard_key(guard)
    completed = [row for row in rows if row.get("success")]
    eligible = [
        row for row in completed
        if row["truth_upper_distance_decades"] >= float(guard)
    ]
    ineligible = [
        row for row in completed
        if row["truth_upper_distance_decades"] < float(guard)
    ]
    passes = [row for row in eligible if row["guard_passes"][key]]
    false_passes = [row for row in ineligible if row["guard_passes"][key]]
    maximum_truth_fold_error = SELECTION_CRITERIA["maximum_truth_fold_error"]
    accurate = [
        row for row in passes
        if row.get("maximum_truth_fold_error") is not None
        and row["maximum_truth_fold_error"] <= maximum_truth_fold_error
    ]
    return {
        "guard_decades": float(guard),
        "eligible": len(eligible),
        "passes": len(passes),
        "retention": len(passes) / len(eligible) if eligible else None,
        "accurate_passes": len(accurate),
        "accurate_retention": len(accurate) / len(eligible) if eligible else None,
        "ineligible": len(ineligible),
        "false_passes": len(false_passes),
        "false_pass_rate": (
            len(false_passes) / len(ineligible) if ineligible else None
        ),
        "false_pass_upper_95": (
            _zero_event_upper_bound(len(ineligible))
            if ineligible and not false_passes else None
        ),
    }


def candidate_passes_criteria(
    metrics: dict,
    *,
    completed_fraction: float,
    criteria=SELECTION_CRITERIA,
) -> bool:
    """Apply the frozen selection/validation criteria to one candidate."""
    return bool(
        completed_fraction >= criteria["minimum_completed"]
        and metrics["eligible"] >= criteria["minimum_eligible"]
        and metrics["ineligible"] >= criteria["minimum_ineligible"]
        and metrics["false_passes"] <= criteria["maximum_false_passes"]
        and metrics["retention"] is not None
        and metrics["retention"] >= criteria["minimum_retention"]
        and metrics["accurate_retention"] is not None
        and metrics["accurate_retention"] >= criteria["minimum_accurate_retention"]
    )


def summarize_guardband_rows(rows: list[dict]) -> dict:
    """Summarize all frozen guard candidates without selecting post-hoc axes."""
    completed = sum(row.get("success", False) for row in rows)
    completed_fraction = completed / len(rows) if rows else 0.0
    candidates = {}
    for guard in CANDIDATE_GUARDS_DECADES:
        metrics = _candidate_metrics(rows, guard)
        metrics["passes_criteria"] = candidate_passes_criteria(
            metrics, completed_fraction=completed_fraction
        )
        candidates[_guard_key(guard)] = metrics
    selected = next(
        (
            guard for guard in CANDIDATE_GUARDS_DECADES
            if candidates[_guard_key(guard)]["passes_criteria"]
        ),
        None,
    )
    return {
        "requested": len(rows),
        "completed": completed,
        "completed_fraction": completed_fraction,
        "selection_criteria": dict(SELECTION_CRITERIA),
        "candidates": candidates,
        "selected_guard_decades": selected,
    }


def validate_holdout_summary(summary: dict, selected_guard: float | None) -> dict:
    """Validate one calibration-selected guard without holdout reselection."""
    if selected_guard is None:
        return {
            "selected_guard_decades": None,
            "passed": False,
            "reason": "calibration_selected_no_guard",
        }
    key = _guard_key(selected_guard)
    metrics = summary["candidates"].get(key)
    if metrics is None:
        return {
            "selected_guard_decades": float(selected_guard),
            "passed": False,
            "reason": "selected_guard_not_in_frozen_candidates",
        }
    passed = candidate_passes_criteria(
        metrics, completed_fraction=summary["completed_fraction"]
    )
    return {
        "selected_guard_decades": float(selected_guard),
        "passed": passed,
        "reason": "criteria_passed" if passed else "holdout_criteria_failed",
        "metrics": metrics,
    }


def run_wo_guardband_benchmark(
    truth_path: str | Path,
    output: str | Path,
    *,
    trim_fractions=(0.10, 0.20),
    max_fold_change=1.5,
    restarts=3,
    max_evaluations=2000,
    fit_seed=20260723,
    selected_guard: float | None = None,
) -> dict:
    """Run one frozen split and optionally validate a preselected guard."""
    truth_path, output = Path(truth_path), Path(output)
    truths = [
        json.loads(line)
        for line in truth_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with output.open("w", encoding="utf-8") as handle:
        for index, truth in enumerate(truths):
            common = {
                "file_name": truth["file_name"],
                "split": truth["split"],
                "grid_name": truth["grid_name"],
                "profile_name": truth["profile_name"],
                "noise_fraction": truth["noise_fraction"],
                "data_seed": truth["seed"],
                "truth_upper_distance_decades": truth[
                    "truth_upper_distance_decades"
                ],
            }
            try:
                dataset = load_eis_file(
                    truth_path.parent / "spectra" / truth["file_name"]
                )
                seed = int(fit_seed) + index * 10007
                fit = fit_circuit(
                    dataset.frequencies,
                    dataset.z,
                    CIRCUIT,
                    estimate_dataset_scale(dataset.frequencies, dataset.z),
                    fit_restarts=restarts,
                    restart_seed=seed,
                    max_fit_evaluations=max_evaluations,
                )
                if not fit.success or fit.model is None:
                    raise ValueError(
                        f"base fit failed: status={fit.status}, "
                        f"error={fit.error_message}"
                    )
                if fit.status == "BAD":
                    row = {
                        "success": True,
                        **common,
                        "fit_status": fit.status,
                        "gate_refusal": "base_fit_bad",
                        "guard_passes": {
                            _guard_key(guard): False
                            for guard in CANDIDATE_GUARDS_DECADES
                        },
                        "maximum_truth_fold_error": None,
                    }
                else:
                    names = parameter_names(CIRCUIT)
                    estimates = dict(zip(names, map(float, fit.model.parameters_)))
                    truths_by_name = dict(
                        zip(names, map(float, truth["parameters"]))
                    )
                    support = characteristic_support(
                        dataset.frequencies, CIRCUIT, fit.model.parameters_
                    )
                    stability = frequency_window_stability(
                        dataset.frequencies,
                        dataset.z,
                        CIRCUIT,
                        fit.model.parameters_,
                        trim_fractions=trim_fractions,
                        max_fold_change=max_fold_change,
                        restarts=restarts,
                        max_evaluations=max_evaluations,
                        seed=seed,
                    )
                    characteristic = support["Wo0_1"]["frequency"]
                    upper_distance = upper_edge_distance_decades(
                        characteristic, float(np.max(dataset.frequencies))
                    )
                    lower_supported = characteristic >= float(
                        np.min(dataset.frequencies)
                    )
                    stable = all(
                        stability["parameters"][name]["stable"]
                        for name in TARGET_PARAMETERS
                    )
                    fold_errors = [
                        max(
                            estimates[name] / truths_by_name[name],
                            truths_by_name[name] / estimates[name],
                        )
                        for name in TARGET_PARAMETERS
                    ]
                    guard_passes = {
                        _guard_key(guard): bool(
                            stable and lower_supported
                            and upper_distance >= float(guard)
                        )
                        for guard in CANDIDATE_GUARDS_DECADES
                    }
                    row = {
                        "success": True,
                        **common,
                        "fit_status": fit.status,
                        "estimated_characteristic_frequency_hz": characteristic,
                        "estimated_upper_distance_decades": upper_distance,
                        "frequency_window_stable": stable,
                        "lower_edge_supported": lower_supported,
                        "guard_passes": guard_passes,
                        "maximum_truth_fold_error": max(fold_errors),
                        "target_fold_errors": dict(
                            zip(TARGET_PARAMETERS, fold_errors)
                        ),
                    }
            except Exception as exc:
                row = {"success": False, **common, "error": str(exc)}
            rows.append(row)
            handle.write(
                json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
            )
            handle.flush()
    summary = summarize_guardband_rows(rows)
    if selected_guard is not None:
        summary["holdout_validation"] = validate_holdout_summary(
            summary, selected_guard
        )
        summary["selected_guard_decades"] = float(selected_guard)
    output.with_name(output.stem + "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir")
    parser.add_argument("--split", choices=("calibration", "holdout"), required=True)
    parser.add_argument("--selection-summary")
    parser.add_argument("--trim-fraction", type=float, action="append")
    parser.add_argument("--max-fold-change", type=float, default=1.5)
    parser.add_argument("--restarts", type=int, default=3)
    parser.add_argument("--max-evaluations", type=int, default=2000)
    parser.add_argument("--fit-seed", type=int, default=20260723)
    args = parser.parse_args(argv)

    selected_guard = None
    if args.split == "holdout":
        if not args.selection_summary:
            parser.error("--selection-summary is required for holdout")
        calibration_summary = json.loads(
            Path(args.selection_summary).read_text(encoding="utf-8")
        )
        selected_guard = calibration_summary.get("selected_guard_decades")

    output_dir = Path(args.output_dir)
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="Simulating circuit based on initial parameters"
        )
        warnings.filterwarnings(
            "ignore", message="overflow encountered in tanh", category=RuntimeWarning
        )
        truth_path = generate_wo_guardband_corpus(output_dir, split=args.split)
        summary = run_wo_guardband_benchmark(
            truth_path,
            output_dir / "results.jsonl",
            trim_fractions=tuple(args.trim_fraction or (0.10, 0.20)),
            max_fold_change=args.max_fold_change,
            restarts=args.restarts,
            max_evaluations=args.max_evaluations,
            fit_seed=args.fit_seed,
            selected_guard=selected_guard,
        )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
