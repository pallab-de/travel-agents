#!/usr/bin/env python
"""Read Google Maps reviews for one place through SerpApi and store them as a review file fact-guard accepts.

The official Places API returns at most ~10 reviews per place; fact-guard needs at least 20 read per source. SerpApi
pages through the full review list under its own licence, so this is the way to reach 20+ from Google without
scraping.

  serpapi_reviews.py fetch PLACE_ID OUT.json [--min 20] [--max-pages 6] [--sort newestFirst]
  serpapi_reviews.py selftest

OUT.json: {"method": "serpapi-google-maps-reviews", "retrieved_at", "place_id", "source_url",
           "reviews": [{"id", "stars", "text", "sha256", "date"}]}
Each review keeps its own text with that text's sha256, so the gate can tell if a review was edited after reading.
Fail-closed: a SerpApi error, or fewer than --min reviews with text, writes nothing and exits 1. Needs SERPAPI_API_KEY.
"""
import argparse, datetime as dt, hashlib, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import serpapi  # noqa: E402

METHOD = "serpapi-google-maps-reviews"


def to_reviews(pages):
    """SerpApi review pages -> stored reviews. A review with no text can't be read, so it isn't counted."""
    out, seen = [], set()
    for page in pages:
        for r in page.get("reviews") or []:
            text = (r.get("extracted_snippet") or {}).get("original") or r.get("snippet") or ""
            rid = r.get("review_id") or hashlib.sha256(f"{r.get('user', {}).get('name')}|{r.get('iso_date')}|{text}".encode()).hexdigest()[:16]
            if not text.strip() or rid in seen or r.get("rating") is None:
                continue
            seen.add(rid)
            out.append({"id": rid, "stars": int(round(float(r["rating"]))), "text": text,
                        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "date": r.get("iso_date") or r.get("date")})
    return out


def fetch(place_id, api_key, minimum, max_pages, sort):
    pages, token = [], None
    for _ in range(max_pages):
        page = serpapi.maps_reviews(place_id, api_key, sort_by=sort, next_page_token=token)
        pages.append(page)
        if len(to_reviews(pages)) >= minimum:
            break
        token = (page.get("serpapi_pagination") or {}).get("next_page_token")
        if not token:
            break
    return pages


def cmd_fetch(a):
    from travel_secrets import load_key
    try:
        pages = fetch(a.place_id, load_key("SERPAPI_API_KEY"), a.min, a.max_pages, a.sort)
    except serpapi.SerpApiError as e:
        print(f"FAIL SerpApi: {e}")
        return 1
    reviews = to_reviews(pages)
    if len(reviews) < a.min:
        print(f"FAIL only {len(reviews)} reviews with text after {len(pages)} page(s); fact-guard needs {a.min}. "
              f"Nothing written. Declare this source unavailable for this place, or read another one.")
        return 1
    doc = {"method": METHOD, "retrieved_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "place_id": a.place_id, "source_url": f"https://www.google.com/maps/place/?q=place_id:{a.place_id}",
           "reviews": reviews}
    Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(reviews)} reviews -> {a.out}")
    return 0


def selftest():
    pages = [{"reviews": [
        {"review_id": "r1", "rating": 5, "snippet": "Lovely pool", "iso_date": "2030-01-02T00:00:00Z"},
        {"review_id": "r2", "rating": 2, "extracted_snippet": {"original": "Noisy road outside"}, "snippet": "Noisy (translated)"},
        {"review_id": "r3", "rating": 4, "snippet": ""},
        {"review_id": "r1", "rating": 5, "snippet": "Lovely pool"}]}]
    revs = to_reviews(pages)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "fact-guard" / "scripts"))
    import guard
    cases = [
        ("reviews keep their text, stars and a matching hash", [r["id"] for r in revs] == ["r1", "r2"] and revs[0]["stars"] == 5
         and revs[0]["sha256"] == hashlib.sha256(b"Lovely pool").hexdigest()),
        ("the original text is kept over a translation", revs[1]["text"] == "Noisy road outside"),
        ("a review with no text, or a repeat, isn't counted", len(revs) == 2),
        ("the method is one fact-guard's policy allows", METHOD in guard.DEFAULT_POLICY["review_methods"]),
        ("the stored source URL classes as a review platform",
         "review_platform" in guard.classes_of("https://www.google.com/maps/place/?q=place_id:x", guard.SOURCE_CLASSES)),
    ]
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
    p = sp.add_parser("fetch"); p.add_argument("place_id"); p.add_argument("out")
    p.add_argument("--min", type=int, default=20); p.add_argument("--max-pages", type=int, default=6)
    p.add_argument("--sort", default="newestFirst"); p.set_defaults(f=cmd_fetch)
    p = sp.add_parser("selftest"); p.set_defaults(f=lambda a: selftest())
    a = ap.parse_args()
    sys.exit(a.f(a))


if __name__ == "__main__":
    main()
