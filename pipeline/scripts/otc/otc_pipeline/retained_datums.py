"""Source-bound numerical alternatives, independent of recommended prediction datums.

No frame conversion, alias inference, datum computation, or acceptance promotion.
Repeated names in an original response remain separate candidates.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def candidate_errors(candidate, record, own_sets):
    """Identity and interval checks additional to the independent carrier schema."""
    errors = []
    if candidate.get("source_record_id") != record.get("source_record_id"):
        errors.append("retained datum source_record_id differs from its original tuple")
    sid = candidate.get("set_id")
    if candidate.get("kind") == "computed" and not sid:
        errors.append("computed retained datum requires its actual set_id")
    if sid is not None:
        cs = own_sets.get(sid)
        source = {"gesla": "gesla-fit", "ticon": "ticon-4"}.get(record.get("source"), record.get("source"))
        if cs is None or (cs.get("source"), cs.get("source_version"), cs.get("source_record_id")) != (
                source, record.get("source_version"), record.get("source_record_id")):
            errors.append("retained datum set_id must resolve to the same station and original source tuple")
    window = candidate.get("window")
    if candidate.get("kind") in ("observed", "computed") and not isinstance(window, dict):
        errors.append("observed/computed retained datum requires its actual calculation window")
    if isinstance(window, dict):
        try:
            bounds = [window[key] for key in ("start", "end")]
            if all(isinstance(value, str) and len(value) == 10 for value in bounds):
                start, end = map(date.fromisoformat, bounds)
            else:
                start, end = map(datetime.fromisoformat, bounds)
                if start.tzinfo is None or end.tzinfo is None:
                    raise ValueError("calculation timestamps require an explicit timezone")
            if start > end:
                errors.append("retained datum window ends before it starts")
        except (KeyError, ValueError, TypeError):
            errors.append("retained datum window requires calendar dates or timezone-aware timestamps")
    return errors


@lru_cache(maxsize=1)
def _candidate_validator():
    from jsonschema import Draft202012Validator
    from otc_pipeline.release_writer import pinned_schema

    schema = pinned_schema()
    return Draft202012Validator({"$ref": "#/$defs/retained_datum", "$defs": schema["$defs"]})


def _append(record, candidate, own_sets):
    errors = [error.message for error in _candidate_validator().iter_errors(candidate)]
    errors += candidate_errors(candidate, record, own_sets)
    # Nested binding/basis facts must also be representable, without NaN/Infinity.
    json.dumps(candidate, allow_nan=False)
    if errors:
        raise ValueError("invalid retained datum: " + "; ".join(errors))
    existing = record.setdefault("retained_datums", [])
    if candidate in existing:
        return 0
    existing.append(copy.deepcopy(candidate))
    return 1


def _base(record, binding):
    return {"kind": "published", "method": "source", "source_record_id": record["source_record_id"],
            "input_binding": copy.deepcopy(binding), "uncertainty_m": None,
            "reliability": {"status": "source_declared", "limitations": [
                "not_independently_validated", "not_selected_for_prediction"]}}


def noaa_candidates(record, body, binding):
    """Keep NOAA numbers in their original units/frame, including ranges/intervals."""
    rid = record["source_record_id"]
    endpoint = f"https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/{rid}/datums.json"
    if body.get("self") != endpoint:
        raise ValueError(f"NOAA retained datum response identity mismatch: {rid}")
    units = body.get("units")
    if not isinstance(units, str) or not units:
        raise ValueError(f"NOAA retained datum units unspecified: {rid}")
    unit = {"meters": "m", "feet": "ft"}.get(units, units)
    entries = [(row["name"], row["value"], row) for row in (body.get("datums") or [])
               if isinstance(row, dict) and isinstance(row.get("name"), str) and _finite(row.get("value"))]
    entries.extend((key, body[key], {"source_field": key}) for key in ("LAT", "HAT", "min", "max")
                   if _finite(body.get(key)))
    grouped = []
    for name, value, original in entries:
        kind = "elapsed_time" if name in ("HWI", "LWI") else "difference" if name in (
            "GT", "MN", "DHQ", "DLQ", "CRD_OFFSET") else "gauge_zero"
        frame = {"kind": kind, "name": "STND" if kind == "gauge_zero" else None,
                 "tie_status": "source_declared" if kind == "gauge_zero" else "not_applicable"}
        this_unit = "h" if kind == "elapsed_time" else unit
        candidate = next((c for c in grouped if c["frame"] == frame and c["units"] == this_unit
                          and name not in c["values"]), None)
        if candidate is None:
            candidate = {**_base(record, binding), "values": {}, "units": this_unit, "frame": frame,
                         "basis": {"source_fields": {}, "epoch": body.get("epoch"),
                                   "DatumAnalysisPeriod": body.get("DatumAnalysisPeriod"),
                                   "ctrlStation": body.get("ctrlStation")}}
            grouped.append(candidate)
        candidate["values"][name] = value
        candidate["basis"]["source_fields"][name] = copy.deepcopy(original)
    return grouped


def _read_noaa(path, rid):
    raw = path.read_bytes()
    receipt_path = Path(str(path) + ".meta.json")
    receipt_raw = receipt_path.read_bytes()
    receipt = json.loads(receipt_raw)
    digest = hashlib.sha256(raw).hexdigest()
    endpoint = f"https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/{rid}/datums.json"
    if (receipt.get("sha256") != digest
            or receipt.get("url", "").split("?", 1)[0] != endpoint):
        raise ValueError(f"NOAA retained datum receipt mismatch: {path}")
    body = json.loads(raw)
    has_values = any(_finite(row.get("value")) for row in (body.get("datums") or []) if isinstance(row, dict))
    has_values = has_values or any(_finite(body.get(key)) for key in ("LAT", "HAT", "min", "max"))
    if receipt.get("status") != 200 and has_values:
        raise ValueError(f"NOAA numerical datum response has unsuccessful receipt: {path}")
    return body if has_values else None, {"path": str(path.resolve()), "sha256": digest,
                            "receipt_sha256": hashlib.sha256(receipt_raw).hexdigest(),
                            "source_url": receipt["url"]}


def retain_source_datums(release, *, noaa_cache=None):
    """Attach held original NOAA/JMA values to their exact original source tuples."""
    changed, candidates, values, empty_responses = set(), 0, 0, 0
    by_source = {}
    for station in release["stations"]:
        own_sets = {cs["set_id"]: cs for cs in station.get("constant_sets", [])}
        for record in station.get("source_records", []):
            metadata = record.get("original_metadata", {})
            prepared = []
            if record["source"] == "noaa":
                rid = record["source_record_id"]
                bodies = []
                for body in json.loads(metadata.get("raw_metadata_rows_json", "[]")):
                    if isinstance(body, dict) and "datums" in body:
                        bodies.append((body, {"original_provenance": record["provenance"],
                                              "metadata_inputs": json.loads(metadata.get("metadata_inputs_json", "[]"))}))
                if noaa_cache is not None and re.fullmatch(r"[A-Za-z0-9_-]+", rid):
                    path = Path(noaa_cache) / "datums" / (rid + ".json")
                    if path.is_file():
                        body, binding = _read_noaa(path, rid)
                        if body is not None:
                            bodies.append((body, binding))
                        else:
                            empty_responses += 1
                for body, binding in bodies:
                    prepared.extend(noaa_candidates(record, body, binding))
            if record["source"] == "jma" and metadata.get("original_constant_set_json"):
                cs = json.loads(metadata["original_constant_set_json"])
                datum = cs.get("datum", {})
                if datum.get("named"):
                    candidate = {**_base(record, {"original_provenance": record["provenance"],
                                                 "native_input_manifests": json.loads(metadata["native_input_manifests_json"])}),
                                 "values": copy.deepcopy(datum["named"]), "units": "m",
                                 "frame": {"kind": datum.get("zero", "unknown"),
                                           "name": datum.get("zero_name"), "tie_status": "source_declared"},
                                 "basis": copy.deepcopy(datum.get("basis", {}))}
                    prepared.append(candidate)
            for candidate in prepared:
                added = _append(record, candidate, own_sets)
                if added:
                    changed.add(station["station_id"])
                    candidates += 1
                    values += len(candidate["values"])
                    by_source[record["source"]] = by_source.get(record["source"], 0) + 1
    return {"changed_stations": len(changed), "added_candidates": candidates,
            "added_values": values, "candidates_by_source": by_source, "responses_without_numeric_datums": empty_responses}


def retain_candidates(release, rows):
    """Attach producer transports with exact source identity; preserve every conflict."""
    stations = {station["station_id"]: station for station in release["stations"]}
    count = 0
    for row in rows:
        station = stations.get(row.get("station_id"))
        if station is None:
            raise ValueError("retained datum station_id does not resolve")
        own_sets = {cs["set_id"]: cs for cs in station.get("constant_sets", [])}
        candidates = row.get("retained_datums", row.get("retained_observed", []))
        for candidate in candidates:
            matching = [record for record in station.get("source_records", [])
                        if record["source_record_id"] == candidate["source_record_id"]
                        and (row.get("source") is None or record["source"] == row["source"])
                        and (row.get("source_version") is None or record["source_version"] == row["source_version"])
                        and not candidate_errors(candidate, record, own_sets)]
            if len(matching) != 1:
                raise ValueError("retained datum requires one exact original source tuple")
            count += _append(matching[0], candidate, own_sets)
    return {"added_candidates": count}


def append_source_sets(release, source_release):
    """Add newly usable original sets without replacing merged station profiles."""
    by_id = {station["station_id"]: station for station in release["stations"]}
    added = 0
    for incoming in source_release["stations"]:
        station = by_id.get(incoming["station_id"])
        if station is None:
            raise ValueError("source-set retention requires an existing station identity")
        current_sets = {cs["set_id"]: cs for cs in station.get("constant_sets", [])}
        records = {(r["source"], r["source_version"], r["source_record_id"]): r
                   for r in station.get("source_records", [])}
        incoming_records = {(r["source"], r["source_version"], r["source_record_id"]): r
                            for r in incoming.get("source_records", [])}
        for cs in incoming.get("constant_sets", []):
            if cs["set_id"] in current_sets:
                # This source projection can predate later datum additions; it
                # never replaces an existing complete native set.
                continue
            source = {"ticon-4": "ticon", "gesla-fit": "gesla"}.get(cs["source"], cs["source"])
            identity = source, cs["source_version"], cs["source_record_id"]
            record, original = records.get(identity), incoming_records.get(identity)
            if record is None or original is None or cs["set_id"] not in original.get("published_set_ids", []):
                raise ValueError("new retained set requires its existing original source tuple and producer binding")
            if (original.get("provenance") != record.get("provenance")
                    or original.get("original_harmonics") != record.get("original_harmonics")
                    or original.get("licence_id") != record.get("licence_id")):
                raise ValueError("new retained set changes original source material or rights")
            metadata = record.setdefault("original_metadata", {})
            for key, value in original.get("original_metadata", {}).items():
                if key in metadata and metadata[key] != value:
                    raise ValueError(f"source metadata conflict must remain explicit: {identity}:{key}")
                metadata[key] = copy.deepcopy(value)
            record.setdefault("published_set_ids", []).append(cs["set_id"])
            station.setdefault("constant_sets", []).append(copy.deepcopy(cs))
            current_sets[cs["set_id"]] = cs
            added += 1
        if station.get("recommended_set_id") is None and current_sets:
            proposed = incoming.get("recommended_set_id")
            if proposed in current_sets:
                station["recommended_set_id"] = proposed
                for key in ("recommended_is_fallback",):
                    if key in incoming:
                        station[key] = incoming[key]
    return {"added_sets": added}


def finalize_datum_references(release):
    """Apply the ordinary source-frame helpers after additions are authenticated."""
    from otc_pipeline.datums import derive_chart_datums
    from otc_pipeline.lat_hat_subordinate import offset_chart_datum

    changed = 0
    for station in release["stations"]:
        datum = offset_chart_datum(station)
        if datum is not None and datum != station["subordinate_offsets"].get("datum"):
            station["subordinate_offsets"]["datum"] = datum
            changed += 1
    return {"subordinate_chart_stations": changed, "chart_designation": derive_chart_datums(release)}
