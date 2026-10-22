---
name: research-delegation
description: "Fan a broad per-city or per-topic sweep (meals, sights, hotels) out to parallel subagents and audit their output. Use for an itinerary's wide sweeps."
user-invocable: true
---

# Research delegation

A broad sweep ("flesh out every meal and sight for three cities") is independent work per city or per topic, so parallel
subagents are faster than one thread. A subagent's output is not ready to merge when it returns: the main thread audits
it first, every time.

## When to fan out

Split one subagent per city (meals, sights and tips for that city) or one per topic across all cities (all meals, all
photo spots and gotchas). This pays off from about three cities; a single-city trip is fine done in sequence.

## Briefing a subagent

Give each one, explicitly:
- **The standing constraints** from `trip-constraints-intake` / `trip-intake` (route, group and mobility and dietary
  parameters, budget shape, meal-coverage standard), and how a dropped option is recorded: it stays in the spec with
  `status: "rejected"` and a note saying why (`report-builder`), never strikethrough markup and never deleted.
- **What is already scheduled**, so it does not repeat a venue or dish another subagent or an earlier pass placed.
- **How to research:** a wide keyword sweep (`things to do in <city>`, `site:reddit.com <city> itinerary`,
  `<city> hidden gems`, `<city> avoid tourist trap`); read full threads, not snippets (convergence across several
  threads is the signal, not one clever comment); then, per named recommendation, hours, price and operating status
  through `venue-verification`'s tools before it counts as usable.
- **An approach plan per sweep** (`approach-planning`: `plans/<sweep>.plan.json`, sources and expected yield, written before the subagent
  starts) and a `plans/<sweep>.fetch_log.json` it fills in, so `check_approach.py` can show the sweep ran.
- **The verify-before-assert rule** (`venue-verification`), which binds a subagent as much as the main thread.

## Auditing what comes back

Check all of these before merging anything:

- **Repetition** across subagents and against what is planned: two subagents choosing the same well-reviewed spot for
  the same meal two days running is the most common failure, because neither sees the other. Grep the whole document
  for repeated venue names, not just the new section.
- **Standing-convention violations:** no strikethrough; dense content (a time, a venue and a concrete tactic, not
  generic advice); meal coverage of a preferred option plus 1 to 2 alternates, each with hours, rating and distance
  from where the traveller is at that hour.
- **Thin or single-sourced claims presented as settled.** A subagent under pressure can round one blog mention up to a
  confident recommendation. Spot-check a sample of its named venues with `venue-verification`'s tools instead of
  trusting its citations.
- **Real findings against restated obvious ones.** The value is the specific, non-obvious fact (a dish native to one
  city, corroborated by independent reviewers). Filler ("great atmosphere, friendly staff") must not weigh the same.
- **Reasoning errors, not only facts.** Findings can each be true and still argue badly. Use the fallacy catalogue in
  `claim-review/brief-core.md` as the checklist (absence of evidence as evidence of absence, a small sample as strong,
  circular justification, "confirmed" with no source, quotes hiding a pattern, wrong-season or wrong-group evidence),
  and record each claim you spot-checked with the test you ran.
- **Every community fetch a conclusion rests on.** A subagent can be handed an empty, blocked or login-walled page
  without noticing. Run `venue-verification/scripts/check_fetch.py <page text> --expect <place>` on a sample of the raw
  text behind its key claims (`fetch-integrity`).

## Gotchas

- **A subagent aiming for a complete answer papers over gaps** (no verified breakfast before a certain hour, an
  operator's season that cannot be established). Read for confidence the evidence does not support.
- **Check `git status` and `git diff` before merging** when another session may be editing the same repo.
- **A blocked fetch and a true "nothing found" look identical** without the raw page text: "no results" after a CAPTCHA
  wall reads exactly like a proper search that found nothing.

## Done when

- Every subagent was briefed with the standing constraints and what is already scheduled, not a bare topic.
- The merge pass checked cross-subagent repetition, not each output alone.
- A sample of each subagent's named venues was spot-checked with `venue-verification`'s tools.
- Convention violations (strikethrough, generic advice, missing distance) were fixed before the merge.
- `check_fetch.py` ran on a sample of the raw pages behind non-obvious claims.
