"""C2a: the one committed entry point of the OTC release build (GESLA + TICON-4 + the official agency sources).

    python -m otc_pipeline.build_otc <gesla_build inputs> --agency-cache ROOT --currents-canary-cache DIR \\
        --datestamp YYYYMMDD --created ISO8601 --out OUT          (bin/build-otc runs this)

1. The agency sets (combine.load_agency): kartverket, linz, jma, noaa, noaa-currents from ROOT/<source>, with the
   network blocked, positions corrected (data/otc/position_corrections.json), their speeds read from --tcd, the
   dump the GESLA build fits and publishes with (check_one_table: any source on another dump stops the build).
2. The GESLA build (gesla_build.build_parts: every GESLA and TICON-4 gate as gesla_build runs them), with the
   agency constituent names in the astro table.
3. The canaries (D8): kartverket, linz, jma and noaa against this build's GESLA fits (canary.canary; comparators:
   fits with a clean time base that the station map does not flag); noaa-currents against NOAA's published current
   predictions cached in DIR (currents_canary; fill DIR first with `currents_canary fetch`).
4. The merge (combine.merge): station ids from the committed map (combine mint assigns them), official sets
   recommended, subordinates mapped; D11: every included source must pass its canary.
5. validate_release (0 errors), no set with a disputed time base, then write_release and BUILD_REPORT.json, whose
   `agency` section lists per source what was published, joined and refused, the canaries and the position
   corrections, and whose provenance adds each agency cache's MANIFEST.json hash.

The tree must be committed (gesla_build.head_commit); OUT must be empty.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from gesla.tcd_table import dump_path

from otc_pipeline import combine
from otc_pipeline import currents_canary as cc
from otc_pipeline import gesla_build as gb
from otc_pipeline import gesla_sets as gs
from otc_pipeline import station_map as sm
from otc_pipeline import original_sources
from otc_pipeline import noaa_historical
from otc_pipeline.adapters import fes2022
from otc_pipeline.constituent_speeds import TableSpeeds
from otc_pipeline.chs_identities import load_chs_identities
from otc_pipeline.astro_tables import TABLE_ID
from otc_pipeline.canary import CANARY_CONSTITUENTS, canary
from otc_pipeline.release_writer import validate_release, write_release

GESLA_CANARY_SOURCES = ("kartverket", "linz", "jma", "noaa")
EXCEPTIONS_PATH = sm.DATA / "canary_exceptions.json"


def _has_canary_constituents(ss) -> bool:
    return set(CANARY_CONSTITUENTS) <= {c["name"] for c in (ss.constant_set or {}).get("constituents") or []}


def gesla_comparators(parts: gb.BuildParts) -> list:
    """COMPARATOR ONLY (gesla_sets.comparator_set): this build's GESLA fits with a time base the final audit does
    not dispute, no station-map flag, and all four canary constituents (M2, S2, K1, O1: a fit without one, a short
    record's Rayleigh drop or a microtidal gauge, cannot be compared); a fit that cannot be a comparator (refused,
    short and uncalibrated) is skipped."""
    out = []
    for d in parts.docs:
        rid = d["record_id"]
        if d["time_base"]["code"] == "disputed" or rid in parts.disputed_reasons or parts.sibling_flags.get(rid):
            continue
        try:
            ss = gs.comparator_set(d)
        except (gs.NotPublishable, gs.FitIntegrityError):
            continue
        if _has_canary_constituents(ss):
            out.append(ss)
    return out


def load_canary_exceptions(path: Path = EXCEPTIONS_PATH) -> list[dict]:
    d = json.loads(Path(path).read_text())
    if d.get("format") != 1:
        raise combine.CombineError(f"{path}: format {d.get('format')!r}, expected 1")
    for e in d["exceptions"]:
        if not str(e.get("reason", "")).strip():
            raise combine.CombineError(f"{path}: exception {e} has no reason")
    return list(d["exceptions"])


def run_canaries(agency: dict, comparators: list, currents_cache: Path, *, start: str = cc.START,
                 exceptions: list[dict] | None = None, log=print) -> tuple[dict, dict]:
    """(source -> convention.canary object, source -> full result) for every included source. A source set that
    does not publish all four canary constituents cannot be compared: it fails the source's canary unless it is
    on the reviewed exceptions list (data/otc/canary_exceptions.json, with the same missing constituents); an
    exception that no longer matches a set stops the build. canary.canary checks every pair it makes."""
    exceptions = load_canary_exceptions() if exceptions is None else exceptions
    conv, full = {}, {}
    used = set()
    for src, a in agency.items():
        if src in GESLA_CANARY_SOURCES:
            lacking = [ss for ss in a.sets if ss.constant_set is not None and not _has_canary_constituents(ss)]
            r = canary(src, [ss for ss in a.sets if ss not in lacking], comparators)
            conv[src], full[src] = r.convention_canary(), r.to_dict()
            rows = [{"source_record_id": ss.constant_set["source_record_id"],
                     "missing": sorted(set(CANARY_CONSTITUENTS) - {c["name"] for c in ss.constant_set["constituents"]})}
                    for ss in lacking]
            full[src]["not_compared_missing_constituents"] = rows
            for row in rows:
                ok = next((i for i, e in enumerate(exceptions) if e["source"] == src
                           and e["source_record_id"] == row["source_record_id"]
                           and sorted(e["missing"]) == row["missing"]), None)
                if ok is None:
                    conv[src] = {**conv[src], "status": "fail"}
                    full[src]["passed"] = False
                    full[src]["reasons"] = [*full[src]["reasons"],
                                            f"{row['source_record_id']}: missing {', '.join(row['missing'])}, "
                                            "not compared and not on the canary exceptions list"]
                else:
                    used.add(ok)
        elif src == "noaa-currents":
            r = cc.run_canary(a.sets, currents_cache, start=start)
            conv[src], full[src] = cc.convention_canary(r), r
        else:
            raise combine.CombineError(f"{src}: no canary is defined")
        log(f"canary {src}: {conv[src]}")
    stale = [e for i, e in enumerate(exceptions) if i not in used and e["source"] in agency]
    if stale:
        raise combine.CombineError(f"canary exceptions that match no uncompared set (review them): {stale}")
    return conv, full


def check_one_table(agency: dict, parts: gb.BuildParts) -> None:
    """Every agency source read its speeds from the dump the GESLA build fitted and published with (the same
    file: SHA-256); else CombineError, the build stops."""
    want = parts.provenance["tcd"]["sha256"]
    other = {src: a.tcd_dump_sha256 for src, a in agency.items() if a.tcd_dump_sha256 != want}
    if other:
        raise combine.CombineError(f"agency sources read another TCD dump than the build's {parts.provenance['tcd']['path']} "
                                   f"({want}): {other}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stage_official_datums(release: dict, path: Path, sha256: str) -> dict:
    """Stage pinned official or diagnostic GESLA drafts without changing values."""
    from otc_pipeline.lat_hat import EPOCH, START, END, _finite, computation_input_sha256, datum_draft, low_water_suppressed

    if _sha256(path) != sha256:
        raise ValueError("official datum draft SHA-256 mismatch")
    native = {cs["set_id"]: (station["station_id"], cs) for station in release["stations"]
              for cs in station.get("constant_sets", [])}
    stations = {station["station_id"]: station for station in release["stations"]}
    conventions = {c["convention_id"]: c for c in release.get("conventions", [])}
    tables = {t["astro_table_id"]: t for t in release.get("astro_tables", [])}
    staged, seen, count = [], set(), 0
    for line in path.read_text().splitlines():
        row = json.loads(line)
        if "set_id" not in row:
            continue
        sid = row["set_id"]
        if sid in seen:
            raise ValueError(f"duplicate official datum draft {sid}")
        seen.add(sid)
        station_id, cs = native[sid]
        convention = conventions.get(cs.get("convention_id"), {})
        table = tables.get(convention.get("astro_table_id"))
        if row.get("datum_frame") == "harmonic_mean":
            records = [record for record in stations[station_id].get("source_records", [])
                       if record.get("source_record_id") == cs.get("source_record_id")]
            payload = {"station_id": station_id, "constant_set": cs, "astro_table": table,
                       "datum_frame": "harmonic_mean", "source_records": records}
            raw = row["raw"]
            if (cs.get("source") != "ticon-4" or cs.get("source_type") != "analysis"
                    or cs.get("quantity") != "water_level" or cs.get("qc_status") not in ("ok", "flagged")
                    or table is None or not any(record.get("source") == "ticon" and sid in record.get("published_set_ids", []) for record in records)
                    or row.get("station_id") != station_id or row.get("accepted") is not False
                    or row.get("normal_datum_gate_accepted") is not False
                    or raw.get("datum_frame") != "harmonic_mean" or raw.get("accepted") is not False
                    or (raw.get("start"), raw.get("end")) != (START, END)
                    or row.get("computation_input_sha256") != computation_input_sha256(payload)):
                raise ValueError(f"TICON harmonic_mean computation/source binding mismatch: {sid}")
            if (cs.get("datum") or {}).get("named"):
                raise ValueError(f"harmonic_mean cannot replace existing datum levels: {sid}")
            draft = datum_draft(cs, raw)
            if row.get("draft_datum") != draft:
                raise ValueError(f"TICON draft changed frame, extrema or fallback metadata: {sid}")
            staged.append({"station_id": station_id, "set_id": sid, "source": "ticon-4", "datum": draft,
                           "input_sha256": sha256, "computed_keys": ["lat", "hat"]})
            count += 2
            continue
        if (table is None or not row.get("computation_input_sha256") or
                row["computation_input_sha256"] != computation_input_sha256(
                    {"constant_set": cs, "astro_table": table}) or
                (row["raw"].get("start"), row["raw"].get("end")) != (START, END)):
            raise ValueError(f"official datum computation input mismatch: {sid}")
        gesla = cs["source"] == "gesla-fit" and cs["source_type"] == "gauge"
        if not gesla and (cs["source"] not in ("noaa", "kartverket", "linz") or cs["source_type"] != "official"):
            raise ValueError("only official NOAA, Kartverket and LINZ drafts can be staged")
        if row.get("calibration_status") != ("diagnostic" if gesla else "available"):
            raise ValueError(f"official datum calibration unavailable: {sid}")
        draft = row["draft_datum"]
        for key, value in cs["datum"].items():
            if key not in ("named", "basis") and draft.get(key) != value:
                raise ValueError(f"official datum metadata changed: {sid}:{key}")
        for key, value in cs["datum"].get("named", {}).items():
            if draft["named"].get(key) != value or draft["basis"].get(key) != cs["datum"]["basis"][key]:
                raise ValueError(f"existing official datum changed: {sid}:{key}")
        for level in ("lat", "hat"):
            key = f"otc_{level}" if cs["datum"].get("basis", {}).get(level, {}).get("kind") == "published" else level
            if gesla and level == "lat" and low_water_suppressed(cs):
                if key in draft["basis"] and key not in cs["datum"].get("basis", {}):
                    raise ValueError(f"computed LAT forbidden by known low-water cutoff: {sid}")
                continue
            basis = draft["basis"].get(key, {})
            if (basis.get("kind"), basis.get("method"), basis.get("epoch")) != ("computed", "harmonic_extremes", EPOCH):
                raise ValueError(f"invalid staged computed datum method: {sid}:{key}")
            if draft["named"][key] != row["raw"][f"{level}_m"]:
                raise ValueError(f"staged datum differs from frozen extrema: {sid}:{key}")
            if _finite(basis.get("uncertainty_m"), "staged uncertainty") < 0 or basis.get("uncertainty_basis") not in ("calibrated", "propagated"):
                raise ValueError(f"invalid staged uncertainty: {sid}:{key}")
            if gesla and (basis.get("fallback", {}).get("reason") != "datum_gate_failed"
                          or not basis["fallback"].get("otc_finding")):
                raise ValueError(f"GESLA diagnostic datum requires a gate-failed finding: {sid}:{key}")
            count += key not in cs["datum"].get("basis", {})
        staged.append({"station_id": station_id, "set_id": sid, "source": cs["source"],
                       "datum": copy.deepcopy(draft), "input_sha256": sha256,
                       "computed_keys": [k for k in draft["basis"] if k not in cs["datum"].get("basis", {})
                                         and k in ("lat", "hat", "otc_lat", "otc_hat")]})
    return {"public_admission": False, "release_unchanged": True, "input_path": str(path),
            "input_sha256": sha256, "staged_set_count": len(staged), "staged_level_count": count,
            "finding": "Official tuning drafts only; full DT4a and GATE-DATUM acceptance is not established.",
            "drafts": staged}


def apply_official_datum_fallbacks(release: dict, path: Path, sha256: str) -> dict:
    """Attach labelled computed levels while preserving every existing set and recommendation."""
    staged = stage_official_datums(release, path, sha256)
    return _publish_official_datum_stage(release, staged)


def apply_ticon_harmonic_mean_fallbacks(release: dict, path: Path, sha256: str) -> dict:
    """Attach relative levels and designate LAT only under existing cited conventions."""
    from otc_pipeline.datums import derive_chart_datums

    staged = stage_official_datums(release, path, sha256)
    if any(row["source"] != "ticon-4" or row["datum"].get("zero") != "harmonic_mean" for row in staged["drafts"]):
        raise ValueError("harmonic_mean publication requires retained TICON drafts only")
    native = {cs["set_id"]: cs for station in release["stations"] for cs in station.get("constant_sets", [])}
    for row in staged["drafts"]:
        native[row["set_id"]]["datum"] = copy.deepcopy(row["datum"])
    preview = copy.deepcopy(release)
    derive_chart_datums(preview, include_computed_approximate=False)
    designated = {cs["set_id"]: cs.get("datum", {}).get("chart_datum")
                  for station in preview["stations"] for cs in station.get("constant_sets", [])}
    for row in staged["drafts"]:
        if designated[row["set_id"]] is not None:
            row["datum"]["chart_datum"] = designated[row["set_id"]]
            native[row["set_id"]]["datum"]["chart_datum"] = designated[row["set_id"]]
    return {**staged, "public_admission": True, "release_unchanged": not bool(staged["drafts"]),
            "normal_datum_gate_accepted": False, "accepted": False,
            "computed_basis_fallback_count": staged["staged_level_count"],
            "admission": "same-set harmonic-mean-relative computed fallback; no absolute tie or validated uncertainty"}


def stage_subordinate_datums(release: dict, path: Path, sha256: str, *,
                             calibration: Path, calibration_sha256: str) -> dict:
    """Authenticate NOAA offset drafts; label failed T5s empirical spread explicitly."""
    from otc_pipeline.lat_hat import EPOCH, START, END, _finite
    from otc_pipeline.lat_hat_subordinate import datum_draft, resolve_reference, strict_request

    if _sha256(path) != sha256 or _sha256(calibration) != calibration_sha256:
        raise ValueError("subordinate datum draft/calibration SHA-256 mismatch")
    tuning = json.loads(calibration.read_text())
    if tuning.get("passed") is not False or tuning.get("accepted") is not False:
        raise ValueError("subordinate fallback requires the explicit failed T5s receipt")
    for level in ("lat", "hat"):
        stats = tuning["levels"][level]
        if stats.get("count") != 6 or stats.get("passed") is not False:
            raise ValueError("subordinate fallback requires all six tuning comparisons")
        if _finite(stats.get("sample_sd_m"), "tuning spread") <= 0:
            raise ValueError("subordinate empirical spread must be positive")
        for field in ("signed_bias_m", "median_abs_m", "max_abs_m"):
            _finite(stats.get(field), field)
        if stats["median_abs_m"] <= .03 and stats["max_abs_m"] <= .08:
            raise ValueError("subordinate failed gate receipt disagrees with fixed thresholds")
    stations = {s["station_id"]: s for s in release["stations"]}
    conventions = {c["convention_id"]: c for c in release.get("conventions", [])}
    tables = {t["astro_table_id"]: t for t in release.get("astro_tables", [])}
    staged, seen = [], set()
    wrapper = Path(__file__).with_name("lat_hat_strict.rb")
    for line in path.read_text().splitlines():
        row = json.loads(line)
        sid = row["station_id"]
        if sid in seen:
            raise ValueError(f"duplicate subordinate datum draft: {sid}")
        seen.add(sid)
        station = stations[sid]
        if station.get("status") != "active" or station.get("type") != "subordinate" or not station.get("aliases", {}).get("noaa"):
            raise ValueError(f"active NOAA subordinate required: {sid}")
        offsets = station["subordinate_offsets"]
        reference_station = stations.get(offsets["reference_station_id"], {})
        payload = {"station": station, "reference_station": reference_station}
        reference = resolve_reference(payload)
        table = tables.get(conventions.get(reference.get("convention_id"), {}).get("astro_table_id"))
        if table is None:
            raise ValueError(f"subordinate reference table unavailable: {sid}")
        payload["astro_table"] = table
        request = strict_request(payload)
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True, allow_nan=False).encode()).hexdigest()
        raw = row["raw"]
        if (raw.get("request_sha256") != fingerprint or raw.get("reference_set_id") != reference["set_id"]
                or raw.get("tables_sha256") != table["tables_sha256"] or raw.get("wrapper_sha256") != _sha256(wrapper)
                or (raw.get("start"), raw.get("end")) != (START, END) or raw.get("accepted") is not False):
            raise ValueError(f"subordinate computation input mismatch: {sid}")
        draft = datum_draft(payload, raw)
        if row["draft_datum"] != draft:
            raise ValueError(f"subordinate draft changed metadata, agency levels or frozen extrema: {sid}")
        old = offsets.get("datum", {})
        keys = []
        for level in ("lat", "hat"):
            key = f"otc_{level}" if old.get("basis", {}).get(level, {}).get("kind") == "published" else level
            if key in old.get("named", {}):
                raise ValueError(f"subordinate computed level already exists: {sid}:{key}")
            _finite(raw.get(f"{level}_m"), level)
            stats = tuning["levels"][level]
            draft["basis"][key] = {"kind": "computed", "method": "subordinate_offsets", "epoch": dict(EPOCH),
                "uncertainty_m": stats["sample_sd_m"], "uncertainty_basis": "calibrated",
                "fallback": {"reason": "datum_gate_failed",
                    "basis": "NOAA offsets on the published reference chart origin; empirical six-case tuning spread only",
                    "otc_finding": f"UNRELIABLE approximate derived-from-offsets {level.upper()}. T5s FAILED: median/max absolute residual {stats['median_abs_m']}/{stats['max_abs_m']} m exceed fixed 0.03/0.08 m limits. Empirical tuning sample SD {stats['sample_sd_m']} m, signed bias {stats['signed_bias_m']} m, n=6. Not independently validated station accuracy or a confidence bound. Normal datum gate not accepted."}}
            keys.append(key)
        staged.append({"station_id": sid, "datum": draft, "computed_keys": keys})
    return {"public_admission": False, "release_unchanged": True, "accepted": False,
            "normal_datum_gate_accepted": False, "input_path": str(path), "input_sha256": sha256,
            "calibration_path": str(calibration), "calibration_sha256": calibration_sha256,
            "tuning": tuning, "staged_station_count": len(staged), "staged_level_count": 2 * len(staged),
            "drafts": staged}


def apply_subordinate_datum_fallbacks(release: dict, path: Path, sha256: str, **inputs) -> dict:
    """Publish only authenticated subordinate levels, preserving all native payloads."""
    staged = stage_subordinate_datums(release, path, sha256, **inputs)
    stations = {s["station_id"]: s for s in release["stations"]}
    for entry in staged["drafts"]:
        stations[entry["station_id"]]["subordinate_offsets"]["datum"] = copy.deepcopy(entry["datum"])
    return {**staged, "public_admission": True, "release_unchanged": False,
            "computed_basis_fallback_count": staged["staged_level_count"],
            "admission": "UNRELIABLE approximate subordinate datum fallback only"}


def _publish_official_datum_stage(release: dict, staged: dict, *, finalize_chart: bool = True) -> dict:
    from otc_pipeline.datums import derive_chart_datums
    from otc_pipeline.lat_hat import COMPUTED_FALLBACK
    by_id = {cs["set_id"]: cs for station in release["stations"] for cs in station.get("constant_sets", [])}
    count = 0
    for entry in staged["drafts"]:
        datum = entry["datum"]
        for key in entry["computed_keys"]:
            if entry["source"] not in ("gesla-fit", "ticon-4"):
                datum["basis"][key]["fallback"] = dict(COMPUTED_FALLBACK)
            count += 1
        by_id[entry["set_id"]]["datum"] = datum
    # Authentication previews and a following observed publication retain the
    # historical computed view. An official-only publication is already final.
    chart_datum_derivation = derive_chart_datums(release, include_computed_approximate=finalize_chart)
    return {**staged, "public_admission": True, "release_unchanged": False,
            "normal_datum_gate_accepted": False, "computed_basis_fallback_count": count,
            "chart_datum_derivation": chart_datum_derivation,
            "admission": "labelled computed datum fallback only"}


class _ObservedCurrentInputMismatch(ValueError):
    """The authenticated draft does not match this current native publication view."""


def stage_observed_datums(release: dict, path: Path, sha256: str, *, source_release: Path,
                          source_release_sha256: str, gesla_root: Path, fit_run: Path, tcd: Path) -> dict:
    """Authenticate frozen reductions against original bytes and current native scientific inputs."""
    from gesla.read_gesla import GeslaRelease
    from jsonschema import Draft202012Validator
    from otc_pipeline.datums import datum_build_errors, derive_chart_datums, observed_companions
    from otc_pipeline.observed_datums import produce
    from otc_pipeline.release_writer import pinned_schema

    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError("observed datum draft SHA-256 mismatch")
    if produce.sha256(source_release) != source_release_sha256:
        raise ValueError("observed source release SHA-256 mismatch")
    rows = [json.loads(line) for line in data.decode().splitlines() if line.strip()]
    required = {"station_id", "set_id", "source_record_id", "input_binding", "draft_datum", "observed_keys"}
    if any(not isinstance(row, dict) or set(row) != required for row in rows):
        raise ValueError("observed datum row requires identity, binding, draft and observed keys")
    ids = [row["set_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate observed datum draft binding")
    wanted = set(ids)
    native = {}
    for station in release["stations"]:
        for cs in station.get("constant_sets", []):
            sid = cs["set_id"]
            if sid in native:
                raise ValueError(f"duplicate current observed set binding: {sid}")
            native[sid] = (station, cs)
    historical = {}
    with source_release.open() as stream:
        for line in stream:
            if not line.strip():
                continue
            station = json.loads(line)
            for cs in station.get("constant_sets", []):
                sid = cs["set_id"]
                if sid in wanted:
                    if sid in historical:
                        raise ValueError(f"duplicate source release observed binding: {sid}")
                    historical[sid] = (station, cs)
    originals = GeslaRelease(gesla_root)
    manifest_path = fit_run / "timebase_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    audit = gs.load_audit(Path(manifest["inputs"]["timebase_csv"]["path"]).parent)
    dump = gs.load_tcd_dump(tcd)
    manifest, fit_rows = gs.check_fit_run(fit_run, dump, originals, audit)
    common = {"release_sha256": source_release_sha256, "fit_manifest_sha256": produce.sha256(manifest_path),
              "results_sha256": produce.sha256(fit_run / "results.jsonl"), "tcd_sha256": dump.sha256,
              "gesla_manifest_sha256": produce.sha256(gesla_root / "MANIFEST.json"),
              "timebase_sha256": audit.csv_sha256}
    schema = pinned_schema()
    validator = Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/datum"})
    staged, unavailable, verified_members = [], [], set()
    for row in rows:
        sid, rid = row["set_id"], row["source_record_id"]
        if sid not in native or sid not in historical:
            raise ValueError(f"foreign observed datum binding: {sid}")
        station, cs = native[sid]
        old_station, old_cs = historical[sid]
        if (cs.get("source") not in (gs.SOURCE, "ticon-4") or row["station_id"] != station["station_id"]
                or rid != cs.get("source_record_id") or row["station_id"] != old_station["station_id"]
                or rid != old_cs.get("source_record_id")
                or any(station.get(key) != old_station.get(key) for key in ("lat", "lon", "country"))):
            raise ValueError(f"observed source identity binding mismatch: {sid}")
        member = originals.members.get(rid)
        fit_path = fit_run / "fits" / f"{rid}.json"
        fit_sha = produce.sha256(fit_path) if fit_path.is_file() else None
        bound = produce.input_binding(old_station, old_cs, member=member, fit_sha256=fit_sha, **common)
        if row["input_binding"] != bound:
            raise ValueError(f"observed historical input binding mismatch: {sid}")
        # Only this generated build stamp may differ. Audit decisions and every other
        # provenance field remain part of the native scientific binding.
        current = copy.deepcopy(cs)
        build = current.get("provenance", {}).get("build")
        old_build = old_cs.get("provenance", {}).get("build", {})
        if isinstance(build, dict) and "build_commit" in build and "build_commit" in old_build:
            build["build_commit"] = old_build["build_commit"]
        if (current.get("datum", {}).get("chart_datum") == "lat"
                and old_cs.get("datum", {}).get("chart_datum") is None):
            # A serial official publication may already have finalized the new
            # computed reference. Replay that exact policy on the authentic old
            # datum; accept no other change to values, findings or metadata.
            preview_station = copy.deepcopy(old_station)
            preview_cs = next(c for c in preview_station["constant_sets"] if c["set_id"] == sid)
            preview_cs["datum"] = copy.deepcopy(old_cs["datum"])
            preview = {"stations": [preview_station]}
            derive_chart_datums(preview, include_computed_approximate=False)
            unchanged = preview_cs["datum"] == old_cs["datum"]
            derived = derive_chart_datums(preview)
            if (unchanged and derived["approximate_changed_sets"]
                    and preview_cs["datum"] == current["datum"]):
                current["datum"] = copy.deepcopy(old_cs["datum"])
        if produce.input_binding(station, current, member=member, fit_sha256=fit_sha, **common) != bound:
            raise _ObservedCurrentInputMismatch(f"observed current input binding mismatch: {sid}")
        if member and rid not in verified_members:
            originals.read_member(rid)  # verifies actual original bytes, not only manifest identity
            verified_members.add(rid)
        if row["draft_datum"] is None:
            if row["observed_keys"] != []:
                raise ValueError(f"unavailable observed draft has values: {sid}")
            unavailable.append(copy.deepcopy(row))
            continue
        if (cs["source"] != gs.SOURCE or member is None or cs.get("quantity") != "water_level"
                or cs.get("qc_status") == "no_constants" or not cs.get("constituents")
                or any(issue.get("issue") == "no_tide" for issue in cs.get("record_issues", []))):
            raise ValueError(f"observed original zero/measurement unavailable: {sid}")
        if fit_sha != manifest["fits_sha256"].get(rid) or fit_rows.get(rid, {}).get("outcome") != "fitted":
            raise ValueError(f"observed completed fit binding mismatch: {sid}")
        fit = json.loads(fit_path.read_text())
        correction = gs.published_correction(audit.rows.get(rid), gs.declared_hours(originals.metadata()[rid]))
        if correction.label != cs.get("time_base", {}).get("published"):
            raise ValueError(f"observed published clock binding mismatch: {sid}")
        gs.check_stamp(rid, fit, correction.label, corr=correction, speed=lambda name: dump.table[name].speed,
                       span=(fit["first_s"], fit["last_s"] + 3600))
        draft, keys = row["draft_datum"], row["observed_keys"]
        if not isinstance(draft, dict) or not isinstance(keys, list) or not keys:
            raise ValueError(f"observed draft has no declared levels: {sid}")
        for key in keys:
            basis = draft.get("basis", {}).get(key, {})
            fallback = basis.get("fallback", {})
            finding = fallback.get("otc_finding", "")
            if (basis.get("kind") != "observed" or fallback.get("reason") != "datum_gate_failed"
                    or "Scientific T1 FAIL; normal acceptance false" not in finding
                    or produce.DIAGNOSTIC_SHA256 not in finding or "uncertainty_m" in basis
                    or "uncertainty_basis" in basis):
                raise ValueError(f"invalid observed gate-failed diagnostic: {sid}:{key}")
        datum = observed_companions(cs.get("datum", {}), draft, keys, truncated_lows=produce.cut_off_lows(cs))
        json.dumps(datum, allow_nan=False)
        errors = list(validator.iter_errors(datum))
        if errors or datum_build_errors(datum):
            raise ValueError(f"invalid observed datum contract: {sid}: {errors or datum_build_errors(datum)}")
        staged.append({"station_id": station["station_id"], "set_id": sid, "datum": datum,
                       "observed_keys": list(keys), "input_binding": copy.deepcopy(bound)})
    if produce.sha256(source_release) != source_release_sha256:
        raise ValueError("observed source release changed during staging")
    return {"public_admission": False, "release_unchanged": True, "normal_datum_gate_accepted": False,
            "input_path": str(path), "input_sha256": sha256, "source_release_path": str(source_release),
            "source_release_sha256": source_release_sha256, "staged_set_count": len(staged),
            "staged_level_count": sum(len(row["observed_keys"]) for row in staged),
            "unavailable_set_count": len(unavailable), "unavailable": unavailable, "drafts": staged}


def apply_observed_datum_fallbacks(release: dict, path: Path, sha256: str, **inputs) -> dict:
    """Attach only authenticated new observed levels, retaining unrelated set and station fields."""
    staged = stage_observed_datums(release, path, sha256, **inputs)
    return _publish_observed_datum_stage(release, staged)


def _publish_observed_datum_stage(release: dict, staged: dict) -> dict:
    from otc_pipeline.datums import derive_chart_datums, observed_companions

    by_id = {cs["set_id"]: cs for station in release["stations"] for cs in station.get("constant_sets", [])}
    pending = []
    for entry in staged["drafts"]:
        # Other authenticated stages may already have added disjoint computed
        # levels and a chart pointer. Attach only this stage's new observed keys.
        keys = entry["observed_keys"]
        draft = {field: {key: entry["datum"][field][key] for key in keys} for field in ("named", "basis")}
        datum = observed_companions(by_id[entry["set_id"]].get("datum", {}), draft, keys)
        pending.append((entry, datum))
    for entry, datum in pending:
        by_id[entry["set_id"]]["datum"] = datum
        entry["datum"] = datum
    chart_datum_derivation = derive_chart_datums(release)
    return {**staged, "public_admission": True,
            "release_unchanged": not bool(staged["drafts"]) and not bool(chart_datum_derivation["changed_sets"]),
            "observed_basis_fallback_count": staged["staged_level_count"],
            "chart_datum_derivation": chart_datum_derivation,
            "admission": "labelled observed datum fallback only"}


def _official_datum_view(release: dict, staged: dict) -> dict:
    """Preview only authenticated computed additions and cited chart designation."""
    native = {cs["set_id"]: cs for station in release["stations"] for cs in station.get("constant_sets", [])}
    for entry in staged["drafts"]:
        old = native[entry["set_id"]].get("datum", {})
        draft = entry["datum"]
        if ({k: v for k, v in old.items() if k not in ("named", "basis")} !=
                {k: v for k, v in draft.items() if k not in ("named", "basis")}
                or set(draft.get("named", {})) - set(old.get("named", {})) != set(entry["computed_keys"])
                or set(draft.get("basis", {})) - set(old.get("basis", {})) != set(entry["computed_keys"])):
            raise ValueError(f"official publication view requires only computed level additions: {entry['set_id']}")
    # Copy the mutable datum containers only. Source, harmonic and station context
    # remains identical; this preview never mutates the caller's release.
    stations = []
    for station in release["stations"]:
        copied = dict(station)
        if "constant_sets" in station:
            copied["constant_sets"] = [
                {**cs, "datum": copy.deepcopy(cs["datum"])} if "datum" in cs else dict(cs)
                for cs in station["constant_sets"]]
        stations.append(copied)
    preview = {**release, "stations": stations}
    _publish_official_datum_stage(preview, copy.deepcopy(staged), finalize_chart=False)
    return preview


def apply_datum_drafts(release: dict, *, official: tuple | None = None, observed: tuple | None = None,
                       publish_official: bool = False, publish_observed: bool = False,
                       observed_inputs: dict | None = None) -> tuple[dict | None, dict | None]:
    """Authenticate exact native or official-published views before merging disjoint additions."""
    official_stage = stage_official_datums(release, *official) if official is not None else None
    observed_stage = None
    if observed is not None:
        try:
            observed_stage = stage_observed_datums(release, *observed, **(observed_inputs or {}))
        except _ObservedCurrentInputMismatch:
            if not publish_official or official_stage is None:
                raise
            # Older accepted drafts bind the post-computed source release. Verify
            # that exact view too; no binding field or historical hash is ignored.
            preview = _official_datum_view(release, official_stage)
            observed_stage = stage_observed_datums(preview, *observed, **(observed_inputs or {}))
    if publish_official and publish_observed and official_stage is not None and observed_stage is not None:
        observed_keys = {row["set_id"]: set(row["observed_keys"]) for row in observed_stage["drafts"]}
        # Reject overlaps before either group mutates the release, including an
        # unexpected extra named key in a computed draft.
        for row in official_stage["drafts"]:
            if set(row["datum"].get("named", {})) & observed_keys.get(row["set_id"], set()):
                raise ValueError(f"conflicting official and observed datum drafts: {row['set_id']}")
    if publish_official and official_stage is not None:
        official_stage = _publish_official_datum_stage(release, official_stage,
            finalize_chart=not (publish_observed and observed_stage is not None))
    if publish_observed and observed_stage is not None:
        observed_stage = _publish_observed_datum_stage(release, observed_stage)
    return official_stage, observed_stage


def cached_subordinate_main(argv: list[str]) -> int:
    """Publish authenticated subordinate additions to an existing native release."""
    from otc_pipeline.astro_tables import load_congen_run

    parser = argparse.ArgumentParser(prog="otc_pipeline.build_otc --cached-subordinate", allow_abbrev=False)
    for name in ("native", "subordinate-drafts", "tuning", "congen-run", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    for name in ("native-sha256", "native-meta-sha256", "subordinate-drafts-sha256", "tuning-sha256"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args(argv)
    if args.out.exists() and any(args.out.iterdir()):
        raise ValueError("cached subordinate output must be empty")
    meta_path = args.native.with_suffix(".meta.json")
    if _sha256(args.native) != args.native_sha256 or _sha256(meta_path) != args.native_meta_sha256:
        raise ValueError("cached native release SHA-256 mismatch")
    release = json.loads(meta_path.read_text())
    with args.native.open() as source:
        release["stations"] = [json.loads(line) for line in source]
    station_count = len(release["stations"])
    source_record_count = sum(len(station.get("source_records", [])) for station in release["stations"])
    staged = apply_subordinate_datum_fallbacks(release, args.subordinate_drafts, args.subordinate_drafts_sha256,
        calibration=args.tuning, calibration_sha256=args.tuning_sha256)
    if _sha256(args.native) != args.native_sha256 or _sha256(meta_path) != args.native_meta_sha256:
        raise ValueError("cached native release changed during staging")
    congen = load_congen_run(args.congen_run)
    files = write_release(release, args.out, datestamp=release["release"]["datestamp"],
                          app_export=None, congen_run=congen)
    (args.out / "SUBORDINATE_DATUM_STAGE.json").write_text(json.dumps(staged, allow_nan=False, sort_keys=True) + "\n")
    report = {"mode": "cached_subordinate_only", "native": str(args.native), "native_sha256": args.native_sha256,
              "native_meta_sha256": args.native_meta_sha256, "stations_preserved": station_count,
              "source_records_preserved": source_record_count, "normal_datum_gate_accepted": False,
              "accepted": False, "computed_basis_fallback_count": staged["computed_basis_fallback_count"],
              "files": [str(path) for path in files]}
    (args.out / "CACHED_SUBORDINATE_REPORT.json").write_text(json.dumps(report, allow_nan=False, sort_keys=True) + "\n")
    print(json.dumps(report, allow_nan=False, sort_keys=True))
    return 0


def cached_harmonic_mean_main(argv: list[str]) -> int:
    """Write the existing native release with authenticated relative TICON additions."""
    from otc_pipeline.astro_tables import load_congen_run

    parser = argparse.ArgumentParser(prog="otc_pipeline.build_otc --cached-harmonic-mean", allow_abbrev=False)
    for name in ("native", "harmonic-mean-drafts", "congen-run", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    for name in ("native-sha256", "native-meta-sha256", "harmonic-mean-drafts-sha256"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args(argv)
    if args.out.exists() and any(args.out.iterdir()):
        raise ValueError("cached harmonic_mean output must be empty")
    meta_path = args.native.with_suffix(".meta.json")
    if _sha256(args.native) != args.native_sha256 or _sha256(meta_path) != args.native_meta_sha256:
        raise ValueError("cached native release SHA-256 mismatch")
    release = json.loads(meta_path.read_text())
    with args.native.open() as source:
        release["stations"] = [json.loads(line) for line in source]
    staged = apply_ticon_harmonic_mean_fallbacks(release, args.harmonic_mean_drafts, args.harmonic_mean_drafts_sha256)
    if _sha256(args.native) != args.native_sha256 or _sha256(meta_path) != args.native_meta_sha256:
        raise ValueError("cached native release changed during staging")
    files = write_release(release, args.out, datestamp=release["release"]["datestamp"], app_export=None,
                          congen_run=load_congen_run(args.congen_run))
    (args.out / "HARMONIC_MEAN_DATUM_STAGE.json").write_text(json.dumps(staged, allow_nan=False, sort_keys=True) + "\n")
    print(json.dumps({"normal_datum_gate_accepted": False, "accepted": False,
                      "staged_set_count": staged["staged_set_count"], "computed_basis_fallback_count": staged["computed_basis_fallback_count"],
                      "native_sha256": args.native_sha256, "files": [str(path) for path in files]}, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "--cached-subordinate":
        return cached_subordinate_main(argv[1:])
    if argv and argv[0] == "--cached-harmonic-mean":
        return cached_harmonic_mean_main(argv[1:])
    ap = argparse.ArgumentParser(prog="otc_pipeline.build_otc", description=(__doc__ or "").split("\n")[0],
                                 allow_abbrev=False)
    for name in ("fit-run", "audit-dir", "verdicts-dir", "calibration", "ticon-csv", "ticon-refs", "congen-run",
                 "agency-cache", "currents-canary-cache", "out"):
        ap.add_argument(f"--{name}", type=Path, required=True)
    ap.add_argument("--overlay-run", type=Path)
    ap.add_argument("--tcd", type=Path, help="the TCD dump of the fits, the astro table and the agency sets "
                    "(default gesla.tcd_table.dump_path(): $TCD_TABLE or $OTC_TCD_TABLE, else $OTC_WORK/tcd_table.json)")
    ap.add_argument("--currents-canary-start", default=cc.START)
    ap.add_argument("--datestamp", required=True)
    ap.add_argument("--created", required=True)
    ap.add_argument("--chs-identities", type=Path)
    ap.add_argument("--chs-identities-sha256")
    ap.add_argument("--source-quality-findings", type=Path)
    ap.add_argument("--source-quality-findings-sha256")
    ap.add_argument("--original-derived-inputs", type=Path)
    ap.add_argument("--original-derived-inputs-sha256")
    ap.add_argument("--country-resolutions", type=Path)
    ap.add_argument("--country-resolutions-sha256")
    ap.add_argument("--noaa-historical-inputs", type=Path)
    ap.add_argument("--noaa-historical-inputs-sha256")
    ap.add_argument("--noaa-historical-comparison", type=Path)
    ap.add_argument("--noaa-historical-comparison-sha256")
    ap.add_argument("--fes-cache", type=Path, help="private, complete FES2022b ocean input cache")
    ap.add_argument("--fes-manifest-sha256", help=f"SHA-256 of that cache's {fes2022.OCEAN_MANIFEST_NAME}")
    ap.add_argument("--official-datum-drafts", type=Path,
                    help="stage pinned official computed drafts in DATUM_STAGE.json; release values stay unchanged")
    ap.add_argument("--official-datum-drafts-sha256")
    ap.add_argument("--publish-labelled-official-datums", action="store_true",
                    help="attach the staged official levels with per-level datum_gate_failed findings")
    ap.add_argument("--observed-datum-drafts", type=Path,
                    help="stage source-bound observed reductions without changing release values")
    ap.add_argument("--observed-datum-drafts-sha256")
    ap.add_argument("--observed-datum-source-release", type=Path,
                    help="the immutable native JSONL input used to reduce the observed drafts")
    ap.add_argument("--observed-datum-source-release-sha256")
    ap.add_argument("--publish-labelled-observed-datums", action="store_true",
                    help="attach pinned observed levels with their per-level datum_gate_failed findings")
    gb.inventory_arguments(ap)
    argv = sys.argv[1:] if argv is None else list(argv)
    a = ap.parse_args(argv)
    if (a.fes_cache is None) != (a.fes_manifest_sha256 is None):
        raise ValueError("--fes-cache and --fes-manifest-sha256 must be supplied together")
    if a.fes_cache is not None and _sha256(a.fes_cache / fes2022.OCEAN_MANIFEST_NAME) != a.fes_manifest_sha256:
        raise ValueError("FES manifest SHA-256 mismatch")
    observed_inputs = (a.observed_datum_drafts, a.observed_datum_drafts_sha256,
                       a.observed_datum_source_release, a.observed_datum_source_release_sha256)
    if any(value is not None for value in observed_inputs) and any(value is None for value in observed_inputs):
        raise ValueError("observed datum drafts and source release require both paths and SHA-256 pins")
    if a.publish_labelled_observed_datums and a.observed_datum_drafts is None:
        raise ValueError("labelled observed datums require pinned drafts and source release")
    if (a.official_datum_drafts is None) != (a.official_datum_drafts_sha256 is None):
        raise ValueError("--official-datum-drafts and --official-datum-drafts-sha256 must be supplied together")
    if a.publish_labelled_official_datums and a.official_datum_drafts is None:
        raise ValueError("labelled official datums require the pinned --official-datum-drafts input")
    inventory = gb.inventory_from_arguments(a)
    if (a.chs_identities is None) != (a.chs_identities_sha256 is None):
        raise ValueError("--chs-identities and --chs-identities-sha256 must be supplied together")
    if (a.source_quality_findings is None) != (a.source_quality_findings_sha256 is None):
        raise ValueError("--source-quality-findings and --source-quality-findings-sha256 must be supplied together")
    if (a.original_derived_inputs is None) != (a.original_derived_inputs_sha256 is None):
        raise ValueError("--original-derived-inputs and --original-derived-inputs-sha256 must be supplied together")
    if (a.country_resolutions is None) != (a.country_resolutions_sha256 is None):
        raise ValueError("--country-resolutions and --country-resolutions-sha256 must be supplied together")
    if (a.noaa_historical_inputs is None) != (a.noaa_historical_inputs_sha256 is None):
        raise ValueError("--noaa-historical-inputs and --noaa-historical-inputs-sha256 must be supplied together")
    if (a.noaa_historical_comparison is None) != (a.noaa_historical_comparison_sha256 is None):
        raise ValueError("--noaa-historical-comparison and --noaa-historical-comparison-sha256 must be supplied together")
    if a.noaa_historical_comparison is not None and a.noaa_historical_inputs is None:
        raise ValueError("historical comparison requires the pinned historical source inputs")
    if inventory is None and (a.chs_identities is not None or a.source_quality_findings is not None
                              or a.original_derived_inputs is not None or a.country_resolutions is not None or a.noaa_historical_inputs is not None):
        raise ValueError("original source publication inputs require the actual pinned --source-inventory")
    chs = None
    if a.chs_identities is not None:
        if _sha256(a.chs_identities / "SHA256SUMS") != a.chs_identities_sha256:
            raise ValueError("CHS identity inventory SHA-256 mismatch")
        chs = load_chs_identities(a.chs_identities)
    if a.source_quality_findings is not None and _sha256(a.source_quality_findings) != a.source_quality_findings_sha256:
        raise ValueError("source quality findings SHA-256 mismatch")
    if a.original_derived_inputs is not None and _sha256(a.original_derived_inputs) != a.original_derived_inputs_sha256:
        raise ValueError("original derived-input manifest SHA-256 mismatch")
    if a.country_resolutions is not None and _sha256(a.country_resolutions) != a.country_resolutions_sha256:
        raise ValueError("country resolution cache SHA-256 mismatch")
    historical_inputs = None
    if a.noaa_historical_inputs is not None:
        historical_inputs = noaa_historical.load_noaa_historical_inputs(
            a.noaa_historical_inputs, a.noaa_historical_inputs_sha256)
    historical_comparison = None
    if a.noaa_historical_comparison is not None:
        historical_comparison = noaa_historical.load_noaa_historical_comparison(
            a.noaa_historical_comparison, a.noaa_historical_comparison_sha256)
    t0 = time.time()

    def log(msg: str) -> None:
        gb._log(t0, msg)
    if historical_inputs is not None:
        log(f"noaa historical inputs: {len(historical_inputs.profiles)} profiles, "
            f"{len(historical_inputs.members)} verified members")
    if a.out.exists() and any(a.out.iterdir()):
        raise SystemExit(f"{a.out}: not empty")
    commit = gb.head_commit()
    log(f"build_commit {commit}")
    tcd = a.tcd or dump_path()
    model_adapter = fes2022.Fes2022Adapter(speeds=TableSpeeds.from_tcd_dump(tcd),
        manifest_sha256=a.fes_manifest_sha256) if a.fes_cache is not None else None
    agency = combine.load_agency(a.agency_cache, jma_year=int(a.datestamp[:4]), tcd_path=tcd)
    log("agency sets: " + ", ".join(f"{s} {len(x.sets)}" for s, x in agency.items()))
    parts = gb.build_parts(fit_run=a.fit_run, audit_dir=a.audit_dir, verdicts_dir=a.verdicts_dir,
                           calibration_dir=a.calibration, overlay_run=a.overlay_run, ticon_csv=a.ticon_csv,
                           ticon_refs=a.ticon_refs, congen_run=a.congen_run, datestamp=a.datestamp,
                           created=a.created, build_commit=commit, tcd_path=tcd,
                           extra_names=combine.agency_names(agency) | (model_adapter.supported_names() if model_adapter else set()),
                           inventory=inventory, log=log)
    log(f"gesla build_release: {parts.report.summary()}")
    check_one_table(agency, parts)
    if model_adapter and model_adapter.speeds.dump_sha256 != parts.provenance["tcd"]["sha256"]:
        raise ValueError("FES adapter and build must use the same TCD dump")
    log(f"one TCD dump: {parts.provenance['tcd']['sha256'][:12]} (fits, astro table and every agency source)")
    comparators = gesla_comparators(parts)
    log(f"canary comparators (GESLA fits): {len(comparators)}")
    conv, full = run_canaries(agency, comparators, a.currents_canary_cache, start=a.currents_canary_start, log=log)
    release, arep = combine.merge(parts.release, agency, station_ids=sm.load().record_ids(), build_commit=commit,
                                  table_id=TABLE_ID, canaries=conv)
    arep.canaries = full
    log("agency: " + json.dumps(arep.sources, sort_keys=True))
    original_retention = quality_findings = derived_inputs = country_resolutions = None
    if inventory is not None:
        from otc_pipeline.original_metadata import load_original_metadata
        metadata = load_original_metadata(inventory, tcd_path=tcd, datestamp=a.datestamp)
        original_retention = original_sources.retain_original_sources(
            release, inventory, fit_run=a.fit_run, docs=parts.docs, report=parts.report,
            source_metadata=metadata, chs=chs)
        log("original source retention: " + json.dumps({k: v for k, v in original_retention.items() if k != "chs"}, sort_keys=True))
        if a.original_derived_inputs is not None:
            derived_inputs = original_sources.retain_derived_inputs(
                release, path=a.original_derived_inputs, sha256=a.original_derived_inputs_sha256)
            log(f"original historical fit documents: {derived_inputs['original_documents']}")
        if a.source_quality_findings is not None:
            quality_findings = original_sources.apply_quality_findings(
                release, inventory, path=a.source_quality_findings, sha256=a.source_quality_findings_sha256)
            log("source quality findings: " + json.dumps(quality_findings, sort_keys=True))
        original_sources.require_country_resolution_cache(release, a.country_resolutions)
        if a.country_resolutions is not None:
            country_resolutions = original_sources.apply_country_resolution_cache(
                release, path=a.country_resolutions, sha256=a.country_resolutions_sha256)
            log("country resolutions: " + json.dumps(country_resolutions, sort_keys=True))
    historical_augmentation = None
    if historical_inputs is not None:
        historical_augmentation = noaa_historical.apply_noaa_historical_offsets(
            release, historical_inputs, build_commit=commit, comparison=historical_comparison)
        log("noaa historical augmentation: " + json.dumps(
            {key: value for key, value in historical_augmentation.items() if key not in {"members", "input_roles"}},
            sort_keys=True))
    datum_stage, observed_stage = apply_datum_drafts(release,
        official=(a.official_datum_drafts, a.official_datum_drafts_sha256) if a.official_datum_drafts is not None else None,
        observed=(a.observed_datum_drafts, a.observed_datum_drafts_sha256) if a.observed_datum_drafts is not None else None,
        publish_official=a.publish_labelled_official_datums, publish_observed=a.publish_labelled_observed_datums,
        observed_inputs={"source_release": a.observed_datum_source_release,
                         "source_release_sha256": a.observed_datum_source_release_sha256,
                         "gesla_root": gs.GeslaRelease().root, "fit_run": a.fit_run, "tcd": tcd}
                        if a.observed_datum_drafts is not None else None)
    if datum_stage is not None:
        log(f"official datums: {datum_stage['staged_set_count']} sets; public_admission={datum_stage['public_admission']}")
    if observed_stage is not None:
        log(f"observed datums: {observed_stage['staged_set_count']} sets; public_admission={observed_stage['public_admission']}")
    model_report = None
    if model_adapter is not None:
        model_adapter.stations = fes2022.station_roster(release)
        log(f"FES2022b extraction: {len(model_adapter.stations)} existing station points")
        model_fetched = model_adapter.fetch(a.fes_cache)
        model_sets = list(model_adapter.sets(model_fetched))
        # Frozen actual gauge cohort, including explicitly unavailable point outcomes.
        native_ids = {"8443970", "9447130", "9414290", "9410170", "8724580"}
        cohort = [ss for ss in model_sets if ss.station_hint["aliases"].get("noaa") in native_ids]
        model_canary = canary("fes2022", cohort, comparators)
        model_report = fes2022.attach(release, model_fetched, build_commit=commit, table_id=TABLE_ID,
                                     canary_result=model_canary, adapter=model_adapter)
        model_report["manifest_sha256"] = a.fes_manifest_sha256
        model_report["tcd_dump_sha256"] = model_adapter.speeds.dump_sha256
        model_report["excluded_station_count"] = len(release["stations"]) - len(model_adapter.stations)
        log(f"FES2022b: {model_report['sets_added']} additive model sets, "
            f"{len(model_report['unavailable'])} unavailable point records; canary={model_canary.passed}")
    errs = validate_release(release)
    log(f"validate_release: {len(errs)} errors")
    for e in errs[:20]:
        print("  ", e)
    disputed = [cs["set_id"] for st in release["stations"] if st["status"] == "active" for cs in st["constant_sets"]
                if (cs.get("time_base") or {}).get("code") == "disputed" and cs.get("qc_status") != "no_constants"
                and not cs.get("fallback")]
    log(f"unlabelled sets with a disputed time base: {len(disputed)}")
    if errs or disputed:
        return 1
    a.out.mkdir(parents=True, exist_ok=True)
    files = write_release(release, a.out, datestamp=a.datestamp, app_export=None, congen_run=parts.congen_run)
    if datum_stage is not None:
        (a.out / "DATUM_STAGE.json").write_text(json.dumps(datum_stage, allow_nan=False, sort_keys=True) + "\n")
    if observed_stage is not None:
        (a.out / "OBSERVED_DATUM_STAGE.json").write_text(json.dumps(observed_stage, allow_nan=False, sort_keys=True) + "\n")
    provenance = {"command_line": [sys.executable, "-m", "otc_pipeline.build_otc", *argv], "cwd": os.getcwd(),
                  **parts.provenance,
                  "agency_cache": {s: {"path": str((a.agency_cache / s).resolve()),
                                       "manifest_sha256": _sha256(a.agency_cache / s / "MANIFEST.json")}
                                   for s in agency},
                  "agency_tcd_dump_sha256": {s: x.tcd_dump_sha256 for s, x in agency.items()},
                  "position_corrections_sha256": _sha256(combine.CORRECTIONS_PATH),
                  "canary_exceptions_sha256": _sha256(EXCEPTIONS_PATH),
                  "currents_canary_cache": str(a.currents_canary_cache.resolve())}
    gb.write_report(parts.report, provenance, a.out / "BUILD_REPORT.json", extra={"agency": arep.as_dict(),
                    "original_source_retention": original_retention, "source_quality_findings": quality_findings,
                    "original_derived_inputs": derived_inputs, "country_resolutions": country_resolutions,
                    "noaa_historical_augmentation": historical_augmentation,
                    **({"fes2022": model_report} if model_report is not None else {}),
                    **({"official_datum_stage": {key: value for key, value in datum_stage.items() if key != "drafts"}}
                       if datum_stage is not None else {}),
                    **({"observed_datum_stage": {key: value for key, value in observed_stage.items()
                                                 if key not in ("drafts", "unavailable")}}
                       if observed_stage is not None else {})},
                    release=release, inventory=inventory)
    log(f"wrote {[p.name for p in files]} + BUILD_REPORT.json to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
