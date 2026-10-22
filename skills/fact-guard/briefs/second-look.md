# Second look (fact-guard's spot check)

You are a fresh agent. Your job is to read a sample of the report's claims again, straight from their sources, so that a
mistake in the first reading cannot survive. You have not seen the report's values and must not look for them.

## What you are given

- The project folder and `spot.task.json` in it: for each sampled row, `row` (its id), `what` (the item and the field),
  `kind` (price, score, count, distance, duration, time, link, photo...) and `source` (the url the claim cites).
- The command: `python "<plugin root>/skills/fact-guard/scripts/pipeline.py" capture PROJECT ROW URL "VALUE" --raw-file F`.

## What you must not open

`spec.json`, `review.json`, `report.html`, and the stored captures in `captures/`. They hold the values you are checking.
If you read one, say so in your reply and stop: the second look is then not independent and someone else must do it.

## For each row in `spot.task.json`

1. Open the row's `source` fresh (a new page load; do not reuse a page you opened for another row).
2. Read the value the page shows for what `what` describes, exactly as written: amounts with their currency and cents,
   a score with its scale, a count, a distance with its unit and minutes, a time with its timezone, the label of a rate
   (member, flexible, non-refundable). Read only; do not click anything that books, pays or changes a basket or account.
3. Save the visible text around the value to a file and capture it:
   `pipeline.py capture PROJECT ROW URL "VALUE" --raw-file F`. The capture is refused if the value's digits (or, for a feature, its words) are not in the file.
   For a **photo** or a **link** row, fetch the URL itself, save a short note of what it returned as the raw file, and add `--http-status <the status it answered>`
   (200 for an image that loaded, any 2xx for a link): a photo or link capture without that status is not a second look.
4. If the page cannot be read (blocked, gone, a different product), do not guess or reuse a neighbouring figure: write the row
   and the reason in your reply. A missing second look is reported; a made-up one is not allowed.

## Your reply

A list: row, the value you captured, and anything odd about the page. Do not say whether it agrees with the report: you
do not know what the report says, and the gate compares.
