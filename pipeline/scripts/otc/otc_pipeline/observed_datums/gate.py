"""The first-release T1 gate: datums from tabulation publish only for stations that pass T1 (orchestrator
decision, DT1). The sealed T1 harness (otc_pipeline.datum_t1) writes t1-station-gate-<partition>.json;
the build reads it here. A station the gate does not list is untested and does not pass: no datum value
from tabulation is published for it."""
from __future__ import annotations

import json
from pathlib import Path

GATE_FORMAT = "otc-t1-station-gate-v1"


class T1Gate:
    def __init__(self, data: dict, *, require_labels: bool = True) -> None:
        if data.get("format") != GATE_FORMAT:
            raise ValueError(f"not a T1 station gate: format {data.get('format')!r}")
        self.partition = data["partition"]
        self.require_labels = require_labels
        self._stations = dict(data["stations"])

    @classmethod
    def load(cls, *paths: str | Path, require_labels: bool = True) -> "T1Gate":
        """One or more gate files (one per partition run); a station listed twice must agree."""
        merged: dict = {}
        part = []
        for p in paths:
            d = json.loads(Path(p).read_text())
            if d.get("format") != GATE_FORMAT:
                raise ValueError(f"{p}: not a T1 station gate")
            part.append(d["partition"])
            for sid, v in d["stations"].items():
                if sid in merged and merged[sid] != v:
                    raise ValueError(f"{p}: station {sid} has two different verdicts")
                merged[sid] = v
        return cls({"format": GATE_FORMAT, "partition": "+".join(part), "stations": merged},
                   require_labels=require_labels)

    def verdict(self, station_id: str) -> str:
        """'pass', 'fail' or 'untested'."""
        v = self._stations.get(station_id)
        if v is None:
            return "untested"
        ok = v["pass"] if self.require_labels else v["pass_without_labels"]
        return "pass" if ok else "fail"

    def allows(self, station_id: str) -> bool:
        return self.verdict(station_id) == "pass"
