"""otc_pipeline.datums: the published-level helpers and build checks 8, 9 and 13 (datums spec §3.6)."""
from __future__ import annotations

import pytest

from otc_pipeline.datums import datum_build_errors, period, published
from otc_pipeline.gesla_sets import _published_stated_zero


def test_period():
    assert period("1983-01-01", "2001-12-31", "NTDE 1983-2001") == {
        "start": "1983-01-01", "end": "2001-12-31", "name": "NTDE 1983-2001"}
    assert period("2020-01-01", "2020-01-01") == {"start": "2020-01-01", "end": "2020-01-01"}
    with pytest.raises(ValueError):
        period("2021-02-30", "2021-03-01")          # not a real date
    with pytest.raises(ValueError, match="end before start"):
        period("2002-01-01", "2001-12-31")


def test_published():
    assert published("MSL") == {"kind": "published", "method": "source", "source_name": "MSL"}
    assert published("MHHW", epoch={"start": "1983-01-01", "end": "2001-12-31"}, control_source_id="8443970",
                     provisional=True) == {
        "kind": "published", "method": "source", "source_name": "MHHW",
        "epoch": {"start": "1983-01-01", "end": "2001-12-31"}, "control": {"source_id": "8443970"},
        "flags": ["provisional"]}
    with pytest.raises(ValueError):
        published("x" * 32)
    with pytest.raises(ValueError):
        published("")


def brest_header(**overrides):
    return {"record_id": "brest-3-fra-refmar", "source_version": "4.1", "contributor": "REFMAR",
            "site_code": "3", "country": "FRA", "datum_information": "Zero Hydrographique", **overrides}


@pytest.mark.parametrize("header", ["Zero Hydrographique", "  zErO hYdRoGrApHiQuE  "])
def test_confirmed_gesla_stated_chart_zero_is_published(header):
    original = {"msl_offset_m": 4.20872}
    datum = _published_stated_zero(brest_header(datum_information=header), original)
    assert datum == {"msl_offset_m": 4.20872, "zero": "chart_datum", "zero_name": "ZH",
                     "named": {"cd": 0.0}, "chart_datum": "cd",
                     "basis": {"cd": published("Zero Hydrographique")}}
    assert original == {"msl_offset_m": 4.20872}
    assert datum_build_errors(datum) == []


@pytest.mark.parametrize("overrides", [
    {"record_id": "unconfirmed-3-fra-refmar"}, {"source_version": "future"},
    {"contributor": "OTHER"}, {"site_code": "other"}, {"country": "SWE"},
    {"datum_information": "unknown"}, {"datum_information": "BSCD2000"},
    {"datum_information": None},
])
def test_unconfirmed_gesla_zero_is_unchanged(overrides):
    original = {"msl_offset_m": 4.20872}
    assert _published_stated_zero(brest_header(**overrides), original) == original


@pytest.mark.parametrize("record_id,contributor,site_code,country,header,zero_name,source_name", [
    ("abbot_point-59300-aus-bom", "BOM", "59300", "AUS", "Chart Datum / Lowest Astronomical Tide",
     "CD / LAT", "CD / LAT"),
    ("abbotts_harbour-382-can-meds", "MEDS", "382", "CAN", "Chart Datum (CD)", "CD", "Chart Datum (CD)"),
    ("aberdeen-abe-gbr-bodc", "BODC", "ABE", "GBR", "Admiralty Chart Datum (ACD) ",
     "ACD", "Admiralty Chart Datum (ACD)"),
    ("aberdeen-abe-gbr-cmems", "CMEMS", "Abe", "GBR", "Admiralty Chart Datum", "ACD", "Admiralty Chart Datum"),
    ("aiguillon_sur_mer-6257-fra-refmar", "REFMAR", "6257", "FRA", "Zero Hydrographique", "ZH", "Zero Hydrographique"),
    ("aiguillonsurmertg_60minute-aig-fra-cmems", "CMEMS", "Aig", "FRA", "chart datum", "CD", "Chart Datum"),
    ("antalya-ant-tur-eseas", "ESEAS", "ANT", "TUR", "Local Chart Datum", "Local CD", "Local Chart Datum"),
    ("howth_water_level_1-how-irl-mi_c", "MI_C", "HOW", "IRL", "Lowest Astronomical Tide (LAT)",
     "LAT", "Lowest Astronomical Tide (LAT)"),
    ("northcormoranttg-nor-gbr-cmems", "CMEMS", "Nor", "GBR", "ACD", "ACD", "ACD"),
])
def test_cached_published_chart_zero_families(record_id, contributor, site_code, country, header, zero_name, source_name):
    doc = brest_header(record_id=record_id, contributor=contributor, site_code=site_code,
                       country=country, datum_information=header)
    original = {"msl_offset_m": 1.25}
    datum = _published_stated_zero(doc, original)
    assert datum == {**original, "zero": "chart_datum", "zero_name": zero_name, "chart_datum": "cd",
                     "named": {"cd": 0.0}, "basis": {"cd": published(source_name)}}
    assert original == {"msl_offset_m": 1.25}
    assert datum_build_errors(datum) == []


def test_unconfirmed_refmar_header_is_not_a_published_chart_zero():
    doc = brest_header(record_id="bourcefranc_le_chapus-155-fra-refmar", site_code="155")
    original = {"msl_offset_m": 1.25}
    assert _published_stated_zero(doc, original) == original


GOOD = {"zero": "chart_datum", "chart_datum": "cd", "named": {"cd": 0.0, "msl": 1.0},
        "basis": {"cd": published("CD"), "msl": published("MSL", epoch=period("1996-01-01", "2014-12-31"))}}


def test_build_checks_pass():
    assert datum_build_errors(GOOD) == []
    assert datum_build_errors({"msl_offset_m": 1.0}) == []


def test_check_8_chart_datum_names_a_key_of_named():
    assert [e for e in datum_build_errors({**GOOD, "chart_datum": "lat"}) if "check 8" in e]


def test_check_9_same_keys():
    bad = {**GOOD, "named": {**GOOD["named"], "lat": 0.1}}
    errs = datum_build_errors(bad)
    assert len(errs) == 1 and "check 9" in errs[0] and "['lat']" in errs[0]
    bad = {**GOOD, "basis": {**GOOD["basis"], "hat": published("HAT")}}
    assert [e for e in datum_build_errors(bad) if "check 9" in e]


@pytest.mark.parametrize("ep", [{"start": "2021-02-30", "end": "2021-03-01"},
                                {"start": "2015-01-01", "end": "2014-12-31"}])
def test_check_13_real_dates_in_order(ep):
    bad = {**GOOD, "basis": {**GOOD["basis"], "msl": {**published("MSL"), "epoch": ep}}}
    assert [e for e in datum_build_errors(bad) if "check 13" in e]
