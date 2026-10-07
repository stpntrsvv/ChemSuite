"""Headless EIS analysis pipeline shared by automation and future interfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
from typing import Iterable

import numpy as np

from .eis_core import (
    DEFAULT_CIRCUITS,
    DEFAULT_BIC_WINDOW,
    DatasetScale,
    FitResult,
    KramersKronigResult,
    choose_best_result,
    family_bic_evidence,
    estimate_dataset_scale,
    fit_circuits,
    lin_kk_check,
    parameter_names,
    route_circuit_candidates,
    route_residual_candidates,
)
from .eis_io import EisDataset, load_eis_file


SUPPORTED_EXTENSIONS = {".mpr", ".mpt", ".txt", ".csv", ".dat"}


def _json_value(value):
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, complex):
        return {"real": _json_value(value.real), "imag": _json_value(value.imag)}
    if isinstance(value, np.ndarray):
        return [_json_value(item) for item in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def fit_result_dict(result: FitResult, *, is_best: bool = False) -> dict:
    parameters = []
    if result.success and result.model is not None:
        values = getattr(result.model, "parameters_", [])
        confidences = getattr(result.model, "conf_", [])
        for index, (name, value) in enumerate(zip(parameter_names(result.circuit_string), values)):
            confidence = confidences[index] if index < len(confidences) else None
            relative_error = None
            if confidence is not None and value != 0:
                relative_error = abs(float(confidence) / float(value)) * 100.0
            parameters.append(
                {
                    "name": name,
                    "value": _json_value(float(value)),
                    "confidence": _json_value(None if confidence is None else float(confidence)),
                    "relative_error_percent": _json_value(relative_error),
                }
            )
    return {
        "circuit": result.circuit_string,
        "is_best": is_best,
        "success": result.success,
        "status": result.status,
        "mean_fit_error_percent": _json_value(result.mean_fit_error),
        "max_parameter_error_percent": _json_value(result.max_param_error),
        "weighted_rss": _json_value(result.rss_weighted),
        "aic": _json_value(result.aic),
        "bic": _json_value(result.bic),
        "parameter_count": result.n_params,
        "elapsed_seconds": result.elapsed_seconds,
        "starts_attempted": result.starts_attempted,
        "starts_succeeded": result.starts_succeeded,
        "best_start_index": result.best_start_index,
        "flags": list(result.flags),
        "error_message": result.error_message,
        "parameters": parameters,
    }


def kk_result_dict(result: KramersKronigResult | None) -> dict | None:
    if result is None:
        return None
    return {
        "success": result.success,
        "status": result.status,
        "rmse_percent": _json_value(result.rmse_percent),
        "max_error_percent": _json_value(result.max_error_percent),
        "mu": _json_value(result.mu),
        "n_rc": result.n_rc,
        "flags": list(result.flags),
        "error_message": result.error_message,
    }


@dataclass
class AnalysisResult:
    file_path: str
    success: bool = False
    source_format: str = ""
    selected_channel: str = ""
    columns: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    point_count: int = 0
    scale: DatasetScale | None = None
    kk: KramersKronigResult | None = None
    fits: list[FitResult] = field(default_factory=list)
    best: FitResult | None = None
    elapsed_seconds: float = 0.0
    stage: str = "pending"
    error_message: str = ""

    def to_dict(self, *, include_all_fits: bool = True) -> dict:
        best = fit_result_dict(self.best, is_best=True) if self.best else None
        payload = {
            "schema_version": 1,
            "file": self.file_path,
            "file_name": os.path.basename(self.file_path),
            "success": self.success,
            "stage": self.stage,
            "error_message": self.error_message,
            "source_format": self.source_format,
            "selected_channel": self.selected_channel,
            "columns": list(self.columns),
            "metadata": _json_value(self.metadata),
            "point_count": self.point_count,
            "scale": None if self.scale is None else {
                "r0_ohm": _json_value(self.scale.r0),
                "r_transfer_ohm": _json_value(self.scale.r_transfer),
                "capacitance_f": _json_value(self.scale.capacitance),
            },
            "kk": kk_result_dict(self.kk),
            "best": best,
            "best_statistical": self.best.circuit_string if self.best else None,
            "model_evidence": family_bic_evidence(self.fits, bic_window=DEFAULT_BIC_WINDOW),
            "selection_policy": {
                "name": "bic_evidence_then_simplicity_v2",
                "bic_window": DEFAULT_BIC_WINDOW,
                "status_order": ["OK", "WARN", "BAD"],
                "bad_fallback": True,
            },
            "elapsed_seconds": self.elapsed_seconds,
        }
        if include_all_fits:
            payload["fits"] = [fit_result_dict(item, is_best=item is self.best) for item in self.fits]
        return payload

    def summary_row(self) -> dict:
        best = self.best
        return {
            "file": self.file_path,
            "file_name": os.path.basename(self.file_path),
            "success": self.success,
            "stage": self.stage,
            "error_message": self.error_message,
            "source_format": self.source_format,
            "selected_channel": self.selected_channel,
            "points": self.point_count,
            "kk_status": self.kk.status if self.kk else "",
            "kk_rmse_percent": _json_value(self.kk.rmse_percent) if self.kk and self.kk.success else "",
            "best_circuit": best.circuit_string if best else "",
            "fit_status": best.status if best else "",
            "fit_percent": _json_value(best.mean_fit_error) if best else "",
            "bic": _json_value(best.bic) if best else "",
            "fit_count": len(self.fits),
            "elapsed_seconds": self.elapsed_seconds,
            "flags": ",".join(best.flags) if best else "",
        }


@dataclass
class SpectrumFitOutcome:
    fits: list[FitResult]
    best: FitResult | None
    routing_metadata: dict
    cancelled: bool = False


def fit_spectrum(
    frequencies,
    z_data,
    *,
    circuits: Iterable[str] | None = DEFAULT_CIRCUITS,
    parameter_overrides_by_circuit=None,
    max_fit_evaluations: int = 5_000,
    fit_tolerance: float = 1e-9,
    fit_restarts: int = 1,
    restart_seed: int = 0,
    on_result=None,
    on_tier=None,
    should_cancel=None,
) -> SpectrumFitOutcome:
    """Fit one loaded spectrum through the shared explicit/adaptive contract."""

    adaptive = circuits is None
    if adaptive:
        routing = route_circuit_candidates(frequencies, z_data)
        selected_circuits = tuple(routing.circuits)
        metadata = {
            "mode": "adaptive_v2",
            "families": list(routing.families),
            "candidate_count": len(selected_circuits),
            "features": routing.features,
            "tiers": [{
                "tier": 1,
                "families": list(routing.families),
                "circuits": list(selected_circuits),
                "features": routing.features,
            }],
        }
    else:
        selected_circuits = tuple(circuits)
        metadata = {
            "mode": "explicit",
            "families": [],
            "candidate_count": len(selected_circuits),
            "features": {},
        }
    if not selected_circuits:
        raise ValueError("At least one circuit is required for fitting.")
    if on_tier is not None:
        on_tier(1, selected_circuits, metadata)

    fits = fit_circuits(
        frequencies,
        z_data,
        circuits=selected_circuits,
        parameter_overrides_by_circuit=parameter_overrides_by_circuit,
        max_fit_evaluations=max_fit_evaluations,
        fit_tolerance=fit_tolerance,
        fit_restarts=fit_restarts,
        restart_seed=restart_seed,
        on_result=on_result,
        should_cancel=should_cancel,
    )
    cancelled = bool(should_cancel is not None and should_cancel())
    if cancelled or not fits:
        return SpectrumFitOutcome(
            fits=fits,
            best=choose_best_result(fits) if fits else None,
            routing_metadata=metadata,
            cancelled=cancelled,
        )

    best = choose_best_result(fits)
    if adaptive:
        residual_routing = route_residual_candidates(
            frequencies,
            z_data,
            best,
            routing.families,
        )
        new_circuits = tuple(
            circuit
            for circuit in residual_routing.circuits
            if circuit not in selected_circuits
        )
        metadata["tiers"].append({
            "tier": 2,
            "families": list(residual_routing.families),
            "circuits": list(new_circuits),
            "features": residual_routing.features,
        })
        if new_circuits:
            if on_tier is not None:
                on_tier(2, new_circuits, metadata)
            fits.extend(fit_circuits(
                frequencies,
                z_data,
                circuits=new_circuits,
                parameter_overrides_by_circuit=parameter_overrides_by_circuit,
                max_fit_evaluations=max_fit_evaluations,
                fit_tolerance=fit_tolerance,
                fit_restarts=fit_restarts,
                restart_seed=restart_seed + 10_000,
                on_result=on_result,
                should_cancel=should_cancel,
            ))
            cancelled = bool(should_cancel is not None and should_cancel())
            if fits:
                best = choose_best_result(fits)
            metadata["families"].extend(
                family
                for family in residual_routing.families
                if family not in metadata["families"]
            )
            metadata["candidate_count"] += len(new_circuits)

    return SpectrumFitOutcome(
        fits=fits,
        best=best,
        routing_metadata=metadata,
        cancelled=cancelled,
    )


def analyze_file(
    file_path: str,
    *,
    channel: str | None = None,
    circuits: Iterable[str] | None = DEFAULT_CIRCUITS,
    max_fit_evaluations: int = 5_000,
    fit_tolerance: float = 1e-9,
    fit_restarts: int = 1,
    restart_seed: int = 0,
    mode: str = "analyze",
) -> AnalysisResult:
    import time

    started = time.monotonic()
    result = AnalysisResult(file_path=os.path.abspath(file_path), stage="load")
    try:
        dataset: EisDataset = load_eis_file(file_path, channel=channel)
        result.source_format = dataset.source_format
        result.selected_channel = str(dataset.metadata.get("selected_channel", channel or "Z"))
        result.columns = list(dataset.columns)
        result.metadata = dict(dataset.metadata)
        result.point_count = len(dataset.frequencies)
        result.scale = estimate_dataset_scale(dataset.frequencies, dataset.z)
        if mode == "parse":
            result.success = True
            result.stage = "complete"
            return result

        result.stage = "kk"
        result.kk = lin_kk_check(dataset.frequencies, dataset.z)
        if mode == "kk":
            result.success = result.kk.success
            result.stage = "complete"
            return result

        result.stage = "fit"
        outcome = fit_spectrum(
            dataset.frequencies,
            dataset.z,
            circuits=circuits,
            max_fit_evaluations=max_fit_evaluations,
            fit_tolerance=fit_tolerance,
            fit_restarts=fit_restarts,
            restart_seed=restart_seed,
        )
        result.fits = outcome.fits
        result.best = outcome.best
        result.metadata["circuit_routing"] = outcome.routing_metadata
        result.success = True
        result.stage = "complete"
        return result
    except Exception as exc:
        result.error_message = str(exc)
        return result
    finally:
        result.elapsed_seconds = time.monotonic() - started


def discover_input_files(inputs: Iterable[str], *, recursive: bool = False) -> list[str]:
    discovered: list[str] = []
    seen: set[str] = set()
    for raw_path in inputs:
        path = Path(raw_path)
        if path.is_file():
            candidates = [path]
        elif path.is_dir():
            iterator = path.rglob("*") if recursive else path.glob("*")
            candidates = [item for item in iterator if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS]
        else:
            raise FileNotFoundError(f"Input path does not exist: {raw_path}")
        for candidate in sorted(candidates):
            resolved = str(candidate.resolve())
            if resolved not in seen:
                seen.add(resolved)
                discovered.append(resolved)
    return discovered


def dumps_result(result: AnalysisResult, *, include_all_fits: bool = True) -> str:
    return json.dumps(result.to_dict(include_all_fits=include_all_fits), ensure_ascii=False, allow_nan=False)
