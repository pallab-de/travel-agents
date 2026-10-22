#!/usr/bin/env python
"""Turn the hotel path's files into report_data.json, the rows stay_spec.py builds cards and the table from.

  python hotel_rows.py PROJECT --area "<city or district>" [--booking raw_booking.json] [--google google.json] \
      [--drives drives.json] [--photos photos.json] [--home "<starting point>"] [--out report_data.json]
  python hotel_rows.py PROJECT --area "<city or district>" --write-hotels      (first: writes hotels.json for google_places.py / rail_distance.py and
                                                                         hotel_cands.json for drive_times.py, both keyed "h<propertyId>")
  python hotel_rows.py selftest

  raw_booking.json  extract_booking_landmark.py's {"candidates": {propertyId: {...}}}
  google.json       hotel-dossier's google_places.py output, keyed by the same ids this script makes ("h<propertyId>"):
                    {"h123": {"match": {...}, "rating", "count", "url"} | {"not_found": ...}}
  drives.json       optional {"h123": {"drive_out_min": 12, "drive_back_min": 14}} (drive_times.py output re-keyed by these ids)

A hotel row carries what the pages support and nothing else: Booking.com's review score (out of 10, shown as the page shows it) and
count, Google's rating where a place matched, the drive time with its Maps directions link, and a note that no live price
was read: Booking.com's "price from" is a cached 1-night, 2-adult figure that does not move with the dates, so it is never
shown as a price. Rooms, beds and pool do not apply to a hotel and are left out.
"""
import argparse, json, sys, urllib.parse
from pathlib import Path

import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def convert(booking, google=None, drives=None, area="", home=None, photos=None):
    google, drives, photos = google or {}, drives or {}, photos or {}
    rows = []
    for pid, c in (booking.get("candidates") or {}).items():
        hid = f"h{pid}"
        g, d = google.get(hid) or google.get(str(pid)) or {}, drives.get(hid) or drives.get(str(pid)) or {}   # keyed "h<propertyId>", or by the bare property id
        row = {"id": hid, "name": c.get("name"), "url": c.get("hotel_page_url") or c.get("source_url"), "area": area,
               "avail": "Not checked", "flags": ["No live price: the booking site's cached rate is not for these dates"],
               "drive": d.get("drive_out_min"), "drive_back": d.get("drive_back_min")}
        imgs = [u for u in (photos.get(hid) or photos.get(str(pid)) or []) if str(u).startswith("https://")]   # the hotel's own or a booking site's photos, saved with the page
        if imgs:
            row["images"] = imgs
        if d.get("ll_approx"):
            row["flags"].append("Location approximate: the page gave no pin")
        if d.get("drive_status") not in (None, "OK") and d.get("drive_out_min") is None:
            row["flags"].append("No drive time: the route could not be measured")
        if c.get("review_score_10") is not None:
            row["arat"], row["an"], row["ascale"] = float(c["review_score_10"]), c.get("review_count"), 10   # the page's own figure and scale, never a derived one
        if g.get("match"):
            row.update({"gname": g["match"].get("name"), "gplace": True, "gurl": "https://www.google.com/maps/place/?q=place_id:" + str(g["match"]["place_id"]),
                        "grat": g.get("rating"), "gn": g.get("count")})
        pin = c.get("ll") or d.get("ll")   # the page's own pin, else the one drive_times.py geocoded
        if pin and home:
            row["dir"] = ("https://www.google.com/maps/dir/?api=1&origin=" + urllib.parse.quote(home)
                          + f"&destination={pin[0]},{pin[1]}&travelmode=driving")
        elif row.get("drive") is not None or row.get("drive_back") is not None:
            row.pop("drive", None)   # a drive time with no directions link has nothing to cite it
            row.pop("drive_back", None)
            row["flags"].append("No drive time: no directions link (give --home and a pin)")
        rows.append({k: v for k, v in row.items() if v not in (None, "")})
    rows.sort(key=lambda r: (-(r.get("arat") or 0), r.get("drive") if r.get("drive") is not None else 10 ** 6, str(r.get("name"))))
    for n, r in enumerate(rows, 1):
        r["rank"] = n
    return {"rows": rows}


def lists(booking, area):
    """-> (hotels.json, hotel_cands.json) from raw_booking.json: the files the Google, rail and drive-time steps read, keyed by the
    same "h<propertyId>" ids convert() joins on."""
    hotels, cands = [], {}
    for pid, c in (booking.get("candidates") or {}).items():
        hid = f"h{pid}"
        hotel = {"id": hid, "name": c.get("name"), "query": f"{c.get('name')} {area}".strip(), "require_in": [area] if area else []}
        if c.get("ll"):
            hotel["lat"], hotel["lng"] = c["ll"][0], c["ll"][1]
            cands[hid] = {"name": c.get("name"), "ll": c["ll"]}
        else:   # no pin on the page: drive_times.py geocodes it by name and area, and the row is flagged approximate
            cands[hid] = {"name": c.get("name"), "geo_text": f"{c.get('name')}, {area}".strip(", "), "ll_approx": True}
        hotels.append(hotel)
    return hotels, cands


def selftest():
    hl, hc = lists({"candidates": {"5": {"name": "Hotel D", "ll": [1.0, 2.0]}, "6": {"name": "Hotel E"}}}, "Valmora")
    bare = convert({"candidates": {"7": {"name": "Hotel C", "review_score_10": 8.0, "review_count": 30}}}, {"7": {"match": {"place_id": "ChIJc", "name": "Hotel C"}, "rating": 4.0, "count": 10}}, {"7": {"drive_out_min": 9, "ll": [1.0, 2.0]}}, area="Valmora", home="Valmora old town")["rows"][0]
    booking = {"candidates": {"1": {"name": "Hotel A", "hotel_page_url": "https://www.booking.com/hotel/x/a.html", "ll": [1.0, 2.0], "review_score_10": 8.6,
                                    "review_count": 410, "price_amount": 99}, "2": {"name": "Hotel B", "source_url": "https://www.booking.com/landmark/x/y.html", "review_score_10": 9.0, "review_count": 50}}}
    google = {"h1": {"match": {"place_id": "ChIJa", "name": "Hotel A"}, "rating": 4.4, "count": 900}, "h2": {"not_found": "no match"}}
    out = convert(booking, google, {"h1": {"drive_out_min": 12}}, area="Valmora", home="Valmora old town")["rows"]
    a, b = next(r for r in out if r["id"] == "h1"), next(r for r in out if r["id"] == "h2")
    cases = [("hotels.json and the drive-time candidates are keyed h<propertyId>; a hotel with no pin is geocoded by name and area",
              [h["id"] for h in hl] == ["h5", "h6"] and list(hc) == ["h5", "h6"] and hc["h6"]["geo_text"] == "Hotel E, Valmora" and hl[0]["query"] == "Hotel D Valmora" and hl[0]["require_in"] == ["Valmora"]),
             ("google and drive files keyed by the bare property id are read too", bare.get("grat") == 4.0 and bare.get("drive") == 9),
             ("a geocoded pin gives the directions link; a drive time with no link is dropped and flagged", bare.get("dir") and not convert({"candidates": {"7": {"name": "H"}}}, {}, {"7": {"drive_out_min": 9}}, area="V")["rows"][0].get("drive")),
             ("rows are ranked by the booking site's score, best first", [r["id"] for r in out] == ["h2", "h1"] and b["rank"] == 1),
             ("the score keeps the page's own scale: 8.6 out of 10", a["arat"] == 8.6 and a["ascale"] == 10 and a["an"] == 410),
             ("a matched Google place carries its rating and link; an unmatched one has none", a["grat"] == 4.4 and a["gurl"].endswith("ChIJa") and "grat" not in b),
             ("the drive time has its directions link", a["drive"] == 12 and a["dir"].startswith("https://www.google.com/maps/dir/")),
             ("the cached price is never shown, and the row says so", "cost" not in a and "total" not in a and "No live price" in a["flags"][0] and len(a["flags"][0].split()) <= 14)]
    for name, ok in cases:
        print(("PASS " if ok else "FAIL ") + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


def main():
    if sys.argv[1:] == ["selftest"]:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project"); ap.add_argument("--area", required=True); ap.add_argument("--booking", default="raw_booking.json")
    ap.add_argument("--google", default="google.json"); ap.add_argument("--drives", default="drives.json"); ap.add_argument("--photos", default="photos.json"); ap.add_argument("--home"); ap.add_argument("--out", default="report_data.json")
    ap.add_argument("--write-hotels", action="store_true", help="write hotels.json and hotel_cands.json from the booking file, then stop")
    a = ap.parse_args()
    proj = Path(a.project)
    load = lambda n: json.loads((proj / n).read_text(encoding="utf-8")) if n and (proj / n).exists() else None
    booking = load(a.booking)
    if not booking:
        print(f"FAIL {proj / a.booking} is missing or empty")
        return 1
    if a.write_hotels:
        hotels, cands = lists(booking, a.area)
        (proj / "hotels.json").write_text(json.dumps(hotels, ensure_ascii=False, indent=1), encoding="utf-8")
        (proj / "hotel_cands.json").write_text(json.dumps(cands, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"wrote hotels.json ({len(hotels)} hotels) and hotel_cands.json ({len(cands)} candidates) in {proj}")
        return 0
    if not (proj / a.drives).exists() and (proj / "hotel_cands.json").exists():
        print(f"WARN {a.drives} is missing although hotel_cands.json exists: the rows will have no drive times (run listing-search/scripts/drive_times.py on hotel_cands.json)")
    if not a.home:
        print('WARN no --home: the rows get no Maps directions links; pass --home "<starting point from config.json: an area, station or landmark>"')
    data = convert(booking, load(a.google), load(a.drives), a.area, a.home, load(a.photos))
    known = {r["id"] for r in data["rows"]} | {r["id"][1:] for r in data["rows"]}
    for label, doc in (("google", load(a.google)), ("drives", load(a.drives))):
        orphans = sorted(k for k in (doc or {}) if k not in known)
        if orphans:
            print(f"WARN {len(orphans)} key(s) in the {label} file match no hotel row and were ignored ({orphans[:3]}); key them \"h<propertyId>\"")
    (proj / a.out).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {proj / a.out}: {len(data['rows'])} hotel rows")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, TypeError, AttributeError, KeyError, IndexError) as e:   # json.JSONDecodeError is a ValueError
        raise SystemExit(f"refused: an input file is malformed or not in the shape this step expects ({type(e).__name__}: {e})")
