"""Published datum helpers and cited chart-datum designation (datums spec §4.1, §4.4).

A published level is the publisher's number, converted to metres and re-expressed above the set's
zero, with a `basis` of kind `published`, method `source`, the publisher's own name for it and,
only where the publisher states one, its epoch and control station. Nothing here computes a level.
An existing computed LAT can be designated as a labelled fallback chart datum without changing
its value or basis, subject to the cited country/area convention and station-wide precedence.

`datum_build_errors` runs the release build checks the schema cannot express that touch a single
datum object: check 8 (chart_datum names a key of named), check 9 (basis and named have the same
keys) and check 13 (every datum date is a real calendar date; every period has start <= end).
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache
import copy
import hashlib
import json
import math
from pathlib import Path

__all__ = ["datum_build_errors", "derive_chart_datums", "observed_companions", "period", "published"]


def observed_companions(datum: dict, draft: dict, keys: list[str], *, truncated_lows: bool = False) -> dict:
    """Add observed keys without replacing a level, its basis, zero or chart pointer."""
    allowed = {"mllw", "mlw", "msl", "mhw", "mhhw", "mtl", "dtl", "mn", "gt", "dhq", "dlq"}
    if (set(draft) != {"named", "basis"} or len(keys) != len(set(keys))
            or set(keys) != set(draft["named"]) or set(keys) != set(draft["basis"])):
        raise ValueError("observed draft must contain exactly its declared new named/basis keys")
    result = copy.deepcopy(datum)
    named = result.setdefault("named", {})
    bases = result.setdefault("basis", {})
    for key in keys:
        level = key.removeprefix("otc_")
        basis = draft["basis"][key]
        if level not in allowed or basis.get("kind") != "observed":
            raise ValueError(f"invalid observed datum key/kind: {key}")
        if key in named or key in bases:
            raise ValueError(f"observed datum conflicts with existing level: {key}")
        if key.startswith("otc_") and (level not in named or bases.get(level, {}).get("kind") != "published"):
            raise ValueError(f"observed companion requires a published same-set level: {key}")
        if truncated_lows and (level not in ("mhw", "mhhw") or "truncated_lows" not in basis.get("flags", [])):
            raise ValueError(f"observed datum forbidden by known low-water cutoff: {key}")
        named[key] = copy.deepcopy(draft["named"][key])
        bases[key] = copy.deepcopy(basis)
    return result


def period(start: str, end: str, name: str | None = None) -> dict:
    """A `datum_period` (inclusive dates YYYY-MM-DD). Raises ValueError on an impossible date or
    an end before the start, so a bad epoch never reaches a release."""
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    if e < s:
        raise ValueError(f"period {start}..{end}: end before start")
    out = {"start": s.isoformat(), "end": e.isoformat()}
    if name:
        out["name"] = name
    return out


def published(source_name: str, *, epoch: dict | None = None, control_source_id: str | None = None,
              provisional: bool = False) -> dict:
    """The basis of one published level: kind published, method source, the publisher's name."""
    if not source_name or len(source_name) > 31:
        raise ValueError(f"source_name {source_name!r} must be 1 to 31 characters")
    b = {"kind": "published", "method": "source", "source_name": source_name}
    if epoch is not None:
        b["epoch"] = epoch
    if control_source_id:
        b["control"] = {"source_id": control_source_id}
    if provisional:
        b["flags"] = ["provisional"]
    return b


def _real_date(value) -> bool:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True


def datum_build_errors(datum: dict, where: str = "datum") -> list[str]:
    """Build checks 8, 9 and 13 on one datum object (empty list = it passes)."""
    errors = []
    named, basis = datum.get("named"), datum.get("basis")
    cd = datum.get("chart_datum")
    if cd is not None and (not isinstance(named, dict) or cd not in named):
        errors.append(f"{where}: check 8: chart_datum {cd!r} is not a key of named")
    if isinstance(named, dict) or isinstance(basis, dict):
        nk, bk = set(named or {}), set(basis or {})
        if nk != bk:
            errors.append(f"{where}: check 9: named and basis keys differ: only in named {sorted(nk - bk)}, "
                          f"only in basis {sorted(bk - nk)}")
    for key, b in sorted((basis or {}).items()):
        for field in ("epoch", "data_span"):
            p = b.get(field) if isinstance(b, dict) else None
            if not isinstance(p, dict):
                continue
            s, e = p.get("start"), p.get("end")
            if not (_real_date(s) and _real_date(e)):
                errors.append(f"{where}: check 13: {key}.{field} {s!r}..{e!r} is not a real date pair")
            elif e < s:
                errors.append(f"{where}: check 13: {key}.{field} ends before it starts")
    return errors


@lru_cache(maxsize=1)
def _chart_conventions() -> tuple[dict, str]:
    raw = Path(__file__).with_name("chart_datum_conventions.json").read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def derive_chart_datums(release: dict) -> dict:
    """Designate existing computed LAT under a cited convention; never compute a level.

    Mutates only missing chart_datum pointers. Gate-failed/unknown-quality fallback bases
    remain intact. Receipt counts places separately from sets; withheld counts are sets.
    Unresolved geographic conditions are unknown, including GBR without UK source facts.
    Conflicting original COUNTRY identities leave new designations unresolved.
    """
    from otc_pipeline.lat_hat import low_water_suppressed

    table, digest = _chart_conventions()
    exclusions = set(table["dt5f_exclusions"]["source_record_ids"])
    receipt = {"conventions_sha256": digest, "lat_places": 0, "matched_places": 0,
               "changed_places": 0, "changed_sets": 0, "withheld": {},
               "by_convention": {}, "citations": {}}
    for station in release.get("stations", []):
        sets = station.get("constant_sets", [])
        candidates = [cs for cs in sets if "lat" in cs.get("datum", {}).get("named", {})]
        datums = [cs.get("datum", {}) for cs in sets]
        datums.append(station.get("subordinate_offsets", {}).get("datum", {}))
        if not any("lat" in d.get("named", {}) for d in datums):
            continue
        receipt["lat_places"] += 1
        metadata = [r.get("original_metadata", {}) for r in station.get("source_records", [])]
        source_countries = {str(m.get("COUNTRY", "")).strip().upper() for m in metadata}
        # Native COUNTRY identities are alpha-3; blank/unknown text asserts no country.
        country_conflict = any(country != station.get("country") for country in source_countries
                               if len(country) == 3 and country.isascii() and country.isalpha())
        country_rows = [r for r in table["rows"] if r["country_code"] == station.get("country")]
        convention = next((r for r in country_rows if not country_conflict and (r["match"] == "country" or (
            r["match"] == "source_metadata" and any(
                m.get("COUNTRY") == station.get("country") and any(
                    str(m.get(field, "")).strip().casefold() in values
                    for field, values in r["metadata_any"].items()) for m in metadata)))), None)
        tally = None
        if convention:
            receipt["matched_places"] += 1
            key = convention["id"]
            tally = receipt["by_convention"].setdefault(
                key, {"matched_places": 0, "changed_places": 0, "changed_sets": 0})
            tally["matched_places"] += 1
            receipt["citations"][key] = {field: convention[field] for field in (
                "country_label", "area_condition", "stated_convention", "source_url",
                "source_sha256", "pdf_page_1_based", "table_locator")}

        published_chart = any(d.get("chart_datum") is not None and
                              d.get("basis", {}).get(d["chart_datum"], {}).get("kind") == "published"
                              for d in datums)
        record_ids = {cs.get("source_record_id") for cs in sets if cs.get("source") == "gesla-fit"}
        record_ids.update(r.get("source_record_id") for r in station.get("source_records", [])
                          if r.get("source") == "gesla")
        aliases = station.get("aliases", {}).get("gesla", [])
        record_ids.update([aliases] if isinstance(aliases, str) else aliases)
        excluded = bool(record_ids & exclusions)
        changed = 0
        for cs in candidates:
            datum = cs["datum"]
            if datum.get("chart_datum") is not None:
                continue
            value = datum["named"]["lat"]
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                    or datum.get("basis", {}).get("lat", {}).get("kind") != "computed"):
                reason = "not_finite_computed_lat"
            elif published_chart:
                reason = "published_chart_datum"
            elif excluded:
                reason = "baltic_or_bscd2000"
            elif low_water_suppressed(cs):
                reason = "actual_low_water_cutoff"
            elif not convention:
                reason = "non_lat_convention" if country_rows and all(
                    r["LAT_evidence_status"] == "not_LAT" for r in country_rows) else "unknown_convention_or_area"
            else:
                datum["chart_datum"] = "lat"
                changed += 1
                continue
            receipt["withheld"][reason] = receipt["withheld"].get(reason, 0) + 1
        if changed:
            receipt["changed_places"] += 1
            receipt["changed_sets"] += changed
            tally["changed_places"] += 1
            tally["changed_sets"] += changed
    return receipt
