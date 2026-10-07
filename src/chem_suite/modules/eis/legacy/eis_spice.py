"""Portable SPICE export for passive rational EIS macromodels."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Sequence

import numpy as np

from .eis_rational import (
    FosterSection,
    PassiveRationalModel,
    RationalFitMetrics,
    relative_error_metrics,
)


SPICE_SECTION_RELATIVE_TOLERANCE = 2e-3


@dataclass(frozen=True)
class SpiceRuntimeStatus:
    status: str
    executable: str | None
    message: str


@dataclass(frozen=True)
class NgspiceRoundTripResult:
    status: str
    executable: str | None
    simulator_version: str | None
    points: int
    metrics: RationalFitMetrics | None
    return_code: int | None
    message: str


@dataclass(frozen=True)
class FosterConditioningResult:
    sections: tuple[FosterSection, ...]
    metrics: RationalFitMetrics
    original_sections: int
    pruned_sections: int
    grid_points: int
    max_error_percent: float


def _sections_impedance(
    model: PassiveRationalModel,
    frequencies_hz: np.ndarray,
    sections: Sequence[FosterSection],
) -> np.ndarray:
    s = 1j * 2.0 * np.pi * frequencies_hz
    impedance = np.full(s.shape, complex(model.direct), dtype=complex)
    impedance += model.derivative * s
    for section in sections:
        impedance += 1.0 / (
            1.0 / section.resistance + s * section.capacitance
        )
    return impedance


def condition_foster_realization(
    model: PassiveRationalModel,
    *,
    max_error_percent: float,
    points_per_decade: int = 80,
) -> FosterConditioningResult:
    """Prune weak sections under one global measured-band error budget.

    Sections are considered from the smallest to the largest maximum relative
    contribution in the declared frequency band. A removal is retained only
    when the error of the complete remaining network stays within the budget.
    """

    if not np.isfinite(max_error_percent) or max_error_percent < 0:
        raise ValueError("Conditioning error budget must be finite and non-negative.")
    if points_per_decade < 1:
        raise ValueError("Conditioning points per decade must be positive.")

    decades = np.log10(model.frequency_max_hz / model.frequency_min_hz)
    grid_points = max(128, int(np.ceil(decades * points_per_decade)) + 1)
    frequencies = np.logspace(
        np.log10(model.frequency_min_hz),
        np.log10(model.frequency_max_hz),
        grid_points,
    )
    target = model.evaluate(frequencies)
    denominator = np.maximum(np.abs(target), np.finfo(float).eps)
    candidates = model.foster_sections(relative_impedance_tolerance=0.0)
    selected = np.ones(len(candidates), dtype=bool)

    contributions = []
    s = 1j * 2.0 * np.pi * frequencies
    for section in candidates:
        response = 1.0 / (
            1.0 / section.resistance + s * section.capacitance
        )
        contributions.append(float(np.max(np.abs(response) / denominator)))

    for index in np.argsort(np.asarray(contributions), kind="stable"):
        trial = selected.copy()
        trial[index] = False
        trial_sections = tuple(
            section for keep, section in zip(trial, candidates) if keep
        )
        trial_metrics = relative_error_metrics(
            target,
            _sections_impedance(model, frequencies, trial_sections),
        )
        if trial_metrics.max_relative_error_percent <= max_error_percent:
            selected = trial

    sections = tuple(
        section for keep, section in zip(selected, candidates) if keep
    )
    metrics = relative_error_metrics(
        target,
        _sections_impedance(model, frequencies, sections),
    )
    return FosterConditioningResult(
        sections=sections,
        metrics=metrics,
        original_sections=len(candidates),
        pruned_sections=len(candidates) - len(sections),
        grid_points=grid_points,
        max_error_percent=float(max_error_percent),
    )


def foster_impedance(
    model: PassiveRationalModel,
    frequencies_hz: Sequence[float],
    *,
    residue_tolerance: float = 0.0,
    relative_impedance_tolerance: float = 1e-12,
    sections: Sequence[FosterSection] | None = None,
) -> np.ndarray:
    """Evaluate the ordinary R/C/L network represented by the model."""

    frequencies = np.asarray(frequencies_hz, dtype=float)
    if frequencies.ndim != 1 or frequencies.size == 0:
        raise ValueError("At least one frequency point is required.")
    if not np.all(np.isfinite(frequencies)) or np.any(frequencies <= 0):
        raise ValueError("Frequencies must be finite and strictly positive.")
    selected_sections = (
        tuple(sections)
        if sections is not None
        else model.foster_sections(
            residue_tolerance=residue_tolerance,
            relative_impedance_tolerance=relative_impedance_tolerance,
        )
    )
    return _sections_impedance(model, frequencies, selected_sections)


def validate_foster_round_trip(
    model: PassiveRationalModel,
    frequencies_hz: Sequence[float],
    *,
    residue_tolerance: float = 0.0,
    relative_impedance_tolerance: float = 1e-12,
    sections: Sequence[FosterSection] | None = None,
):
    """Compare the rational IR with its component-level Foster realization."""

    rational = model.evaluate(frequencies_hz)
    network = foster_impedance(
        model,
        frequencies_hz,
        residue_tolerance=residue_tolerance,
        relative_impedance_tolerance=relative_impedance_tolerance,
        sections=sections,
    )
    return relative_error_metrics(rational, network)


def export_foster_subcircuit(
    model: PassiveRationalModel,
    *,
    subcircuit_name: str = "EIS_MODEL",
    residue_tolerance: float = 0.0,
    relative_impedance_tolerance: float = SPICE_SECTION_RELATIVE_TOLERANCE,
    sections: Sequence[FosterSection] | None = None,
) -> str:
    """Return a simulator-portable series Foster ``.subckt``.

    The output uses only standard R, C, and L elements.  Each rational
    relaxation term becomes one parallel R-C branch in the series chain.
    """

    name = _validate_identifier(subcircuit_name)
    if residue_tolerance < 0:
        raise ValueError("Residue tolerance cannot be negative.")

    elements: list[tuple[str, tuple]] = []
    if model.direct > 0:
        elements.append(("R", ("DIRECT", model.direct)))
    if model.derivative > 0:
        elements.append(("L", ("SERIES", model.derivative)))
    selected_sections = (
        tuple(sections)
        if sections is not None
        else model.foster_sections(
            residue_tolerance=residue_tolerance,
            relative_impedance_tolerance=relative_impedance_tolerance,
        )
    )
    for index, section in enumerate(selected_sections, start=1):
        elements.append(("RC", (f"F{index}", section.resistance, section.capacitance)))
    if not elements:
        raise ValueError("The model has no non-zero component to export.")

    lines = [
        "* EIS Solver passive Foster macromodel",
        f"* source_circuit={model.source_circuit or 'unspecified'}",
        (
            "* valid_band_hz="
            f"{_format_number(model.frequency_min_hz)}:"
            f"{_format_number(model.frequency_max_hz)}"
        ),
        (
            "* approximation_error_percent="
            f"mean:{_format_number(model.metrics.mean_relative_error_percent)},"
            f"rms:{_format_number(model.metrics.rms_relative_error_percent)},"
            f"max:{_format_number(model.metrics.max_relative_error_percent)}"
        ),
        f".subckt {name} p n",
    ]

    current_node = "p"
    for index, (kind, values) in enumerate(elements, start=1):
        next_node = "n" if index == len(elements) else f"x{index}"
        if kind == "R":
            label, resistance = values
            lines.append(
                f"R_{label} {current_node} {next_node} {_format_number(resistance)}"
            )
        elif kind == "L":
            label, inductance = values
            lines.append(
                f"L_{label} {current_node} {next_node} {_format_number(inductance)}"
            )
        else:
            label, resistance, capacitance = values
            lines.append(
                f"R_{label} {current_node} {next_node} {_format_number(resistance)}"
            )
            lines.append(
                f"C_{label} {current_node} {next_node} {_format_number(capacitance)}"
            )
        current_node = next_node
    lines.extend((f".ends {name}", ""))
    return "\n".join(lines)


def build_ngspice_validation_deck(
    model_filename: str,
    model: PassiveRationalModel,
    *,
    subcircuit_name: str = "EIS_MODEL",
    points_per_decade: int = 40,
) -> str:
    """Build an ngspice AC deck with a one-ampere test current."""

    if points_per_decade < 1:
        raise ValueError("Points per decade must be positive.")
    name = _validate_identifier(subcircuit_name)
    safe_filename = str(model_filename).replace("\\", "/")
    return "\n".join(
        (
            "* EIS Solver ngspice round-trip deck",
            f'.include "{safe_filename}"',
            "I_TEST 0 in AC 1",
            f"X_DUT in 0 {name}",
            (
                f".ac dec {int(points_per_decade)} "
                f"{_format_number(model.frequency_min_hz)} "
                f"{_format_number(model.frequency_max_hz)}"
            ),
            ".control",
            "set numdgt=15",
            "run",
            "print frequency vr(in) vi(in)",
            "quit",
            ".endc",
            ".end",
            "",
        )
    )


def parse_ngspice_ac_output(output: str) -> tuple[np.ndarray, np.ndarray]:
    """Parse ``frequency vr(node) vi(node)`` rows from ngspice text output."""

    number = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?"
    row_pattern = re.compile(
        rf"^\s*\d+\s+({number})\s+({number})\s+({number})\s*$",
        flags=re.MULTILINE,
    )
    rows = row_pattern.findall(output)
    if not rows:
        raise ValueError("No complex AC rows were found in ngspice output.")
    frequencies = np.asarray([float(row[0]) for row in rows], dtype=float)
    impedance = np.asarray(
        [complex(float(row[1]), float(row[2])) for row in rows],
        dtype=complex,
    )
    if (
        not np.all(np.isfinite(frequencies))
        or not np.all(np.isfinite(impedance))
        or np.any(frequencies <= 0)
    ):
        raise ValueError("ngspice AC output contains invalid numeric values.")
    return frequencies, impedance


def run_ngspice_round_trip(
    model: PassiveRationalModel,
    *,
    executable: str | None = None,
    subcircuit_name: str = "EIS_MODEL",
    points_per_decade: int = 40,
    relative_impedance_tolerance: float = SPICE_SECTION_RELATIVE_TOLERANCE,
    sections: Sequence[FosterSection] | None = None,
    max_error_percent: float = 1e-6,
    timeout_seconds: float = 60.0,
) -> NgspiceRoundTripResult:
    """Export, simulate, parse, and compare a model with ngspice."""

    runtime = detect_ngspice(executable)
    if runtime.status != "available":
        return NgspiceRoundTripResult(
            status="runtime_missing",
            executable=None,
            simulator_version=None,
            points=0,
            metrics=None,
            return_code=None,
            message=runtime.message,
        )
    if max_error_percent < 0 or not np.isfinite(max_error_percent):
        raise ValueError("Maximum external round-trip error must be finite and non-negative.")
    if timeout_seconds <= 0 or not np.isfinite(timeout_seconds):
        raise ValueError("ngspice timeout must be finite and positive.")

    executable_path = str(runtime.executable)
    try:
        version_process = subprocess.run(
            [executable_path, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        version_text = f"{version_process.stdout}\n{version_process.stderr}"
        version_match = re.search(r"\bngspice-([^\s:]+)", version_text, flags=re.IGNORECASE)
        simulator_version = version_match.group(1) if version_match else "unknown"

        with tempfile.TemporaryDirectory(prefix="eis-ngspice-") as temp_dir:
            workspace = Path(temp_dir)
            model_path = workspace / "model.lib"
            deck_path = workspace / "validate.cir"
            output_path = workspace / "ngspice-output.txt"
            model_path.write_text(
                export_foster_subcircuit(
                    model,
                    subcircuit_name=subcircuit_name,
                    relative_impedance_tolerance=relative_impedance_tolerance,
                    sections=sections,
                ),
                encoding="ascii",
            )
            deck_path.write_text(
                build_ngspice_validation_deck(
                    model_path.name,
                    model,
                    subcircuit_name=subcircuit_name,
                    points_per_decade=points_per_decade,
                ),
                encoding="ascii",
            )
            process = subprocess.run(
                [
                    executable_path,
                    "-b",
                    "-o",
                    str(output_path),
                    str(deck_path),
                ],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            output = output_path.read_text(encoding="utf-8", errors="replace") if output_path.exists() else ""
            if process.returncode != 0:
                detail = output.strip() or process.stderr.strip() or process.stdout.strip()
                return NgspiceRoundTripResult(
                    status="simulation_failed",
                    executable=executable_path,
                    simulator_version=simulator_version,
                    points=0,
                    metrics=None,
                    return_code=int(process.returncode),
                    message=f"ngspice batch run failed: {detail}",
                )
            frequencies, simulated = parse_ngspice_ac_output(output)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return NgspiceRoundTripResult(
            status="simulation_failed",
            executable=executable_path,
            simulator_version=None,
            points=0,
            metrics=None,
            return_code=None,
            message=f"ngspice round-trip failed: {exc}",
        )

    exported_network = foster_impedance(
        model,
        frequencies,
        relative_impedance_tolerance=relative_impedance_tolerance,
        sections=sections,
    )
    metrics = relative_error_metrics(exported_network, simulated)
    passed = metrics.max_relative_error_percent <= max_error_percent
    return NgspiceRoundTripResult(
        status="validated" if passed else "mismatch",
        executable=executable_path,
        simulator_version=simulator_version,
        points=int(frequencies.size),
        metrics=metrics,
        return_code=0,
        message=(
            "External ngspice round-trip passed."
            if passed
            else (
                "External ngspice round-trip exceeded the maximum error: "
                f"{metrics.max_relative_error_percent:.6g}% > {max_error_percent:.6g}%."
            )
        ),
    )


def detect_ngspice(executable: str | None = None) -> SpiceRuntimeStatus:
    candidate = executable or shutil.which("ngspice")
    if candidate is None:
        return SpiceRuntimeStatus(
            status="runtime_missing",
            executable=None,
            message=(
                "ngspice executable was not found; the analytical Foster "
                "round-trip is available, but external simulation was not run."
            ),
        )
    path = Path(candidate)
    if executable is not None and not path.is_file():
        return SpiceRuntimeStatus(
            status="runtime_missing",
            executable=None,
            message=f"Configured ngspice executable does not exist: {candidate}",
        )
    return SpiceRuntimeStatus(
        status="available",
        executable=str(candidate),
        message="ngspice runtime is available for external validation.",
    )


def _validate_identifier(value: str) -> str:
    if not value or not value.replace("_", "").isalnum() or value[0].isdigit():
        raise ValueError("SPICE identifier must contain letters, digits, or underscores.")
    return value


def _format_number(value: float) -> str:
    return f"{float(value):.15g}"
