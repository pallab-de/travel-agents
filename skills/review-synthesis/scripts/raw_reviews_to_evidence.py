#!/usr/bin/env python
"""Turn a saved, raw review download into the evidence file that fact-guard's guest-score gate reads.

  python raw_reviews_to_evidence.py --raw raw/apify-6469.json --id 6469 --method apify-google-maps-reviews-scraper \
      --source-url https://www.google.com/maps/place/?q=place_id:ChIJ... --out reviews/6469.google.json [--limit 20]

--out must be named <row id>.<source>.json: stay_spec.py picks up every reviews/<row id>.*.json as a review source. --source-url is
the page the reviews were read from; its site must be the platform the method reads (a Booking method, a booking.com url).

The raw file is kept as downloaded. The evidence file records the method, the time of retrieval, the sha256 of the raw
file, and each review's id, stars, date, text and text sha256. The gate recomputes the score from these numbers, so a
hand-edited review or a typed score fails. Only the methods listed in the policy's review_methods are accepted.
"""
import argparse, datetime as dt, hashlib, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'fact-guard' / 'scripts'))
import guard  # noqa: E402  (the policy's list of accepted review methods)


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


# How each platform's download names the fields (as the Apify actors return them); a download where no review maps is refused.
FIELD_MAPS = {
    "google": {"id": ("reviewId",), "stars": lambda x: x.get("stars"), "text": lambda x: x.get("text"), "when": ("publishedAtDate",)},
    "booking": {"id": ("id", "reviewId"), "stars": lambda x: (max(1.0, min(5.0, float(x["rating"]) / 2)) if x.get("rating") is not None else None),   # Booking scores out of 10: halves are kept (9/10 is 4.5 stars, never rounded down)
                "text": lambda x: " ".join(t for t in (x.get("likedText"), x.get("dislikedText"), x.get("reviewText"), x.get("text")) if t), "when": ("reviewDate", "date")},
    "tripadvisor": {"id": ("id", "reviewId"), "stars": lambda x: x.get("rating"), "text": lambda x: x.get("text") or x.get("reviewText"), "when": ("publishedDate", "date")},
    "airbnb": {"id": ("id", "reviewId"), "stars": lambda x: x.get("rating"), "text": lambda x: x.get("comments") or x.get("text"), "when": ("createdAt", "date")},
}


def convert(raw_path, method, limit, source_url=""):
    raw = Path(raw_path).read_bytes()
    items = json.loads(raw)
    platform = guard.REVIEW_METHOD_PLATFORM.get(method, "google")
    fm = FIELD_MAPS[platform]
    out, seen_ids, seen_text = [], set(), set()
    for i, x in enumerate(items):
        try:
            text = str(fm["text"](x) or "").strip()
            stars = fm["stars"](x)
        except (KeyError, TypeError, ValueError):
            continue
        if len(text) < 15 or stars is None:
            continue
        rid = f"{platform}-{next((x[k] for k in fm['id'] if x.get(k)), i)}"   # ids carry their platform: one id never names two sources' reviews
        if rid in seen_ids or text.lower() in seen_text:
            continue   # the same review twice counts once
        seen_ids.add(rid); seen_text.add(text.lower())
        out.append({'id': rid, 'stars': int(stars) if float(stars).is_integer() else round(float(stars), 1),
                    'when': str(next((x[k] for k in fm["when"] if x.get(k)), ""))[:10], 'text': text,
                    'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()})
        if len(out) == limit:
            break
    return {'method': method, 'retrieved_at': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'source_url': source_url, 'raw_file': str(raw_path), 'raw_sha256': hashlib.sha256(raw).hexdigest(), 'reviews': out}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--raw', required=True); ap.add_argument('--id', required=True)
    ap.add_argument('--method', required=True); ap.add_argument('--out', required=True); ap.add_argument('--source-url', required=True); ap.add_argument('--limit', type=int, default=20)
    a = ap.parse_args()
    if a.method not in guard.DEFAULT_POLICY['review_methods']:
        print(f"refused: {a.method!r} is not an accepted review method {guard.DEFAULT_POLICY['review_methods']}")
        return 1
    if not __import__('re').fullmatch(r'[^.]+\.[a-z0-9-]+\.json', Path(a.out).name) or not Path(a.out).name.startswith(a.id + '.'):
        print(f"refused: --out must be named {a.id}.<source>.json (stay_spec.py finds review files by that pattern), not {Path(a.out).name!r}")
        return 1
    host = __import__('urllib.parse').parse.urlparse(a.source_url).hostname
    expect = guard.REVIEW_METHOD_PLATFORM.get(a.method)
    if not host or (expect and guard.brand_key(host) != expect and not guard.reserved_host(host)):
        print(f"refused: {a.method} reads {expect}, but --source-url is {host!r}")
        return 1
    doc = convert(a.raw, a.method, a.limit, a.source_url)
    if len(doc['reviews']) < a.limit:
        print(f'{a.id}: only {len(doc["reviews"])} usable reviews in the raw file (need {a.limit}); nothing written')
        return 1
    Path(a.out).parent.mkdir(exist_ok=True)
    Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
    mean = sum(r['stars'] for r in doc['reviews']) / len(doc['reviews'])
    print(f'{a.id}: {len(doc["reviews"])} reviews written, mean {mean:.2f} stars, raw sha256 {doc["raw_sha256"][:12]}')
    return 0


def selftest():
    import io, contextlib, tempfile
    cases = []
    with tempfile.TemporaryDirectory() as td:
        raw = Path(td) / 'raw.json'
        items = [{'reviewId': f'x{i}', 'stars': 5, 'text': f'A genuine long review number {i} about the pool', 'publishedAtDate': '2030-01-01'} for i in range(5)]
        raw.write_text(json.dumps(items + [dict(items[0]), {'reviewId': 'z', 'stars': 4, 'text': 'short'}]), encoding='utf-8')
        doc = convert(raw, 'google-places-api', 20)
        cases.append(('a repeated review and a too-short one are dropped', len(doc['reviews']) == 5))
        sys.argv = ['x', '--raw', str(raw), '--id', '1', '--method', 'google-places-api', '--source-url', 'https://www.google.com/maps/place/x', '--out', str(Path(td) / 'o' / '1.google.json'), '--limit', '20']
        with contextlib.redirect_stdout(io.StringIO()):
            cases.append(('fewer usable reviews than the limit writes nothing', main() == 1 and not (Path(td) / 'o').exists()))
        sys.argv = ['x', '--raw', str(raw), '--id', '1', '--method', 'html-scrape', '--source-url', 'https://www.google.com/maps/place/x', '--out', str(Path(td) / '1.google.json'), '--limit', '3']
        with contextlib.redirect_stdout(io.StringIO()):
            cases.append(('a method the policy does not accept is refused', main() == 1 and not (Path(td) / 'e2.json').exists()))
        sys.argv = ['x', '--raw', str(raw), '--id', '1', '--method', 'google-places-api', '--source-url', 'https://www.google.com/maps/place/x', '--out', str(Path(td) / '1.google.json'), '--limit', '3']
        with contextlib.redirect_stdout(io.StringIO()):
            cases.append(('a good download writes the evidence file', main() == 0 and (Path(td) / '1.google.json').exists()))
        sys.argv = ['x', '--raw', str(raw), '--id', '1', '--method', 'google-places-api', '--source-url', 'https://www.google.com/maps/place/x', '--out', str(Path(td) / 'e4.json'), '--limit', '3']
        with contextlib.redirect_stdout(io.StringIO()):
            cases.append(('an output name stay_spec would not find is refused', main() == 1 and not (Path(td) / 'e4.json').exists()))
        sys.argv = ['x', '--raw', str(raw), '--id', '1', '--method', 'apify-booking-reviews', '--source-url', 'https://www.google.com/maps/place/x', '--out', str(Path(td) / '1.booking.json'), '--limit', '3']
        with contextlib.redirect_stdout(io.StringIO()):
            cases.append(('a Booking method with a Google url is refused', main() == 1))
        braw = Path(td) / 'b.json'
        braw.write_text(json.dumps([{'id': f'b{i}', 'rating': 8, 'likedText': f'Lovely quiet rooms and a helpful host, stay {i}', 'dislikedText': '', 'reviewDate': '2030-02-01'} for i in range(4)]), encoding='utf-8')
        bdoc = convert(braw, 'apify-booking-reviews', 3, 'https://www.booking.com/hotel/x.html')
        cases.append(('a Booking download is read with its own fields: 8 out of 10 is 4 stars', len(bdoc['reviews']) == 3 and bdoc['reviews'][0]['stars'] == 4 and bdoc['source_url'].endswith('x.html')))
    with tempfile.TemporaryDirectory() as td2:
        r2 = Path(td2) / 'r.json'
        r2.write_text(json.dumps([{'id': f'b{i}', 'rating': v, 'likedText': f'Review text long enough to count number {i}'} for i, v in enumerate((9, 1, 7, 5, 10))]), encoding='utf-8')
        stars = [r['stars'] for r in convert(r2, 'apify-booking-reviews', 5, 'https://www.booking.com/hotel/x.html')['reviews']]
        cases.append(('Booking scores out of 10 keep their halves: 9 is 4.5, 1 is held at the 1-star floor, 7 is 3.5, 10 is 5', stars == [4.5, 1, 3.5, 2.5, 5]))
        ids = [r['id'] for r in convert(r2, 'apify-booking-reviews', 2, 'https://www.booking.com/hotel/x.html')['reviews']]
        cases.append(('review ids carry their platform, so two files never share an id', all(i.startswith('booking-') for i in ids)))
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(selftest() if sys.argv[1:] == ['selftest'] else main())
