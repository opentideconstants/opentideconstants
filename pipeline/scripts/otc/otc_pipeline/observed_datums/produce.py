"""Reduce immutable cached original gauge records to labelled observed drafts.

No fitting, acquisition, chart-datum assignment or scientific admission occurs here.
The restored DT1 reconstruction and monthly arithmetic remain the measurement engine.
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import math
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from gesla.qc import clean
from gesla.read_gesla import GeslaRelease, load_contributor_roles
from otc_pipeline import datum_t1, gesla_sets, ticon_sets
from . import monthly, tabulate

EPOCH = {"start": "2002-01-01", "end": "2020-12-31", "name": "OTC 2002-2020"}
DIAGNOSTIC_SHA256 = "e1794ee0e97b6385957aa19c4053409b9c9fd7639f0a651712c43a4f132d5c8e"
# Existing NOAA2015 hourly-picked pooled monthly diagnostics, in centimetres.
# These are descriptive errors, never station accuracy or total uncertainty.
T1_ERRORS_CM = {
    "MLLW": (0.296150826709074, 2.216078382128572),
    "MLW": (0.171165909378046, 1.1773095540252558),
    "MSL": (0.024798387096791608, 0.04798745519711378),
    "MHW": (0.15449694599669606, 1.504735049914243),
    "MHHW": (0.1794765006276955, 1.6765841060230446),
    "MTL": (0.08902278326851087, 0.9531698068250134),
    "DTL": (0.17197008532439106, 1.296407099163246),
    "MN": (0.2507486957302463, 1.8811477559254894),
    "GT": (0.4514021281433078, 2.6032411234422366),
    "DHQ": (0.14532417477145693, 2.0120791944692114),
    "DLQ": (0.18191508637723613, 2.051294316195088),
}


def sha256(path: Path) -> str:
    with Path(path).open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256")
    return digest.hexdigest()


def canonical_hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def good_hours(times: np.ndarray) -> int:
    """Count distinct occupied UTC hours; a subhourly sample is never an hour."""
    return int(len(np.unique(np.asarray(times, dtype=np.int64) // 3600)))


def is_primary(mm: dict) -> bool:
    epoch = {(y, m): row for (y, m), row in mm.items() if 2002 <= y <= 2020}
    return len(epoch) >= 216 and all(sum(m == month for _, m in epoch) >= 17 for month in range(1, 13))


def balanced_mean(mm: dict) -> dict:
    return {key: float(np.mean([np.mean([row[key] for (_, m), row in mm.items() if m == month])
                               for month in range(1, 13)])) for key in monthly.LEVELS}


def own_months(mm: dict) -> dict:
    """Latest at-most-19 whole calendar years; keep the counted interior months.

    Missing interior months remain missing; an incomplete year is never represented
    as twelve counted months. Under twelve counted months, retain the actual months.
    """
    if len(mm) < 12:
        return dict(mm)
    first, last = min(mm), max(mm)
    y0 = first[0] + (first[1] != 1)
    y1 = last[0] - (last[1] != 12)
    y0 = max(y0, y1 - 18)
    return {(y, m): row for (y, m), row in mm.items() if y0 <= y <= y1}


def span_of(mm: dict) -> dict:
    start, end = min(mm), max(mm)
    return {"start": f"{start[0]:04d}-{start[1]:02d}-01",
            "end": f"{end[0]:04d}-{end[1]:02d}-{calendar.monthrange(*end)[1]:02d}", "months": len(mm)}


def cut_off_lows(cs: dict) -> bool:
    return any(row.get("issue") == "flatline" and "low" in row.get("reason", "").lower()
               for row in cs.get("record_issues", [])) or any(
                   "truncated_lows" in row.get("flags", []) for row in cs.get("datum", {}).get("basis", {}).values())


def draft_datum(cs: dict, values: dict, mm: dict, *, method: str, control: dict | None = None,
                primary: bool = False, gaps: bool = False) -> tuple[dict, dict]:
    span = span_of(mm)
    own = method == "first_reduction" and not primary
    epoch = {key: span[key] for key in ("start", "end")} if own else dict(EPOCH)
    flags = []
    code = cs.get("time_base", {}).get("code")
    if code in ("unverified", "disputed"):
        flags.append(f"time_base_{code}")
    if own:
        flags.append("no_qualified_control")
    if len(mm) < 12:
        flags.append("short_record")
    if cut_off_lows(cs):
        flags.append("truncated_lows")
    if cs.get("fit_segment") or any(r.get("issue") == "datum_step" for r in cs.get("record_issues", [])):
        flags.append("segment")
    if gaps:
        flags.append("gaps")
    if values.get("MN", math.inf) < 0.1:
        flags.append("microtidal")
    sampling_assumed = cs.get("time_base", {}).get("published") != "utc_mean_start60"
    # A verified instantaneous clock does not prove that the gauge sampled instants.
    result = {"named": {}, "basis": {}}
    components = {}
    for level in monthly.LEVELS:
        if level not in values or ("truncated_lows" in flags and level not in ("MHW", "MHHW")):
            continue
        key = level.lower()
        existing = cs.get("datum", {}).get("basis", {})
        if key in cs.get("datum", {}).get("named", {}):
            if existing.get(key, {}).get("kind") != "published":
                continue
            key = f"otc_{key}"
            if key in cs["datum"]["named"]:
                continue
        level_flags = list(flags)
        sampling = 0.011 * values.get("MN", 0.0) / 2 if sampling_assumed and level not in ("MSL", "MTL", "DTL") else 0.0
        if sampling_assumed and level not in ("MSL", "MTL", "DTL"):
            level_flags.append("sampling_assumed")
        noise = float(np.std([row[level] for row in mm.values()], ddof=1) / math.sqrt(len(mm))) if len(mm) > 1 else None
        components[key] = {"sampling_m": sampling, "monthly_averaging_noise_m": noise,
                           "total_uncertainty_m": None, "station_accuracy_calibrated": False,
                           "levelling_uncertainty_m": None}
        median, p95 = T1_ERRORS_CM[level]
        finding = (f"Scientific T1 FAIL; normal acceptance false. High/low event labels remain unverified "
                   f"(NOAA2015 tuning label agreement 95.37228%). {level} NOAA2015 hourly-picked pooled "
                   f"monthly absolute-error median/p95 {median:.12g}/{p95:.12g} cm. "
                   f"Diagnostic receipt SHA256 {DIAGNOSTIC_SHA256}. These pooled diagnostics are not "
                   f"calibrated GESLA station accuracy or sigma. Total station/levelling uncertainty "
                   f"is unavailable; partial sampling component {sampling:.12g} m is not a total.")
        if own:
            finding += " Own-record epoch equals the actual used span; not adjusted to OTC 2002-2020."
        basis = {"kind": "observed", "method": method, "epoch": epoch, "data_span": span,
                 "flags": level_flags, "fallback": {"reason": "datum_gate_failed",
                 "basis": "Restored observed tabulation and NOAA-method reduction of original cached gauge heights in their original zero",
                 "otc_finding": finding}}
        if control:
            basis["control"] = dict(control)
        result["named"][key] = float(values[level])
        result["basis"][key] = basis
    return result, components


def input_binding(station: dict, cs: dict, *, release_sha256: str, member: dict | None,
                  fit_sha256: str | None, fit_manifest_sha256: str, results_sha256: str,
                  tcd_sha256: str, gesla_manifest_sha256: str, timebase_sha256: str) -> dict:
    """Bind source bytes and every consumed native reconstruction input."""
    return {"release_sha256": release_sha256, "station_id": station["station_id"], "set_id": cs["set_id"],
            "source_record_id": cs["source_record_id"], "member_sha256": (member or {}).get("sha256"),
            "member_size": (member or {}).get("size"), "fit_sha256": fit_sha256,
            "fit_manifest_sha256": fit_manifest_sha256, "fit_results_sha256": results_sha256,
            "tcd_sha256": tcd_sha256, "gesla_manifest_sha256": gesla_manifest_sha256,
            "timebase_sha256": timebase_sha256,
            "native_input_sha256": canonical_hash({"constituents": cs.get("constituents", []),
            "datum": cs.get("datum", {}), "time_base": cs.get("time_base"),
            "record_span": cs.get("record_span"), "fit_segment": cs.get("fit_segment"),
            "record_issues": cs.get("record_issues", []), "provenance": cs.get("provenance"),
            "source": cs.get("source"), "source_version": cs.get("source_version")}),
            "original_zero": {key: cs.get("datum", {}).get(key) for key in ("zero", "zero_name", "msl_offset_m")},
            "time_base": cs.get("time_base"), "fit_segment": cs.get("fit_segment")}


def form_factor(cs: dict) -> float:
    amps = {c["name"]: c["amplitude_m"] for c in cs.get("constituents", [])}
    den = amps.get("M2", 0.0) + amps.get("S2", 0.0)
    return (amps.get("K1", 0.0) + amps.get("O1", 0.0)) / den if den else math.inf


def tide_class(cs: dict) -> int:
    factor = form_factor(cs)
    return int(factor >= 0.25) + int(factor >= 1.5) + int(factor > 3.0)


def comparison_method(station: dict, cs: dict) -> str:
    if cut_off_lows(cs):
        return "direct"
    if station.get("country") in ("US", "USA"):
        return datum_t1.us_method(station["lon"])
    return "standard" if 0.25 <= form_factor(cs) <= 3.0 else "modified"


def measurement_segments(series, cs: dict) -> list:
    """Use the stated fit segment, else try actual datum-step segments longest first."""
    fit_segment = cs.get("fit_segment")
    if fit_segment:
        use = (series.times_s >= timestamp(fit_segment["start"])) & (series.times_s <= timestamp(fit_segment["end"]))
        return [replace(series, times_s=series.times_s[use], heights_m=series.heights_m[use])]
    issues = [row for row in cs.get("record_issues", []) if row.get("issue") == "datum_step"]
    if not issues:
        return [series]
    if not len(series) or any(not row.get("start") for row in issues):
        return []
    lo, hi = int(series.times_s[0]), int(series.times_s[-1]) + 1
    edges = [lo, *sorted({timestamp(row["start"]) for row in issues if lo < timestamp(row["start"]) < hi}), hi]
    ranges = sorted(zip(edges, edges[1:]), key=lambda pair: (-(pair[1] - pair[0]), pair[0]))
    segments = []
    for start, end in ranges:
        use = (series.times_s >= start) & (series.times_s < end)
        segments.append(replace(series, times_s=series.times_s[use], heights_m=series.heights_m[use]))
    return segments


def compact_tabulation(series, predict, sampling: str) -> tuple[tabulate.Tabulation, dict]:
    """Tabulate complete years with original context, keeping compact comparison inputs.

    Each year receives a full adjacent month of original samples. No boundary
    sample is fabricated, no partial month is counted and the retained events,
    hourly heights and complete days belong to the year's original tidal days.
    """
    times, heights = series.times_s, series.heights_m
    years = times.astype("datetime64[s]").astype("datetime64[Y]").astype(int) + 1970
    days = tabulate.TidalDays()
    arrays = {key: [] for key in ("msl_t", "msl_h", "ev_t", "ev_h", "ev_kind", "ev_day", "ev_label")}
    complete = set()
    mm = {}
    filled = unfilled = 0
    for year in range(int(years.min()), int(years.max()) + 1):
        start = timestamp(f"{year}-01-01T00:00:00Z")
        end = timestamp(f"{year + 1}-01-01T00:00:00Z")
        use = (times >= start - 32 * 86400) & (times < end + 32 * 86400)
        if use.sum() < 2:
            continue
        tab = tabulate.tabulate(times[use], heights[use], predict, interval_s=3600, sampling=sampling)
        mm.update({ym: row for ym, row in monthly.monthly_means(tab).items() if ym[0] == year})
        event_use = np.array([days.month_of(k)[0] == year for k in tab.ev_day], dtype=bool)
        hour_use = (tab.msl_t >= start) & (tab.msl_t < end)
        for key in arrays:
            arrays[key].append(getattr(tab, key)[hour_use if key.startswith("msl_") else event_use])
        complete.update(k for k in tab.complete_days if days.month_of(k)[0] == year)
        # Only retained-year gaps count; context gaps are not counted twice.
        gap_use = (times[1:] >= start) & (times[1:] < end)
        missing = np.diff(times)[gap_use] - 3600
        filled += int(((missing > 0) & (missing <= tabulate.FILL_MAX_S)).sum())
        unfilled += int((missing > tabulate.FILL_MAX_S).sum())
    values = {key: np.concatenate(parts) for key, parts in arrays.items()}
    result = tabulate.Tabulation(int(times[0]), np.zeros(0), **values, complete_days=frozenset(complete),
                                days=days, filled_gaps=filled, unfilled_gaps=unfilled)
    return result, mm


def save_tab(path: Path, tab: tabulate.Tabulation) -> None:
    np.savez_compressed(path, **{key: getattr(tab, key) for key in
                               ("msl_t", "msl_h", "ev_t", "ev_h", "ev_kind", "ev_day", "ev_label")},
                        complete_days=np.array(sorted(tab.complete_days)), grid_t0=tab.grid_t0,
                        filled_gaps=tab.filled_gaps, unfilled_gaps=tab.unfilled_gaps)


def load_tab(path: Path) -> tabulate.Tabulation:
    with np.load(path, allow_pickle=False) as data:
        return tabulate.Tabulation(int(data["grid_t0"]), np.zeros(0),
            **{key: data[key] for key in ("msl_t", "msl_h", "ev_t", "ev_h", "ev_kind", "ev_day", "ev_label")},
            complete_days=frozenset(data["complete_days"].tolist()), days=tabulate.TidalDays(),
            filled_gaps=int(data["filled_gaps"]), unfilled_gaps=int(data["unfilled_gaps"]))


def choose_control(profile: dict, profiles: list[dict], cache: Path) -> tuple[dict | None, dict | None, dict | None]:
    station, cs = profile["station"], profile["cs"]
    method = comparison_method(station, cs)
    choices = []
    ta = None
    for other in profiles:
        if not other.get("primary") or other["cs"]["set_id"] == cs["set_id"]:
            continue
        if method != "direct" and cut_off_lows(other["cs"]):
            continue
        distance = ticon_sets._km((station["lat"], station["lon"]), (other["station"]["lat"], other["station"]["lon"]))
        if distance > 250 or tide_class(cs) != tide_class(other["cs"]):
            continue
        if ta is None:
            ta = load_tab(cache / profile["cache"])
        tb = load_tab(cache / other["cache"])
        common = ta.complete_days & tb.complete_days
        ma = monthly.monthly_means(ta, days=common, min_days=monthly.MIN_COMMON_DAYS)
        mb = monthly.monthly_means(tb, days=common, min_days=monthly.MIN_COMMON_DAYS)
        keys = sorted(set(ma) & set(mb))
        if not keys:
            continue
        if method == "direct":
            aa = {c["name"]: c["amplitude_m"] for c in cs["constituents"]}
            ab = {c["name"]: c["amplitude_m"] for c in other["cs"]["constituents"]}
            ratio = aa.get("M2", 0.0) / ab["M2"] if ab.get("M2", 0.0) else math.inf
        else:
            denominator = np.mean([mb[key]["MN"] for key in keys])
            ratio = np.mean([ma[key]["MN"] for key in keys]) / denominator if denominator else math.inf
        if not 0.5 <= ratio <= 2.0:
            continue
        level = "MHW" if method == "direct" else "MTL"
        scatter = float(np.std([ma[key][level] - mb[key][level] for key in keys], ddof=1)) if len(keys) >= 3 else None
        # Fewer than three months: nearest. Otherwise minimum monthly scatter, then distance.
        rank = (scatter if scatter is not None else math.inf, distance, other["cs"]["set_id"])
        choices.append((rank, distance, other, keys, ma, tb))
    if not choices:
        return None, None, None
    if all(len(row[3]) < 3 for row in choices):
        choices.sort(key=lambda row: (row[1], row[2]["cs"]["set_id"]))
    else:
        choices.sort(key=lambda row: row[0])
    _, _, other, keys, ma, tb = choices[0]
    common = ta.complete_days & tb.complete_days
    mb = monthly.monthly_means(tb, days=common, min_days=monthly.MIN_COMMON_DAYS)
    profile["report"]["comparison_inputs"] = {
        "short": {f"{y:04d}-{m:02d}": ma[(y, m)] for y, m in keys},
        "control": {f"{y:04d}-{m:02d}": mb[(y, m)] for y, m in keys},
        "control_primary": other["primary"],
        "candidate_count": len(choices), "distance_km": choices[0][1],
        "monthly_difference_sd_m": choices[0][0][0] if len(keys) >= 3 else None,
    }
    if method == "direct":
        values = {level: other["primary"][level] + float(np.mean([ma[k][level] - mb[k][level] for k in keys]))
                  for level in ("MHW", "MHHW")}
    else:
        values = datum_t1.compare_reduce(ta, tb, other["primary"], monthly, method, common=True)
        if values is None or not all(math.isfinite(v) for v in values.values()):
            return None, None, None
        values = monthly.derived(values)
    return values, {key: ma[key] for key in keys}, {"station_id": other["station"]["station_id"],
                                                  "set_id": other["cs"]["set_id"]}


def produce(release_path: Path, gesla_root: Path, fit_run: Path, tcd: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    release = GeslaRelease(gesla_root)
    dump = gesla_sets.load_tcd_dump(tcd)
    manifest_path = fit_run / "timebase_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    audit_path = Path(manifest["inputs"]["timebase_csv"]["path"])
    audit = gesla_sets.load_audit(audit_path.parent)
    manifest, fit_rows = gesla_sets.check_fit_run(fit_run, dump, release, audit)
    roles = load_contributor_roles(Path(__file__).resolve().parents[4] / "data/gesla/contributor_roles.csv")
    common_binding = {"release_sha256": sha256(release_path), "fit_manifest_sha256": sha256(manifest_path),
                      "results_sha256": sha256(fit_run / "results.jsonl"), "tcd_sha256": dump.sha256,
                      "gesla_manifest_sha256": sha256(gesla_root / "MANIFEST.json"), "timebase_sha256": audit.csv_sha256}
    report = {"format": "otc-observed-reduction-v1", "scientific_T1": "FAIL", "normal_acceptance": False,
              "diagnostic_receipt_sha256": DIAGNOSTIC_SHA256, "inputs": common_binding, "records": [], "counts": {}}
    targets = []
    seen = set()
    with release_path.open() as stream:
        for line in stream:
            station = json.loads(line)
            for cs in station.get("constant_sets", []):
                if cs.get("source") not in (gesla_sets.SOURCE, ticon_sets.SOURCE):
                    continue
                if cs["set_id"] in seen:
                    raise ValueError(f"duplicate target set {cs['set_id']}")
                seen.add(cs["set_id"])
                targets.append((station, cs))
    with tempfile.TemporaryDirectory(prefix=".observed-", dir=out) as temporary:
        cache = Path(temporary)
        profiles = []
        for index, (station, cs) in enumerate(targets):
            rid = cs["source_record_id"]
            member = release.members.get(rid)
            fit_path = fit_run / "fits" / f"{rid}.json"
            fit_sha = sha256(fit_path) if fit_path.is_file() else None
            binding = input_binding(station, cs, member=member, fit_sha256=fit_sha, **common_binding)
            profile = {"station": station, "cs": cs, "binding": binding, "cache": f"{index}.npz"}
            detail = {"station_id": station["station_id"], "set_id": cs["set_id"], "source_record_id": rid,
                      "input_binding": binding, "disposition": "unavailable", "reason": None}
            profile["report"] = detail
            report["records"].append(detail)
            profiles.append(profile)
            reason = None
            if member is None:
                reason = "original_member_unavailable"
            elif cs.get("source") == ticon_sets.SOURCE:
                reason = "ticon_original_zero_tie_unavailable"
            elif cs.get("qc_status") == "no_constants" or not cs.get("constituents"):
                reason = "no_constants"
            elif any(r.get("issue") == "no_tide" for r in cs.get("record_issues", [])):
                reason = "no_tide"
            elif cs.get("quantity") != "water_level":
                reason = "not_water_level"
            if reason:
                detail["reason"] = reason
                continue
            try:
                if fit_sha != manifest["fits_sha256"].get(rid) or fit_rows.get(rid, {}).get("outcome") != "fitted":
                    raise gesla_sets.FitIntegrityError(f"{rid}: fit bytes not bound to completed run")
                fit = json.loads(fit_path.read_text())
                meta = release.metadata()[rid]
                corr = gesla_sets.published_correction(audit.rows.get(rid), gesla_sets.declared_hours(meta))
                if corr.label != cs["time_base"]["published"]:
                    raise ValueError("published clock differs from bound fit-run correction")
                gesla_sets.check_stamp(rid, fit, corr.label, corr=corr, speed=lambda n: dump.table[n].speed,
                                      span=(fit["first_s"], fit["last_s"] + 3600))
                record = release.record(rid, roles)
                series = clean(record, corr)
                keep = (series.times_s >= timestamp(f"{dump.table.first_year}-01-01T00:00:00Z")) & (
                    series.times_s < timestamp(f"{dump.table.last_year + 1}-01-01T00:00:00Z"))
                series = replace(series, times_s=series.times_s[keep], heights_m=series.heights_m[keep])
                segments = measurement_segments(series, cs)
                if not segments:
                    detail["reason"] = "datum_step_segment_unavailable"
                    continue
                predict = tabulate.predictor(dump.table, {c["name"]: (c["amplitude_m"], c["phase_deg"])
                    for c in cs["constituents"]}, cs.get("datum", {}).get("msl_offset_m", fit["z0_m"]))
                sampling = "mean" if corr.kind == "utc_mean_start60" else "instantaneous"
                mm = {}
                for series in segments:
                    hours = good_hours(series.times_s)
                    duration_days = (int(series.times_s[-1]) - int(series.times_s[0])) / 86400 if len(series) else 0
                    if len(series) < 2 or hours < 4000 or duration_days < 365:
                        continue
                    tab, mm = compact_tabulation(series, predict, sampling)
                    if mm:
                        break
                detail.update(qc=series.stats, qc_good_hours=hours, original_span_days=duration_days,
                              original_zero=meta.get("DATUM INFORMATION"))
                if len(series) < 2 or hours < 4000 or duration_days < 365:
                    detail["reason"] = "below_365_days_or_4000_qc_good_hours"
                    continue
                detail.update(counted_months=[f"{y:04d}-{m:02d}" for y, m in sorted(mm)],
                              monthly_inputs={f"{y:04d}-{m:02d}": row for (y, m), row in sorted(mm.items())},
                              actual_qc_span={"start": datetime.fromtimestamp(int(series.times_s[0]), timezone.utc).isoformat(),
                                              "end": datetime.fromtimestamp(int(series.times_s[-1]), timezone.utc).isoformat()},
                              filled_gaps=tab.filled_gaps, unfilled_gaps=tab.unfilled_gaps)
                if not mm:
                    detail["reason"] = "no_counted_month"
                    continue
                profile["mm"] = mm
                if is_primary(mm):
                    primary_tab = replace(tab, complete_days=frozenset(
                        k for k in tab.complete_days if 2002 <= tab.days.month_of(k)[0] <= 2020))
                    profile["primary"] = monthly.annual_from_gap_free(primary_tab)
                save_tab(cache / profile["cache"], tab)
            except (ValueError, KeyError, OSError) as exc:
                detail.update(disposition="failed", reason=f"{type(exc).__name__}: {exc}")
        with (out / "observed-drafts.jsonl").open("w") as drafts:
            for profile in profiles:
                detail, cs, mm = profile["report"], profile["cs"], profile.get("mm")
                draft, components = None, {}
                if mm and detail["reason"] is None:
                    control = None
                    primary = bool(profile.get("primary"))
                    method = "first_reduction"
                    if primary:
                        used = {key: row for key, row in mm.items() if 2002 <= key[0] <= 2020}
                        values = profile["primary"]
                    else:
                        values, used, control = choose_control(profile, profiles, cache)
                        if control:
                            method = comparison_method(profile["station"], cs)
                            method = "modified_range_ratio" if method == "modified" else method
                        else:
                            used = own_months(mm)
                            values = {key: float(np.mean([row[key] for row in used.values()]))
                                      for key in monthly.LEVELS} if used else None
                    if values and used:
                        draft, components = draft_datum(cs, values, used, method=method, control=control, primary=primary,
                            gaps=bool(detail["filled_gaps"] or detail["unfilled_gaps"]))
                        detail.update(disposition="reduced" if draft["named"] else "unavailable",
                            reason=None if draft["named"] else "existing_observed_or_computed_keys",
                            method=method, data_span=span_of(used), used_months=[f"{y:04d}-{m:02d}" for y, m in sorted(used)],
                            used_monthly_inputs={f"{y:04d}-{m:02d}": row for (y, m), row in sorted(used.items())},
                            uncertainty_components=components, control=control)
                        if not primary and control is None:
                            detail["own_record_limitation"] = (
                                "Whole calendar-year window retains only its actually counted months, including interior gaps; "
                                "the spec does not define an additional incomplete-year discard policy. No seasonal correction or "
                                "calibrated own-record accuracy is established.")
                    else:
                        detail["reason"] = "no_whole_year_own_record_window"
                row = {"station_id": profile["station"]["station_id"], "set_id": cs["set_id"],
                       "source_record_id": cs["source_record_id"], "input_binding": profile["binding"],
                       "draft_datum": draft, "observed_keys": sorted(draft["named"]) if draft else []}
                drafts.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
                count = detail["disposition"]
                report["counts"][count] = report["counts"].get(count, 0) + 1
    report["counts"]["target_sets"] = len(profiles)
    (out / "observed-reduction-report.json").write_text(json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("release", "gesla-root", "fit-run", "tcd", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    report = produce(args.release, args.gesla_root, args.fit_run, args.tcd, args.out)
    print(json.dumps(report["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
