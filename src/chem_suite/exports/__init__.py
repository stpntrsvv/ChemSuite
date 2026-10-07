from chem_suite.core.contracts import ActionSpec, ModuleSpec

SPEC = ModuleSpec(
    "exports", "Export", "0.1.0",
    (ActionSpec("export", "Export results", "chem_suite.exports.actions:export_results"),),
    "export", visible=False,
)
