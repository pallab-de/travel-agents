#!/usr/bin/env python
"""Turn listing-search's merged.json into report_data.json, the rows stay_spec.py builds cards and the table from.

  stay_rows.py PROJECT --config config.json [--merged merged.json] [--order ids.json] [--photos photos.json] [--out report_data.json]
  stay_rows.py selftest

merged.json is merge.py's {id: row}. photos.json (optional, the author's) is {id: [https photo urls]}: the photos each listing's own page
serves, saved while the page was read; without it a card shows "no photo with a recorded source URL". The rows are written in the search's order (or the order of --order's id list); a
card's rank score re-orders them later, once reviews are read. Each row carries only what the pages can support: a drive
time and a price from the search, a Maps directions link from the home base in config.json (the source of the drive
time), Google's rating when a place matched, and the bed rule's result in words. Nothing is invented: a field the
search did not find is left out, and stay_spec marks the gap.
"""
import argparse, json, sys, urllib.parse
from pathlib import Path

BEDS = {"UNKNOWN": "Bed count not shown on the listing", "REAL_BEDS_OK": "Real beds for everyone", "NEEDS_MATTRESSES": "Needs extra mattresses", "SHORT": "Too few beds for the group"}
POOL = {"private": "Private pool", "shared": "Shared pool", "club_only": "Club pool access", "plunge": "Plunge pool",
        "pool_unspecified": "Pool (type not stated)", "none_found": "", "none": "", "": ""}   # every kind merge.py's pool_kind returns
AVAIL = {"available": "Available", "available_price_not_shown": "Available", "unavailable_dates": "Unavailable",
         "not_yet_available": "Unavailable", "unknown": "Not checked", "": ""}
REVIEW_STAR = "[{stars}\u2605 {when}] {text}"   # the line format stay_spec reads back as a review
MAX_FLAG_WORDS = 14   # a flag becomes a watch-out, which the build limits to this many words


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def row_of(i, r, home, rank):
    g = r.get("g") or {}
    ll = r.get("ll") or [None, None]
    kind = r.get("pool_kind") or ""
    if kind not in POOL:
        raise SystemExit(f"stay_rows: row {i} has pool_kind {kind!r}, which this script does not know; add it to POOL")
    avail = r.get("availability") or ""
    if avail not in AVAIL:
        raise SystemExit(f"stay_rows: row {i} has availability {avail!r}, which this script does not know; add it to AVAIL")
    recent = [REVIEW_STAR.format(stars=int(x["stars"]), when=x.get("when") or "", text=x["text"]) for x in (r.get("reviews") or [])
              if isinstance(x, dict) and x.get("stars") and str(x.get("text") or "").strip()][:12]
    out = {"id": str(i), "name": r["name"], "url": r["url"], "area": r.get("area", ""), "rank": rank,
           "drive": r.get("drive_out_min"), "drive_back": r.get("drive_back_min"), "cost": r.get("total"), "total": r.get("total"),
           "refund": r.get("refund_policy") or "", "pool": POOL[kind],
           "beds": BEDS.get(r.get("bed_class"), "") + (" (estimated)" if r.get("beds_estimated") and r.get("bed_class") != "UNKNOWN" else ""),
           "avail": AVAIL[avail], "arat": r.get("a_rating"), "an": r.get("a_n"),
           "sleep": r.get("sleep") or "", "amen": r.get("amen_top") or "", "rules": r.get("rules") or "", "pet_fee": r.get("pet_fee"),
           "recent": recent}
    flags = []   # reasoned notes of our own only; the parser's keyword hits stay in merged.json for the reviewer
    if r.get("drive_status") and r.get("drive_out_min") is None:
        flags.append("No drive time: the route could not be measured")
    if r.get("drive_back_status") and r.get("drive_back_min") is None:
        flags.append("No return drive time: that route could not be measured")
    if r.get("ll_approx"):
        flags.append("Location approximate: the listing gave no pin")
    if g.get("cluster"):
        flags.append("Google listing may be a shared one, not this unit")
    if g.get("renamed"):
        flags.append("Google lists this place under a different name: confirm it")
    out["flags"] = flags
    if r.get("guest_fav"):
        out["vchip"] = "Guest favourite"
    if g.get("place_id"):
        out.update({"gname": g.get("name"), "gplace": True, "gurl": "https://www.google.com/maps/place/?q=place_id:" + str(g["place_id"])})
        if not g.get("cluster"):   # a shared complex's rating is not this listing's own
            out.update({"grat": g.get("rating"), "gn": g.get("n")})
    if ll[0] is not None and home:
        out["dir"] = ("https://www.google.com/maps/dir/?api=1&origin=" + urllib.parse.quote(home)
                      + f"&destination={ll[0]},{ll[1]}&travelmode=driving")
    return {k: v for k, v in out.items() if v not in (None, "")}


def convert(merged, home, order=None, photos=None):
    ids = [i for i in (order or list(merged)) if i in merged]
    rows = [row_of(i, merged[i], home, n) for n, i in enumerate(ids, 1)]
    for r in rows:
        imgs = [u for u in (photos or {}).get(r["id"], []) if str(u).startswith("https://")]
        if imgs:
            r["images"] = imgs
    return {"rows": rows}


def selftest():
    merged = {"a1": {"name": "Farm A", "url": "https://x.test/rooms/a1", "area": "Valmora", "drive_out_min": 60, "drive_back_min": 62, "total": 300,
                     "refund_policy": "Free cancellation", "pool_kind": "private", "bed_class": "REAL_BEDS_OK", "availability": "available",
                     "ll": [10.0, 20.0], "g": {"name": "Farm A", "rating": 4.6, "n": 120, "place_id": "ChIJx"}, "reviews": [{"stars": 5, "when": "June 2030", "text": "Lovely pool"}]},
              "b2": {"name": "Farm B", "url": "https://x.test/rooms/b2", "drive_out_min": 90, "total": 250, "bed_class": "SHORT", "beds_estimated": True, "ll": None, "g": None}}
    rows = convert(merged, "Valmora old town", ["b2", "a1"])["rows"]
    a = next(r for r in rows if r["id"] == "a1")
    cases = [("rows follow the order given, ranked from 1", [r["id"] for r in rows] == ["b2", "a1"] and rows[0]["rank"] == 1),
             ("a matched place carries Google's rating and a place link", a["grat"] == 4.6 and a["gurl"].endswith("place_id:ChIJx")),
             ("the drive time has its Maps directions link", a["dir"].startswith("https://www.google.com/maps/dir/") and "destination=10.0,20.0" in a["dir"]),
             ("the bed rule is shown in words with no digits", a["beds"] == "Real beds for everyone" and rows[0]["beds"] == "Too few beds for the group (estimated)"),
             ("the parser's keyword hits are not published as watch-outs", "flags" not in a or all("caretaker" not in f for f in a["flags"])),
             ("a shared Google place keeps its map link but not its rating", "grat" not in row_of("c", dict(merged["a1"], g=dict(merged["a1"]["g"], cluster=True)), "h", 1)
              and "gurl" in row_of("c", dict(merged["a1"], g=dict(merged["a1"]["g"], cluster=True)), "h", 1)),
             ("availability and pool kinds are mapped, and an unknown one stops the run", row_of("d", dict(merged["a1"], availability="available_price_not_shown", pool_kind="club_only"), "h", 1)["pool"] == "Club pool access"),
             ("the listing's own reviews become 'recent' lines", a["recent"] == ["[5\u2605 June 2030] Lovely pool"]),
             ("photos.json urls become a row's images; a non-https one is left out", convert(merged, "h", None, {"a1": ["https://img.example.test/1.jpg", "http://x/2.jpg"]})["rows"][0]["images"] == ["https://img.example.test/1.jpg"]),
             ("a field the search did not find is left out, not guessed", "grat" not in rows[0] and "dir" not in rows[0] and "pool" not in rows[0])]
    for name, ok in cases:
        print(("PASS " if ok else "FAIL ") + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


def main():
    if sys.argv[1:] == ["selftest"]:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project"); ap.add_argument("--config", required=True); ap.add_argument("--merged", default="merged.json")
    ap.add_argument("--order"); ap.add_argument("--photos", default="photos.json"); ap.add_argument("--out", default="report_data.json")
    a = ap.parse_args()
    proj = Path(a.project)
    cfg = json.loads((proj / a.config).read_text(encoding="utf-8"))
    merged = json.loads((proj / a.merged).read_text(encoding="utf-8"))
    order = json.loads((proj / a.order).read_text(encoding="utf-8")) if a.order else None
    photos = json.loads((proj / a.photos).read_text(encoding="utf-8")) if (proj / a.photos).exists() else None
    data = convert(merged, cfg.get("home_base_address"), order, photos)
    (proj / a.out).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {proj / a.out}: {len(data['rows'])} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
