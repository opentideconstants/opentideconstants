"""NOAA subordinate extrema method gate; no release or calibration admission."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import resource
from statistics import mean, median, stdev
import subprocess
import time

from otc_pipeline.datum_partition import load
from otc_pipeline.lat_hat import START, END, EPOCH, _finite


def resolve_reference(payload: dict) -> dict:
    """Use the offsets' actual reference and first usable NOAA set, as the SDK."""
    offsets = payload["station"]["subordinate_offsets"]
    station = payload.get("reference_station") or {}
    if station.get("station_id") != offsets["reference_station_id"] or station.get("status") == "removed":
        raise ValueError("reference_unavailable")
    sets = station.get("constant_sets", [payload.get("reference_set", {})])
    reference = next((s for s in sets if s.get("source") == "noaa"
                      and s.get("quantity") == "water_level" and s.get("qc_status") in ("ok", "flagged")), None)
    if not reference or not reference.get("constituents"):
        raise ValueError("reference_unavailable")
    return reference


def strict_request(payload: dict, start: str = START, end: str = END) -> dict:
    reference = resolve_reference(payload)
    datum = reference.get("datum", {})
    chart = datum.get("chart_datum")
    if not chart or datum.get("basis", {}).get(chart, {}).get("kind") != "published":
        raise ValueError("datum_unavailable: published reference chart datum required")
    try:
        term = _finite(datum.get("msl_offset_m"), "msl_offset_m") - _finite(datum.get("named", {}).get(chart), "chart datum")
    except ValueError as error:
        raise ValueError("datum_unavailable") from error
    offsets = payload["station"]["subordinate_offsets"]
    if "datum" in offsets and offsets["datum"].get("chart_datum") != chart:
        raise ValueError("datum_unavailable: subordinate chart datum differs")
    return {"astro_table": payload["astro_table"], "constituents": reference["constituents"],
            "msl_offset_m": term, "subordinate_offsets": offsets, "start": start, "end": end}


def compute_raw(payload: dict, *, start: str = START, end: str = END, ruby: str = "ruby") -> dict:
    request = strict_request(payload, start, end)
    root = Path(os.environ["OTC_ENGINE_WORKTREE"])
    wrapper = Path(__file__).with_name("lat_hat_strict.rb")
    started = time.perf_counter()
    completed = subprocess.run([ruby, str(wrapper)], input=json.dumps(request, allow_nan=False),
                               text=True, stdout=subprocess.PIPE, check=True)
    result = json.loads(completed.stdout)
    result.update(elapsed_s=time.perf_counter() - started,
                  child_maxrss_native_units=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
                  request_sha256=hashlib.sha256(json.dumps(request, sort_keys=True, allow_nan=False).encode()).hexdigest(),
                  engine_sha256=hashlib.sha256((root / "lib/harmonics_strict.rb").read_bytes()).hexdigest(),
                  wrapper_sha256=hashlib.sha256(wrapper.read_bytes()).hexdigest(),
                  reference_set_id=resolve_reference(payload)["set_id"],
                  tables_sha256=payload["astro_table"]["tables_sha256"])
    return result


def compute_batch(payloads: list[dict], *, start: str = START, end: str = END,
                  ruby: str = "ruby") -> list[dict]:
    """Run one bounded exact-reference group through the existing array boundary."""
    requests = [strict_request(payload, start, end) for payload in payloads]
    root = Path(os.environ["OTC_ENGINE_WORKTREE"])
    wrapper = Path(__file__).with_name("lat_hat_strict.rb")
    started = time.perf_counter()
    completed = subprocess.run([ruby, str(wrapper)], input=json.dumps(requests, allow_nan=False),
                               text=True, stdout=subprocess.PIPE, check=True)
    results = json.loads(completed.stdout)
    elapsed = time.perf_counter() - started
    if not isinstance(results, list) or len(results) != len(payloads):
        raise ValueError("subordinate batch result count mismatch")
    for result, payload, request in zip(results, payloads, requests, strict=True):
        result.update(elapsed_s=elapsed, batch_size=len(payloads),
                      child_maxrss_native_units=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
                      request_sha256=hashlib.sha256(json.dumps(request, sort_keys=True, allow_nan=False).encode()).hexdigest(),
                      engine_sha256=hashlib.sha256((root / "lib/harmonics_strict.rb").read_bytes()).hexdigest(),
                      wrapper_sha256=hashlib.sha256(wrapper.read_bytes()).hexdigest(),
                      reference_set_id=resolve_reference(payload)["set_id"],
                      tables_sha256=payload["astro_table"]["tables_sha256"])
    return results


def datum_draft(payload: dict, raw: dict) -> dict:
    if (raw["start"], raw["end"]) != (START, END):
        raise ValueError("only the pinned 19-year window receives the OTC epoch")
    # Spec §3.1: NOAA offsets define heights above the subordinate's MLLW.
    # This names their zero; it does not publish an MLLW level or datum tie.
    datum = copy.deepcopy(payload["station"]["subordinate_offsets"].get(
        "datum", {"zero": "chart_datum", "zero_name": "MLLW", "named": {}, "basis": {}}))
    for level in ("lat", "hat"):
        key = f"otc_{level}" if datum.get("basis", {}).get(level, {}).get("kind") == "published" else level
        datum.setdefault("named", {})[key] = _finite(raw[f"{level}_m"], level)
        datum.setdefault("basis", {})[key] = {"kind": "computed", "method": "subordinate_offsets", "epoch": dict(EPOCH)}
    return datum


def offset_chart_datum(station: dict) -> dict | None:
    """Label NOAA's algebraic offset zero after authenticating cached extrema.

    Keep datum_draft unchanged: historical cached drafts authenticate its exact
    output before this additive chart designation is applied.
    """
    offsets = station.get("subordinate_offsets", {})
    datum = copy.deepcopy(offsets.get("datum"))
    noaa = station.get("aliases", {}).get("noaa")
    noaa_ids = noaa if isinstance(noaa, list) else [noaa]
    sources = [row for row in station.get("source_records", [])
               if row.get("source") == "noaa" and row.get("source_record_id") in noaa_ids]
    reference = offsets.get("reference_station_id")
    if (station.get("type") != "subordinate" or not noaa or not sources or not reference
            or offsets.get("licence_id") != "noaa-public-domain"):
        return datum
    if datum and (datum.get("chart_datum") or "mllw" in datum.get("named", {})
                  or datum.get("zero") != "chart_datum" or datum.get("zero_name") != "MLLW"):
        return datum
    if not datum:
        datum = {"zero": "chart_datum", "zero_name": "MLLW", "named": {}, "basis": {}}
    datum["chart_datum"] = "mllw"
    datum.setdefault("named", {})["mllw"] = 0.0
    datum.setdefault("basis", {})["mllw"] = {
        "kind": "computed", "method": "subordinate_offset_zero",
        "fallback": {
            "reason": "datum_gate_failed",
            "basis": f"NOAA subordinate offsets for {sources[0]['source_record_id']} referencing {reference}; "
                     "algebraic zero of the MLLW prediction frame",
            "otc_finding": "DERIVED subordinate offset-zero chart reference, not a locally published or "
                           "surveyed MLLW datum or an independent vertical tie. Uncertainty is unestablished; "
                           "normal datum gate not accepted.",
        },
    }
    return datum


def tuning_summary(rows: list[dict]) -> dict:
    expected = {"8728464", "8728626", "8746819", "9414392", "9414767", "9414819"}
    if len(rows) != 6 or {r["source_record_id"] for r in rows} != expected:
        raise ValueError("all six sealed subordinate tuning cases required")
    levels = {}
    for level in ("lat", "hat"):
        residuals = [_finite(r[f"{level}_residual_m"], "residual") for r in rows]
        absolute = list(map(abs, residuals))
        levels[level] = {"count": 6, "signed_bias_m": mean(residuals), "sample_sd_m": stdev(residuals),
                         "median_abs_m": median(absolute), "max_abs_m": max(absolute),
                         "passed": median(absolute) <= .03 and max(absolute) <= .08}
    return {"levels": levels, "passed": all(v["passed"] for v in levels.values()), "new_coverage": 0,
            "accepted": False, "uncertainty_basis": "tuning scatter only; calibration admission not established"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="*", type=Path)
    parser.add_argument("--batch-input", type=Path, help="prepared own-source NOAA production payload array, without comparators")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--summarize", action="store_true", help="summarize an existing six-case output without predictions")
    parser.add_argument("--ruby", default="ruby")
    parser.add_argument("--start", default=START)
    parser.add_argument("--end", default=END)
    args = parser.parse_args()
    if args.batch_input is not None:
        if args.inputs or args.summarize:
            parser.error("batch-input is separate from tuning inputs and summary")
        payloads = json.loads(args.batch_input.read_text())
        if not isinstance(payloads, list) or not payloads:
            raise ValueError("nonempty prepared NOAA batch required")
        for payload in payloads:
            if any(key.startswith("comparison") for key in payload):
                raise ValueError("production subordinate inputs must not contain comparators")
            if payload.get("offset_source") != "noaa" or not payload.get("station", {}).get("aliases", {}).get("noaa"):
                raise ValueError("prepared own-source NOAA offsets required")
        results = compute_batch(payloads, start=args.start, end=args.end, ruby=args.ruby)
        with args.out.open("w") as output:
            for payload, raw in zip(payloads, results, strict=True):
                row = {"station_id": payload["station"]["station_id"], "raw": raw,
                       "input_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()}
                if (args.start, args.end) == (START, END):
                    row["draft_datum"] = datum_draft(payload, raw)
                output.write(json.dumps(row, allow_nan=False) + "\n")
        return
    if not args.inputs and not args.summarize:
        parser.error("tuning inputs or batch-input required")
    if args.summarize:
        rows = [json.loads(line) for line in args.out.read_text().splitlines()]
        print(json.dumps(tuning_summary(rows), allow_nan=False))
        return
    tuning = set(load("partitions", "tuning", "noaa"))
    with args.out.open("w") as output:
        for path in args.inputs:
            payload = json.loads(path.read_text())
            ident = payload["comparison"]["id"]
            if ident not in tuning or ident != payload["station"]["aliases"]["noaa"]:
                raise ValueError("sealed NOAA tuning input required")
            raw = compute_raw(payload, start=args.start, end=args.end, ruby=args.ruby)
            row = {"station_id": payload["station"]["station_id"], "source_record_id": ident, "raw": raw,
                   "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            if (args.start, args.end) == (START, END):
                row["draft_datum"] = datum_draft(payload, raw)
                for level in ("lat", "hat"):
                    truth = payload["comparison"][level.upper()] - payload["comparison"]["datums"]["MLLW"]
                    row[f"{level}_residual_m"] = raw[f"{level}_m"] - truth
            output.write(json.dumps(row, allow_nan=False) + "\n")
            output.flush()


if __name__ == "__main__":
    main()
