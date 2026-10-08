"""Check the draft JSON Schema and the example document.

1. The current schema (0.2) and the older schemas are valid draft 2020-12 schemas.
2. src/schema/example.json validates. Its .meta.json form (no stations)
   validates against #/$defs/meta, and each station (one .jsonl line)
   validates against #/$defs/station.
3. Alias ids are unique within each alias system across the release. JSON
   Schema cannot express this, so this script checks it, as the release
   build will.
4. Negative controls must fail: for example a constant set without
   convention_id or quantity, a local convention without utc_offset_hours, a
   bad datestamp, a constituent name over 15 characters, and duplicate aliases.

Needs the jsonschema package (pip install jsonschema).
"""
import copy
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

root = Path(__file__).resolve().parent.parent
schema_dir = root / "src/schema"
schema = json.loads((schema_dir / "otc-0.2.schema.json").read_text())
example = json.loads((schema_dir / "example.json").read_text())

for old in sorted(schema_dir.glob("otc-*.schema.json")):
    Draft202012Validator.check_schema(json.loads(old.read_text()))
validator = Draft202012Validator(schema)


def sub_validator(name):
    return Draft202012Validator({"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{name}"})


meta_validator = sub_validator("meta")
station_validator = sub_validator("station")


def duplicate_aliases(doc):
    """Return (system, id) pairs used by more than one station."""
    seen = {}
    dups = []
    for station in doc["stations"]:
        for system, ids in station.get("aliases", {}).items():
            for alias in ids if isinstance(ids, list) else [ids]:
                owner = seen.setdefault((system, alias), station["station_id"])
                if owner != station["station_id"]:
                    dups.append((system, alias))
    return dups


def problems(doc):
    found = [f"{list(e.path)} {e.message}" for e in validator.iter_errors(doc)]
    found += [f"duplicate alias {system}:{alias}" for system, alias in duplicate_aliases(doc)]
    return found


errors = problems(example)
if errors:
    for e in errors:
        print("example.json:", e)
    sys.exit(1)
print("ok: example.json is valid")

meta = {k: v for k, v in example.items() if k != "stations"}
if not meta_validator.is_valid(meta):
    print("FAIL: example.json without stations is not a valid .meta.json")
    sys.exit(1)
print("ok: example.json without stations is a valid .meta.json")
for station in example["stations"]:
    if not station_validator.is_valid(station):
        print(f"FAIL: {station['station_id']} is not a valid .jsonl line")
        sys.exit(1)
print("ok: each station is a valid .jsonl line")


def must_fail(label, mutate):
    doc = copy.deepcopy(example)
    mutate(doc)
    if not problems(doc):
        print(f"FAIL: negative control passed validation: {label}")
        sys.exit(1)
    print(f"ok: rejected {label}")


def first_set(d):
    return d["stations"][0]["constant_sets"][0]


def second_station_same_aliases(d):
    twin = copy.deepcopy(d["stations"][0])
    twin["station_id"] = "OTC-EXAMPLE-0003"
    d["stations"].append(twin)


must_fail("constant set without convention_id",
          lambda d: first_set(d).pop("convention_id"))
must_fail("local convention without utc_offset_hours",
          lambda d: d["conventions"][1].pop("utc_offset_hours"))
must_fail("bad datestamp",
          lambda d: d["release"].__setitem__("datestamp", "2099-12-31"))
must_fail("phase of 360 degrees",
          lambda d: first_set(d)["constituents"][0].__setitem__("phase_deg", 360))
must_fail("constant set without quantity",
          lambda d: first_set(d).pop("quantity"))
must_fail("constant set without source_type",
          lambda d: first_set(d).pop("source_type"))
must_fail("active station without timezone",
          lambda d: d["stations"][0].pop("timezone"))
must_fail("release without the constituents table",
          lambda d: d.pop("constituents"))
must_fail("constituent name of 16 characters",
          lambda d: first_set(d)["constituents"][0].__setitem__("name", "M" * 16))
must_fail("dropped constituent name of 16 characters",
          lambda d: first_set(d)["dropped_constituents"][0].__setitem__("name", "S" * 16))
must_fail("constituent source_name of 32 characters",
          lambda d: first_set(d)["constituents"][0].__setitem__("source_name", "x" * 32))
must_fail("duplicate gesla alias in one station",
          lambda d: d["stations"][0]["aliases"].__setitem__("gesla", ["example-file-id", "example-file-id"]))
must_fail("two stations with the same aliases", second_station_same_aliases)
