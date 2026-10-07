"""Экспорт пассивной дискретной EIS-модели в C для контроллеров."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import csv
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
from .eis_rational import FosterSection, fit_from_ecm_result, relative_error_metrics
from .eis_spice import condition_foster_realization


Q31_SCALE = 1 << 31
Q31_MAX = Q31_SCALE - 1
Q31_MIN = -Q31_SCALE


@dataclass(frozen=True)
class ControllerExportPolicy:
    orders: tuple[int, ...] = (12, 16, 24, 32)
    pole_margin_decades: float = 1.0
    ecm_mean_error_percent: float = 1.0
    ecm_max_error_percent: float = 10.0
    foster_conditioning_error_percent: float = 0.25
    controller_realization_max_error_percent: float = 1.0
    discrete_max_error_percent: float = 5.0
    q31_max_error_percent: float = 0.01
    max_sections: int = 32
    max_frequency_fraction_of_sample_rate: float = 0.01
    validation_points_per_decade: int = 80


@dataclass(frozen=True)
class DiscreteControllerModel:
    sample_period_s: float
    current_full_scale_a: float
    voltage_full_scale_v: float
    frequency_min_hz: float
    frequency_max_hz: float
    direct_resistance_ohm: float
    section_resistances_ohm: np.ndarray
    relaxation_rates_rad_per_s: np.ndarray
    gamma: np.ndarray
    output_gains: np.ndarray
    gamma_q31: np.ndarray
    output_gains_q31: np.ndarray

    def __post_init__(self):
        arrays = (
            np.asarray(self.section_resistances_ohm, dtype=float),
            np.asarray(self.relaxation_rates_rad_per_s, dtype=float),
            np.asarray(self.gamma, dtype=float),
            np.asarray(self.gamma_q31, dtype=np.int64),
        )
        size = arrays[0].size
        if any(array.ndim != 1 or array.size != size for array in arrays):
            raise ValueError("Параметры секций дискретной модели должны быть согласованы.")
        gains = np.asarray(self.output_gains, dtype=float)
        gains_q31 = np.asarray(self.output_gains_q31, dtype=np.int64)
        if gains.shape != (size + 1,) or gains_q31.shape != (size + 1,):
            raise ValueError("Выходные коэффициенты должны включать прямой член и все секции.")
        if (
            self.sample_period_s <= 0
            or self.current_full_scale_a <= 0
            or self.voltage_full_scale_v <= 0
            or self.frequency_min_hz <= 0
            or self.frequency_max_hz <= self.frequency_min_hz
        ):
            raise ValueError("Масштабы, период и частотная полоса должны быть положительными.")
        if (
            np.any(arrays[0] <= 0)
            or np.any(arrays[1] <= 0)
            or np.any(arrays[2] <= 0)
            or np.any(arrays[2] > 1)
            or np.any(gains < 0)
            or not np.isclose(float(np.sum(gains)), 1.0, rtol=1e-10, atol=1e-12)
        ):
            raise ValueError("Дискретная пассивная модель имеет недопустимые коэффициенты.")
        if np.any(arrays[3] <= 0) or np.any(arrays[3] > Q31_MAX):
            raise ValueError("Хотя бы одна секция не разрешается в формате Q31.")
        if np.any(gains_q31 < 0) or int(np.sum(gains_q31)) > Q31_MAX:
            raise ValueError("Выходные коэффициенты Q31 могут вызвать переполнение.")
        object.__setattr__(self, "section_resistances_ohm", arrays[0].copy())
        object.__setattr__(self, "relaxation_rates_rad_per_s", arrays[1].copy())
        object.__setattr__(self, "gamma", arrays[2].copy())
        object.__setattr__(self, "output_gains", gains.copy())
        object.__setattr__(self, "gamma_q31", arrays[3].copy())
        object.__setattr__(self, "output_gains_q31", gains_q31.copy())

    @property
    def section_count(self) -> int:
        return int(self.gamma.size)

    @property
    def total_dc_resistance_ohm(self) -> float:
        return float(self.voltage_full_scale_v / self.current_full_scale_a)

    def frequency_response_float64(self, frequencies_hz) -> np.ndarray:
        frequencies = np.asarray(frequencies_hz, dtype=float)
        delay = np.exp(-1j * 2.0 * np.pi * frequencies * self.sample_period_s)
        response = np.full(frequencies.shape, self.output_gains[0], dtype=complex)
        for gain, gamma in zip(self.output_gains[1:], self.gamma):
            response += gain * gamma / (1.0 - (1.0 - gamma) * delay)
        return self.total_dc_resistance_ohm * response

    def frequency_response_float32(self, frequencies_hz) -> np.ndarray:
        frequencies = np.asarray(frequencies_hz, dtype=float)
        delay = np.exp(-1j * 2.0 * np.pi * frequencies * self.sample_period_s)
        gains = self.output_gains.astype(np.float32).astype(float)
        gamma = self.gamma.astype(np.float32).astype(float)
        response = np.full(frequencies.shape, gains[0], dtype=complex)
        for gain, coefficient in zip(gains[1:], gamma):
            response += gain * coefficient / (
                1.0 - (1.0 - coefficient) * delay
            )
        return self.total_dc_resistance_ohm * response

    def frequency_response_q31(self, frequencies_hz) -> np.ndarray:
        frequencies = np.asarray(frequencies_hz, dtype=float)
        delay = np.exp(-1j * 2.0 * np.pi * frequencies * self.sample_period_s)
        gains = self.output_gains_q31.astype(float) / Q31_SCALE
        gamma = self.gamma_q31.astype(float) / Q31_SCALE
        response = np.full(frequencies.shape, gains[0], dtype=complex)
        for gain, coefficient in zip(gains[1:], gamma):
            response += gain * coefficient / (
                1.0 - (1.0 - coefficient) * delay
            )
        return self.total_dc_resistance_ohm * response

    def simulate_float32(self, current_a) -> np.ndarray:
        current = np.asarray(current_a, dtype=np.float32)
        state = np.zeros(self.section_count, dtype=np.float32)
        gamma = self.gamma.astype(np.float32)
        gains = self.output_gains.astype(np.float32)
        current_scale = np.float32(self.current_full_scale_a)
        voltage_scale = np.float32(self.voltage_full_scale_v)
        output = np.empty(current.size, dtype=np.float32)
        for index, value in enumerate(current):
            normalized = np.clip(value / current_scale, -1.0, 1.0).astype(np.float32)
            state += gamma * (normalized - state)
            normalized_voltage = gains[0] * normalized + np.sum(
                gains[1:] * state,
                dtype=np.float32,
            )
            normalized_voltage = np.clip(normalized_voltage, -1.0, 1.0)
            output[index] = np.float32(normalized_voltage * voltage_scale)
        return output

    def simulate_q31(self, current_q31) -> np.ndarray:
        current = np.asarray(current_q31, dtype=np.int64)
        if np.any(current < Q31_MIN) or np.any(current > Q31_MAX):
            raise ValueError("Вход Q31 выходит за диапазон int32.")
        state = np.zeros(self.section_count, dtype=np.int64)
        output = np.empty(current.size, dtype=np.int64)
        for sample_index, sample in enumerate(current):
            for section_index, coefficient in enumerate(self.gamma_q31):
                delta = int(sample) - int(state[section_index])
                state[section_index] = _saturate_q31(
                    int(state[section_index])
                    + _rounded_shift_q31(int(coefficient) * delta)
                )
            accumulator = int(self.output_gains_q31[0]) * int(sample)
            accumulator += sum(
                int(gain) * int(value)
                for gain, value in zip(self.output_gains_q31[1:], state)
            )
            output[sample_index] = _saturate_q31(_rounded_shift_q31(accumulator))
        return output


@dataclass(frozen=True)
class ControllerPackageResult:
    package_directory: str
    float_header: str
    float_source: str
    q31_header: str
    q31_source: str
    passport_file: str
    selected_order: int
    section_count: int


class ControllerExportRefused(RuntimeError):
    def __init__(self, message: str, *, attempts: Iterable[dict] = ()):
        super().__init__(message)
        self.attempts = tuple(attempts)


def _rounded_shift_q31(product: int) -> int:
    half = 1 << 30
    adjusted = product + half if product >= 0 else product - half
    if adjusted >= 0:
        return adjusted // Q31_SCALE
    return -((-adjusted) // Q31_SCALE)


def _saturate_q31(value: int) -> int:
    return min(Q31_MAX, max(Q31_MIN, int(value)))


def q31_from_normalized(value) -> np.ndarray:
    normalized = np.asarray(value, dtype=float)
    if np.any(~np.isfinite(normalized)) or np.any(normalized < -1) or np.any(normalized > 1):
        raise ValueError("Нормированное значение Q31 должно лежать в [-1, 1].")
    scaled = np.rint(normalized * Q31_SCALE)
    return np.clip(scaled, Q31_MIN, Q31_MAX).astype(np.int64)


def _controller_continuous_response(
    direct_resistance: float,
    sections: Iterable[FosterSection],
    frequencies_hz,
) -> np.ndarray:
    frequencies = np.asarray(frequencies_hz, dtype=float)
    s = 1j * 2.0 * np.pi * frequencies
    response = np.full(frequencies.shape, complex(direct_resistance), dtype=complex)
    for section in sections:
        response += 1.0 / (
            1.0 / section.resistance + s * section.capacitance
        )
    return response


def build_discrete_controller_model(
    *,
    direct_resistance_ohm: float,
    sections: Iterable[FosterSection],
    sample_period_s: float,
    current_full_scale_a: float,
    frequency_min_hz: float,
    frequency_max_hz: float,
) -> DiscreteControllerModel:
    sections = tuple(sections)
    resistances = np.asarray([section.resistance for section in sections], dtype=float)
    rates = np.asarray([section.relaxation_rate for section in sections], dtype=float)
    total_resistance = float(direct_resistance_ohm + np.sum(resistances))
    if total_resistance <= 0 or not np.isfinite(total_resistance):
        raise ValueError("Полное сопротивление постоянному току должно быть положительным.")
    gamma = -np.expm1(-rates * sample_period_s)
    gamma_q31 = np.clip(
        np.rint(gamma * Q31_SCALE),
        0,
        Q31_MAX,
    ).astype(np.int64)
    gains = np.r_[float(direct_resistance_ohm), resistances] / total_resistance
    gains_q31 = np.floor(gains * Q31_MAX).astype(np.int64)
    return DiscreteControllerModel(
        sample_period_s=float(sample_period_s),
        current_full_scale_a=float(current_full_scale_a),
        voltage_full_scale_v=float(current_full_scale_a * total_resistance),
        frequency_min_hz=float(frequency_min_hz),
        frequency_max_hz=float(frequency_max_hz),
        direct_resistance_ohm=float(direct_resistance_ohm),
        section_resistances_ohm=resistances,
        relaxation_rates_rad_per_s=rates,
        gamma=gamma,
        output_gains=gains,
        gamma_q31=gamma_q31,
        output_gains_q31=gains_q31,
    )


def _validate_analysis(analysis: AnalysisResult):
    if not analysis.success or analysis.stage != "complete" or analysis.best is None:
        raise ControllerExportRefused("Нет завершённой научной модели для экспорта.")
    if not analysis.best.success or analysis.best.status not in {"OK", "WARN"}:
        raise ControllerExportRefused(
            f"Статус научной модели {analysis.best.status!r} не допускает экспорт."
        )
    if (
        analysis.kk is None
        or not analysis.kk.success
        or analysis.kk.status not in {"PASS", "WARN"}
    ):
        status = "missing" if analysis.kk is None else analysis.kk.status
        raise ControllerExportRefused(
            f"Проверка Крамерса — Кронига имеет статус {status!r}; экспорт запрещён."
        )


def _validate_policy(policy: ControllerExportPolicy):
    orders = tuple(int(order) for order in policy.orders)
    if not orders or orders != tuple(sorted(set(orders))) or any(order < 1 for order in orders):
        raise ValueError("Порядки должны быть возрастающими уникальными числами.")
    numeric = (
        policy.pole_margin_decades,
        policy.ecm_mean_error_percent,
        policy.ecm_max_error_percent,
        policy.foster_conditioning_error_percent,
        policy.controller_realization_max_error_percent,
        policy.discrete_max_error_percent,
        policy.q31_max_error_percent,
        policy.max_frequency_fraction_of_sample_rate,
    )
    if any(not np.isfinite(value) or value < 0 for value in numeric):
        raise ValueError("Пороги контроллера должны быть конечными и неотрицательными.")
    if (
        policy.max_sections < 1
        or policy.validation_points_per_decade < 8
        or not 0 < policy.max_frequency_fraction_of_sample_rate < 0.5
    ):
        raise ValueError("Некорректная сложность, плотность сетки или полоса контроллера.")
    return orders


def _validation_grid(frequency_min: float, frequency_max: float, points_per_decade: int):
    decades = math.log10(frequency_max / frequency_min)
    count = max(128, int(math.ceil(decades * points_per_decade)) + 1)
    return np.logspace(math.log10(frequency_min), math.log10(frequency_max), count)


def _attempt_order(
    analysis: AnalysisResult,
    measured_frequencies: np.ndarray,
    *,
    order: int,
    sample_period_s: float,
    current_full_scale_a: float,
    requested_max_frequency_hz: float | None,
    policy: ControllerExportPolicy,
):
    try:
        rational = fit_from_ecm_result(
            analysis.best,
            measured_frequencies,
            order=order,
            pole_margin_decades=policy.pole_margin_decades,
        )
        conditioned = condition_foster_realization(
            rational,
            max_error_percent=policy.foster_conditioning_error_percent,
        )
    except Exception as exc:
        return {
            "order": order,
            "status": "refused",
            "refusal_reasons": ["engineering_fit_failed"],
            "error_message": str(exc),
        }, None, None, None

    safe_maximum = min(
        rational.frequency_max_hz,
        policy.max_frequency_fraction_of_sample_rate / sample_period_s,
    )
    maximum = safe_maximum if requested_max_frequency_hz is None else requested_max_frequency_hz
    reasons = []
    if maximum > safe_maximum:
        reasons.append("controller_frequency_above_policy")
    if maximum <= rational.frequency_min_hz:
        reasons.append("controller_band_empty")
        maximum = safe_maximum
    if len(conditioned.sections) > policy.max_sections:
        reasons.append("too_many_sections")

    grid = _validation_grid(
        rational.frequency_min_hz,
        max(maximum, rational.frequency_min_hz * (1.0 + 1e-9)),
        policy.validation_points_per_decade,
    )
    continuous_controller = _controller_continuous_response(
        rational.direct,
        conditioned.sections,
        grid,
    )
    controller_realization = relative_error_metrics(
        rational.evaluate(grid),
        continuous_controller,
    )
    try:
        discrete = build_discrete_controller_model(
            direct_resistance_ohm=rational.direct,
            sections=conditioned.sections,
            sample_period_s=sample_period_s,
            current_full_scale_a=current_full_scale_a,
            frequency_min_hz=rational.frequency_min_hz,
            frequency_max_hz=maximum,
        )
    except Exception as exc:
        reasons.append("discrete_model_failed")
        return {
            "order": order,
            "status": "refused",
            "refusal_reasons": reasons,
            "error_message": str(exc),
            "ecm_error_percent": asdict(rational.metrics),
            "controller_realization_error_percent": asdict(controller_realization),
        }, rational, conditioned, None

    float32_error = relative_error_metrics(
        continuous_controller,
        discrete.frequency_response_float32(grid),
    )
    q31_error = relative_error_metrics(
        discrete.frequency_response_float32(grid),
        discrete.frequency_response_q31(grid),
    )
    if rational.metrics.mean_relative_error_percent > policy.ecm_mean_error_percent:
        reasons.append("ecm_mean_error")
    if rational.metrics.max_relative_error_percent > policy.ecm_max_error_percent:
        reasons.append("ecm_max_error")
    if (
        controller_realization.max_relative_error_percent
        > policy.controller_realization_max_error_percent
    ):
        reasons.append("controller_realization_error")
    if float32_error.max_relative_error_percent > policy.discrete_max_error_percent:
        reasons.append("discrete_frequency_error")
    if q31_error.max_relative_error_percent > policy.q31_max_error_percent:
        reasons.append("q31_frequency_error")
    if np.any(discrete.gamma_q31 <= 0):
        reasons.append("q31_unresolved_state")

    attempt = {
        "order": order,
        "status": "validated" if not reasons else "refused",
        "refusal_reasons": reasons,
        "error_message": "",
        "frequency_band_hz": [
            discrete.frequency_min_hz,
            discrete.frequency_max_hz,
        ],
        "ecm_error_percent": asdict(rational.metrics),
        "controller_realization_error_percent": asdict(controller_realization),
        "float32_discrete_error_percent": asdict(float32_error),
        "q31_quantization_error_percent": asdict(q31_error),
        "original_sections": conditioned.original_sections,
        "pruned_sections": conditioned.pruned_sections,
        "exported_sections": discrete.section_count,
        "series_inductance_removed_h": rational.derivative,
        "current_full_scale_a": discrete.current_full_scale_a,
        "voltage_full_scale_v": discrete.voltage_full_scale_v,
        "q31_gamma_min": (
            int(np.min(discrete.gamma_q31))
            if discrete.section_count
            else None
        ),
        "q31_gain_sum": int(np.sum(discrete.output_gains_q31)),
    }
    return attempt, rational, conditioned, discrete


def _format_float(value: float) -> str:
    literal = f"{float(value):.9g}"
    if "." not in literal and "e" not in literal.lower():
        literal += ".0"
    return literal + "f"


def generate_float32_header(model: DiscreteControllerModel) -> str:
    count = max(1, model.section_count)
    return f"""#ifndef EIS_MODEL_F32_H
#define EIS_MODEL_F32_H

#include <stdint.h>

#define EIS_MODEL_F32_SECTION_COUNT ({model.section_count}u)
#define EIS_MODEL_F32_STATE_STORAGE ({count}u)
#define EIS_MODEL_SAMPLE_PERIOD_S ({_format_float(model.sample_period_s)})
#define EIS_MODEL_CURRENT_FULL_SCALE_A ({_format_float(model.current_full_scale_a)})
#define EIS_MODEL_VOLTAGE_FULL_SCALE_V ({_format_float(model.voltage_full_scale_v)})

typedef struct {{
    float state[EIS_MODEL_F32_STATE_STORAGE];
    uint32_t saturation_count;
}} eis_model_f32_t;

void eis_model_f32_reset(eis_model_f32_t *model);
float eis_model_f32_step(eis_model_f32_t *model, float current_a);

#endif
"""


def generate_float32_source(model: DiscreteControllerModel) -> str:
    gamma = ", ".join(_format_float(value) for value in model.gamma) or "0.0f"
    gains = ", ".join(_format_float(value) for value in model.output_gains)
    return f"""#include "eis_model_f32.h"

static const float EIS_GAMMA[EIS_MODEL_F32_STATE_STORAGE] = {{{gamma}}};
static const float EIS_GAIN[EIS_MODEL_F32_SECTION_COUNT + 1u] = {{{gains}}};

static float eis_clamp_unit(float value, uint32_t *counter) {{
    if (value > 1.0f) {{
        ++(*counter);
        return 1.0f;
    }}
    if (value < -1.0f) {{
        ++(*counter);
        return -1.0f;
    }}
    return value;
}}

void eis_model_f32_reset(eis_model_f32_t *model) {{
    uint32_t k;
    for (k = 0u; k < EIS_MODEL_F32_STATE_STORAGE; ++k) {{
        model->state[k] = 0.0f;
    }}
    model->saturation_count = 0u;
}}

float eis_model_f32_step(eis_model_f32_t *model, float current_a) {{
    uint32_t k;
    float input = eis_clamp_unit(
        current_a / EIS_MODEL_CURRENT_FULL_SCALE_A,
        &model->saturation_count
    );
    float output = EIS_GAIN[0] * input;
    for (k = 0u; k < EIS_MODEL_F32_SECTION_COUNT; ++k) {{
        model->state[k] += EIS_GAMMA[k] * (input - model->state[k]);
        output += EIS_GAIN[k + 1u] * model->state[k];
    }}
    output = eis_clamp_unit(output, &model->saturation_count);
    return output * EIS_MODEL_VOLTAGE_FULL_SCALE_V;
}}
"""


def generate_q31_header(model: DiscreteControllerModel) -> str:
    count = max(1, model.section_count)
    return f"""#ifndef EIS_MODEL_Q31_H
#define EIS_MODEL_Q31_H

#include <stdint.h>

#define EIS_MODEL_Q31_SECTION_COUNT ({model.section_count}u)
#define EIS_MODEL_Q31_STATE_STORAGE ({count}u)

typedef struct {{
    int32_t state[EIS_MODEL_Q31_STATE_STORAGE];
    uint32_t saturation_count;
}} eis_model_q31_t;

void eis_model_q31_reset(eis_model_q31_t *model);
int32_t eis_model_q31_step(eis_model_q31_t *model, int32_t current_q31);

#endif
"""


def generate_q31_source(model: DiscreteControllerModel) -> str:
    gamma = ", ".join(str(int(value)) for value in model.gamma_q31) or "0"
    gains = ", ".join(str(int(value)) for value in model.output_gains_q31)
    return f"""#include "eis_model_q31.h"

#include <limits.h>

static const int32_t EIS_GAMMA_Q31[EIS_MODEL_Q31_STATE_STORAGE] = {{{gamma}}};
static const int32_t EIS_GAIN_Q31[EIS_MODEL_Q31_SECTION_COUNT + 1u] = {{{gains}}};

static int32_t eis_sat_q31(int64_t value, uint32_t *counter) {{
    if (value > INT32_MAX) {{
        ++(*counter);
        return INT32_MAX;
    }}
    if (value < INT32_MIN) {{
        ++(*counter);
        return INT32_MIN;
    }}
    return (int32_t)value;
}}

static int64_t eis_round_q31(int64_t product) {{
    const int64_t half = ((int64_t)1 << 30);
    if (product >= 0) {{
        product += half;
    }} else {{
        product -= half;
    }}
    return product / ((int64_t)1 << 31);
}}

void eis_model_q31_reset(eis_model_q31_t *model) {{
    uint32_t k;
    for (k = 0u; k < EIS_MODEL_Q31_STATE_STORAGE; ++k) {{
        model->state[k] = 0;
    }}
    model->saturation_count = 0u;
}}

int32_t eis_model_q31_step(eis_model_q31_t *model, int32_t current_q31) {{
    uint32_t k;
    int64_t accumulator = (int64_t)EIS_GAIN_Q31[0] * current_q31;
    for (k = 0u; k < EIS_MODEL_Q31_SECTION_COUNT; ++k) {{
        int64_t delta = (int64_t)current_q31 - model->state[k];
        int64_t increment = eis_round_q31((int64_t)EIS_GAMMA_Q31[k] * delta);
        model->state[k] = eis_sat_q31(
            (int64_t)model->state[k] + increment,
            &model->saturation_count
        );
        accumulator += (int64_t)EIS_GAIN_Q31[k + 1u] * model->state[k];
    }}
    return eis_sat_q31(eis_round_q31(accumulator), &model->saturation_count);
}}
"""


def _reference_current(model: DiscreteControllerModel, count: int = 256) -> np.ndarray:
    index = np.arange(count, dtype=float)
    normalized = np.zeros(count, dtype=float)
    normalized[16:48] = 0.25
    normalized[48:80] = -0.5
    normalized[80:] = (
        0.45 * np.sin(2.0 * np.pi * index[80:] / 37.0)
        + 0.15 * np.sin(2.0 * np.pi * index[80:] / 11.0)
    )
    return np.clip(normalized, -0.9, 0.9) * model.current_full_scale_a


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
    if isinstance(value, np.ndarray):
        return [_json_safe(item) for item in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


def export_controller_package(
    analysis: AnalysisResult,
    frequencies_hz,
    output_directory: str | Path,
    *,
    sample_period_s: float,
    current_full_scale_a: float,
    max_frequency_hz: float | None = None,
    source_file: str | Path | None = None,
    policy: ControllerExportPolicy = ControllerExportPolicy(),
) -> ControllerPackageResult:
    """Атомарно выдать C-пакет с вариантами float32 и Q31."""

    _validate_analysis(analysis)
    orders = _validate_policy(policy)
    frequencies = np.asarray(frequencies_hz, dtype=float)
    if (
        frequencies.ndim != 1
        or frequencies.size < 4
        or np.any(~np.isfinite(frequencies))
        or np.any(frequencies <= 0)
    ):
        raise ValueError("Для экспорта нужны конечные положительные частоты.")
    if not np.isfinite(sample_period_s) or sample_period_s <= 0:
        raise ValueError("Период дискретизации должен быть положительным.")
    if not np.isfinite(current_full_scale_a) or current_full_scale_a <= 0:
        raise ValueError("Полный масштаб тока должен быть положительным.")
    if max_frequency_hz is not None and (
        not np.isfinite(max_frequency_hz) or max_frequency_hz <= 0
    ):
        raise ValueError("Верхняя частота контроллера должна быть положительной.")

    source_path = Path(source_file or analysis.file_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"Исходный файл не найден: {source_path}")
    source_sha256 = _sha256(source_path)
    target = Path(output_directory)
    if target.exists():
        raise FileExistsError(
            f"Каталог пакета контроллера уже существует: {target}"
        )

    attempts = []
    selected = None
    for order in orders:
        attempt, rational, conditioned, discrete = _attempt_order(
            analysis,
            frequencies,
            order=order,
            sample_period_s=sample_period_s,
            current_full_scale_a=current_full_scale_a,
            requested_max_frequency_hz=max_frequency_hz,
            policy=policy,
        )
        attempts.append(attempt)
        if attempt["status"] == "validated":
            selected = (order, rational, conditioned, discrete)
            break
    if selected is None:
        compact = "; ".join(
            f"{item['order']}:{','.join(item['refusal_reasons']) or 'unknown'}"
            for item in attempts
        )
        raise ControllerExportRefused(
            f"Ни один порядок не прошёл ворота контроллера: {compact}",
            attempts=attempts,
        )
    if _sha256(source_path) != source_sha256:
        raise ControllerExportRefused(
            "Исходный файл изменился во время проверки; экспорт отменён.",
            attempts=attempts,
        )

    order, rational, conditioned, discrete = selected
    current = _reference_current(discrete)
    current_q31 = q31_from_normalized(current / discrete.current_full_scale_a)
    voltage_float32 = discrete.simulate_float32(current)
    voltage_q31 = discrete.simulate_q31(current_q31)
    voltage_q31_v = (
        voltage_q31.astype(float) / Q31_SCALE * discrete.voltage_full_scale_v
    )
    time_difference = np.abs(voltage_float32.astype(float) - voltage_q31_v)
    max_time_difference_v = float(np.max(time_difference))

    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=parent))
    try:
        files = {
            "float_header": temporary / "eis_model_f32.h",
            "float_source": temporary / "eis_model_f32.c",
            "q31_header": temporary / "eis_model_q31.h",
            "q31_source": temporary / "eis_model_q31.c",
            "vectors": temporary / "reference_vectors.csv",
            "passport": temporary / "passport.json",
        }
        files["float_header"].write_text(generate_float32_header(discrete), encoding="ascii")
        files["float_source"].write_text(generate_float32_source(discrete), encoding="ascii")
        files["q31_header"].write_text(generate_q31_header(discrete), encoding="ascii")
        files["q31_source"].write_text(generate_q31_source(discrete), encoding="ascii")
        with files["vectors"].open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "sample",
                    "current_a",
                    "current_q31",
                    "voltage_float32_v",
                    "voltage_q31",
                    "voltage_q31_v",
                ]
            )
            for row in zip(
                range(current.size),
                current,
                current_q31,
                voltage_float32,
                voltage_q31,
                voltage_q31_v,
            ):
                writer.writerow(row)

        passport = {
            "schema_version": 1,
            "artifact_type": "validated_controller_c_package",
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
            "policy": asdict(policy),
            "controller_model": {
                "input": "current",
                "output": "voltage",
                "float32_units": {"input": "A", "output": "V"},
                "q31_units": {
                    "input": "current/current_full_scale",
                    "output": "voltage/voltage_full_scale",
                },
                "selected_order": order,
                "sample_period_s": discrete.sample_period_s,
                "sample_rate_hz": 1.0 / discrete.sample_period_s,
                "frequency_band_hz": [
                    discrete.frequency_min_hz,
                    discrete.frequency_max_hz,
                ],
                "current_full_scale_a": discrete.current_full_scale_a,
                "voltage_full_scale_v": discrete.voltage_full_scale_v,
                "total_dc_resistance_ohm": discrete.total_dc_resistance_ohm,
                "section_count": discrete.section_count,
                "direct_resistance_ohm": discrete.direct_resistance_ohm,
                "section_resistances_ohm": discrete.section_resistances_ohm,
                "relaxation_rates_rad_per_s": discrete.relaxation_rates_rad_per_s,
                "gamma": discrete.gamma,
                "output_gains": discrete.output_gains,
                "gamma_q31": discrete.gamma_q31,
                "output_gains_q31": discrete.output_gains_q31,
                "q31_format": "signed Q1.31",
                "q31_accumulator": "int64_t",
                "max_reference_time_difference_v": max_time_difference_v,
                "selected_attempt": attempts[-1],
                "attempts": attempts,
            },
            "files": {},
        }
        for name, path in files.items():
            if name == "passport":
                continue
            passport["files"][name] = {
                "name": path.name,
                "sha256": _sha256(path),
            }
        passport["files"]["passport"] = {"name": "passport.json"}
        files["passport"].write_text(
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

    return ControllerPackageResult(
        package_directory=str(target),
        float_header=str(target / "eis_model_f32.h"),
        float_source=str(target / "eis_model_f32.c"),
        q31_header=str(target / "eis_model_q31.h"),
        q31_source=str(target / "eis_model_q31.c"),
        passport_file=str(target / "passport.json"),
        selected_order=order,
        section_count=discrete.section_count,
    )
