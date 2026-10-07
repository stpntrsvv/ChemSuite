"""Streaming batch runner for the unified EIS inference contract."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import time

from .eis_inference import run_inference
from .eis_pipeline import discover_input_files


SUMMARY_FIELDS = (
    "file", "success", "verdict", "best_statistical", "recommended_reliable",
    "recommended_family", "recommended_topology", "family_status", "topology_status",
    "data_validity", "fit_status", "reason", "next_action",
    "information_gap_type", "information_gap_min_hz", "information_gap_max_hz",
    "diffusion_gate_evaluated", "diffusion_gate_passed",
    "diffusion_gate_positive_only", "diffusion_family_delta_bic",
    "diffusion_family_stability_threshold", "diffusion_family_delta_bic_threshold",
    "stable_time_regions", "unstable_time_regions", "elapsed_seconds", "error_message",
)


def inference_summary(result: dict) -> dict:
    decision = result.get("decision") or {}
    gap = decision.get("information_gap") or {}
    region = gap.get("problem_region_hz") or []
    time_regions = decision.get("resolved_time_regions") or []
    diffusion_gate = decision.get("diffusion_gate") or {}
    return {
        "file": result.get("file", ""),
        "success": decision.get("verdict") != "analysis_failed",
        "verdict": decision.get("verdict", "analysis_failed"),
        "best_statistical": decision.get("best_statistical"),
        "recommended_reliable": decision.get("recommended_reliable"),
        "recommended_family": decision.get("recommended_family"),
        "recommended_topology": decision.get("recommended_topology"),
        "family_status": decision.get("family_status"),
        "topology_status": decision.get("topology_status"),
        "data_validity": decision.get("data_validity"),
        "fit_status": decision.get("fit_status"),
        "reason": decision.get("reason"),
        "next_action": decision.get("next_action"),
        "information_gap_type": gap.get("insufficiency_type"),
        "information_gap_min_hz": region[0] if len(region) >= 2 else None,
        "information_gap_max_hz": region[1] if len(region) >= 2 else None,
        "diffusion_gate_evaluated": diffusion_gate.get("evaluated"),
        "diffusion_gate_passed": diffusion_gate.get("passed"),
        "diffusion_gate_positive_only": diffusion_gate.get("positive_only"),
        "diffusion_family_delta_bic": diffusion_gate.get(
            "diffusion_family_delta_bic"
        ),
        "diffusion_family_stability_threshold": diffusion_gate.get(
            "family_stability_threshold"
        ),
        "diffusion_family_delta_bic_threshold": diffusion_gate.get(
            "family_delta_bic_threshold"
        ),
        "stable_time_regions": sum(bool(item.get("stable")) for item in time_regions),
        "unstable_time_regions": sum(not bool(item.get("stable")) for item in time_regions),
        "elapsed_seconds": result.get("elapsed_seconds"),
        "error_message": decision.get("reason") if decision.get("verdict") == "analysis_failed" else None,
    }


def failed_result(file_path: str, error: Exception, elapsed: float) -> dict:
    return {
        "schema_version": 1,
        "analysis": "unified_eis_inference",
        "file": str(Path(file_path).resolve()),
        "elapsed_seconds": elapsed,
        "decision": {
            "verdict": "analysis_failed",
            "best_statistical": None,
            "recommended_reliable": None,
            "reason": str(error),
        },
    }


def run_batch(inputs, output, *, mode="fast", recursive=False, output_format="jsonl",
              detail="decision", circuits=None, bootstrap_samples=30,
              drt_stability_samples=30, seed=0, max_evaluations=2000,
              fail_fast=False, quiet=False):
    files = discover_input_files(inputs, recursive=recursive)
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    counts = {}
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = None
        if output_format == "csv":
            writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS, extrasaction="ignore")
            writer.writeheader()
        for index, file_path in enumerate(files):
            started = time.monotonic()
            try:
                result = run_inference(
                    file_path, mode=mode, circuits=circuits,
                    bootstrap_samples=bootstrap_samples,
                    drt_stability_samples=drt_stability_samples,
                    seed=seed + index * 10007,
                    max_evaluations=max_evaluations,
                )
                result["elapsed_seconds"] = time.monotonic() - started
            except Exception as exc:
                result = failed_result(file_path, exc, time.monotonic() - started)
            summary = inference_summary(result)
            verdict = summary["verdict"]
            counts[verdict] = counts.get(verdict, 0) + 1
            if writer:
                writer.writerow(summary)
            else:
                payload = result if detail == "full" else {
                    "schema_version": 1, "analysis": "unified_eis_inference_summary", **summary
                }
                handle.write(json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
            if not quiet:
                print(f"[{index + 1}/{len(files)}] {Path(file_path).name}: {verdict}")
            if verdict == "analysis_failed" and fail_fast:
                break
    return {"discovered": len(files), "written": sum(counts.values()), "verdict_counts": counts,
            "output": str(destination.resolve())}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Streaming batch unified EIS inference.")
    parser.add_argument("inputs", nargs="+", help="Files or directories")
    parser.add_argument("--output", required=True)
    parser.add_argument("--mode", choices=("fast", "reliable"), default="fast")
    parser.add_argument("--format", choices=("jsonl", "csv"), default="jsonl")
    parser.add_argument("--detail", choices=("decision", "full"), default="decision")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--circuit", action="append")
    parser.add_argument("--bootstrap-samples", type=int, default=30)
    parser.add_argument("--drt-stability-samples", type=int, default=30)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-evaluations", type=int, default=2000)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    summary = run_batch(
        args.inputs, args.output, mode=args.mode, recursive=args.recursive,
        output_format=args.format, detail=args.detail,
        circuits=args.circuit,
        bootstrap_samples=args.bootstrap_samples,
        drt_stability_samples=args.drt_stability_samples,
        seed=args.seed, max_evaluations=args.max_evaluations,
        fail_fast=args.fail_fast, quiet=args.quiet,
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
