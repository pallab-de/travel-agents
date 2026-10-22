#!/usr/bin/env python
"""Find lodging (or any place type) near a point via Google Places Nearby Search -- a systematic radius
sweep, not just named candidates someone already thought of.

Usage (non-interactive -- safe for the agent to run directly):

    python "<plugin root>/skills/venue-verification/scripts/find_hotels_nearby.py" "<Landmark name>, <City>" --radius 4000
    python "<plugin root>/skills/venue-verification/scripts/find_hotels_nearby.py" "<Landmark name>, <City>" --radius 2000 --type restaurant

Geocodes the given location text, then runs a Nearby Search (default type=lodging) within the radius
(meters), sorted by rating (unrated candidates pushed to the end). Only a parsed, sanitized summary is
ever printed -- name, rating, review count, price_level, vicinity. The API key is loaded once
(tools/travel_secrets.py) and never printed, logged, or included in any error message.
"""
from __future__ import annotations
import argparse, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))
from travel_secrets import load_key  # noqa: E402
import google_maps as gm  # noqa: E402

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


def geocode(location: str, api_key: str) -> tuple[float, float]:
    result = gm.geocode(location, api_key)
    if result.get("status") != "OK" or not result.get("results"):
        raise RuntimeError(f"Geocoding failed (status: {result.get('status')})")
    loc = result["results"][0]["geometry"]["location"]
    return loc["lat"], loc["lng"]


MAX_PAGES = 3   # Google returns at most 20 results a page and 60 in all


def nearby(lat: float, lng: float, radius_m: int, api_key: str, place_type: str, sleep=time.sleep) -> tuple:
    """-> (places, truncated). Follows next_page_token for up to MAX_PAGES pages; `truncated` is True when Google still
    had more than it returned, so a capped sweep is never presented as the whole set."""
    places, token, pages = [], None, 0
    while True:
        result = gm.require_ok(gm.nearby_search(lat, lng, radius_m, api_key, place_type, pagetoken=token), "Nearby search")
        places += result.get("results", [])
        pages += 1
        token = result.get("next_page_token")
        if not token:
            return places, len(places) >= MAX_PAGES * 20   # Google caps a search at 60 and sends no token on the last page
        if pages >= MAX_PAGES:
            return places, True
        sleep(2.2)   # a next_page_token becomes valid after a short delay


def format_summary(places: list[dict], center: str, radius_m: int, place_type: str) -> str:
    places = sorted(places, key=lambda p: p.get("rating", 0), reverse=True)
    lines = [f"{place_type} within {radius_m}m of {center} ({len(places)} found):"]
    for p in places:
        name = p.get("name", "?")
        rating = p.get("rating")
        total = p.get("user_ratings_total", 0)
        vicinity = p.get("vicinity", "?")
        price_level = p.get("price_level")
        status = p.get("business_status", "?")
        rating_str = f"{rating}★/{total}" if rating is not None else "no rating"
        price_str = f", price_level={price_level}" if price_level is not None else ""
        lines.append(f"  - {name} ({rating_str}{price_str}, {status}) -- {vicinity}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("location", help='e.g. "<Landmark name>, <City>"')
    parser.add_argument("--radius", type=int, default=4000, help="Search radius in meters (default 4000)")
    parser.add_argument("--type", default="lodging", help="Google place type to search for (default lodging)")
    args = parser.parse_args()

    try:
        api_key = load_key("GOOGLE_MAPS_API_KEY")
    except (FileNotFoundError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        lat, lng = geocode(args.location, api_key)
        places, truncated = nearby(lat, lng, args.radius, api_key, args.type)
    except RuntimeError as exc:
        print(f"Lookup failed: {exc}", file=sys.stderr)
        return 1

    print(format_summary(places, args.location, args.radius, args.type))
    if truncated:
        print(f"TRUNCATED: Google had more results than the {MAX_PAGES} pages read ({len(places)}). This is not the whole set; "
              f"narrow the radius or search a more specific type.")
        return 3
    return 0


def selftest() -> int:
    page = lambda n, token=None: {"status": "OK", "results": [{"name": f"P{n}-{i}"} for i in range(20)], **({"next_page_token": token} if token else {})}
    cases = []
    real = gm.nearby_search
    try:
        gm.nearby_search = lambda lat, lng, r, key, t=None, pagetoken=None: page(1, "t1") if pagetoken is None else page(2)
        got, trunc = nearby(1, 2, 100, "k", "lodging", sleep=lambda s: None)
        cases.append(("a second page is followed", len(got) == 40 and not trunc))
        gm.nearby_search = lambda lat, lng, r, key, t=None, pagetoken=None: page(1, "again")
        got, trunc = nearby(1, 2, 100, "k", "lodging", sleep=lambda s: None)
        cases.append(("more results than the page cap are reported as truncated", len(got) == 60 and trunc))
        gm.nearby_search = lambda *a, **k: {"status": "REQUEST_DENIED", "error_message": "key"}
        try:
            nearby(1, 2, 100, "k", "lodging", sleep=lambda s: None); cases.append(("an API error stops the lookup", False))
        except RuntimeError:
            cases.append(("an API error stops the lookup", True))
    finally:
        gm.nearby_search = real
    for name, ok in cases:
        print(("PASS " if ok else "FAIL ") + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest() if sys.argv[1:] == ["selftest"] else main())
