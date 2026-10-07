import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from chem_suite.bootstrap import builtins
from chem_suite.core.contracts import JobRequest, JobState
from chem_suite.core.jobs import JobService
from chem_suite.modules.cycling.analysis import analyze_steps
from chem_suite.modules.cycling.readers import load_steps
from chem_suite.modules.cycling.views import select_cycles
from tests.test_isolation import wait
from tests.test_science import Context
from tests.cycling_fixtures import mpt, mpr, ROWS, NAMES


def completed(jobs, record):
    wait(jobs, lambda: record.state.terminal, 30)
    assert record.state == JobState.SUCCEEDED, record.error
    return json.loads(Path(record.result_path).read_text())


@pytest.mark.parametrize("suffix", ["mpt", "mpr2", "mpr3"])
def test_biologic_units_cc_cv_rest_zero_cycle_and_charge_weighted_energy(tmp_path, suffix):
    path = (
        mpt(tmp_path / "public.mpt", comma=True)
        if suffix == "mpt"
        else mpr(tmp_path / "public.mpr", version=int(suffix[-1]))
    )
    steps = load_steps(path, Context())
    assert len(steps) == 4
    assert steps[0].current_a[0] == pytest.approx(0.01)
    assert steps[2].current_a == [0.0, 0.0]  # open-circuit mode overrides residual current
    result = analyze_steps(steps, Context(), nominal_capacity_mah=20, reference_cycle=0)
    row = result["cycles"][0]
    assert row["cycle"] == 0
    assert row["charge_mAh"] == pytest.approx(15)
    assert row["discharge_mAh"] == pytest.approx(8)
    assert row["charge_energy_mWh"] == pytest.approx(22.5, rel=1e-6)
    assert row["discharge_energy_mWh"] == pytest.approx(9.6, rel=1e-6)
    assert row["coulombic_efficiency_percent"] == pytest.approx(100 * 8 / 15)
    assert row["voltage_efficiency_percent"] == pytest.approx(80, rel=1e-6)
    assert row["energy_efficiency_percent"] == pytest.approx(100 * 9.6 / 22.5, rel=1e-6)
    assert row["retention_percent"] == 100 and row["utilization_percent"] == 40
    assert result["rests"][0]["duration_s"] == 100
    assert result["rests"][0]["cycle"] == 0
    assert result["rests"][0]["voltage_change_V"] == pytest.approx(-0.05, abs=1e-6)
    assert not any(flag.startswith("unintegrated_boundary") for flag in result["diagnostics"])


def test_electrode_potential_cannot_be_reported_as_cell_efficiency(tmp_path):
    path = mpt(tmp_path / "three-electrode.mpt")
    steps = load_steps(path, Context(), voltage_channel="Ewe/V")
    result = analyze_steps(steps, Context())
    row = result["cycles"][0]
    assert row["coulombic_efficiency_percent"] is not None
    assert row["voltage_efficiency_percent"] is row["energy_efficiency_percent"] is None
    assert row["charge_energy_mWh"] is None
    assert "electrode_potential:0:VE_EE_unavailable" in result["diagnostics"]
    # In a two-electrode setup the user can explicitly declare Ewe to be cell voltage.
    steps = load_steps(path, Context(), voltage_channel="Ewe/V", voltage_kind="cell")
    row = analyze_steps(steps, Context())["cycles"][0]
    assert row["voltage_efficiency_percent"] == pytest.approx(100 * 1.4 / 1.7)
    with pytest.raises(ValueError, match="unavailable"):
        load_steps(path, Context(), voltage_channel="Ece/V")


def test_no_cycle_column_uses_direction_and_pause_preserves_cc_cv_cycle(tmp_path):
    names = [name for name in NAMES if name != "cycle number"]
    rows = [row[:6] + row[7:] for row in ROWS]
    path = mpt(tmp_path / "without-cycle.mpt", rows, names)
    result = analyze_steps(load_steps(path, Context()), Context())
    assert result["cycle_count"] == 1
    assert [r["cycle"] for r in result["steps"]] == [1, 1, 1]
    assert result["cycles"][0]["charge_mAh"] == 15


@pytest.mark.parametrize(
    "change, message",
    [
        ("time", "backwards"),
        ("error", "instrument error"),
        ("cycle", "integer"),
        ("impedance", "EIS"),
        ("unsigned", "signed"),
    ],
)
def test_invalid_biologic_records_fail_instead_of_guessing(tmp_path, change, message):
    rows = [list(row) for row in ROWS]
    names = list(NAMES)
    if change == "time":
        rows[1][2] = -1
    elif change == "error":
        rows[1][1] = 1
    elif change == "cycle":
        rows[1][6] = 0.5
    elif change == "impedance":
        names.append("freq/Hz")
        rows = [r + [100] for r in rows]
    elif change == "unsigned":
        names[5] = "|I|/A"
    with pytest.raises(ValueError, match=message):
        load_steps(mpt(tmp_path / "bad.mpt", rows, names), Context())


def test_mpt_reader_accepts_bt_lab_and_ignores_unrelated_export_columns(tmp_path):
    names = NAMES + ["operator comment", "Temperature/°C"]
    rows = [r + ["GCPL µ", 25] for r in ROWS]
    path = mpt(tmp_path / "bt.mpt", rows, names, encoding="cp1252", magic="BT-Lab ASCII FILE")
    assert len(load_steps(path, Context())) == 4


@pytest.mark.parametrize("encoding", ["cp1252", "utf-8"])
def test_mpt_encoding_detection_checks_beyond_ascii_prefix(tmp_path, encoding):
    path = mpt(
        tmp_path / "long-header.mpt",
        [r + [25] for r in ROWS],
        NAMES + ["Temperature/°C"],
        encoding=encoding,
        magic="BT-Lab ASCII FILE",
    )
    # Reproduce a real formation export whose first non-ASCII byte is at 14 KiB.
    raw = path.read_bytes().splitlines(keepends=True)
    raw[2] = b"comment: " + b"a" * 16000 + b"\n"
    path.write_bytes(b"".join(raw))
    assert len(load_steps(path, Context())) == 4


def test_selected_cycles_validate_gaps_ranges_and_preview_bounds():
    assert select_cycles("0, 3-4", [0, 3, 4]) == {0, 3, 4}
    assert len(select_cycles("", range(1000), preview=True)) == 10
    assert 999 in select_cycles("", range(1000), preview=True)
    for selection in ["1-0", "1-99999999", "1;2", "4", "-1"]:
        with pytest.raises(ValueError):
            select_cycles(selection, [0, 1, 2])


def test_biologic_batch_files_unique_ids_provenance_full_export_and_archived_reanalysis(tmp_path):
    one, two = mpt(tmp_path / "1.mpt"), mpt(tmp_path / "2.mpt")
    digest = hashlib.sha256(one.read_bytes()).hexdigest()
    with JobService(builtins(), tmp_path / "work", max_active=1) as jobs:
        analysis = jobs.submit(JobRequest("cycling", "analyze", (str(one), str(two))))
        result = completed(jobs, analysis)
        payload = result["payload"]
        assert payload["cycle_count"] == 2
        rows = json.loads((Path(analysis.result_path).parent / "metrics.json").read_text())
        assert [r["cycle"] for r in rows["cycles"]] == [0, 1]
        assert [r["charge_mAh"] for r in rows["cycles"]] == [15, 15]
        assert len(set(r["step_id"] for r in rows["steps"] + rows["rests"])) == 8
        assert payload["sources"][0]["sha256"] == digest
        assert payload["plots"][0]["series"][1]["x"] == [10, 15]
        assert payload["plots"][-1]["series"][0]["y"] == [10, 10]
        export = jobs.submit(
            JobRequest(
                "exports",
                "export",
                (analysis.result_path,),
                {
                    "destination": str(tmp_path / "export"),
                    "outputs": {"excel": False},
                    "formats": [],
                    "module_config": {"export_scope": "selected", "selected_cycles": "1"},
                },
            )
        )
        completed(jobs, export)
        folder = next(p for p in (tmp_path / "export").iterdir() if p.is_dir())
        with (folder / "cycles.csv").open(encoding="utf-8-sig") as stream:
            assert [r["cycle"] for r in csv.DictReader(stream)] == ["1"]
        with (folder / "waveforms.csv").open(encoding="utf-8-sig") as stream:
            raw = list(csv.DictReader(stream))
        assert len(raw) == len(ROWS)
        assert {r["cycle"] for r in raw} == {"1"}
        reopened = completed(
            jobs, jobs.submit(JobRequest("sessions", "result", (str(folder / "result.json"),)))
        )
        restored = Path(reopened["payload"]["result_path"])
        scientific = json.loads(restored.read_text())["payload"]
        inputs, identities = [], {}
        for source in scientific["sources"]:
            snapshot = str(restored.parent / source["snapshot"])
            inputs.append(snapshot)
            identities[snapshot] = {"path": source["path"], "sha256": source["sha256"]}
        one.unlink()
        two.unlink()
        rerun = completed(
            jobs,
            jobs.submit(JobRequest("cycling", "analyze", tuple(inputs), {"source_identities": identities})),
        )
        assert rerun["payload"]["sources"][0]["path"] == str(one)
        assert rerun["payload"]["sources"][0]["sha256"] == digest
        assert rerun["payload"]["cycle_count"] == 2


def test_flowbat_config_theoretical_capacity_and_discovery_exclude_sensors_and_camera(tmp_path):
    folder = tmp_path / "flowbat"
    raw = folder / "01_Raw_data" / "potentiostat"
    raw.mkdir(parents=True)
    mpt(raw / "gcpl.mpt")
    (folder / "config.json").write_text(
        json.dumps(
            {
                "experiment_info": {"potentiostat": "BioLogic"},
                "electrolyte": {"concentration_M": 1, "volume_ml": 10, "electrons_transferred": 1},
            }
        )
    )
    sensor = folder / "01_Raw_data" / "OCV"
    sensor.mkdir()
    (sensor / "logger.csv").write_text("sensors excluded")
    with JobService(builtins(), tmp_path / "work") as jobs:
        discovered = completed(jobs, jobs.submit(JobRequest("cycling", "discover", (str(tmp_path),))))
        assert [e["path"] for e in discovered["payload"]["experiments"]] == [str(folder)]
        record = jobs.submit(JobRequest("cycling", "analyze", (str(folder),)))
        payload = completed(jobs, record)["payload"]
        assert payload["nominal_capacity_mAh"] == pytest.approx(96485.33212 * 0.01 / 3.6)
        assert payload["nominal_capacity_origin"] == "electrolyte_nFcV"
        assert len(payload["sources"]) == 2
        assert payload["sources"][0]["role"] == "configuration"
        assert payload["experiment"]["name"] == "flowbat"
        assert len(payload["tables"][2]["rows"]) == 1


def test_mpt_import_math_and_plots_use_stdlib_without_extra_packages(tmp_path):
    path = mpt(tmp_path / "stdlib.mpt")
    source = Path(__file__).parents[1] / "src"
    script = "import sys; sys.path.insert(0," + repr(str(source)) + "); "
    script += "from chem_suite.modules.cycling.readers import load_steps; from chem_suite.modules.cycling.analysis import analyze_steps; "
    script += "from pathlib import Path; from types import SimpleNamespace; c=SimpleNamespace(check_cancelled=lambda:None,progress=lambda *args:None); "
    script += (
        "r=analyze_steps(load_steps(Path("
        + repr(str(path))
        + "),c),c); assert r['cycles'][0]['charge_mAh']==15; "
    )
    script += "assert not any(m in sys.modules for m in ('numpy','scipy','galvani','PySide6','matplotlib')); print('ok')"
    result = subprocess.run([sys.executable, "-S", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_biologic_recorded_boundaries_are_not_invented_and_partial_cycle_is_marked(tmp_path):
    rows = [list(row) for row in ROWS[:4]]
    rows[2][2] += 10
    rows[3][2] += 10
    result = analyze_steps(load_steps(mpt(tmp_path / "gap.mpt", rows), Context()), Context())
    assert result["cycles"][0]["charge_mAh"] == 15
    assert result["cycles"][0]["coulombic_efficiency_percent"] is None
    assert any(d.startswith("unintegrated_boundary_s:") and d.endswith(":10") for d in result["diagnostics"])
    assert "incomplete_cycle:0" in result["diagnostics"]


def test_full_resolution_biologic_export_and_bounded_preview_preserve_endpoints(tmp_path):
    from chem_suite.modules.cycling.exporting import export_data

    rows = [[1, 0, k * 3600 / 5000, 1.7, 1.5, 10, 0, 0] for k in range(5001)] + [list(r) for r in ROWS[2:]]
    source = mpt(tmp_path / "dense.mpt", rows)
    with JobService(builtins(), tmp_path / "work") as jobs:
        record = jobs.submit(JobRequest("cycling", "analyze", (str(source),)))
        result = completed(jobs, record)
        curve = result["payload"]["plots"][0]["series"][0]
        assert len(curve["x"]) <= 2000
        assert curve["x"][0] == 0 and curve["x"][-1] == pytest.approx(10)
        _, tables, groups = export_data(Path(record.result_path), result, {}, Context())
        assert len(groups["cycling_plots"][0]["series"][0]["x"]) == 5001
        assert len(tables["waveforms"]) == len(rows)
        assert isinstance(tables["waveforms"][1]["capacity_from_step_mAh"], float)


def test_many_steps_in_one_cycle_keep_bounded_preview_and_complete_export(tmp_path):
    from chem_suite.modules.cycling.exporting import export_data

    rows = [
        [1, 0, 2 * ns + point, 1.7, 1.5, 10, 0, ns]
        for ns in range(151)
        for point in (0, 1)
    ]
    source = mpt(tmp_path / "many-pulses.mpt", rows)
    with JobService(builtins(), tmp_path / "work") as jobs:
        record = jobs.submit(JobRequest("cycling", "analyze", (str(source),)))
        result = completed(jobs, record)
        payload = result["payload"]
        assert payload["step_count"] == 151
        assert payload["preview_step_count"] == 120
        assert payload["preview_step_total"] == 151
        assert payload["cycle_count"] == 1
        assert payload["tables"][1]["total_rows"] == 151
        time_plot = payload["plots"][5]["series"]
        assert len(time_plot) == 120
        assert sum(len(curve["x"]) for curve in time_plot) <= 6000
        assert time_plot[0]["x"][0] == 0
        assert time_plot[-1]["x"][-1] == 301
        explicit = completed(
            jobs, jobs.submit(JobRequest("cycling", "view", (record.result_path,), {"selected_cycles": "0"}))
        )
        assert explicit["payload"]["preview_step_count"] == 120
        _, tables, groups = export_data(Path(record.result_path), result, {}, Context())
        assert len(tables["waveforms"]) == 302
        assert len(groups["cycling_plots"][0]["series"]) == 151


def test_biologic_explicit_current_channels_and_ampere_units(tmp_path):
    rows = [list(row) for row in ROWS]
    for row in rows:
        row[5] /= 1000
    names = ["I/A" if name == "I/mA" else name for name in NAMES]
    names.append("<I>/mA")
    rows = [row + [row[5] * 2000] for row in rows]
    path = mpt(tmp_path / "currents.mpt", rows, names)
    averaged = analyze_steps(load_steps(path, Context()), Context())
    instantaneous = analyze_steps(load_steps(path, Context(), current_channel="I/A"), Context())
    assert averaged["cycles"][0]["charge_mAh"] == pytest.approx(30)
    assert instantaneous["cycles"][0]["charge_mAh"] == pytest.approx(15)
    assert averaged["steps"][0]["current_channel"] == "<I>/mA"
    with pytest.raises(ValueError, match="signed"):
        load_steps(path, Context(), current_channel="I/mA")


def test_full_export_includes_initial_pause_without_active_cycle_number(tmp_path):
    from chem_suite.modules.cycling.exporting import export_data

    names = [n for n in NAMES if n != "cycle number"]
    rows = [[3, 0, 0, 1.6, 1.4, 0, 99], [3, 0, 100, 1.6, 1.4, 0, 99]]
    for row in ROWS:
        values = row[:6] + row[7:]
        values[2] += 100
        rows.append(values)
    source = mpt(tmp_path / "initial-rest.mpt", rows, names)
    with JobService(builtins(), tmp_path / "work") as jobs:
        record = jobs.submit(JobRequest("cycling", "analyze", (str(source),)))
        result = completed(jobs, record)
        _, tables, groups = export_data(Path(record.result_path), result, {}, Context())
        assert tables["rests"][0]["cycle"] is None
        assert len(tables["waveforms"]) == len(rows)
        assert groups["cycling_time"][0]["series"][0]["label"] == "Rest before cycling"
