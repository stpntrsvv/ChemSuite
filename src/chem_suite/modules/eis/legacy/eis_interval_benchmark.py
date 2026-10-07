"""Truth-aware calibration of covariance, bootstrap, and profile intervals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .eis_core import build_bounds_and_guess, estimate_dataset_scale, fit_circuit, parameter_names
from .eis_io import load_eis_file
from .eis_uncertainty import parametric_bootstrap, profile_likelihood, residual_bootstrap


def parameter_status(*, estimate, low, high, bound_low, bound_high,
                     interval_hits_edge=False) -> str:
    """Conservative conditional-identifiability label for one interval."""
    values = (estimate, low, high)
    if any(value is None or not np.isfinite(value) for value in values):
        return "unbounded"
    estimate = float(estimate)
    bound_low, bound_high = float(bound_low), float(bound_high)
    if bound_low > 0 and estimate > 0:
        near_bound = estimate <= bound_low * 1.05 or estimate >= bound_high / 1.05
    else:
        span = max(bound_high - bound_low, 1e-30)
        near_bound = (
            abs(estimate - bound_low) <= 0.01 * span
            or abs(bound_high - estimate) <= 0.01 * span
        )
    if interval_hits_edge or near_bound:
        return "unbounded"
    relative_width = (float(high) - float(low)) / max(abs(float(estimate)), 1e-30)
    return "identified" if relative_width <= 1.0 else "weak"


def _summarize_methods(completed: list[dict], *, target_only=False) -> dict:
    methods = {}
    for method in ("covariance", "bootstrap", "parametric_bootstrap", "profile"):
        intervals = [
            interval
            for row in completed
            for interval in row.get("intervals", [])
            if interval["method"] == method
            and (
                not target_only
                or interval.get("parameter") in row.get("target_parameters", [])
            )
        ]
        covered = sum(interval["covers_truth"] for interval in intervals)
        identified = [
            interval for interval in intervals
            if interval["parameter_status"] == "identified"
        ]
        methods[method] = {
            "intervals": len(intervals),
            "covered": covered,
            "coverage": covered / len(intervals) if intervals else None,
            "identified_coverage": (
                sum(interval["covers_truth"] for interval in identified) / len(identified)
                if identified else None
            ),
            "statuses": {
                status: sum(interval["parameter_status"] == status for interval in intervals)
                for status in ("identified", "weak", "unbounded")
            },
        }
    return methods


def summarize_interval_rows(rows: list[dict]) -> dict:
    completed = [row for row in rows if row.get("success")]
    summary = {
        "requested": len(rows), "completed": len(completed),
        "methods": _summarize_methods(completed),
        "target_methods": _summarize_methods(completed, target_only=True),
        "strata": {},
    }
    for stratum in sorted({row.get("stratum") for row in rows if row.get("stratum")}):
        requested_rows = [row for row in rows if row.get("stratum") == stratum]
        stratum_rows = [row for row in completed if row.get("stratum") == stratum]
        summary["strata"][stratum] = {
            "requested": len(requested_rows), "completed": len(stratum_rows),
            "methods": _summarize_methods(stratum_rows),
            "target_methods": _summarize_methods(stratum_rows, target_only=True),
        }
    return summary


def run_interval_benchmark(
    truth_path: str | Path,
    output: str | Path,
    *,
    bootstrap_samples=20,
    parametric_bootstrap_samples=20,
    profile_parameters=(),
    profile_grid_points=21,
    profile_representatives_only=False,
    seed=0,
    restarts=1,
    max_evaluations=1200,
) -> dict:
    truth_path = Path(truth_path)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    truths = [
        json.loads(line)
        for line in truth_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows = []
    with output.open("w", encoding="utf-8") as handle:
        for index, truth in enumerate(truths):
            try:
                file_path = truth_path.parent / "spectra" / truth["file_name"]
                dataset = load_eis_file(file_path)
                circuit = truth["circuit"]
                names = parameter_names(circuit)
                true_values = dict(zip(names, truth["parameters"]))
                scale = estimate_dataset_scale(dataset.frequencies, dataset.z)
                low_bounds, high_bounds, _ = build_bounds_and_guess(circuit, scale)
                fit = fit_circuit(
                    dataset.frequencies,
                    dataset.z,
                    circuit,
                    scale,
                    fit_restarts=restarts,
                    restart_seed=seed + index * 10007,
                    max_fit_evaluations=max_evaluations,
                )
                if not fit.success or fit.model is None or fit.status == "BAD":
                    raise ValueError(
                        f"fit inadmissible: status={fit.status}, error={fit.error_message}"
                    )
                intervals = []
                for parameter_index, name in enumerate(names):
                    estimate = float(fit.model.parameters_[parameter_index])
                    sigma = float(fit.model.conf_[parameter_index])
                    ci_low, ci_high = estimate - 1.96 * sigma, estimate + 1.96 * sigma
                    intervals.append({
                        "method": "covariance", "parameter": name,
                        "truth": float(true_values[name]), "estimate": estimate,
                        "ci95_low": ci_low, "ci95_high": ci_high,
                        "covers_truth": ci_low <= true_values[name] <= ci_high,
                        "parameter_status": parameter_status(
                            estimate=estimate, low=ci_low, high=ci_high,
                            bound_low=low_bounds[parameter_index],
                            bound_high=high_bounds[parameter_index],
                        ),
                    })
                bootstrap = residual_bootstrap(
                    dataset.frequencies, dataset.z, circuit,
                    samples=bootstrap_samples, seed=seed + index * 10007,
                    restarts=restarts, max_evaluations=max_evaluations,
                )
                for parameter_index, item in enumerate(bootstrap["parameters"]):
                    low, high = item["ci95_low"], item["ci95_high"]
                    intervals.append({
                        "method": "bootstrap", "parameter": item["name"],
                        "truth": float(true_values[item["name"]]), "estimate": item["base"],
                        "ci95_low": low, "ci95_high": high,
                        "covers_truth": bool(
                            low is not None and high is not None
                            and low <= true_values[item["name"]] <= high
                        ),
                        "parameter_status": parameter_status(
                            estimate=item["base"], low=low, high=high,
                            bound_low=low_bounds[parameter_index],
                            bound_high=high_bounds[parameter_index],
                        ),
                    })
                parametric = parametric_bootstrap(
                    dataset.frequencies, dataset.z, circuit,
                    noise_fraction=float(truth["noise_fraction"]),
                    samples=parametric_bootstrap_samples,
                    seed=seed + index * 10007 + 7919,
                    restarts=restarts, max_evaluations=max_evaluations,
                )
                for parameter_index, item in enumerate(parametric["parameters"]):
                    low, high = item["ci95_low"], item["ci95_high"]
                    intervals.append({
                        "method": "parametric_bootstrap", "parameter": item["name"],
                        "truth": float(true_values[item["name"]]), "estimate": item["base"],
                        "ci95_low": low, "ci95_high": high,
                        "covers_truth": bool(
                            low is not None and high is not None
                            and low <= true_values[item["name"]] <= high
                        ),
                        "parameter_status": parameter_status(
                            estimate=item["base"], low=low, high=high,
                            bound_low=low_bounds[parameter_index],
                            bound_high=high_bounds[parameter_index],
                        ),
                    })
                requested_profiles = [
                    name for name in profile_parameters if name in names
                ]
                if profile_representatives_only and not truth.get("profile_representative", False):
                    requested_profiles = []
                for name in requested_profiles:
                    parameter_index = names.index(name)
                    profile = profile_likelihood(
                        dataset.frequencies, dataset.z, circuit, name,
                        grid_points=profile_grid_points, span_decades=1.0,
                        max_evaluations=max_evaluations, restarts=restarts,
                    )
                    low, high = profile["ci95_low"], profile["ci95_high"]
                    intervals.append({
                        "method": "profile", "parameter": name,
                        "truth": float(true_values[name]), "estimate": profile["base"],
                        "ci95_low": low, "ci95_high": high,
                        "covers_truth": bool(
                            low is not None and high is not None
                            and low <= true_values[name] <= high
                        ),
                        "interval_hits_grid_edge": profile["interval_hits_grid_edge"],
                        "parameter_status": parameter_status(
                            estimate=profile["base"], low=low, high=high,
                            bound_low=low_bounds[parameter_index],
                            bound_high=high_bounds[parameter_index],
                            interval_hits_edge=profile["interval_hits_grid_edge"],
                        ),
                    })
                row = {
                    "success": True, "file_name": truth["file_name"],
                    "circuit": circuit, "stratum": truth.get("stratum"),
                    "target_parameters": truth.get("target_parameters", []),
                    "fit_status": fit.status,
                    "bootstrap_acceptance_fraction": bootstrap["acceptance_fraction"],
                    "parametric_bootstrap_acceptance_fraction": parametric["acceptance_fraction"],
                    "intervals": intervals,
                }
            except Exception as exc:
                row = {
                    "success": False, "file_name": truth.get("file_name"),
                    "circuit": truth.get("circuit"), "stratum": truth.get("stratum"),
                    "target_parameters": truth.get("target_parameters", []),
                    "error": str(exc),
                }
            rows.append(row)
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
    summary = summarize_interval_rows(rows)
    output.with_name(output.stem + "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Calibrate parameter intervals on synthetic truth.")
    parser.add_argument("truth")
    parser.add_argument("--output", required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=20)
    parser.add_argument("--parametric-bootstrap-samples", type=int, default=20)
    parser.add_argument("--profile-parameter", action="append")
    parser.add_argument("--profile-grid-points", type=int, default=21)
    parser.add_argument("--profile-representatives-only", action="store_true")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--restarts", type=int, default=1)
    parser.add_argument("--max-evaluations", type=int, default=1200)
    args = parser.parse_args(argv)
    summary = run_interval_benchmark(
        args.truth, args.output,
        bootstrap_samples=args.bootstrap_samples,
        parametric_bootstrap_samples=args.parametric_bootstrap_samples,
        profile_parameters=tuple(args.profile_parameter or ()),
        profile_grid_points=args.profile_grid_points,
        profile_representatives_only=args.profile_representatives_only,
        seed=args.seed, restarts=args.restarts,
        max_evaluations=args.max_evaluations,
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
