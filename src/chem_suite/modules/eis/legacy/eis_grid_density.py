"""Final benchmark of Wo window stability versus frequency-grid density."""

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
from .eis_wo_guardband import CIRCUIT, TARGET_PARAMETERS, upper_edge_distance_decades


POINT_COUNTS = (25, 31, 41, 51, 61, 81, 101)
TRUTH_DISTANCES_DECADES = (0.5, 0.7, 1.0)
NOISE_FRACTIONS = (0.005, 0.01, 0.02)
SEEDS = (20260901, 20260902, 20260903, 20260904, 20260905)
PARAMETER_PROFILES = (
    {"name": "small_r0", "r0": 0.5, "strength": 10.0},
    {"name": "balanced", "r0": 8.0, "strength": 40.0},
    {"name": "weak_contrast", "r0": 30.0, "strength": 15.0},
)
MIN_FREQUENCY = 0.02
MAX_FREQUENCY = 20.0
SPAN_DECADES = math.log10(MAX_FREQUENCY / MIN_FREQUENCY)
REFERENCE_GUARD_DECADES = 0.4
MAX_FOLD_CHANGE = 1.5

TRIM_RULES = {
    "trim_10": ("drop_low_0.1", "drop_high_0.1"),
    "trim_20": ("drop_low_0.2", "drop_high_0.2"),
    "combined": (
        "drop_low_0.1",
        "drop_high_0.1",
        "drop_low_0.2",
        "drop_high_0.2",
    ),
}

DENSITY_CRITERIA = {
    "minimum_completed_fraction": 1.0,
    "minimum_scenarios": 100,
    "minimum_retention": 0.90,
    "minimum_accurate_retention": 0.90,
    "minimum_each_noise_retention": 0.90,
    "maximum_truth_fold_error": 1.5,
}


def density_scenarios() -> list[dict]:
    """Return the frozen final factorial map."""
    scenarios = []
    for points in POINT_COUNTS:
        for profile in PARAMETER_PROFILES:
            for distance in TRUTH_DISTANCES_DECADES:
                characteristic = MAX_FREQUENCY / (10.0 ** distance)
                tau = 1.0 / (2.0 * math.pi * characteristic)
                for noise in NOISE_FRACTIONS:
                    for seed in SEEDS:
                        scenarios.append({
                            "points": points,
                            "points_per_decade": (points - 1) / SPAN_DECADES,
                            "profile_name": profile["name"],
                            "parameters": [
                                profile["r0"], profile["strength"], tau
                            ],
                            "truth_upper_distance_decades": distance,
                            "characteristic_frequency_hz": characteristic,
                            "noise_fraction": noise,
                            "seed": seed,
                        })
    return scenarios


def generate_density_corpus(output_dir: str | Path) -> Path:
    """Generate the final frozen density-map spectra and truth manifest."""
    output = Path(output_dir)
    spectra_dir = output / "spectra"
    spectra_dir.mkdir(parents=True, exist_ok=True)
    truth_path = output / "truth.jsonl"
    with truth_path.open("w", encoding="utf-8") as manifest:
        for index, scenario in enumerate(density_scenarios(), start=1):
            frequencies = np.logspace(
                math.log10(MAX_FREQUENCY),
                math.log10(MIN_FREQUENCY),
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
                f"{index:04d}_p{scenario['points']}_"
                f"{scenario['profile_name']}_"
                f"d{scenario['truth_upper_distance_decades']:.2f}_"
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
                "min_frequency": MIN_FREQUENCY,
                "max_frequency": MAX_FREQUENCY,
                "outlier_fraction": 0.0,
                **scenario,
            }
            manifest.write(
                json.dumps(truth, ensure_ascii=False, allow_nan=False) + "\n"
            )
    return truth_path


def stability_by_trim_rule(
    stability: dict,
    base_parameters,
    circuit: str = CIRCUIT,
    *,
    max_fold_change: float = MAX_FOLD_CHANGE,
) -> dict[str, bool]:
    """Evaluate the same fitted variants under each frozen trim subset."""
    names = parameter_names(circuit)
    base = np.asarray(base_parameters, dtype=float)
    variants = {row["window"]: row for row in stability["variants"]}
    results = {}
    for rule, required_windows in TRIM_RULES.items():
        stable = True
        for window in required_windows:
            variant = variants.get(window)
            if (
                variant is None
                or not variant["accepted"]
                or variant["parameters"] is None
            ):
                stable = False
                break
            values = np.asarray(variant["parameters"], dtype=float)
            for name in TARGET_PARAMETERS:
                index = names.index(name)
                if base[index] <= 0 or values[index] <= 0:
                    stable = False
                    break
                fold = max(
                    values[index] / base[index], base[index] / values[index]
                )
                if fold > float(max_fold_change):
                    stable = False
                    break
            if not stable:
                break
        results[rule] = stable
    return results


def _rule_metrics(rows: list[dict], rule: str) -> dict:
    completed = [row for row in rows if row.get("success")]
    passes = [row for row in completed if row["rule_passes"][rule]]
    accurate = [
        row for row in passes
        if row.get("maximum_truth_fold_error") is not None
        and row["maximum_truth_fold_error"]
        <= DENSITY_CRITERIA["maximum_truth_fold_error"]
    ]
    by_noise = {}
    for noise in NOISE_FRACTIONS:
        selected = [
            row for row in completed if row["noise_fraction"] == noise
        ]
        selected_passes = [
            row for row in selected if row["rule_passes"][rule]
        ]
        by_noise[f"{noise:g}"] = {
            "total": len(selected),
            "passes": len(selected_passes),
            "retention": (
                len(selected_passes) / len(selected) if selected else None
            ),
        }
    total = len(completed)
    return {
        "completed": total,
        "passes": len(passes),
        "retention": len(passes) / total if total else None,
        "accurate_passes": len(accurate),
        "accurate_retention": len(accurate) / total if total else None,
        "by_noise": by_noise,
    }


def density_passes_criteria(
    metrics: dict,
    *,
    requested: int,
    criteria=DENSITY_CRITERIA,
) -> bool:
    """Apply the final predeclared criterion within one density stratum."""
    noise_rates = [
        item["retention"] for item in metrics["by_noise"].values()
        if item["retention"] is not None
    ]
    return bool(
        requested >= criteria["minimum_scenarios"]
        and metrics["completed"] / requested
        >= criteria["minimum_completed_fraction"]
        and metrics["retention"] >= criteria["minimum_retention"]
        and metrics["accurate_retention"]
        >= criteria["minimum_accurate_retention"]
        and noise_rates
        and min(noise_rates) >= criteria["minimum_each_noise_retention"]
    )


def summarize_density_rows(rows: list[dict]) -> dict:
    """Summarize each point density separately and find a monotone threshold."""
    strata = {}
    for points in POINT_COUNTS:
        selected = [row for row in rows if row["points"] == points]
        rules = {}
        for rule in TRIM_RULES:
            metrics = _rule_metrics(selected, rule)
            metrics["passes_criteria"] = density_passes_criteria(
                metrics, requested=len(selected)
            )
            rules[rule] = metrics
        strata[str(points)] = {
            "points": points,
            "points_per_decade": (points - 1) / SPAN_DECADES,
            "requested": len(selected),
            "rules": rules,
        }

    minimum_qualified = None
    ordered = list(POINT_COUNTS)
    for index, points in enumerate(ordered):
        if all(
            strata[str(later)]["rules"]["combined"]["passes_criteria"]
            for later in ordered[index:]
        ):
            minimum_qualified = points
            break

    return {
        "requested": len(rows),
        "completed": sum(row.get("success", False) for row in rows),
        "reference_guard_decades": REFERENCE_GUARD_DECADES,
        "density_criteria": dict(DENSITY_CRITERIA),
        "strata": strata,
        "minimum_monotone_point_count": minimum_qualified,
        "minimum_monotone_points_per_decade": (
            (minimum_qualified - 1) / SPAN_DECADES
            if minimum_qualified is not None else None
        ),
    }


def run_density_benchmark(
    truth_path: str | Path,
    output: str | Path,
    *,
    restarts=3,
    max_evaluations=2000,
    fit_seed=20260901,
) -> dict:
    """Run the final density map using the unchanged fit/stability thresholds."""
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
                "points": truth["points"],
                "points_per_decade": truth["points_per_decade"],
                "profile_name": truth["profile_name"],
                "truth_upper_distance_decades": truth[
                    "truth_upper_distance_decades"
                ],
                "noise_fraction": truth["noise_fraction"],
                "data_seed": truth["seed"],
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
                        "rule_passes": {rule: False for rule in TRIM_RULES},
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
                    characteristic = support["Wo0_1"]["frequency"]
                    upper_distance = upper_edge_distance_decades(
                        characteristic, float(np.max(dataset.frequencies))
                    )
                    lower_supported = characteristic >= float(
                        np.min(dataset.frequencies)
                    )
                    stability = frequency_window_stability(
                        dataset.frequencies,
                        dataset.z,
                        CIRCUIT,
                        fit.model.parameters_,
                        trim_fractions=(0.10, 0.20),
                        max_fold_change=MAX_FOLD_CHANGE,
                        restarts=restarts,
                        max_evaluations=max_evaluations,
                        seed=seed,
                    )
                    stable_rules = stability_by_trim_rule(
                        stability, fit.model.parameters_
                    )
                    support_pass = bool(
                        lower_supported
                        and upper_distance >= REFERENCE_GUARD_DECADES
                    )
                    rule_passes = {
                        rule: bool(support_pass and stable)
                        for rule, stable in stable_rules.items()
                    }
                    fold_errors = [
                        max(
                            estimates[name] / truths_by_name[name],
                            truths_by_name[name] / estimates[name],
                        )
                        for name in TARGET_PARAMETERS
                    ]
                    row = {
                        "success": True,
                        **common,
                        "fit_status": fit.status,
                        "estimated_upper_distance_decades": upper_distance,
                        "lower_edge_supported": lower_supported,
                        "stability_rules": stable_rules,
                        "rule_passes": rule_passes,
                        "maximum_truth_fold_error": max(fold_errors),
                    }
            except Exception as exc:
                row = {"success": False, **common, "error": str(exc)}
            rows.append(row)
            handle.write(
                json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
            )
            handle.flush()
    summary = summarize_density_rows(rows)
    output.with_name(output.stem + "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir")
    parser.add_argument("--restarts", type=int, default=3)
    parser.add_argument("--max-evaluations", type=int, default=2000)
    parser.add_argument("--fit-seed", type=int, default=20260901)
    args = parser.parse_args(argv)
    output_dir = Path(args.output_dir)
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="Simulating circuit based on initial parameters"
        )
        warnings.filterwarnings(
            "ignore", message="overflow encountered in tanh", category=RuntimeWarning
        )
        truth_path = generate_density_corpus(output_dir)
        summary = run_density_benchmark(
            truth_path,
            output_dir / "results.jsonl",
            restarts=args.restarts,
            max_evaluations=args.max_evaluations,
            fit_seed=args.fit_seed,
        )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
