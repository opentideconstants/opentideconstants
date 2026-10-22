"""Reduce immutable cached original gauge records to labelled observed drafts.

No fitting, acquisition, chart-datum assignment or scientific admission occurs here.
The restored DT1 reconstruction and monthly arithmetic remain the measurement engine.
"""
from __future__ import annotations

import argparse
import calendar
import copy
import fcntl
import hashlib
import importlib
import json
import math
import multiprocessing
import os
import shutil
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
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
    best = None
    nearest_short = None
    candidate_count = 0
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
        required = ("MN", "DLQ", "DHQ") if method == "standard" else ("MN", "GT") if method == "modified" else ()
        undefined = [{"control_set_id": other["cs"]["set_id"], "month": f"{y:04d}-{m:02d}",
                      "level": level, "reason": "zero_control_denominator" if mb[(y, m)][level] == 0 else "nonfinite_control_denominator"}
                     for y, m in keys for level in required
                     if not math.isfinite(mb[(y, m)][level]) or mb[(y, m)][level] == 0]
        if undefined:
            profile["report"].setdefault("ineligible_controls", []).extend(undefined)
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
        candidate_count += 1
        choice = (rank, distance, other)
        if len(keys) >= 3:
            if best is None or rank < best[0]:
                best = choice
        elif nearest_short is None or (distance, other["cs"]["set_id"]) < (nearest_short[1], nearest_short[2]["cs"]["set_id"]):
            nearest_short = choice
        # No candidate tabulation or monthly arrays are retained in the ranking.
        del tb, ma, mb
    chosen = best if best is not None else nearest_short
    if chosen is None:
        return None, None, None
    rank, distance, other = chosen
    tb = load_tab(cache / other["cache"])
    common = ta.complete_days & tb.complete_days
    ma = monthly.monthly_means(ta, days=common, min_days=monthly.MIN_COMMON_DAYS)
    mb = monthly.monthly_means(tb, days=common, min_days=monthly.MIN_COMMON_DAYS)
    keys = sorted(set(ma) & set(mb))
    profile["report"]["comparison_inputs"] = {
        "short": {f"{y:04d}-{m:02d}": ma[(y, m)] for y, m in keys},
        "control": {f"{y:04d}-{m:02d}": mb[(y, m)] for y, m in keys},
        "control_primary": other["primary"],
        "candidate_count": candidate_count, "distance_km": distance,
        "monthly_difference_sd_m": rank[0] if len(keys) >= 3 else None,
    }
    if method == "direct":
        values = {level: other["primary"][level] + float(np.mean([ma[k][level] - mb[k][level] for k in keys]))
                  for level in ("MHW", "MHHW")}
    else:
        class ComparisonMonths:
            MIN_COMMON_DAYS = monthly.MIN_COMMON_DAYS

            @staticmethod
            def monthly_means(tab, *, days, min_days):
                if days != common or min_days != monthly.MIN_COMMON_DAYS:
                    raise ValueError("winning-pair common-month arguments changed")
                if tab is ta:
                    return ma
                if tab is tb:
                    return mb
                raise ValueError("unexpected winning-pair tabulation")

        values = datum_t1.compare_reduce(ta, tb, other["primary"], ComparisonMonths, method, common=True)
        if values is None or not all(math.isfinite(v) for v in values.values()):
            return None, None, None
        values = monthly.derived(values)
    return values, {key: ma[key] for key in keys}, {"station_id": other["station"]["station_id"],
                                                  "set_id": other["cs"]["set_id"]}


def measure_profile(job: dict, release, dump, fit_run, manifest, fit_rows, audit, roles):
    station, cs, binding = job["station"], job["cs"], job["binding"]
    rid = cs["source_record_id"]
    member = release.members.get(rid)
    fit_path = fit_run / "fits" / f"{rid}.json"
    fit_sha = binding["fit_sha256"]
    detail = {"station_id": station["station_id"], "set_id": cs["set_id"], "source_record_id": rid,
              "input_binding": binding, "disposition": "unavailable", "reason": None}
    profile = {**job, "report": detail}
    reason = None
    if member is None:
        reason = "original_member_unavailable"
    elif cs.get("quantity") != "water_level":
        reason = "not_water_level"
    if reason:
        detail["reason"] = reason
        return profile, None
    try:
        if fit_sha != manifest["fits_sha256"].get(rid) or fit_rows.get(rid, {}).get("outcome") != "fitted":
            raise gesla_sets.FitIntegrityError(f"{rid}: fit bytes not bound to completed run")
        if sha256(fit_path) != fit_sha:
            raise gesla_sets.FitIntegrityError(f"{rid}: original-record fit bytes changed")
        fit = json.loads(fit_path.read_text())
        meta = release.metadata()[rid]
        corr = gesla_sets.published_correction(audit.rows.get(rid), gesla_sets.declared_hours(meta))
        if cs.get("source") != ticon_sets.SOURCE and corr.label != cs["time_base"]["published"]:
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
            return profile, None
        if cs.get("source") == ticon_sets.SOURCE or not cs.get("constituents"):
            # Reconstruct this original gauge record with its authenticated own fit.
            # Never combine TICON phases/mean with an unrelated gauge-zero offset.
            coefficients = {name: (c["amp_m"], c["phase_deg"]) for name, c in fit["constituents"].items()}
            predict = tabulate.predictor(dump.table, coefficients, fit["z0_m"])
            detail["reconstruction"] = {"source": "original_record_fit", "fit_sha256": fit_sha,
                                        "published_clock": corr.label, "tie_to_harmonic_set": "unknown"}
        else:
            predict = tabulate.predictor(dump.table, {c["name"]: (c["amplitude_m"], c["phase_deg"])
                for c in cs["constituents"]}, cs.get("datum", {}).get("msl_offset_m", fit["z0_m"]))
        sampling = "mean" if corr.kind == "utc_mean_start60" else "instantaneous"
        mm = {}
        for series in segments:
            hours = good_hours(series.times_s)
            duration_days = (int(series.times_s[-1]) - int(series.times_s[0])) / 86400 if len(series) else 0
            if len(series) < 2:
                continue
            # QC centres hourly means; the tabulator consumes interval starts.
            samples = replace(series, times_s=series.times_s - 1800) if sampling == "mean" else series
            tab, mm = compact_tabulation(samples, predict, sampling)
            if mm:
                break
        detail.update(qc=series.stats, qc_good_hours=hours, original_span_days=duration_days,
                      original_zero=meta.get("DATUM INFORMATION"))
        if len(series) < 2:
            detail["reason"] = "insufficient_samples"
            return profile, None
        if hours < 4000 or duration_days < 365:
            detail.setdefault("limitations", []).append("below_365_days_or_4000_qc_good_hours")
        detail.update(counted_months=[f"{y:04d}-{m:02d}" for y, m in sorted(mm)],
                      monthly_inputs={f"{y:04d}-{m:02d}": row for (y, m), row in sorted(mm.items())},
                      actual_qc_span={"start": datetime.fromtimestamp(int(series.times_s[0]), timezone.utc).isoformat(),
                                      "end": datetime.fromtimestamp(int(series.times_s[-1]), timezone.utc).isoformat()},
                      filled_gaps=tab.filled_gaps, unfilled_gaps=tab.unfilled_gaps)
        if not mm:
            detail["reason"] = "no_counted_month"
            return profile, None
        profile["mm"] = mm
        if is_primary(mm) and cs.get("source") != ticon_sets.SOURCE and not detail.get("limitations"):
            primary_tab = replace(tab, complete_days=frozenset(
                k for k in tab.complete_days if 2002 <= tab.days.month_of(k)[0] <= 2020))
            profile["primary"] = monthly.annual_from_gap_free(primary_tab)
        return profile, tab
    except (ValueError, KeyError, OSError) as exc:
        detail.update(disposition="failed", reason=f"{type(exc).__name__}: {exc}")
    return profile, None


def retained_observation(profile: dict, values: dict, mm: dict, method: str, *, counted_days=None) -> dict:
    """Preserve a measured candidate independently of predictive datum selection."""
    cs, detail = profile["cs"], profile["report"]
    limitations = ["datum_gate_failed", "high_low_labels_unverified", "station_accuracy_uncalibrated"]
    limitations.extend(detail.get("limitations", []))
    if detail.get("reason"):
        limitations.append(detail["reason"])
    if method == "monthly_tabulation":
        limitations.append("monthly_intermediate")
    if cut_off_lows(cs):
        limitations.append("truncated_lows")
    code = cs.get("time_base", {}).get("code")
    if code in ("unverified", "disputed"):
        limitations.append(f"time_base_{code}")
    limitations.extend(row["issue"] for row in cs.get("record_issues", []) if row.get("issue"))
    if detail.get("filled_gaps") or detail.get("unfilled_gaps"):
        limitations.append("gaps")
    span = span_of(mm)
    basis = {"counted_months": [f"{year:04d}-{month:02d}" for year, month in sorted(mm)],
             "quality": detail.get("qc", {}), "actual_sample_window": detail.get("actual_qc_span"),
             "reconstruction": detail.get("reconstruction")}
    if detail.get("measurement_reuse"):
        basis["measurement_reuse"] = detail["measurement_reuse"]
    if counted_days is not None:
        basis["counted_days"] = counted_days
    return {"kind": "observed", "method": method,
            "values": {key.lower(): float(value) for key, value in values.items()
                       if key in monthly.LEVELS and isinstance(value, (int, float)) and math.isfinite(value)},
            "units": "m", "frame": {"kind": "gauge_zero", "name": detail.get("original_zero"), "tie_status": "unknown"},
            "source_record_id": cs["source_record_id"], "set_id": cs["set_id"],
            "input_binding": profile["binding"], "window": {key: span[key] for key in ("start", "end")},
            "reliability": {"status": "limited", "limitations": list(dict.fromkeys(limitations))},
            "uncertainty_m": None, "basis": basis}


def retained_own_record(profile: dict) -> dict | None:
    """Retain the existing own-record arithmetic without an epoch or tie claim."""
    used = own_months(profile.get("mm", {}))
    if not used:
        return None
    values = {key: float(np.mean([row[key] for row in used.values()])) for key in monthly.LEVELS}
    candidate = retained_observation(profile, values, used, "first_reduction")
    candidate["reliability"]["limitations"].extend([
        "no_qualified_control", "own_record_window_not_official_epoch",
        "counted_months_only_no_seasonal_correction"])
    return candidate


def reduce_profile(profile: dict, primaries: list[dict], cache: Path) -> tuple[dict, dict]:
    detail, cs, mm = profile["report"], profile["cs"], profile.get("mm")
    draft, components = None, {}
    reduced_candidate = None
    if mm and detail["reason"] is None and cs.get("source") != ticon_sets.SOURCE and not detail.get("limitations"):
        control = None
        primary = bool(profile.get("primary"))
        method = "first_reduction"
        if primary:
            used = {key: row for key, row in mm.items() if 2002 <= key[0] <= 2020}
            values = profile["primary"]
        else:
            values, used, control = choose_control(profile, primaries, cache)
            if control:
                method = comparison_method(profile["station"], cs)
                method = "modified_range_ratio" if method == "modified" else method
            else:
                used = own_months(mm)
                values = {key: float(np.mean([row[key] for row in used.values()]))
                          for key in monthly.LEVELS} if used else None
        if values and used:
            reduced_candidate = (values, used, method)
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
    retained = [retained_observation(profile, values, {ym: values}, "monthly_tabulation", counted_days=values.get("n_days"))
                for ym, values in sorted((mm or {}).items())]
    if reduced_candidate:
        values, used, method = reduced_candidate
        candidate = retained_observation(profile, values, used, method)
        if control:
            candidate["basis"].update(control=dict(control), epoch=dict(EPOCH))
        retained.append(candidate)
    own = retained_own_record(profile)
    if own:
        equivalent = next((candidate for candidate in retained
                           if candidate["method"] == own["method"] and candidate["values"] == own["values"]
                           and candidate["window"] == own["window"]), None)
        if equivalent is None:
            retained.append(own)
        else:
            equivalent["reliability"]["limitations"] = own["reliability"]["limitations"]
    row["retained_observed"] = [candidate for candidate in retained if candidate["values"]]
    return row, detail


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".part")
    with temporary.open("w") as stream:
        json.dump(value, stream, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def write_checkpoint(cache: Path, key: str, job_sha: str, profile: dict, tab, *, retained_arrays: Path | None = None) -> None:
    """Publish the completion marker last; an interrupted write is never a hit."""
    files = {}
    if retained_arrays is not None:
        temporary = cache / f"{key}.part.npz"
        shutil.copyfile(retained_arrays, temporary)
        os.replace(temporary, cache / f"{key}.npz")
        files[f"{key}.npz"] = sha256(cache / f"{key}.npz")
    elif tab is not None:
        temporary = cache / f"{key}.part.npz"
        save_tab(temporary, tab)
        os.replace(temporary, cache / f"{key}.npz")
        files[f"{key}.npz"] = sha256(cache / f"{key}.npz")
    payload = dict(profile)
    if "mm" in payload:
        payload["mm"] = {f"{y:04d}-{m:02d}": row for (y, m), row in payload["mm"].items()}
    path = cache / f"{key}.profile.json"
    write_json(path, payload)
    files[path.name] = sha256(path)
    write_json(cache / f"{key}.done.json", {"format": "otc-observed-checkpoint-v1", "job_sha256": job_sha,
                                          "files": files, "measurement_implementation": implementation_identity()})


def read_checkpoint(cache: Path, key: str, job_sha: str, *, require_complete: bool = False) -> dict | None:
    marker = cache / f"{key}.done.json"
    if not marker.exists():
        if require_complete:
            raise ValueError(f"completed run checkpoint missing: {key}")
        return None
    try:
        manifest = json.loads(marker.read_text())
        if manifest.get("format") != "otc-observed-checkpoint-v1" or manifest.get("job_sha256") != job_sha:
            raise ValueError("identity mismatch")
        files = manifest["files"]
        if set(files) not in ({f"{key}.profile.json"}, {f"{key}.profile.json", f"{key}.npz"}):
            raise ValueError("unexpected artifact set")
        for name, digest in files.items():
            if sha256(cache / name) != digest:
                raise ValueError(f"artifact hash mismatch: {name}")
        profile = json.loads((cache / f"{key}.profile.json").read_text())
        if "mm" in profile:
            profile["mm"] = {tuple(map(int, ym.split("-"))): row for ym, row in profile["mm"].items()}
        if profile.get("mm") and profile["report"].get("reason") is None and f"{key}.npz" not in files:
            raise ValueError("measurement arrays missing")
        return profile
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise ValueError(f"invalid checkpoint {key}: {exc}") from exc


def implementation_identity() -> dict:
    modules = ("gesla.qc", "gesla.read_gesla", "gesla.record", "gesla.timebase_verdict", "gesla.timebase_audit",
               "gesla.tcd_table", "otc_pipeline.gesla_sets", "otc_pipeline.datum_t1",
               "otc_pipeline.observed_datums.tabulate", "otc_pipeline.observed_datums.monthly")
    return {"producer_sha256": sha256(Path(__file__)), "numpy_version": np.__version__,
            "modules": {name: sha256(Path(importlib.import_module(name).__file__)) for name in modules}}


def primary_summary(profile: dict) -> dict:
    cs = profile["cs"]
    # Only primary selection metadata; the full native inputs stay authenticated on disk.
    short_cs = {key: cs[key] for key in ("set_id", "record_issues", "datum") if key in cs}
    short_cs["constituents"] = [row for row in cs["constituents"] if row["name"] in ("M2", "S2", "K1", "O1")]
    return {"station": profile["station"], "cs": short_cs, "primary": profile["primary"], "cache": profile["cache"]}


_WORKER_CONTEXT = None


def _initialize_measurement_worker(gesla_root: Path, fit_run: Path, tcd: Path, roles_path: Path) -> None:
    global _WORKER_CONTEXT
    release = GeslaRelease(gesla_root)
    dump = gesla_sets.load_tcd_dump(tcd)
    manifest = json.loads((fit_run / "timebase_manifest.json").read_text())
    audit = gesla_sets.load_audit(Path(manifest["inputs"]["timebase_csv"]["path"]).parent)
    manifest, fit_rows = gesla_sets.check_fit_run(fit_run, dump, release, audit)
    _WORKER_CONTEXT = (release, dump, fit_run, manifest, fit_rows, audit, load_contributor_roles(roles_path))


def _measure_checkpoint(cache: Path, item: dict, context) -> str:
    job = json.loads((cache / f"{item['key']}.job.json").read_text())
    if canonical_hash(job) != item["job_sha256"]:
        raise ValueError(f"measurement job changed: {item['set_id']}")
    print(f"measure {item['set_id']} pid={os.getpid()}", flush=True)
    profile, tab = measure_profile(job, *context)
    write_checkpoint(cache, item["key"], item["job_sha256"], profile, tab)
    print(f"checkpoint {item['set_id']} pid={os.getpid()}", flush=True)
    return item["key"]


def _measurement_worker(cache: Path, item: dict) -> str:
    if _WORKER_CONTEXT is None:
        raise RuntimeError("measurement worker was not initialized")
    return _measure_checkpoint(cache, item, _WORKER_CONTEXT)


def _complete_measurements(index: list[dict], cache: Path, context, worker_inputs: tuple,
                           workers: int, require_complete: bool) -> tuple[int, int]:
    """Bound pending jobs and arrays; no worker performs control selection."""
    executor = None
    pending = set()
    prior_children = {child.pid for child in multiprocessing.active_children()}
    measured = reused = 0
    authenticated_members = set()
    release = context[0]

    def reap() -> None:
        done, _ = wait(pending, return_when=FIRST_COMPLETED)
        for future in done:
            future.result()
            pending.remove(future)

    try:
        for item in index:
            key, job_sha = item["key"], item["job_sha256"]
            job = json.loads((cache / f"{key}.job.json").read_text())
            profile = read_checkpoint(cache, key, job_sha, require_complete=require_complete)
            if profile is None:
                if workers == 1:
                    _measure_checkpoint(cache, item, context)
                else:
                    if executor is None:
                        executor = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"),
                            initializer=_initialize_measurement_worker, initargs=worker_inputs)
                    if len(pending) >= workers:
                        reap()
                    pending.add(executor.submit(_measurement_worker, cache, item))
                measured += 1
            else:
                # Reuse authenticates original bytes, never just an old caller context.
                rid = job["cs"]["source_record_id"]
                if rid in release.members and rid not in authenticated_members:
                    release.read_member(rid)
                    authenticated_members.add(rid)
                if profile.get("binding") != job["binding"] or profile.get("cs") != job["cs"] or profile.get("station") != job["station"]:
                    raise ValueError(f"checkpoint source context mismatch: {item['set_id']}")
                reused += 1
                print(f"reuse {item['set_id']}", flush=True)
                print(f"checkpoint {item['set_id']}", flush=True)
            del profile, job
        while pending:
            reap()
    except BaseException:
        for future in pending:
            future.cancel()
        # Python3.12 has no executor terminate_workers API. Use public Process
        # lifecycle methods on only children created by this owned coordinator.
        children = [child for child in multiprocessing.active_children() if child.pid not in prior_children]
        for child in children:
            child.terminate()
        for child in children:
            child.join(timeout=5)
            if child.is_alive():
                child.kill()
                child.join()
        raise
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
    return measured, reused


def authenticate_measured_inputs(source: Path, previous: dict, identity: dict) -> None:
    """Authenticate one pinned legacy-to-corrected-mean transition, never rebind hashes."""
    import ast

    if sha256(source / "observed-run.json") != "a8dfec8864ca6944a01d27a60d9e7a3f5799faec7d217a053d4cef75ef583728":
        raise ValueError("measured-input frozen run identity mismatch")
    old = previous["identity"]
    if old["implementation"]["producer_sha256"] != "dd7ab607b2ac02d4236fc284259f64c1b4d21c45b9cf75e8502f5a849672fbe6":
        raise ValueError("measured-input original producer mismatch")
    if old["inputs"] != identity["inputs"] or old["roles_sha256"] != identity["roles_sha256"]:
        raise ValueError("measured-input current source identity mismatch")
    expected_modules = dict(old["implementation"]["modules"])
    monthly_module = "otc_pipeline.observed_datums.monthly"
    if expected_modules[monthly_module] != "af03e068fd4b7f7df23ead3f1f943e6d2e385702d3dff557a02ece27c821c426":
        raise ValueError("measured-input original monthly dependency mismatch")
    # One reviewed call-local index reuse; original measurements retain the old hash.
    expected_modules[monthly_module] = "ed260f6d3c8b8b77c9d3931380ab7c55e28f56fee1dbf3f65ddccfe70623f0cc"
    if (expected_modules != identity["implementation"]["modules"] or
            old["implementation"]["numpy_version"] != identity["implementation"]["numpy_version"]):
        raise ValueError("measured-input scientific dependency mismatch")
    tree = ast.parse(Path(__file__).read_text())
    surface = ast.Module(body=[node for node in tree.body if not
                        (isinstance(node, ast.FunctionDef) and node.name == "authenticate_measured_inputs")], type_ignores=[])
    digest = hashlib.sha256(ast.dump(surface, include_attributes=False).encode()).hexdigest()
    if (digest != "504bac4b6455375e582144330456f15d7b5874d90ad9e4901b83ec075843736c" or
            identity["implementation"]["producer_sha256"] != sha256(Path(__file__))):
        raise ValueError("measured-input measurement implementation changed")


def prepare_mean_refresh(source: Path, cache: Path, index: list[dict], identity: dict, reference_path: Path) -> list[dict]:
    """Preserve legacy nonmeans byte-for-byte; only corrected means may be measured."""
    previous = json.loads((source / "observed-run.json").read_text())
    authenticate_measured_inputs(source, previous, identity)
    if index != previous["targets"]:
        raise ValueError("mean-refresh complete target cohort mismatch")
    original = source / "checkpoints"
    markers, refreshed = {}, []
    for item in index:
        key, job_sha = item["key"], item["job_sha256"]
        job = json.loads((original / f"{key}.job.json").read_text())
        if canonical_hash(job) != job_sha:
            raise ValueError(f"mean-refresh source job mismatch: {item['set_id']}")
        profile = read_checkpoint(original, key, job_sha, require_complete=True)
        if any(profile.get(name) != job[name] for name in ("binding", "cs", "station", "cache")):
            raise ValueError(f"mean-refresh source context mismatch: {item['set_id']}")
        marker_path = original / f"{key}.done.json"
        markers[key] = sha256(marker_path)
        mean = job["cs"].get("time_base", {}).get("published") == "utc_mean_start60"
        if mean:
            refreshed.append(item)
        names = [f"{key}.job.json"]
        if not mean:
            names += [f"{key}.done.json", *json.loads(marker_path.read_text())["files"]]
        for name in names:
            target = cache / name
            expected = original / name
            if target.exists() or target.is_symlink():
                if not target.is_symlink() or target.resolve() != expected.resolve():
                    raise ValueError(f"mean-refresh retained reference mismatch: {name}")
            else:
                target.symlink_to(expected.resolve())
        if mean:
            for suffix in ("done.json", "profile.json", "npz"):
                if (cache / f"{key}.{suffix}").is_symlink():
                    raise ValueError(f"stale mean checkpoint reference: {item['set_id']}")
            completed = cache / f"{key}.done.json"
            if completed.exists():
                marker = json.loads(completed.read_text())
                if marker.get("measurement_implementation") != identity["implementation"]:
                    raise ValueError(f"stale mean measurement implementation: {item['set_id']}")
                read_checkpoint(cache, key, job_sha, require_complete=True)
                old_table = json.loads(marker_path.read_text())["files"].get(f"{key}.npz")
                if old_table and marker["files"].get(f"{key}.npz") == old_table:
                    raise ValueError(f"stale mean measurement arrays: {item['set_id']}")
    if len(refreshed) != 141 or canonical_hash({"ordered": list(markers.values())}) != "73fd254c82eb9832515799d74fb02cbb361c1c0e0530ac08e3eda550dcf5d5d4":
        raise ValueError("mean-refresh frozen checkpoint cohort mismatch")
    reference = {"source": str(source.resolve()), "source_run_sha256": sha256(source / "observed-run.json"),
        "original_measurement_implementation": previous["identity"]["implementation"],
        "corrected_mean_measurement_implementation": identity["implementation"],
        "original_checkpoint_markers": markers, "corrected_mean_keys": [item["key"] for item in refreshed],
        "measured": len(refreshed), "reused": len(index) - len(refreshed)}
    if reference_path.exists():
        if json.loads(reference_path.read_text()) != reference:
            raise ValueError("mean-refresh provenance mismatch")
    else:
        write_json(reference_path, reference)
    return refreshed


def _initialize_reduction_worker(cache: Path, primaries: list[dict]) -> None:
    global _WORKER_CONTEXT
    _WORKER_CONTEXT = (cache, primaries)


def _reduction_worker(item: dict) -> tuple[dict, dict]:
    cache, primaries = _WORKER_CONTEXT
    profile = read_checkpoint(cache, item["key"], item["job_sha256"], require_complete=True)
    return reduce_profile(profile, primaries, cache)


def reduction_results(index: list[dict], cache: Path, primaries: list[dict], workers: int, *, spool_parent: Path):
    """Refill on completion; spool out-of-order payloads rather than block workers."""
    from tempfile import TemporaryDirectory

    if workers == 1:
        for item in index:
            profile = read_checkpoint(cache, item["key"], item["job_sha256"], require_complete=True)
            yield reduce_profile(profile, primaries, cache)
        return
    prior_children = {child.pid for child in multiprocessing.active_children()}
    # Every attempt gets a fresh namespace: interrupted/foreign spools are never hits.
    with TemporaryDirectory(prefix=".reduction-", dir=spool_parent) as directory:
        spool = Path(directory)
        executor = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"),
            initializer=_initialize_reduction_worker, initargs=(cache, primaries))
        pending, completed = {}, {}
        jobs = iter(enumerate(index))

        def submit_one():
            try:
                position, item = next(jobs)
            except StopIteration:
                return
            pending[executor.submit(_reduction_worker, item)] = position

        try:
            for _ in range(workers):
                submit_one()
            emitted = 0
            while pending or completed:
                if pending:
                    done, _ = wait(pending, timeout=0 if emitted in completed else None,
                                   return_when=FIRST_COMPLETED)
                    while done:
                        future = done.pop()
                        position = pending.pop(future)
                        row, detail = future.result()
                        path = spool / f"{position}.json"
                        write_json(path, {"position": position, "job_sha256": index[position]["job_sha256"],
                                          "row": row, "detail": detail})
                        completed[position] = sha256(path)
                        del row, detail, future
                        submit_one()
                if emitted in completed:
                    path = spool / f"{emitted}.json"
                    if sha256(path) != completed.pop(emitted):
                        raise ValueError(f"reduction spool artifact changed: {emitted}")
                    payload = json.loads(path.read_text())
                    if (payload["position"] != emitted or payload["job_sha256"] != index[emitted]["job_sha256"] or
                            payload["row"]["set_id"] != index[emitted]["set_id"]):
                        raise ValueError(f"reduction spool source identity mismatch: {emitted}")
                    path.unlink()
                    yield payload["row"], payload["detail"]
                    del payload
                    emitted += 1
        except BaseException:
            for future in pending:
                future.cancel()
            children = [child for child in multiprocessing.active_children() if child.pid not in prior_children]
            for child in children:
                child.terminate()
            for child in children:
                child.join(timeout=5)
                if child.is_alive():
                    child.kill()
                    child.join()
            raise
        finally:
            executor.shutdown(wait=True, cancel_futures=True)


def produce(release_path: Path, gesla_root: Path, fit_run: Path, tcd: Path, out: Path, *, resume: bool = False,
            workers: int = 1, measured_inputs: Path | None = None) -> dict:
    """Bounded independent measurements precede ordered full-cohort reductions."""
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError("workers must be an integer from 1 to 4")
    if measured_inputs is not None and out.resolve() == measured_inputs.resolve():
        raise ValueError("measured-input reduction requires a fresh separate output")
    out.mkdir(parents=True, exist_ok=True)
    with (out / ".lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(f"{out}: producer is already running") from exc
        return _produce(release_path, gesla_root, fit_run, tcd, out, resume=resume, workers=workers,
                        measured_inputs=measured_inputs)


def _produce(release_path: Path, gesla_root: Path, fit_run: Path, tcd: Path, out: Path, *, resume: bool,
             workers: int, measured_inputs: Path | None = None) -> dict:
    run_path = out / "observed-run.json"
    if not resume and any(path.name != ".lock" for path in out.iterdir()):
        raise ValueError(f"{out}: occupied; use --resume with the identical inputs")
    if resume and not run_path.is_file():
        raise ValueError(f"{out}: no complete run manifest for --resume")
    source_run = (measured_inputs / "observed-run.json") if measured_inputs is not None else run_path
    previous = json.loads(source_run.read_text()) if resume or measured_inputs is not None else None
    release = GeslaRelease(gesla_root)
    dump = gesla_sets.load_tcd_dump(tcd)
    manifest_path = fit_run / "timebase_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    audit = gesla_sets.load_audit(Path(manifest["inputs"]["timebase_csv"]["path"]).parent)
    manifest, fit_rows = gesla_sets.check_fit_run(fit_run, dump, release, audit)
    roles_path = Path(__file__).resolve().parents[4] / "data/gesla/contributor_roles.csv"
    roles = load_contributor_roles(roles_path)
    common_binding = {"release_sha256": sha256(release_path), "fit_manifest_sha256": sha256(manifest_path),
                      "results_sha256": sha256(fit_run / "results.jsonl"), "tcd_sha256": dump.sha256,
                      "gesla_manifest_sha256": sha256(gesla_root / "MANIFEST.json"), "timebase_sha256": audit.csv_sha256}
    identity = {"inputs": common_binding, "roles_sha256": sha256(roles_path), "implementation": implementation_identity()}
    if measured_inputs is not None:
        authenticate_measured_inputs(measured_inputs, previous, identity)
    elif previous is not None and (previous.get("format") != "otc-observed-run-v1" or previous.get("identity") != identity):
        raise ValueError("resume source/implementation identity mismatch; old results cannot be rebound")
    cache = out / "checkpoints"
    cache.mkdir(exist_ok=True)
    index, seen = [], set()
    # Re-enumerate the actual complete release, authenticating each job from current inputs.
    with release_path.open() as stream:
        for line in stream:
            station = json.loads(line)
            for cs in station.get("constant_sets", []):
                if cs.get("source") not in (gesla_sets.SOURCE, ticon_sets.SOURCE):
                    continue
                if cs["set_id"] in seen:
                    raise ValueError(f"duplicate target set {cs['set_id']}")
                seen.add(cs["set_id"])
                rid = cs["source_record_id"]
                fit_path = fit_run / "fits" / f"{rid}.json"
                binding = input_binding(station, cs, member=release.members.get(rid),
                    fit_sha256=sha256(fit_path) if fit_path.is_file() else None, **common_binding)
                key = canonical_hash(binding)
                job = {"station": {name: station[name] for name in ("station_id", "lat", "lon", "country") if name in station},
                       "cs": cs, "binding": binding, "cache": f"{key}.npz"}
                job_sha = canonical_hash(job)
                job_path = ((measured_inputs / "checkpoints") if measured_inputs is not None else cache) / f"{key}.job.json"
                if resume or measured_inputs is not None:
                    if not job_path.is_file() or canonical_hash(json.loads(job_path.read_text())) != job_sha:
                        raise ValueError(f"resume current-input job mismatch: {cs['set_id']}")
                else:
                    write_json(job_path, job)
                index.append({"key": key, "job_sha256": job_sha, "set_id": cs["set_id"]})
    run = {"format": "otc-observed-run-v1", "identity": identity, "targets": index}
    if previous is not None and (previous["targets"] != index or (measured_inputs is None and previous != run)):
        raise ValueError("resume full-cohort target manifest mismatch")
    if resume and measured_inputs is not None and json.loads(run_path.read_text()) != run:
        raise ValueError("resume current reduction source/implementation identity mismatch")
    if not resume:
        write_json(run_path, run)
    completion_path = out / "observed-completion.json"
    context = (release, dump, fit_run, manifest, fit_rows, audit, roles)
    if measured_inputs is None:
        measured, reused = _complete_measurements(index, cache, context, (gesla_root, fit_run, tcd, roles_path),
                                                 workers, completion_path.exists())
    else:
        refresh = prepare_mean_refresh(measured_inputs, cache, index, identity, out / "observed-measurement-reference.json")
        measured, _ = _complete_measurements(refresh, cache, context, (gesla_root, fit_run, tcd, roles_path),
                                             workers, completion_path.exists())
        reused = len(index) - measured
    primaries = []
    completed_profiles = []
    for item in index:
        key, job_sha = item["key"], item["job_sha256"]
        profile = read_checkpoint(cache, key, job_sha, require_complete=True)
        if profile is None:
            raise ValueError(f"checkpoint missing after measurement barrier: {item['set_id']}")
        if measured_inputs is not None:
            job = json.loads((cache / f"{key}.job.json").read_text())
            if any(profile.get(name) != job[name] for name in ("binding", "cs", "station", "cache")):
                raise ValueError(f"measured-input checkpoint source context mismatch: {item['set_id']}")
            if item in refresh and json.loads((cache / f"{key}.done.json").read_text()).get("measurement_implementation") != identity["implementation"]:
                raise ValueError(f"corrected mean measurement implementation mismatch: {item['set_id']}")
        if profile.get("primary") and profile["report"].get("reason") is None:
            primaries.append(primary_summary(profile))
        completed_profiles.append(sha256(cache / f"{key}.done.json"))
        del profile
    # Full-cohort barrier: every profile is complete before any control selection.
    reduction_identity = {"run_sha256": sha256(run_path), "profile_manifests_sha256": canonical_hash({"ordered": completed_profiles})}
    if measured_inputs is not None:
        reduction_identity["measurement_source_run_sha256"] = sha256(source_run)
        reduction_identity["measurement_reference_sha256"] = sha256(out / "observed-measurement-reference.json")
    if completion_path.exists():
        completion = json.loads(completion_path.read_text())
        if completion.get("identity") != reduction_identity:
            raise ValueError("completed reduction cohort/profile identity mismatch")
        expected_names = {"observed-drafts.jsonl", "observed-reduction-report.json"}
        if set(completion.get("files", {})) != expected_names:
            raise ValueError("completed reduction output manifest mismatch")
        for name, digest in completion["files"].items():
            if not (out / name).is_file() or sha256(out / name) != digest:
                raise ValueError(f"completed reduction artifact mismatch: {name}")
        print(f"complete measured={measured} reused={reused}", flush=True)
        return {"counts": completion["counts"]}
    report = {"format": "otc-observed-reduction-v1", "scientific_T1": "FAIL", "normal_acceptance": False,
              "diagnostic_receipt_sha256": DIAGNOSTIC_SHA256, "inputs": common_binding}
    counts = {}
    draft_temp = out / "observed-drafts.jsonl.part"
    report_temp = out / "observed-reduction-report.json.part"
    with draft_temp.open("w") as drafts, report_temp.open("w") as records:
        prefix = json.dumps(report, sort_keys=True, allow_nan=False)
        records.write(prefix[:-1] + ', "records": [')
        for position, (row, detail) in enumerate(reduction_results(index, cache, primaries, workers, spool_parent=out)):
            drafts.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
            if position:
                records.write(",\n")
            records.write(json.dumps(detail, sort_keys=True, allow_nan=False))
            disposition = detail["disposition"]
            counts[disposition] = counts.get(disposition, 0) + 1
            del row, detail
        counts["target_sets"] = len(index)
        records.write('], "counts": ' + json.dumps(counts, sort_keys=True) + "}\n")
        for stream in (drafts, records):
            stream.flush()
            os.fsync(stream.fileno())
    os.replace(draft_temp, out / "observed-drafts.jsonl")
    os.replace(report_temp, out / "observed-reduction-report.json")
    files = {name: sha256(out / name) for name in ("observed-drafts.jsonl", "observed-reduction-report.json")}
    write_json(completion_path, {"identity": reduction_identity, "files": files, "counts": counts})
    print(f"complete measured={measured} reused={reused}", flush=True)
    return {"counts": counts}


def reuse_measurement(profile: dict, job: dict, context) -> dict | None:
    """Reuse only identical measured inputs, retaining both target and origin bindings."""
    if not profile.get("mm") or context is None:
        return None
    binding, previous = job["binding"], profile["binding"]
    keys = ("source_record_id", "member_sha256", "member_size", "fit_sha256", "fit_manifest_sha256",
            "fit_results_sha256", "tcd_sha256", "gesla_manifest_sha256", "timebase_sha256")
    if any(binding.get(key) != previous.get(key) for key in keys):
        return None
    left, right = profile["cs"], job["cs"]
    if left.get("quantity") != "water_level" or right.get("quantity") != "water_level":
        return None
    if left.get("fit_segment") != right.get("fit_segment"):
        return None
    steps = lambda cs: [row for row in cs.get("record_issues", []) if row.get("issue") == "datum_step"]
    if steps(left) != steps(right):
        return None
    release, dump, fit_run, manifest, fit_rows, audit, _ = context
    rid = binding["source_record_id"]
    member = release.members.get(rid)
    if member is None or member["sha256"] != binding["member_sha256"] or member["size"] != binding["member_size"]:
        return None
    if not binding["fit_sha256"] or manifest["fits_sha256"].get(rid) != binding["fit_sha256"] or fit_rows.get(rid, {}).get("outcome") != "fitted":
        return None
    fit_path = fit_run / "fits" / f"{rid}.json"
    if sha256(fit_path) != binding["fit_sha256"]:
        raise gesla_sets.FitIntegrityError(f"{rid}: original-record fit bytes changed")
    fit = json.loads(fit_path.read_text())
    corr = gesla_sets.published_correction(audit.rows.get(rid), gesla_sets.declared_hours(release.metadata()[rid]))
    if any(cs.get("source") != ticon_sets.SOURCE and cs.get("time_base", {}).get("published") != corr.label
           for cs in (left, right)):
        return None
    gesla_sets.check_stamp(rid, fit, corr.label, corr=corr, speed=lambda n: dump.table[n].speed,
                          span=(fit["first_s"], fit["last_s"] + 3600))

    def reconstruction(cs):
        if cs.get("source") == ticon_sets.SOURCE or not cs.get("constituents"):
            return ({name: (row["amp_m"], row["phase_deg"]) for name, row in fit["constituents"].items()}, fit["z0_m"])
        return ({row["name"]: (row["amplitude_m"], row["phase_deg"]) for row in cs["constituents"]},
                cs.get("datum", {}).get("msl_offset_m", fit["z0_m"]))

    if reconstruction(left) != reconstruction(right):
        return None
    detail = copy.deepcopy(profile["report"])
    detail.update(station_id=job["station"]["station_id"], set_id=right["set_id"],
                  source_record_id=rid, input_binding=binding)
    detail["measurement_reuse"] = {"origin_input_binding":
        profile["report"].get("measurement_reuse", {}).get("origin_input_binding", previous),
        "basis": "identical original samples, authenticated fit, published clock, QC, segmentation and tabulation"}
    return {**job, "report": detail, "mm": profile["mm"]}


def export_retained(measured_inputs: Path, out: Path, *, source_records: set[str] | None = None,
                    context=None, retained_cache: Path | None = None) -> dict:
    """Stream retained measurements without rerunning reference selection or replacing defaults."""
    if out.resolve() == measured_inputs.resolve():
        raise ValueError("retention requires a separate output directory")
    out.mkdir(parents=True, exist_ok=True)
    destination = out / "retained-observed.jsonl"
    if destination.exists():
        raise ValueError("retention output already exists")
    completion = json.loads((measured_inputs / "observed-completion.json").read_text())
    if sha256(measured_inputs / "observed-drafts.jsonl") != completion["files"]["observed-drafts.jsonl"]:
        raise ValueError("retained draft bytes do not match the completed source run")
    run = json.loads((measured_inputs / "observed-run.json").read_text())
    targets = {item["set_id"]: item for item in run["targets"]}
    cache = measured_inputs / "checkpoints"
    related = {}
    if source_records is not None:
        with (measured_inputs / "observed-drafts.jsonl").open() as drafts:
            for line in drafts:
                row = json.loads(line)
                if row["source_record_id"] in source_records:
                    related.setdefault(row["source_record_id"], []).append(targets[row["set_id"]])
    counts = {"rows": 0, "candidates": 0, "monthly_candidates": 0, "new_measurements": 0, "empty_rows": 0}
    with destination.open("x") as output, (out / "retention-outcomes.jsonl").open("x") as outcomes:
        with (measured_inputs / "observed-drafts.jsonl").open() as drafts:
            for line in drafts:
                row = json.loads(line)
                if source_records is not None and row["source_record_id"] not in source_records:
                    continue
                item = targets[row["set_id"]]
                profile = read_checkpoint(cache, item["key"], item["job_sha256"], require_complete=True)
                if profile["binding"] != row["input_binding"]:
                    raise ValueError(f"retention source binding mismatch: {row['set_id']}")
                measured = False
                reused_measurement = False
                elapsed_s = None
                if retained_cache is not None:
                    previous = read_checkpoint(retained_cache / "checkpoints", item["key"], item["job_sha256"])
                    if previous is not None:
                        if previous["binding"] != row["input_binding"]:
                            raise ValueError("completed retention checkpoint binding changed")
                        profile = previous
                        reused_measurement = True
                if source_records is not None and not profile.get("mm") and not reused_measurement:
                    if context is None:
                        raise ValueError("original-record measurement context required")
                    job = json.loads((cache / f"{item['key']}.job.json").read_text())
                    if canonical_hash(job) != item["job_sha256"]:
                        raise ValueError(f"measurement job changed: {row['set_id']}")
                    locations = [out / "checkpoints", cache]
                    if retained_cache is not None:
                        locations.insert(1, retained_cache / "checkpoints")
                    for other in related[row["source_record_id"]]:
                        if reused_measurement:
                            break
                        for location in locations:
                            previous = read_checkpoint(location, other["key"], other["job_sha256"])
                            reused = reuse_measurement(previous, job, context) if previous else None
                            if reused is not None:
                                profile, reused_measurement = reused, True
                                fresh = out / "checkpoints"
                                fresh.mkdir(exist_ok=True)
                                write_json(fresh / f"{item['key']}.job.json", job)
                                write_checkpoint(fresh, item["key"], item["job_sha256"], profile, None,
                                                 retained_arrays=location / f"{other['key']}.npz")
                                break
                if source_records is not None and not profile.get("mm") and not reused_measurement:
                    member = context[0].members.get(row["source_record_id"])
                    if member is not None and (member["sha256"] != job["binding"]["member_sha256"] or
                                               member["size"] != job["binding"]["member_size"]):
                        raise ValueError("original member binding changed")
                    started = time.monotonic()
                    profile, tab = measure_profile(job, *context)
                    elapsed_s = time.monotonic() - started
                    fresh = out / "checkpoints"
                    fresh.mkdir(exist_ok=True)
                    write_json(fresh / f"{item['key']}.job.json", job)
                    write_checkpoint(fresh, item["key"], item["job_sha256"], profile, tab)
                    measured = True
                    counts["new_measurements"] += 1
                mm = profile.get("mm", {})
                if mm and not own_months(mm) and profile["report"].get("reason") is None:
                    profile["report"]["reason"] = "no_whole_year_own_record_window"
                candidates = [retained_observation(profile, values, {ym: values}, "monthly_tabulation",
                                                   counted_days=values.get("n_days"))
                              for ym, values in sorted(mm.items())]
                own = retained_own_record(profile)
                if own:
                    candidates.append(own)
                draft = row.get("draft_datum")
                if draft and draft.get("named") and mm:
                    first_basis = next(iter(draft["basis"].values()))
                    candidate = retained_observation(profile, {}, mm, first_basis["method"])
                    candidate["values"] = dict(draft["named"])
                    candidate["basis"] = {"per_level": draft["basis"]}
                    actual_span = first_basis["data_span"]
                    candidate["window"] = {key: actual_span[key] for key in ("start", "end")}
                    candidates.append(candidate)
                # Conflicting selected keys may survive only in these existing
                # alternatives. Deduplicate complete candidates, never levels.
                retained = {}
                for candidate in row.get("retained_observed", []):
                    retained.setdefault(canonical_hash(candidate), candidate)
                for candidate in candidates:
                    if candidate["values"]:
                        retained.setdefault(canonical_hash(candidate), candidate)
                row["retained_observed"] = list(retained.values())
                output.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
                output.flush()
                outcome = {"station_id": row["station_id"], "set_id": row["set_id"],
                           "source_record_id": row["source_record_id"], "measured": measured, "reused_measurement": reused_measurement,
                           "months": len(mm), "candidates": len(row["retained_observed"]),
                           "measurement_elapsed_s": elapsed_s,
                           "has_own_record_window": bool(own_months(mm)) if mm else False,
                           "reason": profile["report"].get("reason")}
                outcomes.write(json.dumps(outcome, sort_keys=True) + "\n")
                outcomes.flush()
                counts["rows"] += 1
                counts["monthly_candidates"] += len(mm)
                counts["candidates"] += len(row["retained_observed"])
                counts["empty_rows"] += not bool(row["retained_observed"])
                print(f"retained {row['set_id']} months={len(mm)} measured={measured} "
                      f"reason={outcome['reason']} elapsed_s={elapsed_s}", flush=True)
    write_json(out / "retention-counts.json", counts)
    return {"counts": counts}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("release", "gesla-root", "fit-run", "tcd", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--resume", action="store_true", help="resume only authenticated identical run inputs")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=1, help="independent measurement/reduction processes (1–4)")
    parser.add_argument("--measured-inputs", type=Path, help="preserve frozen 1d0d487 measurements and refresh its 141 hourly means")
    parser.add_argument("--retain-only", action="store_true", help="export all authenticated held numerical candidates")
    parser.add_argument("--retained-cache", type=Path, help="reuse completed source-bound retention checkpoints")
    parser.add_argument("--remeasure-source-records", type=Path, help="JSON source-ID array: measure missing original-record monthly values")
    args = parser.parse_args()
    if args.retain_only:
        if args.measured_inputs is None or args.resume or args.workers != 1:
            parser.error("retention requires --measured-inputs, one worker, and a fresh output")
        records = None
        context = None
        if args.remeasure_source_records:
            records = set(json.loads(args.remeasure_source_records.read_text()))
            roles = Path(__file__).resolve().parents[4] / "data/gesla/contributor_roles.csv"
            _initialize_measurement_worker(args.gesla_root, args.fit_run, args.tcd, roles)
            context = _WORKER_CONTEXT
        report = export_retained(args.measured_inputs, args.out, source_records=records, context=context, retained_cache=args.retained_cache)
    else:
        if args.remeasure_source_records:
            parser.error("--remeasure-source-records requires --retain-only")
        report = produce(args.release, args.gesla_root, args.fit_run, args.tcd, args.out, resume=args.resume,
                         workers=args.workers, measured_inputs=args.measured_inputs)
    print(json.dumps(report["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
