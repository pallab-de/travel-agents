#!/usr/bin/env python
"""Traffic-aware drive times for staycation candidates, for ANY home base / city / weekend.

Reads <in>.json ({id: {ll:[lat,lng], ...}} or {geo_text: "..."} when ll is missing), adds:
  drive_out_min : outbound departure, home -> property (Google Distance Matrix, best_guess traffic)
  drive_back_min: return departure, property -> home
Only durations/km are stored. The API key never leaves this process (see travel_secrets.py).

Run (from the project folder that holds <in>.json and where <out>.json should be written):
  python "<plugin root>/skills/listing-search/scripts/drive_times.py" <in>.json <out>.json \\
      --home "<area, station or landmark>, <city>" \\
      --out-departure <YYYY-MM-DDTHH:MM> --back-departure <YYYY-MM-DDTHH:MM> --tz <UTC offset, e.g. +01:00>
"""
import argparse, json, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))   # <plugin_root>/tools, shared across skills and agents
from travel_secrets import load_key  # noqa: E402
import google_maps as gm  # noqa: E402  -- shared Google Maps HTTP client (also used by place-matching's place_match.py)


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def parse_tz(s):
    s = str(s).strip()
    if not re.fullmatch(r'[+-]?\d{2}:?\d{2}', s):
        raise SystemExit(f'--tz {s!r} must be a UTC offset such as +01:00')
    sign = 1 if s[0] != '-' else -1
    digits = s.lstrip('+-').replace(':', '')
    h, m = digits[:2], digits[2:]
    return timezone(sign * timedelta(hours=int(h), minutes=int(m)))


def epoch(iso, tz):
    """Epoch seconds of a local departure. A time that carries its own offset must agree with --tz."""
    t = datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
    if t.tzinfo is not None and t.utcoffset() != tz.utcoffset(None):
        raise SystemExit(f'departure {iso} carries a different UTC offset from --tz; make them agree')
    return int(t.replace(tzinfo=tz).timestamp())


def matrix(origins, dests, dep, key):
    return gm.require_ok(gm.distance_matrix(origins, dests, key, departure_time=dep, traffic_model="best_guess"), "Distance Matrix",
                         allow=("OK",))


ELEMENT_OK = {"OK", "ZERO_RESULTS", "NOT_FOUND"}   # anything else (a quota or request error inside an element) stops the run


def _element(out, n=0):
    return gm.matrix_element(out, "Distance Matrix", n)


def measure_drives(data, ids, home, out_dep, back_dep, key, out_key="drive_out_min", back_key="drive_back_min", km_key="km"):
    """Fill drive times (outbound, return) and km for `ids` in `data`, 25 destinations per call. Every API call is
    status-checked; an element that isn't OK/ZERO_RESULTS/NOT_FOUND stops the run (raises gm.MapsApiError), and each
    point records its own `drive_status`, so a missing time always has a reason."""
    for k in range(0, len(ids), 25):
        chunk = ids[k:k + 25]
        pts = [f"{data[i]['ll'][0]},{data[i]['ll'][1]}" for i in chunk]
        out = matrix([home], pts, out_dep, key)
        els = (out.get("rows") or [{}])[0].get("elements") or []
        if len(els) != len(chunk):
            raise gm.MapsApiError(f"Distance Matrix returned {len(els)} element(s) for {len(chunk)} point(s)")
        for i, el in zip(chunk, els):
            st = el.get("status")
            if st not in ELEMENT_OK:
                raise gm.MapsApiError(f"Distance Matrix element status {st!r} for {data[i].get('name', i)!r}")
            data[i]["drive_status"] = st
            if st == "OK":
                d = el.get("duration_in_traffic", el["duration"])
                data[i][out_key] = round(d["value"] / 60)
                data[i][km_key] = round(el["distance"]["value"] / 1000, 1)
        for i, pt in zip(chunk, pts):
            el = _element(matrix([pt], [home], back_dep, key))
            if el.get("status") not in ELEMENT_OK:
                raise gm.MapsApiError(f"Distance Matrix element status {el.get('status')!r} on the return leg for {data[i].get('name', i)!r}")
            if el.get("status") == "OK":
                d = el.get("duration_in_traffic", el["duration"])
                data[i][back_key] = round(d["value"] / 60)
            else:
                data[i]["drive_back_status"] = el.get("status")   # a missing return time always has a reason


def geo_query(v):
    """What to geocode a listing with no coordinates by: its geo_text, else its name and the area from its type label
    ("Farm stay in Valmora" -> "<name>, Valmora")."""
    if v.get("geo_text"):
        return v["geo_text"]
    typ = v.get("type") or ""
    return f"{v['name']}, {typ.split(' in ', 1)[1]}" if v.get("name") and ' in ' in typ else None


def geocode_missing(data, key):
    for i, v in data.items():
        q = None if v.get("ll") else geo_query(v)
        if q:
            j = gm.require_ok(gm.geocode(q, key), f"geocoding {v.get('name', i)!r}")
            if j.get("results"):
                loc = j["results"][0]["geometry"]["location"]; v["ll"] = [loc["lat"], loc["lng"]]; v["ll_approx"] = True
    for v in data.values():   # a missing time always has a reason
        if not v.get("ll"):
            v["drive_status"] = "NO_COORDINATES"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('infile'); ap.add_argument('outfile')
    ap.add_argument('--home', required=True, help='The starting point (an area, station or landmark) Google Maps can geocode, e.g. "<area or station>, <city>"')
    ap.add_argument('--out-departure', required=True, help='ISO datetime for the outbound leg, e.g. <YYYY-MM-DDTHH:MM>')
    ap.add_argument('--back-departure', required=True, help='ISO datetime for the return leg, e.g. <YYYY-MM-DDTHH:MM>')
    ap.add_argument('--tz', required=True, help='UTC offset for both departure times, e.g. +01:00 or -05:00')
    ap.add_argument('--cap-min', type=int, default=150, help='Soft drive-time cap to report a pass/fail count for (does not filter data)')
    a = ap.parse_args()
    tz = parse_tz(a.tz)
    out_dep, back_dep = epoch(a.out_departure, tz), epoch(a.back_departure, tz)

    src = Path(a.infile); dst = Path(a.outfile)
    raw = json.loads(src.read_text(encoding="utf-8"))
    data = json.loads(raw) if isinstance(raw, str) else raw
    key = load_key("GOOGLE_MAPS_API_KEY")
    try:
        geocode_missing(data, key)
        ids = [i for i, v in data.items() if v.get("ll")]
        measure_drives(data, ids, a.home, out_dep, back_dep, key)
    except gm.MapsApiError as e:
        print(f"FAIL {e}. Nothing was written.")
        return 1
    dst.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = [v for v in data.values() if "drive_out_min" in v]
    print(f"{len(ok)}/{len(data)} with drive times; <={a.cap_min}: {sum(v['drive_out_min'] <= a.cap_min for v in ok)}")
    return 0


def selftest():
    import contextlib, io
    cases = []
    real = gm.distance_matrix, gm.geocode
    el = lambda st="OK", sec=1800, m=40000: {"status": st, "duration": {"value": sec}, "distance": {"value": m}}
    data = lambda: {"1": {"name": "A", "ll": [1.0, 2.0]}, "2": {"name": "B", "ll": [1.1, 2.1]}}
    try:
        gm.distance_matrix = lambda o, d, key, **k: {"status": "OK", "rows": [{"elements": [el() for _ in d]}]}
        d1 = data(); measure_drives(d1, ["1", "2"], "home", 1, 2, "k")
        cases.append(("a healthy run fills drive times, km and a status", d1["1"]["drive_out_min"] == 30 and d1["1"]["km"] == 40.0 and d1["1"]["drive_status"] == "OK"))
        gm.distance_matrix = lambda o, d, key, **k: {"status": "OVER_QUERY_LIMIT", "error_message": "quota"}
        try:
            measure_drives(data(), ["1"], "home", 1, 2, "k"); cases.append(("an API quota error stops the run", False))
        except gm.MapsApiError as e:
            cases.append(("an API quota error stops the run", "OVER_QUERY_LIMIT" in str(e)))
        gm.distance_matrix = lambda o, d, key, **k: {"status": "OK", "rows": [{"elements": [el("OVER_QUERY_LIMIT") for _ in d]}]}
        try:
            measure_drives(data(), ["1"], "home", 1, 2, "k"); cases.append(("an element error stops the run", False))
        except gm.MapsApiError:
            cases.append(("an element error stops the run", True))
        gm.distance_matrix = lambda o, d, key, **k: {"status": "OK", "rows": [{"elements": [el("ZERO_RESULTS") for _ in d]}]}
        d3 = data(); measure_drives(d3, ["1"], "home", 1, 2, "k")
        cases.append(("an unroutable point records why it has no time", d3["1"]["drive_status"] == "ZERO_RESULTS" and "drive_out_min" not in d3["1"]))
        gm.geocode = lambda a_, key: {"status": "REQUEST_DENIED", "error_message": "bad key"}
        try:
            geocode_missing({"1": {"name": "A", "geo_text": "x"}}, "k"); cases.append(("a geocoding error stops the run", False))
        except gm.MapsApiError:
            cases.append(("a geocoding error stops the run", True))
        asked = []
        gm.geocode = lambda a_, key: (asked.append(a_), {"status": "OK", "results": [{"geometry": {"location": {"lat": 5.0, "lng": 6.0}}}]})[1]
        d4 = {"1": {"name": "Farm A", "type": "Farm stay in Valmora"}, "2": {"name": "No place"}}
        geocode_missing(d4, "k")
        cases.append(("a listing with no coordinates is geocoded by name and area, or marked NO_COORDINATES", asked == ["Farm A, Valmora"] and d4["1"]["ll"] == [5.0, 6.0]
                      and d4["2"]["drive_status"] == "NO_COORDINATES"))
    finally:
        gm.distance_matrix, gm.geocode = real
    for name, ok in cases:
        print(("PASS " if ok else "FAIL ") + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest() if sys.argv[1:] == ["selftest"] else main())
