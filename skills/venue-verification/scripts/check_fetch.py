#!/usr/bin/env python
"""Content sanity check for a fetched community page (Reddit, a listicle, a forum thread) — the vacation-planner
counterpart of listing-search's schema-coverage check. There's no fixed field schema to check for a page like this
(every site's markup differs), so the check here is coarser and different in kind: did this fetch actually return
real content, or an empty/blocked/CAPTCHA/login-wall page that a successful navigation can still produce? A page not
erroring is not evidence its content is real. See fetch-integrity's SKILL.md for why this matters.

Usage: pass it the page's visible text (Playwright: page.inner_text('body'), or a browser_evaluate returning
document.body.innerText) and, optionally, a term you expect to appear (the place/city you searched for).

  python check_fetch.py page_text.txt --expect "<City>"
  # or import: from check_fetch import check_fetch; ok, why = check_fetch(text, expect="<City>")
"""
from __future__ import annotations
import argparse, re, sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

MIN_CHARS = 400   # a real Reddit thread / listicle page is almost always much longer than this; a blocked page rarely is

BLOCK_MARKERS = [
    r'\bverify you are human\b', r'\bare you a robot\b', r'\bcomplete the captcha\b', r'\brecaptcha\b',
    r'\benable javascript\b.{0,40}\bcontinue\b', r'\bjavascript is disabled\b',
    r'\baccess denied\b', r'\b403 forbidden\b', r'\b429\b.{0,20}\btoo many requests\b', r'\brate.?limited\b',
    r'\bsign in to (continue|view)\b', r'\blog in to (continue|view|see)\b', r'\bplease log in\b',
    r'\bthis (page|content) (is|has been) (blocked|removed|unavailable)\b', r'\bnetwork security\b.{0,20}\bblock',
    r'\bunusual traffic\b', r'\bcloudflare\b.{0,20}\bcheck', r'\bchecking your browser\b',
]


def check_fetch(text: str, expect: str | None = None) -> tuple[bool, str]:
    text = text or ''
    stripped = re.sub(r'\s+', ' ', text).strip()
    if len(stripped) < MIN_CHARS:
        return False, f'only {len(stripped)} chars of visible text (need >= {MIN_CHARS}) — likely an empty, still-loading, or blocked page'
    low = stripped.lower()
    for pat in BLOCK_MARKERS:
        m = re.search(pat, low)
        if m:
            return False, f'matched a block/CAPTCHA/login-wall marker: "{m.group(0)}" — this page loaded but is not the real content'
    if expect and expect.lower() not in low:
        return False, f'expected term "{expect}" not found anywhere in the fetched text — probably the wrong page, a redirect, or a region-locked variant'
    return True, f'{len(stripped)} chars, no block markers' + (f', "{expect}" found' if expect else '')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('textfile', help='file holding the page\'s visible text (- for stdin)')
    ap.add_argument('--expect', help='a term that must appear somewhere in the text (e.g. the city/place you searched for)')
    a = ap.parse_args()
    text = sys.stdin.read() if a.textfile == '-' else Path(a.textfile).read_text(encoding='utf-8', errors='replace')
    ok, why = check_fetch(text, a.expect)
    print(('PASS: ' if ok else 'FAIL: ') + why)
    if not ok:
        print('Do not treat this fetch as real content. Re-navigate (a fresh session/user-agent, or space out requests — '
              'see venue-verification\'s browser item on spacing out navigations) before retrying.')
    return 0 if ok else 1


def selftest() -> int:
    real = 'Things to do in Valmora. ' + 'A long thread about the old fort and the harbour market. ' * 12
    cases = [('a long page with the expected term passes', check_fetch(real, 'Valmora')[0]),
             ('a short page fails', not check_fetch('Valmora', 'Valmora')[0]),
             ('a captcha page fails', not check_fetch(real + ' Please verify you are human to continue.', 'Valmora')[0]),
             ('a login wall fails', not check_fetch(real + ' Sign in to continue reading.', 'Valmora')[0]),
             ('a missing expected term fails', not check_fetch(real, 'Selmere')[0]),
             ('no expected term is allowed', check_fetch(real)[0])]
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(selftest() if sys.argv[1:] == ['selftest'] else main())
