#!/usr/bin/env python
"""Abstain audit: an INDEPENDENT second path that checks the matcher's misses.

The matcher (place_match.py) is built to abstain rather than guess - which means its worst silent failure is a false negative: a real,
well-reviewed place it gave up on (e.g. a booking site's "Valmora Springs" that Google lists as "Valmora Exotics", 30 m from the pin,
hundreds of reviews). Nothing downstream notices - the report just says "no Google match" - and a reviewer reading the flag in isolation can't
tell either. This script asks Google a different question ("what lodging sits within --km of this pin?") for every row with NO match and
lists each one that has a lodging that close, with its rating, review count and how the names relate.

It is a WORKLIST, not a verdict: most hits are worth a look, some are genuinely a different business next door. For each hit decide
same / different. If SAME: the matcher's logic is wrong for a class of listings - fix place_match.py (never paste the place id in by hand)
and re-run the matcher. Record each decision in a decisions file ({"decisions": [{"listing", "google", "verdict": "same|different", "reason"}]}) and pass it
with --decisions: the exit code is 0 only when every strong hit (>= --min-reviews Google reviews) has a `different` verdict. A `same`
verdict keeps the exit at 1 until the matcher is fixed and the stage re-run. Rows with no "ll" can't be audited; they are counted, and
the run exits 1 unless --allow-no-ll is given.

Input: JSON, {id: row} or [row]; each row needs "name", "ll" [lat, lng] and "g" (the match_result dict, or null when unmatched) -
mb_final.json from direct_booking_pipeline.py already has exactly that shape.

Run from the project folder:
  python "<plugin root>/skills/place-matching/scripts/audit_abstains.py" FILE --pins exact|jittered [--km 0.15] [--min-reviews 20] [--decisions decisions.json] [--allow-no-ll]
  (exact: mb_final.json, a direct-booking site's own coordinates, 150 m; jittered: merged.json, a platform that moves its pins, 2 km). Google returns lodging in
  pages of 20; up to 3 pages are read, and a place with more than that nearby is reported TRUNCATED and fails the run: narrow --km and run again.
  python "<plugin root>/skills/place-matching/scripts/audit_abstains.py" selftest
"""
import argparse, difflib, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))
sys.path.insert(0, str(Path(__file__).parent))
from travel_secrets import load_key  # noqa: E402
import google_maps as gm  # noqa: E402
import place_match as pm  # noqa: E402

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')


def verdict_of(decisions, h):
    for d in decisions:
        if d.get('listing') == h['listing'] and d.get('google') == h['google'] and str(d.get('reason') or '').strip():
            return d.get('verdict')
    return None


def exit_code(strong, decisions, no_ll, allow_no_ll, truncated=0):
    """0 only when every strong hit has a written `different` decision, every row could be audited (or that is allowed), and
    Google's answer for each row was read in full."""
    open_ = [h for h in strong if verdict_of(decisions, h) != 'different']
    return 1 if open_ or truncated or (no_ll and not allow_no_ll) else 0


MAX_PAGES = 3


def nearby_pages(lat, lng, radius_m, key, who, sleep=time.sleep):
    """-> (places, truncated): every lodging Google lists within the radius, up to MAX_PAGES pages of 20; truncated when it had more."""
    out, token = [], None
    for page in range(MAX_PAGES):
        res = gm.require_ok(gm.nearby_search(lat, lng, radius_m, key, 'lodging', pagetoken=token), f"nearby lodging for {who!r}")
        out += res.get('results', [])
        token = res.get('next_page_token')
        if not token:
            return out, False
        sleep(2.2)   # a next_page_token becomes valid after a short delay
    return out, True


def selftest():
    h = {'listing': 'A', 'google': 'B', 'n': 50}
    cases = [("a strong pair with no decision fails", exit_code([h], [], 0, False) == 1),
             ("a 'different' decision with a reason passes", exit_code([h], [{'listing': 'A', 'google': 'B', 'verdict': 'different', 'reason': 'next door'}], 0, False) == 0),
             ("a 'same' decision keeps it failing", exit_code([h], [{'listing': 'A', 'google': 'B', 'verdict': 'same', 'reason': 'x'}], 0, False) == 1),
             ("a decision with no reason doesn't count", exit_code([h], [{'listing': 'A', 'google': 'B', 'verdict': 'different'}], 0, False) == 1),
             ("a row with no coordinates fails unless allowed", exit_code([], [], 2, False) == 1 and exit_code([], [], 2, True) == 0),
             ("a truncated answer fails the run", exit_code([], [], 0, False, truncated=1) == 1)]
    real = gm.nearby_search
    pages = {None: {"status": "OK", "results": [{"name": "a"}] * 20, "next_page_token": "t1"}, "t1": {"status": "OK", "results": [{"name": "b"}] * 20, "next_page_token": "t2"},
             "t2": {"status": "OK", "results": [{"name": "c"}] * 20, "next_page_token": "t3"}}
    try:
        gm.nearby_search = lambda lat, lng, r, key, t=None, pagetoken=None: pages[pagetoken]
        got, trunc = nearby_pages(0, 0, 100, "k", "x", sleep=lambda s_: None)
        cases.append(("a dense area is read for up to three pages and reported TRUNCATED", len(got) == 60 and trunc))
        pages["t1"] = {"status": "OK", "results": [{"name": "b"}] * 5}
        got, trunc = nearby_pages(0, 0, 100, "k", "x", sleep=lambda s_: None)
        cases.append(("a short area is read in full", len(got) == 25 and not trunc))
    finally:
        gm.nearby_search = real
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


def main():
    if sys.argv[1:] == ['selftest']:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('infile')
    ap.add_argument('--decisions', help='JSON {"decisions": [{listing, google, verdict, reason}]}')
    ap.add_argument('--allow-no-ll', action='store_true', help='accept rows that have no coordinates (they are never audited)')
    ap.add_argument('--pins', choices=['exact', 'jittered'], required=True, help="exact: the source's own coordinates (a direct-booking site) so look within 150 m; jittered: a platform that moves its pins by 1-2 km (Airbnb) so look within 2 km")
    ap.add_argument('--km', type=float, default=None, help='Override the search distance from the pin, in km')
    ap.add_argument('--min-reviews', type=int, default=20, help='Exit 1 if any hit has at least this many Google reviews (default 20)')
    a = ap.parse_args()
    a.km = a.km or (2.0 if a.pins == 'jittered' else 0.15)
    raw = json.loads(Path(a.infile).read_text(encoding='utf-8'))
    rows = list(raw.values()) if isinstance(raw, dict) else raw
    unmatched = [r for r in rows if not r.get('g') and r.get('ll')]
    no_ll = [r for r in rows if not r.get('g') and not r.get('ll')]
    decisions = json.loads(Path(a.decisions).read_text(encoding='utf-8')).get('decisions', []) if a.decisions else []
    key = load_key('GOOGLE_MAPS_API_KEY')
    hits, truncated = [], []
    for r in unmatched:
        places, cut = nearby_pages(r['ll'][0], r['ll'][1], int(a.km * 1000), key, r['name'])
        if cut:
            truncated.append(r['name'])
        for p in places:
            loc = p['geometry']['location']
            d = pm.hv(tuple(r['ll']), (loc['lat'], loc['lng']))
            if d > a.km: continue
            shared = sorted(set(pm.toks(r['name'])) & set(pm.toks(p.get('name', ''))) - pm.UNIVERSAL)
            dist = lambda nm: ' '.join(t for t in pm.toks(nm) if t not in pm.UNIVERSAL and not pm.BEDTOK.match(t))
            sim = difflib.SequenceMatcher(None, dist(r['name']), dist(p.get('name', ''))).ratio()
            hits.append({'listing': r['name'], 'google': p.get('name'), 'rating': p.get('rating'), 'n': p.get('user_ratings_total') or 0,
                         'km': round(d, 3), 'shared_words': shared, 'sim': round(sim, 2), 'status': p.get('business_status')})
    hits.sort(key=lambda h: (-h['n'], h['km']))
    related = [h for h in hits if h['shared_words'] or h['sim'] >= 0.6]      # names that could plausibly be the same business
    print(f"{len(rows)} rows, {len(unmatched)} unmatched. {len(hits)} unmatched-row/nearby-lodging pairs within {a.km * 1000:.0f} m; "
          f"{len(hits) - len(related)} have unrelated names (a neighbour, ignored) and {len(related)} have related names:\n")
    for h in related:
        why = ('shares ' + '/'.join(h['shared_words'])) if h['shared_words'] else f"spelled alike ({h['sim']})"
        print(f"  {h['listing'][:34]:34} <-> {str(h['google'])[:38]:38} {h['rating']}★/{h['n']}  {h['km'] * 1000:.0f} m  [{why}]")
    strong = [h for h in related if h['n'] >= a.min_reviews]
    open_ = [h for h in strong if verdict_of(decisions, h) != 'different']
    print(f"\n{len(strong)} related-name pairs with >= {a.min_reviews} Google reviews; {len(open_)} without a written 'different' decision. Decide each one same/different; for SAME, fix the matcher's logic (not the data) and re-run.")
    if no_ll:
        print(f"{len(no_ll)} unmatched row(s) have no coordinates and were NOT audited" + ('' if a.allow_no_ll else ' (pass --allow-no-ll to accept that)'))
    if truncated:
        print(f"TRUNCATED: Google had more than {MAX_PAGES * 20} lodgings near {len(truncated)} row(s) ({', '.join(truncated[:3])}); only the first {MAX_PAGES * 20} were audited. Narrow --km and run again.")
    return exit_code(strong, decisions, len(no_ll), a.allow_no_ll, len(truncated))


if __name__ == '__main__':
    raise SystemExit(main())
