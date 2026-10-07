from chem_suite.core.contracts import ActionSpec, ModuleSpec


class ModuleRegistry:
    def __init__(self) -> None:
        self._modules: dict[str, ModuleSpec] = {}

    def register(self, module: ModuleSpec) -> None:
        if not module.id or module.id in self._modules:
            raise ValueError(f"Duplicate or empty module ID: {module.id}")
        ids = [action.id for action in module.actions]
        if not ids or len(set(ids)) != len(ids):
            raise ValueError(f"Module {module.id} needs distinct actions")
        for action in module.actions:
            if ":" not in action.handler:
                raise ValueError(f"Invalid handler: {action.handler}")
        self._modules[module.id] = module

    def modules(self) -> tuple[ModuleSpec, ...]:
        return tuple(self._modules.values())

    def module(self, module_id: str) -> ModuleSpec:
        try:
            return self._modules[module_id]
        except KeyError:
            raise ValueError(f"Unknown module: {module_id}") from None

    def action(self, module_id: str, action_id: str) -> ActionSpec:
        for action in self.module(module_id).actions:
            if action.id == action_id:
                return action
        raise ValueError(f"Unknown action: {module_id}.{action_id}")
