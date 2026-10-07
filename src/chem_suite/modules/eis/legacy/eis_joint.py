"""Opt-in joint fitting of an ordered EIS series with smooth parameter paths."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import warnings

import numpy as np
from impedance.models.circuits import CustomCircuit
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix

from .eis_core import build_bounds_and_guess, estimate_dataset_scale, fit_circuit, parameter_names
from .eis_io import load_eis_file


def load_manifest(path: str | Path) -> list[dict]:
    manifest = Path(path)
    with manifest.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or not {"file", "soc"}.issubset(rows[0]):
        raise ValueError("Series manifest must contain non-empty 'file' and 'soc' columns.")
    base = manifest.parent
    normalized = []
    for index, row in enumerate(rows, 2):
        if not row.get("file", "").strip() or not row.get("soc", "").strip():
            raise ValueError(f"Manifest row {index} has an empty file or soc.")
        file_path = Path(row["file"])
        if not file_path.is_absolute():
            file_path = base / file_path
        if not file_path.is_file():
            raise FileNotFoundError(f"Manifest row {index} file does not exist: {file_path}")
        normalized.append({**row, "file": str(file_path.resolve()), "soc": float(row["soc"])})
    normalized.sort(key=lambda item: item["soc"])
    if len({row["soc"] for row in normalized}) < 3:
        raise ValueError("Joint SOC fitting needs at least three distinct SOC values.")
    return normalized


def _is_alpha(name: str) -> bool:
    return name.startswith("CPE") and name.endswith("_1")


def _simulate(circuit: str, parameters, frequencies):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return CustomCircuit(circuit, initial_guess=list(parameters)).predict(frequencies)


def _spectrum_rmse(dataset, predicted) -> float:
    relative = np.abs(dataset.z - predicted) / np.maximum(np.abs(dataset.z), 1e-30)
    return float(np.sqrt(np.mean(relative**2)))


def _independent_starts(manifest_rows, datasets, circuit, max_evaluations, restarts, check_cancelled=None):
    cached = {}
    for index, (row, dataset) in enumerate(zip(manifest_rows, datasets)):
        if check_cancelled is not None:
            check_cancelled()
        scale = estimate_dataset_scale(dataset.frequencies, dataset.z)
        _, _, guess = build_bounds_and_guess(circuit, scale)
        fit = fit_circuit(dataset.frequencies, dataset.z, circuit, scale, fit_restarts=restarts,
                          restart_seed=index * 10007, max_fit_evaluations=max_evaluations)
        cached[row["file"]] = {
            "values": list(fit.model.parameters_) if fit.success and fit.model is not None else guess,
            "success": fit.success, "status": fit.status,
            "bic": None if not np.isfinite(fit.bic) else fit.bic,
        }
    return cached


def joint_fit(manifest_rows: list[dict], circuit: str, *, smoothness: float = 1.0,
              max_evaluations: int = 10_000, restarts: int = 3, initial_cache=None, check_cancelled=None) -> dict:
    datasets = [load_eis_file(row["file"], channel=row.get("channel") or None) for row in manifest_rows]
    names = parameter_names(circuit)
    starts, lower, upper = [], [], []
    independent = []
    initial_cache = initial_cache or _independent_starts(
        manifest_rows, datasets, circuit, max_evaluations, restarts, check_cancelled
    )
    for index, dataset in enumerate(datasets):
        scale = estimate_dataset_scale(dataset.frequencies, dataset.z)
        low, high, guess = build_bounds_and_guess(circuit, scale)
        cached = initial_cache[manifest_rows[index]["file"]]
        values = cached["values"]
        starts.extend(np.log(values[i]) if not _is_alpha(names[i]) else values[i] for i in range(len(names)))
        lower.extend(np.log(low[i]) if not _is_alpha(names[i]) else low[i] for i in range(len(names)))
        upper.extend(np.log(high[i]) if not _is_alpha(names[i]) else high[i] for i in range(len(names)))
        independent.append({"file": manifest_rows[index]["file"], "soc": manifest_rows[index]["soc"],
                            "success": cached["success"], "status": cached["status"], "bic": cached["bic"]})

    n_spectra, n_params = len(datasets), len(names)
    data_rows = sum(2 * len(dataset.frequencies) for dataset in datasets)
    regularization_rows = max(0, n_spectra - 2) * n_params if smoothness > 0 else 0
    jacobian_sparsity = lil_matrix((data_rows + regularization_rows, n_spectra * n_params), dtype=int)
    row_offset = 0
    for spectrum_index, dataset in enumerate(datasets):
        rows_count = 2 * len(dataset.frequencies)
        jacobian_sparsity[row_offset:row_offset + rows_count,
                          spectrum_index * n_params:(spectrum_index + 1) * n_params] = 1
        row_offset += rows_count
    if regularization_rows:
        for curvature_index in range(n_spectra - 2):
            for parameter_index in range(n_params):
                row = row_offset + curvature_index * n_params + parameter_index
                for spectrum_index in (curvature_index, curvature_index + 1, curvature_index + 2):
                    jacobian_sparsity[row, spectrum_index * n_params + parameter_index] = 1
    def decode(vector):
        matrix = np.asarray(vector).reshape(n_spectra, n_params).copy()
        for parameter_index, name in enumerate(names):
            if not _is_alpha(name):
                matrix[:, parameter_index] = np.exp(matrix[:, parameter_index])
        return matrix

    def residual(vector):
        if check_cancelled is not None:
            check_cancelled()
        values = decode(vector)
        parts = []
        for dataset, parameters in zip(datasets, values):
            predicted = _simulate(circuit, parameters, dataset.frequencies)
            scale = np.maximum(np.abs(dataset.z), 1e-30)
            parts.extend(((dataset.z.real - predicted.real) / scale).tolist())
            parts.extend(((dataset.z.imag - predicted.imag) / scale).tolist())
        transformed = np.asarray(vector).reshape(n_spectra, n_params)
        if n_spectra >= 3 and smoothness > 0:
            # SOC-aware slope changes: zero means a locally linear trajectory.
            soc = np.asarray([row["soc"] for row in manifest_rows], dtype=float)
            slopes = np.diff(transformed, axis=0) / np.diff(soc)[:, None]
            curvature = np.diff(slopes, axis=0)
            parts.extend((np.sqrt(smoothness) * curvature).ravel().tolist())
        return np.asarray(parts)

    optimized = least_squares(residual, starts, bounds=(lower, upper), max_nfev=max_evaluations,
                              ftol=1e-9, xtol=1e-9, gtol=1e-9, jac_sparsity=jacobian_sparsity.tocsr())
    fitted = decode(optimized.x)
    return {
        "schema_version": 1,
        "analysis": "joint_raw_series_fit",
        "circuit": circuit,
        "policy": {"manifest_required": True, "smoothness": smoothness,
                   "regularizer": "SOC slope second difference on log-positive parameters"},
        "success": bool(optimized.success), "message": optimized.message, "evaluations": optimized.nfev,
        "cost": float(optimized.cost), "independent_results": independent,
        "joint_results": [
            {"file": row["file"], "soc": row["soc"],
             "parameters": [{"name": name, "value": float(value)} for name, value in zip(names, values)]}
            for row, values in zip(manifest_rows, fitted)
        ],
    }


def cross_validate_smoothness(manifest_rows: list[dict], circuit: str, candidates,
                              *, max_evaluations: int = 2_000, restarts: int = 1,
                              minimum_improvement_percent: float = 1.0, max_folds: int | None = None, check_cancelled=None) -> dict:
    """Leave out interior SOC spectra and predict them without using their impedance."""
    candidates = sorted({float(value) for value in candidates})
    if not candidates or any(value < 0 for value in candidates):
        raise ValueError("Smoothness candidates must be a non-empty set of non-negative numbers.")
    if len(manifest_rows) < 5:
        raise ValueError("SOC cross-validation needs at least five spectra.")
    names = parameter_names(circuit)
    interior = np.arange(1, len(manifest_rows) - 1)
    if max_folds and len(interior) > max_folds:
        holdout_indices = sorted(set(np.linspace(1, len(manifest_rows) - 2, max_folds).round().astype(int)))
    else:
        holdout_indices = interior.tolist()
    all_datasets = [load_eis_file(row["file"], channel=row.get("channel") or None) for row in manifest_rows]
    initial_cache = _independent_starts(manifest_rows, all_datasets, circuit, max_evaluations, restarts, check_cancelled)
    scores = {value: [] for value in candidates}
    folds = []
    for holdout_index in holdout_indices:
        training = [row for index, row in enumerate(manifest_rows) if index != holdout_index]
        held_row = manifest_rows[holdout_index]
        held_dataset = load_eis_file(held_row["file"], channel=held_row.get("channel") or None)
        fold_scores = {}
        for smoothness in candidates:
            fitted = joint_fit(training, circuit, smoothness=smoothness,
                               max_evaluations=max_evaluations, restarts=restarts, initial_cache=initial_cache, check_cancelled=check_cancelled)
            train_soc = np.asarray([item["soc"] for item in fitted["joint_results"]], dtype=float)
            parameter_matrix = np.asarray([
                [parameter["value"] for parameter in item["parameters"]]
                for item in fitted["joint_results"]
            ], dtype=float)
            predicted_parameters = []
            for parameter_index, name in enumerate(names):
                values = parameter_matrix[:, parameter_index]
                model_values = values if _is_alpha(name) else np.log(values)
                prediction = np.interp(float(held_row["soc"]), train_soc, model_values)
                predicted_parameters.append(float(prediction if _is_alpha(name) else np.exp(prediction)))
            rmse = _spectrum_rmse(
                held_dataset,
                _simulate(circuit, predicted_parameters, held_dataset.frequencies),
            )
            scores[smoothness].append(rmse)
            fold_scores[str(smoothness)] = rmse
        folds.append({"held_out_file": held_row["file"], "held_out_soc": held_row["soc"], "rmse": fold_scores})
    summary = [
        {"smoothness": value, "mean_relative_rmse": float(np.mean(scores[value])),
         "median_relative_rmse": float(np.median(scores[value])), "folds": len(scores[value])}
        for value in candidates
    ]
    summary.sort(key=lambda item: (item["mean_relative_rmse"], item["smoothness"]))
    baseline = next((item for item in summary if item["smoothness"] == 0.0), None)
    numerical_winner = summary[0]
    improvement = None
    if baseline and baseline["mean_relative_rmse"] > 0:
        improvement = 100.0 * (baseline["mean_relative_rmse"] - numerical_winner["mean_relative_rmse"]) / baseline["mean_relative_rmse"]
    accepted = bool(improvement is not None and improvement >= minimum_improvement_percent)
    selected = numerical_winner if accepted or baseline is None else baseline
    return {"method": "leave-one-interior-SOC-out", "selected_smoothness": selected["smoothness"],
            "numerical_best_smoothness": numerical_winner["smoothness"],
            "regularization_accepted": accepted, "minimum_improvement_percent": minimum_improvement_percent,
            "relative_improvement_vs_zero_percent": improvement, "ranking": summary, "fold_details": folds}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Joint raw-spectrum fit for an explicitly declared SOC series.")
    parser.add_argument("manifest", help="CSV with required file,soc columns; paths are relative to the manifest")
    parser.add_argument("--circuit", required=True, help="One shared circuit topology")
    parser.add_argument("--output", required=True)
    parser.add_argument("--smoothness", type=float, default=1.0)
    parser.add_argument("--cv-grid", help="Comma-separated smoothness values; selects lambda using held-out SOC")
    parser.add_argument("--cv-max-folds", type=int, help="Evenly sample at most this many interior SOC holdouts")
    parser.add_argument("--max-evaluations", type=int, default=10_000)
    parser.add_argument("--restarts", type=int, default=3)
    args = parser.parse_args(argv)
    if args.smoothness < 0:
        parser.error("--smoothness must be non-negative")
    manifest_rows = load_manifest(args.manifest)
    cv = None
    smoothness = args.smoothness
    if args.cv_grid:
        candidates = [float(value.strip()) for value in args.cv_grid.split(",") if value.strip()]
        cv = cross_validate_smoothness(manifest_rows, args.circuit, candidates,
                                       max_evaluations=args.max_evaluations, restarts=args.restarts,
                                       max_folds=args.cv_max_folds)
        smoothness = cv["selected_smoothness"]
    report = joint_fit(manifest_rows, args.circuit, smoothness=smoothness,
                       max_evaluations=args.max_evaluations, restarts=args.restarts)
    report["cross_validation"] = cv
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
