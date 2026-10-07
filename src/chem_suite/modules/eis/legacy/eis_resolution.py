"""Synthetic frequency-window resolution map and measurement recommendation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .eis_drt import fit_drt, select_regularization
from .eis_synthetic import simulate_spectrum


def _has_peak_near(peaks, target_frequency, tolerance_decades=0.5):
    return any(abs(np.log10(peak["frequency_hz"] / target_frequency)) <= tolerance_decades for peak in peaks)


def build_low_frequency_resolution_map(*, min_frequencies=(0.1, 0.03, 0.01, 0.003, 0.001),
                                       noise_fractions=(0.005, 0.01, 0.02), replicates=10,
                                       max_frequency=2e6, points=81, seed=20260717,
                                       regularizations=(1e-4, 1e-3, 1e-2, 1e-1),
                                       stable_threshold=0.90, observed_low_peak_stability=None, check_cancelled=None, on_cell=None):
    # Real-like two-RC reference based on the SOC50 DRT peak locations/amplitudes.
    circuit = "R0-p(R1,C1)-p(R2,C2)"
    tau_fast, tau_slow = 2.79e-7, 8.21
    r0, r_fast, r_slow = 1.17, 1.46, 0.76
    parameters = [r0, r_fast, tau_fast / r_fast, r_slow, tau_slow / r_slow]
    target_frequency = 1.0 / (2 * np.pi * tau_slow)
    rng = np.random.default_rng(seed)
    cells = []
    for noise in noise_fractions:
        for minimum in sorted(min_frequencies, reverse=True):
            detections = 0
            selected_lambdas = []
            for _ in range(int(replicates)):
                if check_cancelled is not None:
                    check_cancelled()
                frequencies = np.logspace(np.log10(max_frequency), np.log10(minimum), int(points))
                _, measured = simulate_spectrum(circuit, parameters, frequencies,
                                                noise_fraction=float(noise), rng=rng)
                selection = select_regularization(frequencies, measured, regularizations,
                                                  tau_points=81, folds=5)
                fitted = fit_drt(frequencies, measured, tau_points=81,
                                 regularization=selection["selected"])
                selected_lambdas.append(selection["selected"])
                detections += int(_has_peak_near(fitted["peaks"], target_frequency))
            fraction = detections / max(int(replicates), 1)
            cells.append({"min_frequency_hz": float(minimum), "noise_fraction": float(noise),
                          "replicates": int(replicates), "slow_peak_detection_fraction": fraction,
                          "resolved_at_threshold": fraction >= stable_threshold,
                          "median_selected_regularization": float(np.median(selected_lambdas))})
            if on_cell is not None:
                on_cell(len(cells), len(noise_fractions) * len(min_frequencies))
    recommendations = []
    for noise in noise_fractions:
        noise_cells = [cell for cell in cells if cell["noise_fraction"] == float(noise)]
        resolved = [cell for cell in noise_cells if cell["resolved_at_threshold"]]
        # Highest minimum frequency is the shortest sufficient measurement.
        recommended = max((cell["min_frequency_hz"] for cell in resolved), default=None)
        recommendations.append({"noise_fraction": float(noise), "recommended_min_frequency_hz": recommended,
                                "resolved": recommended is not None})
    current_min = 0.01
    current_noise = min(noise_fractions, key=lambda value: abs(float(value) - 0.01))
    current_cell = next(cell for cell in cells if cell["min_frequency_hz"] == current_min and
                        cell["noise_fraction"] == float(current_noise))
    recommendation = next(item for item in recommendations if item["noise_fraction"] == float(current_noise))
    calibrated_window_sufficient = current_cell["resolved_at_threshold"]
    observed_unstable = (observed_low_peak_stability is not None and
                         float(observed_low_peak_stability) < stable_threshold)
    insufficient = observed_unstable or not calibrated_window_sufficient
    if observed_unstable and calibrated_window_sufficient:
        gap_type = "low_frequency_repeatability_or_model_mismatch"
        message = "Frequency floor is sufficient in calibration; repeat 0.01-0.1 Hz and verify stationarity/model adequacy"
    elif not calibrated_window_sufficient:
        gap_type = "frequency_window_too_short"
        message = "Extend the low-frequency measurement and verify stationarity"
    else:
        gap_type = None
        message = "Current low-frequency window is sufficient in this calibrated scenario"
    return {"schema_version": 1, "analysis": "synthetic_low_frequency_resolution_map",
            "truth": {"circuit": circuit, "parameters": parameters, "slow_tau_seconds": tau_slow,
                      "slow_peak_frequency_hz": target_frequency},
            "policy": {"stable_detection_threshold": stable_threshold, "peak_tolerance_decades": 0.5},
            "cells": cells, "recommendations_by_noise": recommendations,
            "current_scenario": {"min_frequency_hz": current_min, "noise_fraction": float(current_noise),
                                 "detection_fraction": current_cell["slow_peak_detection_fraction"]},
            "measurement_recommendation": {
                "insufficient_data": insufficient,
                "insufficiency_type": gap_type,
                "problem_region_hz": [0.01, 0.1],
                "recommended_min_frequency_hz": recommendation["recommended_min_frequency_hz"],
                "current_frequency_floor_is_calibrated_sufficient": calibrated_window_sufficient,
                "frequency_extension_needed": not calibrated_window_sufficient,
                "observed_low_peak_stability": observed_low_peak_stability,
                "message": message,
            }}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build a synthetic low-frequency process resolution map.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--replicates", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260717)
    parser.add_argument("--observed-low-peak-stability", type=float,
                        help="Worst observed DRT match fraction used to localize a real information gap")
    args = parser.parse_args(argv)
    report = build_low_frequency_resolution_map(replicates=args.replicates, seed=args.seed,
                                                observed_low_peak_stability=args.observed_low_peak_stability)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
