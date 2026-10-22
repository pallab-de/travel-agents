#!/usr/bin/env python
"""MAINTAINERS' TOOL: (re)build the frozen calibration fixtures under ../calibration/. Users never need to run this.

For each domain it builds a synthetic, internally consistent CLEAN world, asserts the clean world passes the deterministic audit (so any finding a
reviewer reports on a clean element is a false positive), seeds known defects, grades each defect

    easy   - a deterministic audit in this plugin also catches it
    subtle - only reasoning against the evidence catches it

and writes ONLY the seeded result plus a hashed answer key (key.json: SHA-256 of (domain, cell, [code]) - no plaintext answers). The clean version is
never written to disk, so there is nothing to diff the seeded copy against. (The generator itself is deterministic, so a determined reader could re-run
it; the reviewer is told to read nothing else, and calibrate.py score audits `inputs_seen`.)

  python build_fixtures.py [--domain stay|itinerary|all] [--seed N] [--selftest]     dry run: temp folder, frozen calibration untouched
  python build_fixtures.py --write [...]                                                      re-freeze calibration/ (maintainers only)
"""
import argparse, hashlib, json, random, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SK = HERE.parents[1]
sys.path[:0] = [str(SK.parent / 'tools'), str(SK / 'place-matching' / 'scripts'), str(SK / 'report-builder' / 'scripts'), str(SK / 'report-review' / 'scripts'),
                str(SK / 'report-builder' / 'template' / 'generic'), str(SK / 'fact-guard' / 'scripts'), str(HERE)]
import build_page as bp  # noqa: E402
import guard  # noqa: E402
import fixture_stay as fs  # noqa: E402
import fixture_itinerary as fi  # noqa: E402

CAL = HERE.parent / 'calibration'
SALT = 'claim-review-fixture-v1'


def hh(*p): return hashlib.sha256(('|'.join(str(x).lower() for x in p) + '|' + SALT).encode()).hexdigest()[:16]
def prefixes(row_id):
    segs = str(row_id).split('-'); return ['-'.join(segs[:i]) for i in range(len(segs), 0, -1)]
def write_json(path, obj): path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding='utf-8')


def manifest(dirpath):
    files = sorted(p for p in dirpath.rglob('*') if p.is_file() and p.name not in ('key.json', 'manifest.json'))
    return {str(p.relative_to(dirpath)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


import datetime as _dt
STAY_AT = _dt.datetime(2030, 5, 10, tzinfo=_dt.timezone.utc)   # the fixture's own clock: its review files and captures are dated May 2030
STAY_EASY = {'p07': 'drive', 'p08': 'at most 0.2'}   # what fact-guard's table and ranking checks say about the easy defects


def build_stay(seed):
    import tempfile, shutil
    out = CAL / 'stay'
    design = json.loads((SK / 'fact-guard' / 'examples' / 'spec.json').read_text(encoding='utf-8'))['design']
    T = fs.world()
    clean = fs.build_spec(T, design)

    def check(spec, T_):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            for name, doc in T_['reviews'].items(): write_json(td / 'reviews' / f'{name}.json', doc)
            write_json(td / 'trip_intake.json', fs.trip_intake())
            pol = guard.policy(spec)
            return (bp.validate_spec(spec) + guard.policy_fails(spec) + guard.score_gate(spec, td, pol, STAY_AT) + guard.concern_gate(spec, td, pol)
                    + guard.table_drift(spec, pol) + guard.intake_gate(spec, td) + guard.criteria_fails(spec))
    bad = check(clean, T)
    if bad: raise SystemExit('the CLEAN stay report is not clean (fix the generator):\n' + '\n'.join(bad))
    spec2, T2, D = fs.plant(clean, T, random.Random(seed))
    caught = ' '.join(check(spec2, T2))
    defects = []
    for d in D:
        if d['easy'] and STAY_EASY[d['id']] not in caught:
            raise SystemExit(f"defect {d['id']} is marked easy but fact-guard's checks do not catch it")
        defects.append({'cell': hh('stay', 'doc', d['id']), 'code': hh('stay', 'doc', d['id'], d['code']), 'tier': 'easy' if d['easy'] else 'subtle', 'klass': d['klass'],
                        'code_family': d['code'][0], 'parents': [hh('stay', 'doc', q) for q in prefixes(d['id'])[1:]]})
    decoys = [hh('stay', 'doc', c) for c in fs.DECOYS]
    for f in out.rglob('*'):
        if f.is_file() and f.name != 'README.md': f.unlink()
    write_json(out / 'spec.json', spec2)
    (out / 'report.html').write_text(bp.render_page(spec2), encoding='utf-8')
    shutil.copy(SK / 'fact-guard' / 'examples' / 'hero-placeholder.png', out / 'hero-placeholder.png')
    write_json(out / 'trip_intake.json', fs.trip_intake())
    for name, doc in T2['reviews'].items(): write_json(out / 'reviews' / f'{name}.json', doc)
    for name, obj in fs.evidence_files(T2).items(): write_json(out / 'evidence' / name, obj)
    write_json(out / 'key.json', {'domain': 'stay', 'defects': defects, 'decoys': decoys})
    write_json(out / 'manifest.json', manifest(out))
    ret = {'D': D, 'decoy_ids': [('doc', c) for c in fs.DECOYS], 'all_ids': [('doc', c['id']) for b in spec2['blocks'] if b['type'] == 'cards' for c in b['items']]}
    print(f"stay: {len(defects)} defects ({sum(d['tier'] == 'easy' for d in defects)} easy / {sum(d['tier'] == 'subtle' for d in defects)} subtle), {len(decoys)} decoy cards, clean spec passes the score, concern, table and ranking checks")
    return ret


def spec_ids(spec):
    """Every row id a reviewer may cite: days, stops, bookings, log entries, notes, and the booking block itself."""
    ids = []
    for blk in spec['blocks']:
        t = blk.get('type')
        if t == 'booking_ledger':
            ids += [blk['id']] + [x['id'] for x in blk['bookings']]
        if t == 'day_plan':
            for d in blk['days']:
                ids += [d['id']] + [x['id'] for x in d['stops']]
        if t == 'decision_log':
            ids += [x['id'] for x in blk['entries']]
        if t == 'research_notes':
            ids += [x['id'] for x in blk['notes']]
    return ids


EASY_MARKERS = {'day-4': 'is a Thu', 'bookings': 'does not match the sum', 'bk-h2': 'after check_in'}


def build_itinerary(seed):
    out = CAL / 'itinerary'
    design = json.loads((SK / 'fact-guard' / 'examples' / 'spec.json').read_text(encoding='utf-8'))['design']
    T = fi.decorate(fi.finish(fi.world()))
    clean = fi.build_spec(T, design)
    bad = bp.validate_spec(clean) + guard.plan_gate(clean) + guard.booking_ledger_gate(clean) + guard.door_to_door_gate(clean)
    if bad: raise SystemExit('the CLEAN itinerary is not clean (fix the generator):\n' + '\n'.join(bad))
    T2, D = fi.plant(T, random.Random(seed))
    spec2 = fi.build_spec(T2, design)
    caught = ' '.join(guard.plan_gate(spec2) + guard.booking_ledger_gate(spec2))
    defects = []
    for d in D:
        if d['easy'] and EASY_MARKERS[d['id']] not in caught:
            raise SystemExit(f"defect {d['id']} is marked easy but fact-guard's plan/booking checks do not catch it")
        defects.append({'cell': hh('itinerary', 'doc', d['id']), 'code': hh('itinerary', 'doc', d['id'], d['code']), 'tier': 'easy' if d['easy'] else 'subtle', 'klass': d['klass'],
                        'code_family': d['code'][0], 'parents': [hh('itinerary', 'doc', q) for q in prefixes(d['id'])[1:]]})
    decoys = [hh('itinerary', 'doc', c) for c in fi.DECOYS]
    for f in out.rglob('*'):
        if f.is_file() and f.name != 'README.md': f.unlink()
    (out / 'evidence').mkdir(parents=True, exist_ok=True)
    write_json(out / 'spec.json', spec2)
    html2 = bp.render_page(spec2)
    (out / 'report.html').write_text(html2, encoding='utf-8')
    for name, obj in fi.evidence_files(T2).items(): write_json(out / 'evidence' / name, obj)
    (out / 'evidence' / 'RESEARCH.md').write_text(T2['research'], encoding='utf-8')
    (out / 'evidence' / 'BRIEF.md').write_text(T2['brief'], encoding='utf-8')
    write_json(out / 'key.json', {'domain': 'itinerary', 'defects': defects, 'decoys': decoys})
    write_json(out / 'manifest.json', manifest(out))
    ret = {'D': D, 'decoy_ids': [('doc', c) for c in fi.DECOYS], 'all_ids': [('doc', i) for i in spec_ids(spec2)]}
    print(f"itinerary: {len(defects)} defects ({sum(d['tier'] == 'easy' for d in defects)} easy / {sum(d['tier'] == 'subtle' for d in defects)} subtle), {len(decoys)} decoy rows, clean spec passes the plan and booking checks")
    return ret


def pin_key(domain):
    """Record the new key's hash in calibrate.py's PINNED_KEYS (a maintainers' re-freeze changes both, visibly)."""
    import re, calibrate
    sha = calibrate.key_sha(domain)
    src = (HERE / 'calibrate.py').read_text(encoding='utf-8')
    m = re.search(r'^PINNED_KEYS = \{(.*?)\}$', src, re.M | re.S)
    pins = dict(re.findall(r"'(\w+)': '([0-9a-f]{64})'", m.group(1)))
    pins[domain] = sha
    body = ', '.join(f"'{k}': '{v}'" for k, v in sorted(pins.items()))
    (HERE / 'calibrate.py').write_text(src[:m.start()] + 'PINNED_KEYS = {' + body + '}' + src[m.end():], encoding='utf-8')
    print(f'pinned {domain} key {sha[:12]}... in calibrate.py')


def selftest(domain, ret):
    """The calibration mechanism must itself behave: an oracle ledger passes; flag-everything and empty ledgers fail."""
    import tempfile, calibrate
    calibrate.CAL = CAL   # score against the key just built (the dry-run folder), never the frozen one by accident
    tmp = Path(tempfile.mkdtemp())
    oracle = [{'where': l, 'row_id': i, 'code': c} for (l, i, c) in [(d['list'], d['id'], d['code']) for d in ret['D']]]
    spam = [{'where': l, 'row_id': i, 'code': 'C1'} for l, i in ret['all_ids']]
    cases = {'oracle': (oracle, True), 'flag-everything': (spam, False), 'empty': ([], False)}
    for name, (fnd, want) in cases.items():
        f = tmp / f'{domain}-{name}.json'; f.write_text(json.dumps({'findings': fnd, 'inputs_seen': []}), encoding='utf-8')
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): rc = calibrate.score(domain, str(f), str(tmp))
        got = rc == 0
        print(f"  selftest {domain}/{name}: {'CALIBRATED' if got else 'not calibrated'} (expected {'CALIBRATED' if want else 'not calibrated'}) {'OK' if got == want else 'MISMATCH'}")
        if got != want: print(buf.getvalue()); raise SystemExit(1)


def main():
    global CAL
    if sys.argv[1:] == ['selftest']:   # the plugin's standard spelling
        sys.argv[1:] = ['--selftest']
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--domain', default='all', choices=['stay', 'itinerary', 'all']); ap.add_argument('--seed', type=int, default=2026); ap.add_argument('--selftest', action='store_true', help='also test the scorer with an oracle, a flag-everything and an empty ledger')
    ap.add_argument('--write', action='store_true', help='overwrite the FROZEN fixtures in calibration/ (maintainers only; changes what "calibrated" means). Without it, everything is built in a temp folder and nothing frozen is touched.')
    a = ap.parse_args()
    # Fail-closed by default: a plain run or a --selftest must never rewrite the frozen calibration, because doing so
    # silently changes the answer key that every reviewer is scored against.
    if not a.write:
        import tempfile
        CAL = Path(tempfile.mkdtemp(prefix='calibration-dry-run-'))
        print(f'dry run: fixtures built in {CAL}; the frozen calibration/ folder is untouched (add --write to re-freeze)')
    for dom, fn in (('stay', build_stay), ('itinerary', build_itinerary)):
        if a.domain in (dom, 'all'):
            ret = fn(a.seed)
            if a.write: pin_key(dom)
            else:
                import calibrate as _cal; _cal.PINNED_KEYS[dom] = _cal.key_sha(dom)   # a dry run scores against the key it just built
            if a.selftest: selftest(dom, ret)


if __name__ == '__main__':
    main()
