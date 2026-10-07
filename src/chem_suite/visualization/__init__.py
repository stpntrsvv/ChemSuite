from chem_suite.core.contracts import ActionSpec, ModuleSpec

SPEC = ModuleSpec(
    "visualization",
    "Графики",
    "0.1.0",
    (ActionSpec("makie", "Отрисовка Makie", "chem_suite.visualization.makie:render"),),
    visible=False,
)
