"""Check that the datum values of src/schema/example.json agree with the example's own constants.

check_schema.py checks that the example is valid. This script checks that its datum values are
plausible. For each active station, and each water-level constant set with constituents, it checks:

 1. Hours: record_span.good_hours is no more than the hours from start to end.
 2. Rayleigh separation: where a set keeps a constituent's stronger partner and the record has
    fewer good hours than the pair needs (K2/S2 and P1/K1 4383 h, T2/S2 8766 h, N2/M2 662 h, S2/M2
    355 h, O1/K1 328 h, Q1/O1 662 h), the weaker one is dropped, not kept, with not_separable_from
    set to the partner. Each Rayleigh drop is needed, and a short_record reason states the good
    days and names each dropped constituent.
 3. Levels in datum.named against the constants, using each amplitude at its largest Schureman
    node factor (M2 and N2 1.0379, K1 1.1128, O1 and Q1 1.1827, K2 1.3172, others 1):
    - every high level (mhw, mhhw, hat, mhws, mhwn and otc_ forms) is no more than Z0 plus the
      summed amplitudes, and every low level (mlw, mllw, lat, mlws, mlwn, mllws and otc_ forms)
      no less than Z0 minus them (0.02 m slack);
    - msl is within 0.05 m of Z0 (msl_offset_m);
    - a computed hat on a set with only M2 and S2 equals Z0 plus the summed amplitudes (0.02 m);
    - mhw - mlw is within 30% of 2 x M2; mtl = (mhw + mlw) / 2 and dtl = (mhhw + mllw) / 2
      (0.01 m);
    - the levels are in order, among the unprefixed keys and among the otc_ keys:
      hat >= mhhw >= mhw > msl and mtl > mlw >= mllw >= lat, hat >= every high and lat <= every low.
 4. Chart-datum zero: when datum.zero is chart_datum, the lowest predicted tide (Z0 minus the
    summed amplitudes) is between -0.1 m and 0.5 m above the zero; for an MLLW zero, which sits
    above the lowest tides by design, it is between -1.0 m and 0 m.
 5. Flat-line cut-off: a "dries below X m" reason has X above the lowest predicted tide and below Z0.
 6. Each observed basis:
    - data_span is inside record_span;
    - data_span.months is no more than the calendar months of data_span minus the months a
      recorded gaps issue touches;
    - when months were skipped (months below the calendar months), the level carries gaps;
    - good hours supply the counted months: at least 672 h a month for a first reduction (a
      counted month has no gap) and 576 h for a comparison (24 common tidal days);
    - when every month counts, the record's missing hours fit outside the data plus 0.1% of the
      data hours for a first reduction (gaps of 3 hours or less only), or plus 132 h a month for a
      comparison (a month counts with 24 of about 29.5 common tidal days);
    - segment: a level from a record with a datum step carries segment, and with segment no step
      falls inside data_span;
    - time_base_unverified or time_base_disputed matches the set's time_base code;
    - truncated_lows: the set records a flatline issue;
    - uncertainty_m is in a range for the method and month count.
 7. Each comparison (standard, modified_range_ratio, direct): the control set exists, is within
    250 km, has the same tide class (form factor F = (K1 + O1) / (M2 + S2): below 0.25, 0.25 to
    below 1.5, 1.5 to 3.0, above 3.0), a range ratio of 0.5 to 2.0 (the M2 ratio for the direct
    method; Mn from mhw - mlw, else 2 x M2, otherwise), a record that overlaps the data, and an
    observed 19-year first reduction of every level the method uses (direct: mhw, mhhw;
    standard: mtl, msl, mhw, mlw, mhhw, mllw, and dtl when the set writes dtl; modified range
    ratio: those and dtl). Outside the US, standard is used for 0.25 <= F <= 3.0 and
    modified_range_ratio otherwise.
 8. no_qualified_control: no 19-year first reduction elsewhere qualifies as its control (250 km,
    same class, M2 ratio 0.5 to 2.0, record overlap; a primary of only mhw and mhhw counts only for
    a truncated level).
 9. Tide type: F < 0.25 at stations in NOR, NLD, DEU, GBR, SWE, FIN and on the US East Coast, and
    0.25 <= F < 3 in British Columbia (CAN, west of 120 W).
10. Subordinate offsets: for a ratio (R) subordinate station, each level of
    subordinate_offsets.datum equals the ratio times the reference's level (the otc_ level for a
    computed one), within 0.005 m: height_offset_high for highs and height_offset_low for lows.
11. Recommended set: for each station with water-level sets, recommended_set_id is the set the
    published rule picks among those with constants: official before gauge fit before model; then
    the most good hours inside 2002-2020; then the most recent end.

It prints each mismatch, and exits 1 on any mismatch or when fewer than MIN_CHECKS checks run
(a guard against a check that silently stops applying). With --all it prints every check.

Usage: python scripts/check_example.py [path to example.json] [--all]
"""
import json
import math
import re
import sys
from datetime import date

MIN_CHECKS = 420

SPEED = {"M2": 28.9841042, "S2": 30.0, "N2": 28.4397295, "K2": 30.0821373, "K1": 15.0410686,
         "O1": 13.9430356, "P1": 14.9589314, "Q1": 13.3986609, "T2": 29.9589333}
# (weaker constituent, the stronger one it must be separated from)
PAIRS = [("K2", "S2"), ("P1", "K1"), ("T2", "S2"), ("N2", "M2"), ("S2", "M2"), ("O1", "K1"), ("Q1", "O1")]
NODE_FACTOR = {
    "M2": lambda n: 1.0004 - 0.0373 * math.cos(n) + 0.0002 * math.cos(2 * n),
    "N2": lambda n: 1.0004 - 0.0373 * math.cos(n) + 0.0002 * math.cos(2 * n),
    "K1": lambda n: 1.0060 + 0.1150 * math.cos(n) - 0.0088 * math.cos(2 * n) + 0.0006 * math.cos(3 * n),
    "O1": lambda n: 1.0089 + 0.1871 * math.cos(n) - 0.0147 * math.cos(2 * n) + 0.0014 * math.cos(3 * n),
    "Q1": lambda n: 1.0089 + 0.1871 * math.cos(n) - 0.0147 * math.cos(2 * n) + 0.0014 * math.cos(3 * n),
    "K2": lambda n: 1.0241 + 0.2863 * math.cos(n) + 0.0083 * math.cos(2 * n) - 0.0015 * math.cos(3 * n),
}
F_MAX = {k: max(f(math.radians(deg)) for deg in range(360)) for k, f in NODE_FACTOR.items()}
HIGH = {"mhw", "mhhw", "hat", "mhws", "mhwn"}
LOW = {"mlw", "mllw", "lat", "mlws", "mlwn", "mllws"}
ORDER = [("hat", "mhhw", False), ("mhhw", "mhw", False), ("mhw", "msl", True), ("mhw", "mtl", True),
         ("msl", "mlw", True), ("mtl", "mlw", True), ("mlw", "mllw", False), ("mllw", "lat", False)]
SEMIDIURNAL = {"NOR", "NLD", "DEU", "GBR", "SWE", "FIN"}
SOURCE_RANK = {"official": 0, "gauge": 1, "model": 2}


def rayleigh_hours(a, b):
    return 360.0 / abs(SPEED[a] - SPEED[b])


def reach(amp):
    return sum(F_MAX.get(k, 1.0) * v for k, v in amp.items())


def day(s):
    y, m, dd = map(int, s[:10].split("-"))
    return date(y, m, dd)


def months_between(s, e):
    a, b = day(s), day(e)
    return (b.year - a.year) * 12 + b.month - a.month + 1


def hours_between(s, e):
    return (day(e) - day(s)).days * 24


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def amps(cs):
    return {c["name"]: c["amplitude_m"] for c in cs.get("constituents", []) if "amplitude_m" in c}


def form(cs):
    a = amps(cs)
    semi = a.get("M2", 0) + a.get("S2", 0)
    return (a.get("K1", 0) + a.get("O1", 0)) / semi if semi else float("inf")


def tide_class(f):
    return 0 if f < 0.25 else 1 if f < 1.5 else 2 if f <= 3.0 else 3


def gap_months(cs, ds):
    """Months of data_span that a recorded gaps issue touches."""
    n = 0
    for issue in cs.get("record_issues", []):
        if issue["issue"] != "gaps" or not issue.get("end"):
            continue
        y, m = day(ds["start"]).year, day(ds["start"]).month
        while date(y, m, 1) <= day(ds["end"]):
            first, nxt = date(y, m, 1), date(y + (m == 12), m % 12 + 1, 1)
            if day(issue["start"]) < nxt and first <= day(issue["end"]):
                n += 1
            y, m = y + (m == 12), m % 12 + 1
    return n


def is_primary(b):
    return (b.get("kind") == "observed" and b.get("method") == "first_reduction"
            and "no_qualified_control" not in b.get("flags", []) and b.get("data_span", {}).get("months", 0) >= 216)


def audit(doc):
    rows = []

    def row(where, check, value, expected, ok):
        rows.append((where, check, str(value), str(expected), bool(ok)))

    stations = {s["station_id"]: s for s in doc["stations"] if s.get("status") == "active"}
    sets = {cs["set_id"]: (st, cs) for st in stations.values() for cs in st.get("constant_sets", [])}
    primaries = {sid: (st, cs) for sid, (st, cs) in sets.items()
                 if any(is_primary(v) for v in cs.get("datum", {}).get("basis", {}).values())}
    for sid, (st, cs) in sets.items():
        if cs.get("quantity") != "water_level":
            continue
        a = amps(cs)
        if not a:
            continue
        rs = cs.get("record_span")
        f = form(cs)
        datum = cs.get("datum", {})
        named, basis = datum.get("named", {}), datum.get("basis", {})
        z0 = datum.get("msl_offset_m")
        tot = reach(a)
        # 1-2. hours and Rayleigh
        if rs and rs.get("good_hours", 0) > 0:
            span_h = hours_between(rs["start"], rs["end"])
            row(sid, "good_hours within the record span", rs["good_hours"], f"<= {span_h}", rs["good_hours"] <= span_h)
            good = rs["good_hours"]
            dropped = {x["name"]: x for x in cs.get("dropped_constituents", [])}
            for weak, strong in PAIRS:
                need = rayleigh_hours(weak, strong)
                if strong in a and good < need:
                    ok = weak in dropped and weak not in a and dropped[weak].get("not_separable_from") == strong
                    row(sid, f"Rayleigh {weak}/{strong} ({need:.0f} h)", f"{good} h; dropped {sorted(dropped)}", f"{weak} dropped, not separable from {strong}", ok)
            for name, x in dropped.items():
                if x.get("dropped_reason") == "rayleigh":
                    need = rayleigh_hours(name, x["not_separable_from"])
                    row(sid, f"Rayleigh drop {name} is needed", f"{good} h", f"< {need:.0f} h", good < need)
            for flag in cs.get("qc_flags", []):
                if flag["flag"] == "short_record":
                    m = re.match(r"([\d.]+) days", flag["reason"])
                    row(sid, "short_record reason states the good days", m and m.group(1), f"{good / 24:.0f}", bool(m) and abs(float(m.group(1)) - good / 24) < 1.0)
                    for name, x in dropped.items():
                        if x.get("dropped_reason") == "rayleigh":
                            row(sid, f"short_record reason names {name}", flag["reason"][:60], f"mentions {name}", name in flag["reason"])
        # 3. levels
        if z0 is not None and named:
            for k, v in named.items():
                kk = k[4:] if k.startswith("otc_") else k
                if kk in HIGH:
                    row(sid, f"{k} within the predicted range", v, f"<= Z0 + amplitudes = {z0 + tot:.3f}", v <= z0 + tot + 0.02)
                    if kk == "hat" and basis.get(k, {}).get("kind") == "computed" and set(a) <= {"M2", "S2"}:
                        row(sid, f"computed {k} equals the highest predicted level", v, f"{z0 + tot:.3f} +/- 0.02", abs(v - (z0 + tot)) <= 0.02)
                if kk in LOW:
                    row(sid, f"{k} within the predicted range", v, f">= Z0 - amplitudes = {z0 - tot:.3f}", v >= z0 - tot - 0.02)
                if kk == "msl":
                    row(sid, f"{k} near Z0", v, f"{z0} +/- 0.05", abs(v - z0) <= 0.05)
            for prefix in ("", "otc_"):
                lv = {k[len(prefix):]: v for k, v in named.items() if k.startswith(prefix) and (prefix or not k.startswith("otc_"))}
                if "mhw" in lv and "mlw" in lv and "M2" in a:
                    row(sid, f"{prefix}mhw - {prefix}mlw against 2 x M2", round(lv["mhw"] - lv["mlw"], 3), f"{2 * a['M2']:.2f} +/- 30%", abs((lv["mhw"] - lv["mlw"]) - 2 * a["M2"]) <= 0.6 * a["M2"])
                if {"mhw", "mlw", "mtl"} <= set(lv):
                    row(sid, f"{prefix}mtl = (mhw + mlw) / 2", lv["mtl"], round((lv["mhw"] + lv["mlw"]) / 2, 3), abs(lv["mtl"] - (lv["mhw"] + lv["mlw"]) / 2) <= 0.01)
                if {"mhhw", "mllw", "dtl"} <= set(lv):
                    row(sid, f"{prefix}dtl = (mhhw + mllw) / 2", lv["dtl"], round((lv["mhhw"] + lv["mllw"]) / 2, 3), abs(lv["dtl"] - (lv["mhhw"] + lv["mllw"]) / 2) <= 0.01)
                for hi, lo, strict in ORDER:
                    if hi in lv and lo in lv:
                        row(sid, f"{prefix}{hi} {'>' if strict else '>='} {prefix}{lo}", f"{lv[hi]} vs {lv[lo]}", "in order", lv[hi] > lv[lo] if strict else lv[hi] >= lv[lo])
                if "hat" in lv:
                    for k in set(lv) & HIGH - {"hat"}:
                        row(sid, f"{prefix}hat >= {prefix}{k}", f"{lv['hat']} vs {lv[k]}", "in order", lv["hat"] >= lv[k])
                if "lat" in lv:
                    for k in set(lv) & LOW - {"lat"}:
                        row(sid, f"{prefix}lat <= {prefix}{k}", f"{lv['lat']} vs {lv[k]}", "in order", lv["lat"] <= lv[k])
        # 4. chart-datum zero
        if datum.get("zero") == "chart_datum" and z0 is not None:
            lowest = z0 - tot
            lo, hi = (-1.0, 0.0) if datum.get("zero_name") == "MLLW" else (-0.1, 0.5)
            row(sid, "lowest predicted tide above the chart-datum zero", f"{lowest:.2f} m ({datum.get('zero_name')})", f"{lo} .. {hi} m", lo <= lowest <= hi)
        # 5. cut-off
        for flag in cs.get("qc_flags", []):
            m = re.search(r"dries below ([\d.]+) m", flag.get("reason", ""))
            if m and z0 is not None:
                cut = float(m.group(1))
                row(sid, "cut-off between the lowest predicted tide and Z0", cut, f"{z0 - tot:.2f} .. {z0}", z0 - tot < cut < z0)
        # 6-8. each observed basis
        for k, b in basis.items():
            if b.get("kind") != "observed":
                continue
            w = f"{sid} {k}"
            ds = b.get("data_span")
            fl = b.get("flags", [])
            if not ds:
                continue
            if rs and rs.get("start"):
                row(w, "data_span inside record_span", f"{ds['start']}..{ds['end']}", f"{rs['start'][:10]}..{rs['end'][:10]}",
                    rs["start"][:10] <= ds["start"] and ds["end"] < rs["end"][:10])
            cal = months_between(ds["start"], ds["end"])
            gm = gap_months(cs, ds)
            row(w, "months against calendar months and recorded gaps", ds["months"], f"<= {cal - gm} ({cal} calendar, {gm} gap)", ds["months"] <= cal - gm)
            if ds["months"] < cal:
                row(w, "months skipped: the level carries gaps", f"{ds['months']} of {cal}; flags {fl}", "contains gaps", "gaps" in fl)
            first = b.get("method") == "first_reduction"
            if rs and rs.get("good_hours"):
                per = 672 if first else 576
                row(w, "good hours supply the counted months", rs["good_hours"], f">= {ds['months']} x {per} h", rs["good_hours"] >= ds["months"] * per)
                if ds["months"] == cal:
                    span_h = hours_between(rs["start"], rs["end"])
                    missing = span_h - rs["good_hours"]
                    data_h = hours_between(ds["start"], ds["end"]) + 24
                    outside = max(span_h - data_h, 0)
                    allow = outside + (0.001 * data_h if first else 132 * cal)
                    row(w, "every month counts: missing hours fit outside the data and the month rule", f"{missing} h missing, {outside} h outside",
                        f"<= {allow:.0f} h", missing <= allow)
            steps = sorted(i["start"][:10] for i in cs.get("record_issues", []) if i["issue"] == "datum_step")
            if steps:
                row(w, "a level from a record with a datum step carries segment", fl, "contains segment", "segment" in fl)
            if "segment" in fl:
                row(w, "segment: no datum step inside data_span", f"steps {steps}", f"none in {ds['start']}..{ds['end']}",
                    bool(steps) and not any(ds["start"] < s <= ds["end"] for s in steps))
            for tb in ("unverified", "disputed"):
                if f"time_base_{tb}" in fl:
                    row(w, f"time_base_{tb} matches the set", cs.get("time_base", {}).get("code"), tb, cs.get("time_base", {}).get("code") == tb)
            if "truncated_lows" in fl:
                row(w, "truncated_lows set records a flatline", [i["issue"] for i in cs.get("record_issues", [])], "flatline",
                    any(i["issue"] == "flatline" for i in cs.get("record_issues", [])))
            if "uncertainty_m" in b:
                n, u = ds["months"], b["uncertainty_m"]
                if first and n >= 216:
                    lo, hi = 0.002, 0.012
                elif first:
                    lo, hi = 0.024 * (12 / n) ** 0.5, 0.184 / n ** 0.5
                else:
                    lo, hi = 0.004, 0.08 / n ** 0.3
                row(w, f"uncertainty for {n} months ({b['method']})", u, f"{lo:.3f}..{hi:.3f}", lo - 1e-9 <= u <= hi + 1e-9)
            if b.get("method") in ("standard", "modified_range_ratio", "direct"):
                ctl = b["control"]
                cst, ccs = sets.get(ctl.get("set_id"), (None, None))
                row(w, "control set exists", ctl.get("set_id"), "a set in the release", ccs is not None)
                if ccs is None:
                    continue
                dist = km(st, cst)
                row(w, "control within 250 km", f"{dist:.0f} km", "<= 250 km", dist <= 250)
                row(w, "control has the same tide class", f"F {f:.2f} vs {form(ccs):.2f}", "same class", tide_class(f) == tide_class(form(ccs)))
                cn, cb = ccs.get("datum", {}).get("named", {}), ccs.get("datum", {}).get("basis", {})
                if b["method"] == "direct":
                    r, how = a["M2"] / amps(ccs)["M2"], "M2 ratio"
                elif {"mhw", "mlw"} <= set(named) and {"mhw", "mlw"} <= set(cn):
                    r, how = (named["mhw"] - named["mlw"]) / (cn["mhw"] - cn["mlw"]), "Mn ratio"
                else:
                    r, how = a["M2"] / amps(ccs)["M2"], "Mn ratio from 2 x M2"
                row(w, f"control range ({how})", f"{r:.2f}", "0.5..2.0", 0.5 <= r <= 2.0)
                crs = ccs.get("record_span")
                row(w, "control record overlaps the data", f"{crs['start'][:10]}..{crs['end'][:10]}", f"overlaps {ds['start']}..{ds['end']}",
                    ds["start"] < crs["end"][:10] and ds["end"] >= crs["start"][:10])
                need = {"direct": {"mhw", "mhhw"},
                        "standard": {"mtl", "msl", "mhw", "mlw", "mhhw", "mllw"} | ({"dtl"} if "dtl" in named else set()),
                        "modified_range_ratio": {"mtl", "dtl", "msl", "mhw", "mlw", "mhhw", "mllw"}}[b["method"]]
                have = {x for x, v in cb.items() if is_primary(v)}
                row(w, "control has a 19-year determination of every level the method uses", sorted(have & need), sorted(need), need <= have)
                if b["method"] != "direct" and st["country"] != "USA":
                    want = "standard" if 0.25 <= f <= 3.0 else "modified_range_ratio"
                    row(w, "method follows the form factor", f"{b['method']} (F {f:.2f})", want, b["method"] == want)
            if "no_qualified_control" in fl:
                qual = []
                for psid, (pst, pcs) in primaries.items():
                    if psid == sid:
                        continue
                    pb = pcs.get("datum", {}).get("basis", {})
                    if not any(x in pb for x in ("mlw", "mllw", "msl", "mtl")) and "truncated_lows" not in fl:
                        continue
                    prs = pcs.get("record_span")
                    ratio = a.get("M2", 0) / amps(pcs).get("M2", 1)
                    if (km(st, pst) <= 250 and tide_class(f) == tide_class(form(pcs)) and 0.5 <= ratio <= 2.0
                            and ds["start"] < prs["end"][:10] and ds["end"] >= prs["start"][:10]):
                        qual.append(psid)
                row(w, "no_qualified_control: no 19-year control qualifies", qual, "none", not qual)
        # 9. tide type
        if st["country"] in SEMIDIURNAL or (st["country"] == "USA" and st["lon"] > -100):
            row(sid, f"tide type plausible for {st['country']}", f"F {f:.2f}", "F < 0.25", f < 0.25)
        if st["country"] == "CAN" and st["lon"] < -120:
            row(sid, "tide type plausible for British Columbia", f"F {f:.2f}", "0.25 <= F < 3", 0.25 <= f < 3)
    # 10. subordinate offsets
    for st in stations.values():
        so = st.get("subordinate_offsets", {})
        if "datum" not in so or so.get("height_adjusted_type") != "R":
            continue
        ref = stations.get(so["reference_station_id"])
        refset = sets.get(ref and ref.get("recommended_set_id"), (None, None))[1]
        rnamed = refset.get("datum", {}).get("named", {}) if refset else {}
        for k, v in so["datum"].get("named", {}).items():
            kind = so["datum"]["basis"].get(k, {}).get("kind")
            rk = "otc_" + k if kind == "computed" and "otc_" + k in rnamed else k
            if rk not in rnamed:
                row(f"{st['station_id']} {k}", "subordinate level has a reference level", rk, "in the reference datum", False)
                continue
            ratio = so["height_offset_low"] if k in LOW else so["height_offset_high"]
            row(f"{st['station_id']} {k}", f"subordinate level = ratio x reference {rk}", v, f"{ratio} x {rnamed[rk]} = {ratio * rnamed[rk]:.3f}",
                abs(v - ratio * rnamed[rk]) <= 0.005)
    # 11. recommended set
    for st in stations.values():
        cands = [cs for cs in st.get("constant_sets", []) if cs.get("quantity") == "water_level" and cs.get("qc_status") != "no_constants"]
        if not cands:
            continue

        def key(cs):
            rs = cs.get("record_span")
            in_span = 0.0
            if rs:
                a0, a1 = max(day(rs["start"]), date(2002, 1, 1)), min(day(rs["end"]), date(2021, 1, 1))
                span = (day(rs["end"]) - day(rs["start"])).days
                in_span = rs.get("good_hours", 0) * max(0, (a1 - a0).days) / span if span else 0.0
            return (-SOURCE_RANK.get(cs.get("source_type"), 9), in_span, rs["end"] if rs else "")
        best = max(cands, key=key)["set_id"]
        row(st["station_id"], "recommended set: official, then gauge fit, then model; then most good hours in 2002-2020; then most recent",
            st.get("recommended_set_id"), best, st.get("recommended_set_id") == best)
    return rows


def main(argv):
    args = [x for x in argv if not x.startswith("--")]
    path = args[0] if args else "src/schema/example.json"
    rows = audit(json.load(open(path)))
    bad = [r for r in rows if not r[4]]
    for r in rows if "--all" in argv else bad:
        print(("ok" if r[4] else "MISMATCH") + " | " + " | ".join(r[:4]))
    if len(rows) < MIN_CHECKS:
        print(f"FAIL: only {len(rows)} checks ran, fewer than {MIN_CHECKS}: a check stopped applying")
        return 1
    print(f"{'ok' if not bad else 'FAIL'}: {len(rows)} checks of example.json, {len(bad)} mismatches")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
