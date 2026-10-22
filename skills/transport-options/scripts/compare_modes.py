#!/usr/bin/env python
"""transport-options: capture each mode's figures for one leg, assemble door-to-door times, and refuse a comparison
that states a figure with no capture behind it.

A flight is never compared on its air time alone: the journey is getting to the airport + the flight + getting
from the airport, each captured. Rail likewise. Road times use the trip's own departure time, not "now".

Subcommands:
  drive    ORIGIN DESTINATION OUT.json --depart 2030-03-01T08:00+01:00 [--label TEXT] [--public-origin AREA] [--public-destination AREA]
           driving time and distance from Google Maps for that departure (traffic-aware). Writes nothing on failure.
  air      FROM TO DATE OUT.json [--adults N] [--children N] [--infants-in-seat N] [--infants-on-lap N] [--currency EUR] [--top 3]
           one-way fares and schedules from SerpApi's Google Flights engine (IATA codes, YYYY-MM-DD). Writes the top
           options as main-journey segments with currency, fare_basis and passengers. Needs SERPAPI_API_KEY. Writes
           nothing on failure. Search each direction separately: a round-trip total can't sit on a one-way leg.
  rail     OUT.json --label TEXT --time-minutes N --source URL [--cost TEXT] [--raw-file FILE]
           record a fare/timetable read on an operator or rail aggregator page (rail has no API here). Refuses
           a source that isn't a rail site.
  assemble OUT.json --mode air|rail --main MAIN.json [--pick N] --access A.json --egress E.json
           build one door-to-door mode entry: the three segments, and time_minutes as their sum.
  validate COMPARISON.json
           every stated figure has a source and captured_at, every leg has a recommendation with reasons, and
           air/rail are door-to-door (the same check fact-guard runs).
  selftest no network
"""
import argparse, datetime as dt, hashlib, json, sys, urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))                                  # <plugin_root>/tools
sys.path.insert(0, str(ROOT / "skills" / "fact-guard" / "scripts"))
import google_maps as gm  # noqa: E402
import guard  # noqa: E402

MAPS_DIR = "https://www.google.com/maps/dir/?api=1"
NUMERIC_FIELDS = ("time_minutes", "cost", "distance_km")


def utc_now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ApiStop(Exception):
    """An API refused or failed the request: write nothing."""


def departure_epoch(depart, now=None):
    """The trip's own departure moment, as epoch seconds. Google only estimates traffic for now or the future, so a
    past time is refused rather than silently replaced by 'now'."""
    try:
        t = dt.datetime.fromisoformat(str(depart).replace("Z", "+00:00"))
    except ValueError:
        t = None
    if t is None or "T" not in str(depart) or t.tzinfo is None:
        raise ApiStop(f"--depart {depart!r} must be a date and time with its UTC offset, e.g. 2030-03-01T08:00+01:00")
    now = now or dt.datetime.now(dt.timezone.utc)
    if t < now - dt.timedelta(minutes=5):
        raise ApiStop(f"--depart {depart} is in the past; Google estimates traffic only for now or later")
    return int(t.timestamp())


def capture_driving(origin, destination, api_key, depart, label=None, public_origin=None, public_destination=None):
    """One driving leg at the trip's departure time. origin/destination are addresses or 'lat,lng'. The stored label and
    source link show `public_origin` / `public_destination` (an area or landmark) instead of the real endpoint when given,
    so a home address that was only needed for the measurement (at either end of the leg) does not end up in a report."""
    data = gm.distance_matrix([origin], [destination], api_key, departure_time=departure_epoch(depart),
                              traffic_model="best_guess", mode="driving")
    try:
        el = gm.matrix_element(data, "Distance Matrix")
    except gm.MapsApiError as e:
        raise ApiStop(str(e))
    if el.get("status") != "OK":
        raise ApiStop(f"Distance Matrix element status {el.get('status')!r} for {origin!r} -> {destination!r}")
    duration = el.get("duration_in_traffic") or el.get("duration")
    return {
        "mode": "road", "label": label or f"{public_origin or origin} to {public_destination or destination}",
        "time_minutes": round(duration["value"] / 60),
        "distance_km": round(el["distance"]["value"] / 1000, 1),
        "traffic_aware": "duration_in_traffic" in el, "departs": depart,
        "source": "https://www.google.com/maps/dir/?" + urllib.parse.urlencode(
            {"api": 1, "origin": public_origin or origin, "destination": public_destination or destination, "travelmode": "driving"}),
        "captured_at": utc_now(),
    }


def air_options(data, top=3, currency=None, passengers=1):
    """SerpApi Google Flights JSON -> main-journey segments, best first. Each keeps what a traveller acts on:
    flight numbers, departure and arrival times, stops, and the fare with its currency and basis (one way, for the
    whole party of `passengers`). A round trip is never stored here: its fare is one total for both directions."""
    src = (data.get("search_metadata") or {}).get("google_flights_url")
    if not src:
        raise ApiStop("SerpApi returned no google_flights_url to cite")
    out = []
    for opt in (data.get("best_flights") or []) + (data.get("other_flights") or []):
        legs = opt.get("flights") or []
        if not legs or opt.get("total_duration") is None:
            continue
        out.append({
            "label": " + ".join(f"{l.get('airline', '?')} {l.get('flight_number', '')}".strip() for l in legs),
            "main": True, "time_minutes": int(opt["total_duration"]),
            "cost": opt.get("price"), "currency": currency, "fare_basis": "one_way", "passengers": passengers,
            "stops": len(legs) - 1,
            "departs": (legs[0].get("departure_airport") or {}).get("time"),
            "arrives": (legs[-1].get("arrival_airport") or {}).get("time"),
            "from": (legs[0].get("departure_airport") or {}).get("id"),
            "to": (legs[-1].get("arrival_airport") or {}).get("id"),
            "source": src, "captured_at": utc_now(),
        })
        if len(out) >= top:
            break
    if not out:
        raise ApiStop("no flights found for that route and date (the search itself succeeded)")
    return out


def assemble(mode, main, access, egress):
    """One door-to-door mode entry: getting there + the main journey + getting away, each a captured segment."""
    segs = [dict(access, main=False), dict(main, main=True), dict(egress, main=False)]
    for s in segs:
        if s.get("time_minutes") is None or not s.get("source") or not s.get("captured_at"):
            raise ApiStop(f"segment {s.get('label')!r} needs time_minutes, source and captured_at")
    entry = {"mode": mode, "time_minutes": sum(int(s["time_minutes"]) for s in segs),
             "segments": [{k: s[k] for k in ("label", "time_minutes", "source", "captured_at", "main") if k in s} for s in segs],
             "source": main["source"], "captured_at": main["captured_at"]}
    if main.get("cost") is not None:
        entry["cost"] = main["cost"]
        for k in ("currency", "fare_basis", "passengers"):
            if main.get(k) is not None:
                entry[k] = main[k]
    return entry


def validate_comparison(comparison):
    fails = []
    for leg in comparison.get("legs", []):
        leg_id = leg.get("id") or leg.get("label", "?")
        for m in leg.get("modes", []):
            stated = [f for f in NUMERIC_FIELDS if m.get(f) is not None]
            if stated and not m.get("source"):
                fails.append(f"{leg_id}/{m.get('mode')}: states {stated} with no source")
            if stated and not m.get("captured_at"):
                fails.append(f"{leg_id}/{m.get('mode')}: states {stated} with no captured_at")
            if m.get("mode") == "air" and m.get("cost") is not None and (not m.get("currency") or not m.get("fare_basis")):
                fails.append(f"{leg_id}/air: a fare with no currency and fare_basis (one_way or round_trip_total) can't be compared")
            if m.get("fare_basis") == "round_trip_total":
                fails.append(f"{leg_id}/{m.get('mode')}: a round-trip total can't sit on a one-way leg; search each direction")
            for s in m.get("segments") or []:
                if not s.get("source") or not s.get("captured_at"):
                    fails.append(f"{leg_id}/{m.get('mode')}: segment {s.get('label')!r} has no source or captured_at")
        if not leg.get("modes"):
            fails.append(f"{leg_id}: no modes compared at all")
        elif not (leg.get("recommendation") or {}).get("reasons"):
            fails.append(f"{leg_id}: no recommendation with stated reasons")
    fails += guard.door_to_door_gate({"blocks": [{"type": "transport_leg", "legs": comparison.get("legs", [])}]})
    return fails


def load_key(name):
    from travel_secrets import load_key as lk
    return lk(name)


def cmd_drive(a):
    try:
        cap = capture_driving(a.origin, a.destination, load_key("GOOGLE_MAPS_API_KEY"), a.depart, a.label, a.public_origin, a.public_destination)
    except (ApiStop, RuntimeError) as e:
        print(f"FAIL {e}")
        return 1
    Path(a.out).write_text(json.dumps(cap, indent=2), encoding="utf-8")
    print(f"captured driving leg -> {a.out}: {cap['time_minutes']} min, {cap['distance_km']} km (departing {a.depart})")
    return 0


def cmd_air(a):
    import serpapi
    if a.return_date:
        print("FAIL --return: a round-trip search prices both directions as one total, which can't be stored on a one-way leg. "
              "Search each direction on its own, or use flight-research for a round trip.")
        return 1
    try:
        data = serpapi.flights(a.frm, a.to, a.date, load_key("SERPAPI_API_KEY"), return_date=a.return_date,
                               adults=a.adults, children=a.children, currency=a.currency,
                               infants_in_seat=a.infants_in_seat, infants_on_lap=a.infants_on_lap)
        opts = air_options(data, a.top, a.currency, a.adults + a.children + a.infants_in_seat + a.infants_on_lap)
    except (ApiStop, serpapi.SerpApiError) as e:
        print(f"FAIL {e}")
        return 1
    Path(a.out).write_text(json.dumps(opts, indent=2), encoding="utf-8")
    for i, o in enumerate(opts):
        print(f"[{i}] {o['label']}: {o['time_minutes']} min, {o['stops']} stop(s), {o['cost']} {a.currency}, departs {o['departs']}")
    print(f"wrote {len(opts)} option(s) -> {a.out}. A critical figure needs a second source: confirm the chosen "
          f"flight's time and fare on the airline's own site (or flightclaw) before it goes in the report.")
    return 0


def cmd_rail(a):
    classes = guard.classes_of(a.source, guard.SOURCE_CLASSES)
    if "rail" not in classes and "official" not in classes:
        print(f"FAIL {a.source!r} is not a rail operator or aggregator site ({sorted(classes) or 'no known class'}); "
              f"add the operator to the spec's policy.source_classes.rail if it's missing from the registry")
        return 1
    seg = {"label": a.label, "main": True, "time_minutes": a.time_minutes, "source": a.source, "captured_at": utc_now()}
    if a.cost:
        seg["cost"] = a.cost
    if a.raw_file:
        raw = Path(a.raw_file).read_text(encoding="utf-8")
        seg["raw_sha256"], seg["raw_excerpt"] = hashlib.sha256(raw.encode("utf-8")).hexdigest(), raw[:400]
    Path(a.out).write_text(json.dumps(seg, indent=2), encoding="utf-8")
    print(f"recorded rail segment -> {a.out}")
    return 0


def cmd_assemble(a):
    main = json.loads(Path(a.main).read_text(encoding="utf-8"))
    main = main[a.pick] if isinstance(main, list) else main
    try:
        entry = assemble(a.mode, main, json.loads(Path(a.access).read_text(encoding="utf-8")),
                         json.loads(Path(a.egress).read_text(encoding="utf-8")))
    except ApiStop as e:
        print(f"FAIL {e}")
        return 1
    Path(a.out).write_text(json.dumps(entry, indent=2), encoding="utf-8")
    print(f"{a.mode} door to door: {entry['time_minutes']} min = " + " + ".join(f"{s['time_minutes']}" for s in entry["segments"]))
    return 0


def cmd_validate(a):
    fails = validate_comparison(json.loads(Path(a.comparison).read_text(encoding="utf-8")))
    for f in fails:
        print("FAIL", f)
    print("VALIDATE " + ("PASS" if not fails else f"FAIL ({len(fails)})"))
    return 0 if not fails else 1


def selftest():
    cases = []
    import re as _re
    doc = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(encoding="utf-8")
    ex = _re.search(r"`{3}json\s*(.*?)\s*`{3}", doc, _re.S)
    cases.append(("the example comparison in SKILL.md passes validate", bool(ex) and validate_comparison(json.loads(ex.group(1))) == []))
    W = "2030-03-01T00:00:00Z"
    road = {"mode": "road", "time_minutes": 300, "distance_km": 340, "source": "https://www.google.com/maps/dir/x", "captured_at": W}
    seg = lambda label, m: {"label": label, "time_minutes": m, "source": "https://www.google.com/maps/dir/y", "captured_at": W}
    flight = {"label": "XY 123", "time_minutes": 70, "cost": 90, "currency": "EUR", "fare_basis": "one_way", "source": "https://www.google.com/travel/flights?x", "captured_at": W}
    air = assemble("air", flight, seg("to airport", 60), seg("from airport", 45))
    cases.append(("assemble sums door to door (60 + 70 + 45)", air["time_minutes"] == 175 and air["cost"] == 90
                  and [s["main"] for s in air["segments"]] == [False, True, False]))
    clean = {"legs": [{"id": "a-b", "modes": [road, air], "recommendation": {"mode": "road", "reasons": ["similar door to door, no airport runs"]}}]}
    cases.append(("a clean comparison passes", validate_comparison(clean) == []))
    flat = json.loads(json.dumps(clean)); flat["legs"][0]["modes"][1] = dict(flight, mode="air")
    cases.append(("air compared on flight time alone fails", any("door-to-door" in f for f in validate_comparison(flat))))
    nosrc = json.loads(json.dumps(clean)); del nosrc["legs"][0]["modes"][0]["source"]
    cases.append(("a figure with no source fails", any("no source" in f for f in validate_comparison(nosrc))))
    norec = json.loads(json.dumps(clean)); norec["legs"][0]["recommendation"]["reasons"] = []
    cases.append(("a recommendation with no reasons fails", any("no recommendation" in f for f in validate_comparison(norec))))
    note_only = {"legs": [{"id": "x", "modes": [{"mode": "road", "note": "no causeway; not viable"}],
                           "recommendation": {"mode": "air", "reasons": ["only viable mode"]}}]}
    cases.append(("a mode with no figure needs no source", validate_comparison(note_only) == []))
    try:
        assemble("air", flight, {"label": "to airport", "time_minutes": 60}, seg("from airport", 45))
        cases.append(("an uncaptured segment is refused", False))
    except ApiStop:
        cases.append(("an uncaptured segment is refused", True))
    real_dm = gm.distance_matrix
    try:
        gm.distance_matrix = lambda o, d, key, **k: {"status": "OK", "rows": [{"elements": [{"status": "OK", "duration": {"value": 3600}, "distance": {"value": 50000}}]}]}
        back = capture_driving("12 Example Street, Valmora", "7 Home Lane, Valmora", "k", "2999-01-01T08:00+01:00", public_origin="Valmora airport", public_destination="Valmora old town")
        cases.append(("a leg that ends at home keeps the street address out of its label and link", "Home Lane" not in json.dumps(back) and "Example" not in json.dumps(back)
                      and back["label"] == "Valmora airport to Valmora old town"))
    finally:
        gm.distance_matrix = real_dm
    serp = {"search_metadata": {"status": "Success", "google_flights_url": "https://www.google.com/travel/flights?q=x"},
            "best_flights": [{"flights": [{"airline": "Air X", "flight_number": "AX 1", "departure_airport": {"id": "AAA", "time": "2030-03-01 07:10"},
                                           "arrival_airport": {"id": "BBB", "time": "2030-03-01 08:20"}}], "total_duration": 70, "price": 90}],
            "other_flights": [{"flights": [{"airline": "Air Y", "flight_number": "AY 2", "departure_airport": {"id": "AAA"}},
                                            {"airline": "Air Y", "flight_number": "AY 3", "arrival_airport": {"id": "BBB"}}], "total_duration": 200, "price": 60}]}
    opts = air_options(serp)
    cases.append(("SerpApi flights parse into main segments, best first", [o["label"] for o in opts] == ["Air X AX 1", "Air Y AY 2 + Air Y AY 3"]
                  and opts[0]["time_minutes"] == 70 and opts[1]["stops"] == 1 and opts[0]["departs"] == "2030-03-01 07:10"))
    cases.append(("a flight source classes as a flight engine", "flight_engine" in guard.classes_of(opts[0]["source"], guard.SOURCE_CLASSES)))
    try:
        air_options({"search_metadata": {"google_flights_url": "https://www.google.com/travel/flights"}})
        cases.append(("no flights found is an error, not an empty list", False))
    except ApiStop:
        cases.append(("no flights found is an error, not an empty list", True))
    cases.append(("air options carry currency, fare basis and party size", opts[0]["currency"] is None
                  and air_options(serp, 1, "EUR", 2)[0]["currency"] == "EUR" and air_options(serp, 1, "EUR", 2)[0]["fare_basis"] == "one_way"
                  and air_options(serp, 1, "EUR", 2)[0]["passengers"] == 2))
    fare = assemble("air", dict(flight, currency="EUR", fare_basis="one_way", passengers=1), seg("a", 60), seg("b", 45))
    nofare = json.loads(json.dumps({"legs": [{"id": "x", "modes": [dict(fare, currency=None)], "recommendation": {"mode": "air", "reasons": ["r"]}}]}))
    cases.append(("an air fare with no currency fails validation", any("no currency" in f for f in validate_comparison(nofare))))
    rt = json.loads(json.dumps({"legs": [{"id": "x", "modes": [dict(fare, fare_basis="round_trip_total")], "recommendation": {"mode": "air", "reasons": ["r"]}}]}))
    cases.append(("a round-trip total on a one-way leg fails validation", any("round-trip total" in f for f in validate_comparison(rt))))
    cases.append(("a drive source URL-encodes the addresses", "origin=A+%26+B%2C+Town" in "https://www.google.com/maps/dir/?" + urllib.parse.urlencode({"api": 1, "origin": "A & B, Town", "destination": "x", "travelmode": "driving"})))
    now = dt.datetime(2030, 3, 1, 6, 0, tzinfo=dt.timezone.utc)
    cases.append(("a future departure becomes its epoch", departure_epoch("2030-03-01T08:00+00:00", now) == int(dt.datetime(2030, 3, 1, 8, tzinfo=dt.timezone.utc).timestamp())))
    for bad, why in (("2030-02-01T08:00+00:00", "a past departure is refused"), ("2030-03-01", "a date with no time is refused"),
                     ("2030-03-01T08:00", "a time with no UTC offset is refused")):
        try:
            departure_epoch(bad, now)
            cases.append((why, False))
        except ApiStop:
            cases.append((why, True))
    ok = all(p for _, p in cases)
    for name, p in cases:
        print("PASS" if p else "FAIL", name)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("drive"); p.add_argument("origin"); p.add_argument("destination"); p.add_argument("out")
    p.add_argument("--depart", required=True); p.add_argument("--label"); p.add_argument("--public-origin", dest="public_origin"); p.add_argument("--public-destination", dest="public_destination")
    p.set_defaults(f=cmd_drive)
    p = sp.add_parser("air"); p.add_argument("frm"); p.add_argument("to"); p.add_argument("date"); p.add_argument("out")
    p.add_argument("--return", dest="return_date"); p.add_argument("--adults", type=int, default=1)
    p.add_argument("--children", type=int, default=0); p.add_argument("--currency", default="USD")
    p.add_argument("--infants-in-seat", dest="infants_in_seat", type=int, default=0); p.add_argument("--infants-on-lap", dest="infants_on_lap", type=int, default=0)
    p.add_argument("--top", type=int, default=3); p.set_defaults(f=cmd_air)
    p = sp.add_parser("rail"); p.add_argument("out"); p.add_argument("--label", required=True)
    p.add_argument("--time-minutes", type=int, required=True); p.add_argument("--source", required=True)
    p.add_argument("--cost"); p.add_argument("--raw-file"); p.set_defaults(f=cmd_rail)
    p = sp.add_parser("assemble"); p.add_argument("out"); p.add_argument("--mode", choices=["air", "rail"], required=True)
    p.add_argument("--main", required=True); p.add_argument("--pick", type=int, default=0)
    p.add_argument("--access", required=True); p.add_argument("--egress", required=True); p.set_defaults(f=cmd_assemble)
    p = sp.add_parser("validate"); p.add_argument("comparison"); p.set_defaults(f=cmd_validate)
    p = sp.add_parser("selftest"); p.set_defaults(f=lambda a: selftest())
    a = ap.parse_args()
    sys.exit(a.f(a))


if __name__ == "__main__":
    main()
