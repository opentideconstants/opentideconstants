"""DT4a arithmetic and tuning-only T5 harness, with no release admission.

The external strict engine owns astronomy and extrema search. Results here are
raw method evidence. Calibration, T5b and T6 remain separate acceptance gates;
no uncertainty is invented and this module is not called by the release build.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import subprocess
import time
from statistics import mean, median, stdev

from otc_pipeline.datum_partition import load

START = "2020-01-01T00:00:00Z"
END = "2039-01-01T00:00:00Z"
EPOCH = {"start": "2020-01-01", "end": "2038-12-31", "name": "OTC LAT 2020-2038"}
COMPUTED_FALLBACK = {
    "reason": "datum_gate_failed",
    "basis": "Existing strict engine, all constituents, OTC LAT 2020-2038; source-level tuning uncertainty plus unverified sampling term",
    "otc_finding": "Full DT4a/GATE-DATUM acceptance is not established: T5b failed the approved worst-absolute-residual station rule (310 stations, 360 fits retained). LAT median/p95 0.052609/0.240028 m; HAT 0.047485/0.262431 m exceed 0.03/0.08 m. Accuracy is not validated.",
}


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def read_tuning_input(path: Path, *, tier: str = "T5", data_dir: Path | None = None) -> dict:
    """Load a native reference set, refusing all stations outside sealed tuning."""
    payload = json.loads(Path(path).read_text())
    cs = payload["constant_set"]
    reference = payload.get("comparison_set", cs)
    partition_source, partition_id = "noaa", reference.get("source_record_id")
    if tier == "T6":
        partition_source = cs.get("source")
        if partition_source not in ("kartverket", "linz") or reference != cs:
            raise ValueError("T6 requires the source's own Kartverket or LINZ reference set")
        partition_id = cs.get("source_record_id")
        if partition_source == "linz":
            from otc_pipeline.adapters.linz import parse_port_csv_header
            raw_header = Path(payload["linz_port_header_path"]).read_bytes()
            if hashlib.sha256(raw_header).hexdigest() != payload["linz_header_sha256"]:
                raise ValueError("LINZ cached header SHA-256 changed")
            port = parse_port_csv_header(raw_header)
            if not partition_id.startswith(f"{port.number:03d}"):
                raise ValueError("LINZ native id and cached header number differ")
            partition_id = port.name.lower().replace(" ", "_")
            if partition_id != payload["partition_source_id"]:
                raise ValueError("LINZ header and sealed partition id differ")
    elif tier == "T5b":
        if cs.get("source") != "gesla-fit":
            raise ValueError("T5b requires an existing GESLA fitted set")
        if not payload.get("station_id") or payload.get("station_id") != payload.get("comparison_station_id"):
            raise ValueError("T5b fit and reference must belong to the same native station")
    elif tier != "T5" or cs.get("source") != "noaa" or reference != cs:
        raise ValueError("T5 requires NOAA's own harmonic reference set")
    if reference.get("source") != partition_source or partition_id not in load(
            "partitions", "tuning", partition_source, data_dir=data_dir):
        raise ValueError(f"{tier} accepts only sealed {partition_source} tuning reference stations")
    if cs.get("quantity") != "water_level" or not cs.get("constituents"):
        raise ValueError("T5 requires a water-level reference set with constants")
    for key in (("lat", "hat") if tier == "T6" else ("lat", "hat", "msl")):
        if reference["datum"].get("basis", {}).get(key, {}).get("kind") != "published":
            raise ValueError(f"T5 requires published {key}")
        _finite(reference["datum"]["named"].get(key), f"published {key}")
    return payload


def _strict_request(payload: dict, start: str = START, end: str = END) -> dict:
    cs = payload["constant_set"]
    return {"astro_table": payload["astro_table"], "constituents": cs["constituents"],
            "msl_offset_m": _finite(cs["datum"].get("msl_offset_m"), "msl_offset_m"),
            "start": start, "end": end}


def computation_input_sha256(payload: dict) -> str:
    """Bind extrema and uncertainty to the consumed request and source context."""
    cs = payload["constant_set"]
    body = {"request": _strict_request(payload), "convention_id": cs.get("convention_id"),
            "source_version": cs.get("source_version"), "time_base": cs.get("time_base"),
            "record_issues": cs.get("record_issues", []),
            "truncated_lows": sorted(k for k, b in cs["datum"].get("basis", {}).items()
                                     if "truncated_lows" in b.get("flags", []))}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def compute_raw(payload: dict, *, start: str = START, end: str = END,
                ruby: str = "ruby") -> dict:
    """Find raw extrema, adding the set's stated Z0 exactly once in the engine."""
    cs = payload["constant_set"]
    if cs.get("quantity") != "water_level" or cs.get("qc_status") not in ("ok", "flagged"):
        raise ValueError("eligible water-level set required")
    request = _strict_request(payload, start, end)
    root = os.environ.get("OTC_ENGINE_WORKTREE")
    if not root or not (Path(root) / "lib/harmonics_strict.rb").is_file():
        raise ValueError("set OTC_ENGINE_WORKTREE to the external strict engine checkout")
    started = time.perf_counter()
    completed = subprocess.run([ruby, str(Path(__file__).with_name("lat_hat_strict.rb"))],
                               input=json.dumps(request, allow_nan=False), text=True,
                               stdout=subprocess.PIPE, check=True)
    result = json.loads(completed.stdout)
    result["elapsed_s"] = time.perf_counter() - started
    result["child_maxrss_native_units"] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return result


def read_production_input(path: Path) -> dict:
    """Read only the missing-level official production cohort, with no validation truth."""
    payload = json.loads(Path(path).read_text())
    cs = payload["constant_set"]
    if "comparison_set" in payload or "comparison_metadata" in payload:
        raise ValueError("production official inputs must not contain a comparator")
    if cs.get("source") not in ("noaa", "linz") or cs.get("source_type") != "official":
        raise ValueError("missing-level production cohort is official NOAA and LINZ only")
    if cs.get("quantity") != "water_level" or cs.get("qc_status") not in ("ok", "flagged") or not cs.get("constituents"):
        raise ValueError("eligible official water-level constants required")
    _finite(cs.get("datum", {}).get("msl_offset_m"), "msl_offset_m")
    if any(key in cs["datum"].get("named", {}) for key in ("lat", "otc_lat", "hat", "otc_hat")):
        raise ValueError("production cohort must lack both LAT and HAT; reuse existing levels")
    return payload


def datum_draft(constant_set: dict, raw: dict, *, flatline: bool | None = None) -> dict:
    """Proof-only computed shape. Not calibrated, validated, or admitted to a release."""
    if (raw["start"], raw["end"]) != (START, END):
        raise ValueError("only the pinned 19-year window receives the OTC epoch")
    datum = copy.deepcopy(constant_set["datum"])
    named, basis = datum.setdefault("named", {}), datum.setdefault("basis", {})
    known_flatline = flatline or any(issue.get("issue") == "flatline" for issue in constant_set.get("record_issues", []))
    suppress = known_flatline or any("truncated_lows" in b.get("flags", []) for b in basis.values())
    unknown_fit_eligibility = flatline is None and constant_set.get("source_type") == "gauge" and not suppress
    if suppress:
        for key in ("lat", "otc_lat"):
            if basis.get(key, {}).get("kind") == "computed":
                named.pop(key, None)
                basis.pop(key, None)
    for level in ("lat", "hat"):
        if level == "lat" and suppress:
            continue
        key = f"otc_{level}" if basis.get(level, {}).get("kind") == "published" else level
        named[key] = _finite(raw[f"{level}_m"], level)
        basis[key] = {"kind": "computed", "method": "harmonic_extremes", "epoch": dict(EPOCH)}
        if unknown_fit_eligibility:
            basis[key]["fallback"] = {**COMPUTED_FALLBACK,
                                      "otc_finding": COMPUTED_FALLBACK["otc_finding"] +
                                      " This record's low-water eligibility is unknown; no clean-quality claim is made."}
    return datum


def uncertainty_label(propagated: float, analytic: float, *, analytic_basis: str) -> str:
    """Select the owner's V4 label only; never change or combine numeric uncertainty."""
    _finite(propagated, "propagated contribution")
    _finite(analytic, "analytic contribution")
    if min(propagated, analytic) < 0:
        raise ValueError("uncertainty contributions must be nonnegative")
    if analytic_basis not in ("propagated", "calibrated"):
        raise ValueError("analytic contribution needs its established uncertainty basis")
    return "propagated" if propagated >= analytic else analytic_basis


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def comparison_summary(rows: list[dict], *, tier: str = "T5") -> dict:
    """Frozen T5 thresholds per level; a selected pilot cannot establish full T5."""
    out = {"pilot_only": True, "accepted": False, "station_count": len(rows)}
    if tier == "T6":
        for source in ("kartverket", "linz"):
            source_rows = [r for r in rows if r["comparison_source"] == source]
            group = {"station_count": len(source_rows)}
            for level in ("lat", "hat"):
                signed = [r[f"{level}_residual_m"] for r in source_rows]
                if not signed:
                    group[level] = {"status": "not tested", "n": 0}
                    continue
                absolute = [abs(x) for x in signed]
                p95 = _percentile(absolute, 0.95)
                value = max(absolute) if source == "linz" else p95
                group[level] = {"n": len(signed), "signed_median_m": median(signed),
                                "median_abs_m": median(absolute), "p95_abs_m": p95,
                                "max_abs_m": max(absolute), "limit_m": 0.05,
                                "statistic": "maximum" if source == "linz" else "p95",
                                "within_frozen_thresholds": value <= 0.05}
            out[source] = group
        return out
    median_limit, p95_limit = (0.01, 0.03) if tier == "T5" else (0.03, 0.08)
    if tier == "T5b":
        out["fit_set_count"] = len(rows)
        out["station_count"] = len({r["comparison_source_record_id"] for r in rows})
        out["station_aggregation"] = "largest absolute residual per station and level; all fits retained"
        out["pooled_fit_diagnostics"] = {}
    for level in ("lat", "hat"):
        scored = rows
        if tier == "T5b":
            worst = {}
            for row in rows:
                station = row["comparison_source_record_id"]
                if station not in worst or abs(row[f"{level}_residual_m"]) > abs(worst[station][f"{level}_residual_m"]):
                    worst[station] = row
            scored = list(worst.values())
            pooled = [abs(row[f"{level}_residual_m"]) for row in rows]
            out["pooled_fit_diagnostics"][level] = {
                "n": len(pooled), "median_abs_m": median(pooled) if pooled else None,
                "p95_abs_m": _percentile(pooled, 0.95) if pooled else None}
        signed = [row[f"{level}_residual_m"] for row in scored]
        if not signed:
            out[level] = {"status": "not tested", "n": 0}
            continue
        absolute = [abs(x) for x in signed]
        med, p95 = median(absolute), _percentile(absolute, 0.95)
        out[level] = {"n": len(signed), "signed_median_m": median(signed),
                      "median_abs_m": med, "p95_abs_m": p95,
                      "median_limit_m": median_limit, "p95_limit_m": p95_limit,
                      "within_t5_thresholds": med <= median_limit and p95 <= p95_limit}
    if tier == "T5b":
        out["station_level_gate"] = ("not tested" if not rows else "PASS" if all(
            out[level]["within_t5_thresholds"] for level in ("lat", "hat")) else "FAIL")
    return out


def official_calibration(rows: list[dict], expected_ids: dict[str, set[str]]) -> dict:
    """Sample SD by official source and level, retaining bias and cohort identity.

    Completeness and frozen numerical gates are required. This prepares only the
    official source component; it establishes no GESLA or short-record calibration.
    """
    grouped = {source: [] for source in expected_ids}
    seen = set()
    for row in rows:
        sid = row["set_id"]
        source = sid.split("/")[1]
        if source not in ("noaa", "kartverket", "linz") or row.get("comparison_source", source) != source:
            raise ValueError("official calibration requires the source's own reference constants")
        if sid in seen:
            raise ValueError(f"duplicate calibration set {sid}")
        seen.add(sid)
        grouped.setdefault(source, []).append({**row, "comparison_source": source})
    result = {}
    for source, source_rows in grouped.items():
        complete = {r["set_id"] for r in source_rows} == expected_ids.get(source, set())
        enough = len(source_rows) >= 2
        summary = comparison_summary(source_rows, tier="T5" if source == "noaa" else "T6")
        group_summary = summary if source == "noaa" else summary[source]
        passed = enough and all(group_summary[level].get(
            "within_t5_thresholds" if source == "noaa" else "within_frozen_thresholds", False)
                                for level in ("lat", "hat"))
        group = {"status": "available" if complete and passed else "unavailable",
                 "cohort_complete": complete, "gate_passed": passed,
                 "expected_count": len(expected_ids.get(source, set())), "actual_count": len(source_rows),
                 "estimator": "sample SD: sqrt(sum((residual-mean)^2)/(n-1))"}
        for level in ("lat", "hat"):
            values = [_finite(r[f"{level}_residual_m"], "calibration residual") for r in source_rows]
            group[level] = {"n": len(values), "signed_bias_m": mean(values) if values else None,
                            "sample_sd_m": stdev(values) if len(values) >= 2 else None}
        result[source] = group
    return result


def calibrated_draft(constant_set: dict, row: dict, calibration: dict) -> dict:
    """Add a valid empirical base plus the literal sampling term to a proof draft."""
    if row["set_id"] != constant_set["set_id"]:
        raise ValueError("calibration input set identity differs")
    source = constant_set["source"]
    if source not in ("noaa", "kartverket", "linz"):
        raise ValueError("only official reference calibration is established")
    out = copy.deepcopy(row)
    out["draft_datum"] = datum_draft(constant_set, row["raw"])
    group = calibration.get(source, {})
    out["calibration_status"] = group.get("status", "unavailable")
    if group.get("status") != "available":
        return out
    time_base = constant_set.get("time_base", {})
    verified = time_base.get("code") in ("verified", "corrected") and bool(time_base.get("published"))
    amplitude = 0.0
    for c in constant_set["constituents"]:
        if c["name"] in ("M2", "S2"):
            value = _finite(c["amplitude_m"], "sampling amplitude")
            if value < 0:
                raise ValueError("sampling amplitude must be nonnegative")
            amplitude += value
    sampling = 0.0 if verified else 0.011 * amplitude
    out["uncertainty_components"] = {}
    for level in ("lat", "hat"):
        base = group[level]["sample_sd_m"]
        if base is None:
            raise ValueError("available calibration has no empirical spread")
        key = f"otc_{level}" if constant_set["datum"].get("basis", {}).get(level, {}).get("kind") == "published" else level
        basis = out["draft_datum"]["basis"].get(key)
        if basis is None:
            continue
        basis["uncertainty_m"] = base + sampling
        basis["uncertainty_basis"] = "calibrated" if verified else uncertainty_label(sampling, base, analytic_basis="calibrated")
        if not verified:
            basis["flags"] = [*basis.get("flags", []), "sampling_assumed"]
        out["uncertainty_components"][level] = {
            "base_m": base, "base_basis": "calibrated", "sampling_m": sampling,
            "sampling_basis": "propagated", "sampling_verified": verified,
            "signed_bias_m": group[level]["signed_bias_m"], "n": group[level]["n"], "source": source}
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--ruby", default="ruby")
    parser.add_argument("--tier", choices=("T5", "T5b", "T6"), default="T5")
    parser.add_argument("--partition-data-dir", type=Path,
                        help="directory of the existing sealed lists (needed outside a source checkout)")
    parser.add_argument("--calibrate-official-rosters", nargs="+", type=Path,
                        help="prepare official uncertainties from completed result JSONLs and their native rosters")
    parser.add_argument("--production-official-calibration", type=Path,
                        help="pinned existing source calibration for missing-level official production inputs")
    parser.add_argument("--production-official-calibration-sha256")
    parser.add_argument("--without-seasonal", action="store_true",
                        help="tuning sensitivity only; fixed method retains present Sa/Ssa")
    args = parser.parse_args()
    if (args.production_official_calibration is None) != (args.production_official_calibration_sha256 is None):
        raise ValueError("production source calibration path and SHA-256 must be supplied together")
    if args.production_official_calibration:
        if args.calibrate_official_rosters or args.without_seasonal:
            raise ValueError("production arithmetic does not recalibrate or run sensitivities")
        raw_calibration = args.production_official_calibration.read_bytes()
        if hashlib.sha256(raw_calibration).hexdigest() != args.production_official_calibration_sha256:
            raise ValueError("production source calibration SHA-256 mismatch")
        calibration = json.loads(raw_calibration)
        for path in args.inputs:
            payload = read_production_input(path)
            group = calibration[payload["constant_set"]["source"]]
            if group.get("status") != "available" or not group.get("cohort_complete") or not group.get("gate_passed"):
                raise ValueError("existing official source calibration is unavailable")
        with args.out.open("w") as stream:
            for path in args.inputs:
                payload = read_production_input(path)
                cs = payload["constant_set"]
                row = {"set_id": cs["set_id"], "source_record_id": cs["source_record_id"],
                       "station_id": payload.get("station_id"), "raw": compute_raw(payload, ruby=args.ruby),
                       "accepted": False, "normal_datum_gate_accepted": False,
                       "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "source_calibration_sha256": args.production_official_calibration_sha256,
                       "native_json_sha256": payload.get("native_json_sha256")}
                row["computation_input_sha256"] = computation_input_sha256(payload)
                row = calibrated_draft(cs, row, calibration)
                for basis in row["draft_datum"]["basis"].values():
                    if basis["kind"] == "computed":
                        basis["fallback"] = dict(COMPUTED_FALLBACK)
                stream.write(json.dumps(row, allow_nan=False, sort_keys=True) + "\n")
                stream.flush()
        return
    if args.calibrate_official_rosters:
        paths, expected = {}, {}
        for roster_path in args.calibrate_official_rosters:
            for entry in json.loads(roster_path.read_text())["rows"]:
                sid = entry["set_id"]
                if sid in paths:
                    raise ValueError(f"duplicate native calibration input {sid}")
                source = sid.split("/")[1]
                if source not in ("noaa", "kartverket", "linz"):
                    raise ValueError("only official reference cohorts can be calibrated")
                payload = read_tuning_input(Path(entry["path"]), tier="T5" if source == "noaa" else "T6",
                                            data_dir=args.partition_data_dir)
                if payload["constant_set"]["set_id"] != sid:
                    raise ValueError("native roster/input identity differs")
                paths[sid] = Path(entry["path"])
                expected.setdefault(source, set()).add(sid)
        rows = []
        for path in args.inputs:
            rows.extend(row for line in path.read_text().splitlines() if (row := json.loads(line)).get("set_id"))
        calibration = official_calibration(rows, expected)
        with args.out.open("w") as stream:
            for row in rows:
                payload = json.loads(paths[row["set_id"]].read_text())
                result = calibrated_draft(payload["constant_set"], row, calibration)
                result["computation_input_sha256"] = computation_input_sha256(payload)
                stream.write(json.dumps(result, allow_nan=False, sort_keys=True) + "\n")
            stream.write(json.dumps({"official_calibration": calibration, "accepted": False}, allow_nan=False, sort_keys=True) + "\n")
        return
    # Validate the entire pilot before any calculation, so no holdout is run.
    for path in args.inputs:
        read_tuning_input(path, tier=args.tier, data_dir=args.partition_data_dir)
    rows = []
    with args.out.open("w") as stream:
        for path in args.inputs:
            payload = read_tuning_input(path, tier=args.tier, data_dir=args.partition_data_dir)
            cs = payload["constant_set"]
            reference = payload.get("comparison_set", cs)
            raw = compute_raw(payload, ruby=args.ruby)
            datum = datum_draft(cs, raw, flatline=payload.get("flatline"))
            row = {"source_record_id": cs["source_record_id"], "set_id": cs.get("set_id"),
                   "raw": raw, "draft_datum": datum, "accepted": False, "tier": args.tier,
                   "comparison_source_record_id": reference["source_record_id"],
                   "comparison_source": reference["source"],
                   "comparison_metadata": payload.get("comparison_metadata", {}),
                   "low_water_eligibility": ("unknown" if args.tier == "T5b" and "flatline" not in payload
                                              else "suppressed" if payload.get("flatline") else "eligible"),
                   "pending_gates": ["calibration", "full tuning T5", "T5b", "T6"]}
            row["computation_input_sha256"] = computation_input_sha256(payload)
            z0 = cs["datum"]["msl_offset_m"] if args.tier != "T6" else 0.0
            published_msl = reference["datum"]["named"]["msl"] if args.tier != "T6" else 0.0
            origin = "msl" if args.tier != "T6" else "set_zero"
            for level in ("lat", "hat"):
                # T5 compares both predictions and agency levels relative to MSL.
                published = reference["datum"]["named"][level] - published_msl
                computed = raw[f"{level}_m"] - z0
                row[f"{level}_published_above_{origin}_m"] = published
                row[f"{level}_computed_above_{origin}_m"] = computed
                row[f"{level}_residual_m"] = computed - published
            if args.without_seasonal:
                alternative = copy.deepcopy(payload)
                alternative["constant_set"]["constituents"] = [
                    c for c in cs["constituents"] if c["name"].lower() not in ("sa", "ssa")]
                no_seasonal = compute_raw(alternative, ruby=args.ruby)
                row["without_sa_ssa"] = no_seasonal
                row["lat_seasonal_delta_m"] = raw["lat_m"] - no_seasonal["lat_m"]
            rows.append(row)
            stream.write(json.dumps(row, allow_nan=False, sort_keys=True) + "\n")
            stream.flush()
        stream.write(json.dumps({"summary": comparison_summary(rows, tier=args.tier)}, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
