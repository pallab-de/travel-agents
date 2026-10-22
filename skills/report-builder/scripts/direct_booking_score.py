"""Score and package the direct-booking ("Beyond the search platform") candidates from direct_booking_pipeline.py -> beyond_data.json.
Formula score is capped at 4.2 (below the main platform's ceiling) because pets, real beds and whole-property status
are usually NOT independently verified for these direct-booking leads - say so in the report.

Run (from the project folder that holds mb_final.json):
  python "<plugin root>/skills/report-builder/scripts/direct_booking_score.py" --home "<home base address>, <city>" \\
      --max-drive <minutes> --budget <total budget> --min-bedrooms <n>
"""
import hashlib, argparse, json, re, sys, urllib.parse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))   # <plugin_root>/tools/travel_secrets.py, shared across skills
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'skills' / 'place-matching' / 'scripts'))   # place_match.py lives in the place-matching skill
import place_match as pm  # noqa: E402
N = Path.cwd()


SITE_SHADE = 0.1   # a booking site's own rating is self-reported, so the SCORE shades it down; the number SHOWN is always the site's real one


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def rating_of(r, min_reviews_for_trust=10):
    """-> (rating, n, source, note, rating_for_score). `rating`/`n` are what the report DISPLAYS and are never adjusted; `rating_for_score` is what
    the score formula uses. `note` is the honest one-line reason whenever the displayed number is not a plain, exact-name Google rating - a blanket
    "no Google rating" is wrong when a Google listing exists but is thin, unrated, shared or under a different name, and a note that says "showing the
    booking site's rating" is wrong when the site has none."""
    g = r['g']
    site_rt, site_n = r.get('mb_rating'), r.get('mb_n')
    if g and g.get('g_rating') and (g.get('g_n') or 0) >= min_reviews_for_trust and not g.get('cluster'):
        note = "Google lists it under a different name: confirm the match" if g.get('renamed') else None
        return g['g_rating'], g['g_n'], 'Google', note, g['g_rating']
    # Each note is a card watch-out, so it is one short clause (the watch-out limit is 14 words); the detail is in the dossier.
    if not g: why = "No confident Google match"
    elif not g.get('g_rating'): why = "Google listing has no rating yet"
    elif g.get('cluster') and (g.get('g_n') or 0) >= min_reviews_for_trust: why = "Google listing may be shared, not this unit"
    else: why = f"Google has only {g['g_n']} review(s), too few"
    if site_rt and site_n: return site_rt, site_n, 'Site rating', why + "; showing the site's rating", site_rt - SITE_SHADE
    if g and g.get('g_rating') and g.get('g_n'):
        return g['g_rating'], g['g_n'], 'Google (shared listing)' if g.get('cluster') else 'Google (few reviews)', why + '; the site has no rating', g['g_rating']
    return None, 0, '', why + '; the site has no rating either', None


def low_star_flag(r):
    """"N of M sampled reviews are 1-2 stars" or None. The site repeats some reviews verbatim, so each distinct review counts once."""
    uniq, seen = [], set()
    for x in r.get('mb_revs') or []:
        k = re.sub(r'\W+', '', (x[1] or '').lower())[:50]
        if k and k not in seen: seen.add(k); uniq.append(x)
    lows = [x for x in uniq if x[0] <= 2]
    return f'{len(lows)} of {len(uniq)} sampled reviews are 1-2 stars' if lows else None


def selftest():
    rows = [("no match", None, {}), ("renamed", {"g_rating": 4.5, "g_n": 80, "renamed": True, "g_name": "X"}, {}),
            ("no rating", {"g_rating": None, "g_n": 0, "g_name": "X"}, {"mb_rating": 4.0, "mb_n": 12}),
            ("shared", {"g_rating": 4.2, "g_n": 50, "cluster": True, "g_name": "X"}, {"mb_rating": 4.0, "mb_n": 12}),
            ("few", {"g_rating": 4.9, "g_n": 3, "g_name": "X"}, {"mb_rating": 4.0, "mb_n": 12}),
            ("few, no site rating", {"g_rating": 4.9, "g_n": 3, "g_name": "X"}, {}),
            ("shared, no site rating", {"g_rating": 4.2, "g_n": 50, "cluster": True, "g_name": "X"}, {}),
            ("nothing anywhere", {"g_rating": None, "g_n": 0, "g_name": "X"}, {})]
    cases = []
    for label, g, extra in rows:
        note = rating_of(dict(extra, g=g))[3]
        cases.append((f"the note for '{label}' fits a watch-out (14 words)", note is None or len(note.split()) <= 14))
    cases.append(("a low-star flag fits too", len((low_star_flag({"mb_revs": [[1, "bad"]] * 1}) or "").split()) <= 14))
    for name, ok in cases:
        print(("PASS " if ok else "FAIL ") + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


def main():
    if sys.argv[1:] == ["selftest"]:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--home', required=True, help='Home base (an area, station or landmark, never a street address), used only to build the "directions from home" link')
    ap.add_argument('--max-drive', type=int, required=True, help='Longest drive from home, in minutes (the trip\'s own cap from config.json)'); ap.add_argument('--budget', type=int, required=True, help='Total budget for the stay, in the listing currency'); ap.add_argument('--min-bedrooms', type=int, required=True, help='Fewest bedrooms (from config.json)')
    a = ap.parse_args()
    origin = urllib.parse.quote(a.home)

    R = json.load(open(N / 'mb_final.json', encoding='utf-8'))
    ok = [r for r in R.values() if r.get('drive_out') and r['drive_out'] <= a.max_drive and r['total'] and r['total'] <= a.budget and r['rooms'] >= a.min_bedrooms]

    rows = []
    for r in ok:
        rt, n, src, rating_note, rt_score = rating_of(r)
        q = 0.0
        if rt_score:
            bay = (rt_score * n + 4.2 * 20) / (n + 20)
            q = max(0.0, min(2.0, (bay - 3.6) / 1.2 * 2.0))
        beds = 0.15 if r['rooms'] >= 5 else (0.0 if r['rooms'] == 4 else -0.5)
        d = r['drive_out']; drive = 0.5 if d <= 60 else 0.42 if d <= 90 else 0.3 if d <= 120 else 0.12
        t = r['total']; price = 0.4 if t <= a.budget * 0.33 else 0.32 if t <= a.budget * 0.47 else 0.22 if t <= a.budget * 0.67 else 0.1 if t <= a.budget * 0.87 else 0.0
        ev = -0.2 if re.search(r'convention|banquet|celebration|party', r['name'], re.I) else 0.0
        score = round(min(4.2, 1.2 + q + beds + drive + price + ev), 1)
        flags = []
        if r['rooms'] == a.min_bedrooms: flags.append(f'Only {a.min_bedrooms} bedrooms: check real-bed count for your group')
        if rating_note: flags.append(rating_note)
        if n and n < 30 and 'review(s)' not in (rating_note or ''): flags.append(f'Only {n} reviews')
        if not rt: flags.append('No ratings found anywhere')
        lowflag = low_star_flag(r)
        if lowflag: flags.append(lowflag)
        if re.search(r'convention|banquet', r['name'], re.I): flags.append('Event/convention venue')
        g = r['g']
        gurl = f"https://www.google.com/maps/place/?q=place_id:{g['g_place_id']}" if g and g.get('g_place_id') else 'https://www.google.com/maps/search/?api=1&query=' + urllib.parse.quote(f"{r['name']} {r['area']}")
        rows.append({'id': 'd-' + hashlib.sha1(str(r['url']).encode('utf-8')).hexdigest()[:8], 'name': r['name'], 'area': r['area'], 'rooms': r['rooms'], 'guests': r['guests'], 'total': t, 'night1': r.get('night1'), 'night2': r.get('night2'),
                     'rating': rt and round(rt, 1), 'n': n, 'rating_src': src, 'drive': d, 'drive_back': r.get('drive_back'), 'score': score, 'flags': flags,
                     'url': r['url'], 'gurl': gurl, 'dir': f"https://www.google.com/maps/dir/?api=1&origin={origin}&destination={r['ll'][0]},{r['ll'][1]}&travelmode=driving",
                     'gplace': bool(g and g.get('g_place_id')), 'map': f"https://www.google.com/maps/search/?api=1&query={r['ll'][0]},{r['ll'][1]}",
                     'amen': (r.get('amen') or [])[:8]})

    # drop leads that are also on the main platform's list (same Google place, or a near-identical distinctive name)
    places = json.load(open(N / 'places4.json', encoding='utf-8')) if (N / 'places4.json').exists() else {}
    cands = json.load(open(N / 'cands2_drive.json', encoding='utf-8')) if (N / 'cands2_drive.json').exists() else {}
    air_pids = {m.get('g_place_id') for m in places.values() if m and m.get('g_place_id')}
    core = lambda name: set(pm.toks(name)) - pm.UNIVERSAL
    air_names = [core(v['name']) for i, v in cands.items() if i in places]
    def on_main_list(x, r):
        if r.get('g') and r['g'].get('g_place_id') in air_pids: return True
        a_ = core(x['name'])
        return bool(a_) and any(a_ and b and len(a_ & b) / len(a_ | b) >= 0.75 for b in air_names)
    byid = {v['url']: v for v in R.values()}
    dup_urls = {x['url'] for x in rows if on_main_list(x, byid[x['url']])}
    dups = [x['name'] for x in rows if x['url'] in dup_urls]
    rows = [x for x in rows if x['url'] not in dup_urls]   # by identity: a different lead that shares a name stays
    print('also on the main list (removed):', dups)
    rows.sort(key=lambda x: (-x['score'], -(x['n'] or 0)))
    for i, x in enumerate(rows, 1): x['rank'] = i
    json.dump({'rows': rows}, open(N / 'beyond_data.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(len(rows), 'Beyond-the-platform leads')


if __name__ == '__main__':
    main()
