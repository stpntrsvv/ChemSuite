"""Desktop composition root; the core registry never imports Qt panels."""

from importlib import import_module

from chem_suite.desktop.panel import ModulePanel


def create_panel(spec, jobs, makie):
    if spec.id == "eis" and any(action.id == "fit" for action in spec.actions):
        panel_type = import_module("chem_suite.modules.eis.desktop").EisPanel
    elif spec.id == "cycling" and any(action.id == "analyze" for action in spec.actions):
        panel_type = import_module("chem_suite.modules.cycling.desktop").CyclingPanel
    else:
        panel_type = ModulePanel
    return panel_type(spec, jobs, makie)
