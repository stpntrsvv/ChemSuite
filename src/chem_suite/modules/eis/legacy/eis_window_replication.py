"""Replicate the frequency-window identifiability gate on synthetic truth."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np
from impedance.models.circuits import CustomCircuit

from .eis_core import estimate_dataset_scale, fit_circuit, parameter_names
from .eis_identifiability import characteristic_support, frequency_window_stability
from .eis_io import load_eis_file


DEFAULT_SEEDS = (20260718, 20260719, 20260720, 20260721, 20260722)
DEFAULT_NOISE_FRACTIONS = (0.005, 0.01, 0.02)
POSITION_ORDER = (
    "below",
    "low_edge",
    "low_inside",
    "inside",
    "high_inside",
    "high_edge",
    "above",
)


def characteristic_frequencies(min_frequency: float, max_frequency: float) -> dict[str, float]:
    """Return the frozen positions used around both measurement-band edges."""
    if min_frequency <= 0 or max_frequency <= min_frequency:
        raise ValueError("frequency bounds must satisfy 0 < min < max")
    return {
        "below": min_frequency / 10.0,
        "low_edge": min_frequency,
        "low_inside": min_frequency * 2.0,
        "inside": math.sqrt(min_frequency * max_frequency),
        "high_inside": max_frequency / 2.0,
        "high_edge": max_frequency,
        "above": max_frequency * 10.0,
    }


def _cpe_q(resistance: float, characteristic_frequency: float, alpha: float) -> float:
    return 1.0 / (
        float(resistance)
        * (2.0 * math.pi * float(characteristic_frequency)) ** float(alpha)
    )


def replication_scenarios(
    *,
    seeds=DEFAULT_SEEDS,
    noise_fractions=DEFAULT_NOISE_FRACTIONS,
    positions=POSITION_ORDER,
    min_frequency=1e-2,
    max_frequency=10.0,
) -> list[dict]:
    """Return the predeclared CPE/Wo truth matrix for gate replication."""
    frequencies_by_position = characteristic_frequencies(min_frequency, max_frequency)
    unknown = set(positions) - set(frequencies_by_position)
    if unknown:
        raise ValueError(f"Unknown characteristic positions: {sorted(unknown)}")
    scenarios = []
    for process_kind in ("cpe", "wo"):
        for position in positions:
            characteristic = frequencies_by_position[position]
            if process_kind == "cpe":
                resistance, alpha = 20.0, 0.82
                circuit = "R0-p(R1,CPE0)"
                parameters = [
                    5.0,
                    resistance,
                    _cpe_q(resistance, characteristic, alpha),
                    alpha,
                ]
                targets = ["R1", "CPE0_0", "CPE0_1"]
            else:
                circuit = "R0-Wo0"
                parameters = [
                    5.0,
                    20.0,
                    1.0 / (2.0 * math.pi * characteristic),
                ]
                targets = ["Wo0_0", "Wo0_1"]
            for noise_fraction in noise_fractions:
                for seed in seeds:
                    scenarios.append({
                        "process_kind": process_kind,
                        "characteristic_position": position,
                        "characteristic_frequency_hz": characteristic,
                        "expected_supported": position not in {"below", "above"},
                        "circuit": circuit,
                        "parameters": parameters,
                        "target_parameters": targets,
                        "noise_fraction": float(noise_fraction),
                        "seed": int(seed),
                    })
    return scenarios


def generate_window_replication_corpus(
    output_dir: str | Path,
    *,
    seeds=DEFAULT_SEEDS,
    noise_fractions=DEFAULT_NOISE_FRACTIONS,
    positions=POSITION_ORDER,
    points=61,
    min_frequency=1e-2,
    max_frequency=10.0,
) -> Path:
    """Generate the frozen replication spectra and JSONL truth manifest."""
    output = Path(output_dir)
    spectra_dir = output / "spectra"
    spectra_dir.mkdir(parents=True, exist_ok=True)
    frequencies = np.logspace(
        math.log10(max_frequency), math.log10(min_frequency), int(points)
    )
    truth_path = output / "truth.jsonl"
    scenarios = replication_scenarios(
        seeds=seeds,
        noise_fractions=noise_fractions,
        positions=positions,
        min_frequency=min_frequency,
        max_frequency=max_frequency,
    )
    with truth_path.open("w", encoding="utf-8") as manifest:
        for index, scenario in enumerate(scenarios, start=1):
            predicted = np.asarray(
                CustomCircuit(
                    scenario["circuit"], initial_guess=scenario["parameters"]
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
                f"{index:04d}_{scenario['process_kind']}_"
                f"{scenario['characteristic_position']}_"
                f"n{scenario['noise_fraction']:.4f}_s{scenario['seed']}.csv"
            )
            with (spectra_dir / file_name).open(
                "w", newline="", encoding="utf-8"
            ) as csv_handle:
                writer = csv.writer(csv_handle)
                writer.writerow(("frequency", "real", "imag"))
                writer.writerows(zip(frequencies, measured.real, measured.imag))
            truth = {
                "schema_version": 1,
                "file_name": file_name,
                **scenario,
                "parameter_names": parameter_names(scenario["circuit"]),
                "points": int(points),
                "min_frequency": float(min_frequency),
                "max_frequency": float(max_frequency),
                "outlier_fraction": 0.0,
            }
            manifest.write(
                json.dumps(truth, ensure_ascii=False, allow_nan=False) + "\n"
            )
    return truth_path


def _group_summary(rows: list[dict]) -> dict:
    completed = [row for row in rows if row.get("success")]
    parameters = [
        item
        for row in completed
        for item in row.get("target_diagnostics", [])
    ]
    support_values = {row["expected_supported"] for row in rows}
    expected_supported = (
        next(iter(support_values)) if len(support_values) == 1 else None
    )
    scenario_passes = sum(row.get("gate_pass", False) for row in completed)
    parameter_passes = sum(item["gate_pass"] for item in parameters)
    fold_errors = [
        item["estimate_fold_error"]
        for item in parameters
        if item.get("estimate_fold_error") is not None
    ]
    return {
        "requested": len(rows),
        "completed": len(completed),
        "expected_supported": expected_supported,
        "scenario_gate_passes": scenario_passes,
        "scenario_gate_pass_rate": (
            scenario_passes / len(completed) if completed else None
        ),
        "parameter_gate_passes": parameter_passes,
        "parameter_gate_pass_rate": (
            parameter_passes / len(parameters) if parameters else None
        ),
        "median_estimate_fold_error": (
            float(np.median(fold_errors)) if fold_errors else None
        ),
        "max_estimate_fold_error": max(fold_errors) if fold_errors else None,
    }


def summarize_replication_rows(rows: list[dict]) -> dict:
    """Summarize pass/fail behavior across process, position, and noise axes."""
    summary = {
        "requested": len(rows),
        "completed": sum(row.get("success", False) for row in rows),
        "groups": {},
    }
    for process_kind in ("cpe", "wo"):
        process_rows = [row for row in rows if row["process_kind"] == process_kind]
        process_summary = {"overall": _group_summary(process_rows), "positions": {}}
        for position in POSITION_ORDER:
            position_rows = [
                row for row in process_rows
                if row["characteristic_position"] == position
            ]
            if not position_rows:
                continue
            position_summary = {
                "overall": _group_summary(position_rows),
                "noise": {},
            }
            for noise in sorted({row["noise_fraction"] for row in position_rows}):
                selected = [
                    row for row in position_rows
                    if row["noise_fraction"] == noise
                ]
                position_summary["noise"][f"{noise:g}"] = _group_summary(selected)
            process_summary["positions"][position] = position_summary
        summary["groups"][process_kind] = process_summary

    outside = [
        row for row in rows
        if not row["expected_supported"] and row.get("success")
    ]
    edge = [
        row for row in rows
        if row["characteristic_position"] in {"low_edge", "high_edge"}
        and row.get("success")
    ]
    strict_interior = [
        row for row in rows
        if row["characteristic_position"] in {
            "low_inside", "inside", "high_inside"
        }
        and row.get("success")
    ]
    supported = [
        row for row in rows
        if row["expected_supported"] and row.get("success")
    ]
    outside_false_passes = sum(row["gate_pass"] for row in outside)
    summary["decision_metrics"] = {
        "outside_false_passes": outside_false_passes,
        "outside_completed": len(outside),
        "outside_false_pass_rate": (
            outside_false_passes / len(outside)
            if outside else None
        ),
        "outside_false_pass_upper_95": (
            1.0 - 0.05 ** (1.0 / len(outside))
            if outside and outside_false_passes == 0 else None
        ),
        "edge_passes": sum(row["gate_pass"] for row in edge),
        "edge_completed": len(edge),
        "edge_retention": (
            sum(row["gate_pass"] for row in edge) / len(edge)
            if edge else None
        ),
        "strict_interior_passes": sum(
            row["gate_pass"] for row in strict_interior
        ),
        "strict_interior_completed": len(strict_interior),
        "strict_interior_retention": (
            sum(row["gate_pass"] for row in strict_interior)
            / len(strict_interior)
            if strict_interior else None
        ),
        "supported_passes": sum(row["gate_pass"] for row in supported),
        "supported_completed": len(supported),
        "supported_retention": (
            sum(row["gate_pass"] for row in supported) / len(supported)
            if supported else None
        ),
    }
    return summary


def write_replication_summary(results_path: str | Path) -> dict:
    """Rebuild the compact summary from streamed JSONL without refitting."""
    results_path = Path(results_path)
    rows = [
        json.loads(line)
        for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    summary = summarize_replication_rows(rows)
    results_path.with_name(results_path.stem + "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return summary


def run_window_replication(
    truth_path: str | Path,
    output: str | Path,
    *,
    trim_fractions=(0.10, 0.20),
    max_fold_change=1.5,
    restarts=3,
    max_evaluations=2000,
    seed=20260718,
) -> dict:
    """Fit the truth corpus and apply the unchanged frequency-window gate."""
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
            try:
                dataset = load_eis_file(
                    truth_path.parent / "spectra" / truth["file_name"]
                )
                fit_seed = int(seed) + index * 10007
                fit = fit_circuit(
                    dataset.frequencies,
                    dataset.z,
                    truth["circuit"],
                    estimate_dataset_scale(dataset.frequencies, dataset.z),
                    fit_restarts=restarts,
                    restart_seed=fit_seed,
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
                        "file_name": truth["file_name"],
                        "process_kind": truth["process_kind"],
                        "characteristic_position": truth[
                            "characteristic_position"
                        ],
                        "characteristic_frequency_hz": truth[
                            "characteristic_frequency_hz"
                        ],
                        "expected_supported": truth["expected_supported"],
                        "noise_fraction": truth["noise_fraction"],
                        "data_seed": truth["seed"],
                        "fit_status": fit.status,
                        "gate_pass": False,
                        "gate_refusal": "base_fit_bad",
                        "target_diagnostics": [],
                    }
                    rows.append(row)
                    handle.write(
                        json.dumps(
                            row, ensure_ascii=False, allow_nan=False
                        ) + "\n"
                    )
                    handle.flush()
                    continue
                names = parameter_names(truth["circuit"])
                estimates = dict(zip(names, map(float, fit.model.parameters_)))
                true_values = dict(zip(names, map(float, truth["parameters"])))
                support = characteristic_support(
                    dataset.frequencies, truth["circuit"], fit.model.parameters_
                )
                stability = frequency_window_stability(
                    dataset.frequencies,
                    dataset.z,
                    truth["circuit"],
                    fit.model.parameters_,
                    trim_fractions=trim_fractions,
                    max_fold_change=max_fold_change,
                    restarts=restarts,
                    max_evaluations=max_evaluations,
                    seed=fit_seed,
                )
                diagnostics = []
                for name in truth["target_parameters"]:
                    estimate, true_value = estimates[name], true_values[name]
                    supported = support[name]["supported"]
                    stable = stability["parameters"][name]["stable"]
                    diagnostics.append({
                        "parameter": name,
                        "truth": true_value,
                        "estimate": estimate,
                        "estimate_fold_error": (
                            max(estimate / true_value, true_value / estimate)
                            if estimate > 0 and true_value > 0 else None
                        ),
                        "estimated_characteristic_frequency_hz": (
                            support[name]["frequency"]
                        ),
                        "characteristic_supported": supported,
                        "frequency_window_stable": stable,
                        "max_window_fold_change": (
                            stability["parameters"][name]["max_fold_change"]
                        ),
                        "gate_pass": bool(supported and stable),
                    })
                row = {
                    "success": True,
                    "file_name": truth["file_name"],
                    "process_kind": truth["process_kind"],
                    "characteristic_position": truth["characteristic_position"],
                    "characteristic_frequency_hz": truth[
                        "characteristic_frequency_hz"
                    ],
                    "expected_supported": truth["expected_supported"],
                    "noise_fraction": truth["noise_fraction"],
                    "data_seed": truth["seed"],
                    "fit_status": fit.status,
                    "gate_pass": all(item["gate_pass"] for item in diagnostics),
                    "target_diagnostics": diagnostics,
                    "frequency_window_stability": stability,
                    "characteristic_support": support,
                }
            except Exception as exc:
                row = {
                    "success": False,
                    "file_name": truth["file_name"],
                    "process_kind": truth["process_kind"],
                    "characteristic_position": truth["characteristic_position"],
                    "characteristic_frequency_hz": truth[
                        "characteristic_frequency_hz"
                    ],
                    "expected_supported": truth["expected_supported"],
                    "noise_fraction": truth["noise_fraction"],
                    "data_seed": truth["seed"],
                    "error": str(exc),
                }
            rows.append(row)
            handle.write(
                json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
            )
            handle.flush()
    summary = summarize_replication_rows(rows)
    output.with_name(output.stem + "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir")
    parser.add_argument("--seed", type=int, action="append")
    parser.add_argument("--noise", type=float, action="append")
    parser.add_argument("--points", type=int, default=61)
    parser.add_argument("--min-frequency", type=float, default=1e-2)
    parser.add_argument("--max-frequency", type=float, default=10.0)
    parser.add_argument("--trim-fraction", type=float, action="append")
    parser.add_argument("--max-fold-change", type=float, default=1.5)
    parser.add_argument("--restarts", type=int, default=3)
    parser.add_argument("--max-evaluations", type=int, default=2000)
    parser.add_argument("--fit-seed", type=int, default=20260718)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args(argv)
    output_dir = Path(args.output_dir)
    if args.summary_only:
        print(json.dumps(
            write_replication_summary(output_dir / "results.jsonl"),
            ensure_ascii=False,
        ))
        return
    truth_path = generate_window_replication_corpus(
        output_dir,
        seeds=tuple(args.seed or DEFAULT_SEEDS),
        noise_fractions=tuple(args.noise or DEFAULT_NOISE_FRACTIONS),
        points=args.points,
        min_frequency=args.min_frequency,
        max_frequency=args.max_frequency,
    )
    summary = run_window_replication(
        truth_path,
        output_dir / "results.jsonl",
        trim_fractions=tuple(args.trim_fraction or (0.10, 0.20)),
        max_fold_change=args.max_fold_change,
        restarts=args.restarts,
        max_evaluations=args.max_evaluations,
        seed=args.fit_seed,
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
