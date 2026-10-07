"""Full-resolution cycling export, independent from the bounded Qt preview."""

from pathlib import Path

from .analysis import metric_plots
from .views import load_metrics, select_cycles, waveform_groups, waveform_plots


def export_data(path, result, options, context):
    metrics = load_metrics(path.parent)
    settings = options.get("_module_config", {})
    selected = select_cycles(
        settings.get("selected_cycles", "") if settings.get("export_scope") == "selected" else "",
        [r["cycle"] for r in metrics["cycles"]],
    )
    cycles = [r for r in metrics["cycles"] if r["cycle"] in selected]
    steps = [r for r in metrics["steps"] if r["cycle"] in selected]
    all_cycles = settings.get("export_scope") != "selected"
    rests = metrics["rests"] if all_cycles else [r for r in metrics["rests"] if r.get("cycle") in selected]
    waveform_selection = selected | {r.get("cycle") for r in metrics["rests"]} if all_cycles else selected
    waveform = waveform_plots(path.parent, metrics, waveform_selection, context, preview=False)
    statistic_plots = metric_plots(
        cycles, [r for r in rests if r.get("cycle") is not None], limit=max(2, len(cycles), len(rests))
    )
    groups = {
        "cycling_plots": [waveform[0], *statistic_plots[:2]],
        "cycling_time": waveform[1:],
        "cycling_retention": statistic_plots[2:3],
        "cycling_ocv": statistic_plots[3:],
    }
    if options.get("cycling_plots") is False:
        groups = {}
    tables = {"cycles": cycles, "steps": steps, "rests": rests}
    if options.get("waveforms", True):
        tables["waveforms"] = []
        for info, values in waveform_groups(path.parent, metrics, context):
            if info.get("cycle") in waveform_selection:
                tables["waveforms"].extend(values)
    payload = result["payload"]
    source = next(
        (
            s
            for s in payload["sources"]
            if s.get("role") != "configuration" and Path(s["path"]).name != "config.json"
        ),
        payload["sources"][0],
    )
    return (
        {
            "source_file": source["path"],
            "cycle_count": len(cycles),
            "reference_cycle": payload.get("reference_cycle"),
            "nominal_capacity_mAh": payload.get("nominal_capacity_mAh"),
            "voltage_channels": ", ".join(payload.get("voltage_channels", [])),
            "method": payload.get("method", "trapezoidal_current_and_power_v1"),
            "timeline_method": payload.get("timeline_method", "concatenated_step_durations"),
        },
        tables,
        groups,
    )
