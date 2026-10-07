"""Composition root: the application chooses modules; core knows none of them."""

from chem_suite.core.registry import ModuleRegistry


def builtins() -> ModuleRegistry:
    from chem_suite.modules.cycling import SPEC as cycling
    from chem_suite.modules.eis import SPEC as eis
    from chem_suite.visualization import SPEC as visualization
    from chem_suite.exports import SPEC as exports

    from chem_suite.sessions import SPEC as sessions

    registry = ModuleRegistry()
    registry.register(eis)
    registry.register(cycling)
    registry.register(visualization)
    registry.register(exports)
    registry.register(sessions)
    return registry
