"""Direct consumer checks for staging official computed datums without admission."""
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
from statistics import stdev
import sys

import pytest
from jsonschema import Draft202012Validator

from otc_pipeline import build_otc
from otc_pipeline.lat_hat import EPOCH, START, END, computation_input_sha256, datum_draft
from otc_pipeline import lat_hat
from otc_pipeline.observed_datums import produce as observed_produce
from otc_pipeline.release_writer import pinned_schema
from test_datums import native_observed_chart_cases as native_observed_chart_cases
from test_datums import native_approximate_chart_cases as native_approximate_chart_cases
from test_datums import native_computed_chart_cases as native_computed_chart_cases
from test_datums import native_nld_computed_chart_cases as native_nld_computed_chart_cases
from test_datums import native_uhslc_fd_chart_cases as native_uhslc_fd_chart_cases
from test_datums import native_uhslc_fd_chart_evidence as native_uhslc_fd_chart_evidence


def _expected_computed_reference_basis(original):
    expected = copy.deepcopy(original)
    expected["fallback"]["basis"] += "; approximate chart-reference stand-in: existing computed LAT (nonofficial)"
    expected["fallback"]["otc_finding"] += (
        "; approximate chart-reference stand-in; agency chart-datum convention, equivalence and vertical tie are not established")
    return expected


@pytest.fixture
def native_computed_chart_composition(native_computed_chart_cases, tmp_path, request):
    if getattr(request, "param", None) in ("nld", "uhslc_fd"):
        fixture = "native_nld_computed_chart_cases" if request.param == "nld" else "native_uhslc_fd_chart_cases"
        ids = {set_id for _, set_id in request.getfixturevalue(fixture)}
    else:
        ids = {set_id for _, set_id in native_computed_chart_cases}
        ids.add("OTC-2ecb58d9/gesla-fit/aranmore_island_leabgarrow-ara-irl-mi_c")
        ids.add("OTC-000180a3/gesla-fit/idsl_panormos-ids-grc-cmems")
    root = Path("/Users/jpr5/.local/share/copilotkit/cr/otc-impl")
    source = root / "derived-chart-datum/postfix-candidate/native-build/OTC_20261011.jsonl"
    assert observed_produce.sha256(source) == "14a12b36365f0e057633bb8cdf266a50cc24a3d0d417524f29e35547d299e111"
    station_ids = {sid.split("/")[0] for sid in ids}
    with source.open() as stream:
        stations = [s for line in stream if (s := json.loads(line))["station_id"] in station_ids]
    assert len(stations) == len(station_ids)
    metadata = json.loads(source.with_suffix(".meta.json").read_text())
    release = {"stations": stations, **{k: metadata[k] for k in ("conventions", "astro_tables")}}
    postcomputed = copy.deepcopy(release)
    roster_root = root / "datum-lat-hat/missing-gesla"
    roster = json.loads((roster_root / "roster.json").read_text())
    originals = {}
    for row in roster["rows"]:
        if row["set_id"] in ids:
            path = Path(row["path"])
            assert observed_produce.sha256(path) == row["input_sha256"]
            originals[row["set_id"]] = json.loads(path.read_text())["constant_set"]
    assert set(originals) == ids
    for station in stations:
        for cs in station["constant_sets"]:
            if cs["set_id"] in ids:
                original = originals[cs["set_id"]]
                assert cs["constituents"] == original["constituents"]
                assert {k: v for k, v in cs["datum"].items() if k not in ("named", "basis", "chart_datum")} == original["datum"]
                cs["datum"] = copy.deepcopy(original["datum"])
    inputs = {
        "gesla_root": Path("/Users/jpr5/.local/share/opentideconstants/work/inputs/gesla/4.1"),
        "fit_run": root / "FIX-CONST/fit/fit-fc1",
        "tcd": root / "FIX-CONST/astro/otc-tcd-20251228-ma2mb2-r2/tcd-1900-2100.json",
        "source_release": source,
        "source_release_sha256": observed_produce.sha256(source),
    }
    paths = []
    for name, original, sha in (
            ("official", roster_root / "all-public-drafts.jsonl", "77006af6f3274eae6437651abc93440c3d2a9d7d6452c9a6f7fa50a82b21fa99"),
            ("observed", root / "datum-observed-fallback/review/fix-mean-clock/refresh/full/observed-drafts.jsonl",
             "cfb0299eaf9247cd9cb5bba1be294250c3bf439a5d0d1c8086b8a9534a093053")):
        assert observed_produce.sha256(original) == sha
        with original.open() as stream:
            lines = [line for line in stream if json.loads(line)["set_id"] in ids]
        assert len(lines) == len(ids)
        path = tmp_path / f"four-native-{name}-drafts.jsonl"
        path.write_text("".join(lines))
        paths.append((path, observed_produce.sha256(path)))
    return release, postcomputed, paths[0], paths[1], inputs, ids


@pytest.mark.parametrize("index", [None, 0, 1, 2, 3])
def test_native_computed_chart_composition(native_computed_chart_composition, tmp_path, index):
    release, postcomputed, official, observed, inputs, ids = native_computed_chart_composition
    if index is not None:
        selected = sorted(sid for sid in ids if sid.endswith("mi_c"))[index]
        lines = [line for line in observed[0].read_text().splitlines(keepends=True)
                 if json.loads(line)["set_id"] == selected]
        path = tmp_path / "single-native-observed-draft.jsonl"
        path.write_text("".join(lines))
        observed = (path, observed_produce.sha256(path))
    else:
        with pytest.raises(build_otc._ObservedCurrentInputMismatch, match="OTC-000180a3"):
            build_otc.stage_observed_datums(release, *observed, **inputs)
        print("REAL COMPOSITION initial Panormos current-binding mismatch requires computed preview")
    serial = copy.deepcopy(release)
    official_result, observed_result = build_otc.apply_datum_drafts(release, official=official, observed=observed,
        publish_official=True, publish_observed=True, observed_inputs=inputs)
    assert official_result["computed_basis_fallback_count"] == 10
    assert observed_result["observed_basis_fallback_count"] == (44 if index is None else 11)
    if index is None:
        build_otc.apply_official_datum_fallbacks(serial, *official)
        build_otc.apply_observed_datum_fallbacks(serial, *observed, **inputs)
        assert serial == release
        print("REAL COMPOSITION serial official→observed equals combined publication on all five native records")
    expected = copy.deepcopy(postcomputed)
    drafts = {row["set_id"]: row for row in map(json.loads, observed[0].read_text().splitlines())}
    for station in expected["stations"]:
        for cs in station["constant_sets"]:
            if cs["set_id"] in drafts:
                row = drafts[cs["set_id"]]
                if row["draft_datum"] is None:
                    continue
                for field in ("named", "basis"):
                    cs["datum"][field].update(row["draft_datum"][field])
    for station in release["stations"]:
        for cs in station["constant_sets"]:
            if cs["set_id"] not in ids or not cs["set_id"].endswith("mi_c"):
                continue
            print(f"REAL COMPUTED COMPOSITION {cs['set_id']}: chart={cs['datum'].get('chart_datum')!r}; "
                  f"observed_msl={cs['datum']['named'].get('msl')!r}; authenticated=True")
            assert cs["datum"].pop("chart_datum") == "lat"
            original = next(c for s in postcomputed["stations"] for c in s["constant_sets"]
                            if c["set_id"] == cs["set_id"])
            cs["datum"]["basis"]["lat"]["fallback"] = original["datum"]["basis"]["lat"]["fallback"]
    assert release == expected


@pytest.mark.parametrize("case_fixture", ["native_computed_chart_cases", "native_nld_computed_chart_cases", "native_uhslc_fd_chart_cases"])
@pytest.mark.parametrize("combined", [False, True])
def test_native_computed_chart_publication(request, case_fixture, tmp_path, combined):
    native_computed_chart_cases = request.getfixturevalue(case_fixture)
    release = {"stations": [copy.deepcopy(station) for station, _ in native_computed_chart_cases]}
    before = copy.deepcopy(release)
    empty = tmp_path / "no-new-computed-drafts.jsonl"
    empty.write_text("")
    sha = hashlib.sha256(empty.read_bytes()).hexdigest()
    staged = build_otc.stage_official_datums(release, empty, sha)
    assert release == before and "chart_datum_derivation" not in staged
    if combined:
        receipt, _ = build_otc.apply_datum_drafts(release, official=(empty, sha), publish_official=True)
    else:
        receipt = build_otc.apply_official_datum_fallbacks(release, empty, sha)
    assert receipt["computed_basis_fallback_count"] == 0
    assert receipt["normal_datum_gate_accepted"] is False
    actual = []
    for station, (_, set_id) in zip(release["stations"], native_computed_chart_cases, strict=True):
        cs = next(c for c in station["constant_sets"] if c["set_id"] == set_id)
        print(f"REAL COMPUTED PRODUCER {set_id}: chart={cs['datum'].get('chart_datum')!r}; "
              f"official-only publication; combined={combined}")
        actual.append(cs["datum"].get("chart_datum"))
    assert actual == ["lat"] * 3
    assert receipt["chart_datum_derivation"]["approximate_changed_sets"] == 3
    for station, (old_station, set_id) in zip(release["stations"], native_computed_chart_cases, strict=True):
        cs = next(c for c in station["constant_sets"] if c["set_id"] == set_id)
        old = next(c for c in old_station["constant_sets"] if c["set_id"] == set_id)
        assert cs["datum"].pop("chart_datum") == "lat"
        fallback = cs["datum"]["basis"]["lat"]["fallback"]
        assert "existing computed LAT (nonofficial)" in fallback["basis"]
        assert "agency chart-datum convention, equivalence and vertical tie are not established" in fallback["otc_finding"]
        fallback.update(old["datum"]["basis"]["lat"]["fallback"])
    assert release == before


@pytest.mark.parametrize("native_computed_chart_composition", ["nld", "uhslc_fd"], indirect=True)
def test_native_nld_computed_chart_composition(native_computed_chart_composition):
    release, postcomputed, official, observed, inputs, ids = native_computed_chart_composition
    before = copy.deepcopy(release)
    official_only = copy.deepcopy(release)
    official_receipt = build_otc.apply_official_datum_fallbacks(official_only, *official)
    assert official_receipt["computed_basis_fallback_count"] == 6
    assert official_receipt["chart_datum_derivation"]["approximate_changed_sets"] == 3
    serial = copy.deepcopy(official_only)
    serial_receipt = build_otc.apply_observed_datum_fallbacks(serial, *observed, **inputs)
    official_receipt, observed_receipt = build_otc.apply_datum_drafts(release, official=official, observed=observed,
        publish_official=True, publish_observed=True, observed_inputs=inputs)
    assert release == serial
    assert observed_receipt["observed_basis_fallback_count"] == serial_receipt["observed_basis_fallback_count"] == 33
    assert observed_receipt["normal_datum_gate_accepted"] is False
    expected = copy.deepcopy(postcomputed)
    drafts = {row["set_id"]: row for row in map(json.loads, observed[0].read_text().splitlines())}
    for station in expected["stations"]:
        for cs in station["constant_sets"]:
            if cs["set_id"] in ids:
                for field in ("named", "basis"):
                    cs["datum"][field].update(drafts[cs["set_id"]]["draft_datum"][field])
    for output, baseline in ((official_only, postcomputed), (release, expected)):
        for station in output["stations"]:
            for cs in station["constant_sets"]:
                if cs["set_id"] not in ids:
                    continue
                old = next(c for s in baseline["stations"] for c in s["constant_sets"]
                           if c["set_id"] == cs["set_id"])
                print(f"REAL COMPUTED SOURCE COMPOSITION {cs['set_id']}: lat={cs['datum']['named']['lat']!r}; "
                      f"chart={cs['datum'].get('chart_datum')!r}; observed={'msl' in cs['datum']['named']}")
                assert cs["datum"].pop("chart_datum") == "lat"
                fallback = cs["datum"]["basis"]["lat"]["fallback"]
                assert "existing computed LAT (nonofficial)" in fallback["basis"]
                assert "agency chart-datum convention, equivalence and vertical tie are not established" in fallback["otc_finding"]
                fallback.update(old["datum"]["basis"]["lat"]["fallback"])
        assert output == baseline
    assert before != postcomputed  # Exact native pre-computed input was authenticated.


@pytest.mark.parametrize("native_computed_chart_composition", ["nld", "uhslc_fd"], indirect=True)
@pytest.mark.parametrize("defect", ["number", "provenance", "fallback_basis", "fallback_finding", "pointer", "metadata"])
def test_native_nld_finalized_chart_rejects_tampered_binding(native_computed_chart_composition, defect):
    release, _, official, observed, inputs, ids = native_computed_chart_composition
    build_otc.apply_official_datum_fallbacks(release, *official)
    cs = next(c for s in release["stations"] for c in s["constant_sets"] if c["set_id"] in ids)
    assert cs["datum"]["chart_datum"] == "lat"
    if defect == "number":
        cs["datum"]["named"]["lat"] += 0.01
    elif defect == "provenance":
        cs["provenance"]["tcd_sha256"] = "0" * 64
    elif defect in ("fallback_basis", "fallback_finding"):
        key = "basis" if defect == "fallback_basis" else "otc_finding"
        cs["datum"]["basis"]["lat"]["fallback"][key] += "; tampered"
    elif defect == "pointer":
        cs["datum"]["chart_datum"] = "hat"
    else:
        cs["datum"]["zero_name"] = "invented frame"
    before = copy.deepcopy(release)
    with pytest.raises(build_otc._ObservedCurrentInputMismatch, match=cs["set_id"]):
        build_otc.apply_observed_datum_fallbacks(release, *observed, **inputs)
    assert release == before


@pytest.mark.parametrize("defect", ["number", "provenance", "fallback_basis", "fallback_finding", "pointer", "metadata"])
def test_native_finalized_computed_chart_rejects_tampered_binding(native_computed_chart_composition, defect):
    release, _, official, observed, inputs, _ = native_computed_chart_composition
    build_otc.apply_official_datum_fallbacks(release, *official)
    station = next(s for s in release["stations"] if s["station_id"] == "OTC-8fc5a96e")
    cs = next(c for c in station["constant_sets"] if c["source_record_id"] == "roonagh_pier-roo-irl-mi_c")
    assert cs["datum"]["chart_datum"] == "lat"
    if defect == "number":
        cs["datum"]["named"]["lat"] += 0.01
    elif defect == "provenance":
        cs["provenance"]["tcd_sha256"] = "0" * 64
    elif defect in ("fallback_basis", "fallback_finding"):
        key = "basis" if defect == "fallback_basis" else "otc_finding"
        cs["datum"]["basis"]["lat"]["fallback"][key] += "; tampered"
    elif defect == "pointer":
        cs["datum"]["chart_datum"] = "hat"
    else:
        cs["datum"]["zero_name"] = "invented frame"
    before = copy.deepcopy(release)
    with pytest.raises(build_otc._ObservedCurrentInputMismatch, match="OTC-8fc5a96e"):
        build_otc.apply_observed_datum_fallbacks(release, *observed, **inputs)
    assert release == before


@pytest.fixture
def native_approximate_chart_stage(native_approximate_chart_cases, tmp_path):
    facts, stations = native_approximate_chart_cases
    release = {"stations": [copy.deepcopy(stations[row["station_id"]]) for row in facts["witnesses"]]}
    expected = copy.deepcopy(release)
    source = Path("/Users/jpr5/.local/share/copilotkit/cr/otc-impl/datum-observed-fallback/review/fix-mean-clock/refresh/full/observed-drafts.jsonl")
    with source.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == "cfb0299eaf9247cd9cb5bba1be294250c3bf439a5d0d1c8086b8a9534a093053"
    ids = {row["set_id"] for row in facts["witnesses"]}
    rows = {}
    with source.open() as stream:
        for line in stream:
            row = json.loads(line)
            if row["set_id"] in ids:
                rows[row["set_id"]] = row
    assert set(rows) == ids
    for station in release["stations"]:
        for cs in station["constant_sets"]:
            if cs["set_id"] in rows:
                row = rows[cs["set_id"]]
                for key in row["observed_keys"]:
                    for field in ("named", "basis"):
                        assert cs["datum"][field].pop(key) == row["draft_datum"][field][key]
    path = tmp_path / "nine-native-observed-drafts.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows.values()))
    inputs = {
        "gesla_root": Path("/Users/jpr5/.local/share/opentideconstants/work/inputs/gesla/4.1"),
        "fit_run": Path("/Users/jpr5/.local/share/copilotkit/cr/otc-impl/FIX-CONST/fit/fit-fc1"),
        "tcd": Path("/Users/jpr5/.local/share/copilotkit/cr/otc-impl/FIX-CONST/astro/otc-tcd-20251228-ma2mb2-r2/tcd-1900-2100.json"),
        "source_release": Path("/Users/jpr5/.local/share/copilotkit/cr/otc-impl/derived-chart-datum/postfix-candidate/native-build/OTC_20261011.jsonl"),
        "source_release_sha256": "14a12b36365f0e057633bb8cdf266a50cc24a3d0d417524f29e35547d299e111"}
    return release, expected, path, hashlib.sha256(path.read_bytes()).hexdigest(), inputs, facts


@pytest.mark.parametrize("combined", [False, True])
def test_native_approximate_chart_publication(native_approximate_chart_stage, tmp_path, combined):
    release, expected, path, digest, inputs, facts = native_approximate_chart_stage
    before = copy.deepcopy(release)
    staged = build_otc.stage_observed_datums(release, path, digest, **inputs)
    assert release == before and "chart_datum_derivation" not in staged
    if combined:
        empty = tmp_path / "no-new-official-drafts.jsonl"
        empty.write_text("")
        _, result = build_otc.apply_datum_drafts(release,
            official=(empty, hashlib.sha256(empty.read_bytes()).hexdigest()), observed=(path, digest),
            publish_official=True, publish_observed=True, observed_inputs=inputs)
    else:
        result = build_otc.apply_observed_datum_fallbacks(release, path, digest, **inputs)
    for row in facts["witnesses"]:
        station = next(s for s in release["stations"] if s["station_id"] == row["station_id"])
        cs = next(c for c in station["constant_sets"] if c["set_id"] == row["set_id"])
        key = row["key"]
        print(f"REAL APPROXIMATE PRODUCER {row['station_id']}: {key}={cs['datum']['named'][key]!r}; "
              f"chart={cs['datum'].get('chart_datum')!r}; combined={combined}")
        assert cs["datum"].pop("chart_datum") == key
        fallback = cs["datum"]["basis"][key]["fallback"]
        assert "approximate chart-reference stand-in" in fallback["basis"]
        fallback.update(row["existing_basis"]["fallback"])
    assert release == expected
    assert result["chart_datum_derivation"]["approximate_changed_places"] == 9
    assert result["normal_datum_gate_accepted"] is False


@pytest.fixture
def native_observed_chart_stage(native_observed_chart_cases, tmp_path):
    facts, stations = native_observed_chart_cases
    release = {"stations": [copy.deepcopy(stations[row["station_id"]]) for row in facts["witnesses"]]}
    expected = copy.deepcopy(release)
    source = Path("/Users/jpr5/.local/share/copilotkit/cr/otc-impl/datum-observed-fallback/review/fix-mean-clock/refresh/full/observed-drafts.jsonl")
    with source.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == "cfb0299eaf9247cd9cb5bba1be294250c3bf439a5d0d1c8086b8a9534a093053"
    ids = {row["set_id"] for row in facts["witnesses"]}
    rows = {}
    with source.open() as stream:
        for line in stream:
            row = json.loads(line)
            if row["set_id"] in ids:
                rows[row["set_id"]] = row
            if len(rows) == 3:
                break
    assert set(rows) == ids
    for station in release["stations"]:
        for cs in station["constant_sets"]:
            if cs["set_id"] not in rows:
                continue
            row = rows[cs["set_id"]]
            for key in row["observed_keys"]:
                for field in ("named", "basis"):
                    assert cs["datum"][field].pop(key) == row["draft_datum"][field][key]
    path = tmp_path / "three-native-observed-drafts.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows.values()))
    inputs = {
        "gesla_root": Path("/Users/jpr5/.local/share/opentideconstants/work/inputs/gesla/4.1"),
        "fit_run": Path("/Users/jpr5/.local/share/copilotkit/cr/otc-impl/FIX-CONST/fit/fit-fc1"),
        "tcd": Path("/Users/jpr5/.local/share/copilotkit/cr/otc-impl/FIX-CONST/astro/otc-tcd-20251228-ma2mb2-r2/tcd-1900-2100.json"),
        "source_release": Path("/Users/jpr5/.local/share/copilotkit/cr/otc-impl/derived-chart-datum/postfix-candidate/native-build/OTC_20261011.jsonl"),
        "source_release_sha256": "14a12b36365f0e057633bb8cdf266a50cc24a3d0d417524f29e35547d299e111"}
    return release, expected, path, hashlib.sha256(path.read_bytes()).hexdigest(), inputs, facts


@pytest.mark.parametrize("combined", [False, True])
def test_native_observed_chart_publication_follows_authenticated_attachment(native_observed_chart_stage, tmp_path, combined):
    release, expected, path, digest, inputs, facts = native_observed_chart_stage
    before = copy.deepcopy(release)
    staged = build_otc.stage_observed_datums(release, path, digest, **inputs)
    assert release == before
    assert "chart_datum_derivation" not in staged
    if combined:
        empty = tmp_path / "no-new-official-drafts.jsonl"
        empty.write_text("")
        official, result = build_otc.apply_datum_drafts(release,
            official=(empty, hashlib.sha256(empty.read_bytes()).hexdigest()), observed=(path, digest),
            publish_official=True, publish_observed=True, observed_inputs=inputs)
        assert official["computed_basis_fallback_count"] == 0
    else:
        result = build_otc.apply_observed_datum_fallbacks(release, path, digest, **inputs)
    for row in facts["witnesses"]:
        station = next(s for s in release["stations"] if s["station_id"] == row["station_id"])
        cs = next(c for c in station["constant_sets"] if c["set_id"] == row["set_id"])
        print(f"REAL PRODUCER {row['station_id']}: mllw={cs['datum']['named']['mllw']!r}; chart={cs['datum'].get('chart_datum')!r}; combined={combined}")
        assert cs["datum"].get("chart_datum") == "mllw"
        del cs["datum"]["chart_datum"]
    assert release == expected
    assert result["chart_datum_derivation"]["changed_places"] == 3
    assert result["normal_datum_gate_accepted"] is False


@pytest.fixture
def native_observed_attachment():
    root = os.environ.get("OTC_OBSERVED_ATTACHMENT_PROOF")
    if not root:
        pytest.skip("requires the pinned original observed reduction")
    root = Path(root)
    stations = [json.loads(line) for line in (root / "reduction/pilot-release.jsonl").read_text().splitlines()]
    path = root / "reduction/pilot/observed-drafts.jsonl"
    return {"stations": stations}, path, hashlib.sha256(path.read_bytes()).hexdigest(), {
        "gesla_root": Path("/Users/jpr5/.local/share/opentideconstants/work/inputs/gesla/4.1"),
        "fit_run": Path("/Users/jpr5/.local/share/copilotkit/cr/otc-impl/FIX-CONST/fit/fit-fc1"),
        "tcd": Path("/Users/jpr5/.local/share/copilotkit/cr/otc-impl/FIX-CONST/astro/otc-tcd-20251228-ma2mb2-r2/tcd-1900-2100.json"),
        "source_release": root / "reduction/pilot-release.jsonl",
        "source_release_sha256": hashlib.sha256((root / "reduction/pilot-release.jsonl").read_bytes()).hexdigest(),
    }


def test_native_observed_attachment_three_original_value_flips(native_observed_attachment, tmp_path):
    release, path, digest, inputs = native_observed_attachment
    before = copy.deepcopy(release)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    available = [row for row in rows if row["draft_datum"]]
    assert len(available) == 3
    for row in available:
        cs = next(cs for st in release["stations"] for cs in st["constant_sets"] if cs["set_id"] == row["set_id"])
        missing = sorted(set(row["observed_keys"]) - cs["datum"]["named"].keys())
        print(f"REAL BEFORE {row['set_id']}: missing={missing}; binding={row['input_binding']}")
        assert missing == row["observed_keys"]
    apply = getattr(build_otc, "apply_observed_datum_fallbacks", None)
    assert callable(apply), "real producer cannot attach the three frozen original-bound observed reductions"
    staged = build_otc.stage_observed_datums(release, path, digest, **inputs)
    assert release == before
    result = apply(release, path, digest, **inputs)
    assert result["observed_basis_fallback_count"] == sum(len(row["observed_keys"]) for row in available)
    assert staged["unavailable_set_count"] == 2
    for old_st, new_st in zip(before["stations"], release["stations"]):
        assert {k: v for k, v in old_st.items() if k != "constant_sets"} == {k: v for k, v in new_st.items() if k != "constant_sets"}
        for old, new in zip(old_st["constant_sets"], new_st["constant_sets"]):
            row = next(r for r in rows if r["set_id"] == new["set_id"])
            if row["draft_datum"] is None:
                assert new == old
                continue
            assert {k: v for k, v in new.items() if k != "datum"} == {k: v for k, v in old.items() if k != "datum"}
            for key, value in old["datum"].items():
                if key not in ("named", "basis"):
                    assert new["datum"][key] == value
            for key in old["datum"]["named"]:
                assert new["datum"]["named"][key] == old["datum"]["named"][key]
                expected_basis = old["datum"]["basis"][key]
                if new["source_record_id"] == "werkendambuitentg-wer-nld-cmems" and key == "lat":
                    expected_basis = _expected_computed_reference_basis(expected_basis)
                    assert new["datum"]["chart_datum"] == "lat"
                assert new["datum"]["basis"][key] == expected_basis
            for key in row["observed_keys"]:
                assert new["datum"]["named"][key] == row["draft_datum"]["named"][key]
                assert new["datum"]["basis"][key] == row["draft_datum"]["basis"][key]
                basis = new["datum"]["basis"][key]
                assert basis["kind"] == "observed"
                assert basis["fallback"]["reason"] == "datum_gate_failed"
                assert "uncertainty_m" not in basis and "uncertainty_basis" not in basis
            print(f"REAL AFTER {new['set_id']}: {row['draft_datum']['named']}; prior values/set/ranking preserved")
    source_root = Path(build_otc.__file__).resolve().parents[4]
    assert Path(observed_produce.__file__).resolve().is_relative_to(source_root)
    canonical = json.loads((source_root / "src/schema/otc-1.0.schema.json").read_text())
    for schema in (canonical, pinned_schema()):
        validator = Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/station"})
        for station in release["stations"]:
            assert list(validator.iter_errors(station)) == []
    output = tmp_path / "attached-pilot-stations.jsonl"
    output.write_text("".join(json.dumps(station, sort_keys=True, allow_nan=False) + "\n" for station in release["stations"]))
    print(f"COMBINED CONTRACT/IMPORT: 3 actual stations, canonical+pinned; output_sha256={hashlib.sha256(output.read_bytes()).hexdigest()}")


@pytest.mark.parametrize("field", ["constituents", "time_base", "audit", "fit_segment", "datum", "source_record_id", "provenance"])
def test_native_observed_attachment_rejects_changed_current_inputs(native_observed_attachment, field):
    release, path, digest, inputs = native_observed_attachment
    cs = next(cs for st in release["stations"] for cs in st["constant_sets"] if cs["source"] == "gesla-fit")
    if field == "constituents":
        cs[field][0]["amplitude_m"] += 0.01
    elif field == "time_base":
        cs[field]["published"] = "utc_mean_start60"
    elif field == "audit":
        cs["time_base"]["decided_build_commit"] = "0" * 40
    elif field == "fit_segment":
        cs[field] = {"start": "2021-01-01T00:00:00Z", "end": "2021-12-31T00:00:00Z"}
    elif field == "datum":
        cs[field]["msl_offset_m"] += 0.01
    elif field == "provenance":
        cs[field]["tcd_sha256"] = "0" * 64
    else:
        cs[field] = "foreign-original"
    before = copy.deepcopy(release)
    with pytest.raises(ValueError, match="binding"):
        build_otc.apply_observed_datum_fallbacks(release, path, digest, **inputs)
    assert release == before


@pytest.mark.parametrize("defect", ["path", "hash", "bytes"])
def test_native_observed_source_release_is_authenticated(native_observed_attachment, defect, tmp_path):
    release, path, digest, inputs = native_observed_attachment
    if defect == "hash":
        inputs["source_release_sha256"] = "0" * 64
    elif defect == "path":
        inputs["source_release"] = tmp_path / "missing.jsonl"
    else:
        changed = tmp_path / "changed.jsonl"
        changed.write_bytes(inputs["source_release"].read_bytes() + b"\n")
        inputs["source_release"] = changed
    with pytest.raises((ValueError, FileNotFoundError)):
        build_otc.stage_observed_datums(release, path, digest, **inputs)


def test_native_observed_regenerated_build_stamp_and_empty_stations(native_observed_attachment):
    release, path, digest, inputs = native_observed_attachment
    for station in release["stations"]:
        for cs in station["constant_sets"]:
            cs["provenance"]["build"]["build_commit"] = "1" * 40
    empty = {"station_id": "empty", "constant_sets": []}
    tombstone = {"station_id": "tombstone", "status": "merged"}
    release["stations"].extend([empty, tombstone])
    result = build_otc.apply_observed_datum_fallbacks(release, path, digest, **inputs)
    assert result["staged_set_count"] == 3
    assert release["stations"][-2:] == [empty, tombstone]


@pytest.mark.parametrize("defect", ["duplicate", "foreign", "missing_binding", "stale_fit", "conflict", "ticon", "extra_key", "missing_key"])
def test_native_observed_attachment_rejects_invalid_drafts(native_observed_attachment, defect, tmp_path):
    release, path, digest, inputs = native_observed_attachment
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    row = next(r for r in rows if r["draft_datum"])
    if defect == "duplicate":
        rows.append(copy.deepcopy(row))
    elif defect == "foreign":
        row["set_id"] = "foreign-set"
    elif defect == "missing_binding":
        del row["input_binding"]
    elif defect == "stale_fit":
        row["input_binding"]["fit_sha256"] = "0" * 64
    elif defect == "conflict":
        key = row["observed_keys"][0]
        cs = next(cs for st in release["stations"] for cs in st["constant_sets"] if cs["set_id"] == row["set_id"])
        cs["datum"]["named"][key] = 100.0
        cs["datum"]["basis"][key] = {"kind": "computed"}
    elif defect == "ticon":
        target = next(r for r in rows if r["draft_datum"] is None)
        target["draft_datum"] = copy.deepcopy(row["draft_datum"])
        target["observed_keys"] = list(row["observed_keys"])
    elif defect == "extra_key":
        row["draft_datum"]["chart_datum"] = "mhw"
    else:
        row["observed_keys"].pop()
    path = tmp_path / "invalid-observed.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    before = copy.deepcopy(release)
    with pytest.raises(ValueError):
        build_otc.apply_observed_datum_fallbacks(release, path, hashlib.sha256(path.read_bytes()).hexdigest(), **inputs)
    assert release == before


def observed_cli_arguments():
    return [value for name in ("fit-run", "audit-dir", "verdicts-dir", "calibration", "ticon-csv", "ticon-refs",
                               "congen-run", "agency-cache", "currents-canary-cache", "out", "datestamp", "created")
            for value in (f"--{name}", "unused")]


def test_fes_cli_requires_pinned_cache_pair():
    with pytest.raises(ValueError, match="--fes-cache and --fes-manifest-sha256"):
        build_otc.main([*observed_cli_arguments(), "--fes-cache", "private-input"])


def test_fes_cli_rejects_changed_manifest_before_build(tmp_path):
    (tmp_path / "MANIFEST.json").write_text("{}\n")
    with pytest.raises(ValueError, match="FES manifest SHA-256 mismatch"):
        build_otc.main([*observed_cli_arguments(), "--fes-cache", str(tmp_path),
                        "--fes-manifest-sha256", "0" * 64])


@pytest.mark.parametrize("missing", ["drafts", "drafts-sha256", "source-release", "source-release-sha256"])
def test_observed_cli_requires_authenticated_draft_and_source_pairs(missing):
    arguments = observed_cli_arguments()
    for name in ("drafts", "drafts-sha256", "source-release", "source-release-sha256"):
        if name != missing:
            arguments.extend([f"--observed-datum-{name}", "unused"])
    with pytest.raises(ValueError, match="both paths and SHA-256 pins"):
        build_otc.main(arguments)


def test_observed_cli_publication_requires_pinned_inputs():
    with pytest.raises(ValueError, match="pinned drafts and source release"):
        build_otc.main([*observed_cli_arguments(), "--publish-labelled-observed-datums"])


def test_observed_cli_accepts_all_publication_arguments_before_build(monkeypatch):
    def stop_before_build(arguments):
        assert arguments.observed_datum_source_release == Path("source.jsonl")
        assert arguments.publish_labelled_observed_datums
        raise RuntimeError("observed arguments accepted before build")

    monkeypatch.setattr(build_otc.gb, "inventory_from_arguments", stop_before_build)
    arguments = [*observed_cli_arguments(), "--observed-datum-drafts", "drafts.jsonl",
                 "--observed-datum-drafts-sha256", "0" * 64,
                 "--observed-datum-source-release", "source.jsonl",
                 "--observed-datum-source-release-sha256", "1" * 64,
                 "--publish-labelled-observed-datums"]
    with pytest.raises(RuntimeError, match="observed arguments accepted"):
        build_otc.main(arguments)


@pytest.fixture
def native_combined_datum_inputs(native_observed_attachment, tmp_path):
    release, observed_path, observed_sha, inputs = native_observed_attachment
    metadata_path = Path("/Users/jpr5/.local/share/copilotkit/cr/otc-impl/derived-chart-datum/postfix-candidate/native-build/OTC_20261011.meta.json")
    metadata = json.loads(metadata_path.read_text())
    release.update({key: metadata[key] for key in ("conventions", "astro_tables")})
    official_source = Path("/Users/jpr5/.local/share/copilotkit/cr/otc-impl/datum-lat-hat/missing-gesla/all-public-drafts.jsonl")
    assert hashlib.sha256(official_source.read_bytes()).hexdigest() == "77006af6f3274eae6437651abc93440c3d2a9d7d6452c9a6f7fa50a82b21fa99"
    ids = {cs["set_id"] for station in release["stations"] for cs in station["constant_sets"] if cs["source"] == "gesla-fit"}
    rows = [row for line in official_source.read_text().splitlines() if (row := json.loads(line)).get("set_id") in ids]
    assert len(rows) == 3
    official_path = tmp_path / "official-three.jsonl"
    official_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    official_sha = hashlib.sha256(official_path.read_bytes()).hexdigest()
    # NEW test fixture: recover real pre-computed datum metadata; never alter old
    # evidence or claim that its binding describes this newly serialized source.
    reduction_root = Path(os.environ["OTC_OBSERVED_ATTACHMENT_PROOF"]) / "reduction"
    assert observed_sha == "01ac08ab6ebb2a430323c2085636e485e7d9c085302b4f9454cfdde1d335b5ae"
    accepted = {row["set_id"]: row for row in map(json.loads, observed_path.read_text().splitlines())}
    report_path = reduction_root / "pilot/observed-reduction-report.json"
    assert observed_produce.sha256(report_path) == "ba9f0c31c164cf44cd104765306de41d50c61d948e56b53f83cfe6f1bc71f746"
    report = json.loads(report_path.read_text())
    details = {row["set_id"]: row for row in report["records"]}
    roster_path = official_source.with_name("roster.json")
    roster = json.loads(roster_path.read_text())
    payloads = {}
    for entry in roster["rows"]:
        if entry["set_id"] not in ids:
            continue
        payload_path = Path(entry["path"])
        assert hashlib.sha256(payload_path.read_bytes()).hexdigest() == entry["input_sha256"]
        payloads[entry["set_id"]] = json.loads(payload_path.read_text())
    for station in release["stations"]:
        for cs in station["constant_sets"]:
            if cs["set_id"] not in payloads:
                continue
            original = payloads[cs["set_id"]]["constant_set"]
            for key in ("constituents", "time_base", "record_span", "fit_segment", "record_issues", "source", "source_version"):
                assert cs.get(key) == original.get(key)
            provenances = [copy.deepcopy(item["provenance"]) for item in (cs, original)]
            for provenance in provenances:
                provenance.get("build", {}).pop("build_commit", None)
            assert provenances[0] == provenances[1]
            assert {k: v for k, v in cs["datum"].items() if k not in ("named", "basis", "chart_datum")} == original["datum"]
            cs["datum"] = copy.deepcopy(original["datum"])
    source_path = tmp_path / "new-pre-attachment-source.jsonl"
    source_path.write_text("".join(json.dumps(station, sort_keys=True) + "\n" for station in release["stations"]))
    inputs["source_release"] = source_path
    inputs["source_release_sha256"] = observed_produce.sha256(source_path)
    manifest = json.loads((inputs["gesla_root"] / "MANIFEST.json").read_text())
    members = {row["name"]: row for row in manifest["members"]}
    fresh = []
    for station in release["stations"]:
        for cs in station["constant_sets"]:
            old = accepted[cs["set_id"]]
            detail = details[cs["set_id"]]
            old_binding = old["input_binding"]
            common = {"release_sha256": inputs["source_release_sha256"],
                      "fit_manifest_sha256": observed_produce.sha256(inputs["fit_run"] / "timebase_manifest.json"),
                      "results_sha256": observed_produce.sha256(inputs["fit_run"] / "results.jsonl"),
                      "tcd_sha256": observed_produce.sha256(inputs["tcd"]),
                      "gesla_manifest_sha256": observed_produce.sha256(inputs["gesla_root"] / "MANIFEST.json"),
                      "timebase_sha256": old_binding["timebase_sha256"]}
            fit_path = inputs["fit_run"] / "fits" / f"{cs['source_record_id']}.json"
            binding = observed_produce.input_binding(station, cs, member=members.get(cs["source_record_id"]),
                fit_sha256=observed_produce.sha256(fit_path) if fit_path.is_file() else None, **common)
            assert {k: v for k, v in binding.items() if k not in ("release_sha256", "native_input_sha256")} == {
                k: v for k, v in old_binding.items() if k not in ("release_sha256", "native_input_sha256")}
            draft = None
            if old["draft_datum"]:
                used = {tuple(map(int, key.split("-"))): value for key, value in detail["used_monthly_inputs"].items()}
                values = {key.upper(): value for key, value in old["draft_datum"]["named"].items()}
                primary = detail["method"] == "first_reduction" and "own_record_limitation" not in detail
                draft, _ = observed_produce.draft_datum(cs, values, used, method=detail["method"],
                    control=detail["control"], primary=primary, gaps=bool(detail["filled_gaps"] or detail["unfilled_gaps"]))
                assert draft == old["draft_datum"]  # exact accepted measurements and scientific metadata
            fresh.append({"station_id": station["station_id"], "set_id": cs["set_id"],
                          "source_record_id": cs["source_record_id"], "input_binding": binding,
                          "draft_datum": draft, "observed_keys": sorted(draft["named"]) if draft else []})
    new_observed_path = tmp_path / "new-pre-attachment-observed.jsonl"
    new_observed_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in fresh))
    return release, official_path, official_sha, new_observed_path, observed_produce.sha256(new_observed_path), inputs


def test_native_combined_official_and_observed(native_combined_datum_inputs):
    release, official_path, official_sha, observed_path, observed_sha, inputs = native_combined_datum_inputs
    before = copy.deepcopy(release)
    official_staged = build_otc.stage_official_datums(release, official_path, official_sha)
    observed_staged = build_otc.stage_observed_datums(release, observed_path, observed_sha, **inputs)
    assert release == before
    assert official_staged["staged_level_count"] == 6 and observed_staged["staged_level_count"] == 33
    print(f"NEW AUTHENTICATED REAL FIXTURE: official6 observed33; source={inputs['source_release_sha256']}; drafts={observed_sha}")
    official_result, observed_result = build_otc.apply_datum_drafts(release,
        official=(official_path, official_sha), observed=(observed_path, observed_sha),
        publish_official=True, publish_observed=True, observed_inputs=inputs)
    assert official_result["computed_basis_fallback_count"] == 6
    assert observed_result["observed_basis_fallback_count"] == 33
    for old_station, station in zip(before["stations"], release["stations"]):
        assert {k: v for k, v in old_station.items() if k != "constant_sets"} == {k: v for k, v in station.items() if k != "constant_sets"}
        for old_cs, cs in zip(old_station["constant_sets"], station["constant_sets"]):
            assert {k: v for k, v in old_cs.items() if k != "datum"} == {k: v for k, v in cs.items() if k != "datum"}
            if cs["source"] != "gesla-fit":
                assert cs == old_cs
                continue
            official = next(row for row in official_staged["drafts"] if row["set_id"] == cs["set_id"])
            observed = next(row for row in observed_staged["drafts"] if row["set_id"] == cs["set_id"])
            for key, value in old_cs["datum"].items():
                assert cs["datum"][key] == value
            for stage in (official, observed):
                for field in ("named", "basis"):
                    for key, value in stage["datum"].get(field, {}).items():
                        if cs["source_record_id"] == "werkendambuitentg-wer-nld-cmems" and field == "basis" and key == "lat":
                            value = _expected_computed_reference_basis(value)
                            assert cs["datum"]["chart_datum"] == "lat"
                        assert cs["datum"][field][key] == value
            if cs["source_record_id"] == "penarth-pen-gbr-cco":
                assert cs["datum"]["chart_datum"] == "lat"
            print(f"REAL COMBINED GREEN {cs['set_id']}: computed={{{', '.join(str(cs['datum']['named'][key]) for key in ('lat', 'hat'))}}}; observed={{{', '.join(str(cs['datum']['named'][key]) for key in ('mllw', 'msl', 'mhhw'))}}}; prior scientific/data/provenance preserved")
    output = inputs["source_release"].with_name("combined-green-stations.jsonl")
    output.write_text("".join(json.dumps(station, sort_keys=True) + "\n" for station in release["stations"]))
    print(f"COMBINED NATIVE OUTPUT SHA256 {observed_produce.sha256(output)}")


@pytest.mark.parametrize("publish_official,publish_observed", [(False, False), (True, False), (False, True)])
def test_native_combined_staging_and_independent_publication(native_combined_datum_inputs, publish_official, publish_observed):
    release, official_path, official_sha, observed_path, observed_sha, inputs = native_combined_datum_inputs
    before = copy.deepcopy(release)
    official, observed = build_otc.apply_datum_drafts(release, official=(official_path, official_sha),
        observed=(observed_path, observed_sha), publish_official=publish_official,
        publish_observed=publish_observed, observed_inputs=inputs)
    assert official["public_admission"] == publish_official
    assert observed["public_admission"] == publish_observed
    if not publish_official and not publish_observed:
        assert release == before
    for station in release["stations"]:
        for cs in station["constant_sets"]:
            if cs["source"] == "gesla-fit":
                assert ("lat" in cs["datum"].get("named", {})) == publish_official
                assert ("msl" in cs["datum"].get("named", {})) == publish_observed


@pytest.mark.parametrize("defect", ["stale_harmonic", "stale_clock", "draft_hash", "missing_binding", "overlap"])
def test_native_combined_refuses_tampered_inputs_before_mutation(native_combined_datum_inputs, defect, tmp_path):
    release, official_path, official_sha, observed_path, observed_sha, inputs = native_combined_datum_inputs
    if defect in ("stale_harmonic", "stale_clock"):
        cs = next(cs for station in release["stations"] for cs in station["constant_sets"] if cs["source"] == "gesla-fit")
        if defect == "stale_harmonic":
            cs["constituents"][0]["amplitude_m"] += 0.001
        else:
            cs["time_base"]["decided_build_commit"] = "0" * 40
    elif defect == "draft_hash":
        observed_sha = "0" * 64
    elif defect == "missing_binding":
        rows = [json.loads(line) for line in observed_path.read_text().splitlines()]
        del rows[0]["input_binding"]
        observed_path = tmp_path / "missing-binding.jsonl"
        observed_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        observed_sha = observed_produce.sha256(observed_path)
    else:
        rows = [json.loads(line) for line in official_path.read_text().splitlines()]
        measured = next(row["draft_datum"] for row in map(json.loads, observed_path.read_text().splitlines()) if row["set_id"] == rows[0]["set_id"])
        for field in ("named", "basis"):
            rows[0]["draft_datum"][field]["mhw"] = copy.deepcopy(measured[field]["mhw"])
        official_path = tmp_path / "overlapping-official.jsonl"
        official_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        official_sha = observed_produce.sha256(official_path)
    before = copy.deepcopy(release)
    with pytest.raises(ValueError):
        build_otc.apply_datum_drafts(release, official=(official_path, official_sha), observed=(observed_path, observed_sha),
            publish_official=True, publish_observed=True, observed_inputs=inputs)
    assert release == before


@pytest.fixture
def native_postcomputed_source_inputs(native_combined_datum_inputs, tmp_path):
    release, official_path, official_sha, _, _, inputs = native_combined_datum_inputs
    root = Path(os.environ["OTC_OBSERVED_ATTACHMENT_PROOF"])
    qualified = root / "reduction-zero-control/full-reduction-scheduled/observed-drafts.jsonl"
    assert observed_produce.sha256(qualified) == "9900bca092508151fca9fe04d774b1492808b5d88defcdb9c9013c034a8a3132"
    ids = {cs["set_id"] for station in release["stations"] for cs in station["constant_sets"]}
    # Copy the exact accepted rows; their original source and native bindings are untouched.
    with qualified.open() as stream:
        lines = [line for line in stream if json.loads(line)["set_id"] in ids]
    assert len(lines) == 5
    path = tmp_path / "original-postcomputed-bound-rows.jsonl"
    path.write_text("".join(lines))
    inputs["source_release"] = Path("/Users/jpr5/.local/share/copilotkit/cr/otc-impl/derived-chart-datum/postfix-candidate/native-build/OTC_20261011.jsonl")
    inputs["source_release_sha256"] = "14a12b36365f0e057633bb8cdf266a50cc24a3d0d417524f29e35547d299e111"
    return release, official_path, official_sha, path, observed_produce.sha256(path), inputs


def test_native_combined_preserves_original_postcomputed_source(native_postcomputed_source_inputs):
    release, official_path, official_sha, observed_path, observed_sha, inputs = native_postcomputed_source_inputs
    old_order = copy.deepcopy(release)
    build_otc.apply_official_datum_fallbacks(old_order, official_path, official_sha)
    old_observed = build_otc.apply_observed_datum_fallbacks(old_order, observed_path, observed_sha, **inputs)
    assert old_observed["observed_basis_fallback_count"] == 33
    print(f"ORIGINAL ACCEPTED SOURCE old official→observed GREEN: source={inputs['source_release_sha256']}; untouched rows={observed_sha}")
    official, observed = build_otc.apply_datum_drafts(release, official=(official_path, official_sha),
        observed=(observed_path, observed_sha), publish_official=True, publish_observed=True, observed_inputs=inputs)
    assert release == old_order
    assert official["computed_basis_fallback_count"] == 6 and observed["observed_basis_fallback_count"] == 33
    for station in release["stations"]:
        for cs in station["constant_sets"]:
            if cs["source"] == "gesla-fit":
                print(f"ORIGINAL SOURCE COMBINED GREEN {cs['set_id']}: {cs['datum']['named']}; exact old-order values/metadata/provenance retained")
    output = observed_path.with_name("original-source-compatible-output.jsonl")
    output.write_text("".join(json.dumps(station, sort_keys=True) + "\n" for station in release["stations"]))
    print(f"ORIGINAL SOURCE COMPAT OUTPUT SHA256 {observed_produce.sha256(output)}")


@pytest.mark.parametrize("defect", ["scientific_provenance", "historical_binding", "official_zero_injection", "official_value", "overlap", "unpublished_official"])
def test_native_postcomputed_compatibility_still_refuses_tampering(native_postcomputed_source_inputs, defect, tmp_path):
    release, official_path, official_sha, observed_path, observed_sha, inputs = native_postcomputed_source_inputs
    publish_official = defect != "unpublished_official"
    if defect == "scientific_provenance":
        release["stations"][0]["constant_sets"][0]["provenance"]["tcd_sha256"] = "0" * 64
    elif defect == "historical_binding":
        rows = [json.loads(line) for line in observed_path.read_text().splitlines()]
        rows[0]["input_binding"]["native_input_sha256"] = "0" * 64
        observed_path = tmp_path / "stale-original-binding.jsonl"
        observed_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        observed_sha = observed_produce.sha256(observed_path)
    elif defect in ("official_zero_injection", "official_value", "overlap"):
        rows = [json.loads(line) for line in official_path.read_text().splitlines()]
        if defect == "official_zero_injection":
            rows[0]["draft_datum"]["zero"] = "gauge_zero"
        elif defect == "official_value":
            rows[0]["draft_datum"]["named"]["lat"] += 0.001
        else:
            measured = next(row["draft_datum"] for row in map(json.loads, observed_path.read_text().splitlines()) if row["set_id"] == rows[0]["set_id"])
            for field in ("named", "basis"):
                rows[0]["draft_datum"][field]["mhw"] = copy.deepcopy(measured[field]["mhw"])
        official_path = tmp_path / "invalid-original-official.jsonl"
        official_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        official_sha = observed_produce.sha256(official_path)
    before = copy.deepcopy(release)
    with pytest.raises(ValueError):
        build_otc.apply_datum_drafts(release, official=(official_path, official_sha), observed=(observed_path, observed_sha),
            publish_official=publish_official, publish_observed=True, observed_inputs=inputs)
    assert release == before


@pytest.mark.skipif("OTC_GESLA_METADATA_PROOF" not in os.environ, reason="requires actual frozen GESLA metadata cases")
@pytest.mark.parametrize("index", range(3))
def test_native_gesla_sampling_class_is_not_timestamp_verification(index, tmp_path):
    root = Path(os.environ["OTC_GESLA_METADATA_PROOF"])
    directory = root / "missing-gesla"
    facts = json.loads((directory / "metadata-issue-facts.json").read_text())
    sid = facts["actual_gesla_wrong_sampling_examples"][index]["set_id"]
    entry = next(e for e in json.loads((directory / "roster.json").read_text())["rows"] if e["set_id"] == sid)
    payload = json.loads(Path(entry["path"]).read_text())
    assert hashlib.sha256(Path(entry["path"]).read_bytes()).hexdigest() == entry["input_sha256"]
    frozen = None
    for path in [directory / "pilot.jsonl", *[directory / f"shard-{n}.jsonl" for n in range(4)]]:
        with path.open() as stream:
            for line in stream:
                candidate = json.loads(line)
                if candidate["set_id"] == sid:
                    frozen = candidate
                    break
        if frozen is not None:
            break
    assert frozen is not None
    calibration = json.loads((directory / "diagnostic-calibration.json").read_text())
    cs = payload["constant_set"]
    original = copy.deepcopy((cs, frozen))
    assert cs["time_base"]["code"] == "verified"
    assert not any("sampl" in key for key in cs)
    row = lat_hat.calibrated_draft(cs, frozen, calibration)
    sampling = 0.011 * sum(c["amplitude_m"] for c in cs["constituents"] if c["name"] in ("M2", "S2"))
    assert sampling > 0
    for level in ("lat", "hat"):
        parts = row["uncertainty_components"][level]
        assert parts["sampling_verified"] is False
        assert parts["sampling_m"] == sampling
        basis = row["draft_datum"]["basis"][level]
        assert basis["uncertainty_m"] == calibration["gesla-fit"][level]["sample_sd_m"] + sampling
        expected_label = "propagated" if sampling >= parts["base_m"] else "calibrated"
        assert basis["uncertainty_basis"] == expected_label
        assert "sampling_assumed" in basis["flags"]
        assert basis["fallback"]["reason"] == "datum_gate_failed"
        assert f"plus sampling {sampling:.9g} m" in basis["fallback"]["otc_finding"]
    release = json.loads((root / "native-build/OTC_20261011.meta.json").read_text())
    with (root / "native-build/OTC_20261011.jsonl").open() as stream:
        station = next(s for line in stream if (s := json.loads(line))["station_id"] == entry["station_id"])
    release["stations"] = [station]
    before = copy.deepcopy(release)
    target = next(c for c in station["constant_sets"] if c["set_id"] == sid)
    path = tmp_path / "actual-metadata-draft.jsonl"
    sha = write_actual_draft(row, path)
    receipt = build_otc.apply_official_datum_fallbacks(release, path, sha)
    assert receipt["computed_basis_fallback_count"] == 2
    from otc_pipeline.release_writer import validate_release
    assert validate_release(release) == []
    for level in ("lat", "hat"):
        assert target["datum"]["named"][level] == frozen["raw"][f"{level}_m"]
        assert target["datum"]["basis"][level] == row["draft_datum"]["basis"][level]
    target["datum"] = copy.deepcopy(cs["datum"])
    assert release == before
    assert (cs, frozen) == original
    print(json.dumps({"set_id": sid, "sampling_m": sampling, "literal_sum_and_basis": True,
                      "raw_values_sources_published_rankings_and_bindings_preserved": True}))


@pytest.mark.skipif("OTC_GESLA_PILOT_RESULTS" not in os.environ, reason="requires the completed three missing-place pilot")
def test_native_missing_gesla_pilot_public_values_and_sdk(tmp_path):
    root = Path(os.environ["OTC_GESLA_PROOF"])
    path = Path(os.environ["OTC_GESLA_PILOT_RESULTS"])
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    ids = {r["set_id"] for r in rows}
    station_ids = {r["station_id"] for r in rows}
    assert len(ids) == len(station_ids) == 3
    release = json.loads((root / "native-build/OTC_20261011.meta.json").read_text())
    release["stations"] = []
    with (root / "native-build/OTC_20261011.jsonl").open() as stream:
        for line in stream:
            station = json.loads(line)
            if station["station_id"] in station_ids:
                release["stations"].append(station)
    original = copy.deepcopy(release)
    from otc_pipeline.release_writer import validate_release
    assert validate_release(release) == []
    missing = sum(level not in c.get("datum", {}).get("named", {}) for s in release["stations"]
                  for c in s["constant_sets"] if c["set_id"] in ids for level in ("lat", "hat"))
    assert missing == 6
    receipt = build_otc.apply_official_datum_fallbacks(release, path, hashlib.sha256(path.read_bytes()).hexdigest())
    assert receipt["computed_basis_fallback_count"] == 6
    assert receipt["normal_datum_gate_accepted"] is False
    assert validate_release(release) == []
    expected = {r["set_id"]: r for r in rows}
    witnesses = []
    for old, station in zip(original["stations"], release["stations"], strict=True):
        assert station["recommended_set_id"] == old["recommended_set_id"]
        for before, cs in zip(old["constant_sets"], station["constant_sets"], strict=True):
            if cs["set_id"] not in ids:
                assert cs == before
                continue
            assert {k: v for k, v in cs.items() if k != "datum"} == {k: v for k, v in before.items() if k != "datum"}
            for k, v in before["datum"].get("named", {}).items():
                assert cs["datum"]["named"][k] == v
                assert cs["datum"]["basis"][k] == before["datum"]["basis"][k]
            for level in ("lat", "hat"):
                row = expected[cs["set_id"]]
                assert cs["datum"]["named"][level] == row["raw"][f"{level}_m"]
                basis = cs["datum"]["basis"][level]
                parts = row["uncertainty_components"][level]
                assert basis["uncertainty_m"] == parts["base_m"] + parts["sampling_m"]
                assert "unknown" in basis["fallback"]["otc_finding"]
    sys.path.insert(0, os.environ["OTC_DATUM_SDK_SOURCE"])
    try:
        otc = importlib.import_module("opentideconstants")
        for mode in ("eager", "stream"):
            fixture = tmp_path / ("OTC_20261011.json" if mode == "eager" else "OTC_20261011.jsonl")
            if mode == "eager":
                fixture.write_text(json.dumps(release))
            else:
                fixture.write_text("".join(json.dumps(s) + "\n" for s in release["stations"]))
                fixture.with_name("OTC_20261011.meta.json").write_text(json.dumps({k: v for k, v in release.items() if k != "stations"}))
            with otc.OpenTideConstants(file=fixture, mode=mode) as client:
                for row in rows:
                    info = client.datums(row["station_id"], set_id=row["set_id"])
                    for level in ("lat", "hat"):
                        exposed = next(v for v in info.levels if v.key == level)
                        assert exposed.value_m == row["raw"][f"{level}_m"]
                        assert exposed.uncertainty_m == row["draft_datum"]["basis"][level]["uncertainty_m"]
                        assert exposed.fallback["reason"] == "datum_gate_failed"
                        assert dict(exposed.fallback) == row["draft_datum"]["basis"][level]["fallback"]
                    witnesses.append({"mode": mode, "set_id": row["set_id"], "values": {v.key: v.value_m for v in info.levels}})
    finally:
        sys.path.pop(0)
    (root / "missing-gesla/pilot-public-green.json").write_text(json.dumps({
        "new_places": 3, "new_levels": 6, "native_validation_errors": 0,
        "old_sources_values_and_rankings_unchanged": True, "sdk_witnesses": witnesses}, indent=2) + "\n")


@pytest.mark.skipif("OTC_GESLA_PROOF" not in os.environ, reason="requires actual native GESLA sets and frozen extrema")
@pytest.mark.parametrize("index", range(3))
def test_native_gesla_fallback_admission_preserves_sources(index, tmp_path):
    root = Path(os.environ["OTC_GESLA_PROOF"])
    entry = json.loads((root / "full-t5b/roster.json").read_text())["rows"][index]
    payload = json.loads(Path(entry["path"]).read_text())
    comparisons = [r for line in (root / "full-t5b/all-results.jsonl").read_text().splitlines()
                   if (r := json.loads(line)).get("set_id")]
    row = next(r for r in comparisons if r["set_id"] == entry["set_id"])
    release = json.loads((root / "native-build/OTC_20261011.meta.json").read_text())
    with (root / "native-build/OTC_20261011.jsonl").open() as stream:
        station = next(s for line in stream if (s := json.loads(line))["station_id"] == payload["station_id"])
    release["stations"] = [station]
    cs = next(c for c in station["constant_sets"] if c["set_id"] == entry["set_id"])
    before = copy.deepcopy(release)
    draft = datum_draft(cs, row["raw"])
    # Independently calculate the existing sample-spread and literal-sum rule.
    sampling = 0.011 * sum(c["amplitude_m"] for c in cs["constituents"] if c["name"] in ("M2", "S2"))
    for level in ("lat", "hat"):
        spread = stdev(r[f"{level}_residual_m"] for r in comparisons)
        draft["basis"][level].update(uncertainty_m=spread + sampling, uncertainty_basis="calibrated")
    row.update(draft_datum=draft, calibration_status="diagnostic",
               computation_input_sha256=computation_input_sha256({"constant_set": cs, "astro_table": payload["astro_table"]}))
    path = tmp_path / "native-gesla.jsonl"
    sha = write_actual_draft(row, path)
    receipt = build_otc.apply_official_datum_fallbacks(release, path, sha)
    assert receipt["normal_datum_gate_accepted"] is False
    assert receipt["computed_basis_fallback_count"] == 2
    for key in ("lat", "hat"):
        assert cs["datum"]["named"][key] == row["raw"][f"{key}_m"]
        assert cs["datum"]["basis"][key]["fallback"]["reason"] == "datum_gate_failed"
        assert "unknown" in cs["datum"]["basis"][key]["fallback"]["otc_finding"]
    cs["datum"] = before["stations"][0]["constant_sets"][station["constant_sets"].index(cs)]["datum"]
    assert release == before


@pytest.fixture
def actual_datum_case(tmp_path, request):
    """Three cached production stations and their frozen, unrecomputed extrema."""
    root = os.environ.get("OTC_DATUM_FIX_PROOF")
    if not root:
        pytest.skip("requires the actual cached datum fix proof")
    root = Path(root)
    contract = json.loads((root / "new-place-sdk-contract.json").read_bytes())
    case = contract["cases"][request.param]
    release = copy.deepcopy(contract["release_fixture"])
    release["stations"] = [s for s in release["stations"] if s["station_id"] == case["station_id"]]
    entry = next(e for e in json.loads((root / "missing-official/roster.json").read_text())["rows"]
                 if e["set_id"] == case["set_id"])
    payload = json.loads(Path(entry["path"]).read_text())
    cs = next(c for c in release["stations"][0]["constant_sets"] if c["set_id"] == case["set_id"])
    cs["datum"] = copy.deepcopy(payload["constant_set"]["datum"])
    row = next(r for line in (root / "official-public-drafts.jsonl").read_text().splitlines()
               if (r := json.loads(line)).get("set_id") == cs["set_id"])
    # Independently bind the actual request, before any intentional stale-input mutation.
    body = {"request": {"astro_table": payload["astro_table"], "constituents": cs["constituents"],
                        "msl_offset_m": cs["datum"]["msl_offset_m"],
                        "start": "2020-01-01T00:00:00Z", "end": "2039-01-01T00:00:00Z"},
            "convention_id": cs.get("convention_id"), "source_version": cs.get("source_version"),
            "time_base": cs.get("time_base"), "record_issues": cs.get("record_issues", []),
            "truncated_lows": sorted(k for k, b in cs["datum"].get("basis", {}).items()
                                     if "truncated_lows" in b.get("flags", []))}
    row["computation_input_sha256"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return release, cs, row, tmp_path / "actual-drafts.jsonl"


def write_actual_draft(row, path):
    path.write_text(json.dumps(row) + "\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def native_chart_datum_input(tmp_path):
    """Replay the three saved native drafts without recomputing their levels."""
    root = os.environ.get("OTC_CHART_DATUM_PROOF")
    if not root:
        pytest.skip("requires the saved native chart-datum witnesses")
    root = Path(root)
    facts = json.loads((root / "native-cases.json").read_text())
    fixture = Path(facts["fixture_path"])
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == facts["fixture_sha256"]
    original = json.loads(fixture.read_text())
    release = copy.deepcopy(original)
    ids = {row["set_id"] for row in facts["cases"]}
    assert len(ids) == 3
    drafts = Path(os.environ["OTC_CHART_DATUM_DRAFTS"])
    assert hashlib.sha256(drafts.read_bytes()).hexdigest() == "77006af6f3274eae6437651abc93440c3d2a9d7d6452c9a6f7fa50a82b21fa99"
    rows = [row for line in drafts.read_text().splitlines()
            if (row := json.loads(line)).get("set_id") in ids]
    assert len(rows) == 3
    for station in release["stations"]:
        for cs in station["constant_sets"]:
            if cs["set_id"] in ids:
                for level in ("lat", "hat"):
                    assert cs["datum"]["basis"][level]["kind"] == "computed"
                    del cs["datum"]["named"][level]
                    del cs["datum"]["basis"][level]
    path = tmp_path / "actual-chart-drafts.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return root, original, release, ids, path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_native_chart_datum_designation_follows_labelled_level_attachment(native_chart_datum_input):
    _, original, release, ids, path, sha = native_chart_datum_input
    expected = copy.deepcopy(original)
    for station in expected["stations"]:
        for cs in station["constant_sets"]:
            if cs["set_id"] in ids:
                cs["datum"]["chart_datum"] = "lat"
    receipt = build_otc.apply_official_datum_fallbacks(release, path, sha)
    assert release == expected
    assert receipt["computed_basis_fallback_count"] == 6
    derivation = receipt["chart_datum_derivation"]
    assert derivation["changed_places"] == derivation["changed_sets"] == 3
    assert derivation["conventions_sha256"] == hashlib.sha256(
        Path(build_otc.__file__).with_name("chart_datum_conventions.json").read_bytes()).hexdigest()
    assert derivation["citations"]["iho-2021-uk"]["stated_convention"] == "approximate LAT"


def test_native_staging_only_does_not_designate_chart_datum(native_chart_datum_input):
    _, _, release, _, path, sha = native_chart_datum_input
    before = copy.deepcopy(release)
    receipt = build_otc.stage_official_datums(release, path, sha)
    assert release == before
    assert receipt["public_admission"] is False
    assert "chart_datum_derivation" not in receipt


@pytest.mark.parametrize("control", ["published_precedence", "uk_country_overseas_area_not_evidenced",
                                     "southern_norway_not_unmargined_lat", "flatline", "truncated_lows"])
def test_native_producer_chart_datum_controls(native_chart_datum_input, control, tmp_path):
    root, original, _, _, _, _ = native_chart_datum_input
    if control in ("flatline", "truncated_lows"):
        station = copy.deepcopy(original["stations"][0])
        for cs in station["constant_sets"]:
            if control == "flatline":
                cs.setdefault("record_issues", []).append({"issue": "flatline"})
            else:
                for basis in cs.get("datum", {}).get("basis", {}).values():
                    basis.setdefault("flags", []).append("truncated_lows")
    else:
        station = json.loads((root / "native-controls.json").read_text())[control]
    release = {"stations": [station]}
    before = copy.deepcopy(release)
    path = tmp_path / "no-drafts.jsonl"
    path.write_text("")
    receipt = build_otc.apply_official_datum_fallbacks(release, path, hashlib.sha256(path.read_bytes()).hexdigest())
    assert release == before
    assert receipt["chart_datum_derivation"]["changed_places"] == 0


@pytest.mark.parametrize("actual_datum_case", range(3), indirect=True)
def test_actual_tombstone_keeps_official_datum_producer_working(actual_datum_case):
    release, cs, row, path = actual_datum_case
    examples = json.loads((Path(__file__).parent / "fixtures/example-1.0.json").read_text())
    tombstone = copy.deepcopy(next(s for s in examples["stations"] if s["status"] == "removed"))
    tombstone["merged_into"] = release["stations"][0]["station_id"]
    release["stations"].append(tombstone)
    schema = json.loads((Path(__file__).parents[1] / "schema/otc-1.0.schema.json").read_text())
    Draft202012Validator(schema).validate(release)
    receipt = build_otc.apply_official_datum_fallbacks(release, path, write_actual_draft(row, path))
    assert release["stations"][-1] == tombstone
    assert receipt["computed_basis_fallback_count"] == 2


@pytest.mark.parametrize("actual_datum_case", range(3), indirect=True)
@pytest.mark.parametrize("mutation", ["amplitude", "phase", "table", "time_base", "missing_binding"])
def test_actual_stale_harmonics_rejected(actual_datum_case, mutation):
    release, cs, row, path = actual_datum_case
    if mutation == "amplitude":
        cs["constituents"][0]["amplitude_m"] += 0.001
    elif mutation == "phase":
        cs["constituents"][0]["phase_deg"] = (cs["constituents"][0]["phase_deg"] + 1) % 360
    elif mutation == "table":
        release["astro_tables"][0]["constituents"]["M2"]["f"][0] *= 1.001
    elif mutation == "time_base":
        cs["time_base"] = {"status": "unverified"}
    else:
        del row["computation_input_sha256"]
    with pytest.raises(ValueError, match="computation input"):
        build_otc.apply_official_datum_fallbacks(release, path, write_actual_draft(row, path))


@pytest.mark.parametrize("actual_datum_case", range(3), indirect=True)
def test_actual_unrelated_computed_basis_preserved(actual_datum_case):
    release, cs, row, path = actual_datum_case
    prior = {"kind": "computed", "method": "harmonic_extremes", "epoch": EPOCH,
             "uncertainty_m": 0.123, "uncertainty_basis": "propagated",
             "fallback": {"reason": "datum_gate_failed", "basis": "Existing independent computation",
                          "otc_finding": "Preserve this prior finding."}}
    published = next(k for k, b in cs["datum"]["basis"].items() if b["kind"] == "published")
    key = f"otc_{published}"
    cs["datum"]["named"][key] = cs["datum"]["named"][published]
    cs["datum"]["basis"][key] = copy.deepcopy(prior)
    row["draft_datum"]["named"][key] = cs["datum"]["named"][key]
    row["draft_datum"]["basis"][key] = copy.deepcopy(prior)
    schema = json.loads((Path(__file__).parents[1] / "schema/otc-1.0.schema.json").read_text())
    Draft202012Validator(schema).validate(release)
    receipt = build_otc.apply_official_datum_fallbacks(release, path, write_actual_draft(row, path))
    assert cs["datum"]["basis"][key] == prior
    assert receipt["computed_basis_fallback_count"] == 2


@pytest.mark.skipif("OTC_DATUM_FIX_PROOF" not in os.environ,
                    reason="requires the actual frozen519 inputs and completed build")
def test_actual519_binding_preserves_completed_build(tmp_path):
    root = Path(os.environ["OTC_DATUM_FIX_PROOF"])
    rows = {r["set_id"]: r for line in (root / "official-public-drafts.jsonl").read_text().splitlines()
            if (r := json.loads(line)).get("set_id")}
    paths = {}
    for name in ("full-t5", "t6", "missing-official"):
        for entry in json.loads((root / name / "roster.json").read_text())["rows"]:
            assert entry["set_id"] not in paths
            paths[entry["set_id"]] = entry
    assert rows.keys() == paths.keys() and len(rows) == 519
    bound = []
    for sid, row in rows.items():
        entry = paths[sid]
        raw_input = Path(entry["path"]).read_bytes()
        if entry.get("input_sha256"):
            assert hashlib.sha256(raw_input).hexdigest() == entry["input_sha256"]
        payload = json.loads(raw_input)
        assert payload["constant_set"]["set_id"] == sid
        updated = {**row, "computation_input_sha256": computation_input_sha256(payload)}
        assert {k: v for k, v in updated.items() if k != "computation_input_sha256"} == row
        bound.append(updated)
    artifact = root / "official-public-drafts-bound.jsonl"
    artifact.write_text("".join(json.dumps(r, sort_keys=True, allow_nan=False) + "\n" for r in bound))
    sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
    baseline = root.parent / "datum-stated-batch/native-build"
    release = json.loads((baseline / "OTC_20261011.meta.json").read_bytes())
    release["stations"] = []
    with (baseline / "OTC_20261011.jsonl").open() as stream:
        for line in stream:
            station = json.loads(line)
            if any(cs["set_id"] in rows for cs in station.get("constant_sets", [])):
                release["stations"].append(station)
    receipt = build_otc.apply_official_datum_fallbacks(release, artifact, sha)
    assert receipt["staged_set_count"] == 519
    assert receipt["computed_basis_fallback_count"] == 1038
    resulting = {cs["set_id"]: cs["datum"] for s in release["stations"]
                 for cs in s.get("constant_sets", []) if cs["set_id"] in rows}
    checked = 0
    with (root / "native-build/OTC_20261011.jsonl").open() as stream:
        for line in stream:
            station = json.loads(line)
            for cs in station.get("constant_sets", []):
                if cs["set_id"] in resulting:
                    assert cs["datum"] == resulting[cs["set_id"]]
                    checked += 1
    assert checked == 519
    proof = {"bound_artifact": str(artifact), "sha256": sha, "sets": checked,
             "computed_levels": 1038, "unchanged_existing_build_data": True,
             "existing_build_commit": "d992aa3", "strict_predictions_rerun": 0}
    (root / "source-fix519-binding-green.json").write_text(json.dumps(proof, indent=2) + "\n")


@pytest.fixture
def staged_input(tmp_path):
    datum = {"zero": "station_datum", "msl_offset_m": 1.0,
             "named": {"lat": 0.5, "hat": 2.0},
             "basis": {key: {"kind": "published", "method": "source"} for key in ("lat", "hat")}}
    cs = {"set_id": "OTC-test/noaa/9410660", "source": "noaa", "source_type": "official", "datum": datum,
          "convention_id": "test", "constituents": [{"name": "M2", "amplitude_m": 1,
                                                       "phase_deg": 0, "speed_deg_per_hour": 28.9841042}]}
    draft = copy.deepcopy(datum)
    for level, value in (("lat", 0.51), ("hat", 1.99)):
        draft["named"][f"otc_{level}"] = value
        draft["basis"][f"otc_{level}"] = {"kind": "computed", "method": "harmonic_extremes",
                                           "epoch": EPOCH, "uncertainty_m": 0.02,
                                           "uncertainty_basis": "calibrated"}
    row = {"set_id": cs["set_id"], "draft_datum": draft, "calibration_status": "available",
           "raw": {"lat_m": 0.51, "hat_m": 1.99, "start": START, "end": END}}
    table = {"astro_table_id": "test"}
    row["computation_input_sha256"] = computation_input_sha256({"constant_set": cs, "astro_table": table})
    return {"stations": [{"station_id": "OTC-test", "constant_sets": [cs]}],
            "conventions": [{"convention_id": "test", "astro_table_id": "test"}],
            "astro_tables": [table]}, row, tmp_path / "drafts.jsonl"


@pytest.mark.parametrize("defect", ["sha", "published", "source", "method", "epoch", "uncertainty", "raw", "duplicate"])
def test_staging_rejects_invalid_or_changed_drafts(staged_input, defect):
    release, row, path = staged_input
    if defect == "published":
        row["draft_datum"]["named"]["lat"] = 0.6
    elif defect == "source":
        release["stations"][0]["constant_sets"][0]["source"] = "gesla-fit"
    elif defect == "method":
        row["draft_datum"]["basis"]["otc_lat"]["method"] = "source"
    elif defect == "epoch":
        row["draft_datum"]["basis"]["otc_lat"]["epoch"] = {}
    elif defect == "uncertainty":
        row["draft_datum"]["basis"]["otc_lat"]["uncertainty_m"] = -1.0
    elif defect == "raw":
        row["raw"]["lat_m"] = 0.52
    path.write_text(json.dumps(row) + "\n" + (json.dumps(row) + "\n" if defect == "duplicate" else ""))
    sha = "wrong" if defect == "sha" else hashlib.sha256(path.read_bytes()).hexdigest()
    before = copy.deepcopy(release)
    with pytest.raises(ValueError):
        build_otc.stage_official_datums(release, path, sha)
    assert release == before


def test_missing_official_levels_use_unprefixed_keys_and_keep_set_ranking(staged_input):
    release, row, path = staged_input
    release["stations"][0]["country"] = "FRA"
    cs = release["stations"][0]["constant_sets"][0]
    cs["datum"]["named"].clear()
    cs["datum"]["basis"].clear()
    for level in ("lat", "hat"):
        row["draft_datum"]["named"][level] = row["draft_datum"]["named"].pop(f"otc_{level}")
        row["draft_datum"]["basis"][level] = row["draft_datum"]["basis"].pop(f"otc_{level}")
    path.write_text(json.dumps(row) + "\n")
    receipt = build_otc.apply_official_datum_fallbacks(release, path, hashlib.sha256(path.read_bytes()).hexdigest())
    assert receipt["computed_basis_fallback_count"] == 2
    assert set(cs["datum"]["named"]) == {"lat", "hat"}
    assert "fallback" not in cs
    assert cs["datum"]["basis"]["lat"]["fallback"]["reason"] == "datum_gate_failed"
    assert cs["datum"]["chart_datum"] == "lat"
    assert receipt["chart_datum_derivation"]["changed_places"] == 1


@pytest.mark.skipif("OTC_DATUM_NATIVE_RELEASE" not in os.environ,
                    reason="native staging proof requires the pinned cached release and drafts")
def test_native_official_drafts_stage_without_changing_release():
    release_path = Path(os.environ["OTC_DATUM_NATIVE_RELEASE"])
    drafts_path = Path(os.environ["OTC_DATUM_NATIVE_DRAFTS"])
    release = json.loads(release_path.read_bytes())
    before = hashlib.sha256(json.dumps(release, sort_keys=True).encode()).hexdigest()
    candidate = build_otc.stage_official_datums(
        release, drafts_path, hashlib.sha256(drafts_path.read_bytes()).hexdigest())
    assert candidate["public_admission"] is False
    assert candidate["staged_set_count"] == 412
    assert candidate["staged_level_count"] == 824
    assert candidate["release_unchanged"] is True
    assert hashlib.sha256(json.dumps(release, sort_keys=True).encode()).hexdigest() == before
    native = {cs["set_id"]: cs for station in release["stations"] for cs in station["constant_sets"]}
    source_rows = {row["set_id"]: row for line in drafts_path.read_text().splitlines()
                   if (row := json.loads(line)).get("set_id")}
    for staged in candidate["drafts"]:
        sid = staged["set_id"]
        assert staged["datum"] == source_rows[sid]["draft_datum"]
        assert staged["source"] == native[sid]["source"]
        assert staged["input_sha256"] == candidate["input_sha256"]
        for key, basis in native[sid]["datum"]["basis"].items():
            if basis["kind"] == "published":
                assert staged["datum"]["named"][key] == native[sid]["datum"]["named"][key]
                assert staged["datum"]["basis"][key] == basis


@pytest.mark.skipif("OTC_DATUM_NATIVE_RELEASE" not in os.environ,
                    reason="native fallback schema proof requires the pinned cached release and drafts")
def test_native_computed_fallback_schema_preserves_official_sets():
    release = json.loads(Path(os.environ["OTC_DATUM_NATIVE_RELEASE"]).read_bytes())
    drafts = [row for line in Path(os.environ["OTC_DATUM_NATIVE_DRAFTS"]).read_text().splitlines()
              if (row := json.loads(line)).get("set_id")]
    native = {cs["set_id"]: cs for station in release["stations"] for cs in station["constant_sets"]}
    schema_path = Path(__file__).resolve().parents[1] / "schema/otc-1.0.schema.json"
    schema = json.loads(schema_path.read_text())
    validator = Draft202012Validator({"$ref": "#/$defs/datum", "$defs": schema["$defs"]})
    for row in drafts[:3]:
        cs = native[row["set_id"]]
        before = copy.deepcopy(cs)
        datum = copy.deepcopy(row["draft_datum"])
        for key, basis in datum["basis"].items():
            if basis["kind"] == "computed":
                basis["fallback"] = {"reason": "datum_gate_failed", "basis": "Frozen harmonic extrema with tuning uncertainty",
                                     "otc_finding": "Full DT4a tuning gate has not passed; no validated accuracy claim."}
        validator.validate(datum)
        assert cs == before
        assert "fallback" not in cs
        for key, basis in cs["datum"]["basis"].items():
            if basis["kind"] == "published":
                assert datum["basis"][key] == basis
                assert datum["named"][key] == cs["datum"]["named"][key]


@pytest.mark.skipif("OTC_DATUM_NATIVE_RELEASE" not in os.environ,
                    reason="native producer proof requires the pinned cached release and drafts")
def test_native_official_fallbacks_publish_values_without_demoting_sets():
    release = json.loads(Path(os.environ["OTC_DATUM_NATIVE_RELEASE"]).read_bytes())
    drafts_path = Path(os.environ["OTC_DATUM_NATIVE_DRAFTS"])
    before = copy.deepcopy(release)
    receipt = build_otc.apply_official_datum_fallbacks(
        release, drafts_path, hashlib.sha256(drafts_path.read_bytes()).hexdigest())
    assert receipt["public_admission"] is True
    assert receipt["normal_datum_gate_accepted"] is False
    assert receipt["computed_basis_fallback_count"] == 824
    candidates = {row["set_id"]: row for line in drafts_path.read_text().splitlines()
                  if (row := json.loads(line)).get("set_id")}
    count = 0
    for original_station, station in zip(before["stations"], release["stations"], strict=True):
        assert {k: v for k, v in station.items() if k != "constant_sets"} == {
            k: v for k, v in original_station.items() if k != "constant_sets"}
        for old, cs in zip(original_station["constant_sets"], station["constant_sets"], strict=True):
            if cs["set_id"] not in candidates:
                assert cs == old
                continue
            assert {k: v for k, v in cs.items() if k != "datum"} == {k: v for k, v in old.items() if k != "datum"}
            expected = copy.deepcopy(candidates[cs["set_id"]]["draft_datum"])
            for key, basis in cs["datum"]["basis"].items():
                if basis["kind"] == "computed":
                    assert basis["fallback"]["reason"] == "datum_gate_failed"
                    assert basis["fallback"]["basis"] and basis["fallback"]["otc_finding"]
                    expected["basis"][key]["fallback"] = basis["fallback"]
                    count += 1
            assert cs["datum"] == expected
    assert count == 824
    from otc_pipeline.release_writer import validate_release
    assert validate_release(release) == []


@pytest.mark.skipif("OTC_MISSING_OFFICIAL_RESULTS" not in os.environ,
                    reason="full missing-level value proof requires the completed107 native outputs")
def test_native_missing_official_fallbacks_add107_usable_places():
    release = json.loads(Path(os.environ["OTC_DATUM_NATIVE_RELEASE"]).read_bytes())
    path = Path(os.environ["OTC_MISSING_OFFICIAL_RESULTS"])
    before = copy.deepcopy(release)

    def usable_places(doc, level):
        found = set()
        for station in doc["stations"]:
            if station.get("status") != "active":
                continue
            datums = [cs.get("datum", {}) for cs in station.get("constant_sets", [])]
            datums.append(station.get("subordinate_offsets", {}).get("datum", {}))
            if any(any(key in datum.get("named", {}) and datum.get("basis", {}).get(key, {}).get("kind")
                       in ("published", "computed", "observed") for key in (level, f"otc_{level}")) for datum in datums):
                found.add(station["station_id"])
        return found

    initial = {level: usable_places(before, level) for level in ("lat", "hat")}
    receipt = build_otc.apply_official_datum_fallbacks(release, path, hashlib.sha256(path.read_bytes()).hexdigest())
    assert receipt["staged_set_count"] == 107
    assert receipt["computed_basis_fallback_count"] == 214
    assert receipt["normal_datum_gate_accepted"] is False
    candidate_ids = {row["set_id"] for line in path.read_text().splitlines() if (row := json.loads(line)).get("set_id")}
    for old_station, station in zip(before["stations"], release["stations"], strict=True):
        assert {k: v for k, v in station.items() if k != "constant_sets"} == {k: v for k, v in old_station.items() if k != "constant_sets"}
        for old, cs in zip(old_station["constant_sets"], station["constant_sets"], strict=True):
            if cs["set_id"] not in candidate_ids:
                assert cs == old
                continue
            assert {k: v for k, v in cs.items() if k != "datum"} == {k: v for k, v in old.items() if k != "datum"}
            for key, value in old["datum"].get("named", {}).items():
                assert cs["datum"]["named"][key] == value
                assert cs["datum"]["basis"][key] == old["datum"]["basis"][key]
            for level in ("lat", "hat"):
                assert cs["datum"]["basis"][level]["fallback"]["reason"] == "datum_gate_failed"
    for level in ("lat", "hat"):
        final = usable_places(release, level)
        assert len(initial[level]) == 1258
        assert initial[level] <= final
        assert len(final - initial[level]) == 107
        assert len(final) == 1365
    from otc_pipeline.release_writer import validate_release
    assert validate_release(release) == []
