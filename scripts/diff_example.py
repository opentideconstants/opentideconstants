"""List every change to src/schema/example.json against a git revision, keyed by id.

These lists are matched by key: stations by station_id, constant sets by set_id, constituents
and dropped constituents by name, licences by licence_id, conventions by convention_id, and
astronomical tables by astro_table_id. Every other list is matched by position. Each change prints
on its own line, with the path of the item it is in:

  path: old -> new                 a changed value; values of different JSON types (true and 1,
                                   1 and 1.0) count as changed, and both are printed as JSON
  path[key]: added / removed       an item only in the new or only in the old list
  path[key]: duplicate (n items)   a key that n items share in the old or new list; the first of
                                   them is the one compared
  path[#i]: item without key       an item of a keyed list that is not an object with the key
  path: reordered                  the items both lists hold are in a different order
  path: n items -> m items         a list matched by position whose length changed (its common
                                   items are compared)

The base revision must be in the local git history (in a shallow CI checkout, fetch it first,
for example with actions/checkout's fetch-depth: 0). If git cannot read it, the script prints
git's own error and exits 1.

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


def same(a, b):
    return type(a) is type(b) and a == b


def keyed(items, key, path, side, out):
    """The items of a keyed list by key (first wins), reporting keyless and duplicate items."""
    by_key, count = {}, {}
    for i, x in enumerate(items):
        if not isinstance(x, dict) or key not in x:
            out.append(f"{path}[#{i}]: item without key {key} ({side})")
            continue
        count[x[key]] = count.get(x[key], 0) + 1
        by_key.setdefault(x[key], x)
    for k, n in count.items():
        if n > 1:
            out.append(f"{path}[{k}]: duplicate ({n} items, {side})")
    return by_key


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
        o = keyed(old, key, path, "old", out)
        n = keyed(new, key, path, "new", out)
        for k in list(o) + [k for k in n if k not in o]:
            p = f"{path}[{k}]"
            if k not in n:
                out.append(f"{p}: removed")
            elif k not in o:
                out.append(f"{p}: added")
            else:
                diff(o[k], n[k], p, None, out)
        common = [k for k in o if k in n]
        if common != [k for k in n if k in o]:
            out.append(f"{path}: reordered")
    elif isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            out.append(f"{path}: {len(old)} items -> {len(new)} items")
        for i, (a, b) in enumerate(zip(old, new)):
            diff(a, b, f"{path}[{i}]", None, out)
    elif not same(old, new):
        out.append(f"{path}: {show(old)} -> {show(new)}")


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    base, path = argv[0], argv[1] if len(argv) > 1 else "src/schema/example.json"
    got = subprocess.run(["git", "show", f"{base}:src/schema/example.json"], capture_output=True, text=True)
    if got.returncode != 0:
        print(f"diff_example.py: git could not read {base}:src/schema/example.json", file=sys.stderr)
        print(got.stderr.strip(), file=sys.stderr)
        return 1
    old = json.loads(got.stdout)
    new = json.load(open(path))
    out = []
    diff(old, new, "", None, out)
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
