# Calibration fixtures

Frozen, synthetic fixtures used to prove a reviewer can catch seeded mistakes before its verdict on a real deliverable is trusted.

- `stay/` — a ranked-stay spec (twelve cards, a table, review files, a trip brief), its built page and evidence (staycation-finder's deliverable)
- `itinerary/` — an itinerary spec, its built page and evidence (vacation-planner's deliverable)

`key.json` in each folder is a SHA-256 answer key (no plaintext answers); `manifest.json` lets `calibrate.py verify` detect edits to the other files, and the key's own hash is pinned in `calibrate.py` (`PINNED_KEYS`), so editing the key to make a reviewer pass changes `calibrate.py` too, where a reviewer of the change can see it. `build_fixtures.py --write` updates both.
Never point a reviewer at this folder: use `../scripts/calibrate.py prepare --domain <d> --out DIR` to make a copy without the key, and score with
`../scripts/calibrate.py score`.

**Rebuilding.** `../scripts/build_fixtures.py` is a dry run by default: it builds into a temp folder and never touches this folder. Re-freezing
needs `--write` (maintainers only). Changing a fixture changes what "calibrated" means, so re-freeze deliberately, then run
`calibrate.py verify` for both domains.

The fixtures use fictional places, a neutral source label (`Platform B`) and neutral dates and groups; no real property, host, reviewer or listing appears (data-source tool names such as the Google Places API are named only as sources).
