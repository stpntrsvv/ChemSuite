"""Backend-neutral, JSON-compatible descriptions. No plotting library imports."""

import math


def series(label: str, x, y, *, kind: str = "line", limit: int = 2000) -> dict:
    if len(x) != len(y):
        raise ValueError("Plot coordinate lengths differ")
    if kind not in {"line", "scatter"} or limit < 2:
        raise ValueError("Invalid series settings")
    # Preview only; full resolution lives in each module's artifacts.
    count = len(x)
    indices = (
        range(count)
        if count <= limit
        else sorted({round(i * (count - 1) / (limit - 1)) for i in range(limit)})
    )
    pairs = [
        (float(x[i]), float(y[i]))
        for i in indices
        if math.isfinite(float(x[i])) and math.isfinite(float(y[i]))
    ]
    return {
        "label": label,
        "kind": kind,
        "x": [p[0] for p in pairs],
        "y": [p[1] for p in pairs],
        "original_points": count,
        "preview_points": len(pairs),
    }


def plot(
    title: str,
    xlabel: str,
    ylabel: str,
    curves: list[dict],
    *,
    xscale="linear",
    yscale="linear",
    equal_aspect=False,
) -> dict:
    return {
        "schema_version": 1,
        "title": title,
        "xlabel": xlabel,
        "ylabel": ylabel,
        "xscale": xscale,
        "yscale": yscale,
        "equal_aspect": equal_aspect,
        "series": curves,
    }


def validate_plot(spec: dict) -> None:
    if spec.get("schema_version") != 1:
        raise ValueError("Unsupported plot schema")
    for key in ("title", "xlabel", "ylabel"):
        if not isinstance(spec.get(key), str):
            raise ValueError(f"Plot needs {key}")
    if spec.get("xscale") not in {"linear", "log"} or spec.get("yscale") not in {"linear", "log"}:
        raise ValueError("Unsupported axis scale")
    for curve in spec["series"]:
        if curve["kind"] not in {"line", "scatter"} or len(curve["x"]) != len(curve["y"]):
            raise ValueError("Invalid plot series")
        if not all(math.isfinite(v) for v in curve["x"] + curve["y"]):
            raise ValueError("Plot coordinates must be finite")
