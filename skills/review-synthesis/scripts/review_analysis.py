"""Review themes for one project folder: which topics guests mention, how often, and example sentences.

v2 behaviour (kept):
- reviews de-duplicated (the review page repeats the preview reviews) and host responses removed
- word-boundary regexes (no more 'ants' inside 'restaurants')
- example quotes are the sentence containing the match, not the start of the review
- listings with a handful of reviews (no 'show all' page) fall back to the reviews on the listing page

Nothing about any one trip is in this file. The topics live in a themes JSON ({"topic": "regex", ...}); the
staycation themes shipped alongside are themes.staycation.json. Pass another file to analyse another topic.

  python review_analysis.py --reviews reviews_raw.json --parsed detail_parsed.json --merged merged.json \
      [--themes themes.staycation.json] [--out review_analysis.json]

Inputs (in the project folder):
  reviews_raw.json   {listing id: raw review-page text}   (may be a JSON-encoded string, as the capture tool writes it)
  detail_parsed.json {listing id: {"reviews": [{"stars", "when", "text"}]}}   (listing-page reviews, the fallback)
  merged.json        {listing id: {"a_n": review count shown on the listing}}
"""
import argparse, collections, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))   # <plugin_root>/tools/jsonfile.py
from jsonfile import load_json  # noqa: E402

HERE = Path(__file__).parent
DEFAULT_THEMES = HERE / 'themes.staycation.json'

NEGKW = re.compile(r'\b(?:disappoint\w*|worst|not recommend\w*|dirty|rude|fraud|cheat\w*|misleading|not as (?:described|shown|advertised)|different from|poor|terrible|horrible|waste|unsafe|snake|scorpion|leak\w*|broken|not working|no water|mosquito\w*|cockroach\w*|smell\w*|stink\w*|overcharg\w*|extra charge\w*|hidden charge\w*|police|raid|refus\w*)\b', re.I)


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def sentence_with(tx, pat):
    for s in re.split(r'(?<=[.!?])\s+', tx):
        if re.search(pat, s, re.I): return s.strip()[:220]
    return tx[:200]


def split_reviews(t):
    """Split a review-page text into {stars, when, text} items, dropping duplicates and host responses."""
    items, seen = [], set()
    for m in re.finditer(r'\n([^\n]{1,60})\n(?:[^\n]{0,60}\n)?((?:New to Airbnb|\d+ (?:years?|months?) on Airbnb))\nRating, (\d) stars?\n,\n·\n([^\n]+)\n(.*?)(?=\nShow more\n|\n[^\n]{1,60}\n(?:New to Airbnb|\d+ (?:years?|months?) on Airbnb)\nRating|\Z)', t, re.S):
        txt = re.split(r"Show all \d+ reviews|Where you.ll be|Response from ", m.group(5))[0]
        txt = re.sub(r'\s+', ' ', txt).strip()
        key = txt[:40].lower()
        if not txt or key in seen: continue
        seen.add(key)
        items.append({'stars': int(m.group(3)), 'when': m.group(4), 'text': txt})
    return items


def fallback(parsed, i):
    out, seen = [], set()
    for r in parsed.get(i, {}).get('reviews', []):
        txt = re.sub(r'\s+', ' ', re.split(r'Response from ', r['text'])[0]).strip()
        if txt and txt[:40].lower() not in seen:
            seen.add(txt[:40].lower()); out.append({'stars': r['stars'], 'when': r['when'], 'text': txt})
    return out


def analyse(rv, parsed, merged, themes):
    out = {}
    for i, x in merged.items():
        if i in rv: items, src = split_reviews(rv[i]), 'review page'
        elif x['a_n'] > 0: items, src = fallback(parsed, i), 'listing page'
        else: continue
        if not items: continue
        counts = collections.Counter(); ex = collections.defaultdict(list); neg = []
        for r in items:
            tx = r['text']
            for k, pat in themes.items():
                if re.search(pat, tx, re.I):
                    counts[k] += 1
                    if len(ex[k]) < 2: ex[k].append(sentence_with(tx, pat))
            if r['stars'] <= 3 or NEGKW.search(tx):
                neg.append({'stars': r['stars'], 'when': r['when'], 'text': tx[:320]})
        neg.sort(key=lambda n: (n['stars'], 0))
        out[i] = {'n_fetched': len(items), 'source': src, 'stars': dict(collections.Counter(r['stars'] for r in items)),
                  'themes': dict(counts), 'examples': dict(ex), 'negatives': neg[:8],
                  'recent': [f"[{r['stars']}★ {r['when']}] {r['text'][:220]}" for r in items[:4]]}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--reviews', default='reviews_raw.json'); ap.add_argument('--parsed', default='detail_parsed.json')
    ap.add_argument('--merged', default='merged.json'); ap.add_argument('--themes', default=str(DEFAULT_THEMES))
    ap.add_argument('--out', default='review_analysis.json')
    a = ap.parse_args()
    themes = json.loads(Path(a.themes).read_text(encoding='utf-8'))
    rv = load_json(a.reviews)
    parsed = json.loads(Path(a.parsed).read_text(encoding='utf-8'))
    merged = json.loads(Path(a.merged).read_text(encoding='utf-8'))
    out = analyse(rv, parsed, merged, themes)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    over = [(i, v['n_fetched'], merged[i]['a_n']) for i, v in out.items() if v['n_fetched'] > merged[i]['a_n']]
    print(len(out), 'analysed;', sum(v['n_fetched'] for v in out.values()), 'reviews;', 'still n_fetched > a_n:', len(over), over[:5])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
