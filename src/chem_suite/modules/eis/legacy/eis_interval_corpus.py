"""Generate a frozen, stratified synthetic corpus for interval calibration."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from impedance.models.circuits import CustomCircuit

from .eis_core import parameter_names


def _cpe_q(resistance: float, characteristic_frequency: float, alpha: float) -> float:
    return 1.0 / (resistance * (2.0 * np.pi * characteristic_frequency) ** alpha)


def corpus_scenarios(replicates=5) -> list[dict]:
    """Return predeclared observable and weakly-observable CPE/Wo scenarios."""
    definitions = [
        ("cpe_observable", "R0-p(R1,CPE0)",
         [5.0, 20.0, _cpe_q(20.0, 10.0, 0.82), 0.82]),
        ("cpe_weak", "R0-p(R1,CPE0)",
         [5.0, 20.0, _cpe_q(20.0, 0.001, 0.82), 0.82]),
        ("wo_observable", "L0-R0-p(R1,CPE0)-Wo0",
         [2e-6, 5.0, 20.0, _cpe_q(20.0, 100.0, 0.84), 0.84, 12.0, 0.16]),
        ("wo_weak", "L0-R0-p(R1,CPE0)-Wo0",
         [2e-6, 5.0, 20.0, _cpe_q(20.0, 100.0, 0.84), 0.84, 0.08, 1000.0]),
    ]
    return [
        {
            "stratum": stratum, "circuit": circuit, "parameters": parameters,
            "replicate": replicate, "profile_representative": replicate == 0,
        }
        for stratum, circuit, parameters in definitions
        for replicate in range(int(replicates))
    ]


def generate_interval_corpus(output: str | Path, *, replicates=5, noise_fraction=0.01,
                             seed=20260717) -> Path:
    output = Path(output)
    spectra = output / "spectra"
    spectra.mkdir(parents=True, exist_ok=True)
    frequencies = np.logspace(5, -2, 61)
    rng = np.random.default_rng(seed)
    truth_path = output / "truth.jsonl"
    with truth_path.open("w", encoding="utf-8") as truth_handle:
        for index, scenario in enumerate(corpus_scenarios(replicates), start=1):
            file_name = f"{index:04d}_{scenario['stratum']}_r{scenario['replicate']:02d}.csv"
            predicted = CustomCircuit(
                scenario["circuit"], initial_guess=scenario["parameters"]
            ).predict(frequencies, use_initial=True)
            sigma = float(noise_fraction) * np.maximum(np.abs(predicted), 1e-30) / np.sqrt(2.0)
            z = predicted + sigma * (
                rng.standard_normal(len(predicted)) + 1j * rng.standard_normal(len(predicted))
            )
            with (spectra / file_name).open("w", newline="", encoding="utf-8") as csv_handle:
                writer = csv.writer(csv_handle)
                writer.writerow(["frequency", "real", "imag"])
                writer.writerows(zip(frequencies, z.real, z.imag))
            truth = {
                "schema_version": 1, "file_name": file_name,
                "stratum": scenario["stratum"], "circuit": scenario["circuit"],
                "parameter_names": parameter_names(scenario["circuit"]),
                "parameters": scenario["parameters"],
                "noise_fraction": float(noise_fraction), "outlier_fraction": 0.0,
                "seed": int(seed), "replicate": scenario["replicate"],
                "profile_representative": scenario["profile_representative"],
                "target_parameters": (
                    ["R1", "CPE0_0", "CPE0_1"]
                    if scenario["stratum"].startswith("cpe_")
                    else ["Wo0_0", "Wo0_1"]
                ),
                "points": len(frequencies), "min_frequency": float(frequencies[-1]),
                "max_frequency": float(frequencies[0]),
            }
            truth_handle.write(json.dumps(truth, ensure_ascii=False) + "\n")
    return truth_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    parser.add_argument("--replicates", type=int, default=5)
    parser.add_argument("--noise-fraction", type=float, default=0.01)
    parser.add_argument("--seed", type=int, default=20260717)
    args = parser.parse_args(argv)
    print(generate_interval_corpus(
        args.output, replicates=args.replicates,
        noise_fraction=args.noise_fraction, seed=args.seed,
    ))


if __name__ == "__main__":
    main()
