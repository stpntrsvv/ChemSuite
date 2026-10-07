"""Reproducible synthetic EIS spectra with known circuits and parameters."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np
from impedance.models.circuits import CustomCircuit

from .eis_core import parameter_names


DEFAULT_SYNTHETIC_CIRCUITS = (
    "R0-p(R1,C1)",
    "R0-p(R1,CPE0)",
    "R0-p(R1,CPE0)-p(R2,CPE1)",
    "R0-p(R1,CPE0)-W0",
    "L0-R0-p(R1,CPE0)",
)


def _log_uniform(rng: np.random.Generator, low: float, high: float) -> float:
    return float(10 ** rng.uniform(math.log10(low), math.log10(high)))


def sample_parameters(circuit: str, rng: np.random.Generator) -> list[float]:
    values = []
    for name in parameter_names(circuit):
        if name.startswith("R"):
            values.append(_log_uniform(rng, 0.5, 2_000.0))
        elif name.startswith("CPE") and name.endswith("_0"):
            values.append(_log_uniform(rng, 1e-7, 5e-2))
        elif name.startswith("CPE") and name.endswith("_1"):
            values.append(float(rng.uniform(0.65, 0.93)))
        elif name.startswith(("Wo", "Ws")) and name.endswith("_0"):
            values.append(_log_uniform(rng, 0.5, 2_000.0))
        elif name.startswith(("Wo", "Ws")) and name.endswith("_1"):
            values.append(_log_uniform(rng, 1e-3, 1e3))
        elif name.startswith("W"):
            values.append(_log_uniform(rng, 0.1, 1_000.0))
        elif name.startswith("C"):
            values.append(_log_uniform(rng, 1e-8, 5e-2))
        elif name.startswith("L"):
            values.append(_log_uniform(rng, 1e-9, 1e-2))
        else:
            raise ValueError(f"No synthetic parameter distribution for {name} in {circuit}")
    if circuit == "R0-p(R1,CPE0)-p(R2,CPE1)":
        # Exact topology is not recoverable when both relaxation times merge.
        # Resample the second branch until the characteristic times are separated.
        for _ in range(100):
            tau_1 = (values[1] * values[2]) ** (1.0 / values[3])
            tau_2 = (values[4] * values[5]) ** (1.0 / values[6])
            if abs(math.log10(tau_1) - math.log10(tau_2)) >= 1.5:
                break
            values[4] = _log_uniform(rng, 0.5, 2_000.0)
            values[5] = _log_uniform(rng, 1e-7, 5e-2)
            values[6] = float(rng.uniform(0.65, 0.93))
    return values


def simulate_spectrum(
    circuit: str,
    parameters: list[float],
    frequencies: np.ndarray,
    *,
    noise_fraction: float = 0.0,
    outlier_fraction: float = 0.0,
    rng: np.random.Generator | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    rng = rng or np.random.default_rng()
    model = CustomCircuit(circuit, initial_guess=parameters)
    clean = np.asarray(model.predict(frequencies, use_initial=True), dtype=complex)
    noisy = clean.copy()
    if noise_fraction > 0:
        sigma = noise_fraction * np.maximum(np.abs(clean), 1e-30) / np.sqrt(2.0)
        noisy += rng.normal(0.0, sigma) + 1j * rng.normal(0.0, sigma)
    if outlier_fraction > 0:
        count = min(len(noisy), max(1, int(round(len(noisy) * outlier_fraction))))
        indices = rng.choice(len(noisy), size=count, replace=False)
        scale = np.maximum(np.abs(clean[indices]), 1e-30)
        noisy[indices] += rng.normal(0.0, 0.25 * scale) + 1j * rng.normal(0.0, 0.25 * scale)
    return clean, noisy


def generate_corpus(
    output_dir: str | Path,
    *,
    circuits: tuple[str, ...] = DEFAULT_SYNTHETIC_CIRCUITS,
    samples_per_circuit: int = 10,
    noise_fraction: float = 0.01,
    outlier_fraction: float = 0.0,
    seed: int = 20260717,
    points: int = 61,
    min_frequency: float = 1e-2,
    max_frequency: float = 1e5,
) -> Path:
    output = Path(output_dir)
    spectra_dir = output / "spectra"
    spectra_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    frequencies = np.logspace(math.log10(max_frequency), math.log10(min_frequency), points)
    manifest_path = output / "truth.jsonl"

    with manifest_path.open("w", encoding="utf-8") as manifest:
        sample_index = 0
        for circuit_index, circuit in enumerate(circuits):
            for replicate in range(samples_per_circuit):
                sample_index += 1
                parameters = sample_parameters(circuit, rng)
                _, measured = simulate_spectrum(
                    circuit,
                    parameters,
                    frequencies,
                    noise_fraction=noise_fraction,
                    outlier_fraction=outlier_fraction,
                    rng=rng,
                )
                file_name = f"synthetic_{sample_index:04d}_c{circuit_index:02d}_r{replicate:03d}.csv"
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
                    "noise_fraction": noise_fraction,
                    "outlier_fraction": outlier_fraction,
                    "seed": seed,
                    "points": points,
                    "min_frequency": min_frequency,
                    "max_frequency": max_frequency,
                }
                manifest.write(json.dumps(truth, ensure_ascii=False, allow_nan=False) + "\n")
    return manifest_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate EIS spectra with known circuit ground truth.")
    parser.add_argument("output")
    parser.add_argument("--circuit", action="append", help="Circuit to generate; repeat as needed.")
    parser.add_argument("--samples-per-circuit", type=int, default=10)
    parser.add_argument("--noise", type=float, default=0.01, help="Relative complex Gaussian noise.")
    parser.add_argument("--outliers", type=float, default=0.0, help="Fraction of large-error points.")
    parser.add_argument("--seed", type=int, default=20260717)
    parser.add_argument("--points", type=int, default=61)
    parser.add_argument("--min-frequency", type=float, default=1e-2)
    parser.add_argument("--max-frequency", type=float, default=1e5)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.samples_per_circuit < 1 or args.points < 8:
        raise SystemExit("samples and points must be positive; at least 8 frequency points are required")
    circuits = tuple(args.circuit) if args.circuit else DEFAULT_SYNTHETIC_CIRCUITS
    path = generate_corpus(
        args.output,
        circuits=circuits,
        samples_per_circuit=args.samples_per_circuit,
        noise_fraction=args.noise,
        outlier_fraction=args.outliers,
        seed=args.seed,
        points=args.points,
        min_frequency=args.min_frequency,
        max_frequency=args.max_frequency,
    )
    print(path)


if __name__ == "__main__":
    main()
