"""Replay calibrated family decisions over a corpus with frozen bootstrap reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .eis_inference import build_inference_decision
from .eis_pipeline import analyze_file


DIFFUSION_GATE_CIRCUITS = (
    "L0-R0-p(R1,CPE0)",
    "L0-R0-p(R1,CPE0)-W0",
    "L0-R0-p(R1,CPE0)-Wo0",
    "L0-R0-p(R1,CPE0)-Ws0",
)


def run_gate_regression(
    files: list[Path],
    bootstrap_dir: str | Path,
    output: str | Path,
    *,
    restarts=1,
    seed=0,
    max_evaluations=1200,
) -> dict:
    bootstrap_dir = Path(bootstrap_dir)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with output.open("w", encoding="utf-8") as handle:
        for index, file_path in enumerate(files):
            analysis = analyze_file(
                file_path,
                circuits=DIFFUSION_GATE_CIRCUITS,
                fit_restarts=restarts,
                restart_seed=seed + index * 10007,
                max_fit_evaluations=max_evaluations,
            )
            fast = analysis.to_dict()
            report_path = bootstrap_dir / f"{file_path.name}.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            topology = report.get("topology_bootstrap") or report
            decision = build_inference_decision(
                fast_result=fast, topology=topology, drt=None, resolution=None
            )
            row = {
                "schema_version": 1,
                "analysis": "real_diffusion_gate_regression",
                "file": str(file_path.resolve()),
                "file_name": file_path.name,
                "success": bool(analysis.success),
                "best": fast.get("best"),
                "best_statistical": fast.get("best_statistical"),
                "model_evidence": fast.get("model_evidence"),
                "decision": decision,
                "topology_bootstrap": topology,
            }
            rows.append(row)
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
    completed = [row for row in rows if row["success"]]
    deltas = [
        (row["decision"].get("diffusion_gate") or {}).get(
            "diffusion_family_delta_bic"
        )
        for row in completed
        if (row["decision"].get("diffusion_gate") or {}).get(
            "diffusion_family_delta_bic"
        ) is not None
    ]
    summary = {
        "requested": len(rows),
        "completed": len(completed),
        "family_recommendations": sum(
            row["decision"].get("recommended_family") == "inductive_diffusion"
            for row in completed
        ),
        "refusals": sum(
            row["decision"].get("recommended_family") is None for row in completed
        ),
        "topology_recommendations": sum(
            row["decision"].get("recommended_topology") is not None for row in completed
        ),
        "kk_failures": sum(
            row["decision"].get("data_validity") == "FAIL" for row in completed
        ),
        "gate_evaluated": sum(
            bool((row["decision"].get("diffusion_gate") or {}).get("evaluated"))
            for row in completed
        ),
        "old_best_preserved": sum(row.get("best") is not None for row in completed),
        "best_statistical_preserved": sum(
            row.get("best_statistical") is not None for row in completed
        ),
        "diffusion_delta_bic_min": min(deltas) if deltas else None,
        "diffusion_delta_bic_max": max(deltas) if deltas else None,
        "diffusion_delta_bic_mean": sum(deltas) / len(deltas) if deltas else None,
    }
    output.with_name(output.stem + "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay the calibrated diffusion gate.")
    parser.add_argument("input_dir")
    parser.add_argument("--bootstrap-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--restarts", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-evaluations", type=int, default=1200)
    args = parser.parse_args(argv)
    input_dir = Path(args.input_dir)
    files = sorted(
        path for path in input_dir.glob("*.csv")
        if not path.name.endswith(("_0001_v1.csv", "_0023_v1.csv"))
    )
    summary = run_gate_regression(
        files,
        args.bootstrap_dir,
        args.output,
        restarts=args.restarts,
        seed=args.seed,
        max_evaluations=args.max_evaluations,
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
