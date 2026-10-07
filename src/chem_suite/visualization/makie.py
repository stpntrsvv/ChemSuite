"""Optional Julia backend. Scientific modules speak only the common PlotSpec."""

import os
import shutil
import sys
import subprocess
from pathlib import Path

from chem_suite.core.plots import validate_plot


def runtime():
    scripts = Path(__file__).with_name("julia")
    from chem_suite.core.paths import user_data_directory
    project = user_data_directory() if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[3]
    candidates = [os.environ.get("CHEM_SUITE_JULIA"), shutil.which("julia")]
    candidates += [
        str(p) for p in sorted((project / ".runtime").glob("*/Contents/Resources/julia/bin/julia"))
    ]
    candidates += [str(p) for p in sorted((project / ".runtime").glob("julia-*/bin/julia"))]
    executable = next((p for p in candidates if p and Path(p).is_file()), None)
    if executable is None:
        raise RuntimeError("Julia не найдена. Укажите CHEM_SUITE_JULIA; установка описана в docs/makie.md")
    environment = dict(os.environ)
    environment["JULIA_DEPOT_PATH"] = os.environ.get("CHEM_SUITE_JULIA_DEPOT", str(project / ".julia-depot"))
    environment["JULIA_NUM_THREADS"] = "1"
    return executable, scripts, environment


def render(request, context):
    import json
    import time

    if len(request.inputs) != 1:
        raise ValueError("Makie needs one completed result.json")
    source, provenance = context.snapshot(request.inputs[0])
    result = json.loads(source.read_text(encoding="utf-8"))
    for spec in result["payload"]["plots"]:
        validate_plot(spec)
    executable, scripts, environment = runtime()
    directory = context.artifact("makie/.keep").parent
    context.progress(0.1, "Отрисовка CairoMakie")
    with context.artifact("julia.log").open("w") as log:
        process = subprocess.Popen(
            [
                executable,
                f"--project={scripts}",
                "--startup-file=no",
                str(scripts / "render.jl"),
                str(source),
                str(directory),
            ],
            env=environment,
            stdout=log,
            stderr=log,
        )
        try:
            while process.poll() is None:
                context.check_cancelled()
                time.sleep(0.05)
            if process.returncode:
                raise RuntimeError(f"CairoMakie exited with code {process.returncode}; see julia.log")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
    return {
        "sources": [provenance],
        "backend": "CairoMakie",
        "artifacts": [str(p.relative_to(context.directory)) for p in sorted(directory.iterdir())],
    }
