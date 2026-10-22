#!/usr/bin/env python
"""Distance from any set of origins to any set of named places, measured by Google Maps. Generic: no trip,
city or subject is built in. Rail stations, landmarks, offices, airports: anything you can name.

  python -B "<plugin root>/tools/place_distance.py" origins.json destinations.json measured.json [--modes walking,driving]

origins.json       {"<origin id>": {"lat": 10.00, "lng": 20.00}, ...}, or a name resolved with the same
                   city guard as the destinations: {"<origin id>": {"query": "...", "require_in": ["Valmora", "Valmora City"]}}
destinations.json  [{"id": "old-fort", "query": "Old Fort Valmora", "near": [10.02, 20.01],
                     "require_in": ["Valmora", "Valmora City"]}, ...]
                   "near" biases the name search to a point; "lodging": true lets Google's "Hotel" / "Resort" suffix match; "require_in" is a word, or a list of
                   words, one of which the matched address must contain (a same-named place elsewhere is refused).
                   Google may write a city by its local name in some addresses and its common name in others:
                   list both.
measured.json      {"<origin id>": {"<destination id>": {"<mode>": {"name", "place_id", "mode", "metres", "minutes",
                     "value", "url", "source", "captured_at"} | {"not_found": "<reason>"}}}}  (a destination that could
                     not be resolved is {"not_found": "<reason>"} in place of the mode level)
                   "value" is the fact text ("1,250 m walk, 16 min (Google Maps)") ready for a spec fact and
                   for `pipeline.py capture`. "url" is the Maps directions link the capture is stored against.

Fail-closed: a non-OK top-level status (OVER_QUERY_LIMIT, REQUEST_DENIED, ...) or a non-OK element status stops
the run with exit 1 and writes nothing, or records a named reason for that one pair. A name with no match in
the required city is recorded as not_found, never measured against a different place.
The API key is read through tools/travel_secrets.py and never printed.
"""
import argparse, datetime as dt, json, sys, urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import google_maps as gm  # noqa: E402
import textnorm  # noqa: E402
from travel_secrets import load_key  # noqa: E402

MODES = {'walking': 'walk', 'driving': 'drive', 'transit': 'transit', 'bicycling': 'bike'}
MAPS_DIR = 'https://www.google.com/maps/dir/?api=1'


class ApiStop(Exception):
    """A top-level API failure: the whole run stops, because every later answer would be suspect."""


def utc_now():
    return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


GENERIC_WORDS = {'the', 'of', 'and', 'de', 'la', 'le', 'el', 'hotel', 'restaurant', 'cafe', 'old', 'new', 'city', 'central', 'grand', 'royal', 'national'}


PLACE_TYPES = {'fort', 'park', 'palace', 'temple', 'museum', 'lake', 'beach', 'mall', 'market', 'church', 'mosque', 'station', 'airport'}
LODGING_WORDS = {'hotel', 'resort', 'hostel', 'lodge', 'inn', 'guesthouse', 'homestay', 'residency', 'suites', 'cottage', 'apartments', 'villa'}
TYPE_WORDS = LODGING_WORDS | {'restaurant', 'cafe', 'bar', 'bakery', 'store', 'shop', 'bistro', 'pub', 'club', 'spa', 'salon', 'dhaba', 'sweets', 'boutique', 'kitchen', 'canteen', 'diner', 'grill', 'lounge', 'tours', 'travels', 'taxi', 'cabs'}
DESCRIPTORS = {'lake', 'fort', 'palace', 'temple', 'museum', 'village', 'crafts', 'garden', 'gardens', 'mall', 'market', 'beach', 'falls', 'zoo'}   # words a shorter official name may leave out   # a different kind of place with the wanted place's name in it


def name_matches(wanted, found, ignore=(), lodging=False):
    """The place Google returned is the one asked for: the wanted name's distinctive words are in the returned name (all of them for a
    short name, three quarters of a long one). Text search also returns related places for an obscure or misspelled name, so a cafe is
    not measured in place of a fort ('Marrow Fort' is not 'Fort Cafe') and 'Park Hyatt' is not 'Hyatt Regency'."""
    skip = GENERIC_WORDS | {w for x in ignore for w in textnorm.fold(x).split()}   # the city and filler words are not what identifies a place
    wa, wb = set(textnorm.fold(wanted).split()), set(textnorm.fold(found).split())
    a, b = wa - skip, wb - skip
    if not a:   # a wanted name made only of filler and city words ("The Grand Hotel", "Old City"): compare it whole
        a, b = wa, wb
    extra = (wb & TYPE_WORDS) - wa - (LODGING_WORDS if lodging else set())   # a destination that is a lodging may carry hotel / resort in Google's name
    if not (a and b) or extra:
        return False   # a hotel or cafe that merely carries the wanted name, unless every one of two or more wanted words is in it
    if b < a and (a - b) <= DESCRIPTORS and (b - DESCRIPTORS) and (len(b) >= 2 or len(a - b) >= 2):
        return True   # Google's shorter name for a place: 'Marlow Reach' for 'Marlow Reach Lake'
    if len(a) <= 2:
        return a <= b   # every distinctive word of a short name must be in the found name ('Park Hyatt' is not 'Hyatt Hotel')
    return len(a & b) / len(a) >= 0.75


def resolve(dest, key):
    """Name -> place (place_id, lat, lng, name, address), or a not_found reason. The returned name must match the one asked
    for (`name` in the destination, else its `query`), unless the destination sets `accept_first`."""
    kw = {}
    if dest.get('near'):
        kw = {'lat': dest['near'][0], 'lng': dest['near'][1], 'radius': dest.get('radius', 20000)}
    res = gm.text_search(dest['query'], key, **kw)
    if res.get('status') not in ('OK', 'ZERO_RESULTS'):
        raise ApiStop(f"Places status {res.get('status')} resolving {dest['id']}: {res.get('error_message', '')}".strip())
    need = dest.get('require_in') or []
    need = [need] if isinstance(need, str) else need
    closest = None
    for c in res.get('results', []):
        addr = c.get('formatted_address', '')
        if need and not any(str(w).lower() in addr.lower() for w in need):
            continue
        if not c.get('geometry'):
            continue
        if not dest.get('accept_first') and not name_matches(dest.get('name') or dest['query'], c.get('name', ''), need, bool(dest.get('lodging'))):
            closest = closest or c.get('name')
            continue
        loc = c['geometry']['location']
        return {'place_id': c.get('place_id'), 'lat': loc['lat'], 'lng': loc['lng'], 'name': c.get('name'), 'address': addr}
    return {'not_found': f"no place named {dest['query']!r}" + (f" with {need!r} in its address" if need else '')
            + (f" (the closest result, {closest!r}, is a different place; set accept_first to measure it anyway)" if closest else '')}


def measure(origin, place, mode, key):
    """One origin -> one place, one mode. Returns a record; raises ApiStop on a top-level failure."""
    res = gm.distance_matrix([f"{origin['lat']},{origin['lng']}"], [f"{place['lat']},{place['lng']}"], key, mode=mode)
    try:
        el = gm.matrix_element(res, 'Distance Matrix')
    except gm.MapsApiError as e:
        raise ApiStop(str(e))
    if el.get('status') != 'OK':
        return {'not_found': f"Distance Matrix element status {el.get('status')} ({mode})"}
    metres, minutes = el['distance']['value'], round(el['duration']['value'] / 60)
    shown = origin.get('public_label') or f"{origin['lat']},{origin['lng']}"   # a home origin sets public_label: its coordinates stay out of the stored link
    url = (f"{MAPS_DIR}&origin={urllib.parse.quote(str(shown))}&destination=place_id:{place['place_id']}"
           f"&travelmode={mode}")
    return {'name': place['name'], 'place_id': place['place_id'], 'mode': mode, 'metres': metres, 'minutes': minutes,
            'value': f"{metres:,} m {MODES.get(mode, mode)}, {minutes} min (Google Maps)", 'url': url,
            'source': f'Google Maps Distance Matrix ({mode})', 'captured_at': utc_now()}


def locate(origins, key):
    """An origin is coordinates, or a name resolved the same way as a destination (same city guard)."""
    out = {}
    for oid, o in origins.items():
        got = o if 'lat' in o else resolve({'id': oid, **o}, key)
        if o.get('public_label') and 'not_found' not in got:
            got = {**got, 'public_label': o['public_label']}   # a home origin given by name keeps its public label too
        if 'not_found' in got:
            got = {'not_found': f"origin {oid!r} could not be resolved"}   # never echo the query: it may be a home address
        out[oid] = got
    return out


def run(origins, destinations, modes, key):
    places = {d['id']: resolve(d, key) for d in destinations}
    out = {}
    for oid, o in locate(origins, key).items():
        if 'not_found' in o:
            out[oid] = o
            continue
        out[oid] = {}
        for d in destinations:
            p = places[d['id']]
            if 'not_found' in p:
                out[oid][d['id']] = p
                continue
            for m in modes:
                out[oid].setdefault(d['id'], {})
                rec = measure(o, p, m, key)
                out[oid][d['id']][m] = rec
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('origins'); ap.add_argument('destinations'); ap.add_argument('out')
    ap.add_argument('--modes', default='walking', help='comma list of walking, driving, transit, bicycling')
    a = ap.parse_args()
    modes = [m.strip() for m in a.modes.split(',') if m.strip()]
    bad = [m for m in modes if m not in MODES]
    if bad:
        print(f'refused: unknown mode(s) {bad}; use {sorted(MODES)}')
        return 1
    origins = json.loads(Path(a.origins).read_text(encoding='utf-8'))
    destinations = json.loads(Path(a.destinations).read_text(encoding='utf-8'))
    key = load_key('GOOGLE_MAPS_API_KEY')
    try:
        out = run(origins, destinations, modes, key)
    except ApiStop as e:
        print(f'STOPPED: {e}. Nothing was written.')
        return 1
    n = sum(1 for o in out.values() if isinstance(o, dict) for d in o.values() if isinstance(d, dict) and 'not_found' not in d
            for m in d.values() if isinstance(m, dict) and 'metres' in m)
    nf = sum(1 for o in out.values() if isinstance(o, dict) for d in ([o] if 'not_found' in o else o.values()) if isinstance(d, dict) and 'not_found' in d)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{n} measurement(s) written to {a.out}; {nf} destination(s) not found (see the reasons in the file)')
    return 0


def selftest():
    cases = []
    real = gm.text_search
    p = lambda name: {'name': name, 'formatted_address': '1 Rd, Valmora, Country', 'place_id': 'x', 'geometry': {'location': {'lat': 1.0, 'lng': 2.0}}}
    try:
        gm.text_search = lambda q, key, **k: {'status': 'OK', 'results': [p('Corner Cafe')]}
        r = resolve({'id': 'd', 'query': 'Old Fort Valmora'}, 'k')
        cases.append(("a different place for an obscure name is not measured", 'not_found' in r and 'Corner Cafe' in r['not_found']))
        cases.append(("accept_first measures it anyway", 'not_found' not in resolve({'id': 'd', 'query': 'Old Fort Valmora', 'accept_first': True}, 'k')))
        gm.text_search = lambda q, key, **k: {'status': 'OK', 'results': [p('Corner Cafe'), p('Old Fort')]}
        cases.append(("the right place is found past a wrong first hit", resolve({'id': 'd', 'query': 'Old Fort Valmora', 'require_in': ['Valmora']}, 'k').get('name') == 'Old Fort'))
    finally:
        gm.text_search = real
    cases.append(("name_matches reads accents and word order", name_matches('Caf\u00e9 du Port', 'cafe du port') and not name_matches('Old Fort', 'Corner Cafe')))
    cases.append(("a shared city or filler word is not a match", not name_matches('Marrow Fort Valmora', 'Valmora Zoo', ['Valmora'])
                  and not name_matches('Old Fort Valmora', 'Valmora Cafe', ['Valmora']) and name_matches('Marrow Fort Valmora', 'Marrow Fort', ['Valmora'])))
    cases.append(("a landmark is not the town or business that shares its bare name; a shorter official name still matches",
                  not any(name_matches(a, b) for a, b in [('Dunmere Fort', 'Dunmere'), ('Halvey Palace', 'Halvey'), ('Mirabel', 'Mirabel Bakery'), ('Mirabel', 'Hotel Mirabel'), ('Park Hyatt', 'Hyatt Hotel')])
                  and name_matches('Marlow Reach Lake', 'Marlow Reach') and name_matches('Orla Verde', 'Orla Verde Hotel', lodging=True)))
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


if __name__ == '__main__':
    if sys.argv[1:] == ['selftest']:
        raise SystemExit(selftest())
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
