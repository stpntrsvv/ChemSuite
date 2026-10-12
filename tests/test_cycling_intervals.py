"""Synthetic regressions for MPR charge intervals, without private data."""

import csv
import json
from pathlib import Path

import pytest

from chem_suite.bootstrap import builtins
from chem_suite.core.contracts import JobRequest, JobState
from chem_suite.core.jobs import JobService
from chem_suite.locale import text
from chem_suite.modules.cycling.analysis import analyze_steps
from chem_suite.modules.cycling.readers import load_steps
from tests.cycling_fixtures import INTERVAL_ROWS, mpr_intervals
from tests.test_isolation import wait
from tests.test_science import Context


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("charge_id", [7, 23])
def test_recorded_charge_preserves_intervals_at_mode_and_direction_boundaries(tmp_path, version, charge_id):
    path = mpr_intervals(tmp_path / "intervals.mpr", version=version, charge_id=charge_id)
    steps = load_steps(path, Context(), voltage_kind="cell")
    assert sum(len(s.time_s) for s in steps) == len(INTERVAL_ROWS)
    assert all(s.current_channel == "dq/dt" for s in steps)
    assert steps[1].current_a == pytest.approx([0.01])
    result = analyze_steps(steps, Context())
    row = result["cycles"][0]
    assert result["cycle_count"] == result["paired_cycle_count"] == 1
    assert row["charge_mAh"] == 3
    assert row["discharge_mAh"] == 2
    assert row["coulombic_efficiency_percent"] == pytest.approx(200 / 3)
    assert row["charge_duration_s"] == 1080
    assert row["discharge_duration_s"] == 720
    assert row["charge_energy_mWh"] is row["discharge_energy_mWh"] is None
    assert row["voltage_efficiency_percent"] is row["energy_efficiency_percent"] is None
    assert "biologic_interval_charge" in result["diagnostics"]
    assert not any(d.startswith("unintegrated_boundary") for d in result["diagnostics"])


def test_measured_current_has_priority_and_explicit_choice_is_respected(tmp_path):
    path = mpr_intervals(tmp_path / "with-current.mpr", measured_current=True)
    measured = load_steps(path, Context())
    assert all(s.current_channel == "I/mA" and s.charge_increment_mah is None for s in measured)
    explicit = analyze_steps(load_steps(path, Context(), current_channel="dq/dt"), Context())
    assert explicit["cycles"][0]["charge_mAh"] == 3
    path = mpr_intervals(tmp_path / "without-current.mpr")
    with pytest.raises(ValueError, match="requires signed"):
        load_steps(path, Context(), current_channel="I/mA")


@pytest.mark.parametrize("problem, message", [
    ("first-charge", "positive time interval"),
    ("duplicate-time", "positive time interval"),
    ("backwards-time", "backwards"),
    ("nonfinite", "nonfinite"),
    ("instrument-error", "instrument error"),
])
def test_invalid_intervals_fail_instead_of_guessing(tmp_path, problem, message):
    rows = [list(r) for r in INTERVAL_ROWS]
    if problem == "first-charge":
        rows[0][5] = 1
    elif problem == "duplicate-time":
        rows[1][3] = 0
    elif problem == "backwards-time":
        rows[2][3] = -1
    elif problem == "nonfinite":
        rows[1][5] = float("nan")
    else:
        rows[1][1] = 1
    with pytest.raises(ValueError, match=message):
        load_steps(mpr_intervals(tmp_path / "invalid.mpr", rows=rows), Context())


def test_charge_on_first_ocv_sample_is_not_discarded(tmp_path):
    rows = [list(r) for r in INTERVAL_ROWS]
    rows[3][0] = 3
    result = analyze_steps(load_steps(mpr_intervals(tmp_path / "boundary.mpr", rows=rows), Context()), Context())
    assert result["cycles"][0]["discharge_mAh"] == 2


@pytest.mark.parametrize("rest_only", [False, True])
def test_worker_exports_charge_provenance_and_keeps_rest_only_preview(tmp_path, rest_only):
    rows = [[3, 0, 0, n, 1.5, 0, 0] for n in range(8)] if rest_only else INTERVAL_ROWS
    path = mpr_intervals(tmp_path / "source.mpr", rows=rows)
    original = path.read_bytes()
    with JobService(builtins(), tmp_path / "workspace") as jobs:
        record = jobs.submit(JobRequest("cycling", "analyze", (str(path),), {"voltage_kind": "cell"}))
        wait(jobs, lambda: record.state.terminal, timeout=30)
        assert record.state == JobState.SUCCEEDED, record.error
        result_path = Path(record.result_path)
        payload = json.loads(result_path.read_text())["payload"]
        assert payload["cycle_count"] == (0 if rest_only else 1)
        with (result_path.parent / "measurements.csv").open() as f:
            exported = list(csv.DictReader(f))
        assert len(exported) == len(rows)
        assert [float(r["time_s"]) for r in exported] == [r[3] for r in rows]
        assert [float(r["elapsed_s"]) for r in exported] == [r[3] for r in rows]
        assert all(r["current_basis"] == "interval_mean" for r in exported)
        assert all(r["energy_from_step_mWh"] == "" for r in exported)
        assert [float(r["charge_increment_mAh"]) for r in exported] == [r[5] for r in rows]
        if rest_only:
            assert "rest_only_recording" in payload["diagnostics"]
            ut = next(p for p in payload["plots"] if p["title"] == "Напряжение во времени")
            assert ut["series"]
        else:
            assert exported[1]["capacity_from_step_mAh"] == "1.0"
            assert exported[2]["capacity_from_phase_mAh"] == "3.0"
    assert path.read_bytes() == original


def test_interval_warnings_are_human_readable_in_both_languages():
    for key in ("biologic_interval_charge", "rest_only_recording"):
        assert text(key, "ru") != key
        assert text(key, "en") != key
