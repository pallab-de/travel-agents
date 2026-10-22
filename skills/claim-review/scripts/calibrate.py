#!/usr/bin/env python
"""Prove a reviewer can catch seeded mistakes BEFORE its verdict on a real deliverable is believed.

  calibrate.py prepare --domain stay|itinerary --out DIR     copy the frozen fixture (without the answer key) to DIR for the reviewer
  calibrate.py score   --domain stay|itinerary --ledger DIR/ledger.json --dir DIR
  calibrate.py verify  --domain stay|itinerary               check the frozen fixture files were not edited

Design:
  * The fixture is frozen and synthetic; the clean version is never stored, so there is nothing to diff the seeded copy against.
  * All evidence, including a stand-in for Google, is inside the fixture: no network, no real project files, nothing else to open.
  * The answer key is hashed (SHA-256 of domain|cell[|code]); the reviewer can't read the answers, and the scorer only ever compares hashes.
  * Items are identified by their stable id (`row_id`), never by a free-text name (a reviewer shortens names; a name match then misses real catches).
  * Decoys - elements that are correct but look suspicious - measure false positives, so "flag everything" can't pass.
  * Defects are graded easy (a deterministic audit also catches it) or subtle (only reasoning does). The pass gate is on subtle recall, because
    that is the reasoning the audits can't replace.
  * `inputs_seen` is audited: opening anything outside the prepared folder (and the named briefs/scripts) fails the run.

Gate: overall recall >= 0.80, subtle recall >= 0.70, the right code on at least 70% of the defects found, at most 2 findings on
decoys, no inputs outside the fixture. (The code says what kind of mistake it is; a reviewer that points at the right place
but names the wrong problem has not understood it.)
"""
import argparse, datetime as dt, hashlib, json, re, shutil, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CAL = HERE.parent / 'calibration'
SALT = 'claim-review-fixture-v1'
# The answer key's own hash, pinned here as well as in the fixture folder: editing key.json to make a reviewer pass changes
# this file in the same commit, where a reviewer of the change can see it. build_fixtures.py --write rewrites the dict.
PINNED_KEYS = {'itinerary': '44769dbdbcf6a59c842a8b29c3cb66d84a2b6d20f8d26c39faaec8a77262f90d', 'stay': '1a790a953632f1010ba2824893d99e57f659f2492d0bc1afc08600eef5c287bb'}
HIERARCHICAL = ('itinerary', 'stay')   # a finding on a container (a card, a day) speaks for the rows inside it
ROLE_FAMILY = {'facts': 'C', 'judgement': 'L', 'plan': None, 'content': None}
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')


def hh(*p): return hashlib.sha256(('|'.join(str(x).lower() for x in p) + '|' + SALT).encode()).hexdigest()[:16]


def prefixes(row_id):
    """'day-2-1030' -> ['day-2-1030', 'day-2', 'day']: a finding on an element also speaks for the containers it sits in."""
    segs = str(row_id).strip().split('-')
    return ['-'.join(segs[:i]) for i in range(len(segs), 0, -1)]


def prepare(domain, out):
    src = CAL / domain
    if not src.exists(): raise SystemExit(f'no fixture at {src}')
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    for p in src.rglob('*'):
        if p.is_file() and p.name not in ('key.json', 'manifest.json', 'README.md'):
            (out / p.relative_to(src)).parent.mkdir(parents=True, exist_ok=True); shutil.copy2(p, out / p.relative_to(src))
    print(f'prepared {domain} fixture in {out}\n  give the reviewer ONLY this folder (plus the brief files). It should write {out / "ledger.json"}.')
    return 0


def key_sha(domain):
    return hashlib.sha256((CAL / domain / 'key.json').read_bytes()).hexdigest()


def verify(domain):
    d = CAL / domain
    m = json.loads((d / 'manifest.json').read_text(encoding='utf-8'))
    bad = [k for k, v in m.items() if not (d / k).exists() or hashlib.sha256((d / k).read_bytes()).hexdigest() != v]
    present = {f.relative_to(d).as_posix() for f in d.rglob('*') if f.is_file()}
    bad += sorted(present - set(m) - {'key.json', 'manifest.json', 'README.md'})  # same exclusions prepare() uses; any other unlisted file is an edit
    if PINNED_KEYS.get(domain) != key_sha(domain):
        bad.append('key.json (its hash is not the one pinned in calibrate.py)')
    print('fixture intact' if not bad else f'fixture EDITED: {bad}')
    return 1 if bad else 0


def inside(q, root, allowed):
    """Is path q inside the prepared folder (or one of the exact brief files)? Relative paths are read against the
    folder; `..`, sibling folders that share a prefix, and any other absolute path are outside."""
    qp = Path(str(q))
    if not qp.is_absolute():
        qp = Path(root) / qp
    qp = qp.resolve()
    if qp in allowed:
        return True
    try:
        return qp.is_relative_to(Path(root).resolve())
    except ValueError:
        return False


def grade(domain, L, prepared, role='content', brief_files=()):
    """Score a reviewer's ledger against the answer key. Pure: no printing, no writing, so the review gate can
    run it again on the stored ledger and get the same answer."""
    key = json.loads((CAL / domain / 'key.json').read_text(encoding='utf-8'))
    findings = L.get('findings', [])
    fx = []
    for f in findings:
        where = str(f.get('where') or ('doc' if domain in HIERARCHICAL else '')).strip().split('/')[0].lower()
        rid = str(f.get('row_id') or f.get('row') or '')
        fx.append({'cells': [hh(domain, where, c) for c in prefixes(rid)] if domain in HIERARCHICAL else [hh(domain, where, rid)], 'exact': hh(domain, where, rid),
                   'code': str(f.get('code') or ''), 'rid': rid, 'where': where, 'used': False})
    decoy = set(key['decoys'])
    results = []
    for d in key['defects']:
        parents = set(d.get('parents', []))
        hit = None
        for test in (lambda f: f['exact'] == d['cell'], lambda f: d['cell'] in f['cells'], lambda f: f['exact'] in parents):   # same element, a finding inside it, a finding on its container
            hit = next((f for f in fx if not f['used'] and test(f)), None)
            if hit: break
        code_ok = bool(hit) and any(hh(domain, hit['where'], q, hit['code']) == d['code'] for q in (prefixes(hit['rid']) if domain in HIERARCHICAL else [hit['rid']]))
        if hit: hit['used'] = True
        results.append({'tier': d['tier'], 'klass': d['klass'], 'family': d['code_family'], 'found': bool(hit), 'code_ok': code_ok})
    fps = sum(1 for f in fx if any(c in decoy for c in f['cells']))
    allowed = {Path(b).resolve() for b in brief_files}
    outside = []
    for kind, paths in (('read', L.get('inputs_seen', [])), ('wrote', L.get('outputs_written', []))):
        for p_ in paths:
            if not inside(p_, prepared, allowed if kind == 'read' else set()):
                outside.append(f'{kind}: {p_}')
    fam_key = ROLE_FAMILY.get(role)
    scope = [r for r in results if fam_key is None or r['family'] == fam_key]
    s_sub = [r for r in scope if r['tier'] == 'subtle']
    subtle_recall = sum(r['found'] for r in s_sub) / len(s_sub) if s_sub else 1.0
    recall = sum(r['found'] for r in scope) / max(len(scope), 1)
    found = [r for r in scope if r['found']]
    code_share = sum(r['code_ok'] for r in found) / len(found) if found else 0.0
    ok = recall >= 0.80 and subtle_recall >= 0.70 and code_share >= 0.70 and fps <= 2 and not outside
    return {'results': results, 'findings': len(findings), 'decoys': len(decoy), 'fps': fps, 'outside': outside, 'recall': recall,
            'subtle_recall': subtle_recall, 'code_share': code_share, 'ok': ok, 'fam_key': fam_key, 'scope': len(scope)}


def score(domain, ledger_path, prepared, role='content', briefs=(), model=None, out=None):
    if not prepared:
        raise SystemExit('score needs --dir: the prepared folder the reviewer was given, so its inputs can be audited')
    L = json.loads(Path(ledger_path).read_text(encoding='utf-8'))
    g = grade(domain, L, prepared, role, briefs)
    results, fps, outside = g['results'], g['fps'], g['outside']
    tot = len(results); got = sum(r['found'] for r in results)
    def rec(pred):
        rs = [r for r in results if pred(r)]; return (sum(r['found'] for r in rs), len(rs))
    easy, subtle = rec(lambda r: r['tier'] == 'easy'), rec(lambda r: r['tier'] == 'subtle')
    fam = {f: rec(lambda r, f=f: r['family'] == f) for f in ('C', 'L')}
    print(f'{domain}: findings {g["findings"]}; defects found {got}/{tot}  (easy {easy[0]}/{easy[1]}, subtle {subtle[0]}/{subtle[1]}; consistency C {fam["C"][0]}/{fam["C"][1]}, logic L {fam["L"][0]}/{fam["L"][1]})')
    print(f'  right code on {sum(r["code_ok"] for r in results)}/{tot} found; findings on decoys (false positives): {fps}/{g["decoys"]}')
    missed = [f"{r['klass']}({r['tier']})" for r in results if not r['found']]
    print('  missed:', ', '.join(missed) if missed else 'none')
    if outside: print('  FILES OUTSIDE THE FIXTURE (fails the run regardless of findings):', outside)
    if role != 'content':
        print(f'  role {role}: gated on {"all" if g["fam_key"] is None else g["fam_key"]} defects: recall {g["recall"]:.2f}, subtle recall {g["subtle_recall"]:.2f}, right code {g["code_share"]:.2f}')
    if out:
        out = Path(out)
        ledger_copy = out.with_name(out.stem + '.ledger.json')
        ledger_copy.write_bytes(Path(ledger_path).read_bytes())
        out.write_text(json.dumps({
            'domain': domain, 'role': role, 'calibrated': g['ok'], 'recall': round(g['recall'], 3),
            'subtle_recall': round(g['subtle_recall'], 3), 'code_share': round(g['code_share'], 3), 'decoy_findings': fps, 'inputs_outside': outside,
            'key_sha256': key_sha(domain), 'audit_ran': True, 'prepared_dir': str(Path(prepared).resolve()),
            'brief_paths': [str(Path(b).resolve()) for b in briefs],
            'briefs': {Path(b).name: hashlib.sha256(Path(b).read_bytes()).hexdigest() for b in briefs},
            'model_requested': model, 'ledger_file': ledger_copy.name, 'ledger_sha256': hashlib.sha256(ledger_copy.read_bytes()).hexdigest(),
            'fixture_salt': SALT, 'scored_at': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}, indent=2), encoding='utf-8')
        print(f'  result written to {out} (review_gate.py reads it and scores the stored ledger again: a verdict needs a current calibration for each reviewer)')
    print('CALIBRATED' if g['ok'] else 'NOT CALIBRATED: this reviewer setup may not be trusted on a real deliverable (gate: recall>=0.80, subtle recall>=0.70, right code>=0.70 of those found, decoy findings<=2, no outside inputs)')
    return 0 if g['ok'] else 1


def regrade(cal_path):
    """Score the stored ledger of a calibration result again. Returns the reasons it can't be trusted (empty if it
    holds): the ledger is missing or changed, the audit didn't run, or the recomputed score isn't the claimed one."""
    cal_path = Path(cal_path)
    cal = json.loads(cal_path.read_text(encoding='utf-8'))
    fails = []
    if not cal.get('ledger_file'):
        return ['the calibration has no stored ledger to score again']
    led = cal_path.with_name(str(cal['ledger_file']))
    if not led.is_file():
        return ['the calibration has no stored ledger to score again']
    if hashlib.sha256(led.read_bytes()).hexdigest() != cal.get('ledger_sha256'):
        return ['the stored calibration ledger changed after it was scored']
    if cal.get('key_sha256') != PINNED_KEYS.get(cal.get('domain')):
        return ['the calibration was scored against an answer key that is not the pinned one']
    if not cal.get('audit_ran') or not cal.get('prepared_dir'):
        return ['the calibration never audited the reviewer\'s inputs (score needs --dir)']
    g = grade(cal['domain'], json.loads(led.read_text(encoding='utf-8')), cal['prepared_dir'], cal.get('role', 'content'), cal.get('brief_paths') or [])
    if bool(cal.get('calibrated')) != g['ok'] or abs(float(cal.get('recall', -1)) - g['recall']) > 0.01 or abs(float(cal.get('subtle_recall', -1)) - g['subtle_recall']) > 0.01:
        fails.append(f"the stored calibration says recall {cal.get('recall')}, subtle {cal.get('subtle_recall')}, calibrated {cal.get('calibrated')}; "
                     f"scoring its ledger again gives {g['recall']:.2f}, {g['subtle_recall']:.2f}, {g['ok']}")
    return fails


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for n in ('prepare', 'score', 'verify'):
        p = sub.add_parser(n); p.add_argument('--domain', required=True, choices=['stay', 'itinerary'])
        if n == 'prepare': p.add_argument('--out', required=True)
        if n == 'score':
            p.add_argument('--ledger', required=True); p.add_argument('--dir', required=True)
            p.add_argument('--role', default='content', choices=sorted(ROLE_FAMILY))
            p.add_argument('--brief', action='append', default=[], help='each brief file the reviewer was given (hashed into the result)')
            p.add_argument('--model', help='the model the reviewer was asked to run on (recorded, not provable)')
            p.add_argument('--out', help='write the result here for review_gate.py')
    a = ap.parse_args()
    return {'prepare': lambda: prepare(a.domain, a.out), 'verify': lambda: verify(a.domain),
            'score': lambda: score(a.domain, a.ledger, a.dir, a.role, a.brief, a.model, a.out)}[a.cmd]()


if __name__ == '__main__':
    raise SystemExit(main())
