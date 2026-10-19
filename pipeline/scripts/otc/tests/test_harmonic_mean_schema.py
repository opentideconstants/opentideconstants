"""Explicit harmonic-mean frame and unavailable computed-fallback sigma."""
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from otc_pipeline import release_writer


@pytest.fixture(scope="module")
def native_ticon():
    evidence_root = os.environ.get("OTC_TICON_FOUNDATION_PROOF")
    if not evidence_root:
        pytest.skip("requires pinned native TICON foundation evidence")
    root = Path(evidence_root)
    facts = json.loads((root / "native-source-bindings.json").read_text())
    fixture = Path(facts["fixture_path"])
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == facts["fixture_sha256"]
    document = json.loads(fixture.read_text())
    return root, facts["witnesses"], document["stations"]


def validator(definition):
    schema = release_writer.pinned_schema()
    return Draft202012Validator({"$schema": schema["$schema"], "$defs": schema["$defs"],
                                 "$ref": f"#/$defs/{definition}"})


@pytest.mark.parametrize("index", range(3))
def test_original_native_station_schema_still_valid(native_ticon, index):
    _, _, originals = native_ticon
    assert list(validator("station").iter_errors(originals[index])) == []


@pytest.mark.parametrize("index", range(3))
def test_native_ticon_frame_only_schema(native_ticon, index):
    _, witnesses, originals = native_ticon
    witness = witnesses[index]
    station = copy.deepcopy(next(s for s in originals if s["station_id"] == witness["station_id"]))
    selected = next(s for s in station["constant_sets"] if s["set_id"] == witness["set_id"])
    assert selected["source_record_id"] == witness["source_record_id"]
    assert selected["source_version"] == witness["source_version"]
    before = copy.deepcopy(station)
    assert "datum" not in selected
    selected["datum"] = {"zero": "harmonic_mean"}
    errors = [error.message for top in validator("station").iter_errors(station)
              for error in release_writer._leaf_errors(top)]
    print(f"REAL FRAME {witness['set_id']}: frame=harmonic_mean; no MSL, named extrema or sigma fabricated; "
          f"schema_errors={errors}")
    assert errors == []
    del selected["datum"]
    assert station == before


def computed_basis(fallback=True):
    basis = {"kind": "computed", "method": "harmonic_extremes",
             "epoch": {"start": "2020-01-01", "end": "2038-12-31", "name": "OTC LAT 2020-2038"}}
    if fallback:
        basis["fallback"] = {"reason": "datum_gate_failed", "basis": "Explicit harmonic-mean-relative computation",
                             "otc_finding": "Uncertainty unestablished; normal acceptance false"}
    return basis


def test_computed_gate_failed_fallback_may_omit_unknown_uncertainty_pair():
    assert list(validator("datum_basis").iter_errors(computed_basis())) == []


def test_ordinary_computed_basis_still_requires_uncertainty():
    assert list(validator("datum_basis").iter_errors(computed_basis(False)))


@pytest.mark.parametrize("update", [{"uncertainty_m": 0.1}, {"uncertainty_basis": "propagated"},
    {"uncertainty_m": -0.1, "uncertainty_basis": "propagated"},
    {"uncertainty_m": None, "uncertainty_basis": "propagated"},
    {"uncertainty_m": True, "uncertainty_basis": "propagated"},
    {"uncertainty_m": 0.1, "uncertainty_basis": "source"}])
def test_known_or_partial_computed_uncertainty_keeps_existing_validation(update):
    assert list(validator("datum_basis").iter_errors({**computed_basis(), **update}))


def test_known_computed_uncertainty_pair_still_valid():
    assert list(validator("datum_basis").iter_errors({**computed_basis(), "uncertainty_m": 0.1,
                                                       "uncertainty_basis": "propagated"})) == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_uncertainty_is_not_a_json_document(value):
    with pytest.raises(ValueError, match="Out of range float"):
        json.dumps({**computed_basis(), "uncertainty_m": value, "uncertainty_basis": "propagated"}, allow_nan=False)


@pytest.mark.parametrize("extra", [{"msl_offset_m": 0.0}, {"zero_name": "MSL"}])
def test_harmonic_mean_frame_refuses_contradictory_absolute_metadata(extra):
    assert list(validator("datum").iter_errors({"zero": "harmonic_mean", **extra}))


def test_ordinary_absolute_frame_unchanged():
    assert list(validator("datum").iter_errors({"zero": "gauge_zero", "zero_name": "STND", "msl_offset_m": 1.2})) == []
