#!/usr/bin/env python
"""Audit a built report against the rules this skill enforces, independently of the build that produced it.

The build refuses bad input. This audit checks the output, and the evidence beside it, so an agent that edited the
HTML, the spec, a photo or an evidence file by hand cannot get past it by skipping the build.

  python audit_report.py <project folder> <report.html> [--spec spec.json] [--kb path/RESEARCH.md]

Checks (each one a failure class that has occurred; the browser checks are listed in SKILL.md):
  output   the page embeds the spec it claims (fingerprint), and the spec still passes the build's rules
  photos   no hot-linked photo in the page; every local photo exists and still matches the sha256 of the file the
           build saved
  theme    the theme control is an icon only; the fonts come from Google Fonts only
  motion   the engine still carries the reveal, count-up and parallax behaviour (a regression guard)
  scores   fact-guard's score gate passes (same recomputation as the build), and recorded raw downloads match
  kb       every item has a dated RESEARCH.md section with its link (see fact-guard's pipeline check)
Exit 0 only if nothing fails. Each failure names the rule and the place.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(SKILLS / 'report-builder' / 'template' / 'generic'), str(SKILLS / 'fact-guard' / 'scripts')]
import build_page as bp  # noqa: E402
import guard  # noqa: E402


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def check(project, report, spec_name='spec.json', kb_path=None):
    fails = []
    project, report = Path(project), Path(report)
    html = report.read_text(encoding='utf-8')
    spec_path = project / spec_name
    spec = json.loads(spec_path.read_text(encoding='utf-8'))

    # output: the page belongs to this spec, and the spec still meets the rules
    fp = bp.spec_fingerprint(spec)
    if f'name="spec-fingerprint" content="{fp}"' not in html:
        fails.append('output: the report was built from a different spec (fingerprint mismatch). Rebuild it.')
    for e in bp.validate_spec(spec):
        fails.append(f'spec: {e}')
    m = re.search(r'<script type="application/json" id="spec">(.*?)</script>', html, re.S)
    if not m:
        fails.append('output: the page has no embedded spec')
    else:
        embedded = json.loads(m.group(1))   # the script-safe escapes (< and so on) are valid JSON
        if f'name="embedded-fingerprint" content="{bp.spec_fingerprint(embedded)}"' not in html:
            fails.append('output: the embedded data was edited after the build (embedded fingerprint mismatch)')
        mf = report.with_name(report.stem + '_assets') / 'manifest.json'
        if mf.exists():
            manifest_doc = json.loads(mf.read_text(encoding='utf-8'))
            expected_data = bp.apply_manifest(spec, manifest_doc)
            if expected_data != embedded:
                fails.append('output: the data embedded in the page is not what spec.json and the photo manifest produce; '
                             'the page was edited after the build, or built from a different spec. Rebuild it.')
            # the whole page, byte for byte: markup, engine script, CSS and fonts as well as the data
            if html != bp.assemble(spec, expected_data):
                fails.append('output: the page is not what this engine builds from spec.json (markup, script, CSS or data differ); '
                             'it was edited after the build, or built by another version of the engine. Rebuild it.')

    # photos: nothing hot-linked; local photos exist and are the bytes the manifest recorded
    for m in re.finditer(r'<img[^>]+src="(https?://[^"]+)"', html):
        fails.append(f'photos: hot-linked image {m.group(1)[:80]}')
    assets = report.with_name(report.stem + '_assets')
    manifest = assets / 'manifest.json'
    if not manifest.exists():
        fails.append('photos: no assets/manifest.json beside the report')
    else:
        for rec in json.loads(manifest.read_text(encoding='utf-8')):
            f = report.parent / rec['file']
            if not f.exists():
                fails.append(f'photos: {rec["file"]} is listed in the manifest but missing')
            elif not rec.get('saved_sha256'):
                fails.append(f'photos: {rec["file"]} has no saved_sha256 in the manifest (built by an older engine; rebuild)')
            elif hashlib.sha256(f.read_bytes()).hexdigest() != rec['saved_sha256']:
                fails.append(f'photos: {rec["file"]} was changed after the build (sha256 mismatch)')
    # theme and fonts
    if re.search(r'>\s*Theme\s*<', html):
        fails.append('theme: a text "Theme" button is in the page; the control must be an icon only')
    for href in re.findall(r'<link[^>]+href="(https?://[^"]+)"', html):
        if 'fonts.googleapis.com' not in href and 'fonts.gstatic.com' not in href:
            fails.append(f'fonts: a stylesheet or font is loaded from {href[:60]}; only Google Fonts is allowed')

    # motion: regression guard on the engine behaviour the design requires
    for token, what in (('data-reveal', 'scroll reveal'), ('countUp', 'KPI count-up'), ('data-parallax', 'hero parallax')):
        if token not in html:
            fails.append(f'motion: the page lacks {what} ({token})')

    # scores: recomputed by fact-guard's own score gate (one formula, one place; a separate pooled mean would reject
    # every correct multi-source or bumped score), plus each review file's raw
    # download, when one is recorded, still matching its hash.
    fails += [f'scores: {f}' for f in guard.score_gate(spec, project, guard.policy(spec))]
    for _, it in guard.items_of(spec):
        for src in (it.get('score_basis') or {}).get('sources', []):
            path = project / str(src.get('reviews_file', ''))
            if not src.get('reviews_file') or not path.exists():
                continue
            doc = json.loads(path.read_text(encoding='utf-8'))
            if doc.get('raw_file') and not (project / doc['raw_file']).exists():
                fails.append(f'scores: {it["name"]}: the raw download {doc["raw_file"]} is missing')
            elif doc.get('raw_file') and hashlib.sha256((project / doc['raw_file']).read_bytes()).hexdigest() != doc.get('raw_sha256'):
                fails.append(f'scores: {it["name"]}: the raw download no longer matches its recorded sha256')

    # kb: every item has a section with its link and a read date
    kb_file = Path(kb_path) if kb_path else project / 'RESEARCH.md'
    kb = kb_file.read_text(encoding='utf-8') if kb_file.exists() else ''
    fails += [f'kb: {m}' for m in guard.kb_fails(spec, kb)]
    return fails


def main():
    args = sys.argv[1:]
    spec_name, kb_path = 'spec.json', None
    if '--spec' in args:
        i = args.index('--spec'); spec_name = args[i + 1]; del args[i:i + 2]
    if '--kb' in args:
        i = args.index('--kb'); kb_path = args[i + 1]; del args[i:i + 2]
    if len(args) != 2:
        sys.exit(__doc__)
    fails = check(args[0], args[1], spec_name, kb_path)
    for f in fails:
        print('FAIL', f)
    print('AUDIT PASS' if not fails else f'AUDIT FAILED ({len(fails)} problem(s))')
    return 1 if fails else 0


def selftest():
    import tempfile, io, contextlib
    ex = Path(guard.__file__).resolve().parents[1] / 'examples'
    cases = []
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        spec = json.loads((ex / 'spec.json').read_text(encoding='utf-8'))
        cards = [b for b in spec['blocks'] if b.get('type') == 'cards'][0]
        second = json.loads(json.dumps(cards)); second['id'] = 'direct'
        second['items'] = [dict(second['items'][0], name='Direct Lead', url='https://stays.example-hotels.test/lead', rank=1)]
        spec['blocks'].append(second)
        (proj / 'spec.json').write_text(json.dumps(spec), encoding='utf-8')
        kb = ['## Aster Inn', 'https://stays.example-hotels.test/aster read 2030-05-06', '']
        (proj / 'RESEARCH.md').write_text(chr(10).join(kb), encoding='utf-8')
        report = proj / 'report.html'
        report.write_text('<html></html>', encoding='utf-8')
        fails = check(proj, report)
        cases.append(('an item in a second cards block with no KB section fails the audit', any(f.startswith('kb:') and 'Direct Lead' in f for f in fails)))
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(selftest() if sys.argv[1:] == ['selftest'] else main())
