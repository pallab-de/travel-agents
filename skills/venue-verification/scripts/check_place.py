#!/usr/bin/env python
"""Look up a single, specific place's existence, address, rating, hours, business status and top reviews.

Usage (non-interactive -- safe for the agent to run directly):

    python "<plugin root>/skills/venue-verification/scripts/check_place.py" "<Venue name>, <City>"
    python "<plugin root>/skills/venue-verification/scripts/check_place.py" "<Venue name>, <City>" --reviews

Only a parsed, sanitized summary is ever printed. The API key is loaded once (tools/travel_secrets.py),
used to build a request, and never printed, logged, or included in any error message.

This is the single-venue, on-demand counterpart to `place-matching`'s `place_match.py` (which fuzzy-matches
many scraped candidates at once from a name + approximate coordinates). Use this one when you already know
which specific venue you mean and want to verify it before writing a claim about it into a report -- most
importantly `business_status`, which catches a place that's permanently or temporarily closed even though
a blog post or listicle hasn't been updated to say so.
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))
from travel_secrets import load_key  # noqa: E402
import google_maps as gm  # noqa: E402

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

DETAIL_FIELDS = "name,formatted_address,business_status,rating,user_ratings_total,opening_hours,international_phone_number,website"
DETAIL_FIELDS_WITH_REVIEWS = DETAIL_FIELDS + ",reviews"


def find_place(query: str, api_key: str) -> str:
    result = gm.find_place_from_text(query, api_key)
    if result.get("status") != "OK" or not result.get("candidates"):
        raise RuntimeError(f"No match found (status: {result.get('status')})")
    return result["candidates"][0]["place_id"]


def place_details(place_id: str, api_key: str, *, with_reviews: bool = False) -> dict:
    fields = DETAIL_FIELDS_WITH_REVIEWS if with_reviews else DETAIL_FIELDS
    result = gm.place_details(place_id, api_key, fields)
    if result.get("status") != "OK":
        raise RuntimeError(f"Details lookup failed (status: {result.get('status')})")
    return result["result"]


def format_summary(details: dict) -> str:
    lines = [
        f"Name: {details.get('name', '?')}",
        f"Address: {details.get('formatted_address', '?')}",
        f"Business status: {details.get('business_status', '?')}",
    ]
    if "rating" in details:
        lines.append(f"Rating: {details['rating']} ({details.get('user_ratings_total', 0)} reviews)")
    hours = details.get("opening_hours")
    if hours and hours.get("weekday_text"):
        lines.append("Hours:")
        lines.extend(f"  {line}" for line in hours["weekday_text"])
    if details.get("international_phone_number"):
        lines.append(f"Phone: {details['international_phone_number']}")
    if details.get("website"):
        lines.append(f"Website: {details['website']}")
    reviews = details.get("reviews")
    if reviews:
        lines.append(f"Reviews (top {len(reviews)}, Google's 'most relevant' sort):")
        for rev in reviews:
            author = rev.get("author_name", "anonymous")
            rating = rev.get("rating", "?")
            when = rev.get("relative_time_description", "")
            text = (rev.get("text") or "").replace("\n", " ").strip()
            when_str = f" ({when})" if when else ""
            lines.append(f"  [{rating}★]{when_str} {author}: {text}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query", help='e.g. "<Venue name>, <City>"')
    parser.add_argument("--reviews", action="store_true", help="Include Google's top review snippets")
    args = parser.parse_args()

    try:
        api_key = load_key("GOOGLE_MAPS_API_KEY")
    except (FileNotFoundError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        place_id = find_place(args.query, api_key)
        details = place_details(place_id, api_key, with_reviews=args.reviews)
    except RuntimeError as exc:
        print(f"Lookup failed: {exc}", file=sys.stderr)
        return 1

    print(format_summary(details))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
