"""Benchmark-only diagnostics for parameter identifiability across frequency windows."""

from __future__ import annotations

import numpy as np

from .eis_core import estimate_dataset_scale, fit_circuit, parameter_names


def characteristic_support(frequencies, circuit: str, parameters) -> dict:
    """Map parameters to whether their characteristic frequency is measured."""
    names = parameter_names(circuit)
    values = dict(zip(names, np.asarray(parameters, dtype=float)))
    f_min, f_max = float(np.min(frequencies)), float(np.max(frequencies))
    result = {name: None for name in names}
    for name in names:
        if not name.startswith("CPE") or not name.endswith("_0"):
            continue
        element = name.split("_", 1)[0]
        suffix = element[3:]
        alpha_name = f"{element}_1"
        resistance_name = f"R{int(suffix) + 1}"
        if resistance_name not in values or alpha_name not in values:
            continue
        resistance, q, alpha = values[resistance_name], values[name], values[alpha_name]
        frequency = (1.0 / (resistance * q)) ** (1.0 / alpha) / (2.0 * np.pi)
        supported = bool(f_min <= frequency <= f_max)
        for parameter in (resistance_name, name, alpha_name):
            result[parameter] = {
                "frequency": float(frequency), "supported": supported,
                "measured_min": f_min, "measured_max": f_max,
            }
    for name in names:
        if not name.startswith("Wo") or not name.endswith("_1"):
            continue
        element = name.split("_", 1)[0]
        frequency = 1.0 / (2.0 * np.pi * values[name])
        supported = bool(f_min <= frequency <= f_max)
        for parameter in (f"{element}_0", name):
            result[parameter] = {
                "frequency": float(frequency), "supported": supported,
                "measured_min": f_min, "measured_max": f_max,
            }
    return result


def frequency_window_stability(frequencies, z, circuit: str, base_parameters, *,
                               trim_fractions=(0.10, 0.20), max_fold_change=1.5,
                               restarts=3, max_evaluations=2000, seed=0, check_cancelled=None, parameter_overrides_by_circuit=None) -> dict:
    """Refit after trimming both frequency edges and measure parameter drift."""
    frequencies = np.asarray(frequencies, dtype=float)
    z = np.asarray(z, dtype=complex)
    order = np.argsort(frequencies)
    names = parameter_names(circuit)
    base = np.asarray(base_parameters, dtype=float)
    variants = []
    for trim_fraction in trim_fractions:
        count = max(1, int(np.floor(len(order) * float(trim_fraction))))
        masks = {
            f"drop_low_{trim_fraction:g}": order[count:],
            f"drop_high_{trim_fraction:g}": order[:-count],
        }
        for label, indices in masks.items():
            if check_cancelled:
                check_cancelled()
            fit = fit_circuit(
                frequencies[indices], z[indices], circuit,
                estimate_dataset_scale(frequencies[indices], z[indices]),
                parameter_overrides=(parameter_overrides_by_circuit or {}).get(circuit), fit_restarts=restarts, restart_seed=seed + len(variants) * 10007,
                max_fit_evaluations=max_evaluations,
            )
            accepted = bool(fit.success and fit.model is not None and fit.status != "BAD")
            variants.append({
                "window": label, "accepted": accepted, "status": fit.status,
                "parameters": (
                    None if not accepted
                    else [float(value) for value in fit.model.parameters_]
                ),
            })
    diagnostics = {}
    for index, name in enumerate(names):
        values = [
            row["parameters"][index] for row in variants
            if row["accepted"] and row["parameters"] is not None
        ]
        if len(values) != len(variants) or base[index] <= 0 or any(value <= 0 for value in values):
            diagnostics[name] = {
                "stable": False, "max_fold_change": None,
                "accepted_windows": len(values), "requested_windows": len(variants),
            }
            continue
        folds = [max(value / base[index], base[index] / value) for value in values]
        diagnostics[name] = {
            "stable": bool(max(folds) <= float(max_fold_change)),
            "max_fold_change": float(max(folds)),
            "accepted_windows": len(values), "requested_windows": len(variants),
        }
    return {
        "max_fold_change_threshold": float(max_fold_change),
        "variants": variants, "parameters": diagnostics,
    }


def bias_aware_status(interval_status: str, *, window_stable: bool,
                      characteristic_supported: bool | None) -> str:
    """Downgrade narrow conditional intervals when the experiment lacks support."""
    if interval_status == "unbounded":
        return "unbounded"
    if not window_stable or characteristic_supported is False:
        return "weak"
    return interval_status
