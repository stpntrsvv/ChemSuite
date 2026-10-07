"""Apply frequency-window and characteristic-support gates to interval results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .eis_core import estimate_dataset_scale, fit_circuit
from .eis_identifiability import (
    bias_aware_status,
    characteristic_support,
    frequency_window_stability,
)
from .eis_io import load_eis_file


def summarize_window_rows(rows: list[dict]) -> dict:
    summary = {"requested": len(rows), "completed": sum(row.get("success", False) for row in rows),
               "strata": {}}
    for stratum in sorted({row.get("stratum") for row in rows if row.get("stratum")}):
        selected = [row for row in rows if row.get("stratum") == stratum]
        intervals = [
            interval for row in selected if row.get("success")
            for interval in row.get("intervals", [])
            if interval.get("parameter") in row.get("target_parameters", [])
        ]
        by_method = {}
        for method in sorted({interval["method"] for interval in intervals}):
            method_intervals = [item for item in intervals if item["method"] == method]
            before = sum(item["parameter_status"] == "identified" for item in method_intervals)
            after = sum(item["bias_aware_status"] == "identified" for item in method_intervals)
            covered_after = [
                item for item in method_intervals if item["bias_aware_status"] == "identified"
            ]
            by_method[method] = {
                "intervals": len(method_intervals),
                "identified_before": before, "identified_after": after,
                "identified_retention": after / before if before else None,
                "identified_coverage_after": (
                    sum(item["covers_truth"] for item in covered_after) / len(covered_after)
                    if covered_after else None
                ),
            }
        summary["strata"][stratum] = {
            "requested": len(selected),
            "completed": sum(row.get("success", False) for row in selected),
            "methods": by_method,
        }
    return summary


def run_window_benchmark(truth_path: str | Path, interval_results: str | Path,
                         output: str | Path, *, trim_fractions=(0.10, 0.20),
                         max_fold_change=1.5, restarts=3, max_evaluations=2000,
                         seed=0) -> dict:
    truth_path, interval_results, output = map(Path, (truth_path, interval_results, output))
    truths = {
        item["file_name"]: item
        for item in (
            json.loads(line) for line in truth_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    original_rows = [
        json.loads(line) for line in interval_results.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with output.open("w", encoding="utf-8") as handle:
        for index, original in enumerate(original_rows):
            row = dict(original)
            truth = truths[row["file_name"]]
            if row.get("success"):
                dataset = load_eis_file(truth_path.parent / "spectra" / row["file_name"])
                base = fit_circuit(
                    dataset.frequencies, dataset.z, row["circuit"],
                    estimate_dataset_scale(dataset.frequencies, dataset.z),
                    fit_restarts=restarts, restart_seed=seed + index * 10007,
                    max_fit_evaluations=max_evaluations,
                )
                if not base.success or base.model is None or base.status == "BAD":
                    row = {**row, "success": False, "error": "window benchmark base fit inadmissible"}
                else:
                    support = characteristic_support(
                        dataset.frequencies, row["circuit"], base.model.parameters_
                    )
                    stability = frequency_window_stability(
                        dataset.frequencies, dataset.z, row["circuit"],
                        base.model.parameters_, trim_fractions=trim_fractions,
                        max_fold_change=max_fold_change, restarts=restarts,
                        max_evaluations=max_evaluations, seed=seed + index * 10007,
                    )
                    for interval in row["intervals"]:
                        name = interval["parameter"]
                        characteristic = support.get(name)
                        window = stability["parameters"][name]
                        interval["frequency_window_stable"] = window["stable"]
                        interval["max_window_fold_change"] = window["max_fold_change"]
                        interval["characteristic_supported"] = (
                            None if characteristic is None else characteristic["supported"]
                        )
                        interval["bias_aware_status"] = bias_aware_status(
                            interval["parameter_status"],
                            window_stable=window["stable"],
                            characteristic_supported=interval["characteristic_supported"],
                        )
                    row["frequency_window_stability"] = stability
                    row["characteristic_support"] = support
            rows.append(row)
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    summary = summarize_window_rows(rows)
    output.with_name(output.stem + "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("truth")
    parser.add_argument("interval_results")
    parser.add_argument("--output", required=True)
    parser.add_argument("--trim-fraction", type=float, action="append")
    parser.add_argument("--max-fold-change", type=float, default=1.5)
    parser.add_argument("--restarts", type=int, default=3)
    parser.add_argument("--max-evaluations", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    print(json.dumps(run_window_benchmark(
        args.truth, args.interval_results, args.output,
        trim_fractions=tuple(args.trim_fraction or (0.10, 0.20)),
        max_fold_change=args.max_fold_change, restarts=args.restarts,
        max_evaluations=args.max_evaluations, seed=args.seed,
    ), ensure_ascii=False))


if __name__ == "__main__":
    main()
