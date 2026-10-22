---
name: fetch-integrity
description: "Run after every fetch of a page or API response you do not control, whoever fetched it (listing-search, research-delegation, venue-verification or you). Fails loudly on a blocked, empty or changed page."
user-invocable: true
---

# Fetch integrity

A site can change the JSON schema its pages embed. Nothing crashes: the field names an extractor relied on stop
existing and it returns empty or missing fields, which look exactly like "we found nothing". A parser that goes silent
when its target changes is worse than one that crashes. So any fetch a later stage will trust gets a check right after
it.

## Which check fits

- **A page with a consistent schema** (Airbnb or Booking.com search results parsed by `listing-search`): a field-coverage
  check against a fixed manifest, with `tools/coverage_check.py` (below).
- **An arbitrary community page** (a Reddit thread, a listicle, fetched by a browser): no fixed fields, so a content
  check. `venue-verification/scripts/check_fetch.py` tests the text for a minimum length, block, CAPTCHA and login-wall
  markers, and an expected term.
- **A Google Maps API response:** a `status` field that must be checked, not assumed `OK` (Gotchas).

## Using `tools/coverage_check.py`

```python
from coverage_check import FieldSpec, check, report
specs = [
    FieldSpec('listing name', lambda r: r.get('name'), min_coverage=0.95),
    FieldSpec('price', lambda r: r.get('price_amount'), min_coverage=0.90, numeric_range=(500, 200000),
              cross_check=lambda r: float(r['price_amount']) in [float(x) for x in re.findall(r'\d+(?:\.\d+)?', (r.get('price_text') or '').replace(',', ''))]),   # the exact number, not a substring: 150 is not 1500
    FieldSpec('coordinates', lambda r: r.get('ll') and tuple(r['ll']), min_coverage=0.98),
]
ok, results = check(candidates, specs)
print(report(ok, results, 'Airbnb search results'))
if not ok: raise SystemExit(1)   # do not hand a failing batch to place-matching / review-synthesis
```

`min_coverage` is below 1.0 for most fields because some real listings lack a field (no rating yet, no photos); it
catches most of a batch losing a field, which is what drift looks like. `numeric_range` catches a gross scale or unit
error (cents for dollars). It cannot tell a field swapped for another with similar values (a review count where a price
was expected): use `cross_check`, which tests a record's value against another field of the same record (above, the
price must appear in the price text). String values such as `'1,234'` are read as numbers.

## Steps

1. **Fetch a small batch first** (10 to 20 records) and check it before scraping hundreds: a schema change is cheaper
   to find on 10 records than after an hour-long sweep.
2. **Check immediately after extraction**, before any later stage (place-matching, review-synthesis, the spec). A
   failing batch is not partly useful data: stop, diagnose, fix the extraction, re-run.
3. **On failure read the per-field report first.** It names the field whose coverage collapsed, which usually shows what
   moved. Confirm with one live fetch and a read of the current page structure before changing extraction code.
4. **Re-verify the schema periodically even when nothing looks broken**: a new field can coexist with the old for a
   while. Record when the assumptions were last confirmed against a live page, in the extractor's header.

## Gotchas

- **A batch of zero records is a failure.** `check()` treats it as one; confirm with a live spot check that zero is real.
- **Do not raise `min_coverage` to 1.0 "to be safe".** It produces constant false alarms and trains people to ignore
  the check. Set it from what a healthy batch looks like.
- **A Maps API `status` must be checked.** `ZERO_RESULTS`, `OVER_QUERY_LIMIT` and `REQUEST_DENIED` come alongside `OK`;
  treating any non-`OK` as "not found" turns a quota error into a false abstain. Use `tools/google_maps.py`'s
  `require_ok(response, what)`, and add no call site that swallows a status.
- **A community page can load (200 OK) and still be a block page, CAPTCHA or login wall.** A navigation that does not
  error is not evidence the content is real.

## Done when

- Every fetch stage that feeds a later stage ran a check on this session's data (re-run per project, as
  `place-matching`'s regression test is per city).
- A failing check was fixed in the extraction and the whole batch re-run, never by lowering the threshold.
- The extraction code dates its schema assumptions, so staleness is visible.
