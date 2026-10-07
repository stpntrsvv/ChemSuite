"""Opt-in, offline regression against independently published BioLogic data."""

import csv
import hashlib
import json
import os
from pathlib import Path

import pytest
from chem_suite.modules.cycling.analysis import analyze_steps
from chem_suite.modules.cycling.readers import load_steps
from tests.test_science import Context

MANIFEST = json.loads((Path(__file__).parent / "public_data/biologic.json").read_text())


@pytest.fixture
def public_data():
    location = os.environ.get("CHEMSUITE_BIOLOGIC_TEST_DATA")
    if not location:
        pytest.skip("Set CHEMSUITE_BIOLOGIC_TEST_DATA to the verified public-data cache")

    def recording(identity):
        entry = next(e for e in MANIFEST["files"] if e["id"] == identity)
        path = Path(location) / entry["filename"]
        if entry["large"] and not path.is_file():
            pytest.skip("Optional SINTEF recording not downloaded; use --include-large")
        assert path.is_file(), f"Missing fixture: {path}; run tools/fetch_biologic_test_data.py"
        assert path.stat().st_size == entry["size"]
        algorithm = "sha256" if "sha256" in entry else "md5"
        with path.open("rb") as stream:
            assert hashlib.file_digest(stream, algorithm).hexdigest() == entry[algorithm]
        return path

    return recording


def test_real_formation_binary_text_parity_and_numeric_baseline(public_data):
    results = []
    for identity in ("formation-mpr", "formation-mpt"):
        # Align numbering: MPR lacks the cycle number derived during MPT export.
        steps = load_steps(public_data(identity), Context(), cycle_policy="direction")
        assert sum(len(s.time_s) for s in steps) == 1323
        assert len(steps) == 11
        assert {s.instrument_mode for s in steps} == {1, 2, 3}
        result = analyze_steps(steps, Context())
        assert len(result["rests"]) == 5
        assert result["cycle_count"] == 2
        assert [r["cycle"] for r in result["cycles"]] == [1, 2]
        # galvani exposes the binary voltage as Ewe; BT-Lab exports Ecell.
        # Ewe alone does not establish a full-cell voltage convention.
        if identity == "formation-mpr":
            assert all(r["energy_efficiency_percent"] is None for r in result["cycles"])
        else:
            assert all(r["energy_efficiency_percent"] is not None for r in result["cycles"])
        results.append(result)
    for binary, text in zip(results[0]["cycles"], results[1]["cycles"], strict=True):
        for key in ("charge_mAh", "discharge_mAh", "coulombic_efficiency_percent"):
            assert binary[key] == pytest.approx(text[key], rel=2e-8)
    expected = [(62.16546124351016, 50.84896300352752), (51.42257646598667, 49.64532293782346)]
    for row, (charge, discharge) in zip(results[1]["cycles"], expected, strict=True):
        assert row["charge_mAh"] == pytest.approx(charge, rel=1e-9)
        assert row["discharge_mAh"] == pytest.approx(discharge, rel=1e-9)

    # Independent instrument accumulation is close, but not identical to
    # trapezoidal integration of recorded samples with omitted step boundaries.
    with public_data("formation-mpt").open(encoding="latin1") as stream:
        stream.readline()
        header_count = int(stream.readline().split(":")[1])
        for _ in range(header_count - 3):
            stream.readline()
        raw = list(csv.DictReader(stream, delimiter="\t"))
    for cycle, calculated in enumerate(results[1]["cycles"]):
        rows = [r for r in raw if float(r["cycle number"]) == cycle]
        for phase in ("charge", "discharge"):
            recorded = max(float(r[f"Q {phase}/mA.h"]) for r in rows)
            assert calculated[f"{phase}_mAh"] == pytest.approx(recorded, rel=0.005)


def test_real_gcpl_decimal_comma_and_missing_binary_current(public_data):
    steps = load_steps(public_data("gcpl-mpt"), Context())
    result = analyze_steps(steps, Context())
    assert sum(len(s.time_s) for s in steps) == 132
    assert len(result["rests"]) == 4
    assert [r["cycle"] for r in result["cycles"]] == [0, 1, 2, 3]
    assert result["cycles"][0]["charge_mAh"] == pytest.approx(8.329424723287879e-5, rel=1e-9)
    assert result["cycles"][0]["discharge_mAh"] == pytest.approx(8.183806006168985e-5, rel=1e-9)
    with pytest.raises(ValueError, match="requires signed"):
        load_steps(public_data("gcpl-mpr"), Context())


def test_real_sintef_cccv_instrument_error_is_not_a_success(public_data):
    with pytest.raises(ValueError, match="instrument error flag at data row 69922"):
        load_steps(public_data("sintef-cccv-outlier"), Context())


def test_real_sintef_gitt_recording(public_data):
    steps = load_steps(public_data("sintef-gitt"), Context())
    assert all(s.voltage_channel == "Ecell/V" and s.voltage_kind == "cell" for s in steps)
    assert sum(len(s.time_s) for s in steps) == 157985
    assert len(steps) == 227
    result = analyze_steps(steps, Context())
    assert result["cycle_count"] == 1
    assert len(result["rests"]) == 115
    row = result["cycles"][0]
    assert row["cycle"] == 0
    assert row["charge_mAh"] == pytest.approx(11091.626261636557, rel=1e-9)
    assert row["discharge_mAh"] == pytest.approx(11072.844003608345, rel=1e-9)
    assert row["coulombic_efficiency_percent"] == pytest.approx(99.8306627217221, rel=1e-9)
    assert row["energy_efficiency_percent"] == pytest.approx(97.33513918345191, rel=1e-9)
    # Independent BT-Lab accumulated capacities from the unmodified recording.
    with public_data("sintef-gitt").open(encoding="utf-8") as stream:
        stream.readline()
        header_count = int(stream.readline().split(":")[1])
        for _ in range(header_count - 3):
            stream.readline()
        recorded = {"charge": 0.0, "discharge": 0.0}
        for raw in csv.DictReader(stream, delimiter="\t"):
            for phase in recorded:
                recorded[phase] = max(recorded[phase], float(raw[f"Q {phase}/mA.h"]))
    for phase in recorded:
        assert row[f"{phase}_mAh"] == pytest.approx(recorded[phase], rel=4e-5)
