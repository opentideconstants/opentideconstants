"""Retain named levels from existing SDK harmonic-mean extreme events.

MHW/MLW are arithmetic means of all predicted high/low events in the labelled
window; MTL and MN follow the definitions also used by observed_datums.monthly.
These are event-weighted harmonic predictions, not monthly-balanced observed
tidal datums. LAT/HAT are the minimum/maximum among returned extreme events.
Window boundary heights are not evaluated, so continuous-window extrema are
not established. No day grouping, surveyed zero, observed epoch, or accuracy
acceptance is inferred. Optional bound padding enables the existing monthly
tidal-day reduction; optional hourly heights enable a predicted series mean.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from .observed_datums.monthly import derived
from .observed_datums.tabulate import TIDAL_DAY_S, TidalDays, label_events


def _instant(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Event/window times must include their offset")
    return result


def retained_levels(*, station: dict, set_id: str, prediction: dict,
                    window: dict, input_binding: dict, tidal_day_padding: dict | None = None,
                    hourly_heights: dict | None = None, spring_convention: bool = False,
                    lunitidal_convention: dict | None = None, nllw_convention: bool = False,
                    llwlt_convention: bool = False, tlt_projection: dict | None = None,
                    neap_proxy: bool = False) -> dict:
    """Build a source-bound candidate; leave all original source/set data intact."""
    station_id = station.get("id", station.get("station_id"))
    sets = [cs for cs in station["constant_sets"] if cs["set_id"] == set_id]
    if len(sets) != 1:
        raise ValueError("An exact same-station harmonic set is required")
    cs = sets[0]
    source = {"gesla-fit": "gesla", "ticon-4": "ticon"}.get(cs["source"], cs["source"])
    records = [r for r in station["source_records"]
               if r["source"] == source and r["source_record_id"] == cs["source_record_id"]
               and r.get("source_version") == cs.get("source_version")
               and set_id in r.get("published_set_ids", [])]
    if len(records) != 1:
        raise ValueError("An exact source tuple publishing this set is required")
    if (prediction.get("station_id") != station_id or prediction.get("set_id") != set_id
            or prediction.get("method") != "harmonic" or prediction.get("datum") != "mean_level"
            or cs.get("quantity") != "water_level" or not prediction.get("astro_table_id")):
        raise ValueError("Events must use the same water-level set and harmonic-mean frame")
    if not cs.get("convention_id") or prediction.get("convention_id") != cs["convention_id"]:
        raise ValueError("Events must use the selected harmonic set's convention_id")
    if not input_binding.get("native_sha256") or not input_binding.get("sdk_commit"):
        raise ValueError("Native and SDK input bindings are required")
    start, end = _instant(window["start"]), _instant(window["end"])
    if end <= start:
        raise ValueError("A positive evaluation window is required")
    highs, lows = [], []
    previous = None
    for event in prediction["items"]:
        instant = _instant(event["time"])
        value = event["height_m"]
        if (event["type"] not in ("high", "low") or isinstance(value, bool)
                or not isinstance(value, (int, float)) or not math.isfinite(value)
                or not start <= instant < end or (previous is not None and instant <= previous)):
            raise ValueError("Expected ordered finite high/low events within the evaluation window")
        (highs if event["type"] == "high" else lows).append(value)
        previous = instant
    if not highs or not lows:
        raise ValueError("Both high and low events are required")
    mhw, mlw = math.fsum(highs) / len(highs), math.fsum(lows) / len(lows)
    binding = copy.deepcopy(input_binding)
    binding.update({
        "set_id": set_id,
        "constant_set_sha256": hashlib.sha256(json.dumps(cs, sort_keys=True, separators=(",", ":"),
                                                         ensure_ascii=False).encode()).hexdigest(),
        "source_provenance": copy.deepcopy(cs.get("provenance", {})),
        "astro_table_id": prediction["astro_table_id"],
        "convention_id": prediction.get("convention_id"),
        "algorithm": prediction.get("algorithm"),
    })
    record = records[0]
    result = {
        "station_id": station_id,
        "source": record["source"],
        "source_record_id": record["source_record_id"],
        "source_version": record.get("source_version"),
        "retained_datums": [{
            "kind": "computed",
            "method": "sdk_harmonic_extremes_event_means_v1",
            "values": {"mhw": mhw, "mlw": mlw, "mtl": (mhw + mlw) / 2,
                       "mn": mhw - mlw, "lat": min(highs + lows), "hat": max(highs + lows)},
            "units": "m",
            "frame": {"kind": "harmonic_mean", "name": None, "tie_status": "unknown"},
            "source_record_id": record["source_record_id"],
            "set_id": set_id,
            "window": copy.deepcopy(window),
            "input_binding": binding,
            "uncertainty_m": None,
            "reliability": {
                "status": "accuracy_unverified",
                "limitations": [
                    "Predicted harmonic-relative values; not observed or agency datums.",
                    "No surveyed MSL or chart-datum tie is established.",
                    "MHW/MLW weight each predicted event equally, not each month.",
                    "LAT/HAT are event-sampled minima/maxima of the returned extreme-event heights.",
                    "Window boundary heights were not evaluated; continuous-window extrema are not established.",
                    "These are not official astronomical datum determinations.",
                    "MHHW/MLLW and observed MSL are not calculated; no tidal-day grouping is inferred.",
                    "Source quality and convention limitations remain applicable; uncertainty is unknown.",
                ],
            },
            "basis": {"high_count": len(highs), "low_count": len(lows),
                      "qc_status": cs.get("qc_status"), "qc_flags": copy.deepcopy(cs.get("qc_flags", [])),
                      "source_type": cs.get("source_type"), "fallback": copy.deepcopy(cs.get("fallback"))},
        }],
    }
    if tidal_day_padding is not None:
        result["retained_datums"].append(_higher_lower_levels(
            result["retained_datums"][0], prediction, window, input_binding, tidal_day_padding))
    mean = None
    if hourly_heights is not None:
        mean = _hourly_mean(result["retained_datums"][0], prediction, window, input_binding, hourly_heights)
        result["retained_datums"].append(mean)
    if spring_convention:
        if hourly_heights is None:
            raise ValueError("Spring convention requires the bound same-frame hourly mean")
        result["retained_datums"].append(_spring_levels(result["retained_datums"][-1], cs))
    if neap_proxy:
        if mean is None:
            raise ValueError("Neap proxy requires the bound same-frame hourly mean")
        result["retained_datums"].append(_neap_plane_proxy(mean, cs))
    if nllw_convention:
        if mean is None:
            raise ValueError("NLLW convention requires the bound same-frame hourly mean")
        result["retained_datums"].append(_nllw_levels(mean, cs))
    if llwlt_convention:
        result["retained_datums"].append(_annual_low_levels(
            result["retained_datums"][0], prediction, window))
    if tlt_projection is not None:
        result["retained_datums"].append(_projected_window_low(
            result["retained_datums"][0], cs, station, tlt_projection))
    if lunitidal_convention is not None:
        result["retained_datums"].append(_lunitidal_intervals(
            result["retained_datums"][0], cs, station, lunitidal_convention))
    return result


def _projected_window_low(base: dict, cs: dict, station: dict, projection: dict) -> dict:
    """An explicitly selected model/window minimum, not formal Vladimirsky TLT."""
    required, optional = ["M2", "S2", "N2", "K2", "K1", "O1", "P1", "Q1"], ["M4", "MS4", "M6"]
    names = [row["name"] for row in cs["constituents"]]
    selected = required + [name for name in optional if name in names]
    if any(names.count(name) != 1 for name in selected):
        raise ValueError("TLT projection requires each main constituent and each present optional constituent exactly once")
    for row in cs["constituents"]:
        if row["name"] in selected:
            for key in ("amplitude_m", "phase_deg", "speed_deg_per_hour"):
                value = row.get(key)
                if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                        or (key == "amplitude_m" and value < 0) or (key == "phase_deg" and not 0 <= value < 360)
                        or (key == "speed_deg_per_hour" and value <= 0)):
                    raise ValueError("TLT projection requires finite own-model amplitude, phase and speed for every selected constituent")
    expected = copy.deepcopy(cs)
    expected["set_id"] = cs["set_id"] + "/tlt-projection-v1"
    expected["recommended"] = False
    expected["constituents"] = [row for row in cs["constituents"] if row["name"] in selected]
    if projection["constant_set"] != expected:
        raise ValueError("TLT projection must retain the exact selected own-model coefficients and metadata")
    window, pins = projection["window"], projection["input_binding"]
    start, end = (_instant(window[key]) for key in ("start", "end"))
    if projection.get("year_basis_days") != 365.25 or (end - start).total_seconds() != 18.6 * 365.25 * 86400:
        raise ValueError("TLT projection requires an explicit 18.6-Julian-year evaluation window")
    if (pins.get("sdk_commit") != base["input_binding"]["sdk_commit"]
            or pins.get("source_native_sha256") != base["input_binding"].get("current_native_sha256", base["input_binding"]["native_sha256"])
            or pins.get("source_constant_set_sha256") != base["input_binding"]["constant_set_sha256"]
            or any(not pins.get(key) for key in ("events_request_sha256", "events_results_sha256",
                                                "boundaries_request_sha256", "boundaries_results_sha256"))):
        raise ValueError("TLT projection requires the original source/model and bound prediction requests/results")
    private = copy.deepcopy(station)
    private["constant_sets"].append(expected)
    source = {"gesla-fit": "gesla", "ticon-4": "ticon"}.get(cs["source"], cs["source"])
    record = next(record for record in private["source_records"]
                  if (record["source"], record["source_record_id"], record.get("source_version")) ==
                  (source, cs["source_record_id"], cs.get("source_version"))
                  and cs["set_id"] in record.get("published_set_ids", []))
    record["published_set_ids"].append(expected["set_id"])
    try:
        reduced = retained_levels(station=private, set_id=expected["set_id"], prediction=projection["prediction"],
                                  window=window, input_binding=pins)["retained_datums"][0]
    except ValueError as error:
        raise ValueError(f"TLT projected events: {error}") from error
    if pins.get("projected_constant_set_sha256") != reduced["input_binding"]["constant_set_sha256"]:
        raise ValueError("TLT projected model hash must describe the actual selected prediction input")
    boundaries, events = projection["boundary_prediction"], projection["prediction"]
    fields = ("station_id", "set_id", "convention_id", "astro_table_id", "method", "datum", "algorithm", "release")
    if any(boundaries.get(key) != events.get(key) for key in fields) or len(boundaries["items"]) != 2:
        raise ValueError("TLT boundaries must use the same projected model and frame")
    heights = []
    for item, instant in zip(boundaries["items"], (start, end)):
        value = item["height_m"]
        if (_instant(item["time"]) != instant or isinstance(value, bool)
                or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError("TLT boundaries require finite heights at both exact window endpoints")
        heights.append(value)
    candidate = copy.deepcopy(base)
    candidate["method"] = "sdk_harmonic_selected_constituent_window_minimum_v1"
    candidate["values"] = {"tlt": min(reduced["values"]["lat"], *heights)}
    candidate["window"] = copy.deepcopy(window)
    candidate["input_binding"]["tlt_projection"] = copy.deepcopy(pins)
    candidate["basis"].update({
        "selected_constituents": selected, "excluded_constituents": [name for name in names if name not in selected],
        "projected_set_id": expected["set_id"], "duration_years": 18.6, "year_basis_days": 365.25,
        "duration_seconds": (end - start).total_seconds(), "boundary_heights_m": heights,
        "high_count": reduced["basis"]["high_count"], "low_count": reduced["basis"]["low_count"],
        "definition_url": "https://castjournals.cast.org.cn/joweb/aos/EN/1198944206447280371",
    })
    candidate["reliability"]["limitations"] = [
        "Computed minimum of the explicit eight-main-plus-present-M4/MS4/M6 projection over 18.6 Julian years.",
        "Minimum uses predicted extrema and both window-boundary heights in the harmonic-mean frame.",
        "Not the full formal thirteen-constituent Vladimirsky result or an official GB datum.",
        "Sa/Ssa and other unselected constituents are excluded; this is not an all-constituent LAT.",
        "No surveyed datum tie, chart-datum tie or numeric uncertainty is established.",
        "Original source quality and convention limitations remain applicable.",
    ]
    return candidate


def _nllw_levels(mean: dict, cs: dict) -> dict:
    """JHOD four-constituent convention using this model's hourly mean."""
    amplitudes = {}
    for name in ("M2", "S2", "K1", "O1"):
        rows = [row for row in cs["constituents"] if row["name"] == name]
        if len(rows) != 1:
            raise ValueError("NLLW convention requires one own-set M2, S2, K1 and O1 amplitude")
        value = rows[0].get("amplitude_m")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError("NLLW convention requires finite nonnegative amplitudes")
        amplitudes[name] = value
    msl = mean["values"]["msl"]
    candidate = copy.deepcopy(mean)
    candidate["method"] = "harmonic_jhod_nllw_convention_v1"
    candidate["values"] = {"nllw": msl - math.fsum(amplitudes.values())}
    candidate["basis"].update({
        "nllw_convention": "computed JHOD convention: MSL - (H_M2 + H_S2 + H_K1 + H_O1)",
        "definition_url": "https://www1.kaiho.mlit.go.jp/TIDE/harmonic/kaisetsu.html",
        "amplitudes_m": amplitudes, "computed_msl_m": msl,
    })
    candidate["reliability"]["limitations"] = [
        "Computed JHOD four-constituent NLLW convention in the same harmonic-mean frame.",
        "MSL is this model's hourly predicted mean, not long-term observed or surveyed MSL.",
        "This is not an official JHOD datum or chart-datum tie; no Z0 is inferred.",
        "No surveyed datum tie or numeric uncertainty is established.",
        "Original source quality and convention limitations remain applicable.",
    ]
    return candidate


def _annual_low_levels(base: dict, prediction: dict, window: dict) -> dict:
    """Mean of nineteen annual minimum low events in complete calendar years."""
    start, end = (_instant(window[key]).astimezone(timezone.utc) for key in ("start", "end"))
    if (any((t.month, t.day, t.hour, t.minute, t.second, t.microsecond) != (1, 1, 0, 0, 0, 0)
            for t in (start, end)) or end.year - start.year != 19):
        raise ValueError("LLWLT requires nineteen complete calendar years in the evaluation window")
    lows = {year: [] for year in range(start.year, end.year)}
    for event in prediction["items"]:
        if event["type"] == "low":
            lows[_instant(event["time"]).astimezone(timezone.utc).year].append(event["height_m"])
    if any(not values for values in lows.values()):
        raise ValueError("LLWLT requires predicted low events in all nineteen calendar years")
    minima = {str(year): min(values) for year, values in lows.items()}
    candidate = copy.deepcopy(base)
    candidate["method"] = "sdk_harmonic_mean_annual_low_water_v1"
    candidate["values"] = {"llwlt": math.fsum(minima.values()) / 19}
    candidate["basis"].update({
        "llwlt_convention": "mean annual lowest predicted low water",
        "definition_url": "https://tides.gc.ca/en/definitions-content-tides-and-currents",
        "year_count": 19, "calendar_timezone": "UTC",
        "annual_minimum_low_events_m": minima,
        "annual_low_event_counts": {str(year): len(values) for year, values in lows.items()},
    })
    candidate["reliability"]["limitations"] = [
        "Mean annual lowest predicted low water over nineteen complete calendar years.",
        "Only predicted low events enter the annual minima; hourly minima and MLLW are not used.",
        "Computed harmonic-relative estimate; not an official CHS datum or chart-datum tie.",
        "No surveyed datum tie or numeric uncertainty is established.",
        "Original source quality and convention limitations remain applicable.",
    ]
    return candidate


def _spring_levels(mean: dict, cs: dict) -> dict:
    """Explicit generic M2+S2 convention; not a station-local spring datum."""
    amplitudes = {}
    for name in ("M2", "S2"):
        rows = [row for row in cs["constituents"] if row["name"] == name]
        if len(rows) != 1:
            raise ValueError("Spring convention requires one own-set M2 and S2 amplitude")
        value = rows[0].get("amplitude_m")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError("Spring convention requires finite nonnegative amplitudes")
        amplitudes[name] = value
    amplitude = amplitudes["M2"] + amplitudes["S2"]
    msl = mean["values"]["msl"]
    candidate = copy.deepcopy(mean)
    candidate["method"] = "harmonic_m2_s2_spring_convention_v1"
    candidate["values"] = {"mhws": msl + amplitude, "mlws": msl - amplitude}
    candidate["basis"].update({"spring_convention": "MSL +/- (H_M2 + H_S2)",
                               "amplitudes_m": amplitudes, "computed_msl_m": msl})
    candidate["reliability"]["limitations"] = [
        "Computed generic M2+S2 spring convention in the same harmonic-mean frame.",
        "No station-local spring definition is evidenced; this is not an agency datum.",
        "No surveyed datum tie or numeric uncertainty is established.",
        "Original source quality and convention limitations remain applicable.",
    ]
    return candidate


def _neap_plane_proxy(mean: dict, cs: dict) -> dict:
    """Low-reliability two-constituent envelope planes, not mean neap tides."""
    try:
        candidate = _spring_levels(mean, cs)
    except ValueError as error:
        raise ValueError(f"Neap proxy: {error}") from error
    amplitudes = candidate["basis"]["amplitudes_m"]
    amplitude = abs(amplitudes["M2"] - amplitudes["S2"])
    msl = candidate["basis"]["computed_msl_m"]
    candidate["method"] = "harmonic_m2_s2_neap_plane_proxy_v1"
    candidate["values"] = {"mhwn": msl + amplitude, "mlwn": msl - amplitude}
    candidate["basis"].pop("spring_convention")
    candidate["basis"].update({"neap_plane_proxy": "approximation: two-constituent neap-plane proxy",
                               "analytic_basis": "hourly MSL +/- abs(H_M2 - H_S2)",
                               "neap_envelope_amplitude_m": amplitude, "not_selected_for_prediction": True})
    candidate["reliability"]["status"] = "low"
    candidate["reliability"]["limitations"] = [
        "LOW-reliability two-constituent neap-plane proxy using this model's actual hourly mean.",
        "Not formal mean high/low water neaps: no neap-event averaging or surveyed datum is established.",
        "Other constituents and time-varying nodal modulation are omitted from the plane amplitude.",
        "No chart-datum tie is established; these alternatives are not selected for prediction.",
        "Original source quality and convention limitations remain applicable; uncertainty is unknown.",
    ]
    return candidate


def _lunitidal_intervals(base: dict, cs: dict, station: dict, convention: dict) -> dict:
    """M2-only approximate intervals, explicitly referenced to Greenwich transit."""
    if (convention.get("convention_id") != cs["convention_id"]
            or convention.get("phase_reference") != "greenwich_utc"
            or convention.get("nodal_handling") != "f_u_at_prediction"
            or convention.get("v0_model") != "schureman_tcd"
            or convention.get("astro_table_id") != base["input_binding"]["astro_table_id"]):
        raise ValueError("Lunitidal approximation requires the bound Greenwich phase convention")
    rows = [row for row in cs["constituents"] if row["name"] == "M2"]
    if len(rows) != 1:
        raise ValueError("Lunitidal approximation requires one own-set M2 constituent")
    m2 = rows[0]
    phase, speed, longitude = m2.get("phase_deg"), m2.get("speed_deg_per_hour"), station.get("lon")
    amplitude = m2.get("amplitude_m")
    if (any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in (phase, speed, longitude, amplitude)) or not -180 <= longitude <= 180
            or not 0 <= phase < 360 or abs(speed - 28.9841042) > 1e-6
            or amplitude <= 0):
        raise ValueError("Lunitidal approximation requires valid longitude and M2 phase, speed and amplitude")
    # g = kappa + 2*west_longitude; local interval = kappa / M2 speed.
    # Convert back to Greenwich (NOAA's datum HWI/LWI reference), modulo one
    # M2 cycle. This is a constituent approximation, not a mean of actual tides.
    period = 360 / speed
    local_phase = (phase + 2 * longitude) % 360
    local_high, local_low = local_phase / speed, ((local_phase + 180) % 360) / speed
    greenwich_shift = -2 * longitude / speed
    candidate = copy.deepcopy(base)
    candidate["method"] = "harmonic_m2_approximate_lunitidal_intervals_v1"
    candidate["values"] = {"hwi": (local_high + greenwich_shift) % period,
                           "lwi": (local_low + greenwich_shift) % period}
    candidate["units"] = "h"
    candidate["frame"] = {"kind": "elapsed_time", "name": "Greenwich lunar transit", "tie_status": "not_applicable"}
    candidate["input_binding"]["phase_convention"] = copy.deepcopy(convention)
    candidate["basis"].update({"m2_phase_greenwich_deg": phase, "m2_speed_deg_per_hour": speed,
                               "longitude_east_deg": longitude, "local_hwi_hours": local_high,
                               "local_lwi_hours": local_low, "period_hours": period,
                               "formula": "kappa=(g+2*longitude_east) modulo 360; local=kappa/speed; Greenwich=local-2*longitude_east/speed modulo period"})
    candidate["reliability"]["limitations"] = [
        "Approximate M2-only lunitidal intervals in hours following Greenwich lunar transit.",
        "Not observed or agency HWI/LWI; no actual lunar-transit/event average was evaluated.",
        "Other constituents, nodal modulation and diurnal inequality are omitted; mixed-tide applicability is unvalidated.",
        "Original source quality and convention limitations remain applicable; interval uncertainty is unknown.",
    ]
    return candidate


def _hourly_mean(base: dict, prediction: dict, window: dict, binding: dict, hourly: dict) -> dict:
    """Retain a predicted hourly series mean, not observed or surveyed MSL."""
    series = hourly["prediction"]
    fields = ("station_id", "set_id", "convention_id", "astro_table_id", "method", "datum", "algorithm", "release")
    if any(series.get(key) != prediction.get(key) for key in fields):
        raise ValueError("Hourly heights must use the same SDK harmonic source and frame")
    start, end = (_instant(window[key]) for key in ("start", "end"))
    if any(_instant(hourly["window"][key]) != _instant(window[key]) for key in ("start", "end")):
        raise ValueError("Hourly heights must use the same evaluation window")
    pins = hourly["input_binding"]
    if (any(pins.get(key) != binding.get(key) for key in ("native_sha256", "sdk_commit"))
            or not pins.get("request_sha256") or not pins.get("results_sha256")):
        raise ValueError("Hourly heights require matching native/SDK pins and bound request/results")
    items = series["items"]
    if len(items) != math.ceil((end - start).total_seconds() / 3600):
        raise ValueError("Hourly heights must cover the complete half-open evaluation window")
    heights = []
    for index, item in enumerate(items):
        value = item["height_m"]
        if ((_instant(item["time"]) - start).total_seconds() != index * 3600
                or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError("Hourly heights must be finite and regularly spaced at one hour")
        heights.append(value)
    candidate = copy.deepcopy(base)
    candidate["method"] = "sdk_harmonic_hourly_mean_v1"
    candidate["values"] = {"msl": math.fsum(heights) / len(heights)}
    candidate["input_binding"]["hourly_heights"] = {
        "window": copy.deepcopy(hourly["window"]), "input_binding": copy.deepcopy(pins),
        "convention_id": series["convention_id"], "astro_table_id": series["astro_table_id"],
    }
    candidate["basis"].update({"hourly_sample_count": len(heights), "step_seconds": 3600})
    candidate["reliability"]["limitations"] = [
        "Arithmetic mean of hourly predicted heights in the stated harmonic-mean frame and window.",
        "Computed MSL is not an observed epoch datum, surveyed zero, or an agency datum.",
        "No chart, station-datum or geodetic tie is established; uncertainty is unknown.",
        "Original source quality and convention limitations remain applicable.",
    ]
    return candidate


def _higher_lower_levels(base: dict, prediction: dict, window: dict, binding: dict, padding: dict) -> dict:
    """Reuse the accepted monthly tidal-day labels, including month-end lookahead."""
    start, end = (_instant(window[key]).astimezone(timezone.utc) for key in ("start", "end"))
    if any(t.day != 1 or (t.hour, t.minute, t.second, t.microsecond) != (0, 0, 0, 0) for t in (start, end)):
        raise ValueError("Tidal-day padding requires a whole-calendar-month evaluation window")
    pad_start, pad_end = (_instant(padding["window"][key]) for key in ("start", "end"))
    if pad_start != end or (pad_end - end).total_seconds() < TIDAL_DAY_S:
        raise ValueError("Tidal-day padding must cover one full label window after the evaluation window")
    extra = padding["prediction"]
    fields = ("station_id", "set_id", "convention_id", "astro_table_id", "method", "datum", "algorithm", "release")
    if any(extra.get(key) != prediction.get(key) for key in fields):
        raise ValueError("Tidal-day padding must use the same SDK harmonic source and frame")
    pins = padding["input_binding"]
    if (any(pins.get(key) != binding.get(key) for key in ("native_sha256", "sdk_commit"))
            or not pins.get("request_sha256") or not pins.get("results_sha256")):
        raise ValueError("Tidal-day padding requires matching native/SDK pins and bound request/results")
    previous = None
    for event in extra["items"]:
        t, h = _instant(event["time"]), event["height_m"]
        if (event["type"] not in ("high", "low") or isinstance(h, bool) or not isinstance(h, (int, float))
                or not math.isfinite(h) or not pad_start <= t < pad_end or (previous is not None and t <= previous)):
            raise ValueError("Invalid or out-of-window tidal-day padding event")
        previous = t
    events = prediction["items"] + extra["items"]
    times = np.array([_instant(event["time"]).timestamp() for event in events])
    heights = np.array([event["height_m"] for event in events])
    kinds = np.array([1 if event["type"] == "high" else -1 for event in events])
    days = TidalDays("monthly")
    labels = label_events(times, heights, kinds, days.index(times), days)
    inside = (times >= start.timestamp()) & (times < end.timestamp())
    higher, lower = heights[inside & (labels == "HH")], heights[inside & (labels == "LL")]
    if not len(higher) or not len(lower):
        raise ValueError("Tidal-day padding reduction requires higher high and lower low events")
    levels = derived({"MHHW": math.fsum(higher) / len(higher), "MLLW": math.fsum(lower) / len(lower),
                      "MHW": base["values"]["mhw"], "MLW": base["values"]["mlw"]})
    candidate = copy.deepcopy(base)
    candidate["method"] = "sdk_harmonic_extremes_monthly_tidal_day_means_v1"
    candidate["values"] = {key.lower(): levels[key] for key in ("MHHW", "MLLW", "DHQ", "DLQ", "DTL", "GT")}
    candidate["input_binding"]["tidal_day_padding"] = {
        "window": copy.deepcopy(padding["window"]), "input_binding": copy.deepcopy(pins),
        "convention_id": extra["convention_id"], "astro_table_id": extra["astro_table_id"],
    }
    candidate["basis"].update({"tidal_day_mode": "monthly", "tidal_day_seconds": TIDAL_DAY_S,
                               "higher_high_count": len(higher), "lower_low_count": len(lower)})
    candidate["reliability"]["limitations"] = [
        "Predicted harmonic-relative values; not observed or agency datums.",
        "No surveyed MSL or chart-datum tie is established; uncertainty is unknown.",
        "Higher/lower events use the existing monthly tidal-day labels, including month-end lookahead.",
        "Labelled events are averaged over the stated window; this is not an observed monthly-balanced datum.",
        "Original source quality and convention limitations remain applicable.",
    ]
    return candidate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Source-bound SDK event input JSON")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = retained_levels(**json.loads(args.input.read_text()))
    args.out.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
