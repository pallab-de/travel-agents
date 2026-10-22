#!/usr/bin/env python
"""Review corpus for the top N places: deduplicated listing-platform reviews (host responses removed) plus Google reviews,
recency statistics, and one markdown knowledge-base file per place.

  python build_corpus.py --merged merged.json [--reviews reviews_raw.json] --parsed detail_parsed.json --top 45 \
      [--gr10 gr10.json] [--apify apify_reviews.json] [--currency EUR] [--out review_corpus.json] [--kb-dir kb2]
  python build_corpus.py selftest

Runs after place-matching and merge.py, before report-builder: it reads merged.json (the merge step's rows), never a built report.
  merged.json         {id: merge.py row}   (the first N in file order are used)
  reviews_raw.json    {id: raw review-page text}      detail_parsed.json {id: parse_details.py output}
  gr10.json           {id: {reviews: [{stars, when, text}]}}   (the free Google path, optional)
  apify_reviews.json  {id: [{stars, when, text, ...}]}         (a licensed provider's download, optional)
Output: review_corpus.json and <kb-dir>/<id>.md. Recency statistics are relative to the day this runs.
"""
import argparse, datetime as dt, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))   # <plugin_root>/tools/jsonfile.py
from jsonfile import load_json  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))   # review_analysis.py sits alongside this script
from review_analysis import split_reviews  # noqa: E402

MONTHS = {m: i for i, m in enumerate(['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'], 1)}


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def when_to_date(w, now):
    w = (w or '').strip()
    low = w.lower()
    if low in ('today', 'yesterday', 'just now'):
        return now - dt.timedelta(days=1 if low == 'yesterday' else 0)
    m = re.match(r'(\d+|a|an|one) (minute|hour|day|week|month|year)s? ago', low)
    if m:
        n = 1 if m.group(1) in ('a', 'an', 'one') else int(m.group(1))
        days = {'minute': 0, 'hour': 0, 'day': 1, 'week': 7, 'month': 30, 'year': 365}[m.group(2)] * n
        return now - dt.timedelta(days=days)
    m = re.match(r'(\d{4}-\d{2}-\d{2})', w)
    if m:
        try:
            return dt.date.fromisoformat(m.group(1))
        except ValueError:
            return None
    m = re.match(r'([A-Z][a-z]+) (\d{4})', w)
    if m and m.group(1) in MONTHS:
        return dt.date(int(m.group(2)), MONTHS[m.group(1)], 15)
    return None


def place_corpus(i, row, raw_text, parsed, google10, apify, now, currency):
    """-> (corpus entry, knowledge-base markdown) for one place."""
    ab = split_reviews(raw_text) if raw_text and len(raw_text) > 500 else []
    if not ab:   # fall back to the handful on the listing page (host response stripped)
        for x in (parsed or {}).get('reviews', []):
            t = re.sub(r'\s+', ' ', re.split(r'Response from ', x['text'])[0]).strip()
            if t:
                ab.append({'stars': x['stars'], 'when': x['when'], 'text': t})
    for x in ab:
        d = when_to_date(x['when'], now)
        x['date'] = d.isoformat() if d else None
    dated = [x for x in ab if x['date']]
    last12 = [x for x in dated if (now - dt.date.fromisoformat(x['date'])).days <= 365]
    a_n = row.get('a_n')
    stats = {'read': len(ab), 'total_on_platform': a_n, 'avg_all': round(sum(x['stars'] for x in ab) / len(ab), 2) if ab else None,
             'n_last12': len(last12), 'avg_last12': round(sum(x['stars'] for x in last12) / len(last12), 2) if last12 else None,
             'low_all': sum(1 for x in ab if x['stars'] <= 3), 'low_last12': sum(1 for x in last12 if x['stars'] <= 3)}
    grev = [{'stars': x['stars'], 'when': x['when'], 'text': x['text']} for x in (google10 or {}).get('reviews', []) if x.get('text')]
    if apify:   # a licensed provider: many more Google reviews; drop owner replies, empty text, copies of platform reviews, and dupes
        seen = {re.sub(r'\W+', '', x['text'].lower())[:40] for x in ab}
        grev = []
        for x in apify:
            t = (x.get('text') or '').strip()
            k = re.sub(r'\W+', '', t.lower())[:40]
            if not t or len(t) < 15 or k in seen:
                continue
            seen.add(k)
            grev.append({'stars': x['stars'], 'when': x.get('when'), 'text': t})
    g = row.get('g') or {}
    entry = {'name': row['name'], 'cost': row.get('total'), 'drive': row.get('drive_out_min'), 'beds_n': row.get('beds_n'), 'pool': row.get('pool_kind'),
             'pet_fee': row.get('pet_fee'), 'platform_rating': row.get('a_rating'), 'platform_n': a_n, 'google_rating': g.get('rating'), 'google_n': g.get('n'),
             'stats': stats, 'platform_reviews': ab, 'google': grev}
    md = [f"# {row['name']}", '',
          f"Platform {row.get('a_rating')} ({a_n}) | Google {g.get('rating')} ({g.get('n')}) | {currency} {row.get('total')} | {row.get('drive_out_min')} min | {row.get('beds_n')} beds | {row.get('pool_kind')} | pet fee: {row.get('pet_fee')}", '',
          f"Read {stats['read']} platform reviews (avg {stats['avg_all']}; last 12 months: {stats['n_last12']} reviews, avg {stats['avg_last12']}); {len(grev)} Google reviews. Host responses excluded.", '',
          '## Platform reviews (newest first as listed)'] + [f"- [{x['stars']}★ {x['when']}] {x['text']}" for x in ab] \
         + ['', '## Google reviews (text reviews only; owner replies excluded)'] + [f"- [{x['stars']}★ {x['when']}] {x['text']}" for x in grev]
    return entry, '\n'.join(md)


def selftest():
    now = dt.date(2030, 6, 1)
    row = {'name': 'Farm A', 'a_n': 3, 'a_rating': 4.7, 'total': 300, 'drive_out_min': 50, 'beds_n': 3, 'pool_kind': 'private', 'pet_fee': None, 'g': {'rating': 4.6, 'n': 90}}
    parsed = {'reviews': [{'stars': 5, 'when': 'March 2030', 'text': 'Lovely pool and a helpful host.\nResponse from Sam: thanks'},
                          {'stars': 3, 'when': 'May 2028', 'text': 'Noisy road at night.'}]}
    apify = [{'stars': 5, 'when': '2030-02-01', 'text': 'A genuine long review about the garden'}, {'stars': 4, 'when': '2030-02-02', 'text': 'A genuine long review about the garden'},
             {'stars': 1, 'when': '', 'text': 'short'}]
    entry, md = place_corpus('1', row, '', parsed, None, apify, now, 'EUR')
    _d = dt.date(2030, 6, 15)
    cases = [("a week ago, yesterday and an ISO date are dated", when_to_date("a week ago", _d) == dt.date(2030, 6, 8) and when_to_date("Yesterday", _d) == dt.date(2030, 6, 14) and when_to_date("2030-02-03", _d) == dt.date(2030, 2, 3)),
             ("reviews fall back to the listing page, host response stripped", [x['text'] for x in entry['platform_reviews']] == ['Lovely pool and a helpful host.', 'Noisy road at night.']),
             ("recency statistics are relative to the run date", entry['stats']['n_last12'] == 1 and entry['stats']['low_all'] == 1),
             ("provider reviews are deduplicated and short ones dropped", len(entry['google']) == 1),
             ("the knowledge base carries the currency it was given", 'EUR 300' in md and '₹' not in md)]
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


def main():
    if sys.argv[1:] == ['selftest']:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--merged', required=True); ap.add_argument('--reviews', help="optional: {id: the listing's full review-page text}; without it the few reviews on each listing page (detail_parsed.json) are used"); ap.add_argument('--parsed', required=True)
    ap.add_argument('--top', type=int, required=True, help='how many places (the first N of merged.json) to build the corpus for')
    ap.add_argument('--gr10'); ap.add_argument('--apify'); ap.add_argument('--currency', required=True, help='the currency code the report prices in, e.g. EUR')
    ap.add_argument('--out', default='review_corpus.json'); ap.add_argument('--kb-dir', default='kb2')
    a = ap.parse_args()
    merged = json.loads(Path(a.merged).read_text(encoding='utf-8'))
    ids = list(merged)[:a.top]
    reviews, parsed = (load_json(a.reviews) if a.reviews else {}), json.loads(Path(a.parsed).read_text(encoding='utf-8'))
    for flag, path in (('--gr10', a.gr10), ('--apify', a.apify)):
        if path and not Path(path).exists():
            sys.exit(f'FAIL {flag} {path} does not exist; nothing was written (leave the flag out to build without that source)')
    gr = json.loads(Path(a.gr10).read_text(encoding='utf-8')) if a.gr10 else {}
    ap_rev = json.loads(Path(a.apify).read_text(encoding='utf-8')) if a.apify else {}
    now = dt.date.today()
    corpus = {}
    Path(a.kb_dir).mkdir(exist_ok=True)
    for i in ids:
        entry, md = place_corpus(i, merged[i], reviews.get(i), parsed.get(i), gr.get(i), ap_rev.get(i), now, a.currency)
        corpus[i] = entry
        (Path(a.kb_dir) / f'{i}.md').write_text(md, encoding='utf-8')
    Path(a.out).write_text(json.dumps(corpus, ensure_ascii=False, indent=1), encoding='utf-8')
    print(len(corpus), 'places;', sum(len(c['platform_reviews']) for c in corpus.values()), 'platform reviews;', sum(len(c['google']) for c in corpus.values()), 'Google reviews')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
