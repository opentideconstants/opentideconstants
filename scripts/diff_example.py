"""List every change to src/schema/example.json against a git revision, keyed by id.

Lists are matched by their key (stations by station_id, constant sets by set_id, constituents and
dropped constituents by name, licences by licence_id, conventions by convention_id, astronomical
tables by astro_table_id, record issues and qc flags by position), so a change prints with the
path of the item it is in. Every changed value prints on its own line as "path: old -> new"; an
added or removed item prints once, as "path: added" or "path: removed".

Usage: python scripts/diff_example.py BASE_REVISION [path to example.json]
"""
import json
import subprocess
import sys

KEYS = {"stations": "station_id", "constant_sets": "set_id", "constituents": "name",
        "dropped_constituents": "name", "licences": "licence_id", "conventions": "convention_id",
        "astro_tables": "astro_table_id"}


def show(value):
    return json.dumps(value, ensure_ascii=False)


def diff(old, new, path, field, out):
    if isinstance(old, dict) and isinstance(new, dict):
        for k in list(old) + [k for k in new if k not in old]:
            p = f"{path}.{k}" if path else k
            if k not in new:
                out.append(f"{p}: removed")
            elif k not in old:
                out.append(f"{p}: added")
            else:
                diff(old[k], new[k], p, k, out)
    elif isinstance(old, list) and isinstance(new, list) and field in KEYS:
        key = KEYS[field]
        o = {x.get(key): x for x in old if isinstance(x, dict)}
        n = {x.get(key): x for x in new if isinstance(x, dict)}
        for k in list(o) + [k for k in n if k not in o]:
            p = f"{path}[{k}]"
            if k not in n:
                out.append(f"{p}: removed")
            elif k not in o:
                out.append(f"{p}: added")
            else:
                diff(o[k], n[k], p, None, out)
    elif isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        for i, (a, b) in enumerate(zip(old, new)):
            diff(a, b, f"{path}[{i}]", None, out)
    elif old != new:
        out.append(f"{path}: {show(old)} -> {show(new)}")


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    base, path = argv[0], argv[1] if len(argv) > 1 else "src/schema/example.json"
    old = json.loads(subprocess.run(["git", "show", f"{base}:src/schema/example.json"],
                                    capture_output=True, text=True, check=True).stdout)
    new = json.load(open(path))
    out = []
    diff(old, new, "", None, out)
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
