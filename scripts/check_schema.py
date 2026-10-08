"""Check the draft JSON Schema and the example document.

1. The schema is a valid draft 2020-12 schema.
2. src/schema/example.json validates.
3. Negative controls must fail: a constant set without convention_id, a local
   convention without utc_offset_hours, and a bad datestamp.

Needs the jsonschema package (pip install jsonschema).
"""
import copy
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

root = Path(__file__).resolve().parent.parent
schema = json.loads((root / "src/schema/otc-0.1.schema.json").read_text())
example = json.loads((root / "src/schema/example.json").read_text())

Draft202012Validator.check_schema(schema)
validator = Draft202012Validator(schema)

errors = sorted(validator.iter_errors(example), key=lambda e: list(e.path))
if errors:
    for e in errors:
        print("example.json:", list(e.path), e.message)
    sys.exit(1)
print("ok: example.json is valid")


def must_fail(label, mutate):
    doc = copy.deepcopy(example)
    mutate(doc)
    if validator.is_valid(doc):
        print(f"FAIL: negative control passed validation: {label}")
        sys.exit(1)
    print(f"ok: rejected {label}")


must_fail("constant set without convention_id",
          lambda d: d["stations"][0]["constant_sets"][0].pop("convention_id"))
must_fail("local convention without utc_offset_hours",
          lambda d: d["conventions"][1].pop("utc_offset_hours"))
must_fail("bad datestamp",
          lambda d: d["release"].__setitem__("datestamp", "2099-12-31"))
must_fail("phase of 360 degrees",
          lambda d: d["stations"][0]["constant_sets"][0]["constituents"][0].__setitem__("phase_deg", 360))
