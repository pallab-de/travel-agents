---
name: report-review
description: Reviews a built stay report by falsification, not approval. Runs the deterministic audits first (flag-vs-field consistency, abstained-match audit, staleness), then the claim-review panel, then a scripted design sweep (overflow, contrast, tap targets, focus, images, links) across widths and themes. Use after report-builder, as the last stage before calling a report finished.
user-invocable: true
---

# Report review

A report is not finished when it builds. It is finished when the deterministic audits are clean, the calibrated review
panel's verdict is computed PASS, the cold reader found nothing left unexplained, and the design sweep passes. The method
(claim ledger, calibration, panel, fix as classes) is `claim-review`: read it first. This skill adds what is specific to a
ranked-stays report.

## The loop

0. **Deterministic audits; fix everything before any agent is spent.**
   - `scripts/audit_consistency.py --main report_data.json --beyond beyond_data.json` on the pre-spec rows (at least one
     list is required): every flag tested against its own row (a "no Google rating" flag beside a Google place link,
     "only N reviews" with a different N, a rating source with no place behind it, an empty place id, contradictory rating
     flags, ranks or scores out of order, superlatives such as "shortest drive" that the row's own data contradicts). It
     prints a FLAG INVENTORY of every kind of claim the report makes.
   - `place-matching/scripts/audit_abstains.py merged.json --pins jittered` and, with direct leads, `... mb_final.json --pins exact`: an independent nearby-lodging check on every row the matcher
     gave up on.
   - `fact-guard/scripts/pipeline.py check PROJECT` for every report: nothing built from the spec is stale, the page exists
     and embeds this spec, and the knowledge base covers every item. (`--report FILE` if the page has another name.)
1. **Calibrate each reviewer** on the frozen fixtures (`claim-review`, procedure step 1): facts and judgement on `stay`.
2. **Run the panel** (`claim-review`, procedure steps 3-5): facts, judgement and a cold reader on the built page, each a
   fresh agent on opus, with the evidence and never the builder's account of what changed. Fix findings as classes, and re-run
   with the FULL prior ledgers, at most 3 rounds; then stop and show the user what is still open.
3. **Design sweep** (below).
4. **Compute the verdict** (mandatory): `claim-review/scripts/review_gate.py verdict REPORT.html spec.json`, then
   `review_gate.py check REPORT.html review/verdict.json` must print `GATE PASS`. It is computed from the ledgers, the
   calibrations and the audit; it can't be typed. No report is presented without it.

## Domain checks to give the reviewers in addition to their briefs

- Every specific, checkable claim in the report's own text (a method block's "N places compared") re-derived from the data.
- The same fact stated twice for one place in different words (a bed count, "assumed", "non-refundable", a fee): keep it in
  one place.
- Safety-relevant facts sitting unused in already-fetched raw listing text (a pool-safety statement, a bedroom-count
  mismatch between a header and the sleeping list).
- Does a place with a real recurring concern outrank one with none, for no stated reason? Does a verdict match its own
  score's tier? A concern or watch-out follows fact-guard's overindexing rule (two independent sources, or enough reviews
  read with a real share raising it), or is marked isolated.
- Every abstain-audit pair whose Google name is related to the listing's is decided same or different; any SAME is a matcher
  bug fixed in `place-matching`, never a place id pasted into the data.

## Design sweep (scripted: do not eyeball screenshots)

Run `scripts/design_sweep.js` in the browser (Playwright MCP `browser_run_code_unsafe`, passing the file's contents as the
code) against the built HTML served over http (`file:` is blocked). At widths 360, 390, 768, 1024, 1180, 1400 and 1920, in
light and dark, it prints one line per failure for: horizontal overflow; text and background contrast (at least 4.5:1 for
normal text, 3:1 for large); interactive targets under 24x24 px (a FAIL; 24-44 px is reported as advisory); images that
errored (it loads the lazy ones first); empty or `#`-only links; plain-text URLs; console errors.

Then, in the same session, check the engine's affordances by hand from every surface that offers them:
- sort a numeric column both ways (unavailable rows sort last), the filter menus and the min-max range filter, group-by, the
  column chooser, compare (up to 4 rows);
- open a details panel from a card, a table row and a chart point; Previous/Next and the arrow keys step through what is on
  screen; Esc closes it and focus returns to the opener; the panel has an accessible name;
- the theme toggle, and no horizontal scroll at 390 px.

Only propose a CSS or JS fix after injecting it live and re-measuring.

## Gotchas

- **A flag or warning is a claim, and reviewers read them in isolation.** The most damaging errors are two fields on one row
  disagreeing; only a check that puts them side by side finds them.
- **A matcher built to abstain rather than guess has a silent failure mode: the confident-looking miss.**
  `audit_abstains.py` is the independent second path.
- **A screenshot looking right is not evidence for an interactive or accessibility claim.** Each item above has a mechanical
  check.
- **"Regression-free" and "done" are claims**: say which checks you ran and what they showed.
- **A reviewer's silence on an item from a previous round doesn't mean it is still fine**: every round re-checks the full list.

## Done when

- The audits and `pipeline.py check` are clean, or every remaining finding is dispositioned in writing.
- Each reviewer was calibrated for its role with its current briefs; each finding has a class-level fix recorded or a user
  acceptance with their reason.
- The cold reader's hesitations were each resolved (fixed, or explained).
- `design_sweep.js` reports no failures on the CURRENT build in both themes, and the affordance checks passed after the
  last fix.
- `review_gate.py check` passes against the exact file being presented, not an earlier build.
