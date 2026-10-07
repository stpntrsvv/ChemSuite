"""YARST/ES8 format ideas from FlowBat Report Maker; no legacy GUI or folder writes."""

import re
from pathlib import Path

from chem_suite.core.readers import csv_rows, read_text

from .models import CyclingStep


def _append(step, t, v, i):
    step.time_s.append(float(str(t).replace(",", ".")))
    step.voltage_v.append(float(str(v).replace(",", ".")))
    step.current_a.append(float(str(i).replace(",", ".")))


def read_csv(path: Path, context):
    steps = []
    current = None
    required = {"cycle", "step", "time_s", "voltage_V", "current_A"}
    for number, row in enumerate(csv_rows(path), 2):
        if number % 1000 == 0:
            context.check_cancelled()
        if not required.issubset(row):
            raise ValueError(f"Cycling CSV requires columns: {', '.join(sorted(required))}")
        key = (row["cycle"], row["step"])
        if current is None or current.source_index != key:
            current = CyclingStep(key, [], [], [], int(row["cycle"]))
            steps.append(current)
        _append(current, row["time_s"], row["voltage_V"], row["current_A"])
    return steps


def read_yarst(text: str, context):
    steps = []
    in_data = False
    current = None
    for number, line in enumerate(text.splitlines(), 1):
        if number % 1000 == 0:
            context.check_cancelled()
        line = line.strip()
        if not line:
            continue
        if not in_data:
            if all(token in line for token in ("Цикл", "Шаг", "U,V", "I,A")):
                in_data = True
            continue
        if line.startswith("Прервано"):
            break
        values = line.split()
        if len(values) != 9:
            raise ValueError(f"Invalid YARST row at line {number}")
        key = (values[0], values[1])
        if current is None or current.source_index != key:
            current = CyclingStep(key, [], [], [])
            steps.append(current)
        _append(current, *values[2:5])
    return steps


def read_elins(text: str, context):
    steps = []
    block = "1"
    current = None
    in_data = False
    for number, line in enumerate(text.splitlines(), 1):
        if number % 1000 == 0:
            context.check_cancelled()
        line = line.strip()
        if match := re.match(r"Блок\s+(\d+)", line):
            block = match[1]
            in_data = False
        elif match := re.fullmatch(r"Цикл\s+(\d+),\s*Шаг\s+(\d+)", line):
            current = CyclingStep((block, match[1], match[2]), [], [], [])
            steps.append(current)
            in_data = False
        elif "Время" in line and "Потенциал" in line and "Ток" in line:
            if current is None:
                raise ValueError(f"ES8 data without a step header at line {number}")
            in_data = True
        elif not line or line == "u":
            in_data = False
        elif in_data:
            values = line.split()
            if len(values) != 3:
                raise ValueError(f"Invalid ES8 row at line {number}")
            _append(current, *values)
    return steps


def load_steps(
    path: Path,
    context,
    format_name="auto",
    *,
    voltage_channel="auto",
    rest_threshold_a=1e-9,
    cycle_policy="auto",
    voltage_kind="auto",
    current_channel="auto",
):
    if format_name not in {"auto", "csv", "yarst", "elins", "biologic", "mpt", "mpr"}:
        raise ValueError(f"Unknown cycling format: {format_name}")
    if format_name in {"biologic", "mpt", "mpr"} or (
        format_name == "auto" and path.suffix.lower() in {".mpt", ".mpr"}
    ):
        from .biologic import read_mpt, read_mpr

        binary = format_name == "mpr" or (
            format_name in {"auto", "biologic"} and path.suffix.lower() == ".mpr"
        )
        steps = (read_mpr if binary else read_mpt)(
            path,
            context,
            voltage_channel=voltage_channel,
            rest_threshold_a=rest_threshold_a,
            cycle_policy=cycle_policy,
            voltage_kind=voltage_kind,
            current_channel=current_channel,
        )
    elif format_name == "csv" or (format_name == "auto" and path.suffix.lower() == ".csv"):
        steps = read_csv(path, context)
    else:
        text = read_text(path)
        if format_name == "auto":
            format_name = (
                "mpt"
                if text.startswith(("EC-Lab ASCII FILE", "BT-Lab ASCII FILE"))
                else ("elins" if "Потенциал" in text and "Блок" in text else "yarst")
            )
        if format_name == "mpt":
            from .biologic import read_mpt

            steps = read_mpt(
                path,
                context,
                voltage_channel=voltage_channel,
                rest_threshold_a=rest_threshold_a,
                cycle_policy=cycle_policy,
                voltage_kind=voltage_kind,
                current_channel=current_channel,
            )
        else:
            steps = read_elins(text, context) if format_name == "elins" else read_yarst(text, context)
    if not steps:
        raise ValueError(f"No cycling steps found in {path.name}; choose the correct format")
    for step in steps:
        step.source_file = str(path)
        step.validate()
    return steps
