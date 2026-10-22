#!/usr/bin/env python
"""Consistency audit for the built report data: finds rows whose own fields contradict each other - the class of error a reviewer
reading one flag at a time doesn't notice ("No independent Google rating" on a row that links to a Google place and has reviews).

Every FLAG on a row is a claim about that row. This script tests each claim against the row's other fields, so the check does not depend
on a reviewer's attention. It is deterministic and reads only the built JSON (no API calls). Errors exit 1; run it BEFORE spawning the
content/design review agents and hand them its output - an agent's "yes" doesn't count while this is failing.

Checks (ERROR unless marked WARN):
  Beyond list (beyond_data.json rows: rating, n, rating_src, gurl, flags, rank, score)
    B1  a flag says there is no Google rating/match, but gurl is a real Google place link
    B2  rating_src says "Google" but gurl is only a name-search link (no place behind the rating)
    B3  a flag says "Only N reviews" but the row's n is different
    B4  a flag says Google lists the place under a different name, but there is no place link
    B5  rating shown but n is 0/missing, or n shown but no rating
    B6  a flag says no rating exists, but the row shows a rating or another flag cites one
    B7  gurl is a Google place link with an EMPTY place_id (it opens nothing)
  Main list (report_data.json rows: grat, gn, gname, gcluster, gurl, flags, an, arat, rank, score)
    M1  a flag says no Google rating/match, but grat/gn or a place link exists
    M2  grat without gn (a rating with no review count), or gn with no grat
    M3  a flag says "Only N reviews" where N is neither the Airbnb nor the Google count
  Both lists
    S1  prose claims "shortest drive" / "lowest price" / "ranked first" but the row's own drive / cost / rank says otherwise
    R1  rank is not 1..N with no gaps or repeats; R2 (WARN) score rises as rank worsens
    X1  (WARN) the same Google place backs 2+ rows - a shared complex page, not each row's own rating
And prints a FLAG INVENTORY (each distinct flag template, digits normalised, with a count and an example) so a reader can scan every kind of
claim the report makes in a few lines and spot one that doesn't make sense.

Run from the project folder:
  python "<plugin root>/skills/report-review/scripts/audit_consistency.py" --beyond beyond_data.json --main report_data.json
"""
import argparse, collections, json, re, sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

NO_GOOGLE = re.compile(r'\bno (independent |confident )?google\b', re.I)
ONLY_N = re.compile(r'^only (\d+) (?:google |airbnb )?reviews?\b', re.I)   # a flag that STATES the row's own count; "Google listing X has only 7 review(s)" is Google's count, not the row's
DIFF_NAME = re.compile(r'google lists this place as', re.I)
PLACE_LINK = re.compile(r'place_id:|[?&]cid=|/maps/place/')
TEXT_FIELDS = {'beyond': ('critical', 'guests_say', 'source_note', 'score_why'), 'main': ('why_score', 'verdict', 'summary', 'revsum')}


def is_place_link(u): return bool(u and PLACE_LINK.search(u))
def pid(u):
    m = re.search(r'place_id:([A-Za-z0-9_-]+)|[?&]cid=(\d+)', u or '')
    return (m.group(1) or m.group(2)) if m else None


def audit(rows, kind, errs, warns):
    label = lambda r: f"{kind} #{r.get('rank')} {r.get('name')}"
    flags_of = lambda r: [f for f in (r.get('flags') or []) if isinstance(f, str)]
    ranks = [r.get('rank') for r in rows]
    if ranks != list(range(1, len(rows) + 1)) and sorted(x for x in ranks if x) != list(range(1, len(rows) + 1)):
        errs.append(f"R1 {kind}: ranks are not 1..{len(rows)} without gaps/repeats (first few: {ranks[:8]})")
    ordered = sorted((r for r in rows if r.get('rank') and r.get('score') is not None), key=lambda r: r['rank'])
    for a, b in zip(ordered, ordered[1:]):
        if b['score'] > a['score'] + 1e-9: warns.append(f"R2 {kind}: score rises from rank {a['rank']} ({a['score']}) to rank {b['rank']} ({b['score']}) - {b.get('name')}")
    by_place = collections.defaultdict(list)
    min_drive = min((r['drive'] for r in rows if r.get('drive') is not None), default=0)
    min_cost = min((r['cost'] for r in rows if r.get('cost') is not None), default=0)
    for r in rows:
        u = r.get('gurl'); fl = flags_of(r)
        if kind == 'beyond':
            n, rating, src = r.get('n'), r.get('rating'), r.get('rating_src')
            for f in fl:
                if NO_GOOGLE.search(f) and is_place_link(u): errs.append(f"B1 {label(r)}: flag \"{f[:70]}\" but gurl is a Google PLACE link")
                if DIFF_NAME.search(f) and not is_place_link(u): errs.append(f"B4 {label(r)}: flag says Google lists it under another name but gurl is not a place link")
                m = ONLY_N.search(f)
                if m and n is not None and int(m.group(1)) != n: errs.append(f"B3 {label(r)}: flag says only {m.group(1)} reviews but n={n}")
            if str(src).startswith('Google') and not is_place_link(u): errs.append(f"B2 {label(r)}: rating_src is Google but gurl is only a name-search link")
            if (rating and not n) or (n and not rating): errs.append(f"B5 {label(r)}: rating={rating} n={n}")
            if any(re.match(r'no ratings? found', f, re.I) for f in fl) and (rating or any('★' in f for f in fl)):
                errs.append(f"B6 {label(r)}: a flag says no rating exists, but the row shows rating={rating} or another flag cites one")
        else:
            grat, gn = r.get('grat'), r.get('gn')
            for f in fl:
                if NO_GOOGLE.search(f) and (grat or gn or is_place_link(u)): errs.append(f"M1 {label(r)}: flag \"{f[:70]}\" but grat={grat} gn={gn} place-link={is_place_link(u)}")
                m = ONLY_N.search(f)
                if m and int(m.group(1)) not in (r.get('an'), gn): errs.append(f"M3 {label(r)}: flag says only {m.group(1)} reviews; an={r.get('an')} gn={gn}")
            if (grat and not gn) or (gn and not grat): errs.append(f"M2 {label(r)}: grat={grat} gn={gn}")
        if re.search(r'place_id:(?![A-Za-z0-9_-])', u or ''): errs.append(f"B7 {label(r)}: gurl has an EMPTY place_id, so the Google link opens nothing")
        for fld in TEXT_FIELDS[kind]:   # S1: a superlative or rank claim in prose must hold against the data it is about
            t = r.get(fld) if isinstance(r.get(fld), str) else ''
            if re.search(r'shortest drive|closest to (home|you)', t, re.I) and r.get('drive') is not None and r['drive'] > min_drive:
                errs.append(f"S1 {label(r)}: {fld} claims the shortest drive but drive={r['drive']} and the list minimum is {min_drive}")
            if re.search(r'\b(cheapest|lowest (price|cost))\b', t, re.I) and r.get('cost') is not None and r['cost'] > min_cost:
                errs.append(f"S1 {label(r)}: {fld} claims the lowest price but cost={r['cost']} and the list minimum is {min_cost}")
            if re.search(r'\branked (first|#?1)\b|\btop-ranked\b|\bour top pick\b', t, re.I) and r.get('rank') != 1:
                errs.append(f"S1 {label(r)}: {fld} claims top rank but rank={r.get('rank')}")
        if pid(u): by_place[pid(u)].append(r)
    for k, rs in by_place.items():
        if len(rs) > 1: warns.append(f"X1 {kind}: {len(rs)} rows share one Google place: " + '; '.join(f"#{x.get('rank')} {x.get('name')}" for x in rs))


def inventory(rows):
    inv = collections.OrderedDict()
    for r in rows:
        for f in r.get('flags') or []:
            if not isinstance(f, str): continue
            key = re.sub(r'“[^”]*”|"[^"]*"', '"…"', re.sub(r'\d+(\.\d+)?', '#', f))
            inv.setdefault(key, [0, f, r.get('name')])[0] += 1
    return inv


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--beyond'); ap.add_argument('--main')
    a = ap.parse_args()
    if not (a.main or a.beyond):
        ap.error('give --main and/or --beyond: with no list there is nothing to audit, and a vacuous run would read as clean')
    errs, warns, inv = [], [], []
    for path, kind in ((a.main, 'main'), (a.beyond, 'beyond')):
        if not path: continue
        d = json.loads(Path(path).read_text(encoding='utf-8'))
        rows = d['rows'] if isinstance(d, dict) else d
        audit(rows, kind, errs, warns)
        inv.append((kind, inventory(rows)))
        print(f'audited the {kind} list: {len(rows)} row(s)')
    for kind, i in inv:
        print(f"FLAG INVENTORY ({kind}): {len(i)} distinct kinds of flag")
        for key, (c, ex, who) in sorted(i.items(), key=lambda kv: -kv[1][0]):
            print(f"  {c:>4} x  {ex[:110]}   (e.g. {who})")
        print()
    for w in warns: print('WARN ', w)
    for e in errs: print('ERROR', e)
    print(f"\n{len(errs)} error(s), {len(warns)} warning(s)")
    return 1 if errs else 0


if __name__ == '__main__':
    raise SystemExit(main())
