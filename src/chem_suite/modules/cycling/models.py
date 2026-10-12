import math
from dataclasses import dataclass


@dataclass
class CyclingStep:
    source_index: tuple[str, ...]
    time_s: list[float]
    voltage_v: list[float]
    current_a: list[float]
    declared_cycle: int | None = None
    source_file: str = ""
    step_id: str = ""
    voltage_channel: str = "voltage_V"
    voltage_kind: str = "cell"
    time_basis: str = "step"
    instrument_mode: int | None = None
    instrument_cycle: int | float | None = None
    current_channel: str = "current_A"
    # Signed instrument charge per incoming interval, in mAh. This is not a
    # sampled instantaneous current and must never be integrated as trapezoids.
    charge_increment_mah: list[float] | None = None
    interval_start_s: float | None = None

    @property
    def start_s(self):
        return self.time_s[0] if self.interval_start_s is None else self.interval_start_s

    @property
    def energy_available(self):
        # Mean current alone does not establish the integral of V(t) * I(t).
        return self.charge_increment_mah is None

    def validate(self):
        if not self.time_s or not (len(self.time_s) == len(self.voltage_v) == len(self.current_a)):
            raise ValueError(f"Empty or mismatched step: {self.source_index}")
        if not all(math.isfinite(x) for a in (self.time_s, self.voltage_v, self.current_a) for x in a):
            raise ValueError(f"Nonfinite measurement in step {self.source_index}")
        if any(b < a for a, b in zip(self.time_s, self.time_s[1:])):
            raise ValueError(f"Time runs backwards in step {self.source_index}")
        if self.charge_increment_mah is not None:
            if len(self.charge_increment_mah) != len(self.time_s) or not all(
                math.isfinite(q) for q in self.charge_increment_mah
            ):
                raise ValueError(f"Invalid charge intervals in step {self.source_index}")
            if self.interval_start_s is not None and (
                not math.isfinite(self.interval_start_s) or self.interval_start_s > self.time_s[0]
            ):
                raise ValueError(f"Invalid interval start in step {self.source_index}")
