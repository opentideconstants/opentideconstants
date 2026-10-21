"""Private FES2022b ocean grids -> additive station-point model constants.

Loading grids are retained inputs, not gauge-relative ocean elevation. No authenticated
download occurs here; acquisition owns the immutable, SHA-pinned private input cache.
"""
from __future__ import annotations

import copy
import cmath
import json
import math
import os
from pathlib import Path
from typing import Iterable

import numpy as np
from netCDF4 import Dataset

from otc_pipeline.adapter import FetchResult, InputFile, SourceSet, is_relative_inside, sha256_file
from otc_pipeline.constituent_speeds import TableSpeeds, dropped_no_row
from otc_pipeline.adapters.noaa import canonical_sha256
from otc_pipeline.gesla_release import recommend, reconcile_recommendation, set_order
from otc_pipeline.combine import _candidate
from otc_pipeline.gesla_sets import label_fallback

SOURCE_VERSION = "FES2022b"
OCEAN_DIRECTORY = "ocean_tide_20241025"
OCEAN_MANIFEST_NAME = "OCEAN_MANIFEST.json"
NATIVE_NAMES = ("2n2", "eps2", "j1", "k1", "k2", "l2", "lambda2", "m2", "m3", "m4", "m6",
                "m8", "mf", "mks2", "mm", "mn4", "ms4", "msf", "msqm", "mtm", "mu2", "n2",
                "n4", "nu2", "o1", "p1", "q1", "r2", "s1", "s2", "s4", "sa", "ssa", "t2")
ALGORITHM_VERSION = "complex-bilinear-valid-corners-ocean-v1"
LICENCE = {
    "licence_id": "fes2022-aviso-derived", "spdx": "LicenseRef-AVISO-Issue20",
    "provider": "CNES, LEGOS, Noveltis and CLS / AVISO+", "commercial_use": True,
    "attribution": "Contains model constants derived from FES2022 (Cnes/Legos/Noveltis/CLS), "
                   "doi:10.24400/527896/a01-2024.004. Modified by complex interpolation to station points.",
    "citation": "FES2022, doi:10.24400/527896/a01-2024.004",
    "terms_url": "https://www.aviso.altimetry.fr/fileadmin/documents/data/License_Aviso.pdf",
    "restriction": "Derived point products only; original grids are not redistributed. "
                   "Original-grid onward distribution requires written CNES authorization.",
}

# The accepted canonical release's actual G9 station coordinates. Full builds supply
# their canonical station roster instead; there is no model-driven position adjustment.
CONTRACT_STATIONS = {
    "8443970": {"name": "BOSTON", "country": "USA", "lat": 42.35388888888889,
        "lon": -71.05027777777778, "type": "reference", "timezone": "America/New_York",
        "aliases": {"noaa": "8443970", "gesla": ["boston-8443970-usa-noaa"]}},
    "9447130": {"name": "SEATTLE (Madison St.), Elliott Bay", "country": "USA", "lat": 47.60263888888889,
        "lon": -122.3393055555556, "type": "reference", "timezone": "America/Los_Angeles",
        "aliases": {"noaa": "9447130", "gesla": ["seattle-9447130-usa-noaa"]}},
    "9414290": {"name": "SAN FRANCISCO (Golden Gate)", "country": "USA", "lat": 37.80630555555555,
        "lon": -122.4658888888889, "type": "reference", "timezone": "America/Los_Angeles",
        "aliases": {"noaa": "9414290", "gesla": ["san_francisco-9414290-usa-noaa"]}},
}


def load_manifest(root: Path, expected_sha256: str | None = None) -> tuple[dict, str, tuple[InputFile, ...]]:
    """Verify the manifest and all its expanded members before sampling any model values."""
    root = Path(root)
    path = root / OCEAN_MANIFEST_NAME
    pin = sha256_file(path)
    if expected_sha256 is not None and pin != expected_sha256:
        raise ValueError("FES manifest SHA-256 mismatch")
    manifest = json.loads(path.read_text())
    if manifest.get("source_version") != SOURCE_VERSION:
        raise ValueError(f"FES source_version must be {SOURCE_VERSION}")
    files = []
    seen = set()
    for row in manifest["files"]:
        name = row["path"]
        if not is_relative_inside(name) or not (root / name).resolve().is_relative_to(root.resolve()):
            raise ValueError(f"FES file path must stay inside cache: {name}")
        if name in seen:
            raise ValueError(f"duplicate FES input: {name}")
        seen.add(name)
        member = root / name
        if not member.is_file() or member.stat().st_size != row["size"] or sha256_file(member) != row["sha256"]:
            raise ValueError(f"FES input SHA-256/size mismatch: {name}")
        files.append(InputFile(**{key: row[key] for key in ("path", "sha256", "size", "source_url", "fetched_at")}))
    if not files:
        raise ValueError("FES manifest has no input files")
    return manifest, pin, tuple(files)


def mapped_name(native: str, speeds: TableSpeeds) -> str | None:
    name = {"lambda2": "LAM2"}.get(native.lower(), native.upper())
    return name if speeds.tcd_name(name) is not None else None


def interpolate_complex(values: list, weights: list[float]) -> tuple[float, float, int] | None:
    """CNES bounded bilinear rule: normalize weights of valid immediate corners only.

    Complex conjugation is undone on output: published phases remain Greenwich lags.
    No valid weight means unavailable, never a manufactured zero amplitude.
    See CNES/aviso-fes include/fes/detail/math.hpp, bilinear_interpolation.
    """
    usable = [(value, weight) for value, weight in zip(values, weights) if value is not None]
    total = sum(weight for _, weight in usable)
    if total <= 0:
        return None
    z = sum(cmath.rect(value[0], -math.radians(value[1])) * weight for value, weight in usable) / total
    phase = -math.degrees(cmath.phase(z)) % 360
    return abs(z), 0.0 if phase >= 360 or z == 0 else phase, len(usable)


def sample_grid(path: Path, stations: dict) -> dict:
    """Read one grid's axes and four immediate cells per point, not its global arrays."""
    results = {}
    with Dataset(path) as ds:
        amplitude, phase = ds.variables["amplitude"], ds.variables["phase"]
        if (amplitude.dimensions != ("lat", "lon") or phase.dimensions != ("lat", "lon")
                or amplitude.units != "cm" or phase.units != "degrees"):
            raise ValueError(f"unsupported FES grid dimensions or units: {path}")
        lats = np.asarray(ds.variables["lat"][:], dtype=float)
        lons = np.asarray(ds.variables["lon"][:], dtype=float)
        if not (np.all(np.diff(lats) > 0) and np.all(np.diff(lons) > 0)
                and lons[0] == 0 and lons[-1] == 360):
            raise ValueError(f"unsupported FES grid axes: {path}")
        cells = {}
        for rid, station in stations.items():
            lat, lon = float(station["lat"]), float(station["lon"]) % 360
            if not math.isfinite(lat) or not math.isfinite(lon) or not lats[0] <= lat <= lats[-1]:
                results[rid] = {"status": "unavailable", "reason": "outside grid", "cells": []}
                continue
            iy = max(0, min(len(lats) - 2, int(np.searchsorted(lats, lat, side="right")) - 1))
            ix = max(0, min(len(lons) - 2, int(np.searchsorted(lons, lon, side="right")) - 1))
            y = (lat - lats[iy]) / (lats[iy + 1] - lats[iy])
            x = (lon - lons[ix]) / (lons[ix + 1] - lons[ix])
            indices = [(iy, ix), (iy, ix + 1), (iy + 1, ix), (iy + 1, ix + 1)]
            weights = [float((1-y)*(1-x)), float((1-y)*x), float(y*(1-x)), float(y*x)]
            values, evidence = [], []
            for (j, i), weight in zip(indices, weights):
                if (j, i) not in cells:
                    a, g = amplitude[j, i], phase[j, i]
                    cells[j, i] = None if (np.ma.is_masked(a) or np.ma.is_masked(g)
                        or not math.isfinite(float(a)) or not math.isfinite(float(g)) or float(a) < 0) else (float(a) / 100, float(g))
                value = cells[j, i]
                values.append(value)
                evidence.append({"lat_index": j, "lon_index": i, "weight": weight,
                    "amplitude_cm": value[0] * 100 if value is not None else None,
                    "phase_deg": value[1] if value is not None else None})
            interpolated = interpolate_complex(values, weights)
            if interpolated is None:
                results[rid] = {"status": "unavailable", "reason": "no valid weighted immediate grid corner", "cells": evidence}
            else:
                a, g, used = interpolated
                results[rid] = {"status": "available", "amplitude_m": a, "phase_deg": g,
                                "corners_used": used, "cells": evidence}
    return results


class Fes2022Adapter:
    source = "fes2022"
    source_type = "model"
    quantity = "water_level"
    adapter_version = "1.0.0"
    contract_sample = ("8443970", "9447130", "9414290")

    def __init__(self, *, stations: dict | None = None, speeds: TableSpeeds | None = None,
                 manifest_sha256: str | None = None):
        self.stations = copy.deepcopy(CONTRACT_STATIONS if stations is None else stations)
        self._speeds = speeds
        self.manifest_sha256 = manifest_sha256

    @property
    def speeds(self) -> TableSpeeds:
        if self._speeds is None:
            self._speeds = TableSpeeds.from_tcd_dump()
        return self._speeds

    def supported_names(self) -> set[str]:
        return {name for native in NATIVE_NAMES if (name := mapped_name(native, self.speeds)) is not None}

    def fetch(self, cache_dir: Path, only: list[str] | None = None) -> FetchResult:
        wanted = sorted(self.stations if only is None else set(only))
        unknown = set(wanted) - self.stations.keys()
        if unknown:
            raise ValueError(f"unknown FES station: {sorted(unknown)}")
        root = Path(os.environ.get("OTC_FES_INPUT_ROOT", cache_dir))
        manifest, pin, files = load_manifest(root, self.manifest_sha256)
        required = {f"{OCEAN_DIRECTORY}/{name}_fes2022.nc" for name in NATIVE_NAMES}
        missing = required - {f.path for f in files}
        if missing:
            raise ValueError(f"FES2022b input inventory incomplete: {len(missing)} required ocean files missing")
        items = extract(root, manifest, pin, files, {key: self.stations[key] for key in wanted}, self.speeds,
                        derived_dir=Path(cache_dir) / "derived")
        return FetchResult(self.source, SOURCE_VERSION, root, files, items)

    def convention(self) -> dict:
        return {"convention_id": "fes2022b-ocean-greenwich", "phase_reference": "greenwich_utc",
                "v0_model": "schureman_tcd", "nodal_handling": "f_u_at_prediction",
                "constituent_table_version": "fes2022b-supported"}

    def licence(self) -> dict:
        return dict(LICENCE)

    def records(self, fetched: FetchResult) -> Iterable:
        return ()

    def watch_keys(self, fetched: FetchResult) -> dict[str, str]:
        return {key: canonical_sha256(value) for key, value in fetched.items.items()}

    def sets(self, fetched: FetchResult) -> Iterable[SourceSet]:
        for rid, item in sorted(fetched.items.items()):
            if not item["constituents"]:
                continue
            dropped = [dropped_no_row(row["native_name"].upper()) for row in item["native"]
                       if row.get("mapped_name") is None and row["status"] == "available"]
            cs = {
                "source": self.source, "source_type": self.source_type, "quantity": self.quantity,
                "source_record_id": rid, "source_version": SOURCE_VERSION,
                "convention_id": self.convention()["convention_id"], "licence_id": LICENCE["licence_id"],
                "qc_status": "ok", "constituents": item["constituents"],
                **({"dropped_constituents": dropped} if dropped else {}),
                "provenance": {"adapter_version": self.adapter_version,
                    "input_sha256": item["input_sha256"],
                    "selection_reason": "FES2022b structured ocean elevation; complex bilinear interpolation "
                                        "at the existing station coordinates. No loading tide added; no datum tie. "
                                        + item["detail"]},
            }
            if item["missing_supported"]:
                label_fallback(cs, "reliability_withheld", "Unavailable model constituents at this point: "
                               + ", ".join(item["missing_supported"]), basis="Retained available FES2022b ocean coefficients")
            yield SourceSet(station_hint=item["station"], constant_set=cs)


def extract(root: Path, manifest: dict, pin: str, files: tuple[InputFile, ...],
            stations: dict, speeds: TableSpeeds, *, derived_dir: Path) -> dict:
    """Extract exact requested station points, retaining every native result privately."""
    key = canonical_sha256({"manifest_sha256": pin, "stations": stations, "algorithm": ALGORITHM_VERSION,
                            "tcd_dump_sha256": speeds.dump_sha256,
                            "speeds": speeds.speeds, "aliases": speeds.aliases})
    output = derived_dir / f"{key}.json"
    receipt = derived_dir / f"{key}.sha256"
    if output.is_file() and receipt.is_file() and sha256_file(output) == receipt.read_text().strip():
        cached = json.loads(output.read_text())
        if cached["key"] == key:
            return cached["items"]
    ocean = {Path(f.path).name.removesuffix("_fes2022.nc"): f for f in files
             if Path(f.path).parent.as_posix() == OCEAN_DIRECTORY}
    items = {rid: {"station": hint, "native": [], "constituents": [], "input_sha256": [],
                   "manifest_sha256": pin, "missing_supported": []} for rid, hint in stations.items()}
    for native, file in sorted(ocean.items()):
        name = mapped_name(native, speeds)
        points = sample_grid(root / file.path, stations)
        for rid, point in points.items():
            item = items[rid]
            item["native"].append({"native_name": native, "mapped_name": name,
                                   "input_sha256": file.sha256, **point})
            item["input_sha256"].append(file.sha256)
            if point["status"] == "available" and name is not None:
                item["constituents"].append({"name": name, "source_name": native,
                    "speed_deg_per_hour": speeds.speed(name), "amplitude_m": point["amplitude_m"],
                    "phase_deg": point["phase_deg"]})
            elif name is not None:
                item["missing_supported"].append(name)
    for item in items.values():
        available = sum(row["status"] == "available" for row in item["native"])
        item["detail"] = (f"{len(item['constituents'])} supported coefficients; {available}/{len(ocean)} native "
                          "ocean coefficients available. Masked immediate corners are omitted with remaining "
                          "bilinear weights normalized; no offshore search or extrapolation.")
    derived_dir.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"key": key, "algorithm": ALGORITHM_VERSION, "manifest_sha256": pin,
                                 "items": items}, allow_nan=False, sort_keys=True) + "\n")
    receipt.write_text(sha256_file(output) + "\n")
    return items


def station_roster(release: dict) -> dict:
    """Attempt existing active water-level and identity-only coastal stations."""
    return {st["station_id"]: {key: st[key] for key in
            ("name", "country", "lat", "lon", "type", "timezone", "aliases")}
            for st in release["stations"] if st.get("status") == "active"
            and (not st.get("constant_sets") or any(cs.get("quantity") == "water_level"
                                                   for cs in st["constant_sets"]))
            and "default_current_bin" not in st}


def extend_cached_astronomy(release: dict, extraction: dict, *, primary, extended) -> dict:
    """Materialize only retained MSQM/MTM values using the separately authenticated FES table."""
    from otc_pipeline.astro_tables import (FES_ADDED, FES_TABLE_ID, FES_TCD_SHA256, TABLE_ID,
                                          TCD_SHA256, CongenRun, astro_table, release_table_errors)

    if (not isinstance(primary, CongenRun) or not isinstance(extended, CongenRun)
            or primary.tcd.tcd_sha256 != TCD_SHA256 or extended.tcd.tcd_sha256 != FES_TCD_SHA256):
        raise ValueError("FES extension requires authenticated original and extended astronomy runs")
    if set(extended.tcd.constituents) != set(primary.tcd.constituents) | set(FES_ADDED):
        raise ValueError("FES extension changes other constituent identities")
    for name, old in primary.tcd.constituents.items():
        new = extended.tcd.constituents[name]
        if (old.speed, old.v0u, old.f) != (new.speed, new.v0u, new.f):
            raise ValueError(f"FES extension changes existing astronomy: {name}")
    if dict(primary.tcd.aliases) != dict(extended.tcd.aliases):
        raise ValueError("FES extension changes existing astronomy aliases")
    if extraction.get("algorithm") != ALGORITHM_VERSION:
        raise ValueError("FES extraction algorithm differs")
    conventions = [row for row in release["conventions"] if row["convention_id"] == "fes2022b-ocean-greenwich"]
    if len(conventions) != 1 or conventions[0]["astro_table_id"] != TABLE_ID:
        raise ValueError("FES convention is absent or already extended")
    table = next(row for row in release["astro_tables"] if row["astro_table_id"] == TABLE_ID)
    if release_table_errors(table, *primary):
        raise ValueError("Original astronomy table differs from the accepted run")
    speeds = TableSpeeds({name: row.speed for name, row in extended.tcd.constituents.items()}, extended.tcd.aliases)
    staged, names = [], set(FES_ADDED)
    for station in release["stations"]:
        for cs in station["constant_sets"]:
            if cs["source"] != "fes2022":
                continue
            sid = station["station_id"]
            item = extraction.get("items", {}).get(sid)
            if item is None or cs["provenance"]["input_sha256"] != item.get("input_sha256"):
                raise ValueError(f"FES retained input binding differs: {sid}")
            record = next((row for row in station["source_records"] if row["source"] == "fes2022"), None)
            if record is None or record["provenance"]["inventory_sha256"] != extraction.get("manifest_sha256"):
                raise ValueError(f"FES retained manifest differs: {sid}")
            if cs["constituents"] != item.get("constituents"):
                raise ValueError(f"FES existing coefficients differ: {sid}")
            raw = {row["native_name"]: row for row in item["native"]}
            if len(raw) != len(item["native"]) or set(raw) != set(NATIVE_NAMES):
                raise ValueError(f"FES native inventory differs: {sid}")
            for coefficient in cs["constituents"]:
                source = raw[coefficient["source_name"]]
                if source["status"] != "available" or any(coefficient[key] != source[key] for key in ("amplitude_m", "phase_deg")):
                    raise ValueError(f"FES retained coefficient differs: {sid}")
            additions = []
            for name in FES_ADDED:
                source = raw[name.lower()]
                if source["status"] != "available":
                    continue
                amplitude, phase = source["amplitude_m"], source["phase_deg"]
                if not math.isfinite(amplitude) or amplitude < 0 or not math.isfinite(phase) or not 0 <= phase < 360:
                    raise ValueError(f"Invalid retained FES coefficient: {sid}/{name}")
                dropped = next((row for row in cs.get("dropped_constituents", []) if row["name"] == name), None)
                if dropped != dropped_no_row(name):
                    raise ValueError(f"FES constituent has another exclusion: {sid}/{name}")
                additions.append({"name": name, "source_name": name.lower(), "speed_deg_per_hour": speeds.speed(name),
                                  "amplitude_m": amplitude, "phase_deg": phase})
            staged.append((cs, additions))
            names.update(row["name"] for row in cs["constituents"])
    extended_table = astro_table(*extended, names, table_id=FES_TABLE_ID)
    for name in names - set(FES_ADDED):
        if extended_table["constituents"][name] != table["constituents"][name]:
            raise ValueError(f"FES extension changes a published astronomy row: {name}")
    # All bindings and numerical preservation checks precede mutation.
    for cs, additions in staged:
        cs["constituents"].extend(additions)
        added = {row["name"] for row in additions}
        dropped = [row for row in cs.get("dropped_constituents", []) if row["name"] not in added]
        if dropped:
            cs["dropped_constituents"] = dropped
        else:
            cs.pop("dropped_constituents", None)
    conventions[0]["astro_table_id"] = FES_TABLE_ID
    release["astro_tables"].append(extended_table)
    release["constituents"].extend({"name": name, "constituent_table_version": conventions[0]["constituent_table_version"],
                                    "speed_deg_per_hour": speeds.speed(name)} for name in FES_ADDED)
    return {"sets_extended": sum(bool(rows) for _, rows in staged),
            "coefficients_added": sum(len(rows) for _, rows in staged)}


def attach(release: dict, fetched: FetchResult, *, build_commit: str, table_id: str,
           canary_result, adapter: Fes2022Adapter) -> dict:
    """Append model results by canonical ID, preserving all preexisting source values."""
    convention = {**adapter.convention(), "astro_table_id": table_id,
                  "canary": canary_result.convention_canary()}
    for collection, row, identity in (("conventions", convention, "convention_id"),
                                       ("licences", adapter.licence(), "licence_id")):
        existing = next((old for old in release[collection] if old[identity] == row[identity]), None)
        if existing is not None and existing != row:
            raise ValueError(f"conflicting FES {identity}")
        if existing is None:
            release[collection].append(row)
    versions = release["release"]["source_versions"]
    if adapter.source in versions and versions[adapter.source] != SOURCE_VERSION:
        raise ValueError("conflicting FES source version")
    versions[adapter.source] = SOURCE_VERSION
    table_version = convention["constituent_table_version"]
    for name in sorted(adapter.supported_names()):
        row = {"name": name, "constituent_table_version": table_version, "speed_deg_per_hour": adapter.speeds.speed(name)}
        old = next((r for r in release["constituents"] if r["name"] == name
                    and r["constituent_table_version"] == table_version), None)
        if old is not None and old != row:
            raise ValueError(f"conflicting FES constituent table row: {name}")
        if old is None:
            release["constituents"].append(row)
    sets = {ss.constant_set["source_record_id"]: ss.constant_set for ss in adapter.sets(fetched)}
    by_id = {station["station_id"]: station for station in release["stations"]}
    report = {"attempted": len(fetched.items), "sets_added": 0, "unavailable": [],
              "partial": [], "canary": canary_result.to_dict(),
              "model_version": SOURCE_VERSION, "algorithm": ALGORITHM_VERSION,
              "inputs": [file.as_dict() for file in fetched.files]}
    for rid, item in fetched.items.items():
        station = by_id[rid]
        if any(r["source"] == adapter.source for r in station.get("source_records", [])):
            raise ValueError(f"FES already attached at {rid}")
        cs = copy.deepcopy(sets.get(rid))
        ids = []
        if cs is not None:
            cs["set_id"] = f"{rid}/fes2022"
            cs["provenance"]["build"] = {"build_commit": build_commit}
            if not canary_result.passed:
                label_fallback(cs, "reliability_withheld", "FES2022b model/gauge canary failed: "
                               + "; ".join(canary_result.reasons),
                               basis="Actual ocean-grid coefficients retained; model agreement does not establish accuracy")
            station["constant_sets"].append(cs)
            ids.append(cs["set_id"])
            report["sets_added"] += 1
            if item["missing_supported"]:
                report["partial"].append(rid)
            licences = {lic["licence_id"]: lic for lic in release["licences"]}
            candidates = [_candidate(c, licences[c["licence_id"]]["commercial_use"])
                          for c in station["constant_sets"]]
            old_win = next((c for c in candidates if c.cs["set_id"] == station.get("recommended_set_id")), None)
            win = old_win if old_win is not None else recommend(candidates)[0]
            station["constant_sets"] = [candidate.cs for candidate in set_order(win, candidates)]
            reconcile_recommendation(station, win)
        else:
            report["unavailable"].append(rid)
        station.setdefault("source_records", []).append({
            "source": adapter.source, "source_version": SOURCE_VERSION, "source_record_id": rid,
            "role": "model", "disposition": "retained" if cs is not None else "unavailable",
            "detail": item["detail"], "licence_id": LICENCE["licence_id"], "published_set_ids": ids,
            "provenance": {"input_sha256": item["input_sha256"], "inventory_sha256": item["manifest_sha256"]},
            "original_metadata": {"latitude": item["station"]["lat"], "longitude": item["station"]["lon"],
                                  "ocean_grid": OCEAN_DIRECTORY, "algorithm": ALGORITHM_VERSION,
                                  "loading_added": False}})
    return report
