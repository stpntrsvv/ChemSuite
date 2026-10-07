"""Пользовательский экспорт проверенных инженерных SPICE-моделей."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Iterable

import numpy as np

from .eis_pipeline import AnalysisResult, fit_result_dict, kk_result_dict
from .eis_rational import fit_from_ecm_result
from .eis_spice import (
    condition_foster_realization,
    export_foster_subcircuit,
    run_ngspice_round_trip,
    validate_foster_round_trip,
)


@dataclass(frozen=True)
class SpiceExportPolicy:
    orders: tuple[int, ...] = (12, 16, 24, 32)
    pole_margin_decades: float = 1.0
    ecm_mean_error_percent: float = 1.0
    ecm_max_error_percent: float = 10.0
    realization_max_error_percent: float = 0.25
    ngspice_max_error_percent: float = 1e-6
    max_sections: int = 32
    max_resistance_span_decades: float = 12.0
    max_capacitance_span_decades: float = 12.0


@dataclass(frozen=True)
class SpicePackageResult:
    package_directory: str
    model_file: str
    passport_file: str
    selected_order: int
    simulator_version: str


class SpiceExportRefused(RuntimeError):
    def __init__(self, message: str, *, attempts: Iterable[dict] = ()):
        super().__init__(message)
        self.attempts = tuple(attempts)


def _span_decades(values: Iterable[float]) -> tuple[float | None, float | None, float]:
    positive = np.asarray(
        [float(value) for value in values if np.isfinite(value) and value > 0],
        dtype=float,
    )
    if positive.size == 0:
        return None, None, 0.0
    minimum = float(np.min(positive))
    maximum = float(np.max(positive))
    span = float(math.log10(maximum / minimum)) if maximum > minimum else 0.0
    return minimum, maximum, span


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _json_safe(value):
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, complex):
        return {"real": _json_safe(value.real), "imag": _json_safe(value.imag)}
    if isinstance(value, np.ndarray):
        return [_json_safe(item) for item in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


def _validate_analysis(analysis: AnalysisResult) -> None:
    if not analysis.success or analysis.stage != "complete":
        raise SpiceExportRefused("Анализ не завершён успешно; SPICE-экспорт запрещён.")
    if analysis.best is None:
        raise SpiceExportRefused("Научная эквивалентная схема не выбрана.")
    if not analysis.best.success or analysis.best.status not in {"OK", "WARN"}:
        raise SpiceExportRefused(
            f"Статус научной модели {analysis.best.status!r} не допускает экспорт."
        )
    if (
        analysis.kk is None
        or not analysis.kk.success
        or analysis.kk.status not in {"PASS", "WARN"}
    ):
        status = "missing" if analysis.kk is None else analysis.kk.status
        raise SpiceExportRefused(
            f"Проверка Крамерса — Кронига имеет статус {status!r}; экспорт запрещён."
        )


def _validate_policy(policy: SpiceExportPolicy) -> tuple[int, ...]:
    orders = tuple(int(order) for order in policy.orders)
    if not orders or orders != tuple(sorted(set(orders))) or any(order < 1 for order in orders):
        raise ValueError("Порядки экспорта должны быть возрастающими уникальными числами.")
    numeric_limits = (
        policy.pole_margin_decades,
        policy.ecm_mean_error_percent,
        policy.ecm_max_error_percent,
        policy.realization_max_error_percent,
        policy.ngspice_max_error_percent,
        policy.max_resistance_span_decades,
        policy.max_capacitance_span_decades,
    )
    if any(not np.isfinite(value) or value < 0 for value in numeric_limits):
        raise ValueError("Пороги экспорта должны быть конечными и неотрицательными.")
    if policy.max_sections < 1:
        raise ValueError("Допустимое число секций должно быть положительным.")
    return orders


def _attempt_order(
    analysis: AnalysisResult,
    frequencies_hz: np.ndarray,
    *,
    order: int,
    policy: SpiceExportPolicy,
    ngspice_executable: str | None,
):
    try:
        model = fit_from_ecm_result(
            analysis.best,
            frequencies_hz,
            order=order,
            pole_margin_decades=policy.pole_margin_decades,
        )
        conditioned = condition_foster_realization(
            model,
            max_error_percent=policy.realization_max_error_percent,
        )
        realization = validate_foster_round_trip(
            model,
            frequencies_hz,
            sections=conditioned.sections,
        )
    except Exception as exc:
        return {
            "order": order,
            "status": "refused",
            "refusal_reasons": ["engineering_fit_failed"],
            "error_message": str(exc),
        }, None, None, None

    resistance_values = [section.resistance for section in conditioned.sections]
    if model.direct > 0:
        resistance_values.append(model.direct)
    capacitance_values = [section.capacitance for section in conditioned.sections]
    r_min, r_max, r_span = _span_decades(resistance_values)
    c_min, c_max, c_span = _span_decades(capacitance_values)

    reasons = []
    if not model.stable:
        reasons.append("unstable")
    if not model.passive:
        reasons.append("non_passive")
    if model.metrics.mean_relative_error_percent > policy.ecm_mean_error_percent:
        reasons.append("ecm_mean_error")
    if model.metrics.max_relative_error_percent > policy.ecm_max_error_percent:
        reasons.append("ecm_max_error")
    if realization.max_relative_error_percent > policy.realization_max_error_percent:
        reasons.append("realization_error")
    if len(conditioned.sections) > policy.max_sections:
        reasons.append("too_many_sections")
    if r_span > policy.max_resistance_span_decades:
        reasons.append("resistance_span")
    if c_span > policy.max_capacitance_span_decades:
        reasons.append("capacitance_span")

    external = None
    if not reasons:
        external = run_ngspice_round_trip(
            model,
            executable=ngspice_executable,
            sections=conditioned.sections,
            max_error_percent=policy.ngspice_max_error_percent,
        )
        if external.status != "validated":
            reasons.append(f"ngspice_{external.status}")

    attempt = {
        "order": order,
        "status": "validated" if not reasons else "refused",
        "refusal_reasons": reasons,
        "error_message": "" if external is None else external.message,
        "stable": model.stable,
        "passive": model.passive,
        "ecm_error_percent": asdict(model.metrics),
        "realization_error_percent": asdict(realization),
        "original_sections": conditioned.original_sections,
        "pruned_sections": conditioned.pruned_sections,
        "exported_sections": len(conditioned.sections),
        "resistance_ohm": {"minimum": r_min, "maximum": r_max, "span_decades": r_span},
        "capacitance_f": {"minimum": c_min, "maximum": c_max, "span_decades": c_span},
        "external_validation": None if external is None else {
            "status": external.status,
            "simulator": "ngspice",
            "version": external.simulator_version,
            "executable": external.executable,
            "points": external.points,
            "max_error_percent": (
                None
                if external.metrics is None
                else external.metrics.max_relative_error_percent
            ),
            "return_code": external.return_code,
            "message": external.message,
        },
    }
    return attempt, model, conditioned, external


def export_spice_package(
    analysis: AnalysisResult,
    frequencies_hz,
    output_directory: str | Path,
    *,
    source_file: str | Path | None = None,
    ngspice_executable: str | None = None,
    policy: SpiceExportPolicy = SpiceExportPolicy(),
) -> SpicePackageResult:
    """Validate and atomically publish ``model.lib`` plus ``passport.json``."""

    _validate_analysis(analysis)
    orders = _validate_policy(policy)
    frequencies = np.asarray(frequencies_hz, dtype=float)
    if (
        frequencies.ndim != 1
        or frequencies.size < 4
        or not np.all(np.isfinite(frequencies))
        or np.any(frequencies <= 0)
    ):
        raise ValueError("Для SPICE-экспорта нужны конечные положительные частоты.")

    source_path = Path(source_file or analysis.file_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"Исходный файл не найден: {source_path}")
    source_sha256 = _sha256(source_path)
    target = Path(output_directory)
    if target.exists():
        raise FileExistsError(
            f"Каталог SPICE-пакета уже существует и не будет перезаписан: {target}"
        )

    attempts = []
    selected = None
    for order in orders:
        attempt, model, conditioned, external = _attempt_order(
            analysis,
            frequencies,
            order=order,
            policy=policy,
            ngspice_executable=ngspice_executable,
        )
        attempts.append(attempt)
        if attempt["status"] == "validated":
            selected = (order, model, conditioned, external)
            break
        if "ngspice_runtime_missing" in attempt["refusal_reasons"]:
            break
    if selected is None:
        compact = "; ".join(
            f"{attempt['order']}:{','.join(attempt['refusal_reasons']) or 'unknown'}"
            for attempt in attempts
        )
        raise SpiceExportRefused(
            f"Ни один инженерный порядок не прошёл ворота: {compact}",
            attempts=attempts,
        )

    order, model, conditioned, external = selected
    if _sha256(source_path) != source_sha256:
        raise SpiceExportRefused(
            "Исходный файл изменился во время инженерной проверки; экспорт отменён.",
            attempts=attempts,
        )

    netlist = export_foster_subcircuit(
        model,
        subcircuit_name="EIS_MODEL",
        sections=conditioned.sections,
    )
    rational = model.to_dict()
    rational["foster_sections"] = [asdict(section) for section in conditioned.sections]
    selected_attempt = attempts[-1]

    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=parent))
    try:
        model_path = temporary / "model.lib"
        passport_path = temporary / "passport.json"
        model_path.write_text(netlist, encoding="ascii")
        passport = {
            "schema_version": 1,
            "artifact_type": "validated_spice_package",
            "status": "validated",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": {
                "file": str(source_path.resolve()),
                "sha256": source_sha256,
                "source_format": analysis.source_format,
                "selected_channel": analysis.selected_channel,
                "point_count": analysis.point_count,
            },
            "scientific_model": fit_result_dict(analysis.best, is_best=True),
            "kramers_kronig": kk_result_dict(analysis.kk),
            "engineering_policy": asdict(policy),
            "engineering_model": {
                "selected_order": order,
                "realization_strategy": "global_error_budget",
                "rational_model": rational,
                "selected_attempt": selected_attempt,
                "attempts": attempts,
            },
            "files": {
                "model": "model.lib",
                "model_sha256": _sha256(model_path),
                "passport": "passport.json",
            },
        }
        passport_path.write_text(
            json.dumps(
                _json_safe(passport),
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    return SpicePackageResult(
        package_directory=str(target),
        model_file=str(target / "model.lib"),
        passport_file=str(target / "passport.json"),
        selected_order=order,
        simulator_version=str(external.simulator_version),
    )
