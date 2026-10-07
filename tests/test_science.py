import hashlib
import json
from pathlib import Path

import pytest

from chem_suite.bootstrap import builtins
from chem_suite.core.artifacts import export_result
from chem_suite.core.contracts import JobRequest, JobState
from chem_suite.core.jobs import JobService
from chem_suite.modules.cycling.analysis import analyze_steps
from chem_suite.modules.cycling.models import CyclingStep
from chem_suite.modules.cycling.readers import load_steps
from tests.test_isolation import wait

ROOT = Path(__file__).parents[1]


class Context:
    def check_cancelled(self):
        pass

    def progress(self, *_):
        pass


def test_real_modules_in_parallel_and_provenance(tmp_path):
    eis_source = ROOT / "examples/eis_double_cpe.txt"
    cycling_source = ROOT / "examples/cycling.csv"
    digest = hashlib.sha256(eis_source.read_bytes()).hexdigest()
    with JobService(builtins(), tmp_path) as jobs:
        eis = jobs.submit(
            JobRequest("eis", "fit", (str(eis_source),), {"circuit": "R0-p(R1,CPE0)-p(R2,CPE1)"})
        )
        cycling = jobs.submit(JobRequest("cycling", "analyze", (str(cycling_source),)))
        wait(jobs, lambda: eis.state.terminal and cycling.state.terminal, timeout=45)
        assert eis.state == cycling.state == JobState.SUCCEEDED, (eis.error, cycling.error)
        assert eis.pid != cycling.pid
        data = json.loads(Path(eis.result_path).read_text())
        assert data["payload"]["point_count"] == 60
        assert data["payload"]["kk"]["status"] == "PASS"
        assert data["payload"]["best"]["status"] == "WARN"
        assert data["payload"]["best"]["mean_fit_error_percent"] == pytest.approx(0.999566, abs=1e-5)
        assert data["payload"]["sources"][0]["sha256"] == digest
        assert hashlib.sha256(eis_source.read_bytes()).hexdigest() == digest
        payload = json.loads(Path(cycling.result_path).read_text())["payload"]
        first = payload["tables"][0]["rows"][0]
        assert first["charge_mAh"] == pytest.approx(10)
        assert first["discharge_mAh"] == pytest.approx(8)
        assert first["coulombic_efficiency_percent"] == pytest.approx(80)
        assert first["voltage_efficiency_percent"] == pytest.approx(80)
        assert first["energy_efficiency_percent"] == pytest.approx(64)
        bundle = export_result(cycling.result_path, str(tmp_path / "export"))
        assert (bundle / "metrics.json").is_file()
        assert json.loads((bundle / "result.json").read_text())["payload"]["cycle_count"] == 2
        with pytest.raises(FileExistsError):
            export_result(cycling.result_path, str(bundle))


def test_requested_eis_channel_is_respected(tmp_path):
    from chem_suite.modules.eis.legacy.eis_io import load_eis_file

    source = tmp_path / "channels.csv"
    source.write_text(
        "freq,Re(Z1)/Ohm,-Im(Z1)/Ohm,Re(Z2)/Ohm,-Im(Z2)/Ohm\n"
        + "\n".join(f"{10 ** (i / 2)},1,2,10,20" for i in range(10))
    )
    data = load_eis_file(source, channel="Z2")
    assert data.metadata["selected_channel"] == "Z2"
    assert data.z.real[0] == 10 and data.z.imag[0] == -20
    with pytest.raises(ValueError, match="not available"):
        load_eis_file(source, channel="Zce")


def test_irregular_sampling_integrates_actual_power_and_rests_do_not_increment_cycles():
    steps = [
        CyclingStep(("1", "ch"), [0, 1, 3], [1, 2, 4], [1, 1, 1]),
        CyclingStep(("1", "rest"), [0, 10], [3, 3], [0, 0]),
        CyclingStep(("1", "dch"), [0, 3], [2, 2], [-1, -1]),
        CyclingStep(("1", "rest2"), [0, 10], [2, 2], [0, 0]),
        CyclingStep(("2", "ch"), [0, 3], [3, 3], [1, 1]),
    ]
    result = analyze_steps(steps, Context())
    assert [m["cycle"] for m in result["steps"]] == [1, 1, 2]
    assert result["steps"][0]["energy_mWh"] == pytest.approx(7.5 / 3.6)
    assert result["cycles"][0]["energy_efficiency_percent"] == pytest.approx(80)
    assert result["cycles"][1]["coulombic_efficiency_percent"] is None
    assert "incomplete_cycle:2" in result["diagnostics"]


@pytest.mark.parametrize(
    "step",
    [
        CyclingStep(("bad",), [0, 1], [1, 1], [1, -1]),
        CyclingStep(("bad",), [1, 0], [1, 1], [1, 1]),
        CyclingStep(("bad",), [0, 1], [1, float("nan")], [1, 1]),
    ],
)
def test_invalid_measurements_are_not_silently_used(step):
    with pytest.raises(ValueError):
        analyze_steps([step], Context())


def test_yarst_cp1251_and_elins_decimal_comma(tmp_path):
    yarst = tmp_path / "yarst.txt"
    yarst.write_bytes(
        (
            "Описание\n   Цикл   Шаг  Время,s    U,V    I,A T,°C ESR,mR   Q,Ah   E,Wh\n"
            "1 1 0 1.5 0.01 25 0 0 0\n1 1 3600 1.5 0.01 25 0 0 0\n"
            "1 2 0 1.2 -0.008 25 0 0 0\n1 2 3600 1.2 -0.008 25 0 0 0\n"
            "Прервано\n"
        ).encode("cp1251")
    )
    elins = tmp_path / "elins.txt"
    elins.write_bytes(
        (
            "ES8\n\nБлок 1:\nЦикл 1, Шаг 1\nВремя, с., Потенциал, В, Ток, А\n"
            "0 1,5 0,01\n3600 1,5 0,01\n\nЦикл 1, Шаг 2\n"
            "Время, с., Потенциал, В, Ток, А\n0 1,2 -0,008\n3600 1,2 -0,008\n"
        ).encode("cp1251")
    )
    for file in (yarst, elins):
        result = analyze_steps(load_steps(file, Context()), Context())
        assert len(result["cycles"]) == 1
        assert result["cycles"][0]["energy_efficiency_percent"] == pytest.approx(64)
