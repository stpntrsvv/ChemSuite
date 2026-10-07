"""Shared cycling plots and table pages, constructed in workers from full artifacts."""

import csv
import json
import re
from itertools import groupby
from collections import Counter
from pathlib import Path
from chem_suite.core.plots import plot, series
from .analysis import metric_plots


def select_cycles(text, available, *, preview=False):
    available = sorted(set(available))
    if not text.strip():
        if preview and len(available) > 10:
            return {available[round(i * (len(available) - 1) / 9)] for i in range(10)}
        return set(available)
    selected = set()
    for token in text.split(","):
        match = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", token)
        if not match:
            raise ValueError("Choose cycles as 0, 1, 5-10")
        first, last = int(match[1]), int(match[2] or match[1])
        if last < first or last - first > 100000:
            raise ValueError("Invalid cycle range")
        selected.update(range(first, last + 1))
    if not selected or not selected.issubset(available):
        raise ValueError("Some selected cycles are absent from this experiment")
    if preview and len(selected) > 50:
        raise ValueError("Select up to 50 cycles for the interactive preview; export includes all cycles")
    return selected


def load_metrics(root):
    path = (Path(root) / "metrics.json").resolve(strict=True)
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError("Cycling artifact escapes the result directory")
    return json.loads(path.read_text(encoding="utf-8"))


def waveform_groups(root, metrics, context, *, selected=None, sample_limit=None, step_ids=None):
    lookup = {str(row.get("step_id") or "/".join(row["source_index"])): row for row in metrics["steps"]}
    rests = {
        str(row.get("step_id") or "/".join(row["source_index"])): row for row in metrics.get("rests", [])
    }
    file = (Path(root) / "measurements.csv").resolve(strict=True)
    if not file.is_relative_to(Path(root).resolve()):
        raise ValueError("Cycling artifact escapes the result directory")
    elapsed = 0.0
    with file.open(encoding="utf-8-sig", newline="") as stream:
        for key, group in groupby(csv.DictReader(stream), lambda r: r.get("step_id", r["source_index"])):
            context.check_cancelled()
            info = lookup.get(key) or rests.get(key)
            values, q, last, first_time = [], 0.0, None, None
            if (selected is not None and (not info or info.get("cycle") not in selected)) or (
                step_ids is not None and key not in step_ids
            ):
                for _ in group:
                    context.check_cancelled()
                continue
            count = (info or {}).get("points", 0)
            indices = (
                {round(i * (count - 1) / (sample_limit - 1)) for i in range(sample_limit)}
                if sample_limit and count > sample_limit
                else None
            )
            for index, row in enumerate(group):
                if index % 10000 == 0:
                    context.check_cancelled()
                t, v, i = (float(row[k]) for k in ("time_s", "voltage_V", "current_A"))
                if last:
                    q += 0.5 * (abs(i) + abs(last[2])) * (t - last[0]) / 3.6
                if first_time is None:
                    first_time = t
                row_elapsed = float(row.get("elapsed_s", elapsed + t - first_time))
                if indices is None or index in indices:
                    values.append(
                        {
                            **row,
                            "time_s": t,
                            "voltage_V": v,
                            "current_A": i,
                            "capacity_from_phase_mAh": float(row.get("capacity_from_phase_mAh", q)),
                            "elapsed_s": row_elapsed,
                            **{
                                name: float(row[name]) if row.get(name) not in {None, ""} else None
                                for name in ("capacity_from_step_mAh", "energy_from_step_mWh")
                                if name in row
                            },
                        }
                    )
                last = (t, v, i)
            if values:
                elapsed = row_elapsed
            if info:
                yield info, values


def preview_steps(metrics, selected):
    eligible = [r for r in metrics["steps"] + metrics.get("rests", []) if r.get("cycle") in selected]
    eligible.sort(key=lambda r: r.get("elapsed_start_s", 0.0))
    total = len(eligible)
    if total > 120:
        eligible = [eligible[round(i * (total - 1) / 119)] for i in range(120)]
    return eligible, total


def waveform_plots(root, metrics, selected, context, *, preview=True):
    if preview:
        eligible, _ = preview_steps(metrics, selected)
    else:
        eligible = [r for r in metrics["steps"] + metrics.get("rests", []) if r.get("cycle") in selected]
    step_ids = (
        {str(r.get("step_id") or "/".join(r["source_index"])) for r in eligible} if preview else None
    )
    point_limit = max(32, min(2000, 6000 // max(1, len(eligible))))
    phase_counts = Counter((r.get("cycle"), r.get("step")) for r in eligible)
    uq, ut, it = [], [], []
    for info, values in waveform_groups(
        root, metrics, context, selected=selected, sample_limit=point_limit if preview else None,
        step_ids=step_ids,
    ):
        if info.get("cycle") not in selected or not values:
            continue
        limit = point_limit if preview else max(2, len(values))
        label = (
            f"Cycle {info['cycle']} · {info.get('step', 'rest')}"
            if info.get("cycle") is not None
            else "Rest before cycling"
        )
        if phase_counts[(info.get("cycle"), info.get("step"))] > 1 and info.get("step_id"):
            label += " · Step: " + info["step_id"]
        if info.get("step") in {"ch", "dch"}:
            uq.append(
                series(
                    label,
                    [v["capacity_from_phase_mAh"] for v in values],
                    [v["voltage_V"] for v in values],
                    limit=limit,
                )
            )
        ut.append(
            series(label, [v["elapsed_s"] for v in values], [v["voltage_V"] for v in values], limit=limit)
        )
        it.append(
            series(
                label, [v["elapsed_s"] for v in values], [v["current_A"] * 1000 for v in values], limit=limit
            )
        )
    return [
        plot("Заряд / разряд", "Ёмкость от начала полупериода, mAh", "Напряжение, V", uq),
        plot("Напряжение во времени", "Время записи, s", "Напряжение, V", ut),
        plot("Ток во времени", "Время записи, s", "Ток, mA", it),
    ]


def build_view(root, payload, context, *, selected_text="", page=1):
    metrics = load_metrics(root)
    if not 1 <= page <= 100000:
        raise ValueError("Table page must be between 1 and 100000")
    selected = select_cycles(selected_text, [r["cycle"] for r in metrics["cycles"]], preview=True)
    shown_steps, total_steps = preview_steps(metrics, selected)
    waveform = waveform_plots(root, metrics, selected, context)
    # Preserve the initial charge/efficiency/capacity ordering in saved results.
    plots = [
        waveform[0],
        *metric_plots(metrics["cycles"], [r for r in metrics["rests"] if r.get("cycle") is not None]),
        *waveform[1:],
    ]
    begin = (page - 1) * 200
    return {
        "plots": plots,
        "selected_cycles": sorted(selected),
        "preview_step_count": len(shown_steps),
        "preview_step_total": total_steps,
        "table_page": page,
        "table_pages": max(1, max((len(metrics[k]) + 199) // 200 for k in ("cycles", "steps", "rests"))),
        "tables": [
            {"title": title, "rows": metrics[key][begin : begin + 200], "total_rows": len(metrics[key])}
            for key, title in (("cycles", "Циклы"), ("steps", "Шаги"), ("rests", "Паузы"))
        ],
    }
