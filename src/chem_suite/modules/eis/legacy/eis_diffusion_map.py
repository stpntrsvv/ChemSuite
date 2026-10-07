"""Controlled synthetic map of diffusion observability in a measured EIS band."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np
from impedance.models.circuits import CustomCircuit

from .eis_core import parameter_names
from .eis_synthetic import sample_parameters, simulate_spectrum


BASE_CIRCUIT = "L0-R0-p(R1,CPE0)"
DIFFUSION_CIRCUITS = {
    "W": "L0-R0-p(R1,CPE0)-W0",
    "Wo": "L0-R0-p(R1,CPE0)-Wo0",
    "Ws": "L0-R0-p(R1,CPE0)-Ws0",
}
BASE_PARAMETERS = (1e-5, 5.0, 20.0, 1e-3, 0.85)


def _predict(circuit: str, parameters, frequencies) -> np.ndarray:
    model = CustomCircuit(circuit, initial_guess=list(parameters))
    return np.asarray(model.predict(frequencies, use_initial=True), dtype=complex)


def controlled_parameters(
    kind: str,
    frequencies: np.ndarray,
    *,
    signal_fraction: float,
    characteristic_frequency_hz: float | None = None,
    base_parameters=BASE_PARAMETERS,
) -> tuple[list[float], float]:
    """Scale a diffusion element to a requested RMS contribution fraction."""
    if kind not in DIFFUSION_CIRCUITS:
        raise ValueError(f"Unknown diffusion kind: {kind}")
    if signal_fraction <= 0:
        raise ValueError("signal_fraction must be positive")
    frequencies = np.asarray(frequencies, dtype=float)
    baseline = _predict(BASE_CIRCUIT, base_parameters, frequencies)
    circuit = DIFFUSION_CIRCUITS[kind]
    if kind == "W":
        unit_parameters = [*base_parameters, 1.0]
    else:
        if characteristic_frequency_hz is None or characteristic_frequency_hz <= 0:
            raise ValueError("finite-length diffusion needs a positive characteristic frequency")
        tau = 1.0 / (2.0 * math.pi * characteristic_frequency_hz)
        unit_parameters = [*base_parameters, 1.0, tau]
    unit_delta = _predict(circuit, unit_parameters, frequencies) - baseline
    baseline_rms = float(np.sqrt(np.mean(np.abs(baseline) ** 2)))
    unit_rms = float(np.sqrt(np.mean(np.abs(unit_delta) ** 2)))
    strength = signal_fraction * baseline_rms / max(unit_rms, 1e-30)
    parameters = list(unit_parameters)
    parameters[len(base_parameters)] = strength
    achieved = float(
        np.sqrt(np.mean(np.abs(_predict(circuit, parameters, frequencies) - baseline) ** 2))
        / baseline_rms
    )
    return parameters, achieved


def generate_observability_corpus(
    output_dir: str | Path,
    *,
    kinds=("W", "Wo", "Ws"),
    signal_fractions=(0.01, 0.05, 0.20),
    characteristic_positions=("below", "inside", "above"),
    noise_fractions=(0.005, 0.02),
    replicates=1,
    seed=20260727,
    points=61,
    min_frequency=1e-2,
    max_frequency=1e5,
) -> Path:
    output = Path(output_dir)
    spectra_dir = output / "spectra"
    spectra_dir.mkdir(parents=True, exist_ok=True)
    frequencies = np.logspace(math.log10(max_frequency), math.log10(min_frequency), points)
    # Production Wo/Ws bounds currently constrain tau to [1e-3, 1e3] s,
    # corresponding to characteristic frequencies of roughly
    # [1.6e-4, 1.6e2] Hz. Keep synthetic truth inside that fitted space.
    upper_resolvable_characteristic = min(max_frequency, 100.0)
    characteristic_frequencies = {
        "below": min_frequency / 10.0,
        "inside": math.sqrt(min_frequency * upper_resolvable_characteristic),
        "above": upper_resolvable_characteristic,
    }
    unknown = set(characteristic_positions) - set(characteristic_frequencies)
    if unknown:
        raise ValueError(f"Unknown characteristic positions: {sorted(unknown)}")
    rng = np.random.default_rng(seed)
    manifest_path = output / "truth.jsonl"
    sample_index = 0
    with manifest_path.open("w", encoding="utf-8") as manifest:
        for kind in kinds:
            positions = ("none",) if kind == "W" else tuple(characteristic_positions)
            for position in positions:
                characteristic = None if position == "none" else characteristic_frequencies[position]
                for signal_fraction in signal_fractions:
                    parameters, achieved = controlled_parameters(
                        kind,
                        frequencies,
                        signal_fraction=float(signal_fraction),
                        characteristic_frequency_hz=characteristic,
                    )
                    for noise_fraction in noise_fractions:
                        for replicate in range(int(replicates)):
                            sample_index += 1
                            circuit = DIFFUSION_CIRCUITS[kind]
                            _, measured = simulate_spectrum(
                                circuit,
                                parameters,
                                frequencies,
                                noise_fraction=float(noise_fraction),
                                rng=rng,
                            )
                            file_name = f"diffmap_{sample_index:04d}_{kind}_{position}_r{replicate:02d}.csv"
                            with (spectra_dir / file_name).open("w", newline="", encoding="utf-8") as handle:
                                writer = csv.writer(handle)
                                writer.writerow(("frequency", "real", "imag"))
                                writer.writerows(zip(frequencies, measured.real, measured.imag))
                            truth = {
                                "schema_version": 1,
                                "file_name": file_name,
                                "circuit": circuit,
                                "parameter_names": parameter_names(circuit),
                                "parameters": parameters,
                                "diffusion_kind": kind,
                                "signal_fraction_requested": float(signal_fraction),
                                "signal_fraction_achieved": achieved,
                                "characteristic_position": position,
                                "characteristic_frequency_hz": characteristic,
                                "noise_fraction": float(noise_fraction),
                                "signal_to_noise_ratio": float(signal_fraction) / max(float(noise_fraction), 1e-30),
                                "seed": seed,
                                "points": points,
                                "min_frequency": min_frequency,
                                "max_frequency": max_frequency,
                            }
                            manifest.write(json.dumps(truth, ensure_ascii=False, allow_nan=False) + "\n")
    return manifest_path


def generate_diverse_negative_controls(
    output_dir: str | Path,
    *,
    samples=60,
    seed=20260801,
    point_counts=(41, 61, 81),
    frequency_bands=((1e-3, 1e4), (1e-2, 1e5), (1e-1, 1e4)),
    noise_fractions=(0.0025, 0.01, 0.02),
    outlier_fractions=(0.0, 0.02),
) -> Path:
    """Generate base-family controls across geometry, grids, and noise models."""
    if samples < 1:
        raise ValueError("samples must be positive")
    if not point_counts or not frequency_bands or not noise_fractions:
        raise ValueError("control axes must not be empty")
    output = Path(output_dir)
    spectra_dir = output / "spectra"
    spectra_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    manifest_path = output / "truth.jsonl"
    with manifest_path.open("w", encoding="utf-8") as manifest:
        for index in range(int(samples)):
            points = int(point_counts[index % len(point_counts)])
            min_frequency, max_frequency = frequency_bands[
                (index // len(point_counts)) % len(frequency_bands)
            ]
            noise_fraction = float(
                noise_fractions[
                    (index // (len(point_counts) * len(frequency_bands)))
                    % len(noise_fractions)
                ]
            )
            outlier_fraction = float(outlier_fractions[index % len(outlier_fractions)])
            frequencies = np.logspace(
                math.log10(max_frequency), math.log10(min_frequency), points
            )
            parameters = sample_parameters(BASE_CIRCUIT, rng)
            _, measured = simulate_spectrum(
                BASE_CIRCUIT,
                parameters,
                frequencies,
                noise_fraction=noise_fraction,
                outlier_fraction=outlier_fraction,
                rng=rng,
            )
            file_name = f"negative_{index + 1:04d}.csv"
            with (spectra_dir / file_name).open(
                "w", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.writer(handle)
                writer.writerow(("frequency", "real", "imag"))
                writer.writerows(zip(frequencies, measured.real, measured.imag))
            truth = {
                "schema_version": 1,
                "file_name": file_name,
                "circuit": BASE_CIRCUIT,
                "parameter_names": parameter_names(BASE_CIRCUIT),
                "parameters": parameters,
                "noise_fraction": noise_fraction,
                "outlier_fraction": outlier_fraction,
                "seed": seed,
                "points": points,
                "min_frequency": float(min_frequency),
                "max_frequency": float(max_frequency),
                "control_kind": "diverse_base_family",
            }
            manifest.write(json.dumps(truth, ensure_ascii=False, allow_nan=False) + "\n")
    return manifest_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate a controlled diffusion-observability corpus.")
    parser.add_argument("output")
    parser.add_argument("--kind", action="append", choices=tuple(DIFFUSION_CIRCUITS))
    parser.add_argument("--signal-fraction", action="append", type=float)
    parser.add_argument("--position", action="append", choices=("below", "inside", "above"))
    parser.add_argument("--noise", action="append", type=float)
    parser.add_argument("--replicates", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260727)
    parser.add_argument("--points", type=int, default=61)
    parser.add_argument("--min-frequency", type=float, default=1e-2)
    parser.add_argument("--max-frequency", type=float, default=1e5)
    args = parser.parse_args(argv)
    path = generate_observability_corpus(
        args.output,
        kinds=tuple(args.kind or DIFFUSION_CIRCUITS),
        signal_fractions=tuple(args.signal_fraction or (0.01, 0.05, 0.20)),
        characteristic_positions=tuple(args.position or ("below", "inside", "above")),
        noise_fractions=tuple(args.noise or (0.005, 0.02)),
        replicates=args.replicates,
        seed=args.seed,
        points=args.points,
        min_frequency=args.min_frequency,
        max_frequency=args.max_frequency,
    )
    print(path)


if __name__ == "__main__":
    main()
