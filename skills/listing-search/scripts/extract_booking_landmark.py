#!/usr/bin/env python
"""Booking.com landmark-page extractor: the hotel-platform counterpart to extract_airbnb.js, with no browser needed.

Booking.com's search-results page and hotel pages are rendered client-side, so a plain HTTP fetch returns an empty
shell. Its SEO "landmark" pages (`/landmark/<cc>/<slug>.html`, one per named point of interest) are server-rendered and
embed the same property data as JSON: several `"accommodations":[...]` arrays per page (featured, most booked, top
reviewed, targeted, value pick). This script finds each array by the literal substring `"accommodations":[` and
bracket-counting to the matching `]`, not by a hardcoded JSON path, so a change in the page's build does not break it
silently.

Per landmark page (schema last verified live 2026-10-03; Booking.com has no public API, so re-verify before trusting an
old run): up to ~30 raw entries with heavy overlap between the arrays (deduplicated by propertyId), a SAMPLE of what is
near the landmark, not its inventory. Use several landmarks across the area as the grid-sweep equivalent; one page
undercounts badly. Per entry, when present (the "most booked" and "top reviewed" carousels often lack price and pin):
name, propertyId, hotel page URL, exact lat/lng (Booking.com's own pin, not jittered, so match with a tight max_km in
place-matching), star rating, review score (/10) and count, a short description, one sample guest review (name,
country, text, 1-5 score), and a "price from" figure in the page's currency.

PRICE CAVEAT: the embedded "price from" does not move with the checkin/checkout/group_adults/no_rooms query parameters
(tested by varying all four on one URL). It is Booking.com's cached nightly rate for a default 1-night, 2-adult search.
Use it to rank and shortlist, never for a final budget decision: confirm the shortlist's price for the real dates and
rooms with a live Playwright pass on searchresults.html or a targeted search, and say in the report which figures are
which.

USAGE:
    python extract_booking_landmark.py <landmark-url> [<landmark-url> ...] --out raw_booking.json
    python extract_booking_landmark.py selftest

Output: {"fetched": [{url, landmark_slug, n_raw, n_unique_new}, ...], "candidates": {propertyId: {...}}}, deduplicated
across all URLs in the run. Run again with more URLs and merge by propertyId to extend a sweep.

Standard library only (urllib, re, json).
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def fetch_html(url: str, retries: int = 2, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'en-US,en;q=0.9'})
    last_err = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode('utf-8', errors='ignore')
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url} after {retries + 1} attempts: {last_err}")


def find_json_arrays(html: str, key: str):
    """Find every top-level JSON array embedded as `"<key>":[...]` in the page, by bracket-
    counting from the first `[` after each occurrence of the key to its matching `]` -- robust to
    the array containing nested objects/arrays, and to there being more than one such array on
    the page (there were FIVE on a single Booking.com landmark page when this was written)."""
    arrays = []
    needle = f'"{key}":['
    start_search = 0
    while True:
        idx = html.find(needle, start_search)
        if idx == -1:
            break
        arr_start = html.find('[', idx)
        depth, i, in_str, esc = 0, arr_start, False, False
        while i < len(html):
            c = html[i]
            if in_str:
                if esc:
                    esc = False
                elif c == '\\':
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == '[':
                    depth += 1
                elif c == ']':
                    depth -= 1
                    if depth == 0:
                        break
            i += 1
        arr_end = i + 1
        try:
            arrays.append(json.loads(html[arr_start:arr_end]))
        except json.JSONDecodeError as e:
            raise ValueError(f'an embedded "{key}" array did not decode ({e.msg}): the page\'s shape has moved') from e
        start_search = arr_end
    return arrays


def normalize_entry(raw: dict, source_url: str, list_kind: str):
    """Both entry shapes seen (root 'accommodations' entries, and 'LxAccMostBookedItem' carousel
    entries) nest the actual property under raw['property'] -- but the most-booked shape omits
    price/pin/distance entirely. Pull whatever is present; leave the rest None rather than guess."""
    prop = raw.get('property') or {}
    basic = prop.get('basic') or {}
    loc = prop.get('location') or {}
    pin = loc.get('pin') or {}
    ratings = prop.get('ratings') or {}
    reviews = ratings.get('reviews') or {}
    facilities = ratings.get('facilitiesRating') or {}
    price = raw.get('price') or {}
    dist = loc.get('distanceToDestination') or {}
    review = raw.get('review') or {}
    property_id = basic.get('propertyId')
    if property_id is None:
        return None
    return {
        'property_id': property_id,
        'name': basic.get('name'),
        'description': basic.get('description'),
        'hotel_page_url': raw.get('hotelPageUrl'),
        'll': [pin['latitude'], pin['longitude']] if pin.get('latitude') is not None else None,
        'distance_to_landmark': dist.get('localized'),
        'star_rating': facilities.get('score') if facilities.get('representation') == 'STARS' else None,
        'review_score_10': reviews.get('score'),
        'review_count': reviews.get('count'),
        'price_amount': price.get('amount'),
        'price_currency': price.get('currency'),
        'price_basis_caveat': '1 night, 2 adults, Booking.com cached "from" rate -- NOT verified for the '
                               'actual search dates/room count; see this script\'s module docstring',
        'sample_review': {
            'reviewer_name': review.get('reviewerName'),
            'reviewer_country': review.get('reviewerCountryCode'),
            'text': review.get('text'),
            'score_of_5': review.get('score'),
        } if review else None,
        'source_url': source_url,
        'source_list_kind': list_kind,  # which of the page's several carousels this came from
    }


def _filled(rec):
    return sum(1 for v in rec.values() if v not in (None, '', [], {}))


def merge_records(a: dict, b: dict) -> dict:
    """One property seen twice (a landmark page repeats it across carousels, and across pages): keep the fuller record and fill
    its gaps from the other, field by field, so a sparse carousel entry never costs a full record its pin, score or count."""
    rich, other = (a, b) if _filled(a) >= _filled(b) else (b, a)
    out = dict(rich)
    for k, v in other.items():
        if out.get(k) in (None, '', [], {}) and v not in (None, '', [], {}):
            out[k] = v
    kinds = []
    for rec in (a, b):
        kinds += rec.get('source_list_kinds') or [rec.get('source_list_kind')]
    out['source_list_kinds'] = [k for k in dict.fromkeys(kinds) if k]
    return out


def extract_landmark_page(url: str, fetch=None):
    html = (fetch or fetch_html)(url)
    candidates = {}
    arrays = find_json_arrays(html, 'accommodations')
    if not arrays:
        raise RuntimeError('no "accommodations" array on the page: a block or consent page, or the page\'s shape has moved')
    for arr in arrays:
        for raw in arr:
            kind = raw.get('__typename', 'unknown')
            norm = normalize_entry(raw, url, kind)
            if norm:
                pid = norm['property_id']
                candidates[pid] = merge_records(candidates[pid], norm) if pid in candidates else norm
    if not candidates:
        raise RuntimeError('the page held accommodations arrays but no usable property in them')
    return candidates, html


def main(argv, fetch=None):
    if '--out' not in argv:
        print(__doc__)
        return 1
    out_idx = argv.index('--out')
    urls = argv[:out_idx]
    out_path = Path(argv[out_idx + 1])
    if not urls:
        print('No landmark URLs given.')
        return 1

    all_candidates = {}
    fetched_log = []
    for url in urls:
        try:
            cands, _html = extract_landmark_page(url, fetch)
        except Exception as e:
            fetched_log.append({'url': url, 'error': str(e)})
            print(f'[FAIL] {url}: {e}')
            continue
        n_before = len(all_candidates)
        for pid, rec in cands.items():
            all_candidates[pid] = merge_records(all_candidates[pid], rec) if pid in all_candidates else rec
        n_new = len(all_candidates) - n_before
        fetched_log.append({'url': url, 'n_raw': len(cands), 'n_unique_new': n_new})
        print(f'[ok] {url}: {len(cands)} raw, {n_new} new unique (running total {len(all_candidates)})')

    failed = [f for f in fetched_log if 'error' in f]
    if failed:
        print(f'\n{len(failed)} of {len(fetched_log)} landmark page(s) failed; nothing was written. A sweep with a page missing is '
              f'not a smaller sweep, it is an incomplete one: fix or drop those URLs and run again.')
        return 1
    out_path.write_text(json.dumps({'fetched': fetched_log, 'candidates': all_candidates}, indent=2, ensure_ascii=False),
                         encoding='utf-8')
    print(f'\nWrote {len(all_candidates)} unique candidates to {out_path}')
    return 0


def selftest():
    import tempfile
    good = '<html>"accommodations":[{"__typename":"LxAccSearchResultSnippetProperty","property":{"basic":{"propertyId":1,"name":"A"}}}]</html>'
    pages = {'https://x.test/ok': good, 'https://x.test/blocked': '<html>consent</html>',
             'https://x.test/drift': '<html>"accommodations":[{"a":</html>'}

    def fetch(url):
        if url == 'https://x.test/down':
            raise RuntimeError('timeout')
        return pages[url]
    cases = []
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / 'raw.json'
        cases.append(('one good page writes the file', main(['https://x.test/ok', '--out', str(out)], fetch) == 0 and out.exists()))
        for name, url in (('a fetch that raises', 'https://x.test/down'), ('a page with no arrays (block or consent page)', 'https://x.test/blocked'),
                          ('an array that does not decode', 'https://x.test/drift')):
            bad = Path(td) / (name[:6].strip().replace(' ', '_') + '.json')
            rc = main(['https://x.test/ok', url, '--out', str(bad)], fetch)
            cases.append((f'{name} fails the run and writes nothing', rc == 1 and not bad.exists()))
    full = {'property_id': 1, 'name': 'A', 'lat': 1.5, 'lng': 2.5, 'review_score_10': 8.9, 'review_count': 120, 'price_amount': None, 'source_list_kind': 'Snippet'}
    sparse = {'property_id': 1, 'name': 'A', 'lat': None, 'lng': None, 'review_score_10': None, 'review_count': None, 'price_amount': 90, 'source_list_kind': 'Carousel'}
    for first, second in ((full, sparse), (sparse, full)):
        m = merge_records(first, second)
        cases.append(('a property seen twice keeps its pin, score and count whichever order it is read in',
                      (m['lat'], m['review_score_10'], m['review_count'], m['price_amount']) == (1.5, 8.9, 120, 90) and m['source_list_kinds'] == [first['source_list_kind'], second['source_list_kind']]))
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(selftest() if sys.argv[1:] == ['selftest'] else main(sys.argv[1:]))
