---
name: claim-review
description: "The review method for any deliverable: falsification by calibrated fresh reviewers (facts, judgement, plan, cold reader) over a claim ledger. Use before anything is called finished."
user-invocable: true
---

# Claim review — the shared review method

Every agent ships a deliverable a person will act on (a ranked report; an itinerary). "An agent read it and said yes" is not evidence, so the review is a procedure whose result is computed from artifacts, not typed. `report-review` adds the deterministic audits and a domain brief; everything here is shared.

## What makes a review believable

1. **Deterministic audits first.** Anything a script can check (arithmetic, dates, links, flag-vs-field, schedule feasibility) is checked by a script and fixed before an agent is spent. The reviewer's attention is for reasoning.
2. **A claim ledger, not an impression.** The reviewer writes down every claim, the test it ran, the evidence it found, and the result (`brief-core.md`). `scripts/check_ledger.py` rejects a malformed or empty ledger and `review_gate.py` rejects thin coverage, so a skimmed "yes" cannot pass.
3. **A fallacy catalogue.** Named errors to hunt (contradiction, absence treated as evidence, small sample treated as strong, circularity, equivocation, overclaimed certainty, wrong context...), so reasoning errors are looked for on purpose.
4. **Calibration.** A reviewer's silence means nothing until it has been shown to find planted mistakes. `scripts/calibrate.py` runs it on a frozen synthetic fixture and scores it (below).
5. **A cold reader.** A separate agent that sees only the rendered deliverable, with no data and no brief, and lists everywhere it hesitated. It can't be argued into the builder's reasoning because it doesn't know it.
6. **Fix classes, not rows.** Every finding is traced to the rule that produced it; that rule is fixed, everything downstream is re-run, and a new audit rule plus a new fixture defect are added if no existing check would have caught it.

## Calibration design

A reviewer is scored on a fixture it cannot shortcut:

- **Frozen synthetic fixtures** (`calibration/stay/`, `calibration/itinerary/`): a fictional region or trip with internally consistent data and defects seeded once; the clean version is never stored, so there is nothing to diff against.
- **Self-contained evidence, including a stand-in for Google**: no network, no project files, nothing else to open. Runs cheaply and identically every time.
- **Hashed answer key**: the reviewer can't read the answers.
- **Stable ids** (`row_id`), never names: a reviewer that shortens a name still gets credit for the catch.
- **Decoys**: correct-but-suspicious elements; flagging them counts against the reviewer, so "flag everything" fails.
- **Difficulty by detectability**: a defect is *easy* if a deterministic audit also catches it, *subtle* if only reasoning does. The gate is on subtle recall.
- **Input audit**: `inputs_seen` must stay inside the prepared folder.
- **The scorer is itself tested** (`build_fixtures.py --selftest`): an oracle ledger must pass; a flag-everything ledger and an empty ledger must fail.

Gate: overall recall ≥ 0.80, subtle recall ≥ 0.70, the right code on at least 70% of the defects found, at most 2 findings on decoys, no inputs outside the fixture.

## What calibration proves, and what it does not

It shows that a reviewer setup, run on a fixture it could not look the answers up in, finds seeded mistakes at the gate's rates. It does not
prove which model ran, and the fixtures' defect lists sit in this repository (`scripts/fixture_*.py`) so the fixtures can be rebuilt: a
reviewer that was allowed to read the repository could find them. The input audit is self-reported (`inputs_seen`), so run the reviewer
with only the prepared folder and the two brief files in reach, and read its ledger rather than only its verdict.

## The review panel

The judging is done by LLM adversaries, each a fresh agent (Agent tool, `model: "opus"`, the strongest available,
whatever model built the output), given the output and the evidence and never the builder's reasoning. Each has
one job, so its attention isn't spread across every kind of mistake:

| Reviewer | Brief | Looks for |
|---|---|---|
| facts | `brief-core.md` + `briefs/facts.md` | every figure, label, link, status and date against the evidence; wrong source for the kind of claim; staleness; critical facts against two sources |
| judgement | `brief-core.md` + `briefs/judgement.md` | ranking overlays earned by a captured fact and an agreed fit criterion; concerns neither overindexed nor buried; verdicts that follow from their reasons; the group, not a generic traveller |
| plan | `brief-core.md` + `briefs/plan.md` | feasibility with these people, opening days, door-to-door transfers, bookings against the plan (only when there's an itinerary) |
| cold_reader | `briefs/cold_reader.md` | the rendered page only: every place a first-time reader hesitates |

`scripts/review_gate.py roles spec.json` prints which ones this output needs.

## Procedure

1. **Calibrate each LLM reviewer** (facts and judgement on the `stay` fixture, plan on the `itinerary` fixture):
   `python "<plugin root>/skills/claim-review/scripts/calibrate.py" prepare --domain D --out DIR`; run the reviewer on DIR with its two brief files;
   `python "<plugin root>/skills/claim-review/scripts/calibrate.py" score --domain D --role ROLE --ledger DIR/ledger.json --dir DIR --brief "<plugin root>/skills/claim-review/brief-core.md" --brief "<plugin root>/skills/claim-review/briefs/ROLE.md" --model opus --out review/calibration/ROLE.json`.
   The facts reviewer is gated on consistency defects, judgement on logic defects. Not calibrated: add the missed
   class to the role brief, with a concrete example, and repeat. `--dir` is required: the reviewer's
   inputs are audited against that folder. The result records each brief's hash and keeps the ledger beside it
   (`ROLE.ledger.json`); the review gate scores that ledger again, so a hand-written result fails. Editing a brief
   means calibrating again; the result also expires after 90 days.
2. **Run the code audits** on the deliverable and fix everything (`report-builder/scripts/audit_report.py`; it also
   re-runs fact-guard's score gate). The gate runs it again itself, so a skipped audit is caught.
3. **Round 1:** run every reviewer in parallel. Each writes `<project>/review/round-1/<role>.json`: the core ledger
   schema plus `reviewer`, `round`, `output_sha256` (of the exact HTML it read) and `model_requested`.
4. **Fix each finding as a class** (the rule that produced it, not the one value), rebuild, and run the next round
   with every reviewer given the FULL prior ledgers. Each earlier finding must be re-tested: a claim on the same row with
   result `holds` and `"resolves": ["<round>:<role>:<claim_id>"]`. Answer each cold-reader hesitation with `resolution` (`fixed` or
   `explained`) and a `note`.
5. **Stop after round 3.** If findings are still open, stop and show the user each one. They may accept a finding;
   record it in `review/resolutions.json` as `{"<round>:<role>:<claim_id>": {"status": "accepted_by_user",
   "reason": "<their reason, in their words>"}}`. Never record an acceptance the user didn't give.
6. **Compute the verdict:** `review_gate.py verdict REPORT.html spec.json`. It reads the artifacts and decides; there
   is no way to type PASS. It fails unless:
   - the audit passes, and fact-guard's project gate (`pipeline.py gate`) passes on `spec.json`;
   - every needed reviewer has a current calibration for its exact briefs;
   - the final round reviewed this exact file;
   - every card, day, leg and booking was examined;
   - the items the gate sampled from the file's hash were examined in depth;
   - every ranking bump was tested;
   - no finding is open unless the user accepted it.
7. **Before presenting the file as finished**, run `review_gate.py check REPORT.html review/verdict.json` and show
   its `GATE PASS` line. `check` recomputes everything from the artifacts, so a ledger edited afterwards, or a
   changed report, fails.

What this can't do: prove which model a reviewer ran on (it records what was asked for), or make a shallow reviewer
deep. Calibration, coverage and the gate's own sample are what make a shallow review fail.

## Gotchas

- **A calibrated reviewer is calibrated on the fixture's kinds of error.** It is a floor, not a guarantee; that is why real-run findings feed new defect classes into `build_fixtures` (and why the ledger, not the verdict, is what you read).
- **Never edit a frozen fixture to make a reviewer pass.** `calibrate.py verify` detects it; changing a fixture changes what "calibrated" means, so rebuild it deliberately with `build_fixtures.py --write` (a plain run is a dry run that never touches the frozen folder).
- **Don't hand a reviewer the builder's summary of what changed.** It shares the builder's blind spots.
- **A finding closed by editing one value is not closed.** Find the rule; fix it; re-run downstream.

## Done when

- Each LLM reviewer was calibrated for its role with its current briefs (calibration result in `review/calibration/`), and the run's `inputs_seen` stayed inside the fixture.
- `review_gate.py check` prints `GATE PASS` for the exact file being presented.
- Every finding has a class-level fix recorded, or a user acceptance with their reason.
- `calibrate.py verify` reports both fixtures (`stay`, `itinerary`) intact.
