"""Passive rational impedance models for engineering export.

The scientific ECM remains the source of physical interpretation.  This module
approximates its impedance response with a stable, passive Foster model that
can be realized by ordinary R, C, and L components.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Sequence

import numpy as np
from scipy.optimize import lsq_linear


@dataclass(frozen=True)
class RationalFitMetrics:
    mean_relative_error_percent: float
    rms_relative_error_percent: float
    max_relative_error_percent: float


@dataclass(frozen=True)
class FosterSection:
    resistance: float
    capacitance: float
    relaxation_rate: float
    residue: float


@dataclass(frozen=True)
class PassiveRationalModel:
    """Impedance ``d + s*e + sum(r_k / (s + a_k))``.

    ``a_k > 0`` and all coefficients are non-negative.  The poles are
    therefore real and strictly negative, and every term has a passive Foster
    R-C realization.  ``d`` is a series resistor and ``e`` a series inductor.
    """

    relaxation_rates: np.ndarray
    residues: np.ndarray
    direct: float
    derivative: float
    frequency_min_hz: float
    frequency_max_hz: float
    metrics: RationalFitMetrics
    source_circuit: str | None = None

    def __post_init__(self) -> None:
        rates = np.asarray(self.relaxation_rates, dtype=float)
        residues = np.asarray(self.residues, dtype=float)
        if rates.ndim != 1 or residues.ndim != 1 or rates.shape != residues.shape:
            raise ValueError("Relaxation rates and residues must be aligned 1-D arrays.")
        if rates.size == 0 or not np.all(np.isfinite(rates)) or np.any(rates <= 0):
            raise ValueError("Relaxation rates must be finite and strictly positive.")
        coefficients = np.r_[float(self.direct), float(self.derivative), residues]
        if not np.all(np.isfinite(coefficients)) or np.any(coefficients < 0):
            raise ValueError("A passive rational model requires finite non-negative coefficients.")
        if (
            not np.isfinite(self.frequency_min_hz)
            or not np.isfinite(self.frequency_max_hz)
            or self.frequency_min_hz <= 0
            or self.frequency_max_hz <= self.frequency_min_hz
        ):
            raise ValueError("The model frequency band must be finite, positive, and increasing.")
        object.__setattr__(self, "relaxation_rates", rates.copy())
        object.__setattr__(self, "residues", residues.copy())

    @property
    def poles(self) -> np.ndarray:
        return -self.relaxation_rates.copy()

    @property
    def stable(self) -> bool:
        return bool(np.all(self.relaxation_rates > 0))

    @property
    def passive(self) -> bool:
        return bool(
            self.direct >= 0
            and self.derivative >= 0
            and np.all(self.residues >= 0)
        )

    def evaluate(self, frequencies_hz: Sequence[float]) -> np.ndarray:
        frequencies = _validate_frequencies(frequencies_hz, minimum_points=1)
        s = 1j * 2.0 * np.pi * frequencies
        response = np.full(s.shape, complex(self.direct), dtype=complex)
        response += self.derivative * s
        response += np.sum(
            self.residues[None, :] / (s[:, None] + self.relaxation_rates[None, :]),
            axis=1,
        )
        return response

    def foster_sections(
        self,
        *,
        residue_tolerance: float = 0.0,
        relative_impedance_tolerance: float = 1e-12,
    ) -> tuple[FosterSection, ...]:
        if residue_tolerance < 0:
            raise ValueError("Residue tolerance cannot be negative.")
        if relative_impedance_tolerance < 0:
            raise ValueError("Relative impedance tolerance cannot be negative.")
        band_grid = np.logspace(
            np.log10(self.frequency_min_hz),
            np.log10(self.frequency_max_hz),
            128,
        )
        impedance_floor = max(
            float(np.min(np.abs(self.evaluate(band_grid)))),
            np.finfo(float).eps,
        )
        minimum_section_resistance = relative_impedance_tolerance * impedance_floor
        sections = []
        for rate, residue in zip(self.relaxation_rates, self.residues):
            if residue <= residue_tolerance:
                continue
            resistance = float(residue / rate)
            capacitance = float(1.0 / residue) if residue >= 1.0 / np.finfo(float).max else np.inf
            # Bounded least squares may return positive subnormal tails.  They
            # are mathematically harmless but cannot be represented by finite
            # SPICE component values, so they are not exported as sections.
            if (
                not np.isfinite(resistance)
                or not np.isfinite(capacitance)
                or resistance <= 0
                or capacitance <= 0
                or resistance <= minimum_section_resistance
            ):
                continue
            sections.append(
                FosterSection(
                    resistance=resistance,
                    capacitance=capacitance,
                    relaxation_rate=float(rate),
                    residue=float(residue),
                )
            )
        return tuple(sections)

    def to_dict(self) -> dict:
        return {
            "representation": "passive_foster_impedance_v1",
            "formula": "Z(s)=d+s*e+sum(r_k/(s+a_k))",
            "source_circuit": self.source_circuit,
            "frequency_band_hz": [
                float(self.frequency_min_hz),
                float(self.frequency_max_hz),
            ],
            "direct_resistance_ohm": float(self.direct),
            "series_inductance_h": float(self.derivative),
            "relaxation_rates_rad_per_s": self.relaxation_rates.tolist(),
            "poles_rad_per_s": self.poles.tolist(),
            "residues_ohm_per_s": self.residues.tolist(),
            "stable": self.stable,
            "passive": self.passive,
            "metrics": asdict(self.metrics),
            "foster_sections": [asdict(section) for section in self.foster_sections()],
        }


def relative_error_metrics(target: Sequence[complex], predicted: Sequence[complex]) -> RationalFitMetrics:
    target_array = np.asarray(target, dtype=complex)
    predicted_array = np.asarray(predicted, dtype=complex)
    if target_array.shape != predicted_array.shape or target_array.ndim != 1:
        raise ValueError("Target and predicted impedance must be aligned 1-D arrays.")
    if target_array.size == 0 or not np.all(np.isfinite(target_array)):
        raise ValueError("Target impedance must be non-empty and finite.")
    if not np.all(np.isfinite(predicted_array)):
        raise ValueError("Predicted impedance must be finite.")
    denominator = np.maximum(np.abs(target_array), np.finfo(float).eps)
    relative = np.abs(predicted_array - target_array) / denominator
    return RationalFitMetrics(
        mean_relative_error_percent=float(100.0 * np.mean(relative)),
        rms_relative_error_percent=float(100.0 * np.sqrt(np.mean(relative**2))),
        max_relative_error_percent=float(100.0 * np.max(relative)),
    )


def fit_passive_rational(
    frequencies_hz: Sequence[float],
    target_impedance: Sequence[complex],
    *,
    order: int = 16,
    pole_margin_decades: float = 1.0,
    include_derivative: bool = True,
    source_circuit: str | None = None,
) -> PassiveRationalModel:
    """Fit a stable passive Foster approximation with fixed real poles.

    The complex least-squares problem is weighted by ``1 / |Z|`` and solved
    with non-negative coefficient bounds.  Fixed log-spaced relaxation rates
    make the passivity constraint transparent and deterministic.
    """

    frequencies = _validate_frequencies(frequencies_hz, minimum_points=4)
    impedance = np.asarray(target_impedance, dtype=complex)
    if impedance.ndim != 1 or impedance.shape != frequencies.shape:
        raise ValueError("Frequencies and target impedance must be aligned 1-D arrays.")
    if not np.all(np.isfinite(impedance)):
        raise ValueError("Target impedance must be finite.")
    if not isinstance(order, (int, np.integer)) or order < 1:
        raise ValueError("Rational order must be a positive integer.")
    if not np.isfinite(pole_margin_decades) or pole_margin_decades < 0:
        raise ValueError("Pole margin must be finite and non-negative.")

    frequency_min = float(np.min(frequencies))
    frequency_max = float(np.max(frequencies))
    pole_frequencies = np.logspace(
        np.log10(frequency_min) - pole_margin_decades,
        np.log10(frequency_max) + pole_margin_decades,
        int(order),
    )
    rates = 2.0 * np.pi * pole_frequencies
    s = 1j * 2.0 * np.pi * frequencies

    columns = [np.ones_like(s)]
    if include_derivative:
        columns.append(s)
    columns.extend(1.0 / (s + rate) for rate in rates)
    complex_design = np.column_stack(columns)

    weights = 1.0 / np.maximum(np.abs(impedance), np.finfo(float).eps)
    weighted_design = complex_design * weights[:, None]
    weighted_target = impedance * weights
    design = np.vstack((weighted_design.real, weighted_design.imag))
    target = np.r_[weighted_target.real, weighted_target.imag]

    column_norms = np.linalg.norm(design, axis=0)
    column_norms = np.maximum(column_norms, np.finfo(float).eps)
    scaled_design = design / column_norms[None, :]
    solution = lsq_linear(
        scaled_design,
        target,
        bounds=(0.0, np.inf),
        method="trf",
        tol=1e-12,
        lsmr_tol=1e-12,
        max_iter=2_000,
    )
    if not solution.success:
        raise RuntimeError(f"Passive rational fit failed: {solution.message}")
    coefficients = np.maximum(solution.x / column_norms, 0.0)

    direct = float(coefficients[0])
    if include_derivative:
        derivative = float(coefficients[1])
        residues = coefficients[2:]
    else:
        derivative = 0.0
        residues = coefficients[1:]

    provisional_metrics = RationalFitMetrics(np.inf, np.inf, np.inf)
    model = PassiveRationalModel(
        relaxation_rates=rates,
        residues=residues,
        direct=direct,
        derivative=derivative,
        frequency_min_hz=frequency_min,
        frequency_max_hz=frequency_max,
        metrics=provisional_metrics,
        source_circuit=source_circuit,
    )
    metrics = relative_error_metrics(impedance, model.evaluate(frequencies))
    return PassiveRationalModel(
        relaxation_rates=rates,
        residues=residues,
        direct=direct,
        derivative=derivative,
        frequency_min_hz=frequency_min,
        frequency_max_hz=frequency_max,
        metrics=metrics,
        source_circuit=source_circuit,
    )


def fit_from_ecm_result(
    fit_result,
    frequencies_hz: Sequence[float],
    **fit_options,
) -> PassiveRationalModel:
    """Build an engineering model from an admissible scientific fit result.

    This bridge deliberately evaluates the already fitted ECM.  It never
    refits raw experimental impedance and fails closed for rejected fits.
    """

    if not getattr(fit_result, "success", False):
        raise ValueError("A failed scientific ECM fit cannot be exported.")
    status = str(getattr(fit_result, "status", "")).upper()
    if status not in {"OK", "WARN"}:
        raise ValueError(
            f"Scientific ECM status {status or 'missing'} is not exportable."
        )
    model = getattr(fit_result, "model", None)
    if model is None or not callable(getattr(model, "predict", None)):
        raise ValueError("The scientific fit does not contain a predictive ECM.")
    frequencies = _validate_frequencies(frequencies_hz, minimum_points=4)
    response = np.asarray(model.predict(frequencies), dtype=complex)
    options = dict(fit_options)
    options.setdefault("source_circuit", getattr(fit_result, "circuit_string", None))
    return fit_passive_rational(frequencies, response, **options)


def _validate_frequencies(
    frequencies_hz: Sequence[float],
    *,
    minimum_points: int,
) -> np.ndarray:
    frequencies = np.asarray(frequencies_hz, dtype=float)
    if frequencies.ndim != 1 or frequencies.size < minimum_points:
        raise ValueError(f"At least {minimum_points} frequency points are required.")
    if not np.all(np.isfinite(frequencies)) or np.any(frequencies <= 0):
        raise ValueError("Frequencies must be finite and strictly positive.")
    return frequencies
