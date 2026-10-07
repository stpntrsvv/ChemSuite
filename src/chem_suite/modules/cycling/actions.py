"""Cycling worker actions. The desktop only receives bounded ready JSON."""

import csv
import json
import math
import re
from pathlib import Path
from chem_suite.core.artifacts import write_json
from .analysis import analyze_steps, integrate
from .readers import load_steps

SUFFIXES = {".txt", ".csv", ".mpt", ".mpr"}


def natural_key(path):
    return tuple((0, int(p)) if p.isdigit() else (1, p.casefold()) for p in re.split(r"(\d+)", path.name))


def experiment_files(folder):
    raw = folder / "01_Raw_data" / "potentiostat"
    raw = raw if raw.is_dir() else folder
    return sorted((p for p in raw.iterdir() if p.is_file() and p.suffix.lower() in SUFFIXES), key=natural_key)


def discover(request, context):
    experiments = []
    for raw in request.inputs:
        context.check_cancelled()
        source = Path(raw).resolve(strict=True)
        if source.is_file():
            if source.suffix.lower() not in SUFFIXES:
                raise ValueError("Supported cycling files: MPT, MPR, CSV, YARST/Elins TXT")
            experiments.append({"path": str(source), "file": source.name, "inputs": [str(source)]})
        elif (source / "config.json").is_file() or (source / "01_Raw_data" / "potentiostat").is_dir():
            experiments.append({"path": str(source), "file": source.name, "inputs": [str(source)]})
        else:
            for child in sorted(source.iterdir(), key=natural_key):
                context.check_cancelled()
                if child.is_dir() and (
                    (child / "config.json").is_file() or (child / "01_Raw_data" / "potentiostat").is_dir()
                ):
                    experiments.append({"path": str(child), "file": child.name, "inputs": [str(child)]})
                elif child.is_file() and child.suffix.lower() in SUFFIXES:
                    experiments.append({"path": str(child), "file": child.name, "inputs": [str(child)]})
    if not experiments:
        raise ValueError("No cycling experiments found")
    return {"experiments": experiments}


def analyze_file(request, context):
    if not request.inputs:
        raise ValueError("Cycling needs an MPT, MPR, CSV, potentiostat file, or experiment folder")
    settings = request.config
    for key, choices in {
        "cycle_policy": {"auto", "direction"},
        "file_cycle_policy": {"separate", "continue"},
        "voltage_kind": {"auto", "cell", "electrode"},
        "export_scope": {"all", "selected"},
    }.items():
        if key in settings and settings[key] not in choices:
            raise ValueError(f"Unsupported cycling setting: {key}")
    threshold = float(settings.get("rest_threshold_A", 1e-9))
    if not math.isfinite(threshold) or threshold < 0:
        raise ValueError("Rest threshold must be finite and nonnegative")
    format_name = str(settings.get("format", "auto")).lower()
    sources = [Path(p) for p in request.inputs]
    original_folder = None
    config_path = None
    if len(sources) == 1 and sources[0].is_dir():
        original_folder = str(sources[0])
        config_path = sources[0] / "config.json"
        sources = experiment_files(sources[0])
    elif settings.get("config_path"):
        config_path = Path(settings["config_path"])
    if not sources:
        raise ValueError("No raw cycling files found")
    identities = settings.get("source_identities", {})
    provenance_list, metadata = [], {}

    def snapshot(source, role):
        snapshot_path, provenance = context.snapshot(source)
        identity = identities.get(str(source))
        if identity:
            if provenance["sha256"] != identity["sha256"]:
                raise ValueError("Archived source checksum differs from provenance")
            provenance["path"] = identity["path"]
        provenance["role"] = role
        provenance_list.append(provenance)
        return snapshot_path, provenance

    if config_path and config_path.is_file():
        config_snapshot, _ = snapshot(config_path, "configuration")
        metadata = json.loads(config_snapshot.read_text(encoding="utf-8-sig"))
        if not isinstance(metadata, dict):
            raise ValueError("Experiment config.json must contain an object")
        if format_name == "auto":
            instrument = str(metadata.get("experiment_info", {}).get("potentiostat", "auto")).lower()
            format_name = {
                "bio-logic": "biologic",
                "bio logic": "biologic",
                "ec-lab": "biologic",
                "es8": "elins",
            }.get(instrument, instrument)
            if format_name not in {"auto", "csv", "yarst", "elins", "biologic", "mpt", "mpr"}:
                format_name = "auto"
    nominal = settings.get("nominal_capacity_mAh")
    nominal_origin = "explicit" if nominal is not None else "unspecified"
    if nominal is None and "electrolyte" in metadata:
        e = metadata["electrolyte"]
        concentration, volume, electrons = (
            (float(e[k]) for k in ("concentration_M", "volume_ml", "electrons_transferred"))
            if "electrons_transferred" in e
            else (float(e["concentration_M"]), float(e["volume_ml"]), 1.0)
        )
        if any(not math.isfinite(x) or x <= 0 for x in (concentration, volume, electrons)):
            raise ValueError(
                "Electrolyte concentration, volume and electron count must be finite and positive"
            )
        nominal = electrons * 96485.33212 * concentration * volume / 1000 / 3.6
        nominal_origin = "electrolyte_nFcV"
    steps = []
    for index, source in enumerate(sources):
        context.progress(0.05 + 0.2 * index / len(sources), f"Чтение {source.name}")
        source_snapshot, provenance = snapshot(source, "measurements")
        loaded = load_steps(
            source_snapshot,
            context,
            format_name,
            voltage_channel=settings.get("voltage_channel", "auto"),
            rest_threshold_a=threshold,
            cycle_policy=settings.get("cycle_policy", "auto"),
            voltage_kind=settings.get("voltage_kind", "auto"),
            current_channel=settings.get("current_channel", "auto"),
        )
        for step_index, step in enumerate(loaded, 1):
            step.step_id = f"{index + 1}:{step_index}"
            step.source_file = provenance["path"]
            if settings.get("cycle_policy") == "direction":
                step.declared_cycle = None
            steps.append(step)
    # A reset counter in a later file must not silently merge independent cycles.
    previous_max = None
    counter_offset = 0
    counter_resets = []
    for source in dict.fromkeys(s.source_file for s in steps):
        file_steps = [s for s in steps if s.source_file == source]
        declared = [s.declared_cycle for s in file_steps if s.declared_cycle is not None]
        if declared:
            if (
                previous_max is not None
                and min(declared) <= previous_max
                and settings.get("file_cycle_policy", "separate") != "continue"
            ):
                counter_offset = previous_max + 1 - min(declared)
                counter_resets.append("cycle_counter_offset:" + source + ":" + str(counter_offset))
            else:
                counter_offset = 0
            for step in file_steps:
                if step.declared_cycle is not None:
                    step.declared_cycle += counter_offset
            previous_max = max(declared) + counter_offset
    result = analyze_steps(
        steps,
        context,
        rest_threshold_a=threshold,
        nominal_capacity_mah=float(nominal) if nominal is not None else None,
        reference_cycle=settings.get("reference_cycle"),
    )
    result["diagnostics"].extend(counter_resets)
    with context.artifact("measurements.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "step_id",
                "source_index",
                "source_file",
                "cycle",
                "phase",
                "time_s",
                "elapsed_s",
                "voltage_V",
                "current_A",
                "capacity_from_step_mAh",
                "capacity_from_phase_mAh",
                "energy_from_step_mWh",
            ]
        )
        for step in steps:
            info = result["timeline"][step.step_id]
            voltage_valid = step.voltage_kind == "cell" and all(vv >= 0 for vv in step.voltage_v)
            for n, ((t, v, i), (q, energy)) in enumerate(
                zip(zip(step.time_s, step.voltage_v, step.current_a), integrate(step, context))
            ):
                if n % 10000 == 0:
                    context.check_cancelled()
                writer.writerow(
                    [
                        step.step_id,
                        "/".join(step.source_index),
                        step.source_file,
                        info["cycle"],
                        info["step"],
                        t,
                        info["elapsed_start_s"] + t - step.time_s[0],
                        v,
                        i,
                        q if info["step"] != "rest" else 0.0,
                        info["phase_offset_mAh"] + q if info["step"] != "rest" else 0.0,
                        energy if voltage_valid else None,
                    ]
                )
    for name, rows in (
        ("step_metrics.csv", result["steps"]),
        ("efficiencies.csv", result["cycles"]),
        ("ocv.csv", result["rests"]),
    ):
        with context.artifact(name).open("w", encoding="utf-8", newline="") as stream:
            if rows:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    write_json(
        context.artifact("metrics.json"), {k: result[k] for k in ("steps", "rests", "cycles", "timeline")}
    )
    experiment = {
        "name": settings.get("experiment_name")
        or (Path(original_folder).name if original_folder else sources[0].stem),
        "folder": original_folder,
        "metadata": metadata,
        "file_cycle_policy": settings.get("file_cycle_policy", "separate"),
    }
    channels = sorted(set(s.voltage_channel for s in steps))
    payload = {
        "dataset_type": "cycling.run.v2",
        "sources": provenance_list,
        "experiment": experiment,
        "summary": {
            "Эксперимент": experiment["name"],
            "Циклов": result["cycle_count"],
            "Пар заряд–разряд": result["paired_cycle_count"],
            "Шагов": len(steps),
            "Пауз": len(result["rests"]),
            "Канал напряжения": ", ".join(channels),
            "Номинальная ёмкость, mAh": nominal if nominal is not None else "не задана",
            "Опорный цикл": result["reference_cycle"]
            if result["reference_cycle"] is not None
            else "не задан",
        },
        "cycle_count": result["cycle_count"],
        "step_count": len(steps),
        "nominal_capacity_mAh": nominal,
        "nominal_capacity_origin": nominal_origin,
        "reference_cycle": result["reference_cycle"],
        "voltage_channels": channels,
        "method": "trapezoidal_current_and_power_within_steps_v2",
        "timeline_method": "file_time_then_concatenated_files"
        if all(s.time_basis == "file" for s in steps)
        else "concatenated_step_durations",
        "diagnostics": result["diagnostics"][:200],
        "diagnostic_count": len(result["diagnostics"]),
        "plots": result["plots"],
        "tables": [{"title": "Циклы", "rows": result["cycles"][:200]}],
        "artifacts": ["measurements.csv", "step_metrics.csv", "efficiencies.csv", "ocv.csv", "metrics.json"],
    }
    # One implementation supplies bounded screen plots and full-resolution exports.
    from .views import build_view

    payload.update(
        build_view(
            context.directory,
            payload,
            context,
            selected_text=settings.get("selected_cycles", ""),
            page=int(settings.get("table_page", 1)),
        )
    )
    return payload


def view_file(request, context):
    from .views import build_view

    path = Path(request.inputs[0]).resolve(strict=True)
    result = json.loads(path.read_text())
    if (
        result.get("module") != "cycling"
        or result.get("action") != "analyze"
        or result.get("schema_version") != 1
    ):
        raise ValueError("Cycling view needs a completed cycling analysis")
    return build_view(
        path.parent,
        result["payload"],
        context,
        selected_text=request.config.get("selected_cycles", ""),
        page=int(request.config.get("table_page", 1)),
    )
