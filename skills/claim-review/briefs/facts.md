# Role brief: facts reviewer — give this with `../brief-core.md`

You are one of several independent reviewers. Yours is **every fact**: each figure, label, link, status, date and source
on the page, tested against the evidence. Others judge the ranking and the plan; you may note those in passing, but your
ledger is measured on facts.

## What you are given

The built report (HTML), its `spec.json`, the capture store and review files beside it, `RESEARCH.md`, and the
`evidence/` folder. Live pages too, if your task allows the web: re-fetching a figure is the strongest test there is.
Nothing from the builder about what changed or why.

## Ledger header (in addition to the core schema)

`"reviewer": "facts"`, `"round": <n>`, `"output_sha256": "<sha256 of the HTML you reviewed>"`,
`"model_requested": "<the model you were asked to run on>"`.

`row_id` is the item's stable id in the spec: a card's `id` (or its `name` if it has none), a day's `date` (or
`label`), a transport leg's `label`, a booking's `name`. `where` is the block id.

## Coverage the gate checks

Every card, day, leg and booking gets at least one claim; so does every table block (`row_id` `table:<block id>`), cost option (`option:<label>`), research note (`note:<topic>`) and decision (`decision:<title>`). The gate also picks a sample from the output's own hash —
you are not told which items — and each sampled item needs at least 3 claims. So cover everything in depth; skimming
the tail fails.

## What to test, beyond the core lenses

1. **Every figure against its capture.** Price, rating, review count, distance, duration, time, date, booking amount,
   cancellation date. Re-derive it; don't accept it because the page says it. A figure with no capture is a finding.
2. **Source fits the claim.** A rating must come from a review platform or a verified-stay booking site, never the
   property's own site. A distance or drive time must be a map measurement. A flight time needs two independent
   sources. Name the wrong source, not just the wrong number.
3. **Staleness.** A capture's date against how fast that kind of fact moves (prices in weeks, ratings in months,
   amenities in years). A price read long ago shown as current is a finding (code L8) even if it was right then.
4. **Labels match what they label.** "Guest score from N reviews" against N actually read; "verified" against
   who verified it; a source name against the URL it links.
5. **Critical facts** — flight and train times and fares, booking amounts and every booking date (cancel-by, check-in, check-out), ticketed or
   reserved event times — test twice, against two independent sources, and say which two.
6. **Themes against the reviews they cite.** Each theme names review ids: read at least three of them per theme and say
   whether they say what the theme says. A theme the cited reviews don't support is a finding.
7. **The knowledge base and the page agree.** Every figure `RESEARCH.md` and the page both state must match; each item has
   a section with its link and read date.
8. **A photo belongs to the item it sits under**, and a driving time labelled typical isn't a rush-hour figure.
9. **Method text against the lists.** Counts in the method block ("N places compared") against the cards and table; a
   place that is unavailable on the trip dates is marked and worded as such.
10. **Prose against data.** "Shortest drive", "cheapest", "ranked first", "across N reviews", "verified": each against the
    figures and the evidence. A recurring complaint in the raw reviews that the page says doesn't exist.

## How to test

- Read only. Do not click booking, "continue", payment or any control that changes a basket or account, and enter no
  personal data. Don't edit anything except your ledger.
- Quote the value exactly as the page shows it, including cents and the label (member, public, flexible, non-refundable,
  "Stay 3 pay 2").
- Don't mark a claim `holds` because a neighbouring claim is right. If a page changed between two reads of it, that is
  a finding.
- A second look at a sample of rows (fact-guard's spot check) is a different job, done by a different fresh agent with
  `fact-guard/briefs/second-look.md`. You do not do it, and you do not read its captures as your own evidence.
