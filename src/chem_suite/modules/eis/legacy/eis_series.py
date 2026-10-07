"""Series-level diagnostics for EIS results across SOC, cells and conditions."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
from collections import Counter, defaultdict

import numpy as np
from scipy.interpolate import UnivariateSpline
from scipy.stats import spearmanr


def infer_series_metadata(file_name: str) -> dict:
    lg = re.search(r"SOC-?(\d+)", file_name, flags=re.IGNORECASE)
    if file_name.lower().startswith("08e_") and lg:
        return {"series_id": "lg_mj1_discharge", "cell_id": "lg_mj1", "soc": float(lg.group(1)), "direction": "discharge"}
    if file_name.lower().startswith("08i_") and lg:
        return {"series_id": "lg_mj1_charge", "cell_id": "lg_mj1", "soc": float(lg.group(1)), "direction": "charge"}
    cell = re.match(r"ID(\d+)\.csv$", file_name, flags=re.IGNORECASE)
    if cell:
        return {"series_id": "nmc811_21700_soc30", "cell_id": f"ID{int(cell.group(1)):02d}", "soc": 30.0, "direction": "fixed_soc"}
    return {"series_id": "unclassified", "cell_id": file_name, "soc": None, "direction": "unknown"}


def load_jsonl(path: str | Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _pooled_topology(rows: list[tuple[dict, dict]]) -> tuple[str | None, list[dict]]:
    """Pool per-spectrum BIC evidence, while keeping reliability as a hard gate."""
    by_circuit = defaultdict(lambda: {"fits": 0, "non_bad": 0, "deltas": [], "statuses": Counter()})
    for _, result in rows:
        fits = [fit for fit in result.get("fits", []) if fit.get("success") is not False and _finite(fit.get("bic"))]
        admissible = [fit for fit in fits if fit.get("status") != "BAD"] or fits
        if not admissible:
            continue
        reference_bic = min(float(fit["bic"]) for fit in admissible)
        for fit in fits:
            circuit = fit.get("circuit")
            if not circuit:
                continue
            item = by_circuit[circuit]
            item["fits"] += 1
            item["statuses"][fit.get("status", "NONE")] += 1
            if fit.get("status") != "BAD":
                item["non_bad"] += 1
                item["deltas"].append(float(fit["bic"]) - reference_bic)

    evidence = []
    observations = len(rows)
    for circuit, item in by_circuit.items():
        deltas = item["deltas"]
        evidence.append(
            {
                "circuit": circuit,
                "coverage": item["fits"] / observations,
                "non_bad_coverage": item["non_bad"] / observations,
                "aggregate_delta_bic": None if not deltas else float(sum(deltas)),
                "mean_delta_bic": None if not deltas else float(np.mean(deltas)),
                "median_delta_bic": None if not deltas else float(np.median(deltas)),
                "status_counts": dict(item["statuses"]),
            }
        )
    if not evidence:
        return None, evidence
    best_coverage = max(item["non_bad_coverage"] for item in evidence)
    coverage_floor = max(0.0, best_coverage - 0.15)
    eligible = [item for item in evidence if item["non_bad_coverage"] >= coverage_floor and item["mean_delta_bic"] is not None]
    winner = min(eligible, key=lambda item: (item["mean_delta_bic"], -item["non_bad_coverage"], item["circuit"]))
    evidence.sort(key=lambda item: (item is not winner, -item["non_bad_coverage"], item["circuit"]))
    return winner["circuit"], evidence


def _fit_for_circuit(result: dict, circuit: str) -> dict | None:
    return next(
        (
            fit
            for fit in result.get("fits", [])
            if fit.get("circuit") == circuit and fit.get("success") is not False and fit.get("status") != "BAD"
        ),
        None,
    )


def _smooth_trajectory(name: str, points: list[tuple[float, float]]) -> dict:
    grouped = defaultdict(list)
    for soc, value in points:
        grouped[soc].append(value)
    x = np.asarray(sorted(grouped), dtype=float)
    values = np.asarray([np.median(grouped[soc]) for soc in x], dtype=float)
    positive_log_scale = not (name.startswith("CPE") and name.endswith("_1")) and np.all(values > 0)
    transformed = np.log(values) if positive_log_scale else values.copy()
    fitted = transformed.copy()
    method = "insufficient_unique_soc"
    if len(x) >= 4 and np.std(transformed) > 0:
        standardized = (transformed - np.mean(transformed)) / np.std(transformed)
        spline = UnivariateSpline(x, standardized, k=min(3, len(x) - 1), s=0.35 * len(x))
        fitted = spline(x) * np.std(transformed) + np.mean(transformed)
        method = "cubic_smoothing_spline"
    smooth_values = np.exp(fitted) if positive_log_scale else fitted
    residual = transformed - fitted
    return {
        "parameter": name,
        "unique_soc_points": len(x),
        "scale": "log" if positive_log_scale else "linear",
        "method": method,
        "smoothing_factor_per_point": 0.35 if method == "cubic_smoothing_spline" else None,
        "rmse_on_model_scale": float(np.sqrt(np.mean(residual**2))),
        "curve": [{"soc": float(soc), "value": float(value)} for soc, value in zip(x, smooth_values)],
    }


def analyze_result_series(results: list[dict]) -> dict:
    groups = defaultdict(list)
    for result in results:
        metadata = result.get("series_metadata") or infer_series_metadata(result.get("file_name", ""))
        groups[metadata["series_id"]].append((metadata, result))

    summaries = []
    for series_id, rows in sorted(groups.items()):
        circuits = Counter((result.get("best") or {}).get("circuit", "NONE") for _, result in rows)
        statuses = Counter((result.get("best") or {}).get("status", "NONE") for _, result in rows)
        kk_statuses = Counter((result.get("kk") or {}).get("status", "NONE") for _, result in rows)
        dominant_circuit, dominant_count = circuits.most_common(1)[0]
        pooled_circuit, pooled_evidence = _pooled_topology(rows)
        trajectories = []
        parameter_points = defaultdict(list)
        for metadata, result in rows:
            best = result.get("best") or {}
            if best.get("circuit") != dominant_circuit or metadata.get("soc") is None:
                continue
            for parameter in best.get("parameters", []):
                value = parameter.get("value")
                if _finite(value):
                    parameter_points[parameter["name"]].append((float(metadata["soc"]), float(value)))

        for name, points in sorted(parameter_points.items()):
            points.sort()
            soc = np.asarray([point[0] for point in points], dtype=float)
            values = np.asarray([point[1] for point in points], dtype=float)
            if len(points) >= 3 and len(np.unique(soc)) >= 3 and len(np.unique(values)) >= 2:
                rho, p_value = spearmanr(soc, values)
            else:
                rho, p_value = np.nan, np.nan
            relative_jumps = []
            for previous, current in zip(values[:-1], values[1:]):
                relative_jumps.append(abs(current - previous) / max(abs(previous), abs(current), 1e-30))
            trajectories.append(
                {
                    "parameter": name,
                    "points": len(points),
                    "spearman_soc_rho": None if not np.isfinite(rho) else float(rho),
                    "spearman_p_value": None if not np.isfinite(p_value) else float(p_value),
                    "median_adjacent_relative_jump": None if not relative_jumps else float(np.median(relative_jumps)),
                    "minimum": float(np.min(values)),
                    "maximum": float(np.max(values)),
                }
            )

        pooled_points = defaultdict(list)
        if pooled_circuit:
            for metadata, result in rows:
                if metadata.get("soc") is None:
                    continue
                fit = _fit_for_circuit(result, pooled_circuit)
                if not fit:
                    continue
                for parameter in fit.get("parameters", []):
                    if _finite(parameter.get("value")):
                        pooled_points[parameter["name"]].append((float(metadata["soc"]), float(parameter["value"])))
        pooled_trajectories = [
            _smooth_trajectory(name, points) for name, points in sorted(pooled_points.items())
        ]

        summaries.append(
            {
                "series_id": series_id,
                "observations": len(rows),
                "cells": len({metadata["cell_id"] for metadata, _ in rows}),
                "soc_values": sorted({metadata["soc"] for metadata, _ in rows if metadata.get("soc") is not None}),
                "topology_counts": dict(circuits),
                "dominant_topology": dominant_circuit,
                "dominant_topology_fraction": dominant_count / len(rows),
                "pooled_topology": pooled_circuit,
                "pooled_topology_policy": "within 0.15 of best non-BAD coverage, minimize mean delta-BIC",
                "pooled_topology_evidence": pooled_evidence,
                "fit_status_counts": dict(statuses),
                "kk_status_counts": dict(kk_statuses),
                "dominant_topology_parameter_trajectories": trajectories,
                "pooled_parameter_trajectories": pooled_trajectories,
            }
        )
    return {"schema_version": 2, "analysis": "pooled_empirical_series", "series": summaries}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Summarize topology and parameter stability across an EIS series.")
    parser.add_argument("results", help="JSONL output from eis_cli.py")
    parser.add_argument("--output", required=True, help="Destination JSON report")
    args = parser.parse_args(argv)
    report = analyze_result_series(load_jsonl(args.results))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
