"""TEMPLATE for a direct-booking pipeline: take a direct-booking site's own listing pages (scraped with Playwright into the
two files below), attach a Google Maps match (place_match.py) and traffic-aware drive times, apply the hard filters, and
write mb_final.json. The prefix "mb_" means "direct-booking site", whatever the site.

Inputs, in the project folder (a file may hold the JSON text itself or that text as a JSON string):
  mb_cards.json        [{"href", "name", "area", "guests", "rooms", "baths"}]   one per listing, from the site's list page
  mb_detail2_raw.json  {href: {"lat", "lng", "night1", "night2", "night3", "rating", "n", "revs", "amen", "err"}}   from each
                       detail page: coordinates; night1 and night2, the nightly prices of the two stay nights (summed into the
                       total used for the budget) and night3, a third night shown for reference and not counted; rating,
                       review count, review texts; amenity names; "err" if the page failed.
A new site needs its own scrape into this shape and its own SITE_BASE; the matching, drive-time and filter logic stays.
--require-amenity NAME keeps only listings whose amenity list contains NAME (case-insensitive; repeat for several).

Run (from the project folder that holds mb_cards.json/mb_detail2_raw.json):
  python "<plugin root>/skills/report-builder/scripts/direct_booking_pipeline.py" \
      --home "<home base address>, <city>" \
      --out-departure <YYYY-MM-DDTHH:MM> --back-departure <YYYY-MM-DDTHH:MM> --tz <UTC offset, e.g. +01:00> \
      --budget <total budget, optional> --max-drive <minutes> [--require-amenity pool]
"""
import argparse, json, sys
from pathlib import Path
N = Path.cwd()
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))   # <plugin_root>/tools/travel_secrets.py, shared across skills
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'skills' / 'place-matching' / 'scripts'))   # place_match.py lives in the place-matching skill
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'skills' / 'listing-search' / 'scripts'))   # drive_times.py lives in the listing-search skill
import place_match as pm         # noqa: E402
import drive_times as dt         # noqa: E402  (reuses its matrix() helper and parse_tz/epoch)
from travel_secrets import load_key  # noqa: E402
from jsonfile import load_json  # noqa: E402


SITE_BASE = 'https://<direct-booking-site-domain>'   # the site's own domain; listing paths are appended to it
EXACT_PIN_KM = 0.5   # a direct-booking site's own coordinates are exact, unlike Airbnb's deliberately jittered pins - a Google place 2 km away is a different business


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--home'); ap.add_argument('--out-departure'); ap.add_argument('--back-departure')   # required unless --rematch-only
    ap.add_argument('--tz', default=None); ap.add_argument('--budget', type=int, default=None, help='Optional total budget; omit for no budget filter'); ap.add_argument('--max-drive', type=int, required=True)
    ap.add_argument('--require-amenity', action='append', default=[], help='keep only listings with this amenity (repeatable)')
    ap.add_argument('--rematch-only', action='store_true', help='Reuse the drive times already in mb_final.json and redo ONLY the Google matching. Use this after improving place_match.py, so a matcher fix reaches this list without re-querying drive times.')
    a = ap.parse_args()
    if a.rematch_only:
        rows = json.load(open(N / 'mb_final.json', encoding='utf-8'))
        for r in rows.values(): r['g'] = pm.match(r['name'], r['ll'], max_km=EXACT_PIN_KM)
        pm.mark_shared_places(r['g'] for r in rows.values())   # one Google place claimed by 2+ listings = a shared complex page, not any one listing's own rating
        pm.save_cache(); json.dump(rows, open(N / 'mb_final.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('rematched', len(rows), 'rows;', sum(1 for r in rows.values() if r['g']), 'now have a Google match;', sum(1 for r in rows.values() if r['g'] and r['g'].get('renamed')), 'of those under a different Google name')
        return
    if not (a.home and a.out_departure and a.back_departure and a.tz): ap.error('--home, --out-departure, --back-departure and --tz are required (unless --rematch-only)')
    tz = dt.parse_tz(a.tz); out_dep, back_dep = dt.epoch(a.out_departure, tz), dt.epoch(a.back_departure, tz)

    det = load_json(N / 'mb_detail2_raw.json'); cards = {c['href']: c for c in json.load(open(N / 'mb_cards.json', encoding='utf-8'))}
    rows = {}
    for h, d in det.items():
        c = cards[h]
        if d.get('err') or not d.get('lat'): continue
        have = ' | '.join(str(x).lower() for x in (d.get('amen') or []))
        if any(w.lower() not in have for w in a.require_amenity): continue
        total = (d.get('night1') or 0) + (d.get('night2') or 0)
        rows[h] = {'id': h, 'name': c['name'], 'area': (c['area'].rsplit(', ', 1)[0] if ', ' in c['area'] else c['area']), 'guests': c['guests'], 'rooms': c['rooms'], 'baths': c['baths'],
                   'night1': d.get('night1'), 'night2': d.get('night2'), 'night3': d.get('night3'), 'total': total, 'mb_rating': d.get('rating'), 'mb_n': d.get('n') or 0,
                   'mb_revs': d.get('revs') or [], 'amen': d.get('amen') or [], 'll': [float(d['lat']), float(d['lng'])], 'url': SITE_BASE + h}
    print(len(rows), 'listings' + (' with ' + ', '.join(a.require_amenity) if a.require_amenity else ''))
    for n, (h, r) in enumerate(rows.items()):
        r['g'] = pm.match(r['name'], r['ll'], max_km=EXACT_PIN_KM)
        if (n + 1) % 30 == 0: print('google', n + 1, flush=True)
    pm.mark_shared_places(r['g'] for r in rows.values())   # one Google place claimed by 2+ listings = a shared complex page, not any one listing's own rating
    pm.save_cache()
    key = load_key('GOOGLE_MAPS_API_KEY')
    ids = list(rows)
    try:
        dt.measure_drives(rows, ids, a.home, out_dep, back_dep, key, out_key='drive_out', back_key='drive_back')
    except dt.gm.MapsApiError as e:
        sys.exit(f'FAIL {e}. mb_final.json was not written.')
    json.dump(rows, open(N / 'mb_final.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    ok = [r for r in rows.values() if r.get('drive_out') and r['drive_out'] <= a.max_drive and r['total'] and (a.budget is None or r['total'] <= a.budget)]
    print(f'within {a.max_drive} min and <= {a.budget}:', len(ok), '; with Google rating', sum(1 for r in ok if r['g'] and r['g']['g_rating']), '; with MB rating', sum(1 for r in ok if r['mb_rating']))


if __name__ == '__main__':
    main()
