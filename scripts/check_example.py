"""Check that every value in src/schema/example.json follows from the example's own constants.

check_schema.py checks that the example is valid. This script checks that it is plausible: each
water-level set's levels, months, hours, flags, uncertainties, method and control follow from its
constants, its record and the published rules (Data format, Method). It prints each mismatch and
exits 1 if there is any; with --all it prints every check.

Usage: python scripts/check_example.py [path to example.json] [--all]
"""
import json, math, re, sys
from datetime import date

SPEED = {"M2": 28.9841042, "S2": 30.0, "N2": 28.4397295, "K2": 30.0821373, "K1": 15.0410686,
         "O1": 13.9430356, "P1": 14.9589314, "Q1": 13.3986609, "T2": 29.9589333}
# Rayleigh pairs (candidate, the stronger constituent it must be separated from)
PAIRS = [("K2", "S2"), ("P1", "K1"), ("T2", "S2"), ("N2", "M2"), ("S2", "M2"), ("O1", "K1"), ("Q1", "O1")]
def rayleigh_hours(a, b): return 360.0 / abs(SPEED[a] - SPEED[b])
# Node factors f(N) (Schureman), and their largest value over a full nodal cycle: the 2020-2038
# LAT window holds one, so HAT and LAT reach these values.
NODE_FACTOR = {
    "M2": lambda n: 1.0004 - 0.0373 * math.cos(n) + 0.0002 * math.cos(2 * n),
    "N2": lambda n: 1.0004 - 0.0373 * math.cos(n) + 0.0002 * math.cos(2 * n),
    "K1": lambda n: 1.0060 + 0.1150 * math.cos(n) - 0.0088 * math.cos(2 * n) + 0.0006 * math.cos(3 * n),
    "O1": lambda n: 1.0089 + 0.1871 * math.cos(n) - 0.0147 * math.cos(2 * n) + 0.0014 * math.cos(3 * n),
    "Q1": lambda n: 1.0089 + 0.1871 * math.cos(n) - 0.0147 * math.cos(2 * n) + 0.0014 * math.cos(3 * n),
    "K2": lambda n: 1.0241 + 0.2863 * math.cos(n) + 0.0083 * math.cos(2 * n) - 0.0015 * math.cos(3 * n),
}
F_MAX = {k: max(f(math.radians(deg)) for deg in range(360)) for k, f in NODE_FACTOR.items()}
def reach(a): return sum(F_MAX.get(k, 1.0) * v for k, v in a.items())
def d(s): y, m, dd = map(int, s[:10].split("-")); return date(y, m, dd)
def months_between(s, e):
    a, b = d(s), d(e); return (b.year - a.year) * 12 + b.month - a.month + 1
def hours_between(s, e): return (d(e) - d(s)).days * 24
def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))
def amps(cs): return {c["name"]: c["amplitude_m"] for c in cs.get("constituents", [])}
def form(cs):
    a = amps(cs); semi = a.get("M2", 0) + a.get("S2", 0)
    return (a.get("K1", 0) + a.get("O1", 0)) / semi if semi else float("inf")
def tide_class(F): return 0 if F < 0.25 else 1 if F < 1.5 else 2 if F < 3.0 else 3
HIGH = {"mhw", "mhhw", "hat", "mhws", "mhwn"}; LOW = {"mlw", "mllw", "lat", "mlws", "mlwn", "mllws"}
MEAN = {"msl", "mtl", "dtl", "mw"}
# plausible tide class by region (country): semidiurnal coasts and mixed coasts
SEMI_COUNTRIES = {"NOR", "NLD", "DEU", "GBR", "SWE", "FIN", "USA-EAST"}

def audit(doc):
    rows = []
    def row(where, check, value, expected, ok): rows.append((where, check, str(value), str(expected), bool(ok)))
    stations = {s["station_id"]: s for s in doc["stations"] if s.get("status") == "active"}
    sets = {cs["set_id"]: (st, cs) for st in stations.values() for cs in st.get("constant_sets", [])}
    primaries = {}
    for sid, (st, cs) in sets.items():
        b = cs.get("datum", {}).get("basis", {})
        if any(v.get("kind") == "observed" and v.get("method") == "first_reduction" and "no_qualified_control" not in v.get("flags", [])
               for v in b.values()):
            primaries[sid] = (st, cs)
    for sid, (st, cs) in sets.items():
        if cs.get("quantity") != "water_level":
            continue
        where = sid
        rs = cs.get("record_span"); a = amps(cs); F = form(cs)
        # record hours
        if rs and rs.get("good_hours", 0) > 0:
            span_h = hours_between(rs["start"], rs["end"])
            row(where, "good_hours within the record span", rs["good_hours"], f"<= {span_h}", rs["good_hours"] <= span_h)
            L = rs["good_hours"]
            # Rayleigh: every candidate whose partner is present and the record is too short is dropped
            dropped = {x["name"]: x for x in cs.get("dropped_constituents", [])}
            present = set(a)
            for cand, part in PAIRS:
                need = rayleigh_hours(cand, part)
                if part in present and L < need:
                    ok = cand in dropped and cand not in present and dropped[cand].get("not_separable_from") == part
                    row(where, f"Rayleigh {cand}/{part} ({need/24:.1f} d)", f"{L/24:.0f} d; dropped: {sorted(dropped)}", f"{cand} dropped, not separable from {part}", ok)
                if cand in present and part in present and L < need:
                    row(where, f"Rayleigh {cand}/{part} both kept", f"{L/24:.0f} d", "not both kept", False)
            for name, x in dropped.items():
                if x.get("dropped_reason") == "rayleigh":
                    p = x.get("not_separable_from"); need = rayleigh_hours(name, p)
                    row(where, f"Rayleigh drop {name}/{p} is needed", f"{L/24:.0f} d", f"< {need/24:.1f} d", L < need)
            # qc reason text for short records names the dropped constituents
            for f in cs.get("qc_flags", []):
                if f["flag"] == "short_record":
                    m = re.match(r"([\d.]+) days", f["reason"])
                    if m:
                        row(where, "short_record reason states the good days", m.group(1), f"{L/24:.0f}", abs(float(m.group(1)) - L / 24) < 1.0)
                    for name in dropped:
                        if dropped[name].get("dropped_reason") == "rayleigh":
                            row(where, f"short_record reason names {name}", f["reason"][:70], f"mentions {name}", name in f["reason"])
        datum = cs.get("datum", {}); named = datum.get("named", {}); basis = datum.get("basis", {})
        z0 = datum.get("msl_offset_m"); tot = reach(a)
        # levels against the constants
        if z0 is not None and a and named:
            for k, v in named.items():
                kk = k[4:] if k.startswith("otc_") else k
                if kk in HIGH:
                    row(where, f"{k} within the predicted range", v, f"<= Z0 + sum(f_max amp) = {z0 + tot:.3f}", v <= z0 + tot + 0.02)
                    if kk == "hat" and basis.get(k, {}).get("kind") == "computed" and set(a) <= {"M2", "S2"}:
                        row(where, f"computed {k} equals the highest predicted level (two constituents align within 19 years)", v, f"{z0 + tot:.3f} +/- 0.02", abs(v - (z0 + tot)) <= 0.02)
                if kk in LOW:
                    row(where, f"{k} within the predicted range", v, f">= Z0 - sum(f_max amp) = {z0 - tot:.3f}", v >= z0 - tot - 0.02)
                if kk == "msl":
                    row(where, f"{k} near Z0", v, f"{z0} +/- 0.05", abs(v - z0) <= 0.05)
            hw = named.get("mhw", named.get("otc_mhw")); lw = named.get("mlw", named.get("otc_mlw"))
            if hw is not None and lw is not None and "M2" in a:
                row(where, "mean range MHW - MLW against 2 x M2", round(hw - lw, 3), f"{2*a['M2']:.2f} +/- 30%", abs((hw - lw) - 2 * a["M2"]) <= 0.3 * 2 * a["M2"])
            if "mhw" in named and "mlw" in named and "mtl" in named:
                row(where, "mtl = (mhw + mlw) / 2", named["mtl"], round((named["mhw"] + named["mlw"]) / 2, 3), abs(named["mtl"] - (named["mhw"] + named["mlw"]) / 2) <= 0.01)
            if "mhhw" in named and "mllw" in named and "dtl" in named:
                row(where, "dtl = (mhhw + mllw) / 2", named["dtl"], round((named["mhhw"] + named["mllw"]) / 2, 3), abs(named["dtl"] - (named["mhhw"] + named["mllw"]) / 2) <= 0.01)
        # a chart-datum zero sits near the lowest tides: the predicted lows do not fall far below it
        if datum.get("zero") == "chart_datum" and z0 is not None and a:
            tot_mean = sum(a.values())
            limit = -1.0 if datum.get("zero_name") == "MLLW" else -0.3   # MLLW sits above the lowest tides by design
            row(where, "chart-datum zero at or below the lowest predicted tide, and not far below it", f"Z0 - sum(f_max amp) = {z0 - tot:.2f}", f">= {limit} m ({datum.get('zero_name')})", z0 - tot >= limit)
        # cut-off for flatline sets
        for f in cs.get("qc_flags", []):
            m = re.search(r"dries below ([\d.]+) m", f.get("reason", ""))
            if m and z0 is not None and a:
                cut = float(m.group(1))
                row(where, "cut-off is reached by the predicted lows", cut, f"> Z0 - sum(amp) = {z0 - tot:.2f}", cut > z0 - tot)
                row(where, "cut-off is below the predicted highs", cut, f"< Z0 = {z0}", cut < z0)
        # each basis
        for k, b in basis.items():
            w = f"{where} {k}"
            ds = b.get("data_span")
            if ds and rs and rs.get("start"):
                row(w, "data_span inside record_span", f"{ds['start']}..{ds['end']}", f"{rs['start'][:10]}..{rs['end'][:10]}",
                    rs["start"][:10] <= ds["start"] and ds["end"] < rs["end"][:10])
            if ds:
                cal = months_between(ds["start"], ds["end"])
                gap_months = 0
                for iss in cs.get("record_issues", []):
                    if iss["issue"] == "gaps" and iss.get("end"):
                        for y in range(1900, 2100):
                            for mo in range(1, 13):
                                first = date(y, mo, 1)
                                last = date(y + (mo == 12), mo % 12 + 1, 1)
                                if d(iss["start"]) < last and first <= d(iss["end"]) and d(ds["start"]) <= first <= d(ds["end"]):
                                    gap_months += 1
                expected_max = cal - gap_months
                row(w, "months against calendar months and recorded gaps", ds["months"], f"<= {expected_max} ({cal} calendar, {gap_months} in recorded gaps)", ds["months"] <= expected_max)
                if b.get("kind") == "observed" and ds["months"] < cal:
                    row(w, "months skipped: the level carries gaps", f"{ds['months']} of {cal} months; flags {b.get('flags', [])}", "contains gaps",
                        "gaps" in b.get("flags", []))
                if b.get("kind") == "observed" and ds["months"] == cal and rs and rs.get("good_hours"):
                    # every month counted needs no gap: the record's missing hours inside the data span are tiny
                    span_h = hours_between(rs["start"], rs["end"]); missing = span_h - rs["good_hours"]
                    data_h = hours_between(ds["start"], ds["end"])
                    outside_h = span_h - data_h - 24
                    row(w, "all months counted: missing hours fit outside the data or in 3-hour fills",
                        f"{missing} h missing; {outside_h} h of record outside the data", "missing <= outside + 1% of the data",
                        missing <= max(outside_h, 0) + 0.01 * data_h)
            # flags
            fl = b.get("flags", [])
            if b.get("kind") == "observed" and ds and any(i["issue"] == "datum_step" for i in cs.get("record_issues", [])):
                row(w, "a level from a record split by a datum step carries segment", fl, "contains segment", "segment" in fl)
            if "segment" in fl:
                steps = sorted(i["start"][:10] for i in cs.get("record_issues", []) if i["issue"] == "datum_step")
                one = bool(steps) and ds is not None and all(not (ds["start"] < st_ <= ds["end"]) for st_ in steps)
                row(w, "segment: the level is reduced from one segment of a record split by a datum step", f"steps {steps}; data {ds and ds['start']}..{ds and ds['end']}",
                    "a datum step, with no step inside data_span", one)
            if "time_base_unverified" in fl or "time_base_disputed" in fl:
                code = cs.get("time_base", {}).get("code")
                want = "unverified" if "time_base_unverified" in fl else "disputed"
                row(w, "time-base flag matches the set's time base", code, want, code == want)
            if "truncated_lows" in fl:
                row(w, "truncated_lows set records a flatline", [i["issue"] for i in cs.get("record_issues", [])], "flatline", any(i["issue"] == "flatline" for i in cs.get("record_issues", [])))
            # uncertainty against record length
            if b.get("kind") == "observed" and ds and "uncertainty_m" in b:
                n = ds["months"]; u = b["uncertainty_m"]; meth = b["method"]
                if meth == "first_reduction" and n >= 216 and "no_qualified_control" not in fl:
                    lo, hi = 0.002, 0.01
                elif meth == "first_reduction" and n >= 216:
                    lo, hi = 0.002, 0.012
                elif meth == "first_reduction":
                    lo, hi = 0.04 * (12 / n) ** 0.5 * 0.6, 0.115 / n ** 0.5 * 1.6
                else:
                    lo, hi = 0.004, 0.05 / n ** 0.3 * 1.6
                row(w, f"uncertainty for {n} months ({meth})", u, f"{lo:.3f}..{hi:.3f}", lo - 1e-9 <= u <= hi + 1e-9)
            # method against form factor and control
            if b.get("method") in ("standard", "modified_range_ratio", "direct"):
                ctl = b["control"]; cst, ccs = sets.get(ctl.get("set_id"), (None, None))
                if ccs is None:
                    row(w, "control set exists", ctl.get("set_id"), "a set in the release", False); continue
                dist = km(st, cst)
                row(w, "control within 250 km", f"{dist:.0f} km", "<= 250 km", dist <= 250)
                row(w, "control has the same tide class", f"F {F:.2f} vs {form(ccs):.2f}", "same class", tide_class(F) == tide_class(form(ccs)))
                cn = ccs.get("datum", {}).get("named", {}); cb = ccs.get("datum", {}).get("basis", {})
                if b["method"] == "direct":
                    if "M2" in a and "M2" in amps(ccs):
                        r = a["M2"] / amps(ccs)["M2"]
                        row(w, "M2 ratio with the control (direct method)", f"{r:.2f}", "0.5..2.0", 0.5 <= r <= 2.0)
                else:
                    if all(x in named for x in ("mhw", "mlw")) and all(x in cn for x in ("mhw", "mlw")):
                        r = (named["mhw"] - named["mlw"]) / (cn["mhw"] - cn["mlw"]); src = "Mn from the levels"
                    else:
                        r = a["M2"] / amps(ccs)["M2"]; src = "Mn from 2 x M2"
                    row(w, f"mean-range ratio with the control ({src})", f"{r:.2f}", "0.5..2.0", 0.5 <= r <= 2.0)
                need = {"direct": {"mhw", "mhhw"},
                        "standard": {"mtl", "msl", "mhw", "mlw", "mhhw", "mllw"} | ({"dtl"} if "dtl" in named else set()),
                        "modified_range_ratio": {"mtl", "dtl", "msl", "mhw", "mlw", "mhhw", "mllw"}}[b["method"]]
                have = {x for x, v in cb.items() if v.get("kind") == "observed" and v.get("method") == "first_reduction"
                        and "no_qualified_control" not in v.get("flags", []) and v.get("data_span", {}).get("months", 0) >= 216}
                row(w, "control has a 19-year determination of every level the method uses", sorted(have & need), sorted(need), need <= have)
                crs = ccs.get("record_span")
                row(w, "control record overlaps the data", f"{ds['start']}..{ds['end']} vs {crs['start'][:10]}..{crs['end'][:10]}", "overlap",
                    ds["start"] < crs["end"][:10] and ds["end"] >= crs["start"][:10])
                if b["method"] != "direct" and st["country"] != "USA":
                    want = "standard" if 0.25 <= F <= 3.0 else "modified_range_ratio"
                    row(w, "method follows the form factor", f"{b['method']} (F {F:.2f})", want, b["method"] == want)
            if "no_qualified_control" in fl and ds:
                qual = []
                for psid, (pst, pcs) in primaries.items():
                    if psid == sid: continue
                    prs = pcs.get("record_span")
                    pb = pcs.get("datum", {}).get("basis", {})
                    truncated_primary = not any(k in pb for k in ("mlw", "mllw", "msl", "mtl"))
                    if truncated_primary and "truncated_lows" not in fl:
                        continue
                    ratio_ok = "M2" in a and "M2" in amps(pcs) and 0.5 <= a["M2"] / amps(pcs)["M2"] <= 2.0
                    if km(st, pst) <= 250 and tide_class(F) == tide_class(form(pcs)) and ratio_ok and ds["start"] < prs["end"][:10] and ds["end"] >= prs["start"][:10]:
                        qual.append(psid)
                row(w, "no_qualified_control: no qualifying control (250 km, class, overlap)", qual, "none", not qual)
        # tide type against the region
        c = st["country"] + ("-EAST" if st["country"] == "USA" and st["lon"] > -100 else "")
        if a and c in SEMI_COUNTRIES:
            row(where, f"tide type plausible for {st['country']}", f"F {F:.2f}", "semidiurnal (F < 0.25)", F < 0.25)
        if a and st["country"] == "CAN" and st["lon"] < -120:
            row(where, "tide type plausible for British Columbia", f"F {F:.2f}", "mixed (0.25 <= F < 3)", 0.25 <= F < 3)
    # recommended set: most good data in the 19-year span 2002-2020; recent before old
    for st in stations.values():
        cands = [cs for cs in st.get("constant_sets", []) if cs.get("qc_status") != "no_constants" and cs.get("record_span") and cs.get("quantity") == "water_level"]
        if len(cands) < 2:
            continue
        def in_span(cs):
            rs = cs["record_span"]; a0, a1 = max(d(rs["start"]), date(2002, 1, 1)), min(d(rs["end"]), date(2021, 1, 1))
            span = (d(rs["end"]) - d(rs["start"])).days
            frac = max(0, (a1 - a0).days) / span if span else 0
            return (rs["good_hours"] * frac, rs["end"])
        best = max(cands, key=in_span)["set_id"]
        row(st["station_id"], "recommended set: most good data in 2002-2020, then most recent", st.get("recommended_set_id"), best, st.get("recommended_set_id") == best)
    return rows

def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    path = args[0] if args else "src/schema/example.json"
    rows = audit(json.load(open(path)))
    bad = [r for r in rows if not r[4]]
    for r in rows if "--all" in argv else bad:
        print(("ok" if r[4] else "MISMATCH") + " | " + " | ".join(r[:4]))
    print(f"{'ok' if not bad else 'FAIL'}: {len(rows)} checks of example.json, {len(bad)} mismatches")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
