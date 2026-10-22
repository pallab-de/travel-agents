#!/usr/bin/env python
"""Checks a research approach plan against what actually happened. See ../SKILL.md.

  python check_approach.py plan.json fetch_log.json
  python check_approach.py selftest

FAILS (exit 1) on: a plan with no sources, or an unfilled one (a blank or duplicated name, expected_min_yield <= 0);
a plan not dated before the first fetch; a planned source never attempted; an attempted source whose actual_yield is
well under its expected_min_yield with no reason given. WARNS (does not fail) on: a source in the log that was never in
the plan (fine to have adapted; update the plan so it's visible next time).

plan.json:      {"created_at": "<ISO time>", "sources": [{"name", "expected_min_yield", "open_ended": bool, "stop_rule": "..."}]}
fetch_log.json: {"sources": [{"name": "...", "attempted": true, "actual_yield": 0, "reason": "", "at": "<ISO time>"}]}
A yield under UNDER_FRACTION of expected (default 0.5, i.e. less than half) needs a non-empty "reason".
"""
from __future__ import annotations
import argparse, datetime as dt, json, sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

UNDER_FRACTION = 0.5


def evaluate(plan, log, under_fraction=UNDER_FRACTION):
    """-> (planned by name, logged by name, errors, warnings)."""
    errs, warns = [], []
    sources = plan.get('sources') or []
    if not sources:
        errs.append('the plan lists no sources: there is nothing to compare the run against')
    names = [str(s.get('name') or '').strip() for s in sources]
    if any(not n for n in names):
        errs.append('a planned source has no name (is this the unedited template?)')
    dup = sorted({n for n in names if n and names.count(n) > 1})
    if dup:
        errs.append(f'duplicate planned source name(s): {dup}')
    for s in sources:
        try:
            positive = float(s.get('expected_min_yield', 0)) > 0
        except (TypeError, ValueError):
            positive = False
        if not positive:
            errs.append(f'"{s.get("name")}": expected_min_yield must be a number above 0 (say what a normal run returns)')
    planned = {n: s for n, s in zip(names, sources) if n}
    logged = {str(s.get('name') or '').strip(): s for s in log.get('sources', [])}
    created = plan.get('created_at')
    def when(v):   # an ISO time with an offset, or a date (read as UTC midnight); None when it is neither
        try:
            t = dt.datetime.fromisoformat(str(v).replace('Z', '+00:00'))
        except ValueError:
            return None
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    firsts = sorted(t for t in (when(e.get('at')) for e in log.get('sources', []) if e.get('at')) if t)
    untimed = [e.get('name') for e in log.get('sources', []) if e.get('attempted') and not when(e.get('at'))]
    if not created or when(created) is None:
        errs.append('plan.json has no created_at as an ISO time: a plan written after the fetch proves nothing')
    elif untimed:
        errs.append(f'fetch_log entries without a usable "at" time ({untimed[:3]}): the plan cannot be shown to come first')
    elif firsts and when(created) >= firsts[0]:
        errs.append(f'the plan (created {created}) was not written before the first fetch ({firsts[0].isoformat()})')

    for name, s in planned.items():
        entry = logged.get(name)
        if not entry or not entry.get('attempted'):
            errs.append(f'"{name}" is in the plan but was never attempted (missing or attempted=false in the log)')
            continue
        try:
            exp = float(s.get('expected_min_yield', 0))
        except (TypeError, ValueError):
            continue
        got = entry.get('actual_yield', 0) or 0
        if exp > 0 and got < exp:
            if got < exp * under_fraction and not (entry.get('reason') or '').strip():
                errs.append(f'"{name}": expected >= {exp:g}, got {got} (under {under_fraction:.0%} of expected) with no reason recorded')
            else:
                warns.append(f'"{name}": expected >= {exp:g}, got {got} - under target but a reason is recorded, or it\'s close enough')

    for name in logged:
        if name not in planned:
            warns.append(f'"{name}" was fetched but was never in the plan - fine if deliberate, but add it to plan.json so it\'s visible next time')

    for n, s in planned.items():
        if s.get('open_ended') and not str(s.get('stop_rule') or '').strip():
            errs.append(f'"{n}" is open-ended (a sweep, a multi-page search) but has no stop_rule: say when it stops, or it stops when it feels done')
    return planned, logged, errs, warns


def selftest() -> int:
    good = {'created_at': '2030-01-01T00:00:00Z', 'sources': [{'name': 'A', 'expected_min_yield': 10}]}
    log = {'sources': [{'name': 'A', 'attempted': True, 'actual_yield': 12, 'at': '2030-01-02T00:00:00Z'}]}
    cases = [('a filled plan with a matching log passes', not evaluate(good, log)[2]),
             ('an empty plan fails', bool(evaluate({'created_at': 'x', 'sources': []}, log)[2])),
             ('the unedited template fails', bool(evaluate({'sources': [{'name': '', 'expected_min_yield': 0}]}, log)[2])),
             ('duplicate source names fail', bool(evaluate(dict(good, sources=good['sources'] * 2), log)[2])),
             ('a plan written after the first fetch fails', any('before the first fetch' in e for e in evaluate(dict(good, created_at='2030-02-01T00:00:00Z'), log)[2])),
             ('a planned source never attempted fails', bool(evaluate(good, {'sources': []})[2])),
             ('a yield far under target with no reason fails', bool(evaluate(good, {'sources': [dict(log['sources'][0], actual_yield=1)]})[2])),
             ('an open-ended source with no stop rule fails and names the source', any('grid' in e for e in evaluate(dict(good, sources=[{'name': 'grid', 'expected_min_yield': 5, 'open_ended': True}]), {'sources': [{'name': 'grid', 'attempted': True, 'actual_yield': 9}]})[2])),
             ('a plan written after the fetch fails even when the offsets differ', any('before the first fetch' in e for e in evaluate(dict(good, created_at='2030-01-02T05:00:00+05:00'), dict(log, sources=[dict(log['sources'][0], at='2030-01-01T23:00:00Z')]))[2])),
             ('a log entry with no time cannot show the plan came first', any('usable' in e for e in evaluate(good, {'sources': [{'name': 'A', 'attempted': True, 'actual_yield': 12}]})[2])),
             ('a source with no name does not crash', bool(evaluate({'created_at': 'x', 'sources': [{'expected_min_yield': 3}]}, log)[2]))]
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


def main() -> int:
    if sys.argv[1:] == ['selftest']:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('plan'); ap.add_argument('log')
    ap.add_argument('--under-fraction', type=float, default=UNDER_FRACTION,
                     help='a yield below this fraction of expected_min_yield needs a stated reason (default 0.5)')
    a = ap.parse_args()
    plan = json.loads(Path(a.plan).read_text(encoding='utf-8'))
    log = json.loads(Path(a.log).read_text(encoding='utf-8'))
    planned, logged, errs, warns = evaluate(plan, log, a.under_fraction)
    print(f"{len(planned)} planned source(s), {len(logged)} logged.")
    for w in warns: print('WARN ', w)
    for e in errs: print('ERROR', e)
    print('\nAPPROACH OK' if not errs else f'\n{len(errs)} problem(s): coverage of the plan does not check out')
    return 1 if errs else 0


if __name__ == '__main__':
    raise SystemExit(main())
