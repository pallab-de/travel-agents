#!/usr/bin/env python
"""Rail distance validator: the nearest rail or metro station to each hotel, measured with Google Maps (Places for
the station, Distance Matrix for walking distance and time). The hotel's or operator's own figures are never the
source of a rail claim; they are compared against this and flagged when they disagree.

Usage (run from the trip folder; key comes from the encrypted env, never printed):
  python "<plugin root>/skills/hotel-dossier/scripts/rail_distance.py" hotels.json rail.json --city "City,City local name"
         [--line "Metro=metro station" --line "Rail=train station" ...]

--city   comma-separated CITY words (not the country), at least one of which a matched hotel's address must contain
         outside its last part, and the hotel's name must match the search result's (keeps a sister property or a
         same-named hotel in another city out). Required.
--line   label=search term, one per rail system to look for. Default: Metro=subway station, Rail=train station.
Fail-closed: a Google API error stops the run and writes nothing (an error is never recorded as "no station").

hotels.json: [{"id": "...", "name": "...", "query": "<hotel name> <city>", "operator_claim_m": 30 (optional)}]
Output per id: {"stations": [{"name", "line", "straight_m", "walk_m", "walk_min", "source", "captured_at"}],
                "nearest": {...} | null, "operator_claim_m": ..., "disagrees": bool | null} or {"not_found": reason}.
Captures are stored so the fact-guard gate can require a Maps capture for every distance claim.
"""
import argparse, json, math, sys, time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PLUGIN / 'tools'))
import google_maps as gm  # noqa: E402
from travel_secrets import load_key  # noqa: E402

DEFAULT_LINES = [("Metro", "subway station"), ("Rail", "train station")]
SEARCH_RADIUS_M = 2000
DISAGREE_RATIO = 0.25  # operator vs Maps walking distance difference beyond this is flagged
import google_places as gp  # noqa: E402  (the same name-similarity rule the Google dossier uses)

ApiStop = gm.MapsApiError


def checked(res, what):
    return gm.require_ok(res, what)


def haversine_m(a, b):
    (la1, lo1), (la2, lo2) = a, b
    r = 6371000.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = p2 - p1, math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def locality(address):
    """The address without its last comma-separated part (the country), so a country named in --city can't match."""
    parts = [p.strip() for p in str(address).split(',')]
    return ', '.join(parts[:-1]) if len(parts) > 1 else str(address)


def hotel_location(query, key, city_words, name=None):
    """The place that is BOTH this hotel (name similarity >= the dossier's threshold) and in the city (a city word in
    the address, country excluded). The best-named candidate wins, not the first search hit."""
    res = checked(gm.text_search(query, key), f"place search for {query!r}")
    best, best_s = None, 0.0
    for c in res.get('results', []):
        addr = locality(c.get('formatted_address', ''))
        if not (any(w.lower() in addr.lower() for w in city_words) and c.get('geometry')):
            continue
        sim = gp.similarity(name or query, c.get('name', '')) if name else 1.0
        if sim >= gp.MIN_NAME_SIMILARITY and sim > best_s:
            best, best_s = c, sim
    if best is not None:
        loc = best['geometry']['location']
        return (loc['lat'], loc['lng']), best.get('name')
    return None, None


def walking(origin, dest, key):
    res = checked(gm.distance_matrix([f"{origin[0]},{origin[1]}"], [f"{dest[0]},{dest[1]}"], key, mode='walking'), "walking distance")
    el = gm.matrix_element(res, "walking distance")   # raises when Google sent no element at all
    if el.get('status') in ('ZERO_RESULTS', 'NOT_FOUND'):
        return None, None   # no walking route: this station does not count
    if el.get('status') != 'OK':
        raise gm.MapsApiError(f"walking distance: element status {el.get('status')!r}")
    return el['distance']['value'], round(el['duration']['value'] / 60)


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # hotel names contain non-ASCII
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('hotels'); ap.add_argument('out_path'); ap.add_argument('--city', required=True)
    ap.add_argument('--line', action='append', default=[])
    a = ap.parse_args()
    lines = [tuple(x.split('=', 1)) for x in a.line] or DEFAULT_LINES
    city_words = [w.strip() for w in a.city.split(',') if w.strip()]
    hotels = json.loads(Path(a.hotels).read_text(encoding='utf-8'))
    key = load_key('GOOGLE_MAPS_API_KEY')
    out = {}
    try:
        for h in hotels:
            measure(h, key, lines, city_words, out)
    except ApiStop as e:
        sys.exit(f'FAIL {e}. Nothing written.')
    Path(a.out_path).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print('wrote', a.out_path)


def measure(h, key, lines, city_words, out):
    loc, matched = hotel_location(h['query'], key, city_words, h.get('name'))
    if loc is None:
        out[h['id']] = {'not_found': f'no Google place in {", ".join(city_words)} matched {h["query"]!r}'}
        print('NOT FOUND', h['name']); return
    stations = []
    for line, term in lines:
        res = checked(gm.text_search(f"{term}", key, lat=loc[0], lng=loc[1], radius=SEARCH_RADIUS_M), f"station search near {h['name']!r}")
        for c in res.get('results', []):
            if not c.get('geometry'):
                continue
            s_loc = (c['geometry']['location']['lat'], c['geometry']['location']['lng'])
            straight = haversine_m(loc, s_loc)
            if straight > SEARCH_RADIUS_M:
                continue
            # Only real transit stations: a shop named 'Metro Station' is not a station (types decides, not the name)
            # (transit_station alone is also given to bus and tram stops, so it is not enough; a bus station is never a rail station)
            types = set(c.get('types', []))
            if not types & {'subway_station', 'train_station', 'light_rail_station'} or 'bus_station' in types:
                continue
            stations.append({'name': c['name'], 'line': line, 'straight_m': round(straight), 'loc': s_loc})
    stations.sort(key=lambda x: x['straight_m'])
    seen, uniq = set(), []
    for s in stations:
        k = s['name'].lower()
        if k not in seen:
            seen.add(k); uniq.append(s)
    ts = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    measured = []
    for s in uniq[:6]:
        walk_m, walk_min = walking(loc, s['loc'], key)
        if walk_m is None:
            continue
        measured.append({'name': s['name'], 'line': s['line'], 'straight_m': s['straight_m'],
                         'walk_m': walk_m, 'walk_min': walk_min,
                         'source': 'Google Maps Distance Matrix (walking) + Places station', 'captured_at': ts})
    measured.sort(key=lambda x: x['walk_m'])
    nearest = measured[0] if measured else None
    claim = h.get('operator_claim_m')
    disagrees = None
    if nearest and claim:
        disagrees = abs(nearest['walk_m'] - claim) / max(claim, 1) > DISAGREE_RATIO
    out[h['id']] = {'matched_place': matched, 'stations': measured, 'nearest': nearest,
                    'operator_claim_m': claim, 'disagrees': disagrees} if nearest else \
                   {'not_found': 'no rail station within 2 km confirmed by Maps walking distance'}
    print('OK' if nearest else 'NO RAIL', h['name'], (nearest['name'], nearest['walk_m'], nearest['walk_min']) if nearest else '',
          'DISAGREES with the operator figure' if disagrees else '')
    time.sleep(0.2)


def selftest():
    import contextlib, io
    cases = []
    real = {'ts': gm.text_search, 'dm': gm.distance_matrix}
    p = lambda name, addr, lat=1.0, lng=2.0, types=None: {'name': name, 'formatted_address': addr, 'geometry': {'location': {'lat': lat, 'lng': lng}}, 'types': types or []}
    try:
        gm.text_search = lambda q, key, **k: {'status': 'OK', 'results': [p('Sister Hotel', '5 Rd, Valmora, Country'), p('Harbour Hotel', '1 Quay, Valmora, Country')]}
        cases.append(('the hotel by name is used, not the first hit', hotel_location('Harbour Hotel Valmora', 'k', ['Valmora'], 'Harbour Hotel')[1] == 'Harbour Hotel'))
        gm.text_search = lambda q, key, **k: {'status': 'OK', 'results': [p('Harbour Hotel', '9 Rd, Selmere, Country')]}
        cases.append(('a result in another city of the same country is rejected', hotel_location('Harbour Hotel', 'k', ['Valmora', 'Country'], 'Harbour Hotel')[0] is None))
        stations = {'status': 'OK', 'results': [p('Metro Station Shop', 'x, Valmora, Country', 1.0, 2.001, ['store']),
                                                 p('Central', 'x, Valmora, Country', 1.0, 2.002, ['subway_station'])]}
        calls = []
        def ts(q, key, **k):
            calls.append(q)
            return {'status': 'OK', 'results': [p('Harbour Hotel', '1 Quay, Valmora, Country')]} if 'Harbour' in q else stations
        gm.text_search = ts
        gm.distance_matrix = lambda o, d, key, **k: {'status': 'OK', 'rows': [{'elements': [{'status': 'OK', 'distance': {'value': 300}, 'duration': {'value': 240}}]}]}
        out = {}
        with contextlib.redirect_stdout(io.StringIO()):
            measure({'id': 'h', 'name': 'Harbour Hotel', 'query': 'Harbour Hotel Valmora', 'operator_claim_m': 100}, 'k', [('Metro', 'subway station')], ['Valmora'], out)
        cases.append(('a shop named Metro Station is ignored; the real station is measured', out['h']['nearest']['name'] == 'Central'))
        cases.append(('an operator figure far from the Maps walk is flagged', out['h']['disagrees'] is True))
        stations['results'] = [p('Central Bus Stop', 'x, Valmora, Country', 1.0, 2.001, ['transit_station', 'bus_station']),
                               p('Tram Halt', 'x, Valmora, Country', 1.0, 2.002, ['light_rail_station', 'transit_station'])]
        out2 = {}
        with contextlib.redirect_stdout(io.StringIO()):
            measure({'id': 'h', 'name': 'Harbour Hotel', 'query': 'Harbour Hotel Valmora'}, 'k', [('Metro', 'subway station')], ['Valmora'], out2)
        cases.append(('a bus stop is not a rail station; a light-rail halt is', out2['h']['nearest']['name'] == 'Tram Halt'))
        gm.text_search = lambda q, key, **k: {'status': 'OVER_QUERY_LIMIT'}
        try:
            measure({'id': 'h', 'name': 'Harbour Hotel', 'query': 'q'}, 'k', [('Metro', 'x')], ['Valmora'], {})
            cases.append(('an API error stops the run', False))
        except ApiStop:
            cases.append(('an API error stops the run', True))
    finally:
        gm.text_search, gm.distance_matrix = real['ts'], real['dm']
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


if __name__ == '__main__':
    if sys.argv[1:] == ['selftest']:
        raise SystemExit(selftest())
    main()
