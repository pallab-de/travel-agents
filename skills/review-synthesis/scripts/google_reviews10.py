"""Official Places API only: up to ~10 distinct reviews per place (5 newest + 5 most relevant). No scraping.

  python google_reviews10.py --rows report_data.json --places places.json --out gr10.json [--limit 45]

--rows    JSON with a "rows" list (or a bare list); each row has an "id".
--places  JSON map: row id -> {"g_place_id", "g_rating", "g_n"} (from place-matching).
--out     where to write {row id: {place_id, rating, n, reviews[]}}.

Fail-closed (tools/google_maps.py's require_ok): every Places response must say status OK (or ZERO_RESULTS for a place with
no reviews). Any other status (OVER_QUERY_LIMIT, REQUEST_DENIED, INVALID_REQUEST, UNKNOWN_ERROR, HTTP errors, network errors) stops the
run with exit 1 and writes NOTHING. Zero saved reviews with exit 0 would read as
"no reviews exist" in the report.
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))   # <plugin_root>/tools, shared across skills
import google_maps as gm  # noqa: E402
from travel_secrets import load_key  # noqa: E402


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def fetch(place_id, sort, key):
    """One sort's reviews for a place. Raises on any status but OK / ZERO_RESULTS."""
    data = gm.require_ok(gm.place_details(place_id, key, "reviews,rating,user_ratings_total", reviews_sort=sort),
                         f"Places reviews for {place_id} ({sort})")
    return data.get('result', {}).get('reviews') or []


def main():
    if sys.argv[1:] == ['selftest']:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rows', required=True); ap.add_argument('--places', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--limit', type=int, default=45, help='how many rows (in list order) to fetch')
    a = ap.parse_args()
    key = load_key("GOOGLE_MAPS_API_KEY")
    data = json.loads(Path(a.rows).read_text(encoding='utf-8'))
    rows = (data['rows'] if isinstance(data, dict) else data)[:a.limit]
    places = json.loads(Path(a.places).read_text(encoding='utf-8'))
    out = {}
    try:
        for r in rows:
            m = places.get(r['id'])
            if not m or not m.get('g_place_id'):
                continue
            revs = {}
            for sort in ('newest', 'most_relevant'):
                for x in fetch(m['g_place_id'], sort, key):
                    k = (x.get('author_name'), x.get('time'))
                    revs[k] = {'stars': x.get('rating'), 'when': x.get('relative_time_description'), 'time': x.get('time'),
                               'text': (x.get('text') or '').strip()}
            out[r['id']] = {'place_id': m['g_place_id'], 'rating': m.get('g_rating'), 'n': m.get('g_n'),
                            'reviews': sorted(revs.values(), key=lambda v: -(v['time'] or 0))}
    except (gm.MapsApiError, RuntimeError) as e:
        print(f'STOPPED: {e}. Nothing was written. Fix the key or quota, then re-run.')
        return 1
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    total = sum(len(v['reviews']) for v in out.values())
    print(len(out), 'places;', total, 'reviews;', 'avg per place', round(total / max(1, len(out)), 1))
    return 0


def selftest():
    calls = []
    def fake(place_id, key, fields, reviews_sort=None):
        calls.append(reviews_sort)
        return {'status': 'OK', 'result': {'reviews': [{'author_name': 'a', 'time': 1, 'rating': 5, 'text': ' fine '}]}}
    real, gm.place_details = gm.place_details, fake
    try:
        got = fetch('p1', 'newest', 'k')
        denied = {'status': 'REQUEST_DENIED', 'error_message': 'bad key'}
        gm.place_details = lambda *a, **k: denied
        try:
            fetch('p1', 'newest', 'k'); raised = False
        except gm.MapsApiError:
            raised = True
        gm.place_details = lambda *a, **k: {'status': 'ZERO_RESULTS'}
        empty = fetch('p1', 'newest', 'k')
    finally:
        gm.place_details = real
    cases = [('a good response returns its reviews and passes the sort on', len(got) == 1 and calls == ['newest']),
             ('REQUEST_DENIED raises instead of reading as no reviews', raised),
             ('ZERO_RESULTS is an empty list', empty == [])]
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
