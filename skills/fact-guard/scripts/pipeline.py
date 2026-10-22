#!/usr/bin/env python
"""fact-guard pipeline: plan, capture, verify and build a ranked, cited report from stored evidence.

One project folder holds the whole job:
  brief.json       the question, the audience, the criteria (written by the agent; checked for shape)
  spec.json        the report content: blocks, figures, links, and the policy (extra sources, banned phrases,
                   how labels are classified, required dossier sections). See examples/spec.json.
  captures/        capture store: each checkable figure, with its URL, time and the full raw page text it was read from
  review.json      verdicts derived from the store (never typed by hand)
  spot.plan.json   the deterministic spot-check sample; spot.task.json is the same rows as a task for the fresh second-look agent
                   (row, what, kind, source; no values); spot.json is written after that second look
  RESEARCH.md      knowledge base: one section per item, with sources, read dates and a verdict
  report.html      the built page, written only when the gate passes

Commands (PROJECT is the folder; run with python -B):
  init     PROJECT [--shape stay|itinerary]     skeleton: brief.json, spec.json, captures/, RESEARCH.md; an itinerary
                                                reads trip_intake.json beside it for the dates and the route
  capture  PROJECT ROW URL VALUE [--raw-file F] [--http-status N]   store one capture; ROW must be a row the spec creates
  exclude  PROJECT ROW INDEX REASON             a capture is wrong for this claim: kept on record, never used
  status   PROJECT                              spec problems, missing or stale captures, and what to capture next
  review   PROJECT                              derive review.json from fresh stored captures (offline)
  sample   PROJECT                              write spot.plan.json (the rows a second reviewer must re-check)
  spot     PROJECT                              write spot.json for the planned rows; each needs a capture taken after the review
  gate     PROJECT                              fact-guard gate; prints every failure; exit 1 if any
  build    PROJECT [--out FILE]                 validate the spec, gate, then write the page (default report.html)
  check    PROJECT [--report FILE]              nothing built is stale; the KB covers every item with a dated, sourced entry
  selftest                                      offline end-to-end run on the bundled example, plus refusals that must hold

Fail-closed throughout: an unknown row, a missing review, a stale hash, an empty spot check, or a KB gap stops the run.
Stdlib only, plus the plugin's own fact-guard and report-builder scripts.
"""
import argparse, copy, datetime as dt, json, re, shutil, sys, tempfile, time
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2]          # <plugin root>/skills
sys.path[:0] = [str(SKILLS / 'fact-guard' / 'scripts'),
                str(SKILLS / 'report-builder' / 'template' / 'generic')]
import guard                    # noqa: E402  (fact-guard: policy, ledger, gate, sample)
import capture_store as cs      # noqa: E402  (fact-guard: stored captures, freshness, derived review)
import build_page as bp         # noqa: E402  (report-builder: spec validation and the page embedding)

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples'
SPOT_RATE, SPOT_MIN = guard.SPOT_RATE, guard.SPOT_MIN
REQUIRED_BRIEF = ('question', 'audience', 'criteria')

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


class Project:
    def __init__(self, root):
        self.root = Path(root)
        self.spec_path = self.root / 'spec.json'
        self.store = self.root / 'captures'
        self.review_path = self.root / 'review.json'
        self.spot_plan_path = self.root / 'spot.plan.json'
        self.spot_path = self.root / 'spot.json'
        self.kb_path = self.root / 'RESEARCH.md'
        self.brief_path = self.root / 'brief.json'
        self.report_path = self.root / 'report.html'

    def spec_bytes(self):
        if not self.spec_path.exists():
            raise SystemExit(f"{self.spec_path} does not exist: start the project with `pipeline.py init PROJECT --shape stay|itinerary` (or build the spec) first")
        return self.spec_path.read_bytes()

    def spec(self):
        return self.json_of(self.spec_path, self.spec_bytes())

    @staticmethod
    def json_of(path, data):
        try:
            return json.loads(data)
        except ValueError as e:
            raise SystemExit(f"{path} is not valid JSON ({e}): fix or regenerate it")

    def read_json(self, path):
        if not Path(path).exists():
            raise SystemExit(f"{path} does not exist: run the step that writes it first")
        return self.json_of(path, Path(path).read_text(encoding='utf-8'))


def ledger_rows(p):
    return guard.ledger(p.spec())


def problems_of_spec(spec):
    return bp.validate_spec(spec)


# ----------------------------------------------------------------- commands

def itinerary_blocks(root):
    """A starting itinerary: the hero, one day per date of the trip (from trip_intake.json when it is there), and the
    method. Every day starts with one stop saying so; the agent replaces them as research lands."""
    intake = Path(root) / 'trip_intake.json'
    info = json.loads(intake.read_text(encoding='utf-8')) if intake.exists() else {}
    dates = info.get('dates') or {}
    route = (info.get('destination') or {}).get('route') or []
    days, trip = [], None
    try:
        d0, d1 = dt.date.fromisoformat(dates['start']), dt.date.fromisoformat(dates['end'])
        trip = {'start': dates['start'], 'end': dates['end']}
        while d0 <= d1 and len(days) < 60:
            days.append({'id': f'day-{len(days) + 1}', 'date': d0.isoformat(),
                         'label': f"{d0.strftime('%a')} {d0.day} {d0.strftime('%b')}",
                         'city': route[0] if route else '', 'stops': [{'name': 'Nothing planned yet', 'type': 'free'}]})
            d0 += dt.timedelta(days=1)
    except (KeyError, TypeError, ValueError):
        pass
    if not days:
        days = [{'id': 'day-1', 'label': 'Day 1', 'city': '', 'stops': [{'name': 'Nothing planned yet', 'type': 'free'}]}]
    blocks = [{"type": "hero", "eyebrow": "", "title": "", "lead": "", "kpis": []},
              {"type": "day_plan", "id": "plan", "title": "Day by day", "days": days},
              {"type": "method", "id": "method", "title": "How this was checked", "items": []}]
    return blocks, trip


def cmd_init(a):
    root = Path(a.project)
    root.mkdir(parents=True, exist_ok=True)
    p = Project(root)
    if p.spec_path.exists() or p.brief_path.exists():
        print(f'refused: {root} already has brief.json or spec.json. Choose an empty folder.')
        return 1
    p.brief_path.write_text(json.dumps({
        "question": "", "audience": "", "decision": "",
        "criteria": [{"name": "", "why": "", "how_checked": ""}],
        "out_of_scope": [], "assumptions": []}, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    spec = {"policy": {},   # the defaults apply; a spec's own policy can only tighten them
            "blocks": [{"type": "hero", "eyebrow": "", "title": "", "lead": "", "kpis": []},
                       {"type": "method", "id": "method", "title": "How this was checked", "items": []}],
            "footer": ""}
    if getattr(a, 'shape', 'stay') == 'itinerary':
        spec['blocks'], trip = itinerary_blocks(root)
        spec['as_of'] = utc_now().strftime('%Y-%m-%d')
        spec['locale'] = 'en'
        if trip:
            spec['trip'] = trip
    intake_path = root / 'trip_intake.json'
    if intake_path.exists():   # the traits, the fit profile and the trip dates, exactly as the group agreed them
        sys.path.insert(0, str(SKILLS / 'trip-intake' / 'scripts'))
        import trip_intake
        spec = trip_intake.sync(json.loads(intake_path.read_text(encoding='utf-8')), spec)
    p.spec_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    p.store.mkdir(exist_ok=True)
    if not p.kb_path.exists():   # trip-constraints-intake may already have written the fixed constraints at its top
        p.kb_path.write_text('# Knowledge base\n\n<!-- One section per item: "## <item name>", then Sources (URL + read date),\n'
                             'Claim, Group or audience fit, Verdict, Not verified. -->\n', encoding='utf-8')
    print(f'created {root}: brief.json (fill in), spec.json, captures/, RESEARCH.md')
    print('next: write the criteria into brief.json, add the blocks to spec.json, then run: pipeline.py status')
    return 0


def cmd_capture(a):
    p = Project(a.project)
    kinds = {r['row']: r['kind'] for r in ledger_rows(p)}
    rows = set(kinds)
    if a.row not in rows:
        near = sorted(r for r in rows if a.row.split('|')[0] in r)[:5]
        print(f'refused: {a.row!r} is not a row the spec creates. Rows like it: {near or "none"}')
        return 1
    raw = Path(a.raw_file).read_text(encoding='utf-8') if a.raw_file else None
    try:
        cs.put(str(p.store), a.row, a.url, a.value, raw=raw, http_status=a.http_status, kind=kinds[a.row])
    except SystemExit as e:
        print(f'refused: {e}')
        return 1
    print(f'stored capture for {a.row}')
    return 0


def cmd_exclude(a):
    p = Project(a.project)
    cs.exclude(str(p.store), a.row, a.index, a.reason)
    print(f'excluded capture {a.index} of {a.row}: {a.reason}')
    return 0


def cmd_status(a):
    p = Project(a.project)
    spec = p.spec()
    problems = problems_of_spec(spec)
    for pr in problems:
        print('SPEC', pr)
    review, missing = cs.derive_review(spec, str(p.store), spec_bytes=p.spec_bytes(), base_dir=p.root)
    for m in missing:
        print('MISSING', m)
    print(f'{len(ledger_rows(p))} rows; {len(review["rows"])} covered by fresh captures; {len(missing)} need capture')
    if not p.brief_path.exists() or any(not p.read_json(p.brief_path).get(k) for k in REQUIRED_BRIEF):
        print('BRIEF incomplete: brief.json needs question, audience and criteria')
    return 0 if not problems and not missing else 1


def cmd_review(a):
    p = Project(a.project)
    review, missing = cs.derive_review(p.spec(), str(p.store), spec_bytes=p.spec_bytes(), base_dir=p.root)
    p.review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding='utf-8')
    cs.mark_review(str(p.store), p.spec_bytes())   # the review time lives in the store: the second look must come after it
    print(f'review written: {len(review["rows"])} rows confirmed from the store, {len(missing)} need capture')
    for m in missing:
        print('MISSING', m)
    return 0 if not missing else 1


def cmd_sample(a):
    p = Project(a.project)
    rows = ledger_rows(p)
    plan = guard.sample(rows, SPOT_RATE, SPOT_MIN, guard.spot_seed(rows))
    p.spot_plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    by_row = {r['row']: r for r in rows}
    task = [{'row': k, 'what': f"{by_row[k]['item']}: {by_row[k]['field']}", 'kind': by_row[k]['kind'], 'source': by_row[k].get('src')} for k in plan['rows'] if k in by_row]
    (p.root / 'spot.task.json').write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding='utf-8')   # for the fresh agent: no spec values in it
    print(f'spot plan: {len(plan["rows"])} rows (seed {plan["seed"]}, fixed by the ledger); a second reviewer re-checks each with a new capture')
    return 0


def cmd_spot(a):
    p = Project(a.project)
    if not p.spot_plan_path.exists() or not p.review_path.exists():
        print('refused: run sample and review first')
        return 1
    plan = p.read_json(p.spot_plan_path)
    try:
        mk = json.loads((p.store / '_review.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        mk = {}
    if mk.get('spec_sha256') != guard.sha(p.spec_bytes()):
        print('refused: the store has no review of this spec; run review first')
        return 1
    reviewed_at = mk.get('derived_at', '')
    t0 = guard.parse_time(reviewed_at)
    by_row = {r['row']: r for r in ledger_rows(p)}
    late = guard.second_look_missing(str(p.store), {r: by_row[r] for r in plan['rows'] if r in by_row}, t0, guard.policy(p.spec()), base_dir=p.root)
    late += [r for r in plan['rows'] if r not in by_row]
    if late:
        print(f'refused: {len(late)} sampled row(s) have no extra capture taken after the review ({reviewed_at}). '
              f'A spot check must be a fresh, independent look: {late[:3]}...')
        return 1
    spot = {"rows": {r: {"checked_after_review": reviewed_at} for r in plan['rows']},
            "plan_seed": plan['seed'], "recorded_at": utc_now().strftime('%Y-%m-%dT%H:%M:%SZ'),
            "agreement": "recomputed by the gate from the store; this file records only which rows were sampled"}
    p.spot_path.write_text(json.dumps(spot, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'spot.json written for {len(plan["rows"])} rows; the gate recomputes agreement from the store')
    return 0


def run_gate(p, quiet=False):
    spec_bytes = p.spec_bytes()
    spec = json.loads(spec_bytes)
    problems = problems_of_spec(spec)
    if not p.review_path.exists():
        return ['no review.json: run pipeline.py review (it is derived from stored captures)'], []
    review = p.read_json(p.review_path)
    spot = p.read_json(p.spot_path) if p.spot_path.exists() else None
    if spot is None:
        problems.append('no spot.json: run sample, then a fresh second look, then spot')
    rail_path = p.root / 'rail.json'   # hotel-dossier's rail_distance.py writes it; a 'Nearest rail' fact is checked against it
    rail = p.read_json(rail_path) if rail_path.exists() else None
    fails, warns, rows = guard.gate(spec, review, spot, spec_bytes, rail, None, spot_store=str(p.store), base_dir=p.root)
    if not quiet:
        for w in warns: print('WARN', w)
    return problems + fails, rows


def cmd_gate(a):
    p = Project(a.project)
    fails, rows = run_gate(p)
    for f in fails: print('FAIL', f)
    if fails:
        print(f'GATE FAILED ({len(fails)} problem(s))')
        return 1
    print(f'GATE PASS ({len(rows)} claims)')
    return 0


def cmd_build(a):
    p = Project(a.project)
    fails, rows = run_gate(p)
    if fails:
        for f in fails: print('FAIL', f)
        print('build refused: the gate did not pass')
        return 1
    problems = problems_of_spec(p.spec())
    if problems:
        for pr in problems: print('SPEC', pr)
        print('build refused: the spec has problems (see above)')
        return 1
    out = Path(a.out) if a.out else p.report_path
    try:
        html = bp.render_page(p.spec(), out, p.root)
    except bp.BuildError as e:
        print('build refused:', e)
        return 1
    out.write_text(html, encoding='utf-8')
    print(f'GATE PASS ({len(rows)} claims); wrote {out} ({round(out.stat().st_size / 1024)} KB)')
    return 0


def cmd_check(a):
    p = Project(a.project)
    spec_bytes = p.spec_bytes()
    spec = json.loads(spec_bytes)
    issues = []
    if not p.review_path.exists():
        issues.append('review.json is missing')
    elif p.read_json(p.review_path).get('spec_sha256') != guard.sha(spec_bytes):
        issues.append('review.json is STALE: it was made for another spec. Run review again.')
    report = Path(a.report) if getattr(a, 'report', None) else p.report_path
    if not report.exists():
        issues.append(f'{report.name} is missing: nothing has been built from this spec (build, or name the file with --report)')
    elif f'name="spec-fingerprint" content="{bp.spec_fingerprint(spec)}"' not in report.read_text(encoding='utf-8'):
        issues.append(f'{report.name} is STALE: it embeds a different spec. Run build again.')
    review, missing = cs.derive_review(spec, str(p.store), spec_bytes=spec_bytes, base_dir=p.root)
    issues += [f'capture: {m}' for m in missing]
    kb = p.kb_path.read_text(encoding='utf-8') if p.kb_path.exists() else ''
    issues += [f'KB: {m}' for m in guard.kb_fails(spec, kb)]
    for pr in problems_of_spec(spec):
        issues.append(f'spec: {pr}')
    if not p.spot_path.exists():
        issues.append('spot.json is missing: the report has not had a second look')
    for i in issues: print('ISSUE', i)
    print('CHECK PASS' if not issues else f'CHECK FAILED ({len(issues)} issue(s))')
    return 0 if not issues else 1


# ----------------------------------------------------------------- selftest

def selftest(_a):
    """Offline, end to end, on the bundled synthetic example. Every refusal below must hold; a green path that
    only ever passes proves nothing."""
    tmp = Path(tempfile.mkdtemp(prefix='fact-guard-selftest-'))
    results = []

    def run(name, fn, want):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = fn()
        ok = (rc == 0) == want
        results.append(ok)
        print(f"  {'ok  ' if ok else 'FAIL'} {name}: exit {rc} (expected {'0' if want else 'non-zero'})")
        if not ok:
            print(buf.getvalue())

    def ns(**kw):
        return argparse.Namespace(**kw)

    proj = tmp / 'example'
    shutil.copytree(EXAMPLE, proj)
    p = Project(proj)
    p.store.mkdir(exist_ok=True)
    seed = json.loads((EXAMPLE / 'captures.example.json').read_text(encoding='utf-8'))
    now = utc_now().strftime('%Y-%m-%dT%H:%M:%SZ')
    for rf in (proj / 'reviews').glob('*.json'):   # review files age out; the example must not depend on today's date
        j = json.loads(rf.read_text(encoding='utf-8'))
        j['retrieved_at'] = now
        rf.write_text(json.dumps(j, indent=2, ensure_ascii=False), encoding='utf-8')
    loads = {}
    for c in seed:   # two captures of one row are two loads, a minute apart
        k = loads[c['row']] = loads.get(c['row'], -1) + 1
        cs.put(str(p.store), c['row'], c['url'], c['value'], raw=(c.get('raw') or '') + (f' (load {k + 1})' if k else ''), http_status=c.get('http_status'),
               when=(utc_now() - dt.timedelta(minutes=k)).strftime('%Y-%m-%dT%H:%M:%SZ'))

    run('status on the example is clean', lambda: cmd_status(ns(project=str(proj))), True)
    run('review derives from the store', lambda: cmd_review(ns(project=str(proj))), True)
    run('sample planned', lambda: cmd_sample(ns(project=str(proj))), True)
    run('spot refused before a fresh second look', lambda: cmd_spot(ns(project=str(proj))), False)
    time.sleep(1.1)   # the review stamps whole seconds; a second look must come strictly after it
    for row in json.loads(p.spot_plan_path.read_text(encoding='utf-8'))['rows']:
        page_url, value = _capture_for(row, seed)
        cs.put(str(p.store), row, page_url, value, raw=f'second look: {value}', http_status=200, when=utc_now().strftime('%Y-%m-%dT%H:%M:%SZ'))
    run('spot accepted after fresh captures', lambda: cmd_spot(ns(project=str(proj))), True)
    run('gate passes on the example', lambda: cmd_gate(ns(project=str(proj))), True)
    run('build writes the page', lambda: cmd_build(ns(project=str(proj), out=None)), True)
    run('check passes after build', lambda: cmd_check(ns(project=str(proj))), True)

    # The audit is independent of the build: it must pass the built page and catch tampering with it.
    sys.path.insert(0, str(SKILLS / 'report-builder' / 'scripts'))
    import audit_report
    report = proj / 'report.html'
    results.append(not audit_report.check(str(proj), str(report)))
    print(f"  {'ok  ' if results[-1] else 'FAIL'} audit passes the built page")
    tampered = tmp / 'tampered.html'
    tampered.write_text(report.read_text(encoding='utf-8').replace('<body>', '<body><img src="https://evil.example.test/x.jpg">', 1), encoding='utf-8')
    hot = any('hot-linked' in f for f in audit_report.check(str(proj), str(tampered)))
    results.append(hot); print(f"  {'ok  ' if hot else 'FAIL'} audit refuses a hot-linked photo")
    edited = tmp / 'edited.html'
    edited.write_text(report.read_text(encoding='utf-8').replace('Synthetic example', 'Edited by hand', 1), encoding='utf-8')
    caught = any('embedded data was edited' in f for f in audit_report.check(str(proj), str(edited)))
    results.append(caught); print(f"  {'ok  ' if caught else 'FAIL'} audit refuses a page whose embedded data was edited")
    for nm in ('markup', 'script'):
        shutil.copytree(proj / 'report_assets', tmp / f'{nm}_assets')   # the manifest sits beside the page it belongs to
    markup = tmp / 'markup.html'
    markup.write_text(report.read_text(encoding='utf-8').replace('<main class="wrap"', '<p>Rated 4.9 by 2,000 guests</p><main class="wrap"', 1), encoding='utf-8')
    caught_m = any('not what this engine builds' in f for f in audit_report.check(str(proj), str(markup)))
    results.append(caught_m); print(f"  {'ok  ' if caught_m else 'FAIL'} audit refuses markup added to the built page")
    script = tmp / 'script.html'
    script.write_text(report.read_text(encoding='utf-8').replace("const safeUrl = u => {", "const safeUrl = u => { return String(u);", 1), encoding='utf-8')
    caught_s = any('not what this engine builds' in f for f in audit_report.check(str(proj), str(script)))
    results.append(caught_s); print(f"  {'ok  ' if caught_s else 'FAIL'} audit refuses an edited engine script")

    # Negative cases, each on a fresh copy so one failure cannot mask another.
    def fresh_copy(name):
        d = tmp / name
        shutil.copytree(proj, d)
        return d

    d = fresh_copy('price-changed')
    spec = json.loads((d / 'spec.json').read_text(encoding='utf-8'))
    for b in spec['blocks']:
        if b.get('type') == 'cards':
            b['items'][0]['price'] = 999
            b['items'][0]['priceNote'] = ''
    (d / 'spec.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    run('gate refuses a price the store does not support (review is also stale)', lambda: cmd_gate(ns(project=str(d))), False)

    d = fresh_copy('bad-host')
    spec = json.loads((d / 'spec.json').read_text(encoding='utf-8'))
    for b in spec['blocks']:
        if b.get('type') == 'cards':
            b['items'][1]['url'] = 'javascript:alert(1)'
    (d / 'spec.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    run('gate refuses a javascript: link', lambda: cmd_gate(ns(project=str(d))), False)

    d = fresh_copy('banned-word')
    spec = json.loads((d / 'spec.json').read_text(encoding='utf-8'))
    for b in spec['blocks']:
        if b.get('type') == 'cards':
            b['items'][0]['verdict'] = 'The cheapest option in the set.'
    (d / 'spec.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    run('gate refuses a banned claim word', lambda: cmd_gate(ns(project=str(d))), False)

    def price_row(dd):
        sp = json.loads((dd / 'spec.json').read_text(encoding='utf-8'))
        return next(r['row'] for r in guard.ledger(sp) if r['kind'] == 'price')

    d = fresh_copy('typed-review')   # a review.json typed by hand cannot stand in for captures that are not in the store
    row = price_row(d)
    cs.path_for(str(Path(d) / 'captures'), row).unlink()
    rv = json.loads((d / 'review.json').read_text(encoding='utf-8'))
    rv['rows'][row] = {'verdict': 'CONFIRMED', 'captures': [{'value': 'x', 'url': 'https://stays.example-hotels.test/aster', 'at': '2030-01-01T00:00:00Z'}] * 2}
    (d / 'review.json').write_text(json.dumps(rv), encoding='utf-8')
    run('gate ignores a typed review.json: a row with no stored captures still fails', lambda: cmd_gate(ns(project=str(d))), False)

    d = fresh_copy('one-load')   # two captures that are one load of the page are not two looks
    row = price_row(d)
    sf = cs.path_for(str(Path(d) / 'captures'), row)
    doc = json.loads(sf.read_text(encoding='utf-8'))
    doc['captures'] = [dict(doc['captures'][0]), dict(doc['captures'][0])]
    sf.write_text(json.dumps(doc), encoding='utf-8')
    run('gate refuses two captures that are one load of the page', lambda: cmd_gate(ns(project=str(d))), False)

    d = fresh_copy('retest-reason')   # a typed retest reason does not clear a spot-check disagreement
    spot = json.loads((d / 'spot.json').read_text(encoding='utf-8'))
    srow = next(iter(spot['rows']))
    sf = cs.path_for(str(Path(d) / 'captures'), srow)
    doc = json.loads(sf.read_text(encoding='utf-8'))
    doc['captures'][-1]['value'] = '999999'
    sf.write_text(json.dumps(doc), encoding='utf-8')
    rv = json.loads((d / 'review.json').read_text(encoding='utf-8'))
    rv.update({'scope': 'full', 'retest_reason': 'looked again, it was fine'})
    (d / 'review.json').write_text(json.dumps(rv), encoding='utf-8')
    run('gate refuses a spot-check disagreement even with a typed retest reason', lambda: cmd_gate(ns(project=str(d))), False)

    d = fresh_copy('backdated-review')   # backdating the review time does not make a missing second look appear
    for srow in json.loads((d / 'spot.json').read_text(encoding='utf-8'))['rows']:
        sf = cs.path_for(str(Path(d) / 'captures'), srow)
        doc = json.loads(sf.read_text(encoding='utf-8'))
        need = 2 if srow.endswith(('|price', '|score', '|count')) else 1
        doc['captures'] = doc['captures'][:need]
        sf.write_text(json.dumps(doc), encoding='utf-8')
    mk = json.loads((d / 'captures' / '_review.json').read_text(encoding='utf-8'))
    mk['derived_at'] = '2000-01-01T00:00:00Z'
    (d / 'captures' / '_review.json').write_text(json.dumps(mk), encoding='utf-8')
    run('gate refuses a spot check with no second look even when the review time is backdated', lambda: cmd_gate(ns(project=str(d))), False)

    d = fresh_copy('unreadable-review-time')   # an unparseable review time must not switch the second-look check off
    for srow in json.loads((d / 'spot.json').read_text(encoding='utf-8'))['rows']:
        sf = cs.path_for(str(Path(d) / 'captures'), srow)
        doc = json.loads(sf.read_text(encoding='utf-8'))
        need = 2 if srow.endswith(('|price', '|score', '|count')) else 1
        doc['captures'] = doc['captures'][:need]
        sf.write_text(json.dumps(doc), encoding='utf-8')
    mk = json.loads((d / 'captures' / '_review.json').read_text(encoding='utf-8'))
    mk['derived_at'] = 'not a time'
    (d / 'captures' / '_review.json').write_text(json.dumps(mk), encoding='utf-8')
    run('gate refuses a spot check when the recorded review time is unreadable', lambda: cmd_gate(ns(project=str(d))), False)

    d = fresh_copy('future-second-look')   # a second look dated in the future, disagreeing with the spec, is not a second look
    for srow in json.loads((d / 'spot.json').read_text(encoding='utf-8'))['rows']:
        sf = cs.path_for(str(Path(d) / 'captures'), srow)
        doc = json.loads(sf.read_text(encoding='utf-8'))
        need = 2 if srow.endswith(('|price', '|score', '|count')) else 1
        late = dict(doc['captures'][-1], captured_at='2099-01-01T00:00:00Z', value='1')
        doc['captures'] = doc['captures'][:need] + [late]
        sf.write_text(json.dumps(doc), encoding='utf-8')
    run('gate refuses second looks dated in the future', lambda: cmd_gate(ns(project=str(d))), False)

    d = fresh_copy('ahead-second-look')   # a capture stamped hours ahead (inside the day of skew freshness allows) is not a second look either
    ahead = (utc_now() + dt.timedelta(hours=20)).strftime('%Y-%m-%dT%H:%M:%SZ')
    for srow in json.loads((d / 'spot.json').read_text(encoding='utf-8'))['rows']:
        sf = cs.path_for(str(Path(d) / 'captures'), srow)
        doc = json.loads(sf.read_text(encoding='utf-8'))
        need = 2 if srow.endswith(('|price', '|score', '|count')) else 1
        doc['captures'] = doc['captures'][:need] + [dict(doc['captures'][-1], captured_at=ahead)]
        sf.write_text(json.dumps(doc), encoding='utf-8')
    run('gate refuses second looks stamped hours ahead of the clock', lambda: cmd_gate(ns(project=str(d))), False)

    d = fresh_copy('blog-second-look')   # a second look from a host the kind may not cite is not a second look either
    for srow in json.loads((d / 'spot.json').read_text(encoding='utf-8'))['rows']:
        sf = cs.path_for(str(Path(d) / 'captures'), srow)
        doc = json.loads(sf.read_text(encoding='utf-8'))
        need = 2 if srow.endswith(('|price', '|score', '|count')) else 1
        late = dict(doc['captures'][-1], url='https://random-blog.example/post')
        doc['captures'] = doc['captures'][:need] + [late]
        sf.write_text(json.dumps(doc), encoding='utf-8')
    run('gate refuses second looks from a source the kind may not cite', lambda: cmd_gate(ns(project=str(d))), False)

    d = fresh_copy('raw-tamper')   # a stored raw text that no longer matches its hash is not evidence
    row = price_row(d)
    sf = cs.path_for(str(Path(d) / 'captures'), row)
    doc = json.loads(sf.read_text(encoding='utf-8'))
    for c in doc['captures']:
        c['raw_text'] = 'typed afterwards'
    sf.write_text(json.dumps(doc), encoding='utf-8')
    run('gate refuses captures whose raw text does not match its hash', lambda: cmd_gate(ns(project=str(d))), False)

    d = fresh_copy('one-capture')
    store = d / 'captures'
    target = next(r['row'] for r in seed if r['row'].endswith('|price'))
    f = cs.path_for(str(store), target)
    data = json.loads(f.read_text(encoding='utf-8'))
    data['captures'] = data['captures'][:1]
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    run('review refuses a price with one capture (two are needed)', lambda: cmd_review(ns(project=str(d))), False)

    d = fresh_copy('kb-gap')
    (d / 'RESEARCH.md').write_text('# Knowledge base\n', encoding='utf-8')
    run('check refuses a KB with no entries', lambda: cmd_check(ns(project=str(d))), False)

    d = fresh_copy('no-report')
    (d / 'report.html').unlink()
    run('check refuses a project with nothing built', lambda: cmd_check(ns(project=str(d), report=None)), False)

    d = fresh_copy('stale-report')
    spec = json.loads((d / 'spec.json').read_text(encoding='utf-8'))
    spec['footer'] = spec.get('footer', '') + ' (edited after build)'
    (d / 'spec.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    run('check flags a report built from an older spec', lambda: cmd_check(ns(project=str(d))), False)

    d = fresh_copy('no-design')
    spec = json.loads((d / 'spec.json').read_text(encoding='utf-8'))
    spec.pop('design', None)
    (d / 'spec.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    run('build refuses a spec with no design', lambda: cmd_build(ns(project=str(d), out=None)), False)

    d = fresh_copy('low-contrast')
    spec = json.loads((d / 'spec.json').read_text(encoding='utf-8'))
    spec['design']['palette']['mute'] = '#e6e6e6'
    (d / 'spec.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    run('build refuses a palette below contrast', lambda: cmd_build(ns(project=str(d), out=None)), False)

    d = fresh_copy('text-in-number-column')
    spec = json.loads((d / 'spec.json').read_text(encoding='utf-8'))
    for b in spec['blocks']:
        if b['type'] == 'table':
            b['columns'][-1]['type'] = 'num'
            b['columns'] = b['columns']
            for it in b['items']:
                it['price'] = 'call us'
            b['columns'] = [dict(c, key='price', type='num') if c['key'] == 'price' else c for c in b['columns']]
    (d / 'spec.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    run('build refuses a numeric column that holds text', lambda: cmd_build(ns(project=str(d), out=None)), False)

    d = fresh_copy('amounts-typed-text')
    spec = json.loads((d / 'spec.json').read_text(encoding='utf-8'))
    for b in spec['blocks']:
        if b['type'] == 'table':
            for c in b['columns']:
                if c['key'] == 'price':
                    c['type'] = 'text'
    (d / 'spec.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    run('build refuses amounts typed as text (they would sort wrongly)', lambda: cmd_build(ns(project=str(d), out=None)), False)

    # The itinerary example: a mixed page (route, day plan, bookings, fares, decisions, notes and two stay cards)
    # goes through the same pipeline, and the plan, booking and critical-fact checks refuse what they must.
    import io, contextlib

    def says(name, fn, text):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn()
        ok_ = text in buf.getvalue()
        results.append(ok_)
        print(f"  {'ok  ' if ok_ else 'FAIL'} {name}")
        if not ok_:
            print(buf.getvalue())

    itin = tmp / 'itinerary'
    shutil.copytree(EXAMPLE / 'itinerary', itin)
    iseed = json.loads((itin / 'captures.example.json').read_text(encoding='utf-8'))
    for rf in (itin / 'reviews').glob('*.json'):
        j = json.loads(rf.read_text(encoding='utf-8'))
        j['retrieved_at'] = now
        rf.write_text(json.dumps(j, indent=2, ensure_ascii=False), encoding='utf-8')
    (itin / 'captures').mkdir(exist_ok=True)
    loads = {}
    for c in iseed:
        k = loads[c['row']] = loads.get(c['row'], -1) + 1
        cs.put(str(itin / 'captures'), c['row'], c['url'], c['value'], raw=(c.get('raw') or '') + (f' (load {k + 1})' if k else ''), http_status=c.get('http_status'),
               when=(utc_now() - dt.timedelta(minutes=k)).strftime('%Y-%m-%dT%H:%M:%SZ'))
    run('itinerary: status is clean', lambda: cmd_status(ns(project=str(itin))), True)
    run('itinerary: review derives from the store', lambda: cmd_review(ns(project=str(itin))), True)
    run('itinerary: sample planned', lambda: cmd_sample(ns(project=str(itin))), True)
    time.sleep(1.1)
    for row in json.loads((itin / 'spot.plan.json').read_text(encoding='utf-8'))['rows']:
        page_url, value = _capture_for(row, iseed)
        cs.put(str(itin / 'captures'), row, page_url, value, raw=f'second look: {value}', http_status=200, when=utc_now().strftime('%Y-%m-%dT%H:%M:%SZ'))
    run('itinerary: spot accepted', lambda: cmd_spot(ns(project=str(itin))), True)
    run('itinerary: gate passes', lambda: cmd_gate(ns(project=str(itin))), True)
    run('itinerary: build writes the mixed page', lambda: cmd_build(ns(project=str(itin), out=None)), True)
    run('itinerary: check passes after build', lambda: cmd_check(ns(project=str(itin))), True)
    page = (itin / 'report.html').read_text(encoding='utf-8')
    rendered = all(k in page for k in ('decisionLogBlock', 'costComparisonBlock', 'Pier Grill', '"locale": "en-GB"'))
    results.append(rendered)
    print(f"  {'ok  ' if rendered else 'FAIL'} itinerary: the page carries the new blocks, the rejected option and the locale")

    def itin_copy(name, edit):
        d = tmp / name
        shutil.copytree(itin, d)
        sp_ = json.loads((d / 'spec.json').read_text(encoding='utf-8'))
        edit(sp_)
        (d / 'spec.json').write_text(json.dumps(sp_, ensure_ascii=False, indent=2), encoding='utf-8')
        return d

    def day_plan(sp_):
        return next(b for b in sp_['blocks'] if b['type'] == 'day_plan')

    d = itin_copy('itin-weekday', lambda sp_: day_plan(sp_)['days'][1].update(label='Fri 7 May'))
    says('itinerary: a day label with the wrong weekday fails the gate', lambda: cmd_gate(ns(project=str(d))), 'is a Tue')
    d = itin_copy('itin-order', lambda sp_: day_plan(sp_)['days'][1]['stops'][2].update(time='10:00'))
    says('itinerary: a stop sooner than the train allows fails the gate', lambda: cmd_gate(ns(project=str(d))), 'leaves less than')
    d = itin_copy('itin-sum', lambda sp_: next(b for b in sp_['blocks'] if b['type'] == 'booking_ledger')['summary'].update(EUR=999))
    says('itinerary: a booking total that does not add up fails the gate', lambda: cmd_gate(ns(project=str(d))), 'does not match the sum')
    d = itin_copy('itin-train', lambda sp_: day_plan(sp_)['days'][1]['stops'][1].update(time='10:10'))
    run('itinerary: a train time the captures do not support fails the gate', lambda: cmd_gate(ns(project=str(d))), False)
    d = itin_copy('itin-no-reason', lambda sp_: day_plan(sp_)['days'][1]['stops'][4].pop('note'))
    says('itinerary: a rejected option with no reason refuses the build', lambda: cmd_build(ns(project=str(d), out=None)), 'needs a note saying why')
    d = tmp / 'itin-one-source'
    shutil.copytree(itin, d)
    target = next(r['row'] for r in iseed if r['row'].endswith('Selmere Grand|amount'))
    f = cs.path_for(str(d / 'captures'), target)
    data = json.loads(f.read_text(encoding='utf-8'))
    data['captures'] = [c for c in data['captures'] if not str(c['url']).startswith('evidence/')]
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    run('itinerary: the review is still derived from one-source captures', lambda: cmd_review(ns(project=str(d))), True)
    says('itinerary: a booking amount with one source fails the gate', lambda: cmd_gate(ns(project=str(d))), 'independent')
    d = tmp / 'itin-new'
    d.mkdir()
    (d / 'trip_intake.json').write_text(json.dumps({'dates': {'start': '2030-05-06', 'end': '2030-05-08'},
                                                    'destination': {'route': ['Valmora', 'Selmere']}}), encoding='utf-8')
    run('itinerary: init --shape itinerary writes one day per date of the trip', lambda: cmd_init(ns(project=str(d), shape='itinerary')), True)
    made = json.loads((d / 'spec.json').read_text(encoding='utf-8'))
    shaped = made.get('trip') == {'start': '2030-05-06', 'end': '2030-05-08'} and len(day_plan(made)['days']) == 3 \
        and day_plan(made)['days'][0]['label'] == 'Mon 6 May'
    results.append(shaped)
    print(f"  {'ok  ' if shaped else 'FAIL'} itinerary: the skeleton carries the trip dates and their weekdays")

    # A field of the wrong type anywhere in a spec is a named refusal, never a traceback (from the validator or the gate).
    crashes = set()
    for base in (EXAMPLE / 'spec.json', EXAMPLE / 'itinerary' / 'spec.json'):
        orig = json.loads(base.read_text(encoding='utf-8'))
        stack = [()]
        while stack:
            path = stack.pop()
            node = orig
            for k in path:
                node = node[k]
            if isinstance(node, dict):
                stack += [path + (k,) for k in node]
            elif isinstance(node, list):
                stack += [path + (i,) for i in range(len(node))]
            if not path or path[0] == 'design':
                continue
            for wrong in ([], {}, 5, None):
                mut = copy.deepcopy(orig)
                cur = mut
                for k in path[:-1]:
                    cur = cur[k]
                cur[path[-1]] = copy.deepcopy(wrong)
                try:
                    if not bp.validate_spec(mut):
                        raw = json.dumps(mut).encode()
                        with tempfile.TemporaryDirectory() as td:
                            guard.gate(mut, {'spec_sha256': guard.sha(raw), 'rows': {}}, None, raw, None, None, spot_store=td, base_dir=Path(td))
                except Exception as e:   # noqa: BLE001 - the point is that nothing escapes
                    crashes.add((base.name, '/'.join(map(str, path)), type(e).__name__))
    results.append(not crashes)
    print(f"  {'ok  ' if not crashes else 'FAIL'} a wrong type anywhere in the example specs is refused by name, never a traceback{'' if not crashes else ': ' + str(sorted(crashes)[:3])}")

    ok = all(results)
    print('SELFTEST PASS' if ok else f'SELFTEST FAIL ({results.count(False)} case(s))')
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok else 1


def _capture_for(row, seed):
    for c in seed:
        if c['row'] == row:
            return c['url'], c['value']
    raise KeyError(row)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    s = sp.add_parser('init'); s.add_argument('project'); s.add_argument('--shape', choices=['stay', 'itinerary'], default='stay'); s.set_defaults(fn=cmd_init)
    s = sp.add_parser('capture'); s.add_argument('project'); s.add_argument('row'); s.add_argument('url'); s.add_argument('value')
    s.add_argument('--raw-file'); s.add_argument('--http-status', type=int); s.set_defaults(fn=cmd_capture)
    s = sp.add_parser('exclude'); s.add_argument('project'); s.add_argument('row'); s.add_argument('index', type=int); s.add_argument('reason'); s.set_defaults(fn=cmd_exclude)
    s = sp.add_parser('status'); s.add_argument('project'); s.set_defaults(fn=cmd_status)
    s = sp.add_parser('review'); s.add_argument('project'); s.set_defaults(fn=cmd_review)
    s = sp.add_parser('sample'); s.add_argument('project'); s.set_defaults(fn=cmd_sample)
    s = sp.add_parser('spot'); s.add_argument('project'); s.set_defaults(fn=cmd_spot)
    s = sp.add_parser('gate'); s.add_argument('project'); s.set_defaults(fn=cmd_gate)
    s = sp.add_parser('build'); s.add_argument('project'); s.add_argument('--out'); s.set_defaults(fn=cmd_build)
    s = sp.add_parser('check'); s.add_argument('project'); s.add_argument('--report'); s.set_defaults(fn=cmd_check)
    s = sp.add_parser('selftest'); s.set_defaults(fn=selftest)
    a = ap.parse_args()
    try:
        return a.fn(a)
    except (ValueError, TypeError, AttributeError, KeyError, IndexError) as e:   # json.JSONDecodeError is a ValueError
        raise SystemExit(f"refused: an input file is malformed or not in the shape this step expects ({type(e).__name__}: {e}); check spec.json, spot.plan.json and captures/*.json")


if __name__ == '__main__':
    raise SystemExit(main())
