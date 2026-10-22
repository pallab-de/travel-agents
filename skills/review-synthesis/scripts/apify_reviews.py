#!/usr/bin/env python
"""Google reviews for matched places via Apify (a licensed provider). The token comes from the encrypted env and is never printed.
Resumable, and capped: you must say how many places and how many reviews per place the user approved.

  python apify_reviews.py --max-places 40 --max-reviews 60 [--corpus review_corpus.json] [--places places4.json] \
      [--out apify_reviews.json] [--raw-dir raw]
  python apify_reviews.py selftest

For each place with a confident Google match it saves the raw download (raw/apify-<id>.json, kept as downloaded so
raw_reviews_to_evidence.py can turn it into the review file fact-guard's score gate reads) and a trimmed list in --out. Exits 1 if
any place failed, with the file left resumable: run it again to continue.
"""
import argparse, json, sys, time, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))   # <plugin_root>/tools/travel_secrets.py, shared across skills
ACTOR = 'https://api.apify.com/v2/acts/compass~google-maps-reviews-scraper/run-sync-get-dataset-items'


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def trim(items):
    return [{'stars': x.get('stars'), 'when': (x.get('publishedAtDate') or '')[:10], 'text': (x.get('text') or '').strip(),
             'owner_reply': bool(x.get('responseFromOwnerText')), 'local_guide': x.get('isLocalGuide'),
             'reviewer_n': x.get('reviewerNumberOfReviews'), 'place': x.get('title')} for x in items]


def fetch(place_id, token, max_reviews, opener=urllib.request.urlopen):
    body = {'placeIds': [place_id], 'maxReviews': max_reviews, 'reviewsSort': 'newest', 'language': 'en', 'personalData': False}
    req = urllib.request.Request(ACTOR, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    with opener(req, timeout=290) as r:
        return json.loads(r.read().decode())


def run(ids, places, out, raw_dir, token, max_places, max_reviews, opener=urllib.request.urlopen, sleep=time.sleep):
    """Fetch up to max_places not yet done. Returns (done count, errors)."""
    errors, calls = [], 0
    for i in ids:
        if i in out:
            continue
        if calls >= max_places:
            print(f'stopped at the approved cap of {max_places} place(s); run again with a higher cap to continue')
            break
        calls += 1
        try:
            items = fetch(places[i]['g_place_id'], token, max_reviews, opener)
        except Exception as e:   # the type and HTTP code only: the request carries the token
            print(i, 'ERR', type(e).__name__, getattr(e, 'code', ''), flush=True)
            errors.append(i)
            continue
        if not items or (isinstance(items[0], dict) and items[0].get('error')):   # an empty answer is a failed run, not 'no reviews'
            print(i, 'ERR empty or error answer from the provider', flush=True)
            errors.append(i)
            continue
        Path(raw_dir).mkdir(exist_ok=True)
        (Path(raw_dir) / f'apify-{i}.json').write_text(json.dumps(items, ensure_ascii=False), encoding='utf-8')
        out[i] = trim(items)
        sleep(1)
    return calls, errors


def selftest():
    import tempfile, io
    cases = []
    class R:
        def __init__(s, data): s.d = data
        def __enter__(s): return s
        def __exit__(s, *a): return False
        def read(s): return json.dumps(s.d).encode()
    with tempfile.TemporaryDirectory() as td:
        places = {str(k): {'g_place_id': f'p{k}'} for k in range(5)}
        out = {}
        calls, errs = run(list(places), places, out, td, 'tok', 2, 10, opener=lambda req, timeout: R([{'stars': 5, 'text': 'x', 'publishedAtDate': '2030-01-01T00:00:00Z'}]), sleep=lambda s: None)
        cases.append(("the approved cap stops the run", calls == 2 and len(out) == 2 and (Path(td) / 'apify-0.json').exists()))
        calls, errs = run(list(places), places, out, td, 'tok', 5, 10, opener=lambda req, timeout: R([{'stars': 4, 'text': 'y', 'publishedAtDate': '2030-01-01T00:00:00Z'}]), sleep=lambda s: None)
        cases.append(("a resumed run skips places already done", calls == 3 and len(out) == 5))
        out2 = {}
        calls, errs = run(['0'], places, out2, td, 'tok', 5, 10, opener=lambda req, timeout: R([]), sleep=lambda s: None)
        cases.append(("an empty answer is an error, not 'done'", errs == ['0'] and out2 == {}))
        def boom(req, timeout): raise urllib.error.URLError('down')
        out2 = {}
        calls, errs = run(['0'], places, out2, td, 'tok', 5, 10, opener=boom, sleep=lambda s: None)
        cases.append(("a failed place is reported, not swallowed", errs == ['0'] and not out2))
    cases.append(("the caps are required arguments", all(f in Path(__file__).read_text(encoding='utf-8') for f in ("'--max-places', type=int, required=True", "'--max-reviews', type=int, required=True"))))
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


def main():
    if sys.argv[1:] == ['selftest']:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--max-places', type=int, required=True, help='how many places the user approved fetching in this run')
    ap.add_argument('--max-reviews', type=int, required=True, help='reviews per place the user approved (the provider bills per review)')
    ap.add_argument('--corpus', default='review_corpus.json'); ap.add_argument('--places', default='places4.json')
    ap.add_argument('--out', default='apify_reviews.json'); ap.add_argument('--raw-dir', default='raw')
    a = ap.parse_args()
    from travel_secrets import load_key
    token = load_key('APIFY_TOKEN')
    corpus, places = json.loads(Path(a.corpus).read_text(encoding='utf-8')), json.loads(Path(a.places).read_text(encoding='utf-8'))
    out = json.loads(Path(a.out).read_text(encoding='utf-8')) if Path(a.out).exists() else {}
    ids = [i for i in corpus if (places.get(i) or {}).get('g_place_id') and not places[i].get('cluster')]
    print(len(ids), 'places with a confident Google match;', len(out), 'done')
    calls, errors = run(ids, places, out, a.raw_dir, token, a.max_places, a.max_reviews)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False), encoding='utf-8')
    print('done', len(out), f'({calls} fetched this run)')
    if errors:
        print(f'FAIL: {len(errors)} place(s) could not be fetched ({errors[:5]}); the file is incomplete. Run again to resume.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
