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


_NOAA_SUBORDINATE_METHOD = "noaa_subordinate_reference_datums_v1"
_NOAA_TIME_METHOD = "noaa_subordinate_reference_time_offsets_v1"
_NOAA_MSL_PROXY_METHOD = "noaa_subordinate_mtl_msl_proxy_v1"
_OFFSET_FIELDS = ("reference_station_id", "height_adjusted_type", "height_offset_high", "height_offset_low",
                  "time_offset_high_min", "time_offset_low_min")


def _published_body(binding):
    raw = binding["response_json"]
    if hashlib.sha256(raw.encode("utf-8")).hexdigest() != binding["sha256"]:
        raise ValueError("published response SHA-256 mismatch")
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError("published response must be a JSON object")
    json.dumps(body, allow_nan=False)
    return body


def _noaa_subordinate_errors(candidate, record, station, reference_station):
    """Check published reference algebra against both actual native source records."""
    shape = [error.message for error in _candidate_validator().iter_errors(candidate)]
    if shape:
        return ["invalid NOAA subordinate proof shape: " + message for message in shape]
    if not isinstance(station, dict) or not isinstance(reference_station, dict):
        return ["NOAA subordinate derivation requires actual own and reference stations"]
    try:
        if record.get("source") != "noaa" or record not in station.get("source_records", []):
            raise ValueError("NOAA subordinate derivation requires its actual own NOAA source record")
        binding, basis = candidate["input_binding"], candidate["basis"]
        own, ref = binding["published_offsets"], binding["published_reference"]
        if (own["source_record_id"], own["source_version"]) != (record["source_record_id"], record["source_version"]):
            raise ValueError("published offsets differ from the exact own source tuple")
        offsets = station.get("subordinate_offsets", {})
        if not isinstance(offsets, dict):
            raise ValueError("NOAA subordinate derivation requires actual native height offsets")
        if (basis["native_offsets"] != {key: offsets.get(key) for key in _OFFSET_FIELDS}
                or ref["native_station_id"] != offsets.get("reference_station_id")
                or reference_station.get("station_id") != ref["native_station_id"]):
            raise ValueError("published derivation differs from actual native offsets or reference station")
        ref_id = ref["source_record_id"]
        metadata = record.get("original_metadata")
        if not isinstance(metadata, dict):
            raise ValueError("NOAA subordinate derivation requires original source metadata")
        rows = json.loads(metadata.get("raw_metadata_rows_json", "[]"))
        if not isinstance(rows, list) or not any(isinstance(row, dict) and row.get("id") == record["source_record_id"]
                                               and row.get("reference_id") == ref_id for row in rows):
            raise ValueError("published reference differs from the original own source metadata")
        endpoint = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/"
        if own["source_url"] != endpoint + record["source_record_id"] + "/tidepredoffsets.json":
            raise ValueError("published offset URL differs from the exact own source")
        ref_endpoint = endpoint + ref_id + "/datums.json"
        if ref["source_url"] != ref_endpoint + "?units=metric":
            raise ValueError("published reference URL must identify exact metric datums")
        published, reference = _published_body(own), _published_body(ref)
        if (own["reference_source_record_id"] != ref_id or published.get("refStationId") != ref_id
                or reference.get("self") != ref_endpoint or reference.get("units") != "meters"):
            raise ValueError("published reference identity or metre units differ")
        if basis["reference_epoch"] != reference.get("epoch"):
            raise ValueError("reference epoch differs from the published response")
        operation = published.get("heightAdjustedType")
        if operation not in ("R", "F") or basis["operation"] != operation:
            raise ValueError("unsupported or conflicting published height operation")
        if (basis["offset_height_units"] != ("ratio" if operation == "R" else "feet")
                or offsets.get("height_adjusted_type") != ("R" if operation == "R" else "A")):
            raise ValueError("published height units differ from native normalization")
        high, low = published["heightOffsetHighTide"], published["heightOffsetLowTide"]
        if not all(_finite(value) for value in (high, low)) or (operation == "R" and min(high, low) <= 0):
            raise ValueError("height adjustments require finite differences or positive ratios")
        for native, value in (("height_offset_high", high * (0.3048 if operation == "F" else 1)),
                              ("height_offset_low", low * (0.3048 if operation == "F" else 1)),
                              ("time_offset_high_min", published.get("timeOffsetHighTide")),
                              ("time_offset_low_min", published.get("timeOffsetLowTide"))):
            if not isinstance(value, (int, float)) or not _finite(value) or not _finite(offsets.get(native)) or not math.isclose(
                    value, offsets[native], rel_tol=0, abs_tol=1e-12):
                raise ValueError("published adjustments differ from actual native offsets")
        values = {}
        for row in reference.get("datums", []):
            name, value = row["name"], row["value"]
            if name in values:
                raise ValueError("ambiguous duplicate published reference datum")
            values[name] = value
        for name in ("LAT", "HAT"):
            if _finite(reference.get(name)):
                values[name] = reference[name]
        if "published_values" in basis and basis["published_values"] != values:
            raise ValueError("copied reference values differ from the hashed published body")
        references = [r for r in reference_station.get("source_records", [])
                      if isinstance(r, dict) and (r.get("source"), r.get("source_record_id")) == ("noaa", ref_id)]
        if len(references) != 1:
            raise ValueError("published reference requires one actual native NOAA source record")
        supported = {key: value for key, value in values.items()
                     if key in ("MLLW", "MHHW", "MHW", "MLW", "MSL", "LAT", "HAT") and _finite(value)}
        if not any(isinstance(c, dict) and c.get("kind") == "published" and c.get("method") == "source"
                   and c.get("source_record_id") == ref_id and c.get("units") == "m"
                   and isinstance(c.get("input_binding"), dict) and isinstance(c.get("values"), dict)
                   and c.get("frame") == {"kind": "gauge_zero", "name": "STND", "tie_status": "source_declared"}
                   and c.get("input_binding", {}).get("sha256") == ref["sha256"]
                   and c.get("input_binding", {}).get("source_url") == ref["source_url"]
                   and all(c.get("values", {}).get(key) == value for key, value in supported.items())
                   for c in references[0].get("retained_datums", [])):
            raise ValueError("published reference body is not bound to retained native source values")
        if not _finite(values.get("MLLW")):
            raise ValueError("published reference requires a finite MLLW baseline")
        zero = values["MLLW"]
        mapped = {}
        for name, adjustment in (("MHHW", high), ("MHW", high), ("MLW", low), ("MLLW", low),
                                 ("LAT", low), ("HAT", high)):
            if _finite(values.get(name)):
                height = values[name] - zero
                mapped[name.lower()] = height * adjustment if operation == "R" else height + adjustment * 0.3048
        for key, first, second, factor, subtract in (
                ("mtl", "mhw", "mlw", 0.5, False), ("dtl", "mhhw", "mllw", 0.5, False),
                ("mn", "mhw", "mlw", 1, True), ("gt", "mhhw", "mllw", 1, True),
                ("dhq", "mhhw", "mhw", 1, True), ("dlq", "mlw", "mllw", 1, True)):
            if first in mapped and second in mapped:
                mapped[key] = (mapped[first] - mapped[second] if subtract else mapped[first] + mapped[second]) * factor
        if high == low and published["timeOffsetHighTide"] == published["timeOffsetLowTide"] and _finite(values.get("MSL")):
            mapped["msl"] = (values["MSL"] - zero) * high if operation == "R" else values["MSL"] - zero + high * 0.3048
        for key, value in candidate["values"].items():
            if key not in mapped or not _finite(value) or not math.isclose(value, mapped[key], rel_tol=0, abs_tol=1e-12):
                raise ValueError("derived value does not follow the authenticated published reference algebra")
            if key in ("lat", "hat") and basis.get("extrema_period", {}).get(key) != {"date": reference.get(key.upper() + "date")}:
                raise ValueError("published extrema require their own exact source date or explicit unknown date")
        for key, period in basis.get("extrema_period", {}).items():
            if period != {"date": reference.get(key.upper() + "date")}:
                raise ValueError("copied extrema date differs from the published response")
    except (KeyError, TypeError, ValueError, UnicodeError) as error:
        return [f"invalid NOAA subordinate derivation: {error}"]
    return []


def _noaa_increment_source(candidate, record, station, reference_station):
    """Authenticate original source material for time intervals and labelled proxies."""
    if not isinstance(station, dict) or not isinstance(reference_station, dict):
        raise ValueError("actual own and reference stations are required")
    binding, basis = candidate["input_binding"], candidate["basis"]
    own, ref = binding["published_offsets"], binding["published_reference"]
    if (record.get("source") != "noaa" or record not in station.get("source_records", [])
            or (own["source_record_id"], own["source_version"]) != (record["source_record_id"], record["source_version"])):
        raise ValueError("published offsets differ from the actual own NOAA source tuple")
    offsets = station.get("subordinate_offsets")
    if (not isinstance(offsets, dict) or basis["native_offsets"] != {key: offsets.get(key) for key in _OFFSET_FIELDS}
            or offsets.get("reference_station_id") != reference_station.get("station_id")
            or ref["native_station_id"] != reference_station.get("station_id")):
        raise ValueError("published proof differs from actual native offsets or reference station")
    metadata = record.get("original_metadata")
    if not isinstance(metadata, dict):
        raise ValueError("original own source metadata is required")
    rows = json.loads(metadata.get("raw_metadata_rows_json", "[]"))
    ref_id = ref["source_record_id"]
    if not isinstance(rows, list) or not any(isinstance(row, dict) and row.get("id") == record["source_record_id"]
                                           and row.get("reference_id") == ref_id for row in rows):
        raise ValueError("reference differs from original own source metadata")
    endpoint = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/"
    ref_endpoint = endpoint + ref_id + "/datums.json"
    if (own["source_url"] != endpoint + record["source_record_id"] + "/tidepredoffsets.json"
            or ref["source_url"] != ref_endpoint + "?units=metric"):
        raise ValueError("publisher URLs must identify the exact own offsets and metric reference")
    published, reference = _published_body(own), _published_body(ref)
    if (published.get("refStationId") != ref_id or own["reference_source_record_id"] != ref_id
            or reference.get("self") != ref_endpoint or reference.get("units") != "meters"
            or basis["reference_epoch"] != reference.get("epoch")):
        raise ValueError("published reference identity, units or epoch differ")
    operation = published.get("heightAdjustedType")
    if (operation not in ("R", "F") or basis["operation"] != operation
            or basis["offset_height_units"] != ("ratio" if operation == "R" else "feet")
            or offsets.get("height_adjusted_type") != ("R" if operation == "R" else "A")):
        raise ValueError("published height operation or normalized units differ")
    high, low = published["heightOffsetHighTide"], published["heightOffsetLowTide"]
    if not all(_finite(value) for value in (high, low)) or (operation == "R" and min(high, low) <= 0):
        raise ValueError("height context requires finite differences or positive ratios")
    for field, value in (("height_offset_high", high * (0.3048 if operation == "F" else 1)),
                         ("height_offset_low", low * (0.3048 if operation == "F" else 1)),
                         ("time_offset_high_min", published.get("timeOffsetHighTide")),
                         ("time_offset_low_min", published.get("timeOffsetLowTide"))):
        if not isinstance(value, (int, float)) or not _finite(value) or not _finite(offsets.get(field)) or not math.isclose(
                value, offsets[field], rel_tol=0, abs_tol=1e-12):
            raise ValueError("published adjustments differ from actual native offsets")
    references = [r for r in reference_station.get("source_records", [])
                  if isinstance(r, dict) and (r.get("source"), r.get("source_record_id")) == ("noaa", ref_id)]
    if len(references) != 1:
        raise ValueError("one actual native reference NOAA source record is required")
    return published, reference, references[0]


def _has_published_reference(record, binding, values, units, frame):
    return any(isinstance(candidate, dict) and candidate.get("kind") == "published"
               and candidate.get("method") == "source" and candidate.get("units") == units
               and candidate.get("source_record_id") == record.get("source_record_id")
               and candidate.get("frame") == frame and isinstance(candidate.get("input_binding"), dict)
               and isinstance(candidate.get("values"), dict)
               and candidate["input_binding"].get("sha256") == binding["sha256"]
               and candidate["input_binding"].get("source_url") == binding["source_url"]
               and all(candidate["values"].get(key) == value for key, value in values.items())
               for candidate in record.get("retained_datums", []))


def _computed_reference_levels(candidate, record, station):
    """Resolve the retained own-model event means and their actual hourly mean."""
    proof = candidate["input_binding"]["computed_reference"]
    transport = _published_body({"response_json": proof["source_candidate_json"], "sha256": proof["sha256"]})
    model = _published_body({"response_json": proof["reference_model_json"], "sha256": proof["reference_model_sha256"]})
    identity = record["source"], record["source_version"], record["source_record_id"]
    if (transport["station_id"] != station["station_id"]
            or (transport["source"], transport["source_version"], transport["source_record_id"]) != identity
            or (model["source"], model["source_version"], model["source_record_id"]) != identity
            or model.get("quantity") != "water_level"):
        raise ValueError("computed reference requires the actual own NOAA source and water-level model")
    own_sets = {cs["set_id"]: cs for cs in station.get("constant_sets", [])}
    actual_model = own_sets.get(model["set_id"])
    if actual_model is None or ({key: value for key, value in model.items() if key != "datum"}
                                != {key: value for key, value in actual_model.items() if key != "datum"}):
        raise ValueError("computed reference model differs from actual native coefficients or scientific bindings")
    prepared = {}
    for method in ("sdk_harmonic_extremes_event_means_v1", "sdk_harmonic_hourly_mean_v1"):
        matching = [c for c in transport["retained_datums"] if isinstance(c, dict) and c.get("method") == method]
        if len(matching) != 1:
            raise ValueError("computed reference requires one event-mean and one hourly-mean candidate")
        source = matching[0]
        if (source not in record.get("retained_datums", []) or source.get("kind") != "computed"
                or source.get("units") != "m" or source.get("set_id") != model["set_id"]
                or source.get("frame") != {"kind": "harmonic_mean", "name": None, "tie_status": "unknown"}
                or list(_candidate_validator().iter_errors(source)) or candidate_errors(source, record, own_sets)):
            raise ValueError("computed reference must be an exact retained own-set candidate with a valid window and frame")
        binding = source["input_binding"]
        if (binding.get("constant_set_sha256") != proof["reference_model_sha256"]
                or binding.get("set_id") != model["set_id"]
                or binding.get("convention_id") != model.get("convention_id")
                or binding.get("source_provenance") != model.get("provenance")
                or not isinstance(binding.get("request_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", binding["request_sha256"])):
            raise ValueError("computed reference model, convention, provenance or request hash differs")
        prepared[method] = source
    events = prepared["sdk_harmonic_extremes_event_means_v1"]
    hourly = prepared["sdk_harmonic_hourly_mean_v1"]
    hourly_binding = hourly["input_binding"].get("hourly_heights")
    if not isinstance(hourly_binding, dict) or not isinstance(hourly_binding.get("input_binding"), dict):
        raise ValueError("computed reference requires its actual hourly-response binding")
    hourly_input = hourly_binding["input_binding"]
    if (events["window"] != hourly["window"] or hourly_binding.get("window") != hourly["window"]
            or events["input_binding"]["request_sha256"] != hourly["input_binding"]["request_sha256"]
            or events["input_binding"].get("results_sha256") != proof["events_response_sha256"]
            or hourly["input_binding"].get("results_sha256") != proof["events_response_sha256"]
            or hourly_input.get("results_sha256") != proof["hourly_response_sha256"]
            or not isinstance(hourly_input.get("request_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", hourly_input["request_sha256"])):
        raise ValueError("computed reference event/hourly windows, requests or response hashes differ")
    values = events["values"].get("mhw"), events["values"].get("mlw"), hourly["values"].get("msl")
    if not all(_finite(value) for value in values):
        raise ValueError("computed reference requires finite event means and actual hourly MSL")
    return values


def _noaa_increment_errors(candidate, record, station, reference_station):
    shape = [error.message for error in _candidate_validator().iter_errors(candidate)]
    if shape:
        return ["invalid NOAA time/proxy proof shape: " + error for error in shape]
    try:
        published, reference, reference_record = _noaa_increment_source(candidate, record, station, reference_station)
        rows = {}
        for row in reference.get("datums", []):
            if row["name"] in rows:
                raise ValueError("ambiguous duplicate published reference datum")
            rows[row["name"]] = row
        binding = candidate["input_binding"]["published_reference"]
        if candidate["method"] == _NOAA_TIME_METHOD:
            values = {}
            for key, offset in (("hwi", "timeOffsetHighTide"), ("lwi", "timeOffsetLowTide")):
                if key not in candidate["values"]:
                    continue
                row = rows[key.upper()]
                description = row.get("description")
                if (not _finite(row.get("value")) or not isinstance(description, str)
                        or not re.search(r"\bGreenwich\b", description, re.IGNORECASE)
                        or not re.search(r"\bhours?\b", description, re.IGNORECASE)):
                    raise ValueError("reference interval requires explicit published Greenwich hours")
                values[key.upper()] = row["value"]
                actual = candidate["values"][key]
                expected = row["value"] + published[offset] / 60
                if not _finite(actual) or not math.isclose(actual, expected, rel_tol=0, abs_tol=1e-12):
                    raise ValueError("interval must add the corresponding published minutes divided by 60")
            if not _has_published_reference(reference_record, binding, values, "h", {
                    "kind": "elapsed_time", "name": None, "tie_status": "not_applicable"}):
                raise ValueError("reference hours are not bound to existing native published interval values")
        else:
            if (published["heightOffsetHighTide"] == published["heightOffsetLowTide"]
                    and published["timeOffsetHighTide"] == published["timeOffsetLowTide"]):
                raise ValueError("uniform affine MSL must not be replaced by a low-confidence MTL proxy")
            computed = candidate["basis"]["reference_statistic_origin"] == "computed_harmonic_reference"
            keys = ("MSL", "MLLW") if computed else ("MHW", "MLW", "MLLW")
            values = {key: rows[key]["value"] for key in keys}
            if not all(_finite(value) for value in values.values()):
                raise ValueError("proxy requires finite same-body published reference levels")
            if not _has_published_reference(reference_record, binding, values, "m", {
                    "kind": "gauge_zero", "name": "STND", "tie_status": "source_declared"}):
                raise ValueError("reference levels are not bound to existing native published values")
            if computed:
                if all(_finite(rows.get(key, {}).get("value")) for key in ("MHW", "MLW")):
                    raise ValueError("published reference levels must take precedence over computed fallback")
                high, low, mean = _computed_reference_levels(candidate, reference_record, reference_station)
                shift = values["MSL"] - values["MLLW"]
                high, low = high - mean + shift, low - mean + shift
            else:
                high, low = values["MHW"] - values["MLLW"], values["MLW"] - values["MLLW"]
            if published["heightAdjustedType"] == "R":
                high *= published["heightOffsetHighTide"]
                low *= published["heightOffsetLowTide"]
            else:
                high += published["heightOffsetHighTide"] * 0.3048
                low += published["heightOffsetLowTide"] * 0.3048
            actual = candidate["values"]["msl"]
            if not _finite(actual) or not math.isclose(actual, (high + low) / 2, rel_tol=0, abs_tol=1e-12):
                raise ValueError("MSL proxy must be the mean of separately mapped MHW and MLW")
    except (KeyError, TypeError, ValueError, UnicodeError) as error:
        return [f"invalid NOAA time/proxy derivation: {error}"]
    return []


def candidate_errors(candidate, record, own_sets, *, station=None, reference_station=None):
    """Identity and interval checks additional to the independent carrier schema."""
    errors = []
    method = candidate.get("method")
    source_computed = method in (_NOAA_SUBORDINATE_METHOD, _NOAA_TIME_METHOD, _NOAA_MSL_PROXY_METHOD)
    if method == _NOAA_SUBORDINATE_METHOD:
        errors.extend(_noaa_subordinate_errors(candidate, record, station, reference_station))
    elif method in (_NOAA_TIME_METHOD, _NOAA_MSL_PROXY_METHOD):
        errors.extend(_noaa_increment_errors(candidate, record, station, reference_station))
    if candidate.get("source_record_id") != record.get("source_record_id"):
        errors.append("retained datum source_record_id differs from its original tuple")
    chart = candidate.get("basis", {}).get("chart_datum")
    if chart is not None and (not isinstance(chart, str) or not _finite(candidate.get("values", {}).get(chart))):
        errors.append("retained datum chart_datum must name an existing finite value")
    sid = candidate.get("set_id")
    if candidate.get("kind") == "computed" and not sid and not source_computed:
        errors.append("computed retained datum requires its actual set_id")
    if sid is not None:
        cs = own_sets.get(sid)
        source = {"gesla": "gesla-fit", "ticon": "ticon-4"}.get(record.get("source"), record.get("source"))
        if cs is None or (cs.get("source"), cs.get("source_version"), cs.get("source_record_id")) != (
                source, record.get("source_version"), record.get("source_record_id")):
            errors.append("retained datum set_id must resolve to the same station and original source tuple")
    window = candidate.get("window")
    if candidate.get("kind") in ("observed", "computed") and not source_computed and not isinstance(window, dict):
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


def _append(record, candidate, own_sets, *, station=None, reference_station=None):
    errors = [error.message for error in _candidate_validator().iter_errors(candidate)]
    errors += candidate_errors(candidate, record, own_sets, station=station, reference_station=reference_station)
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
        offsets = station.get("subordinate_offsets")
        reference_station = stations.get(offsets.get("reference_station_id")) if isinstance(offsets, dict) else None
        candidates = row.get("retained_datums", row.get("retained_observed", []))
        for candidate in candidates:
            matching = [record for record in station.get("source_records", [])
                        if record["source_record_id"] == candidate["source_record_id"]
                        and (row.get("source") is None or record["source"] == row["source"])
                        and (row.get("source_version") is None or record["source_version"] == row["source_version"])
                        and not candidate_errors(candidate, record, own_sets, station=station,
                                                 reference_station=reference_station)]
            if len(matching) != 1:
                raise ValueError("retained datum requires one exact original source tuple")
            count += _append(matching[0], candidate, own_sets, station=station, reference_station=reference_station)
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
            if record.get("disposition") == "metadata_only":
                record["disposition"] = "retained"
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


def designate_noaa_published_charts(release):
    """Name held published MLLW levels for the two datum-only NOAA references.

    The pointer is a key in the same candidate's values, above its unchanged STND
    zero. It does not select a prediction datum or establish independent accuracy.
    """
    targets = {"OTC-d14a171f": "8660754", "OTC-e48e302c": "9751388"}
    pending, changed = [], set()
    for station in release["stations"]:
        rid = targets.get(station["station_id"])
        if rid is None:
            continue
        for record in station.get("source_records", []):
            if (record["source"], record["source_record_id"]) != ("noaa", rid):
                continue
            for candidate in record.get("retained_datums", []):
                basis = candidate.get("basis", {})
                original = basis.get("source_fields", {}).get("MLLW", {})
                value = candidate.get("values", {}).get("MLLW")
                if (candidate.get("source_record_id") != rid or candidate.get("kind") != "published"
                        or candidate.get("method") != "source" or candidate.get("units") != "m"
                        or candidate.get("frame") != {"kind": "gauge_zero", "name": "STND", "tie_status": "source_declared"}
                        or not _finite(value) or original.get("name") != "MLLW" or original.get("value") != value):
                    continue
                pointer = basis.get("chart_datum")
                if pointer is not None and pointer != "MLLW":
                    raise ValueError(f"NOAA {rid}: existing retained chart pointer conflicts with published MLLW")
                if pointer is None:
                    pending.append(basis)
                    changed.add(station["station_id"])
    for basis in pending:
        basis["chart_datum"] = "MLLW"
    return {"changed_stations": len(changed), "changed_candidates": len(pending)}


def finalize_datum_references(release):
    """Apply the ordinary source-frame helpers after additions are authenticated."""
    from otc_pipeline.datums import derive_chart_datums
    from otc_pipeline.lat_hat_subordinate import offset_chart_datum

    noaa_chart = designate_noaa_published_charts(release)
    changed = 0
    for station in release["stations"]:
        datum = offset_chart_datum(station)
        if datum is not None and datum != station["subordinate_offsets"].get("datum"):
            station["subordinate_offsets"]["datum"] = datum
            changed += 1
    return {"subordinate_chart_stations": changed, "chart_designation": derive_chart_datums(release),
            "published_noaa_chart": noaa_chart}
