"""Unified decision layer over fitting, bootstrap, DRT and resolution diagnostics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .eis_drt import analyze_drt
from .eis_io import load_eis_file
from .eis_pipeline import analyze_file
from .eis_uncertainty import topology_bootstrap


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def build_inference_decision(*, fast_result: dict, topology: dict | None = None,
                             drt: dict | None = None, resolution: dict | None = None) -> dict:
    best = fast_result.get("best") or {}
    kk = fast_result.get("kk") or {}
    data_status = kk.get("status", "UNKNOWN")
    best_statistical = best.get("circuit")
    model_evidence = fast_result.get("model_evidence") or {}

    if not fast_result.get("success") or not best_statistical:
        return {"verdict": "analysis_failed", "best_statistical": best_statistical,
                "recommended_reliable": None, "reason": fast_result.get("error_message") or "no successful fit"}
    if data_status == "FAIL":
        return {"verdict": "insufficient_information", "best_statistical": best_statistical,
                "recommended_reliable": None, "reason": "Kramers-Kronig validation failed",
                "data_validity": data_status, "fit_status": best.get("status"),
                "next_action": "check measurement stationarity, wiring and artifacts"}

    candidate_families = set((topology or {}).get("candidate_families") or [])
    calibrated_diffusion_competition = (
        "inductive_diffusion" in candidate_families and len(candidate_families) >= 2
    )
    raw_stable_topology = bool(topology and topology.get("stable_recommendation"))
    raw_stable_family = bool(topology and topology.get("stable_family_recommendation"))
    raw_family_recommendation = (
        topology.get("family_recommendation") if raw_stable_family else None
    )
    diffusion_delta_bic = model_evidence.get("diffusion_family_delta_bic")
    diffusion_gate_passed = bool(
        calibrated_diffusion_competition
        and raw_family_recommendation == "inductive_diffusion"
        and diffusion_delta_bic is not None
        and float(diffusion_delta_bic) >= 10.0
    )
    # The calibrated rule supports only the presence of the diffusion ECM
    # family. It neither selects W/Wo/Ws nor proves diffusion absent.
    stable_topology = (
        raw_stable_topology if not calibrated_diffusion_competition else False
    )
    recommendation = topology.get("recommendation") if stable_topology else None
    stable_family = (
        raw_stable_family
        if not calibrated_diffusion_competition
        else diffusion_gate_passed
    )
    recommended_family = (
        raw_family_recommendation if stable_family else None
    )
    information_gap = (resolution or {}).get("measurement_recommendation")
    unstable_peaks = []
    stable_peaks = []
    if drt and drt.get("stability"):
        peaks = drt["stability"].get("reference_peaks", [])
        stable_peaks = [peak for peak in peaks if peak.get("stable_at_90_percent")]
        unstable_peaks = [peak for peak in peaks if not peak.get("stable_at_90_percent")]
    drt_supports_recommendation = drt is None or bool(stable_peaks)

    if topology is None:
        verdict = "insufficient_information"
        reason = "reliable topology diagnostics were not run"
        next_action = "run reliable mode"
    elif stable_topology and best.get("status") != "BAD" and drt_supports_recommendation:
        verdict = "recommended"
        reason = "topology passed bootstrap stability gate"
        next_action = None
    elif stable_topology and not drt_supports_recommendation:
        verdict = "models_indistinguishable"
        reason = "topology is bootstrap-stable but no DRT time region passed the stability gate"
        next_action = "run targeted resolution diagnostics before interpreting the circuit"
    elif information_gap and information_gap.get("insufficient_data"):
        verdict = "insufficient_information"
        reason = information_gap.get("insufficiency_type", "localized information gap")
        next_action = information_gap.get("message")
    else:
        verdict = "models_indistinguishable"
        reason = (topology or {}).get("reason", "reliable topology evidence is unavailable")
        next_action = "run or extend targeted resolution diagnostics" if unstable_peaks else "collect independent or repeated measurements"

    if verdict != "recommended":
        recommendation = None
    family_status = "supported" if stable_family else ("unstable" if topology is not None else "not_evaluated")
    topology_status = "supported" if stable_topology else (
        "models_indistinguishable" if stable_family else "unstable"
    )
    if stable_family and not stable_topology and verdict == "models_indistinguishable":
        reason = (
            "diffusion ECM family passed calibrated bootstrap and BIC gates; "
            "boundary condition remains uncalibrated"
            if diffusion_gate_passed
            else "ECM family is bootstrap-stable; exact topology is selection-unstable"
        )
    elif calibrated_diffusion_competition and not diffusion_gate_passed:
        reason = "calibrated positive diffusion gate not passed"
    return {
        "verdict": verdict,
        "best_statistical": best_statistical,
        "recommended_reliable": recommendation,
        "recommended_family": recommended_family,
        "recommended_topology": recommendation,
        "supported_topologies": list(model_evidence.get("supported_topologies") or []),
        "family_status": family_status,
        "topology_status": topology_status,
        "data_validity": data_status,
        "fit_status": best.get("status"),
        "reason": reason,
        "diffusion_gate": {
            "evaluated": calibrated_diffusion_competition,
            "passed": diffusion_gate_passed,
            "family_stability_threshold": 0.90,
            "family_delta_bic_threshold": 10.0,
            "diffusion_family_delta_bic": diffusion_delta_bic,
            "positive_only": True,
        },
        "next_action": next_action,
        "information_gap": information_gap,
        "resolved_time_regions": [] if not drt else [
            {
                "frequency_hz": peak["reference_frequency_hz"],
                "stable": peak["stable_at_90_percent"],
                "worst_match_fraction": peak["worst_condition_match_fraction"],
            }
            for peak in (drt.get("stability") or {}).get("reference_peaks", [])
        ],
    }


def run_inference(file_path, *, mode="fast", channel=None, circuits=None, bootstrap_samples=30,
                  drt_stability_samples=30, seed=0, max_evaluations=2000,
                  topology_report=None, drt_report=None, resolution_report=None):
    circuits = None if circuits is None else list(dict.fromkeys(circuits))
    analysis = analyze_file(file_path, channel=channel, circuits=circuits,
                            fit_restarts=3 if mode == "reliable" else 1,
                            restart_seed=seed, max_fit_evaluations=max_evaluations)
    fast_payload = analysis.to_dict()
    topology = _read_json(topology_report)
    if topology and "topology_bootstrap" in topology:
        topology = topology["topology_bootstrap"]
    drt = _read_json(drt_report)
    resolution = _read_json(resolution_report)

    if mode == "reliable" and analysis.success:
        dataset = load_eis_file(file_path, channel=channel)
        if topology is None:
            bootstrap_circuits = [fit.circuit_string for fit in analysis.fits]
            if len(bootstrap_circuits) >= 2:
                topology = topology_bootstrap(
                    dataset.frequencies, dataset.z, bootstrap_circuits,
                    samples=bootstrap_samples, seed=seed, restarts=1,
                    max_evaluations=max_evaluations,
                )
            else:
                topology = {
                    "stable_recommendation": False,
                    "stable_family_recommendation": False,
                    "reason": "topology stability needs at least two candidate circuits",
                }
        if drt is None:
            drt = analyze_drt(file_path, stability_samples=drt_stability_samples, seed=seed)

    decision = build_inference_decision(fast_result=fast_payload, topology=topology,
                                        drt=drt, resolution=resolution)
    return {
        "schema_version": 1,
        "analysis": "unified_eis_inference",
        "mode": mode,
        "file": str(Path(file_path).resolve()),
        "decision": decision,
        "fast_analysis": fast_payload,
        "topology_bootstrap": topology,
        "drt": drt,
        "resolution": resolution,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Unified reliable EIS inference.")
    parser.add_argument("file")
    parser.add_argument("--output", required=True)
    parser.add_argument("--mode", choices=("fast", "reliable"), default="fast")
    parser.add_argument("--channel", help="Impedance channel to analyze.")
    parser.add_argument("--circuit", action="append")
    parser.add_argument("--bootstrap-samples", type=int, default=30)
    parser.add_argument("--drt-stability-samples", type=int, default=30)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-evaluations", type=int, default=2000)
    parser.add_argument("--topology-report", help="Reuse an existing topology-bootstrap JSON")
    parser.add_argument("--drt-report", help="Reuse an existing DRT JSON")
    parser.add_argument("--resolution-report", help="Attach a calibrated resolution-map JSON")
    args = parser.parse_args(argv)
    result = run_inference(
        args.file, mode=args.mode, channel=args.channel, circuits=args.circuit,
        bootstrap_samples=args.bootstrap_samples, drt_stability_samples=args.drt_stability_samples,
        seed=args.seed, max_evaluations=args.max_evaluations,
        topology_report=args.topology_report, drt_report=args.drt_report,
        resolution_report=args.resolution_report,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
