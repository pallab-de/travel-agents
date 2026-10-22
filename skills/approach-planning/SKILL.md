---
name: approach-planning
description: "Before any multi-source search, write plan.json (sources, search variants, expected yield), then check what actually ran against it. Catches a source that was silently skipped."
user-invocable: true
---

# Approach planning

Write the research plan before searching: the sources, the search variants and the expected yield for each. After the
fetch, `scripts/check_approach.py` compares the fetch log against the plan, so a source that was skipped or a sweep that
came back empty fails loudly instead of looking like "there wasn't much there".

## Every agent uses this the same way

The sources are whatever the agent's search stage uses: the platform's base search, the map-grid sweep, the no-amenity-filter sweep and each
"beyond the platform" search (`listing-search`); each city or topic sweep and its keyword variants (`research-delegation`). One plan entry per source.
This sits in front of the research method: a short plan written before it runs, a short check after.

## Steps

1. **Before fetching, copy `plan.template.json`** into the project folder (one plan per sweep: `plans/<sweep>.plan.json` and `plans/<sweep>.fetch_log.json` when there are several) and fill it in: set `created_at` to the current time, and for each source,
   its name, *why* it's being used, the specific search variants planned, and an `expected_min_yield` — a
   rough number that would indicate the sweep actually ran and found real coverage (not a precise target;
   a number that would make you go "that's suspiciously low" if missed). Give each open-ended source (a map-grid
   sweep, a multi-page community search) `"open_ended": true` and its own `stop_rule`: a stated point to stop, not "until
   it feels done". The check fails an open-ended source without one.
2. **Do the research**, per the relevant skill (`listing-search`, `research-delegation`).
3. **Write down what actually happened** as you go — `fetch_log.json`, one entry per source: whether it was
   attempted, the time (`at`), the actual yield, and (if the yield came in well under the plan) a one-line reason (a genuinely
   thin market, a source that turned out to be paywalled, a rate limit hit). A low yield isn't a failure by
   itself; an *unexplained* one is what this catches.
4. **Run `scripts/check_approach.py plan.json fetch_log.json`** before moving on to the next stage
   (place-matching, or writing findings into the spec). It fails on: an empty or unfilled plan (no sources, a blank or duplicate name, `expected_min_yield` of 0), a plan not
   dated before the first fetch, a planned source never attempted,
   a yield far under `expected_min_yield` with no reason given, or (as a warning, not a failure) a source
   used that was never in the plan — fine to do, but update the plan so it's not invisible next time.

## Gotchas

- **A plan is not a promise you can't deviate from.** Real research surfaces things a plan didn't
  anticipate — add the new source to the plan when you find it, don't skip planning it just because it's
  already been searched. The check flags an unplanned source as a warning, not an error, specifically so
  this doesn't punish genuine adaptation.
- **`expected_min_yield` set too high just produces noise you'll learn to ignore** (the same failure mode
  `fetch-integrity` warns about for coverage thresholds) — base it on what a real prior search in a
  comparable city/market actually returned, not a round number picked without evidence.
- **The stop rule matters most for open-ended sweeps.** Without one, "I searched enough" is a feeling, not a
  fact — a map-grid sweep that quietly stopped after 2 cells instead of the planned 12 looks the same in the
  final candidate count as one that ran the whole grid and genuinely found nothing new past cell 2.

## Done when

- `plan.json` was written and reviewed BEFORE the first fetch of this stage, not reconstructed afterward to
  match whatever happened (a plan written after the fact checks nothing).
- `check_approach.py` passes, or every failure it reports has a stated, specific reason recorded in
  `fetch_log.json` — not silently ignored.
- Every source in the final report or itinerary traces back to an entry in `fetch_log.json`; nothing was used
  that was never logged as having been fetched.
