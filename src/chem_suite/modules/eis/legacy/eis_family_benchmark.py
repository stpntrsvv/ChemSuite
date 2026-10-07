"""Synthetic calibration of topology- and family-level EIS inference."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from .eis_core import (
    choose_best_result,
    circuit_family,
    family_bic_evidence,
    fit_circuits,
)
from .eis_io import load_eis_file
from .eis_uncertainty import topology_bootstrap


DEFAULT_FAMILY_CANDIDATES = (
    "L0-R0-p(R1,CPE0)",
    "L0-R0-p(R1,CPE0)-W0",
    "L0-R0-p(R1,CPE0)-Wo0",
    "L0-R0-p(R1,CPE0)-Ws0",
)


def calibrated_diffusion_gate(
    row: dict,
    *,
    stability_threshold=0.90,
    family_delta_bic_threshold=10.0,
) -> dict:
    """Conservative experimental gate calibrated only for positive diffusion support."""
    family_supported = (
        row.get("winner_family") == "inductive_diffusion"
        and float(row.get("family_fraction") or 0.0) >= stability_threshold
        and row.get("diffusion_family_delta_bic") is not None
        and float(row["diffusion_family_delta_bic"]) >= family_delta_bic_threshold
    )
    return {
        "recommended_family": "inductive_diffusion" if family_supported else None,
        "recommended_topology": None,
        "status": "family_supported" if family_supported else "insufficient_information",
        "reason": (
            "diffusion family passes stability and BIC gates; boundary condition is uncalibrated"
            if family_supported
            else "calibrated positive diffusion gate not passed"
        ),
    }


def summarize_calibrated_gate(rows: list[dict]) -> dict:
    """Measure the frozen positive-only diffusion gate without retuning it."""
    completed = [row for row in rows if row.get("success")]
    positives = [
        row for row in completed if row.get("truth_family") == "inductive_diffusion"
    ]
    negatives = [
        row for row in completed if row.get("truth_family") != "inductive_diffusion"
    ]
    recommendations = [
        row for row in completed
        if calibrated_diffusion_gate(row)["recommended_family"] is not None
    ]
    correct = sum(
        calibrated_diffusion_gate(row)["recommended_family"] == row.get("truth_family")
        for row in recommendations
    )
    false_positives = sum(
        row.get("truth_family") != "inductive_diffusion"
        for row in recommendations
    )
    negative_count = len(negatives)
    zero_event_upper_95 = (
        1.0 - 0.05 ** (1.0 / negative_count)
        if negative_count and false_positives == 0 else None
    )
    return {
        "completed": len(completed),
        "positive_truths": len(positives),
        "negative_controls": negative_count,
        "recommendations": len(recommendations),
        "correct_recommendations": correct,
        "false_positive_recommendations": false_positives,
        "refusals": len(completed) - len(recommendations),
        "precision": correct / len(recommendations) if recommendations else None,
        "positive_recall": correct / len(positives) if positives else None,
        "false_positive_rate": (
            false_positives / negative_count if negative_count else None
        ),
        "zero_event_false_positive_rate_upper_95": zero_event_upper_95,
    }


def load_truth(path: str | Path) -> list[dict]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def summarize_rows(rows: list[dict], thresholds=(0.80, 0.90, 0.95)) -> dict:
    completed = [row for row in rows if row.get("success")]
    total = len(completed)
    threshold_rows = []
    for threshold in thresholds:
        topology_recommended = [
            row for row in completed if row["topology_fraction"] >= threshold
        ]
        family_recommended = [
            row for row in completed if row["family_fraction"] >= threshold
        ]
        threshold_rows.append({
            "threshold": float(threshold),
            "topology_recommendations": len(topology_recommended),
            "correct_topology_recommendations": sum(
                row["winner"] == row["truth_circuit"] for row in topology_recommended
            ),
            "false_topology_recommendations": sum(
                row["winner"] != row["truth_circuit"] for row in topology_recommended
            ),
            "family_recommendations": len(family_recommended),
            "correct_family_recommendations": sum(
                row["winner_family"] == row["truth_family"] for row in family_recommended
            ),
            "false_family_recommendations": sum(
                row["winner_family"] != row["truth_family"] for row in family_recommended
            ),
            "false_positive_diffusion_recommendations": sum(
                row["truth_family"] != "inductive_diffusion"
                and row["winner_family"] == "inductive_diffusion"
                for row in family_recommended
            ),
            "false_negative_diffusion_recommendations": sum(
                row["truth_family"] == "inductive_diffusion"
                and row["winner_family"] != "inductive_diffusion"
                for row in family_recommended
            ),
        })
    return {
        "requested": len(rows),
        "completed": total,
        "failures": len(rows) - total,
        "truth_in_bic_window": sum(row.get("truth_in_bic_window", False) for row in completed),
        "statistical_topology_correct": sum(
            row["best_statistical"] == row["truth_circuit"] for row in completed
        ),
        "bootstrap_topology_winner_correct": sum(
            row["winner"] == row["truth_circuit"] for row in completed
        ),
        "bootstrap_family_winner_correct": sum(
            row["winner_family"] == row["truth_family"] for row in completed
        ),
        "calibrated_diffusion_gate": summarize_calibrated_gate(completed),
        "thresholds": threshold_rows,
    }


def run_benchmark(
    truth_path: str | Path,
    output: str | Path,
    *,
    circuits=DEFAULT_FAMILY_CANDIDATES,
    bootstrap_samples=10,
    seed=0,
    restarts=1,
    max_evaluations=1200,
    filter_signal_fraction=None,
    filter_noise_fraction=None,
) -> dict:
    truth_path = Path(truth_path)
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    truths = load_truth(truth_path)
    if filter_signal_fraction is not None:
        truths = [
            truth for truth in truths
            if abs(float(truth.get("signal_fraction_requested", -1.0))
                   - float(filter_signal_fraction)) <= 1e-12
        ]
    if filter_noise_fraction is not None:
        truths = [
            truth for truth in truths
            if abs(float(truth.get("noise_fraction", -1.0))
                   - float(filter_noise_fraction)) <= 1e-12
        ]
    rows = []
    with destination.open("w", encoding="utf-8") as handle:
        for index, truth in enumerate(truths):
            started = time.monotonic()
            file_path = truth_path.parent / "spectra" / truth["file_name"]
            try:
                dataset = load_eis_file(file_path)
                fits = fit_circuits(
                    dataset.frequencies,
                    dataset.z,
                    circuits=circuits,
                    fit_restarts=restarts,
                    restart_seed=seed + index * 10007,
                    max_fit_evaluations=max_evaluations,
                )
                evidence = family_bic_evidence(fits)
                admissible = [
                    fit for fit in fits
                    if fit.success and fit.status != "BAD"
                ]
                finite_ranked = sorted(admissible, key=lambda fit: fit.bic)
                topology_bic_margin = (
                    float(finite_ranked[1].bic - finite_ranked[0].bic)
                    if len(finite_ranked) >= 2 else None
                )
                diffusion_bics = [
                    fit.bic for fit in admissible
                    if circuit_family(fit.circuit_string) == "inductive_diffusion"
                ]
                competing_bics = [
                    fit.bic for fit in admissible
                    if circuit_family(fit.circuit_string) != "inductive_diffusion"
                ]
                diffusion_family_delta_bic = (
                    float(min(competing_bics) - min(diffusion_bics))
                    if diffusion_bics and competing_bics else None
                )
                best_statistical = choose_best_result(fits).circuit_string
                if int(bootstrap_samples) > 0:
                    bootstrap = topology_bootstrap(
                        dataset.frequencies,
                        dataset.z,
                        circuits,
                        samples=bootstrap_samples,
                        seed=seed + index * 10007,
                        restarts=restarts,
                        max_evaluations=max_evaluations,
                    )
                    ranking = bootstrap["ranking"]
                    family_ranking = bootstrap["family_ranking"]
                    winner = ranking[0]["circuit"]
                    winner_family = family_ranking[0]["family"]
                    topology_fraction = ranking[0]["fraction_of_accepted"]
                    family_fraction = family_ranking[0]["fraction_of_accepted"]
                    selection_refusals = bootstrap["selection_refusals"]
                else:
                    winner = best_statistical
                    winner_family = circuit_family(best_statistical)
                    topology_fraction = 0.0
                    family_fraction = 0.0
                    selection_refusals = None
                row = {
                    "success": True,
                    "file_name": truth["file_name"],
                    "truth_circuit": truth["circuit"],
                    "truth_family": circuit_family(truth["circuit"]),
                    "best_statistical": best_statistical,
                    "supported_topologies": evidence["supported_topologies"],
                    "truth_in_bic_window": truth["circuit"] in evidence["supported_topologies"],
                    "topology_bic_margin": topology_bic_margin,
                    "diffusion_family_delta_bic": diffusion_family_delta_bic,
                    "winner": winner,
                    "topology_fraction": topology_fraction,
                    "winner_family": winner_family,
                    "family_fraction": family_fraction,
                    "selection_refusals": selection_refusals,
                    "bootstrap_samples": int(bootstrap_samples),
                    "elapsed_seconds": time.monotonic() - started,
                }
            except Exception as exc:
                row = {
                    "success": False,
                    "file_name": truth.get("file_name"),
                    "truth_circuit": truth.get("circuit"),
                    "truth_family": circuit_family(truth.get("circuit", "")),
                    "error": str(exc),
                    "elapsed_seconds": time.monotonic() - started,
                }
            rows.append(row)
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
    summary = summarize_rows(rows)
    summary_path = destination.with_name(destination.stem + "_summary.json")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Calibrate family inference on synthetic truth.")
    parser.add_argument("truth")
    parser.add_argument("--output", required=True)
    parser.add_argument("--candidate-circuit", action="append")
    parser.add_argument("--bootstrap-samples", type=int, default=10)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--restarts", type=int, default=1)
    parser.add_argument("--max-evaluations", type=int, default=1200)
    parser.add_argument("--filter-signal-fraction", type=float)
    parser.add_argument("--filter-noise-fraction", type=float)
    args = parser.parse_args(argv)
    summary = run_benchmark(
        args.truth,
        args.output,
        circuits=args.candidate_circuit or DEFAULT_FAMILY_CANDIDATES,
        bootstrap_samples=args.bootstrap_samples,
        seed=args.seed,
        restarts=args.restarts,
        max_evaluations=args.max_evaluations,
        filter_signal_fraction=args.filter_signal_fraction,
        filter_noise_fraction=args.filter_noise_fraction,
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
