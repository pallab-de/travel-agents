# Contributing

Thanks for helping improve travel-agents. This guide covers the ground rules, the layout, and the checks to run.

## Ground rules

- **No personal data.** Do not commit names, home addresses, trip dates, group compositions, booking references
  or receipts. Use neutral placeholders such as `<home base address>` and `YYYY-MM-DD`.
- **No real API responses or scraped data.** Listing data, place caches and review text belong to their owners
  and often have terms that forbid redistribution. Build small synthetic examples instead.
- **No secrets in commits.** Keys live only in the encrypted `secrets/` store. Run `git check-ignore -v <path>`
  on any new data or cache file to confirm it is ignored before you add it.
- **Frozen calibration fixtures are never edited to make a reviewer pass.** See the calibration section below.

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # macOS/Linux; use .venv\Scripts\pip on Windows
```

For live checks you need a Google Maps key. Keep it in an encrypted store. For development, point
`TRAVEL_SECRETS_DIR` at a folder that holds a dev store, so the key never sits in the repo.

## Layout rules

- Shared, agent-agnostic code goes in `tools/`. Skill code goes in `skills/<name>/scripts/`.
- Resolve plugin-owned paths relative to the script with `Path(__file__)` (for example
  `Path(__file__).resolve().parents[3] / 'tools'`).
- Read project data through `Path.cwd()`. Scripts are run from the trip folder, and they write there.
- Do not hardcode a city, party size, date, budget or brand in shared code. Put it in a flag, a config field or a
  config field, and refuse to run without it (see `merge.py`'s `--config` and `--booking-url`).
- Before adding an HTTP or API client, check `tools/google_maps.py` and `tools/travel_secrets.py` first.

## Adding a skill or agent

- A skill is a folder under `skills/<name>/` with a `SKILL.md` whose frontmatter `description` says when to use it.
- An agent is `agents/<name>.md`. Its `tools:` frontmatter must list every tool it needs, including any MCP tools
  such as Playwright. Missing grants fail silently at run time.
- Add the new skill to the catalogue table in `README.md`.

## Checks to run before a pull request

1. Run every offline check: `python tools/run_selftests.py` (each script's `selftest`, the calibration fixtures'
   integrity, the agent frontmatter check, a compile of every `.py`). A new script with logic gets a `selftest`
   subcommand and is picked up automatically.
2. Run any deterministic audit that covers your change (see the Tests section of `README.md`).
3. Scan for personal content before you commit. Search for your own name, your home city and any trip dates you
   used while testing.

## Calibration fixtures

`skills/claim-review/calibration/` holds frozen, seeded fixtures that prove a reviewer catches known mistakes.
`calibrate.py verify` checks them against `manifest.json`.

- Do not edit a frozen fixture, its key or its manifest to make a check pass. A failing `verify` is information.
- `build_fixtures.py` is a dry run; `--write` re-freezes the set (maintainers only). Run it only when you mean to change what
  "calibrated" means, and say so in the pull request.

## Version bump

Bump `version` in `.claude-plugin/plugin.json` on every change that users will receive. Installed copies are
cached by version, so an unbumped change will not reach users.

## Commits and pull requests

- One concern per pull request. Describe the "why" in the description, not only the "what".
- Do not commit generated HTML reports, caches (`__pycache__/`, `*.json` from a live run), `secrets/`, or
  screenshots from a real trip.
- Keep the diff minimal. Do not reformat files you are not changing.
- `main` is protected: no direct pushes and no force pushes. Work on a branch, push it, open a pull request, and squash-merge it (history stays linear and the
  branch is deleted on merge). Run `python tools/privacy_scan.py --install-hooks` once so a commit or push that carries a key or a personal detail is refused locally.

## Reporting problems

Security issues: follow [SECURITY.md](SECURITY.md) and do not open a public issue. Other bugs: open an issue
with the command you ran, the output with secrets and personal details removed, and your Python version.
