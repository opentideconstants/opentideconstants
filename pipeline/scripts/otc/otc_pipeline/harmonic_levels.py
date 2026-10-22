"""Retain named levels from existing SDK harmonic-mean extreme events.

MHW/MLW are arithmetic means of all predicted high/low events in the labelled
window; MTL and MN follow the definitions also used by observed_datums.monthly.
These are event-weighted harmonic predictions, not monthly-balanced observed
tidal datums. LAT/HAT are the minimum/maximum among returned extreme events.
Window boundary heights are not evaluated, so continuous-window extrema are
not established. No day grouping, surveyed zero, observed epoch, or accuracy
acceptance is inferred.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path


def _instant(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Event/window times must include their offset")
    return result


def retained_levels(*, station: dict, set_id: str, prediction: dict,
                    window: dict, input_binding: dict) -> dict:
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
    return {
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Source-bound SDK event input JSON")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = retained_levels(**json.loads(args.input.read_text()))
    args.out.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
