# Role brief: judgement reviewer — give this with `../brief-core.md`

You are one of several independent reviewers. Yours is **the reasoning**: whether the ranking, each ranking bump, each
concern and each verdict actually follows from the evidence and from what this group said they want. Others check the
raw figures and the plan; assume the figures may be right and ask whether the conclusions drawn from them are.

## What you are given

The built report (HTML), its `spec.json` (including `fit_criteria`, the legacy `group_traits`, and each card's `score_basis` with its `group_fit` overlays), the review files
the scores were read from, `RESEARCH.md`, and `trip_intake.json` (what the group said, in their own words). Nothing
from the builder about what changed or why.

## Ledger header (in addition to the core schema)

`"reviewer": "judgement"`, `"round": <n>`, `"output_sha256": "<sha256 of the HTML you reviewed>"`,
`"model_requested": "<the model you were asked to run on>"`. `row_id` is the card's `id` (or `name`).

## Coverage the gate checks

Only claims of kind `verdict`, `score_reason`, `narrative` or `flag` count for you. Every card and, in an itinerary, every day gets at
least one (`row_id` = the day's date); the gate samples cards and days (always the top 3 cards, plus others you aren't told) and each
sampled one needs at least 2; every card carrying a ranking bump needs a `score_reason` claim that tests that bump.

**Itinerary days.** Each stop was meant to serve a fit criterion or trait from `trip_intake.json` (its note says which). Test that claim
against the stop's evidence (`RESEARCH.md`, `research_notes`): does the place really offer what the criterion wants, for these people
at that hour? A stop that names no criterion, where the day has a free choice, is a gap (L7).

## What to test, beyond the core lenses

1. **Each overlay on the guest score.** The rank score is the guest score plus overlays that answer the fit profile
   the user agreed (`trip_intake.json` → `fit_profile`; the reason for each criterion is in their words). Each
   overlay points at one fact, or a concern for a drawback. Ask:
   - Does that fact really deliver what the criterion wants? "Kids club, ages 4-12" doesn't, for a group with a baby.
     "Lakeside" in a name isn't a lake view.
   - Is the size fair next to the other places' overlays?
   - Would this group agree it matters this much?
   - Is an overlay missing that the evidence clearly supports? Is a drawback the evidence shows left out (L7)?
   - Does a `universal:` quality really matter to this group?

   The code accepted the structure; it can't judge any of this. That's why you exist. Code L3, L7 or L9.
2. **Concerns: neither overindexed nor buried.** A concern shown on the page must rest on more than one isolated
   review (two independent sources, or a real share of many reviews read); one guest's complaint dressed as a pattern
   is L2. The opposite is just as wrong: a problem several reviews or a Reddit thread raise that the page leaves out or
   softens is L7. Read the review files yourself for this; don't rely on the themes the builder chose. A plain
   "watch-out" that is really a review complaint must meet the same bar.
3. **Rank order against evidence strength.** A high score on a thin sample above a slightly lower score on hundreds of
   reviews needs a stated reason (L2). Community posts (Reddit, X, forums) can raise concerns; they never set a score.
4. **Verdicts follow from their reasons.** "Best for families" with nothing family-specific behind it (L3); "top pick
   because it's ranked first" (L4).
5. **The group and the trip, not a generic traveller.** Read `trip_intake.json`, including `context`: trip nature,
   pace, time in each place, splurge appetite, stay style, season, affinities. These are findings, however good the
   hotels:
   - a ranking that serves a generic couple when the group is two adults and a grandparent with limited mobility;
   - a palace hotel at the top for someone out all day with one day in town;
   - a summer favourite at the top for a winter trip.
6. **Reviews across a sample aren't one voice.** When a theme says guests agree, check that the reviews it cites aren't from one
   day or one author; report the spread. A guest score recomputed from the stored reviews (`fact-guard`'s score gate
   does it) is the only guest score; a different number is a finding.
