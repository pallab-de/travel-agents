"""Robustness test of place_match.py in other countries with GROUND TRUTH: real lodgings from Google are given an Airbnb-style title and coordinates
jittered up to 1.2 km (Airbnb obfuscates locations); the matcher must return the same place_id. Negative cases (a listing titled like one lodging, pinned at a different lodging nearby with a different name) must not match
that other lodging. Exits 1 on any wrong match, any wrong negative, any wrong rename, any API error or no cases at all.
Run: python live_match_check.py [seed]   (calls the Google Places API and spends quota; needs GOOGLE_MAPS_API_KEY)"""
import json, random, math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))
import place_match as pm
import google_maps as gm
CITIES = [('Lisbon, Portugal', 'guest house'), ('Florence, Italy', 'agriturismo villa'), ('Kyoto, Japan', 'ryokan guest house'), ('Marrakech, Morocco', 'riad'),
          ('Tulum, Mexico', 'boutique hotel villa'), ('Ubud, Bali', 'villa'), ('Cape Town, South Africa', 'guest house'), ('Reykjavik, Iceland', 'guesthouse'),
          ('Paris, France', 'boutique hotel'), ('Berlin, Germany', 'pension apartment'), ('Sydney, Australia', 'holiday house'), ('Nashville, USA', 'inn cottage')]
TITLES = ['{n} | Private pool & garden | 3BR', 'Charming {n} with views', '{n} - entire home by the beach', 'Cosy {n}, 2 bedrooms, near centre', '{n} by Sunrise Hosts']

import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def jitter(lat, lng, km=1.2):
    r = km * math.sqrt(random.random()); th = random.random() * 2 * math.pi
    return lat + (r * math.cos(th)) / 111.0, lng + (r * math.sin(th)) / (111.0 * math.cos(lat * math.pi / 180))

def main():
    if sys.argv[1:] in (['-h'], ['--help']):
        print(__doc__)
        return 0
    random.seed(int(sys.argv[1]) if len(sys.argv) > 1 else 7)

    cases = []
    for city, kind in CITIES:
        res = gm.require_ok(gm.text_search(f"{kind} in {city}", pm.load_key('GOOGLE_MAPS_API_KEY')), f"search in {city}")
        ok = [p for p in res.get('results', []) if (p.get('user_ratings_total') or 0) >= 5 and 'lodging' in (p.get('types') or [])][:4]
        for p in ok:
            g = p['geometry']['location']; cases.append({'city': city, 'truth': p['place_id'], 'name': p['name'], 'll': jitter(g['lat'], g['lng']), 'true_ll': (g['lat'], g['lng'])})
    print(len(cases), 'positive cases in', len(CITIES), 'cities')
    if not cases:
        sys.exit('FAIL: no positive cases were built (an API problem?); nothing was tested')
    right = wrong = abst = 0; rows = []
    for k, c in enumerate(cases):
        title = TITLES[k % len(TITLES)].format(n=c['name'])
        m = pm.match(title, c['ll'])
        if not m: abst += 1; rows.append(('ABSTAIN', c['city'], title[:60], c['name'][:30], None))
        elif m['g_place_id'] == c['truth']: right += 1
        else: wrong += 1; rows.append(('WRONG', c['city'], title[:60], c['name'][:30], m['g_name']))
        if k % 10 == 9: pm.save_cache()
    print(f'POSITIVE: right {right}, wrong {wrong}, abstained {abst} of {len(cases)}  (precision {right / max(right + wrong, 1):.2f}, recall {right / len(cases):.2f})')
    for r in rows: print(r)
    # negatives: this case's title, pinned at a DIFFERENT lodging in the same city (< 2 km away, no shared name word). The matcher must
    # not return that other lodging: the name differs, and only the pin is close.
    neg_wrong = 0; neg = 0
    for k, c in enumerate(cases[:20]):
        near = gm.require_ok(gm.nearby_search(c['true_ll'][0], c['true_ll'][1], 2000, pm.load_key('GOOGLE_MAPS_API_KEY'), 'lodging'), 'nearby lodging').get('results', [])
        mine = set(pm.toks(c['name']))
        other = next((p for p in near if p.get('place_id') != c['truth'] and not (set(pm.toks(p.get('name', ''))) & mine) and p.get('geometry')), None)
        if other is None: continue
        og = other['geometry']['location']
        neg += 1; m = pm.match(TITLES[k % len(TITLES)].format(n=c['name']), (og['lat'], og['lng']))
        if m and m['g_place_id'] == other['place_id']: neg_wrong += 1; print('NEG-FALSE-MATCH', c['name'][:30], '->', m['g_name'], m['sim'])
    print(f'NEGATIVE: false matches {neg_wrong} of {neg}')
    # renamed / re-spelled at an EXACT pin (a direct-booking site's own coordinates): the title keeps only the place's rarest word plus a different second
    # word, or a one-letter-off spelling. Correct match or abstain are both fine; a WRONG match is the failure this guards (the same-pin anchor paths).
    ren_right = ren_wrong = ren_abst = 0
    for k, c in enumerate(cases):
        words = sorted((w for w in pm.toks(c['name']) if w not in pm.UNIVERSAL and len(w) >= 5), key=len, reverse=True)
        if not words: continue
        g = c['true_ll']   # the real pin: a direct-booking site's own coordinates, unlike the jittered ones above
        title = f"{words[0].capitalize()} Springs" if k % 2 == 0 else c['name'].replace(words[0], words[0][:-1] + ('x' if words[0][-1] != 'x' else 'z'), 1)
        m = pm.match(title, g, max_km=0.5)
        if not m: ren_abst += 1
        elif m['g_place_id'] == c['truth']: ren_right += 1
        else: ren_wrong += 1; print('RENAME-WRONG', title, '->', m['g_name'], m.get('renamed'), m['evidence'])
    print(f'RENAMED-AT-EXACT-PIN: right {ren_right}, wrong {ren_wrong}, abstained {ren_abst}')
    print('calls', pm.CALLS); pm.save_cache()
    if wrong or neg_wrong or ren_wrong or not neg:
        print(f'FAIL: wrong {wrong}, negative false matches {neg_wrong}, rename wrong {ren_wrong}' + ('' if neg else ' and no negative case could be built'))
        sys.exit(1)
    print('PASS: no wrong matches')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
