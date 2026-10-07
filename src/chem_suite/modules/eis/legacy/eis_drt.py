"""Non-negative ridge DRT used as a topology-independent relaxation diagnostic."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.optimize import lsq_linear
from scipy.signal import find_peaks

from .eis_io import load_eis_file


def _basis(frequencies, tau):
    kernel = 1.0 / (1.0 + 1j * 2.0 * np.pi * frequencies[:, None] * tau[None, :])
    delta_log_tau = float(np.mean(np.diff(np.log(tau))))
    kernel *= delta_log_tau
    # First column is the non-negative high-frequency series resistance.
    return np.column_stack([np.ones(len(frequencies), dtype=complex), kernel])


def _second_difference(size):
    matrix = np.zeros((max(0, size - 2), size), dtype=float)
    for index in range(size - 2):
        matrix[index, index:index + 3] = (1.0, -2.0, 1.0)
    return matrix


def fit_drt(frequencies, z, *, tau_points=81, regularization=1e-2):
    frequencies = np.asarray(frequencies, dtype=float)
    z = np.asarray(z, dtype=complex)
    tau = np.logspace(np.log10(1.0 / (2 * np.pi * np.max(frequencies))) - 1.0,
                      np.log10(1.0 / (2 * np.pi * np.min(frequencies))) + 1.0, int(tau_points))
    basis = _basis(frequencies, tau)
    design = np.vstack([basis.real, basis.imag])
    target = np.r_[z.real, z.imag]
    curvature = _second_difference(len(tau))
    penalty = np.zeros((len(curvature), len(tau) + 1))
    penalty[:, 1:] = curvature
    if regularization > 0:
        design = np.vstack([design, np.sqrt(regularization) * penalty])
        target = np.r_[target, np.zeros(len(penalty))]
    solved = lsq_linear(design, target, bounds=(0.0, np.inf), max_iter=2000, tol=1e-10)
    predicted = basis @ solved.x
    relative_rmse = float(np.sqrt(np.mean((np.abs(z - predicted) / np.maximum(np.abs(z), 1e-30)) ** 2)))
    gamma = solved.x[1:]
    prominence = max(float(np.max(gamma)) * 0.05, 1e-15)
    peak_indices, properties = find_peaks(gamma, prominence=prominence)
    peaks = [
        {"tau_seconds": float(tau[index]), "frequency_hz": float(1.0 / (2 * np.pi * tau[index])),
         "gamma_ohm": float(gamma[index]), "prominence_ohm": float(properties["prominences"][position]),
         "inside_measured_frequency_band": bool(np.min(frequencies) <= 1.0 / (2 * np.pi * tau[index]) <= np.max(frequencies))}
        for position, index in enumerate(peak_indices)
    ]
    peaks.sort(key=lambda item: item["tau_seconds"])
    return {"success": bool(solved.success), "message": solved.message, "regularization": float(regularization),
            "r_infinity_ohm": float(solved.x[0]), "relative_rmse": relative_rmse,
            "measured_frequency_min_hz": float(np.min(frequencies)),
            "measured_frequency_max_hz": float(np.max(frequencies)),
            "tau_seconds": tau.tolist(), "gamma_ohm": gamma.tolist(), "peaks": peaks,
            "resolved_peak_count": sum(peak["inside_measured_frequency_band"] for peak in peaks)}


def select_regularization(frequencies, z, candidates, *, tau_points=81, folds=5, check_cancelled=None):
    candidates = sorted({float(value) for value in candidates})
    if not candidates or any(value < 0 for value in candidates):
        raise ValueError("DRT regularization candidates must be non-negative.")
    frequencies = np.asarray(frequencies, dtype=float)
    z = np.asarray(z, dtype=complex)
    fold_ids = np.arange(len(frequencies)) % max(2, int(folds))
    ranking = []
    for regularization in candidates:
        errors = []
        for fold in range(max(2, int(folds))):
            if check_cancelled:
                check_cancelled()
            test = fold_ids == fold
            train = ~test
            fitted = fit_drt(frequencies[train], z[train], tau_points=tau_points, regularization=regularization)
            tau = np.asarray(fitted["tau_seconds"])
            coefficients = np.r_[fitted["r_infinity_ohm"], fitted["gamma_ohm"]]
            predicted = _basis(frequencies[test], tau) @ coefficients
            errors.append(float(np.sqrt(np.mean((np.abs(z[test] - predicted) / np.maximum(np.abs(z[test]), 1e-30)) ** 2))))
        ranking.append({"regularization": regularization, "mean_heldout_relative_rmse": float(np.mean(errors))})
    ranking.sort(key=lambda item: (item["mean_heldout_relative_rmse"], item["regularization"]))
    selected = ranking[0]["regularization"]
    return {"method": "interleaved-frequency-cross-validation", "selected": selected,
            "selected_hits_grid_edge": selected in {min(candidates), max(candidates)},
            "folds": max(2, int(folds)), "ranking": ranking}


def drt_peak_stability(frequencies, z, reference_fit, regularizations, *, samples=30,
                       keep_fractions=(1.0, 0.8), tau_points=81, seed=0, tolerance_decades=0.5, check_cancelled=None):
    """Measure whether reference DRT peaks recur under residual/frequency perturbations."""
    frequencies = np.asarray(frequencies, dtype=float)
    z = np.asarray(z, dtype=complex)
    reference_peaks = reference_fit.get("peaks", [])
    if not reference_peaks:
        return {"method": "drt_peak_stability", "reference_peaks": [], "conditions": []}
    tau = np.asarray(reference_fit["tau_seconds"], dtype=float)
    coefficients = np.r_[reference_fit["r_infinity_ohm"], reference_fit["gamma_ohm"]]
    predicted = _basis(frequencies, tau) @ coefficients
    residuals = z - predicted
    residuals -= np.mean(residuals)
    rng = np.random.default_rng(seed)
    conditions = []
    for regularization in sorted({float(value) for value in regularizations}):
        for keep_fraction in keep_fractions:
            if not 0 < float(keep_fraction) <= 1:
                raise ValueError("Frequency keep fractions must be in (0, 1].")
            matches = [[] for _ in reference_peaks]
            peak_counts = []
            for _ in range(int(samples)):
                if check_cancelled:
                    check_cancelled()
                synthetic = predicted + residuals[rng.integers(0, len(residuals), len(residuals))]
                keep_count = max(8, int(round(len(frequencies) * float(keep_fraction))))
                indices = np.sort(rng.choice(len(frequencies), size=min(keep_count, len(frequencies)), replace=False))
                fitted = fit_drt(frequencies[indices], synthetic[indices], tau_points=tau_points,
                                 regularization=regularization)
                peaks = fitted["peaks"]
                peak_counts.append(len(peaks))
                available = set(range(len(peaks)))
                for reference_index, reference in enumerate(reference_peaks):
                    candidates = [
                        (abs(np.log10(peaks[index]["frequency_hz"] / reference["frequency_hz"])), index)
                        for index in available if peaks[index]["frequency_hz"] > 0
                    ]
                    if candidates:
                        distance, peak_index = min(candidates)
                        if distance <= tolerance_decades:
                            matches[reference_index].append(peaks[peak_index]["frequency_hz"])
                            available.remove(peak_index)
            condition_peaks = []
            for reference, frequencies_found in zip(reference_peaks, matches):
                condition_peaks.append({
                    "reference_frequency_hz": reference["frequency_hz"],
                    "match_fraction": len(frequencies_found) / max(int(samples), 1),
                    "median_frequency_hz": None if not frequencies_found else float(np.median(frequencies_found)),
                    "frequency_ci95_hz": None if not frequencies_found else [float(np.quantile(frequencies_found, 0.025)),
                                                                              float(np.quantile(frequencies_found, 0.975))],
                })
            conditions.append({"regularization": regularization, "frequency_keep_fraction": float(keep_fraction),
                               "samples": int(samples), "median_detected_peak_count": float(np.median(peak_counts)),
                               "reference_peak_matches": condition_peaks})
    aggregate = []
    for index, reference in enumerate(reference_peaks):
        fractions = [condition["reference_peak_matches"][index]["match_fraction"] for condition in conditions]
        aggregate.append({"reference_frequency_hz": reference["frequency_hz"],
                          "worst_condition_match_fraction": float(min(fractions)),
                          "median_condition_match_fraction": float(np.median(fractions)),
                          "stable_at_90_percent": bool(min(fractions) >= 0.90)})
    return {"method": "residual-bootstrap-by-regularization-and-frequency-subset",
            "tolerance_decades": tolerance_decades, "reference_peaks": aggregate, "conditions": conditions}


def analyze_drt(file_path, *, candidates=(0.0001, 0.001, 0.01, 0.1, 1.0), tau_points=81, folds=5,
                stability_samples=0, seed=0, channel=None, check_cancelled=None):
    dataset = load_eis_file(file_path, channel=channel)
    selection = select_regularization(dataset.frequencies, dataset.z, candidates,
                                      tau_points=tau_points, folds=folds, check_cancelled=check_cancelled)
    fit = fit_drt(dataset.frequencies, dataset.z, tau_points=tau_points,
                  regularization=selection["selected"])
    stability = None
    if stability_samples:
        stability = drt_peak_stability(dataset.frequencies, dataset.z, fit, candidates,
                                       samples=stability_samples, tau_points=tau_points, seed=seed, check_cancelled=check_cancelled)
    return {"schema_version": 2, "analysis": "nonnegative_drt", "file": str(Path(file_path).resolve()),
            "selection": selection, "fit": fit,
            "stability": stability,
            "interpretation_warning": "DRT peaks are relaxation features, not automatically chemical mechanisms"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Topology-independent non-negative DRT diagnostic.")
    parser.add_argument("file")
    parser.add_argument("--output", required=True)
    parser.add_argument("--lambda-grid", default="0.0001,0.001,0.01,0.1,1")
    parser.add_argument("--tau-points", type=int, default=81)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--stability-samples", type=int, default=0)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    candidates = [float(value.strip()) for value in args.lambda_grid.split(",") if value.strip()]
    result = analyze_drt(args.file, candidates=candidates, tau_points=args.tau_points, folds=args.folds,
                         stability_samples=args.stability_samples, seed=args.seed)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
