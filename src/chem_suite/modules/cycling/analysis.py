"""Recorded-current and power integration; no scientific or GUI dependencies."""

import math
from chem_suite.core.plots import plot, series


def integrate(step, context):
    q = e = 0.0
    yield 0.0, 0.0
    for k in range(1, len(step.time_s)):
        if k % 10000 == 0:
            context.check_cancelled()
        dt = step.time_s[k] - step.time_s[k - 1]
        q += 0.5 * (step.current_a[k] + step.current_a[k - 1]) * dt / 3.6
        e += (
            0.5
            * (step.voltage_v[k] * step.current_a[k] + step.voltage_v[k - 1] * step.current_a[k - 1])
            * dt
            / 3.6
        )
        yield abs(q), abs(e)


def metric_plots(cycles, rests, *, limit=2000):
    def curve(label, rows, key):
        available = [r for r in rows if r.get(key) is not None and r.get("cycle") is not None]
        return series(label, [r["cycle"] for r in available], [r[key] for r in available], limit=limit)

    return [
        plot(
            "Эффективность",
            "Цикл",
            "Эффективность, %",
            [
                curve(label, cycles, key)
                for key, label in (
                    ("coulombic_efficiency_percent", "CE"),
                    ("voltage_efficiency_percent", "VE"),
                    ("energy_efficiency_percent", "EE"),
                )
            ],
        ),
        plot(
            "Ёмкость по циклам",
            "Цикл",
            "Ёмкость, mAh",
            [curve("Заряд", cycles, "charge_mAh"), curve("Разряд", cycles, "discharge_mAh")],
        ),
        plot(
            "Сохранение ёмкости",
            "Цикл",
            "Сохранение ёмкости, %",
            [curve("Сохранение ёмкости", cycles, "retention_percent")],
        ),
        plot("Напряжение в конце паузы", "Цикл", "Напряжение, V", [curve("Пауза", rests, "ocv_V")]),
    ]


def analyze_steps(steps, context, *, rest_threshold_a=1e-9, nominal_capacity_mah=None, reference_cycle=None):
    if not math.isfinite(rest_threshold_a) or rest_threshold_a < 0:
        raise ValueError("Rest threshold must be finite and nonnegative")
    if nominal_capacity_mah is not None and (
        not math.isfinite(nominal_capacity_mah) or nominal_capacity_mah <= 0
    ):
        raise ValueError("Nominal capacity must be finite and positive")
    metrics, rests, curves, diagnostics = [], [], [], []
    cycle, previous_direction, elapsed = 0, None, 0.0
    file_origins, file_offsets, phase_q = {}, {}, {}
    timeline, grouped = {}, {}
    previous_step = None
    for index, step in enumerate(steps):
        context.check_cancelled()
        step.validate()
        step_id = step.step_id or str(index + 1)
        positive = any(i > rest_threshold_a for i in step.current_a)
        negative = any(i < -rest_threshold_a for i in step.current_a)
        if positive and negative:
            raise ValueError(f"Current changes direction inside step {step.source_index}; split the step")
        direction = "ch" if positive else "dch" if negative else "rest"
        if step.declared_cycle is not None:
            cycle = step.declared_cycle
        elif direction != "rest" and (
            previous_direction is None or (previous_direction == "dch" and direction == "ch")
        ):
            cycle += 1
        if direction != "rest":
            previous_direction = direction
        assigned_cycle = (
            cycle
            if direction != "rest" or previous_direction is not None or step.declared_cycle is not None
            else None
        )
        duration = step.time_s[-1] - step.time_s[0]
        if step.time_basis == "file":
            if step.source_file not in file_origins:
                file_origins[step.source_file] = step.time_s[0]
                file_offsets[step.source_file] = elapsed
            start = file_offsets[step.source_file] + step.time_s[0] - file_origins[step.source_file]
            if previous_step and previous_step.source_file == step.source_file:
                gap = step.time_s[0] - previous_step.time_s[-1]
                if gap > 0:
                    diagnostics.append(f"unintegrated_boundary_s:{step_id}:{gap:.9g}")
        else:
            start = elapsed
        elapsed = max(elapsed, start + duration)
        common = {
            "cycle": assigned_cycle,
            "step": direction,
            "step_id": step_id,
            "source_index": list(step.source_index),
            "source_file": step.source_file,
            "instrument_cycle": step.instrument_cycle,
            "voltage_channel": step.voltage_channel,
            "current_channel": step.current_channel,
            "duration_s": duration,
            "elapsed_start_s": start,
            "elapsed_end_s": start + duration,
            "points": len(step.time_s),
        }
        if direction == "rest":
            rests.append(
                {
                    **common,
                    "ocv_V": step.voltage_v[-1],
                    "initial_voltage_V": step.voltage_v[0],
                    "voltage_change_V": step.voltage_v[-1] - step.voltage_v[0],
                }
            )
            timeline[step_id] = {**common, "phase_offset_mAh": 0.0}
        else:
            charges, energies = zip(*integrate(step, context))
            capacity, energy = charges[-1], energies[-1]
            voltage_valid = step.voltage_kind == "cell" and all(v >= 0 for v in step.voltage_v)
            if capacity == 0:
                diagnostics.append(f"zero_capacity:{cycle}:{step.source_index}")
            if any(v < 0 for v in step.voltage_v):
                diagnostics.append(f"negative_voltage:{cycle}:efficiencies_require_cell_voltage")
            if step.voltage_kind != "cell":
                diagnostics.append(f"electrode_potential:{cycle}:VE_EE_unavailable")
            row = {
                **common,
                "capacity_mAh": capacity,
                "energy_mWh": energy if voltage_valid else None,
                "charge_weighted_voltage_V": energy / capacity if capacity and voltage_valid else None,
                "voltage_valid_for_efficiency": voltage_valid,
            }
            metrics.append(row)
            offset = phase_q.get((cycle, direction), 0.0)
            timeline[step_id] = {**common, "phase_offset_mAh": offset}
            phase_q[(cycle, direction)] = offset + capacity
            if len(curves) < 20:
                curves.append(
                    series(f"Цикл {cycle} · {direction}", [offset + q for q in charges], step.voltage_v)
                )
            values = grouped.setdefault(
                cycle,
                {
                    "ch_q": 0.0,
                    "dch_q": 0.0,
                    "ch_e": 0.0,
                    "dch_e": 0.0,
                    "ch_duration": 0.0,
                    "dch_duration": 0.0,
                    "voltage_valid": True,
                },
            )
            values[f"{direction}_q"] += capacity
            values[f"{direction}_e"] += energy
            values[f"{direction}_duration"] += duration
            values["voltage_valid"] &= voltage_valid
        previous_step = step
        context.progress(0.3 + 0.5 * (index + 1) / len(steps), f"Циклирование: шаг {index + 1}/{len(steps)}")
    cycles = []
    for cycle_id, values in sorted(grouped.items()):
        ch_q, dch_q, ch_e, dch_e = (values[k] for k in ("ch_q", "dch_q", "ch_e", "dch_e"))
        paired = ch_q > 0 and dch_q > 0
        if not paired:
            diagnostics.append(f"incomplete_cycle:{cycle_id}")
        voltage_valid = paired and values["voltage_valid"] and ch_e > 0
        cycles.append(
            {
                "cycle": cycle_id,
                "charge_mAh": ch_q,
                "discharge_mAh": dch_q,
                "charge_energy_mWh": ch_e if values["voltage_valid"] else None,
                "discharge_energy_mWh": dch_e if values["voltage_valid"] else None,
                "charge_duration_s": values["ch_duration"],
                "discharge_duration_s": values["dch_duration"],
                "paired": paired,
                "coulombic_efficiency_percent": 100 * dch_q / ch_q if paired else None,
                "voltage_efficiency_percent": 100 * (dch_e / dch_q) / (ch_e / ch_q)
                if voltage_valid
                else None,
                "energy_efficiency_percent": 100 * dch_e / ch_e if voltage_valid else None,
                "utilization_percent": 100 * dch_q / nominal_capacity_mah
                if nominal_capacity_mah is not None
                else None,
                "retention_percent": None,
            }
        )
    reference = None
    if reference_cycle is not None:
        if (
            isinstance(reference_cycle, bool)
            or not float(reference_cycle).is_integer()
            or float(reference_cycle) < 0
        ):
            raise ValueError("Reference cycle must be a nonnegative integer")
        reference = next(
            (r for r in cycles if r["cycle"] == int(reference_cycle) and r["discharge_mAh"] > 0), None
        )
        if reference is None:
            raise ValueError("Reference cycle has no measured discharge capacity")
    else:
        reference = next((r for r in cycles if r["paired"]), None)
    if reference:
        for row in cycles:
            row["retention_percent"] = (
                100 * row["discharge_mAh"] / reference["discharge_mAh"] if row["discharge_mAh"] > 0 else None
            )
    return {
        "steps": metrics,
        "rests": rests,
        "cycles": cycles,
        "timeline": timeline,
        "plots": [
            plot("Заряд / разряд", "Ёмкость от начала полупериода, mAh", "Напряжение, V", curves),
            *metric_plots(cycles, rests),
        ],
        "diagnostics": diagnostics,
        "cycle_count": len(cycles),
        "reference_cycle": reference["cycle"] if reference else None,
        "paired_cycle_count": sum(r["paired"] for r in cycles),
        "duration_s": elapsed,
    }
