"""FES private-cache validation and bounded station-point extraction."""
import copy
import json
import math
import os
from pathlib import Path

import pytest

from otc_pipeline.adapter import sha256_file
from otc_pipeline.adapters.fes2022 import Fes2022Adapter, OCEAN_MANIFEST_NAME, load_manifest, mapped_name
from otc_pipeline.constituent_speeds import TableSpeeds


def test_complex_interpolation_wrap_and_missing_corners():
    from otc_pipeline.adapters.fes2022 import interpolate_complex
    amplitude, phase, used = interpolate_complex([(1.0, 359.0), (1.0, 1.0), None, None], [0.25] * 4)
    assert amplitude == pytest.approx(math.cos(math.radians(1)))
    assert min(phase, 360 - phase) < 1e-10
    assert used == 2
    assert interpolate_complex([None] * 4, [0.25] * 4) is None
    assert interpolate_complex([(0.0, 20.0)], [1.0]) == (0.0, 0.0, 1)


def test_actual_m2_three_frozen_station_points(tmp_path):
    from otc_pipeline.adapters.fes2022 import CONTRACT_STATIONS, sample_grid
    source = os.environ.get("OTC_FES_REAL_M2")
    if not source:
        pytest.skip("set OTC_FES_REAL_M2 to the complete private M2 grid for native proof")
    assert sha256_file(Path(source)) == "8433373716a994bf1203d525ee362c227202f07a67cef62e20a53b662a202788"
    got = sample_grid(Path(source), CONTRACT_STATIONS)
    print(json.dumps(got, sort_keys=True))
    assert set(got) == set(CONTRACT_STATIONS)
    assert got["8443970"]["status"] == "unavailable"
    assert all(cell["amplitude_cm"] is None for cell in got["8443970"]["cells"])
    for sid in ("9447130", "9414290"):
        result = got[sid]
        assert result["status"] == "available", result
        assert result["amplitude_m"] > 0
        assert 0 <= result["phase_deg"] < 360
        assert 1 <= result["corners_used"] <= 4
        assert len(result["cells"]) == 4


@pytest.fixture
def native_m2_attachment(tmp_path):
    from otc_pipeline.adapters.fes2022 import extract, station_roster
    from otc_pipeline.adapter import FetchResult, InputFile
    source = os.environ.get("OTC_FES_REAL_M2")
    stations = os.environ.get("OTC_FES_NATIVE_STATIONS")
    tcd = os.environ.get("OTC_FES_TCD")
    if not all((source, stations, tcd)):
        pytest.skip("requires actual FES M2, frozen station slice and TCD proof inputs")
    metadata = json.loads((Path(stations).parent / "native-release-meta.json").read_text())
    release = {**{key: metadata[key] for key in ("constituents", "conventions", "licences", "release", "astro_tables", "format_version")},
               "stations": json.loads(Path(stations).read_text())}
    root = Path(source).parents[1]
    acquisition = json.loads((Path(stations).parent.parent / "acquisition/files/ocean_tide_20241025_m2_fes2022.nc.xz.json").read_text())
    file = InputFile(Path(source).relative_to(root).as_posix(), sha256_file(Path(source)),
                     Path(source).stat().st_size, acquisition["url"], acquisition["downloaded_at"])
    # An explicitly M2-only diagnostic, not a purported complete FES release.
    manifest = {"source_version": "FES2022b", "files": [file.as_dict()]}
    path = tmp_path / "m2-diagnostic-manifest.json"
    path.write_text(json.dumps(manifest))
    speeds = TableSpeeds.from_tcd_dump(Path(tcd))
    roster = station_roster(release)
    adapter = Fes2022Adapter(stations=roster, speeds=speeds)
    items = extract(root, manifest, sha256_file(path), (file,), roster, speeds, derived_dir=tmp_path / "derived")
    fetched = FetchResult("fes2022", "FES2022b", root, (file,), items)
    return release, adapter, fetched


def test_actual_m2_available_points_and_retained_masked_points(native_m2_attachment):
    _, _, fetched = native_m2_attachment
    print(json.dumps(dict(fetched.items), sort_keys=True))
    assert not fetched.items["OTC-e813b80c"]["constituents"]
    assert not fetched.items["OTC-db860799"]["constituents"]
    for sid in ("OTC-bb22c37b", "OTC-ee4593cd", "OTC-35cdc643"):
        assert len(fetched.items[sid]["constituents"]) == 1
        assert fetched.items[sid]["constituents"][0]["amplitude_m"] > 0


def test_actual_m2_additive_attachment_preserves_official_sets(native_m2_attachment):
    import copy
    from otc_pipeline.adapters.fes2022 import attach
    from otc_pipeline.canary import canary
    release, adapter, fetched = native_m2_attachment
    before = copy.deepcopy(release["stations"])
    failed = canary("fes2022", list(adapter.sets(fetched)), [])
    report = attach(release, fetched, build_commit="abcdef0", table_id=release["astro_tables"][0]["astro_table_id"],
                    canary_result=failed, adapter=adapter)
    assert report["sets_added"] == 3
    assert set(report["unavailable"]) == {"OTC-e813b80c", "OTC-db860799"}
    assert report["canary"]["passed"] is False
    for old, new in zip(before, release["stations"]):
        assert new["recommended_set_id"] == old["recommended_set_id"]
        assert {cs["set_id"]: cs for cs in new["constant_sets"] if cs["source"] != "fes2022"} == {
            cs["set_id"]: cs for cs in old["constant_sets"]}
        for cs in new["constant_sets"]:
            if cs["source"] == "fes2022":
                assert "datum" not in cs
                assert cs["fallback"]["reason"] == "reliability_withheld"
        assert new["source_records"][:-1] == old["source_records"]


def test_actual_m2_model_only_recommendations(native_m2_attachment):
    from otc_pipeline.adapters.fes2022 import attach
    from otc_pipeline.canary import canary
    release, adapter, fetched = native_m2_attachment
    # Controlled removal of all competitors in this test only; production preserves them.
    for station in release["stations"]:
        station["constant_sets"] = []
        station["recommended_set_id"] = None
        station.pop("recommended_is_fallback", None)
        station["source_records"] = []
    result = canary("fes2022", list(adapter.sets(fetched)), [])
    attach(release, fetched, build_commit="abcdef0", table_id=release["astro_tables"][0]["astro_table_id"],
           canary_result=result, adapter=adapter)
    for station in release["stations"]:
        if fetched.items[station["station_id"]]["constituents"]:
            assert station["recommended_set_id"] == station["station_id"] + "/fes2022"
            assert station["recommended_is_fallback"] is True
        else:
            assert station["recommended_set_id"] is None


def test_actual_m2_attachment_has_valid_release_contract(native_m2_attachment):
    from otc_pipeline.adapters.fes2022 import attach
    from otc_pipeline.canary import canary
    from otc_pipeline.release_writer import validate_release
    release, adapter, fetched = native_m2_attachment
    attach(release, fetched, build_commit="abcdef0", table_id=release["astro_tables"][0]["astro_table_id"],
           canary_result=canary("fes2022", list(adapter.sets(fetched)), []), adapter=adapter)
    assert validate_release(release) == []
    if output := os.environ.get("OTC_FES_DIAGNOSTIC_OUT"):
        from otc_pipeline.release_writer import write_release
        write_release(release, Path(output), datestamp=release["release"]["datestamp"],
                      app_export=None, congen_run=None)


def test_actual_fes_preserves_existing_fallback_recommendations():
    from otc_pipeline.adapters.fes2022 import attach, station_roster
    from otc_pipeline.canary import canary
    station_path = os.environ.get("OTC_FES_PRESERVATION_STATIONS")
    cache = os.environ.get("OTC_FES_COMPLETE_CACHE")
    if not station_path or not cache:
        pytest.skip("requires actual affected baseline stations and complete FES ocean cache")
    stations = json.loads(Path(station_path).read_text())
    metadata = json.loads((Path(station_path).parent / "native-release-meta.json").read_text())
    release = {**metadata, "stations": copy.deepcopy(stations)}
    adapter = Fes2022Adapter(stations=station_roster(release),
                            speeds=TableSpeeds.from_tcd_dump(Path(os.environ["OTC_FES_TCD"])),
                            manifest_sha256=os.environ["OTC_FES_MANIFEST_SHA256"])
    fetched = adapter.fetch(Path(cache))
    result = canary("fes2022", list(adapter.sets(fetched)), [])
    attach(release, fetched, build_commit="abcdef0", table_id=release["astro_tables"][0]["astro_table_id"],
           canary_result=result, adapter=adapter)
    outcomes = []
    for before, after in zip(stations, release["stations"]):
        old_sets = {cs["set_id"]: cs for cs in before["constant_sets"]}
        retained = {cs["set_id"]: cs for cs in after["constant_sets"] if cs["source"] != "fes2022"}
        model = next(cs for cs in after["constant_sets"] if cs["source"] == "fes2022")
        expected = before["recommended_set_id"] or model["set_id"]
        outcomes.append({"station_id": before["station_id"], "before": before["recommended_set_id"],
                         "after": after["recommended_set_id"], "expected": expected,
                         "old_values_unchanged": retained == old_sets,
                         "model_coefficients": len(model["constituents"]),
                         "model_fallback": model["fallback"]["reason"]})
        assert after["recommended_is_fallback"] is True
    print(json.dumps(outcomes, sort_keys=True))
    assert all(row["old_values_unchanged"] and row["model_coefficients"] > 0
               and row["model_fallback"] == "reliability_withheld" for row in outcomes)
    assert all(row["after"] == row["expected"] for row in outcomes)


def test_complete_native_cache_contract_and_all_point_results(tmp_path):
    from otc_pipeline.adapter import check_contract
    from otc_pipeline.adapters.fes2022 import NATIVE_NAMES, OCEAN_DIRECTORY, station_roster
    root = os.environ.get("OTC_FES_COMPLETE_CACHE")
    if not root:
        pytest.skip("requires all 34 acquired FES ocean files and pinned ocean manifest")
    stations = json.loads(Path(os.environ["OTC_FES_NATIVE_STATIONS"]).read_text())
    adapter = Fes2022Adapter(stations=station_roster({"stations": stations}),
                            speeds=TableSpeeds.from_tcd_dump(Path(os.environ["OTC_FES_TCD"])),
                            manifest_sha256=os.environ["OTC_FES_MANIFEST_SHA256"])
    fetched = adapter.fetch(Path(root))
    assert check_contract(adapter, fetched) == []
    assert {f"{OCEAN_DIRECTORY}/{name}_fes2022.nc" for name in NATIVE_NAMES} <= {file.path for file in fetched.files}
    assert set(fetched.items) == {station["station_id"] for station in stations}
    for item in fetched.items.values():
        assert {row["native_name"] for row in item["native"]} == set(NATIVE_NAMES)
    for sid in ("OTC-bb22c37b", "OTC-ee4593cd", "OTC-35cdc643"):
        assert {row["name"] for row in fetched.items[sid]["constituents"]} >= {"M2", "S2", "K1", "O1"}
        unsupported = [row for row in fetched.items[sid]["native"] if row["native_name"] in {"msqm", "mtm"}]
        assert len(unsupported) == 2
        assert all(row["amplitude_m"] >= 0 for row in unsupported)
    for source_set in adapter.sets(fetched):
        assert {row["name"] for row in source_set.constant_set["dropped_constituents"]} == {"MSQM", "MTM"}


def test_registry_constructor_does_not_read_private_files():
    adapter = Fes2022Adapter()
    assert adapter.source == "fes2022"
    assert adapter.source_type == "model"
    assert adapter.contract_sample == ("8443970", "9447130", "9414290")


def test_incomplete_ocean_inventory_is_rejected(tmp_path):
    member = tmp_path / "ocean_tide_20241025" / "m2_fes2022.nc"
    member.parent.mkdir()
    member.write_bytes(b"inventory must be checked before sampling")
    (tmp_path / OCEAN_MANIFEST_NAME).write_text(json.dumps({"source_version": "FES2022b", "files": [{
        "path": "ocean_tide_20241025/m2_fes2022.nc", "sha256": sha256_file(member),
        "size": member.stat().st_size, "source_url": "https://example.test/m2.nc.xz",
        "fetched_at": "2026-10-17T00:00:00Z"}]}))
    with pytest.raises(ValueError, match="33 required ocean files missing"):
        Fes2022Adapter().fetch(tmp_path)


def test_manifest_pin_and_each_actual_file_are_checked(tmp_path):
    member = tmp_path / "ocean_tide_20241025" / "m2_fes2022.nc"
    member.parent.mkdir()
    member.write_bytes(b"not a grid: validation must precede extraction")
    manifest = tmp_path / OCEAN_MANIFEST_NAME
    manifest.write_text(json.dumps({"source_version": "FES2022b", "files": [{
        "path": "ocean_tide_20241025/m2_fes2022.nc", "sha256": sha256_file(member),
        "size": member.stat().st_size, "source_url": "https://example.test/m2.nc.xz",
        "fetched_at": "2026-10-17T00:00:00Z"}]}))
    pin = sha256_file(manifest)
    with pytest.raises(ValueError, match="manifest SHA-256"):
        load_manifest(tmp_path, "0" * 64)
    assert load_manifest(tmp_path, pin)[0]["source_version"] == "FES2022b"
    member.write_bytes(b"changed")
    with pytest.raises(ValueError, match="m2_fes2022.nc"):
        load_manifest(tmp_path, pin)


def test_manifest_cannot_escape_private_root(tmp_path):
    (tmp_path / OCEAN_MANIFEST_NAME).write_text(json.dumps({"source_version": "FES2022b", "files": [{
        "path": "../secret", "sha256": "0" * 64, "size": 1,
        "source_url": "https://example.test/m2", "fetched_at": "2026-10-17T00:00:00Z"}]}))
    with pytest.raises(ValueError, match="inside"):
        load_manifest(tmp_path)


def test_native_aliases_never_substitute_unsupported_harmonics():
    speeds = TableSpeeds({"LDA2": 29.4556253, "M2": 28.9841042, "MSF": 1.0158958})
    assert mapped_name("lambda2", speeds) == "LAM2"
    assert mapped_name("m2", speeds) == "M2"
    assert mapped_name("msqm", speeds) is None
    assert mapped_name("mtm", speeds) is None


def test_unknown_station_is_rejected_before_reading_grids(tmp_path):
    with pytest.raises(ValueError, match="unknown FES station"):
        Fes2022Adapter().fetch(tmp_path, only=["made-up"])


@pytest.fixture
def retained_fes_astro():
    root = os.environ.get("OTC_FES_ASTRO_PROOF")
    if not root:
        pytest.skip("requires retained actual FES extraction and authenticated astronomy runs")
    from otc_pipeline.astro_tables import load_congen_run

    root = Path(root)
    release = json.loads((root / "baseline.meta.json").read_text())
    release["stations"] = list(json.loads((root / "actual-three-stations.json").read_text()).values())
    extraction = {"algorithm": "complex-bilinear-valid-corners-ocean-v1",
                  "manifest_sha256": "6fe6a5233b4375065daea5a72ce134e91be4f072cd629533ef8ff89bab8ecadb",
                  "items": json.loads((root / "actual-three-extractions.json").read_text())}
    return (release, extraction, load_congen_run(Path(os.environ["OTC_CONGEN_RUN"])),
            load_congen_run(root / "astro-fes", fes=True))


def test_cached_fes_extra_terms_materialize_actual_three_stations(retained_fes_astro):
    from otc_pipeline.adapters.fes2022 import extend_cached_astronomy
    from otc_pipeline.astro_tables import FES_TABLE_ID

    release, extraction, primary, extended = retained_fes_astro
    before = copy.deepcopy(release)
    report = extend_cached_astronomy(release, extraction, primary=primary, extended=extended)
    assert report == {"sets_extended": 3, "coefficients_added": 6}
    for prior, after in zip(before["stations"], release["stations"]):
        old_fes = next(cs for cs in prior["constant_sets"] if cs["source"] == "fes2022")
        fes = next(cs for cs in after["constant_sets"] if cs["source"] == "fes2022")
        added = [row for row in fes["constituents"] if row["name"] in {"MSQM", "MTM"}]
        assert len(added) == 2
        raw = {row["native_name"].upper(): row for row in extraction["items"][after["station_id"]]["native"]}
        for row in added:
            assert (row["amplitude_m"], row["phase_deg"]) == (raw[row["name"]]["amplitude_m"], raw[row["name"]]["phase_deg"])
        restored = copy.deepcopy(fes)
        restored["constituents"] = [row for row in restored["constituents"] if row["name"] not in {"MSQM", "MTM"}]
        restored["dropped_constituents"] = old_fes["dropped_constituents"]
        assert restored == old_fes
        assert after["recommended_set_id"] == prior["recommended_set_id"]
    convention = next(row for row in release["conventions"] if row["convention_id"] == "fes2022b-ocean-greenwich")
    assert convention["astro_table_id"] == FES_TABLE_ID
    assert release["astro_tables"][0] == before["astro_tables"][0]


@pytest.mark.parametrize("corruption", ["amplitude", "phase", "input_hash", "missing_station"])
def test_cached_fes_extra_terms_reject_changed_inputs_before_mutation(retained_fes_astro, corruption):
    from otc_pipeline.adapters.fes2022 import extend_cached_astronomy

    release, extraction, primary, extended = retained_fes_astro
    before = copy.deepcopy(release)
    sid = release["stations"][0]["station_id"]
    item = extraction["items"][sid]
    if corruption in {"amplitude", "phase"}:
        item["native"][0][f"{corruption}_{'m' if corruption == 'amplitude' else 'deg'}"] += 1
    elif corruption == "input_hash":
        item["input_sha256"][0] = "0" * 64
    else:
        del extraction["items"][sid]
    with pytest.raises(ValueError):
        extend_cached_astronomy(release, extraction, primary=primary, extended=extended)
    assert release == before


def test_cached_fes_extra_terms_writer_requires_every_authenticated_table(retained_fes_astro, tmp_path):
    from otc_pipeline.adapters.fes2022 import extend_cached_astronomy
    from otc_pipeline.astro_tables import FES_TABLE_ID, TABLE_ID
    from otc_pipeline.release_writer import write_release

    release, extraction, primary, extended = retained_fes_astro
    extend_cached_astronomy(release, extraction, primary=primary, extended=extended)
    options = {"datestamp": release["release"]["datestamp"], "app_export": None}
    with pytest.raises(ValueError, match="unbound"):
        write_release(release, tmp_path / "bad", congen_run={TABLE_ID: primary}, **options)
    with pytest.raises(ValueError, match="astro tables"):
        write_release(release, tmp_path / "wrong", congen_run={TABLE_ID: primary, FES_TABLE_ID: primary}, **options)
    files = write_release(release, tmp_path / "good", congen_run={TABLE_ID: primary, FES_TABLE_ID: extended}, **options)
    assert files


@pytest.mark.parametrize("target", ["ticon_set", "ticon_definition", "old_table", "unknown_binding"])
def test_cached_fes_extra_terms_never_admit_unsupported_ticon(retained_fes_astro, target):
    from otc_pipeline.adapters.fes2022 import extend_cached_astronomy
    from otc_pipeline.astro_tables import FES_TABLE_ID
    from otc_pipeline.release_writer import validate_release

    release, extraction, primary, extended = retained_fes_astro
    extend_cached_astronomy(release, extraction, primary=primary, extended=extended)
    fes_table = next(row for row in release["astro_tables"] if row["astro_table_id"] == FES_TABLE_ID)
    if target == "ticon_set":
        station = release["stations"][1]
        ticon = next(cs for cs in station["constant_sets"] if cs["source"] == "ticon-4")
        fes = next(cs for cs in station["constant_sets"] if cs["source"] == "fes2022")
        ticon["constituents"].append(copy.deepcopy(next(row for row in fes["constituents"] if row["name"] == "MTM")))
    elif target == "ticon_definition":
        release["constituents"].append({"name": "MTM", "constituent_table_version": "ticon-4", "speed_deg_per_hour": 1.6424078})
    elif target == "old_table":
        release["astro_tables"][0]["constituents"]["MTM"] = fes_table["constituents"]["MTM"]
    else:
        fes_table["tables_sha256"] = "0" * 64
    assert any("unsupported original rows" in error for error in validate_release(release))
