#!/usr/bin/env python
"""Google dossier collector: match each hotel to its Google place and record rating, review count, and the
reviews Google returns (Places API Place Details returns up to 5 per place; this is an API limit, and the
dossier says so).

Never guesses. A hotel with no confident name match gets {"not_found": "<reason>"}, which the dossier
completeness gate then reports, rather than a wrong place's rating.

Usage (run from the trip folder; the key comes from the encrypted env, never printed):
  python "<plugin root>/skills/hotel-dossier/scripts/google_places.py" hotels.json google.json [--require-in Valmora,Valmora City]

hotels.json: [{"id": "...", "name": "Harbour Hotel", "query": "Harbour Hotel Valmora", "lat": 10.00, "lng": 20.00,
               "require_in": ["Valmora"]}, ...]
A place is accepted only if its address contains one of the "require_in" words (per hotel, or the global
--require-in list), so a same-named place in another city is refused. A hotel with neither is an error.
Environment: TRAVEL_SECRETS_DIR may point at an encrypted-env folder (see tools/travel_secrets.py).
"""
import difflib, json, re, sys, time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PLUGIN / 'tools'))
from textnorm import fold  # noqa: E402
import google_maps as gm  # noqa: E402
from travel_secrets import load_key  # noqa: E402

FIELDS = "place_id,name,formatted_address,geometry,rating,user_ratings_total,reviews,url,website,business_status"
MIN_NAME_SIMILARITY = 0.75


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def similarity(a, b):
    na, nb = fold(a), fold(b)
    return difflib.SequenceMatcher(None, na, nb).ratio() if na and nb else 0.0   # an empty name matches nothing


def collect(hotels, key, glob_req=()):
    """Match each hotel to its Google place. Raises gm.MapsApiError on any Google error, before anything is written."""
    out = {}
    for h in hotels:
        name = h.get('name') or h['query']
        req = h.get('require_in') or list(glob_req)
        res = gm.require_ok(gm.text_search(h['query'], key, lat=h.get('lat'), lng=h.get('lng'), radius=5000 if h.get('lat') else None),
                            f"place search for {name!r}")
        best, best_s = None, 0.0
        for c in res.get('results', []):
            s = similarity(name, c.get('name', ''))
            addr = c.get('formatted_address', '')
            if s > best_s and any(w.lower() in addr.lower() for w in req):
                best, best_s = c, s
        if best is None or best_s < MIN_NAME_SIMILARITY:
            out[h['id']] = {'not_found': f"no Google place with name similarity >= {MIN_NAME_SIMILARITY} (best {best_s:.2f})"}
            print('NOT FOUND', name)
            continue
        d = gm.require_ok(gm.place_details(best['place_id'], key, FIELDS), f"place details for {name!r}", allow=("OK",)).get('result', {})
        out[h['id']] = {
            'match': {'place_id': best['place_id'], 'name': d.get('name'), 'address': d.get('formatted_address'),
                      'similarity': round(best_s, 2)},
            'rating': d.get('rating'), 'count': d.get('user_ratings_total'),
            'reviews': [{'author': r.get('author_name'), 'rating': r.get('rating'), 'time': r.get('time'),
                         'relative': r.get('relative_time_description'), 'text': r.get('text')}
                        for r in d.get('reviews', [])],
            'url': d.get('url'), 'website': d.get('website'), 'status': d.get('business_status'),
            'source': 'Google Places API, Place Details (reviews capped at 5 by the API)',
            'captured_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        }
        print('OK', name, '->', d.get('name'), d.get('rating'), d.get('user_ratings_total'), f'sim={best_s:.2f}')
        time.sleep(0.2)
    return out


def main():
    args = sys.argv[1:]
    glob_req = []
    if '--require-in' in args:
        i = args.index('--require-in')
        glob_req = [w.strip() for w in args[i + 1].split(',') if w.strip()]
        del args[i:i + 2]
    if len(args) != 2:
        sys.exit(__doc__)
    hotels = json.loads(Path(args[0]).read_text(encoding='utf-8'))
    bad = [h.get('id') for h in hotels if not h.get('id') or not (h.get('name') or h.get('query'))]
    if bad:
        sys.exit(f"every hotel needs an id and a name or query; check {bad or 'the entries without an id'}")
    missing = [h.get('id') for h in hotels if not (h.get('require_in') or glob_req)]
    if missing:
        sys.exit(f"no city guard for {missing}: give each hotel a require_in list or pass --require-in")
    try:
        out = collect(hotels, load_key('GOOGLE_MAPS_API_KEY'), glob_req)
    except gm.MapsApiError as e:
        sys.exit(f"FAIL {e}. Nothing written.")
    Path(args[1]).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print('wrote', args[1])


def selftest():
    import contextlib, io, tempfile
    cases = []
    place = lambda name, addr: {'name': name, 'formatted_address': addr, 'place_id': 'p1'}
    real = {'ts': gm.text_search, 'pd': gm.place_details}
    try:
        hotels = [{'id': 'h', 'name': 'Harbour Hotel', 'query': 'Harbour Hotel Valmora', 'require_in': ['Valmora']}]
        det = {'status': 'OK', 'result': {'name': 'Harbour Hotel', 'rating': 4.4, 'user_ratings_total': 120, 'reviews': []}}
        gm.place_details = lambda *a, **k: det
        gm.text_search = lambda *a, **k: {'status': 'OK', 'results': [place('Harbour Hotel', '1 Quay, Valmora, Country')]}
        with contextlib.redirect_stdout(io.StringIO()):
            ok = collect(hotels, 'k')
        cases.append(('two different Japanese names are not similar', similarity('\u6771\u6a2a\u30a4\u30f3\u4eac\u90fd\u99c5', '\u30db\u30c6\u30eb\u30b0\u30e9\u30f3\u30f4\u30a3\u30a2\u4eac\u90fd') < 0.75))
        cases.append(('a close name in the right city is matched', 'match' in ok['h'] and ok['h']['rating'] == 4.4))
        gm.text_search = lambda *a, **k: {'status': 'OK', 'results': [place('Sunrise Palace', '1 Quay, Valmora, Country')]}
        with contextlib.redirect_stdout(io.StringIO()):
            nf = collect(hotels, 'k')
        cases.append(('a name below the similarity threshold is not_found', 'not_found' in nf['h']))
        gm.text_search = lambda *a, **k: {'status': 'OK', 'results': [place('Harbour Hotel', '9 Road, Selmere, Country')]}
        with contextlib.redirect_stdout(io.StringIO()):
            wc = collect(hotels, 'k')
        cases.append(('a same-named hotel in another city is refused', 'not_found' in wc['h']))
        gm.text_search = lambda *a, **k: {'status': 'REQUEST_DENIED', 'error_message': 'key invalid'}
        try:
            collect(hotels, 'k')
            cases.append(('an API error stops the run', False))
        except gm.MapsApiError as e:
            cases.append(('an API error stops the run', 'REQUEST_DENIED' in str(e)))
    finally:
        gm.text_search, gm.place_details = real['ts'], real['pd']
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


if __name__ == '__main__':
    if sys.argv[1:] == ['selftest']:
        raise SystemExit(selftest())
    main()
