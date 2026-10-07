"""Замороженный массовый стенд инженерных SPICE-моделей EIS."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Iterable

import numpy as np
from impedance.models.circuits import CustomCircuit

from .eis_rational import PassiveRationalModel, fit_passive_rational
from .eis_spice import (
    condition_foster_realization,
    run_ngspice_round_trip,
    validate_foster_round_trip,
)


@dataclass(frozen=True)
class BenchmarkCriteria:
    ecm_mean_error_percent: float
    ecm_max_error_percent: float
    realization_max_error_percent: float
    ngspice_max_error_percent: float
    max_sections: int
    max_resistance_span_decades: float
    max_capacitance_span_decades: float


@dataclass(frozen=True)
class BenchmarkScenario:
    scenario_id: str
    split: str
    source_kind: str
    circuit: str
    parameters: tuple[float, ...]
    frequency_min_hz: float
    frequency_max_hz: float
    points: int
    provenance: dict


@dataclass(frozen=True)
class OrderEvaluation:
    order: int
    stable: bool
    passive: bool
    ecm_mean_error_percent: float
    ecm_rms_error_percent: float
    ecm_max_error_percent: float
    realization_max_error_percent: float
    realization_strategy: str
    original_sections: int
    pruned_sections: int
    sections: int
    resistance_min_ohm: float | None
    resistance_max_ohm: float | None
    resistance_span_decades: float
    capacitance_min_f: float | None
    capacitance_max_f: float | None
    capacitance_span_decades: float
    direct_resistance_ohm: float
    series_inductance_h: float
    internal_passed: bool
    external_status: str
    external_points: int
    external_max_error_percent: float | None
    passed: bool
    refusal_reasons: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioBenchmarkResult:
    scenario_id: str
    split: str
    source_kind: str
    circuit: str
    selected_order: int | None
    status: str
    refusal_reason: str | None
    evaluations: tuple[OrderEvaluation, ...]
    provenance: dict


def load_frozen_manifest(path: str | Path) -> tuple[dict, BenchmarkCriteria, tuple[BenchmarkScenario, ...]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Поддерживается только схема корпуса версии 1.")
    orders = payload.get("orders")
    if (
        not isinstance(orders, list)
        or not orders
        or any(not isinstance(order, int) or order < 1 for order in orders)
        or orders != sorted(set(orders))
    ):
        raise ValueError("Порядки должны быть непустым возрастающим списком уникальных целых чисел.")
    raw_criteria = payload.get("criteria") or {}
    criteria = BenchmarkCriteria(**raw_criteria)
    if min(
        criteria.ecm_mean_error_percent,
        criteria.ecm_max_error_percent,
        criteria.realization_max_error_percent,
        criteria.ngspice_max_error_percent,
        criteria.max_resistance_span_decades,
        criteria.max_capacitance_span_decades,
    ) < 0 or criteria.max_sections < 1:
        raise ValueError("Критерии корпуса должны быть неотрицательными.")

    scenarios = []
    identifiers = set()
    for raw in payload.get("scenarios", []):
        scenario_id = str(raw.get("id", ""))
        if not scenario_id or scenario_id in identifiers:
            raise ValueError(f"Идентификатор сценария пуст или повторяется: {scenario_id!r}")
        identifiers.add(scenario_id)
        split = str(raw.get("split", ""))
        source_kind = str(raw.get("source_kind", ""))
        if split not in {"calibration", "holdout"}:
            raise ValueError(f"Неизвестная часть корпуса у {scenario_id}: {split}")
        if source_kind not in {"synthetic", "real_ecm"}:
            raise ValueError(f"Неизвестный источник у {scenario_id}: {source_kind}")
        parameters = tuple(float(value) for value in raw.get("parameters", []))
        frequency_min = float(raw.get("frequency_min_hz", 0.0))
        frequency_max = float(raw.get("frequency_max_hz", 0.0))
        points = int(raw.get("points", 0))
        if (
            not parameters
            or not np.all(np.isfinite(parameters))
            or frequency_min <= 0
            or frequency_max <= frequency_min
            or points < 8
        ):
            raise ValueError(f"Некорректные параметры или частотная сетка у {scenario_id}.")
        scenarios.append(
            BenchmarkScenario(
                scenario_id=scenario_id,
                split=split,
                source_kind=source_kind,
                circuit=str(raw.get("circuit", "")),
                parameters=parameters,
                frequency_min_hz=frequency_min,
                frequency_max_hz=frequency_max,
                points=points,
                provenance=dict(raw.get("provenance") or {}),
            )
        )
    if not scenarios:
        raise ValueError("Замороженный корпус не содержит сценариев.")
    return payload, criteria, tuple(scenarios)


def scenario_response(scenario: BenchmarkScenario) -> tuple[np.ndarray, np.ndarray]:
    frequencies = np.logspace(
        math.log10(scenario.frequency_min_hz),
        math.log10(scenario.frequency_max_hz),
        scenario.points,
    )
    model = CustomCircuit(scenario.circuit, initial_guess=list(scenario.parameters))
    impedance = np.asarray(model.predict(frequencies, use_initial=True), dtype=complex)
    if not np.all(np.isfinite(impedance)):
        raise ValueError(f"ECM {scenario.scenario_id} дала нечисловой импеданс.")
    return frequencies, impedance


def _span_decades(values: Iterable[float]) -> tuple[float | None, float | None, float]:
    positive = np.asarray([float(value) for value in values if np.isfinite(value) and value > 0])
    if positive.size == 0:
        return None, None, 0.0
    minimum = float(np.min(positive))
    maximum = float(np.max(positive))
    return minimum, maximum, float(math.log10(maximum / minimum)) if maximum > minimum else 0.0


def evaluate_order(
    scenario: BenchmarkScenario,
    frequencies: np.ndarray,
    target_impedance: np.ndarray,
    *,
    order: int,
    criteria: BenchmarkCriteria,
    pole_margin_decades: float,
    section_relative_tolerance: float,
    ngspice_executable: str | None,
    realization_strategy: str = "legacy_section_cutoff",
) -> OrderEvaluation:
    model: PassiveRationalModel = fit_passive_rational(
        frequencies,
        target_impedance,
        order=order,
        pole_margin_decades=pole_margin_decades,
        source_circuit=scenario.circuit,
    )
    if realization_strategy == "global_error_budget":
        conditioned = condition_foster_realization(
            model,
            max_error_percent=criteria.realization_max_error_percent,
        )
        sections = conditioned.sections
        original_sections = conditioned.original_sections
        pruned_sections = conditioned.pruned_sections
        realization = validate_foster_round_trip(
            model,
            frequencies,
            sections=sections,
        )
    elif realization_strategy == "legacy_section_cutoff":
        sections = model.foster_sections(
            relative_impedance_tolerance=section_relative_tolerance,
        )
        original_sections = len(
            model.foster_sections(relative_impedance_tolerance=0.0)
        )
        pruned_sections = original_sections - len(sections)
        realization = validate_foster_round_trip(
            model,
            frequencies,
            relative_impedance_tolerance=section_relative_tolerance,
        )
    else:
        raise ValueError(f"Неизвестная стратегия реализации: {realization_strategy}")
    resistance_values = [section.resistance for section in sections]
    if model.direct > 0:
        resistance_values.append(model.direct)
    capacitance_values = [section.capacitance for section in sections]
    r_min, r_max, r_span = _span_decades(resistance_values)
    c_min, c_max, c_span = _span_decades(capacitance_values)

    reasons = []
    if not model.stable:
        reasons.append("unstable")
    if not model.passive:
        reasons.append("non_passive")
    if model.metrics.mean_relative_error_percent > criteria.ecm_mean_error_percent:
        reasons.append("ecm_mean_error")
    if model.metrics.max_relative_error_percent > criteria.ecm_max_error_percent:
        reasons.append("ecm_max_error")
    if realization.max_relative_error_percent > criteria.realization_max_error_percent:
        reasons.append("realization_error")
    if len(sections) > criteria.max_sections:
        reasons.append("too_many_sections")
    if r_span > criteria.max_resistance_span_decades:
        reasons.append("resistance_span")
    if c_span > criteria.max_capacitance_span_decades:
        reasons.append("capacitance_span")
    internal_passed = not reasons

    external_status = "skipped_internal_gate"
    external_points = 0
    external_max = None
    if internal_passed:
        external = run_ngspice_round_trip(
            model,
            executable=ngspice_executable,
            relative_impedance_tolerance=section_relative_tolerance,
            sections=sections,
            max_error_percent=criteria.ngspice_max_error_percent,
        )
        external_status = external.status
        external_points = external.points
        external_max = (
            None if external.metrics is None else external.metrics.max_relative_error_percent
        )
        if external.status != "validated":
            reasons.append(f"ngspice_{external.status}")

    return OrderEvaluation(
        order=order,
        stable=model.stable,
        passive=model.passive,
        ecm_mean_error_percent=model.metrics.mean_relative_error_percent,
        ecm_rms_error_percent=model.metrics.rms_relative_error_percent,
        ecm_max_error_percent=model.metrics.max_relative_error_percent,
        realization_max_error_percent=realization.max_relative_error_percent,
        realization_strategy=realization_strategy,
        original_sections=original_sections,
        pruned_sections=pruned_sections,
        sections=len(sections),
        resistance_min_ohm=r_min,
        resistance_max_ohm=r_max,
        resistance_span_decades=r_span,
        capacitance_min_f=c_min,
        capacitance_max_f=c_max,
        capacitance_span_decades=c_span,
        direct_resistance_ohm=model.direct,
        series_inductance_h=model.derivative,
        internal_passed=internal_passed,
        external_status=external_status,
        external_points=external_points,
        external_max_error_percent=external_max,
        passed=not reasons,
        refusal_reasons=tuple(reasons),
    )


def select_minimal_order(evaluations: Iterable[OrderEvaluation]) -> int | None:
    passed = sorted(item.order for item in evaluations if item.passed)
    return passed[0] if passed else None


def benchmark_scenario(
    scenario: BenchmarkScenario,
    *,
    orders: Iterable[int],
    criteria: BenchmarkCriteria,
    pole_margin_decades: float,
    section_relative_tolerance: float,
    ngspice_executable: str | None,
    realization_strategy: str = "legacy_section_cutoff",
) -> ScenarioBenchmarkResult:
    frequencies, impedance = scenario_response(scenario)
    evaluations = tuple(
        evaluate_order(
            scenario,
            frequencies,
            impedance,
            order=order,
            criteria=criteria,
            pole_margin_decades=pole_margin_decades,
            section_relative_tolerance=section_relative_tolerance,
            ngspice_executable=ngspice_executable,
            realization_strategy=realization_strategy,
        )
        for order in orders
    )
    selected = select_minimal_order(evaluations)
    if selected is None:
        final_reasons = sorted({reason for item in evaluations for reason in item.refusal_reasons})
        refusal_reason = ",".join(final_reasons) if final_reasons else "no_order_passed"
        status = "refused"
    else:
        refusal_reason = None
        status = "passed"
    return ScenarioBenchmarkResult(
        scenario_id=scenario.scenario_id,
        split=scenario.split,
        source_kind=scenario.source_kind,
        circuit=scenario.circuit,
        selected_order=selected,
        status=status,
        refusal_reason=refusal_reason,
        evaluations=evaluations,
        provenance=scenario.provenance,
    )


def summarize_results(results: Iterable[ScenarioBenchmarkResult]) -> dict:
    rows = tuple(results)
    summary = {
        "total": len(rows),
        "passed": sum(row.status == "passed" for row in rows),
        "refused": sum(row.status == "refused" for row in rows),
        "by_split": {},
        "by_source": {},
        "selected_order_counts": {},
    }
    for field, target in (("split", "by_split"), ("source_kind", "by_source")):
        values = sorted({getattr(row, field) for row in rows})
        for value in values:
            subset = [row for row in rows if getattr(row, field) == value]
            summary[target][value] = {
                "total": len(subset),
                "passed": sum(row.status == "passed" for row in subset),
                "refused": sum(row.status == "refused" for row in subset),
            }
    for row in rows:
        key = "none" if row.selected_order is None else str(row.selected_order)
        summary["selected_order_counts"][key] = summary["selected_order_counts"].get(key, 0) + 1
    return summary


def run_benchmark(
    manifest_path: str | Path,
    *,
    ngspice_executable: str | None,
    split: str = "all",
) -> dict:
    manifest, criteria, scenarios = load_frozen_manifest(manifest_path)
    if split not in {"all", "calibration", "holdout"}:
        raise ValueError("Часть корпуса должна быть all, calibration или holdout.")
    selected_scenarios = (
        scenarios if split == "all" else tuple(item for item in scenarios if item.split == split)
    )
    orders = tuple(int(value) for value in manifest["orders"])
    pole_margin = float(manifest["pole_margin_decades"])
    section_tolerance = float(manifest["spice_section_relative_tolerance"])
    realization_strategy = str(
        manifest.get("realization_strategy", "legacy_section_cutoff")
    )
    if realization_strategy not in {
        "legacy_section_cutoff",
        "global_error_budget",
    }:
        raise ValueError(f"Неизвестная стратегия реализации: {realization_strategy}")
    results = tuple(
        benchmark_scenario(
            scenario,
            orders=orders,
            criteria=criteria,
            pole_margin_decades=pole_margin,
            section_relative_tolerance=section_tolerance,
            ngspice_executable=ngspice_executable,
            realization_strategy=realization_strategy,
        )
        for scenario in selected_scenarios
    )
    return {
        "schema_version": 1,
        "analysis": "spice_engineering_corpus",
        "manifest_name": manifest["name"],
        "manifest_path": str(Path(manifest_path)),
        "split": split,
        "orders": list(orders),
        "pole_margin_decades": pole_margin,
        "spice_section_relative_tolerance": section_tolerance,
        "realization_strategy": realization_strategy,
        "criteria": asdict(criteria),
        "summary": summarize_results(results),
        "results": [
            {
                **asdict(result),
                "evaluations": [asdict(item) for item in result.evaluations],
            }
            for result in results
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Массовая проверка инженерных SPICE-моделей на замороженном корпусе."
    )
    parser.add_argument("manifest", help="Путь к замороженному JSON-манифесту.")
    parser.add_argument("--ngspice", help="Путь к консольному исполняемому файлу ngspice.")
    parser.add_argument(
        "--split",
        choices=("all", "calibration", "holdout"),
        default="all",
        help="Часть корпуса для запуска.",
    )
    parser.add_argument("--output", required=True, help="Итоговый JSON-файл.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = run_benchmark(
        args.manifest,
        ngspice_executable=args.ngspice,
        split=args.split,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["refused"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
