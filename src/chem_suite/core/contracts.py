from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"

    @property
    def terminal(self) -> bool:
        return self in {self.SUCCEEDED, self.FAILED, self.CANCELLED, self.TIMED_OUT}


@dataclass(frozen=True)
class ActionSpec:
    id: str
    title: str
    handler: str  # import path resolved only inside a job process
    visible: bool = True


@dataclass(frozen=True)
class OptionSpec:
    id: str
    title: str
    kind: str = "text"
    default: Any = ""
    choices: tuple[str, ...] = ()
    minimum: int = 1
    maximum: int = 100


@dataclass(frozen=True)
class ModuleSpec:
    id: str
    title: str
    version: str
    actions: tuple[ActionSpec, ...]
    install_extra: str = ""
    options: tuple[OptionSpec, ...] = ()
    supports_directory: bool = False
    visible: bool = True


@dataclass(frozen=True)
class JobRequest:
    module: str
    action: str
    inputs: tuple[str, ...]
    config: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 600.0


@dataclass
class JobRecord:
    id: str
    request: JobRequest
    state: JobState = JobState.QUEUED
    progress: float = 0.0
    message: str = "В очереди"
    result_path: str | None = None
    error: str = ""
    started_at: float | None = None
    finished_at: float | None = None
    pid: int | None = None


@dataclass(frozen=True)
class JobEvent:
    job_id: str
    state: JobState
    progress: float
    message: str
