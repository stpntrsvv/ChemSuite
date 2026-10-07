"""EC/BT-Lab cycling adapters. MPT needs stdlib; MPR loads galvani only in workers.

Preserve instrument cycle numbers (including zero). Missing cycle counters use
direction transitions in the analyzer. Never treat ox/red alone as cycling.
"""

import csv
import codecs
import math
import re

from .models import CyclingStep

VOLTAGES = ("Ecell/V", "Ewe-Ece/V", "Ewe/V", "<Ewe>/V", "<Ewe/V>", "Ece/V")
CURRENTS = {"<I>/A": 1.0, "<I>/mA": 0.001, "I/A": 1.0, "I/mA": 0.001}


def number(value):
    result = float(value.strip().replace(",", ".")) if isinstance(value, str) else float(value)
    if not math.isfinite(result):
        raise ValueError("BioLogic contains a nonfinite measurement")
    return result


def integer(value, name):
    result = number(value)
    if result < 0 or not result.is_integer():
        raise ValueError(f"BioLogic {name} must be a nonnegative integer")
    return int(result)


def columns(names, voltage_channel, current_channel="auto"):
    names = set(names)
    if "freq/Hz" in names:
        raise ValueError("BioLogic impedance data is not a cycling recording; open it in EIS")
    if "time/s" not in names:
        raise ValueError("BioLogic cycling requires time/s")
    voltage = (
        next((name for name in VOLTAGES if name in names), None)
        if voltage_channel == "auto"
        else voltage_channel
    )
    if voltage not in names or voltage not in VOLTAGES:
        raise ValueError(
            f"BioLogic voltage channel {voltage_channel!r} unavailable; available: "
            + ", ".join(name for name in VOLTAGES if name in names)
        )
    current = (
        next((name for name in CURRENTS if name in names), None)
        if current_channel == "auto"
        else current_channel
    )
    if current not in names or current not in CURRENTS:
        raise ValueError(
            "BioLogic cycling requires signed I/A, <I>/A, I/mA or <I>/mA (not |I|); "
            "if missing in MPR, export this recording as MPT from EC-Lab/BT-Lab"
        )
    return voltage, current


def steps_from_rows(
    rows,
    names,
    context,
    *,
    voltage_channel="auto",
    rest_threshold_a=1e-9,
    cycle_policy="auto",
    voltage_kind="auto",
    current_channel="auto",
):
    voltage, current = columns(names, voltage_channel, current_channel)
    names = set(names)
    cycle_column = "cycle number" if "cycle number" in names and cycle_policy != "direction" else None
    kind = (
        voltage_kind
        if voltage_kind != "auto"
        else ("cell" if voltage in {"Ecell/V", "Ewe-Ece/V"} else "electrode")
    )
    steps, previous_key, last_time, active = [], None, None, None
    previous_declared = None
    for index, row in enumerate(rows, 1):
        if index % 1000 == 0:
            context.check_cancelled()
        t, v, i = number(row["time/s"]), number(row[voltage]), number(row[current]) * CURRENTS[current]
        if last_time is not None and t < last_time:
            raise ValueError(f"BioLogic time/s runs backwards at data row {index}")
        last_time = t
        mode = integer(row["mode"], "mode") if "mode" in names else None
        # EC-Lab mode=3 is open circuit; measured residual current is not a charge.
        if mode == 3:
            i = 0.0
        direction = "ch" if i > rest_threshold_a else "dch" if i < -rest_threshold_a else "rest"
        instrument_cycle = (
            (
                number(row["cycle number"])
                if cycle_policy == "direction"
                else integer(row["cycle number"], "cycle number")
            )
            if "cycle number" in names
            else None
        )
        declared = instrument_cycle if cycle_column else None
        if declared is not None:
            if previous_declared is not None and declared < previous_declared:
                raise ValueError(
                    "BioLogic cycle number resets inside a file; choose current-direction cycle definition"
                )
            previous_declared = declared
        ns = integer(row["Ns"], "Ns") if "Ns" in names else 0
        half = integer(row["half cycle"], "half cycle") if "half cycle" in names else None
        key = (declared, ns, half, mode, direction)
        if active is None or previous_key != key:
            active = CyclingStep(
                tuple(str(x) for x in (instrument_cycle, ns, half, direction)),
                [],
                [],
                [],
                declared,
                voltage_channel=voltage,
                voltage_kind=kind,
                time_basis="file",
                instrument_mode=mode,
                instrument_cycle=instrument_cycle,
                current_channel=current,
            )
            steps.append(active)
            previous_key = key
        active.time_s.append(t)
        active.voltage_v.append(v)
        active.current_a.append(i)
        if "error" in names and integer(row["error"], "error"):
            raise ValueError(f"BioLogic instrument error flag at data row {index}")
    if not steps:
        raise ValueError("BioLogic recording has no cycling measurements")
    return steps


def read_mpt(path, context, **settings):
    # Scientific columns are ASCII. Raw metadata stays in the source snapshot;
    # Latin-1 accepts single-byte Western/Russian exports without losing rows.
    # Validate the whole stream: long BT-Lab headers can remain ASCII for more
    # than 8 KiB before their first degree/micro sign or operator comment.
    decoder = codecs.getincrementaldecoder("utf-8-sig")()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(65536):
                context.check_cancelled()
                decoder.decode(chunk, final=False)
            decoder.decode(b"", final=True)
        encoding = "utf-8-sig"
    except UnicodeDecodeError:
        encoding = "latin1"
    with path.open(encoding=encoding, newline="") as stream:
        if stream.readline().strip("\ufeff\r\n") not in {"EC-Lab ASCII FILE", "BT-Lab ASCII FILE"}:
            raise ValueError("Invalid BioLogic MPT signature")
        match = re.fullmatch(r"Nb header lines\s*:\s*(\d+)\s*", stream.readline().strip())
        if not match or not 3 <= int(match[1]) <= 100000:
            raise ValueError("Invalid BioLogic Nb header lines")
        for _ in range(int(match[1]) - 3):
            context.check_cancelled()
            if not stream.readline():
                raise ValueError("Truncated BioLogic MPT header")
        names = [field.strip() for field in stream.readline().rstrip("\r\n").split("\t")]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate BioLogic MPT column names")
        reader = csv.DictReader(stream, fieldnames=names, delimiter="\t")
        rows = (row for row in reader if any(str(v or "").strip() for v in row.values()))
        return steps_from_rows(rows, names, context, **settings)


def read_mpr(path, context, **settings):
    try:
        from galvani import BioLogic
    except ImportError as exc:
        raise ImportError(
            "BioLogic MPR requires chem-suite[biologic]; export MPT from EC-Lab as an alternative"
        ) from exc
    context.check_cancelled()
    try:
        with path.open("rb") as stream:
            recording = BioLogic.MPRfile(stream)
    except (ValueError, KeyError, AssertionError, NotImplementedError) as exc:
        raise ValueError(
            f"Unsupported BioLogic MPR variant ({exc}); export this recording as MPT from EC-Lab"
        ) from exc
    names = list(recording.data.dtype.names or ())
    flags = {}
    for name in ("mode", "error"):
        if name in getattr(recording, "flags_dict", {}):
            flags[name] = recording.get_flag(name)
            names.append(name)

    def rows():
        for index, row in enumerate(recording.data):
            yield {
                **{name: row[name].item() for name in recording.data.dtype.names},
                **{name: values[index].item() for name, values in flags.items()},
            }

    return steps_from_rows(rows(), names, context, **settings)
