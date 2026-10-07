"""Residual bootstrap and profile-likelihood diagnostics for one EIS fit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import warnings

import numpy as np
from impedance.models.circuits import CustomCircuit
from scipy.optimize import least_squares

from .eis_core import (build_bounds_and_guess, choose_best_result, circuit_family, estimate_dataset_scale,
                      fit_circuit, fit_circuits, parameter_names)
from .eis_io import load_eis_file


def _predict(circuit, parameters, frequencies):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return CustomCircuit(circuit, initial_guess=list(parameters)).predict(frequencies)


def residual_bootstrap(frequencies, z, circuit, *, samples=100, seed=0, restarts=1, max_evaluations=2000, check_cancelled=None, parameter_overrides_by_circuit=None):
    scale = estimate_dataset_scale(frequencies, z)
    base = fit_circuit(frequencies, z, circuit, scale, parameter_overrides=(parameter_overrides_by_circuit or {}).get(circuit), fit_restarts=restarts,
                       max_fit_evaluations=max_evaluations, restart_seed=seed)
    if not base.success or base.model is None:
        raise ValueError(f"Base fit failed: {base.error_message}")
    predicted = base.model.predict(frequencies)
    residuals = z - predicted
    residuals = residuals - np.mean(residuals)
    rng = np.random.default_rng(seed)
    accepted = []
    statuses = {}
    for sample in range(int(samples)):
        if check_cancelled:
            check_cancelled()
        synthetic = predicted + residuals[rng.integers(0, len(residuals), len(residuals))]
        fitted = fit_circuit(frequencies, synthetic, circuit, estimate_dataset_scale(frequencies, synthetic),
                             parameter_overrides=(parameter_overrides_by_circuit or {}).get(circuit), fit_restarts=restarts, max_fit_evaluations=max_evaluations,
                             restart_seed=seed + sample + 1)
        statuses[fitted.status] = statuses.get(fitted.status, 0) + 1
        if fitted.success and fitted.model is not None and np.all(np.isfinite(fitted.model.parameters_)):
            accepted.append(np.asarray(fitted.model.parameters_, dtype=float))
    names = parameter_names(circuit)
    matrix = np.asarray(accepted, dtype=float)
    parameters = []
    for index, name in enumerate(names):
        values = matrix[:, index] if len(matrix) else np.asarray([])
        parameters.append({"name": name, "base": float(base.model.parameters_[index]),
                           "median": None if not len(values) else float(np.median(values)),
                           "ci95_low": None if not len(values) else float(np.quantile(values, 0.025)),
                           "ci95_high": None if not len(values) else float(np.quantile(values, 0.975))})
    return {"method": "centered_complex_residual_bootstrap", "requested": int(samples),
            "accepted": len(accepted), "acceptance_fraction": len(accepted) / max(int(samples), 1),
            "status_counts": statuses, "parameters": parameters}


def parametric_bootstrap(frequencies, z, circuit, *, noise_fraction, samples=100, seed=0,
                         restarts=1, max_evaluations=2000, check_cancelled=None, parameter_overrides_by_circuit=None):
    """Refit synthetic spectra under an explicit relative complex-Gaussian noise model."""
    if noise_fraction < 0:
        raise ValueError("noise_fraction must be non-negative")
    scale = estimate_dataset_scale(frequencies, z)
    base = fit_circuit(frequencies, z, circuit, scale, parameter_overrides=(parameter_overrides_by_circuit or {}).get(circuit), fit_restarts=restarts,
                       max_fit_evaluations=max_evaluations, restart_seed=seed)
    if not base.success or base.model is None:
        raise ValueError(f"Base fit failed: {base.error_message}")
    predicted = base.model.predict(frequencies)
    component_sigma = float(noise_fraction) * np.maximum(np.abs(predicted), 1e-30) / np.sqrt(2.0)
    rng = np.random.default_rng(seed)
    accepted, statuses = [], {}
    for sample in range(int(samples)):
        if check_cancelled:
            check_cancelled()
        noise = component_sigma * (
            rng.standard_normal(len(predicted)) + 1j * rng.standard_normal(len(predicted))
        )
        synthetic = predicted + noise
        fitted = fit_circuit(
            frequencies, synthetic, circuit, estimate_dataset_scale(frequencies, synthetic),
            parameter_overrides=(parameter_overrides_by_circuit or {}).get(circuit), fit_restarts=restarts, max_fit_evaluations=max_evaluations,
            restart_seed=seed + sample + 1,
        )
        statuses[fitted.status] = statuses.get(fitted.status, 0) + 1
        if fitted.success and fitted.model is not None and np.all(np.isfinite(fitted.model.parameters_)):
            accepted.append(np.asarray(fitted.model.parameters_, dtype=float))
    names = parameter_names(circuit)
    matrix = np.asarray(accepted, dtype=float)
    parameters = []
    for index, name in enumerate(names):
        values = matrix[:, index] if len(matrix) else np.asarray([])
        parameters.append({
            "name": name, "base": float(base.model.parameters_[index]),
            "median": None if not len(values) else float(np.median(values)),
            "ci95_low": None if not len(values) else float(np.quantile(values, 0.025)),
            "ci95_high": None if not len(values) else float(np.quantile(values, 0.975)),
        })
    return {
        "method": "relative_complex_parametric_bootstrap",
        "noise_fraction": float(noise_fraction), "requested": int(samples),
        "accepted": len(accepted),
        "acceptance_fraction": len(accepted) / max(int(samples), 1),
        "status_counts": statuses, "parameters": parameters,
    }


def topology_bootstrap(frequencies, z, circuits, *, samples=100, seed=0, restarts=1,
                       max_evaluations=2000, check_cancelled=None, parameter_overrides_by_circuit=None):
    circuits = list(dict.fromkeys(circuits))
    if len(circuits) < 2:
        raise ValueError("Topology bootstrap needs at least two candidate circuits.")
    candidate_families = sorted({circuit_family(circuit) for circuit in circuits})
    base_fits = fit_circuits(frequencies, z, circuits=circuits, parameter_overrides_by_circuit=parameter_overrides_by_circuit, fit_restarts=restarts,
                             restart_seed=seed, max_fit_evaluations=max_evaluations)
    base = choose_best_result(base_fits)
    predicted = base.model.predict(frequencies)
    residuals = z - predicted
    residuals = residuals - np.mean(residuals)
    rng = np.random.default_rng(seed)
    winners, family_winners, winner_statuses, failures = {}, {}, {}, 0
    margins = []
    rows = []
    for sample in range(int(samples)):
        if check_cancelled:
            check_cancelled()
        synthetic = predicted + residuals[rng.integers(0, len(residuals), len(residuals))]
        fits = fit_circuits(frequencies, synthetic, circuits=circuits, parameter_overrides_by_circuit=parameter_overrides_by_circuit, fit_restarts=restarts,
                            restart_seed=seed + (sample + 1) * 10007,
                            max_fit_evaluations=max_evaluations)
        try:
            winner = choose_best_result(fits, allow_bad_fallback=False)
        except ValueError:
            failures += 1
            rows.append({"sample": sample, "winner": None, "status": "REFUSED", "bic_margin": None})
            continue
        winners[winner.circuit_string] = winners.get(winner.circuit_string, 0) + 1
        family = circuit_family(winner.circuit_string)
        family_winners[family] = family_winners.get(family, 0) + 1
        winner_statuses[winner.status] = winner_statuses.get(winner.status, 0) + 1
        admissible_bic = sorted(fit.bic for fit in fits if fit.success and fit.status != "BAD" and np.isfinite(fit.bic))
        margin = float(admissible_bic[1] - admissible_bic[0]) if len(admissible_bic) >= 2 else None
        if margin is not None:
            margins.append(margin)
        rows.append({"sample": sample, "winner": winner.circuit_string, "winner_family": family,
                     "status": winner.status,
                     "bic_margin": margin})
    accepted = int(samples) - failures
    ranking = [
        {"circuit": circuit, "wins": winners.get(circuit, 0),
         "fraction_of_accepted": winners.get(circuit, 0) / max(accepted, 1)}
        for circuit in circuits
    ]
    ranking.sort(key=lambda item: (-item["wins"], item["circuit"]))
    family_ranking = [
        {
            "family": family,
            "wins": family_winners.get(family, 0),
            "fraction_of_accepted": family_winners.get(family, 0) / max(accepted, 1),
        }
        for family in candidate_families
    ]
    family_ranking.sort(key=lambda item: (-item["wins"], item["family"]))
    dominant_fraction = ranking[0]["fraction_of_accepted"] if ranking else 0.0
    dominant_family_fraction = family_ranking[0]["fraction_of_accepted"] if family_ranking else 0.0
    stable = dominant_fraction >= 0.90 and failures / max(int(samples), 1) <= 0.10
    family_competition = len(candidate_families) >= 2
    family_stable = (
        family_competition
        and dominant_family_fraction >= 0.90
        and failures / max(int(samples), 1) <= 0.10
    )
    family_recommendation = family_ranking[0]["family"] if family_stable else None
    return {"method": "conditional_residual_topology_bootstrap", "requested": int(samples),
            "generating_circuit": base.circuit_string, "generating_status": base.status,
            "selection_refusals": failures, "accepted_selections": accepted,
            "winner_status_counts": winner_statuses, "ranking": ranking,
            "family_ranking": family_ranking,
            "candidate_families": candidate_families,
            "family_competition": family_competition,
            "stability_threshold": 0.90, "stable_recommendation": stable,
            "recommendation": ranking[0]["circuit"] if stable else None,
            "stable_family_recommendation": family_stable,
            "family_recommendation": family_recommendation,
            "recommended_topology": ranking[0]["circuit"] if stable else None,
            "recommended_family": family_recommendation,
            "family_status": "supported" if family_stable else "unstable",
            "topology_status": "supported" if stable else "models_indistinguishable",
            "reason": "stable bootstrap winner" if stable else "candidate topologies are not selection-stable",
            "family_reason": (
                "stable bootstrap family"
                if family_stable
                else (
                    "family stability is conditional because no competing family was tested"
                    if not family_competition
                    else "candidate families are not selection-stable"
                )
            ),
            "median_bic_margin": None if not margins else float(np.median(margins)), "samples": rows,
            "interpretation": "conditional stability around the selected generating model, not posterior model probability"}


def profile_likelihood(frequencies, z, circuit, parameter, *, grid_points=41, span_decades=0.5,
                       max_evaluations=2000, restarts=3, check_cancelled=None, parameter_overrides_by_circuit=None):
    names = parameter_names(circuit)
    if parameter not in names:
        raise ValueError(f"Unknown parameter '{parameter}'. Available: {', '.join(names)}")
    fixed_index = names.index(parameter)
    scale = estimate_dataset_scale(frequencies, z)
    low, high, guess = build_bounds_and_guess(circuit, scale)
    if parameter_overrides_by_circuit:
        from .eis_core import apply_parameter_overrides
        low, high, guess = apply_parameter_overrides(circuit, low, high, guess, parameter_overrides_by_circuit.get(circuit))
    base = fit_circuit(frequencies, z, circuit, scale, parameter_overrides=(parameter_overrides_by_circuit or {}).get(circuit), fit_restarts=restarts,
                       max_fit_evaluations=max_evaluations)
    if not base.success or base.model is None:
        raise ValueError(f"Base fit failed: {base.error_message}")
    best = np.asarray(base.model.parameters_, dtype=float)
    center = best[fixed_index]
    if low[fixed_index] > 0:
        grid_low = max(low[fixed_index], center / 10**span_decades)
        grid_high = min(high[fixed_index], center * 10**span_decades)
        left_count = max(2, int(grid_points) // 2 + 1)
        right_count = max(2, int(grid_points) - left_count + 2)
        grid = np.r_[
            np.geomspace(grid_low, center, left_count),
            np.geomspace(center, grid_high, right_count)[1:],
        ]
    else:
        width = (high[fixed_index] - low[fixed_index]) * 0.25
        grid = np.linspace(max(low[fixed_index], center - width), min(high[fixed_index], center + width), grid_points)
    free = [index for index in range(len(names)) if index != fixed_index]
    denom = np.maximum(np.abs(z), 1e-30)
    points = []
    for fixed in grid:
        if check_cancelled:
            check_cancelled()
        def objective(values):
            if check_cancelled:
                check_cancelled()
            parameters = best.copy()
            parameters[fixed_index] = fixed
            parameters[free] = values
            residual = (z - _predict(circuit, parameters, frequencies)) / denom
            return np.r_[residual.real, residual.imag]
        if free:
            optimized = least_squares(objective, best[free], bounds=(np.asarray(low)[free], np.asarray(high)[free]),
                                      max_nfev=max_evaluations, ftol=1e-9, xtol=1e-9, gtol=1e-9)
            rss = float(np.sum(objective(optimized.x) ** 2))
            success = bool(optimized.success)
        else:
            rss = float(np.sum(objective([]) ** 2))
            success = True
        points.append({"value": float(fixed), "weighted_rss": rss, "success": success})
    minimum = min(point["weighted_rss"] for point in points)
    degrees = max(1, 2 * len(frequencies) - len(names))
    variance = minimum / degrees
    for point in points:
        point["delta_chi_square"] = (point["weighted_rss"] - minimum) / max(variance, 1e-30)
    threshold = 3.841
    minimum_index = min(
        range(len(points)), key=lambda index: points[index]["delta_chi_square"]
    )

    def crossing(inside, outside):
        x1, y1 = inside["value"], inside["delta_chi_square"]
        x2, y2 = outside["value"], outside["delta_chi_square"]
        if x1 > 0 and x2 > 0:
            x1, x2 = np.log(x1), np.log(x2)
            value = np.exp(x1 + (threshold - y1) * (x2 - x1) / max(y2 - y1, 1e-30))
        else:
            value = x1 + (threshold - y1) * (x2 - x1) / max(y2 - y1, 1e-30)
        return float(value)

    lower = points[0]["value"]
    lower_edge = True
    for index in range(minimum_index - 1, -1, -1):
        if points[index]["delta_chi_square"] > threshold:
            lower = crossing(points[index + 1], points[index])
            lower_edge = False
            break
    upper = points[-1]["value"]
    upper_edge = True
    for index in range(minimum_index + 1, len(points)):
        if points[index]["delta_chi_square"] > threshold:
            upper = crossing(points[index - 1], points[index])
            upper_edge = False
            break
    return {"method": "weighted_profile_likelihood", "parameter": parameter, "base": float(center),
            "confidence_level": 0.95, "threshold_delta_chi_square": 3.841,
            "ci95_low": float(lower), "ci95_high": float(upper),
            "interval_hits_grid_edge": bool(lower_edge or upper_edge),
            "points": points}


def analyze_uncertainty(file_path, circuit, *, bootstrap_samples=100, profile_parameter=None,
                        seed=0, restarts=1, max_evaluations=2000, candidate_circuits=None):
    dataset = load_eis_file(file_path)
    result = {"schema_version": 1, "analysis": "fit_uncertainty", "file": str(Path(file_path).resolve()),
              "circuit": circuit,
              "bootstrap": residual_bootstrap(dataset.frequencies, dataset.z, circuit, samples=bootstrap_samples,
                                               seed=seed, restarts=restarts, max_evaluations=max_evaluations)}
    if profile_parameter:
        result["profile_likelihood"] = profile_likelihood(dataset.frequencies, dataset.z, circuit, profile_parameter,
                                                           max_evaluations=max_evaluations, restarts=restarts)
    if candidate_circuits:
        result["topology_bootstrap"] = topology_bootstrap(
            dataset.frequencies, dataset.z, candidate_circuits, samples=bootstrap_samples,
            seed=seed, restarts=restarts, max_evaluations=max_evaluations
        )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bootstrap and profile-likelihood diagnostics for one EIS spectrum.")
    parser.add_argument("file")
    parser.add_argument("--circuit", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=100)
    parser.add_argument("--profile-parameter")
    parser.add_argument("--candidate-circuit", action="append",
                        help="Repeat for topology bootstrap across two or more circuits")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--restarts", type=int, default=1)
    parser.add_argument("--max-evaluations", type=int, default=2000)
    args = parser.parse_args(argv)
    report = analyze_uncertainty(args.file, args.circuit, bootstrap_samples=args.bootstrap_samples,
                                 profile_parameter=args.profile_parameter, seed=args.seed,
                                 restarts=args.restarts, max_evaluations=args.max_evaluations,
                                 candidate_circuits=args.candidate_circuit)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
