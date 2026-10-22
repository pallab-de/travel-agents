#!/usr/bin/env python
"""The mandatory review gate. A report or itinerary is finished only when this computes PASS from the review's own
artifacts. The agent can't type a verdict; it can only produce the work the verdict is computed from.

The judging is done by LLM adversaries; this script checks that they ran, on this exact file, were calibrated, covered
enough of it, and that nothing they found is still open. It can't make a shallow review deep - calibration, coverage
and the gate-chosen sample are what make a shallow one fail.

The review panel (each a fresh agent on the strongest model, seeing the output and the evidence, never the builder's
reasoning or summary):
  facts       every figure, label, link and status against the captures and live pages; staleness
  judgement   ranking: is each overlay earned by a captured fact and a criterion of the fit profile the user agreed, are concerns weighted right (not overindexed,
              not buried), do verdicts follow from the evidence
  plan        timings, legs, bookings against dates, feasibility (only when the output has an itinerary)
  cold_reader sees only the rendered page; lists every place a real reader would hesitate
Code audits (audit_report.py, which also re-runs fact-guard's score gate) run first and must pass.

Layout, inside the project folder:
  review/calibration/<role>.json      calibrate.py score --role <role> --brief ... --out (facts, judgement, plan)
  review/round-<n>/<role>.json        one ledger per reviewer per round (brief-core.md schema, plus the header
                                      fields reviewer, round, output_sha256, model_requested); n = 1..3
  review/resolutions.json             optional: {"<round>:<role>:<claim_id>": {"status": "accepted_by_user",
                                      "reason": "..."}} for a finding the user chose to accept
  review/verdict.json                 written by `verdict`

Subcommands:
  roles    SPEC                        which reviewers this output needs
  verdict  OUTPUT SPEC [--dir review]  compute PASS/FAIL from the artifacts; write review/verdict.json
  check    OUTPUT VERDICT.json         recompute everything from the artifacts the verdict names; GATE PASS/FAIL
  selftest
"""
import argparse, datetime as dt, hashlib, json, math, random, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILLS = HERE.parents[1]
BRIEFS = HERE.parent / "briefs"
sys.path[:0] = [str(HERE), str(SKILLS / "report-builder" / "scripts"), str(SKILLS / "fact-guard" / "scripts")]

MAX_ROUNDS = 3
MAX_UNVERIFIABLE_SHARE = 0.2   # of a reviewer's claims; more than this and the ledger tested too little
CALIBRATION_MAX_AGE_DAYS = 90
LLM_ROLES = ("facts", "judgement", "plan")
CALIBRATION_DOMAIN = {"facts": "stay", "judgement": "stay", "plan": "itinerary"}
ROLE_BRIEFS = {r: ["brief-core.md", f"briefs/{r}.md"] for r in LLM_ROLES}
PER_SAMPLED_ITEM = {"facts": 3, "judgement": 2, "plan": 3}
JUDGEMENT_KINDS = {"verdict", "score_reason", "narrative", "flag"}


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def has_itinerary(spec):
    return any(b.get("type") in ("day_plan", "transport_leg", "booking_ledger") for b in spec.get("blocks", []))


def roles_for(spec):
    return [r for r in LLM_ROLES if r != "plan" or has_itinerary(spec)] + ["cold_reader"]


def item_ids(spec):
    """Stable ids of everything a reviewer can be asked about: cards by id (or name), days by date (or label),
    legs by label, bookings by name."""
    out = {"cards": [], "days": [], "legs": [], "bookings": [], "other": []}
    for b in spec.get("blocks", []):
        t = b.get("type")
        if t == "cards":
            out["cards"] += [str(it.get("id") or it.get("name")) for it in sorted(b.get("items", []), key=lambda x: x.get("rank") or 0)]
        elif t == "day_plan":
            out["days"] += [str(d.get("date") or d.get("label")) for d in b.get("days", [])]
        elif t == "transport_leg":
            out["legs"] += [str(l.get("label")) for l in b.get("legs", [])]
        elif t == "booking_ledger":
            out["bookings"] += [str(k.get("name")) for k in b.get("bookings", [])]
        elif t == "table":
            out["other"].append(f"table:{b.get('id')}")   # one claim per table block: its rows against their sources
        elif t == "cost_comparison":
            out["other"] += [f"option:{o.get('label')}" for o in b.get("options", [])]
        elif t == "research_notes":
            out["other"] += [f"note:{n.get('topic')}" for n in b.get("notes", [])]
        elif t == "decision_log":
            out["other"] += [f"decision:{e.get('title')}" for e in b.get("entries", [])]
    return out


def gate_sample(spec, output_sha):
    """What each reviewer must have covered in depth, chosen by the gate from the output's own hash, so the
    reviewers can't be pointed only at the easy parts: the top 3 cards plus 30% of the rest (at least 5), and 30% of
    the days (at least 3). Legs and bookings are few and consequential, so all of them."""
    rng = random.Random(int(output_sha[:16], 16))
    ids = item_ids(spec)
    cards = ids["cards"]
    rest = cards[3:]
    picked_cards = cards[:3] + sorted(rng.sample(rest, min(len(rest), max(5, math.ceil(0.3 * len(rest))))))
    days = ids["days"]
    picked_days = sorted(rng.sample(days, min(len(days), max(3, math.ceil(0.3 * len(days))))))
    return {"facts": picked_cards + picked_days + ids["legs"] + ids["bookings"] + ids["other"],
            "judgement": picked_cards + picked_days,
            "plan": picked_days + ids["legs"] + ids["bookings"]}


def load_json(path, fails, what):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        fails.append(f"{what}: {path} is missing")
    except json.JSONDecodeError as e:
        fails.append(f"{what}: {path} is not valid JSON ({e.msg})")
    return None


def check_calibration(role, cal_dir, fails, now):
    cal = load_json(Path(cal_dir) / f"{role}.json", fails, f"calibration for {role}")
    if cal is None:
        return None
    if cal.get("role") != role or cal.get("domain") != CALIBRATION_DOMAIN[role]:
        fails.append(f"calibration for {role}: scored as role {cal.get('role')!r} on {cal.get('domain')!r}; "
                     f"needs role {role!r} on the {CALIBRATION_DOMAIN[role]!r} fixture")
    import calibrate
    fails += [f"calibration for {role}: {m}" for m in calibrate.regrade(Path(cal_dir) / f"{role}.json")]
    if not cal.get("calibrated"):
        fails.append(f"calibration for {role}: the reviewer did not pass (recall {cal.get('recall')}, subtle "
                     f"{cal.get('subtle_recall')}); improve the brief and re-run calibrate.py")
    for rel in ROLE_BRIEFS[role]:
        p = HERE.parent / rel
        want = sha256_file(p) if p.exists() else None
        if want is None:
            fails.append(f"calibration for {role}: brief {rel} doesn't exist")
        elif cal.get("briefs", {}).get(Path(rel).name) != want:
            fails.append(f"calibration for {role}: brief {rel} changed since calibration (or wasn't given); re-calibrate")
    t = cal.get("scored_at")
    try:
        age = now - dt.datetime.fromisoformat(str(t).replace("Z", "+00:00"))
        if age > dt.timedelta(days=CALIBRATION_MAX_AGE_DAYS):
            fails.append(f"calibration for {role}: {age.days} days old; re-calibrate at least every {CALIBRATION_MAX_AGE_DAYS} days")
    except ValueError:
        fails.append(f"calibration for {role}: no valid scored_at")
    return cal


def coverage_fails(role, ledger, spec, sample):
    """Every card, day, leg and booking gets at least one claim; each sampled one gets PER_SAMPLED_ITEM claims (of
    a judgement kind, for the judgement reviewer); every card that carries a ranking bump gets a judgement claim."""
    fails = []
    claims = ledger.get("claims", [])
    unver = [c for c in claims if c.get("result") == "unverifiable"]
    if claims and len(unver) > MAX_UNVERIFIABLE_SHARE * len(claims):
        fails.append(f"{role}: {len(unver)} of {len(claims)} claims are unverifiable (limit {MAX_UNVERIFIABLE_SHARE:.0%}); "
                     f"a ledger that couldn't test most of what it listed is not a review")
    by_item = {}
    for c in claims:
        if c.get("result") not in ("holds", "contradicted"):
            continue   # an unverifiable claim tested nothing, so it doesn't count toward coverage
        if role == "judgement" and c.get("kind") not in JUDGEMENT_KINDS:
            continue
        by_item.setdefault(str(c.get("row_id")), []).append(c)
    ids = item_ids(spec)
    scope = {"facts": ids["cards"] + ids["days"] + ids["legs"] + ids["bookings"] + ids["other"],
             "judgement": ids["cards"] + ids["days"], "plan": ids["days"] + ids["legs"] + ids["bookings"]}[role]
    untouched = [i for i in scope if i not in by_item]
    if untouched:
        fails.append(f"{role}: no claim at all on {len(untouched)} item(s), e.g. {untouched[:3]}")
    thin = [i for i in sample[role] if len(by_item.get(i, [])) < PER_SAMPLED_ITEM[role]]
    if thin:
        fails.append(f"{role}: {len(thin)} item(s) the gate sampled have fewer than {PER_SAMPLED_ITEM[role]} claims, "
                     f"e.g. {thin[:3]}")
    if role == "judgement":
        for b in spec.get("blocks", []):
            for it in b.get("items", []) if b.get("type") == "cards" else []:
                rid = str(it.get("id") or it.get("name"))
                if (it.get("score_basis") or {}).get("group_fit") and not any(
                        c.get("kind") == "score_reason" for c in by_item.get(rid, [])):
                    fails.append(f"judgement: {rid} carries ranking bumps but no score_reason claim tests them")
    return fails


def compute(output, spec_path, review_dir, now=None, audit=True):
    """Every reason the review isn't finished, plus the record of what was checked. Empty fails means PASS."""
    import check_ledger
    now = now or utc_now()
    fails, artifacts = [], {}
    output, spec_path, review_dir = Path(output), Path(spec_path), Path(review_dir)
    out_sha = sha256_file(output)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    roles = roles_for(spec)
    sample = gate_sample(spec, out_sha)

    if audit:
        import audit_report
        fails += [f"audit: {f}" for f in audit_report.check(spec_path.parent, output, spec_path.name)]
        # The review verdict stands on the fact-guard gate too: a panel cannot pass a report whose figures fail it.
        if spec_path.name != "spec.json":
            fails.append("fact-guard gate: it runs on the project's spec.json; this spec has another name")
        else:
            import pipeline
            gate_fails, _rows = pipeline.run_gate(pipeline.Project(spec_path.parent), quiet=True)
            fails += [f"fact-guard gate: {f}" for f in gate_fails]

    cals = {}
    for role in roles:
        if role in LLM_ROLES:
            p = review_dir / "calibration" / f"{role}.json"
            cals[role] = check_calibration(role, review_dir / "calibration", fails, now)
            if p.exists():
                artifacts[str(p)] = sha256_file(p)

    rounds = sorted(int(p.name.split("-")[1]) for p in review_dir.glob("round-*") if p.name.split("-")[1].isdigit())
    if not rounds:
        fails.append("no review rounds: run the panel (review/round-1/<role>.json for each reviewer)")
        return fails, {"output_sha256": out_sha, "roles": roles, "rounds": [], "artifacts": artifacts, "sample": sample}
    if rounds != list(range(1, len(rounds) + 1)):
        fails.append(f"review rounds must be numbered 1..n with no gaps; found {rounds}")
    if len(rounds) > MAX_ROUNDS:
        fails.append(f"{len(rounds)} rounds run; the limit is {MAX_ROUNDS}. Stop and show the user what's still open.")
    resolutions = {}
    res_path = review_dir / "resolutions.json"
    if res_path.exists():
        resolutions = load_json(res_path, fails, "resolutions") or {}
        artifacts[str(res_path)] = sha256_file(res_path)

    final = rounds[-1]
    models, open_findings = {}, []
    ledgers = {}
    for n in rounds:
        for role in roles:
            p = review_dir / f"round-{n}" / f"{role}.json"
            L = load_json(p, fails, f"round {n} {role}")
            if L is None:
                continue
            artifacts[str(p)] = sha256_file(p)
            ledgers[(n, role)] = L
            if L.get("reviewer") != role or L.get("round") != n:
                fails.append(f"round {n} {role}: header says reviewer {L.get('reviewer')!r}, round {L.get('round')!r}")
            if n != final:
                continue
            models[role] = L.get("model_requested")
            if L.get("output_sha256") != out_sha:
                fails.append(f"round {n} {role}: reviewed a different version of the output "
                             f"({str(L.get('output_sha256'))[:12]} vs {out_sha[:12]}); the final round must review this exact file")
            if role == "cold_reader":
                for h in L.get("hesitations", []):
                    if h.get("resolution") not in ("fixed", "explained") or not str(h.get("note", "")).strip():
                        fails.append(f"cold reader hesitation {str(h.get('where'))[:40]!r} has no resolution (fixed/explained + note)")
                if "hesitations" not in L:
                    fails.append("cold reader: ledger has no hesitations list (an empty list is fine; a missing one isn't)")
                continue
            errs = []
            check_ledger.schema(L, errs)
            fails += [f"round {n} {role}: {e}" for e in errs]
            fails += [f"round {n} {role} coverage: {f}" for f in coverage_fails(role, L, spec, sample)]
            for f in L.get("findings", []):
                key = f"{n}:{role}:{f.get('claim_id')}"
                acc = resolutions.get(key) or {}
                if acc.get("status") == "accepted_by_user" and str(acc.get("reason", "")).strip():
                    continue
                open_findings.append(key)
    for (n, role), L in sorted(ledgers.items()):
        nxt = ledgers.get((n + 1, role))
        if nxt is None or role == "cold_reader":
            continue
        rows_by_claim = {c.get("id"): str(c.get("row_id")) for c in L.get("claims", [])}
        for f in L.get("findings", []):
            key = f"{n}:{role}:{f.get('claim_id')}"
            if (resolutions.get(key) or {}).get("status") == "accepted_by_user":
                continue
            row = rows_by_claim.get(f.get("claim_id"), str(f.get("row_id")))
            retested = any(str(c.get("row_id")) == row and c.get("result") == "holds" and key in (c.get("resolves") or [])
                           for c in nxt.get("claims", []))
            if not retested:
                fails.append(f"round {n + 1} {role}: the finding {key} on {row} was never re-tested; a claim on that row with "
                             f"result 'holds' and \"resolves\": [\"{key}\"] is needed before the fix counts")
    if open_findings:
        if final >= MAX_ROUNDS:
            fails.append(f"{len(open_findings)} finding(s) still open after {MAX_ROUNDS} rounds ({open_findings[:5]}): "
                         f"stop and show the user; they may accept a finding with a reason in review/resolutions.json")
        else:
            fails.append(f"{len(open_findings)} finding(s) open in round {final} ({open_findings[:5]}): fix each as a "
                         f"class, rebuild, and run round {final + 1} with the full prior ledgers")
    for role, m in models.items():
        if role in LLM_ROLES and not m:
            fails.append(f"round {final} {role}: model_requested is not recorded")
    record = {"output_sha256": out_sha, "spec_sha256": sha256_file(spec_path), "roles": roles, "rounds": rounds,
              "sample": sample, "models_requested": models, "artifacts": artifacts,
              "accepted_by_user": {k: v for k, v in resolutions.items() if v.get("status") == "accepted_by_user"},
              "calibrations": {r: {k: c.get(k) for k in ("recall", "subtle_recall", "scored_at", "model_requested")}
                               for r, c in cals.items() if c}}
    return fails, record


def cmd_roles(a):
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    for r in roles_for(spec):
        briefs = ROLE_BRIEFS.get(r, ["briefs/cold_reader.md"])
        print(f"{r:<12} brief: {', '.join(str(HERE.parent / b) for b in briefs)}")
    return 0


def cmd_verdict(a):
    review_dir = Path(a.dir) if a.dir else Path(a.spec).resolve().parent / "review"
    fails, record = compute(a.output, a.spec, review_dir)
    doc = dict(record, verdict="PASS" if not fails else "FAIL", fails=fails, review_dir=str(review_dir.resolve()),
               spec=str(Path(a.spec).resolve()), computed_at=utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"))
    out = Path(a.out) if a.out else review_dir / "verdict.json"
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    for f in fails:
        print("FAIL", f)
    print(f"VERDICT {doc['verdict']} -> {out}")
    return 0 if not fails else 1


def check_verdict(output, verdict_doc, audit=True):
    """Recompute the verdict from the artifacts it names; never trust the stored PASS."""
    fails = []
    if verdict_doc.get("verdict") != "PASS":
        fails.append(f"verdict is {verdict_doc.get('verdict')!r}, not PASS")
    if verdict_doc.get("output_sha256") != sha256_file(output):
        fails.append("the output changed after the verdict was computed; review it again")
    for p, h in (verdict_doc.get("artifacts") or {}).items():
        if not Path(p).exists() or sha256_file(p) != h:
            fails.append(f"review artifact {p} changed or disappeared after the verdict")
    if verdict_doc.get("spec") and verdict_doc.get("review_dir"):
        again, _ = compute(output, verdict_doc["spec"], verdict_doc["review_dir"], audit=audit)
        fails += [f"recomputed: {f}" for f in again]
    else:
        fails.append("verdict doesn't name its spec and review folder, so it can't be recomputed")
    return fails


def cmd_check(a):
    fails = check_verdict(a.output, json.loads(Path(a.verdict).read_text(encoding="utf-8")))
    for f in fails:
        print("FAIL", f)
    print("GATE " + ("PASS" if not fails else f"FAIL ({len(fails)})"))
    return 0 if not fails else 1


def selftest():
    import check_ledger  # noqa: F401  (the schema check must be importable)
    cases = []
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        spec = {"blocks": [{"type": "cards", "id": "c", "items": [
            {"id": f"p{i}", "name": f"P{i}", "rank": i, **({"score_basis": {"group_fit": [{"signal": "x"}]}} if i == 1 else {})}
            for i in range(1, 7)]}]}
        (td / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        out = td / "report.html"; out.write_text("<html>v1</html>", encoding="utf-8")
        rv = td / "review"; (rv / "calibration").mkdir(parents=True)
        now = dt.datetime(2030, 3, 1, tzinfo=dt.timezone.utc)
        osha = sha256_file(out)

        # A real calibration: build the itinerary fixture in a temp folder, grade an oracle ledger with calibrate.py,
        # and keep the ledger beside the result so the gate can score it again.
        import build_fixtures
        import calibrate as calmod
        fixture_root = td / "fixtures"
        calmod.CAL = build_fixtures.CAL = fixture_root
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            built = build_fixtures.build_itinerary(2026)
        calmod.PINNED_KEYS['itinerary'] = calmod.key_sha('itinerary')
        global CALIBRATION_DOMAIN
        CALIBRATION_DOMAIN = {r: "itinerary" for r in LLM_ROLES}
        prepared = td / "prepared"; prepared.mkdir()
        oracle = {"findings": [{"where": l, "row_id": i, "code": c} for (l, i, c) in [(d["list"], d["id"], d["code"]) for d in built["D"]]], "inputs_seen": ["spec.json"], "outputs_written": []}
        (td / "oracle.json").write_text(json.dumps(oracle), encoding="utf-8")

        def calibrate_role(role, ok=True, when=None, tamper=None):
            briefs = [str(HERE.parent / b) for b in ROLE_BRIEFS[role]]
            target = rv / "calibration" / f"{role}.json"
            ledger_in = td / ("oracle.json" if ok else "empty.json")
            if not ok:
                (td / "empty.json").write_text(json.dumps({"findings": [], "inputs_seen": [], "outputs_written": []}), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                calmod.score("itinerary", str(ledger_in), str(prepared), role, briefs, "opus", str(target))
            doc = json.loads(target.read_text(encoding="utf-8"))
            if when:
                doc["scored_at"] = when
            if tamper:
                doc.update(tamper)
            target.write_text(json.dumps(doc), encoding="utf-8")

        def calibrate(role, ok=True, when="2030-02-20T00:00:00Z"):
            calibrate_role(role, ok=ok, when=when)

        def ledger(role, n, sha=None, per=3, findings=(), skip=(), unver=False, resolves=()):
            claims, k = [], 0
            for i in range(1, 7):
                if f"p{i}" in skip:
                    continue
                for _ in range(per):
                    k += 1
                    claims.append({"id": f"c{k}", "where": "cards", "row_id": f"p{i}", "row": f"P{i}",
                                   "kind": "score_reason" if role == "judgement" else "number", "text": "t", "test": "t",
                                   "evidence": "e", "result": "contradicted" if findings and k == 1 else ("unverifiable" if unver else "holds"),
                                   "code": "C1" if findings and k == 1 else None, **({"resolves": list(resolves)} if resolves and k == 1 else {})})
            return {"reviewer": role, "round": n, "output_sha256": sha or osha, "model_requested": "opus",
                    "inputs_seen": [], "outputs_written": [], "claims": claims, "unverified": [],
                    "findings": [{"claim_id": "c1", "where": "cards", "row_id": "p1", "code": "C1", "what": "w", "evidence": "e", "fix": "f"}] if findings else [],
                    "verdict": "no" if findings else "yes"}

        def write_round(n, **kw):
            d = rv / f"round-{n}"; d.mkdir(exist_ok=True)
            for role in ("facts", "judgement"):
                (d / f"{role}.json").write_text(json.dumps(ledger(role, n, **kw.get(role, {}))), encoding="utf-8")
            (d / "cold_reader.json").write_text(json.dumps(kw.get("cold", {"reviewer": "cold_reader", "round": n,
                                                                             "output_sha256": osha, "hesitations": []})), encoding="utf-8")
        run = lambda: compute(out, td / "spec.json", rv, now=now, audit=False)[0]

        cases.append(("no rounds fails", any("no review rounds" in f for f in run())))
        calibrate("facts"); calibrate("judgement")
        write_round(1)
        cases.append(("a calibrated, covering, clean round passes", run() == []))
        cases.append(("a pure stay needs no plan reviewer", "plan" not in roles_for(spec)))
        cases.append(("an itinerary needs the plan reviewer", "plan" in roles_for({"blocks": [{"type": "day_plan", "days": []}]})))
        calibrate("judgement", ok=False)
        cases.append(("an uncalibrated reviewer fails", any("did not pass" in f for f in run())))
        calibrate("judgement", when="2029-06-01T00:00:00Z")
        cases.append(("a stale calibration fails", any("days old" in f for f in run())))
        calibrate("judgement")
        stale_brief = json.loads((rv / "calibration" / "facts.json").read_text()); stale_brief["briefs"]["facts.md"] = "0" * 64
        (rv / "calibration" / "facts.json").write_text(json.dumps(stale_brief))
        cases.append(("a brief edited since calibration fails", any("changed since calibration" in f for f in run())))
        calibrate("facts")
        write_round(1, facts={"sha": "0" * 64})
        cases.append(("a ledger for another version of the output fails", any("different version" in f for f in run())))
        write_round(1, facts={"per": 1})
        cases.append(("thin coverage of the gate's sample fails", any("gate sampled" in f for f in run())))
        write_round(1, judgement={"skip": ("p1",)})
        cases.append(("a card never examined fails, and its bump goes untested", any("no claim at all" in f for f in run())
                      and any("ranking bumps" in f for f in run())))
        write_round(1, facts={"findings": True})
        cases.append(("an open finding in round 1 asks for round 2", any("run round 2" in f for f in run())))
        (rv / "resolutions.json").write_text(json.dumps({"1:facts:c1": {"status": "accepted_by_user", "reason": "the user knows the venue changed hands"}}))
        cases.append(("a finding the user accepted with a reason passes", run() == []))
        (rv / "resolutions.json").unlink()
        write_round(2); write_round(3, facts={"findings": True})
        cases.append(("a finding still open after round 3 stops for the user", any("stop and show the user" in f for f in run())))
        write_round(3); write_round(4)
        cases.append(("a fourth round is over the limit", any("the limit is 3" in f for f in run())))
        import shutil
        for n in (2, 3, 4):
            shutil.rmtree(rv / f"round-{n}")
        write_round(1, cold={"reviewer": "cold_reader", "round": 1, "output_sha256": osha, "hesitations": [{"where": "hero", "text": "unclear"}]})
        cases.append(("an unresolved cold-reader hesitation fails", any("hesitation" in f for f in run())))
        write_round(1)
        fails, record = compute(out, td / "spec.json", rv, now=now, audit=False)
        doc = dict(record, verdict="PASS", fails=fails, review_dir=str(rv), spec=str(td / "spec.json"))
        cases.append(("check recomputes and passes an untouched verdict", check_verdict(out, doc, audit=False) == []))
        led = rv / "round-1" / "facts.json"; L = json.loads(led.read_text()); L["claims"] = L["claims"][:2]; led.write_text(json.dumps(L))
        cases.append(("check fails when a ledger is edited after the verdict", any("changed or disappeared" in f for f in check_verdict(out, doc, audit=False))))
        write_round(1); doc = dict(compute(out, td / "spec.json", rv, now=now, audit=False)[1], verdict="PASS", review_dir=str(rv), spec=str(td / "spec.json"))
        out.write_text("<html>v2</html>", encoding="utf-8")
        cases.append(("check fails when the output changes after the verdict", any("output changed" in f for f in check_verdict(out, doc, audit=False))))
        cases.append(("a typed PASS with no artifacts can't pass", bool(check_verdict(out, {"verdict": "PASS", "output_sha256": sha256_file(out)}, audit=False))))
        out.write_text("<html>v1</html>", encoding="utf-8"); osha = sha256_file(out)
        write_round(1)
        cases.append(("a real calibration (oracle ledger re-scored by the gate) passes", run() == []))
        calibrate_role("facts", ok=False, tamper={"calibrated": True, "recall": 0.95, "subtle_recall": 0.9})
        cases.append(("a calibration marked passed whose stored ledger scores badly fails", any("scoring its ledger again" in f for f in run())))
        handwritten = rv / "calibration" / "facts.json"
        hw = json.loads(handwritten.read_text()); hw.pop("ledger_file", None); handwritten.write_text(json.dumps(hw))
        cases.append(("a hand-written calibration with no ledger fails", any("no stored ledger" in f for f in run())))
        calibrate("facts")
        hw = json.loads(handwritten.read_text()); hw["prepared_dir"] = None; handwritten.write_text(json.dumps(hw))
        cases.append(("a calibration that never audited its inputs fails", any("never audited" in f for f in run())))
        calibrate("facts")
        write_round(1, facts={"unver": True})
        cases.append(("a ledger of nothing but unverifiable claims fails coverage", any("unverifiable" in f for f in run()) and any("no claim at all" in f for f in run())))
        write_round(1, facts={"findings": True}); write_round(2)
        cases.append(("a round-1 finding with no re-test in round 2 fails", any("never re-tested" in f for f in run())))
        write_round(2, facts={"resolves": ("1:facts:c1",)})
        cases.append(("a re-tested finding in round 2 passes", run() == []))
        shutil.rmtree(rv / "round-2")
        write_round(1)
        s1, s2 = gate_sample(spec, "a" * 64), gate_sample(spec, "b" * 64)
        cases.append(("the gate's sample always includes the top 3 and depends on the output", s1["judgement"][:3] == ["p1", "p2", "p3"] and len(s1["judgement"]) == 6))
        cases.append(("the same output always gets the same sample", gate_sample(spec, "a" * 64) == s1 and isinstance(s2, dict)))
    # Ledger rules: a verdict is yes or no, "no" says what is wrong, an empty ledger tests nothing, and every block with figures is in scope.
    def schema_errs(L):
        errs = []
        check_ledger.schema(L, errs)
        return errs
    one = {"id": "c1", "where": "w", "row_id": "r", "text": "t", "test": "t", "evidence": "e", "result": "holds", "kind": "number"}
    base = {"claims": [one], "findings": [], "unverified": [], "verdict": "yes"}
    cases.append(("a verdict that is neither yes nor no fails", any("verdict must be" in e for e in schema_errs(dict(base, verdict="maybe")))))
    cases.append(("a 'no' with no finding fails", any("no finding says" in e for e in schema_errs(dict(base, verdict="no")))))
    cases.append(("a ledger with no claims fails the schema", any("zero claims" in e for e in schema_errs(dict(base, claims=[])))))
    ids = item_ids({"blocks": [{"type": "table", "id": "all", "items": []}, {"type": "cost_comparison", "options": [{"label": "A"}]},
                               {"type": "research_notes", "notes": [{"topic": "T"}]}, {"type": "decision_log", "entries": [{"title": "D"}]}]})
    cases.append(("tables, cost options, notes and decisions need claims too", set(ids["other"]) == {"table:all", "option:A", "note:T", "decision:D"}))
    ok = all(p for _, p in cases)
    for name, p in cases:
        print("PASS" if p else "FAIL", name)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("roles"); p.add_argument("spec"); p.set_defaults(f=cmd_roles)
    p = sp.add_parser("verdict"); p.add_argument("output"); p.add_argument("spec"); p.add_argument("--dir"); p.add_argument("--out"); p.set_defaults(f=cmd_verdict)
    p = sp.add_parser("check"); p.add_argument("output"); p.add_argument("verdict"); p.set_defaults(f=cmd_check)
    p = sp.add_parser("selftest"); p.set_defaults(f=lambda a: selftest())
    a = ap.parse_args()
    sys.exit(a.f(a))


if __name__ == "__main__":
    main()
