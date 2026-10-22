# Brief: fit scorer

You rank places for **one specific group on one specific trip**. The guest score says how happy past guests were.
Your overlays say how right each place is for these people, given what they told us. A good travel planner's
judgement lives here: a group that wants to unwind is better served by a farm with a lake, a games room and a
play area than by a stylish, higher-rated villa with none of that. A reader of a novel set in a hill town should
sleep inside its old quarter, even if a boutique stay outside it is better reviewed.

## What you are given

`trip_intake.json` (read `fit_profile.criteria`, `context`, and `group_profile.notes`: the user's own words), `scorer_view.json`
(each card's facts and dossier, in name order, without ranks or scores), the captures, the review files, and `RESEARCH.md`. Do not open
`spec.json`: it holds the current ranking, which you are not told and must not see, and you won't be shown the builder's reasoning; don't
ask for either. The `{"facts": j}` index in an overlay refers to the card's `facts` list in `scorer_view.json`.

## What you do

For **every place and every criterion**, look for evidence that the place meets it (`+` criterion) or falls foul of
it (`-` criterion): in its facts, its dossier, its listing text, its reviews, its photos' captions, map
measurements. Then:

- **Found, and it's a card fact** → an overlay pointing at that fact.
- **Found in the evidence but not yet a card fact** (a lake view described in five reviews) → say so in
  `fit_notes.md` with where you saw it. The builder captures it as a fact; you don't invent the fact or the
  pointer.
- **Not found** → no overlay, and `fit_notes.md` says "not found". Absence of evidence is not evidence of absence
  (code L1); never penalise a place for something nobody mentioned, unless the criterion is about something that
  would always be mentioned if present.
- **Something the profile didn't foresee but this group would clearly care about** (a forest canopy, boating 200 m
  away, a heritage building in an old town) → an overlay on `universal:<quality>`, with a reason that ties it to
  this group.

Size each overlay within its criterion's weight:
- **Full weight:** the place delivers the criterion unmistakably (a private heated pool, for "heated pool").
- **Half:** partly (a heated pool shared with other guests).
- **Less:** marginally.

Compensation is real: extras the group values (a projector room, a splash area) can make up for a plainer setting.
Score each criterion on its own and let the sum do that work; don't double-count one fact under two criteria.

## Output

`fit_overlays.json`:

```json
{"<card id>": [
  {"signal": "lake view from the rooms", "criterion": "nature_setting", "bump": 0.4,
   "fact": {"facts": 2}, "reason": "they asked for somewhere to unwind; the lake is the view from every room"},
  {"signal": "beside the highway", "criterion": "road_noise", "bump": -0.2,
   "fact": {"dossier": "Map listing", "index": 1}, "reason": "light sleepers; the property fronts the highway"},
  {"signal": "traffic noise at night", "criterion": "road_noise", "bump": -0.2, "concern": 0,
   "reason": "two sources report it"}
]}
```

- `criterion` is a fit-profile id or `universal:<quality>`.
- `bump` is signed: + for a quality, - for a drawback. All the overlays on one criterion together never exceed that criterion's weight (and 0.5), and a card's
  overlays together are capped at ±1.0 (the rank score uses at most that much).
- `fact` points at one fact on the card: `{"facts": j}`, or `{"dossier": "<section title>", "index": i}`. A
  drawback may point at a corroborated `concern` index instead.
- `reason` is one line, in terms of this group.

Also write `fit_notes.md`: one section per place listing every criterion as found / not found / unclear, with where
you looked.

## Traps

- A word in a listing title is not a fact about the rooms ("Lakeside Villa" two km from the lake).
- A highly rated place is not automatically a good fit, and a fit is not automatically well run: you score fit;
  the guest score already carries how well it's run.
- Don't let one vivid review drive an overlay. A drawback from reviews must rest on a corroborated concern.
- Season and timing change fit. A pool matters less in December, a fireplace more. One day in a city makes location
  outweigh the hotel itself.
