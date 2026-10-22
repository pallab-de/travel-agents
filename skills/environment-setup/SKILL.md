---
name: environment-setup
description: "Use before the first script or API call in a project: Python venv, API-key store (asks where it lives; encrypted), Playwright MCP. Run credential_status.py first; it never shows a key value."
user-invocable: true
---

# Environment setup

Three things must exist before an API-calling or browser-searching skill runs: a Python environment with this plugin's dependencies, an encrypted place for API keys, and (if the platform needs a real browser) the Playwright MCP server. Check all three each session: a venv can belong to another shell, a key can have expired, an MCP server can be unregistered in a fresh session.

## 1. Python environment — one venv per PROJECT, not one shared across projects

```bash
python -m venv .venv
# Windows:
.venv\Scripts\python -m pip install -r "<plugin root>/requirements.txt"
# macOS/Linux:
.venv/bin/python -m pip install -r "<plugin root>/requirements.txt"
```

Run this in the PROJECT's own folder (the trip you are working on), not in the plugin's folder and not reusing another
project's venv. Scripts take the project folder as the working directory (they read and write data files relative to
`Path.cwd()`). One venv per project keeps `pip list` honest and means deleting a finished project removes its
dependencies too. **Exception:** infrastructure shared across projects, such as a locally run MCP server like
`flightclaw`, gets its own venv outside any project folder.

`requirements.txt` at the plugin root lists the only non-standard-library packages (`Pillow` for photo handling and
`visual-identity`'s palette extraction; `cryptography` for the secrets store below). Point `pip install -r` at the
plugin's copy, installing into the project's `.venv`; do not copy it into the project.

## 2. Encrypted secrets — what it is and how it works

**The model:** a plaintext `.env` file is never committed, never read by Claude, and never sits on disk for
longer than an active edit. Instead, you (the user) create it, run one script to encrypt it into `.env.enc`,
and that script deletes the plaintext itself the moment encryption is verified. From then on Claude (via
`tools/travel_secrets.py`) reads only the encrypted file, decrypts it in memory using a locally-generated key
file, and never prints or logs a value.

**Why:** a plaintext `.env` in a project folder is one `git add .`, one screen-share, or one accidental paste
into chat away from leaking. The encrypted form means the only two files on disk are `.env.key` (a random
44-byte key, meaningless without the matching `.env.enc`) and `.env.enc` (unreadable without that key) — both
gitignored, and Claude is never the one creating or reading the plaintext.

### Where the keys live — confirm this with the user once

The encryption is **not tied to a person or a machine**. `.env.key` is a random key that sits in the same folder as `.env.enc`; anyone who holds *both*
files can decrypt on any computer, and anyone with only one cannot. What the encryption buys you is that no key is ever plain text in a file that an
assistant, a `grep` or a screen-share can read. What keeps the pair private is **where the folder is**. So the folder must never be committed (it is
gitignored) and should not sit in a folder that a sync service uploads (OneDrive, Dropbox, iCloud Drive, Google Drive), because the sync would carry the
key file and the encrypted file to the cloud together.

Before the first key is written, **ask the user** (AskUserQuestion, one question, and do not ask again once answered) where to keep the store:

| Choice | What it means |
|---|---|
| **Default: `secrets/` in this project folder** | Nothing to configure. Fine if the project folder is local-only. If `setup_check.py` or `credential_status.py` print a warning that it is inside OneDrive / Dropbox / iCloud / Google Drive, recommend one of the next two. |
| **A per-user local folder** (recommended when the project folder is synced) | One store shared by every trip, kept out of any sync folder. Windows `%LOCALAPPDATA%\travel-agents\secrets`; macOS `~/Library/Application Support/travel-agents/secrets`; Linux `~/.config/travel-agents/secrets`. The user sets `TRAVEL_SECRETS_DIR` to it once (Windows: `setx TRAVEL_SECRETS_DIR "%LOCALAPPDATA%\travel-agents\secrets"`, then restart the terminal and Claude Code; macOS/Linux: `export TRAVEL_SECRETS_DIR=...` in the shell profile). |
| **An existing store from another project** | Point `TRAVEL_SECRETS_DIR` at it. |
| **Environment variables, no files** (cloud sessions, CI, containers) | Set `GOOGLE_MAPS_API_KEY` (and any other name from the table below) in the environment. An environment variable of the same name is used before the encrypted store, on every operating system. Nothing is written to disk. |

Never ask the user to paste a key into the chat. If the store is already inside a synced folder, say so plainly, offer to help move it (copy the two files
with the user's own file manager or shell; do not open them), and tell the user that the keys which were synced should be **rotated** at the provider,
because a cloud copy may remain in its version history. Whatever the answer, run `setup_check.py` afterwards and report any warning it prints.

### First-time setup (you do this yourself; do not ask Claude to open or create the plaintext file)

1. Copy `skills/environment-setup/scripts/.env.template` to `secrets/.env` in your project folder.
2. Open it in your own editor and fill in the keys you need (a `GOOGLE_MAPS_API_KEY` at minimum — see below
   for how to get one; leave a review-provider key blank unless `requirements-intake` confirmed you want one;
   leave `OPENROUTESERVICE_API_KEY` blank unless vacation-planner's `venue-verification` needs a non-Google
   distance provider).
3. Run the encryption script:
   ```bash
   .venv/Scripts/python "<plugin root>/skills/environment-setup/scripts/encrypt_env.py"     # Windows
   .venv/bin/python "<plugin root>/skills/environment-setup/scripts/encrypt_env.py"          # macOS/Linux
   ```
   This writes `secrets/.env.enc` and `secrets/.env.key` (generating the key on first run, reusing it on
   later runs), **verifies the encrypted file actually decrypts back to what you just wrote, then deletes
   the plaintext `secrets/.env` itself** — you don't do that step by hand, and it only happens after that
   verification, not on blind faith that the write succeeded.
4. Tell Claude you're done. From here, every script calls `tools/travel_secrets.py`'s
   `load_key("GOOGLE_MAPS_API_KEY")`, which decrypts `secrets/.env.enc` in memory using `secrets/.env.key`
   and hands back just that one value — never the whole file, never printed.

### Adding a new credential, or changing an existing one, later

The store is already encrypted at this point, so there's no plaintext `.env` sitting around to edit. The
cycle is symmetric with setup:

1. Run the decryption script — **you ask for this explicitly when you have a new key to add**, Claude
   doesn't run it unprompted since it recreates a plaintext credentials file:
   ```bash
   .venv/Scripts/python "<plugin root>/skills/environment-setup/scripts/decrypt_env.py"     # Windows
   .venv/bin/python "<plugin root>/skills/environment-setup/scripts/decrypt_env.py"         # macOS/Linux
   ```
   This writes plaintext `secrets/.env` from what's currently in `secrets/.env.enc`, decrypted with the
   existing `secrets/.env.key` (the key itself doesn't change, so nothing that already reads from this store
   breaks).
2. Edit `secrets/.env` yourself to add or change whatever key you need — same rule as first-time setup,
   Claude doesn't open or edit this file.
3. Run `encrypt_env.py` again (same command as step 3 above). It re-encrypts with the same key, verifies the
   round-trip, and deletes the plaintext automatically — closing the window you opened in step 1.

The plaintext exists only between steps 1 and 3, and step 3 deletes it. If you decrypt and change your mind, delete
`secrets/.env` yourself: `decrypt_env.py` only reads `.env.enc`.

**Getting a Google Maps API key:** console.cloud.google.com → APIs & Services → Credentials → Create API key, then enable "Places API", "Distance Matrix API" and "Geocoding API" on it (APIs & Services → Library). A free-tier project easily covers one city's search.

**If you already have an encrypted secrets store from another project** (so you don't have to make a new key for every city), set the environment variable `TRAVEL_SECRETS_DIR` to that folder instead of creating a new one — `tools/travel_secrets.py` reads from there. Run `python "<plugin root>/skills/environment-setup/scripts/setup_check.py"` afterward to confirm it resolves. The decrypt/edit/re-encrypt cycle above still applies — just run it in that other project's folder, since that's where its `secrets/` directory actually lives.

**Verify:** `python "<plugin root>/skills/environment-setup/scripts/setup_check.py"` (from the project folder) checks the venv, the two packages, and that the encrypted files exist — without ever reading their contents. It also flags a stray plaintext `secrets/.env`, which at this point in the workflow usually means an unfinished decrypt→edit→encrypt cycle, not a forgotten manual cleanup step.

## First-run credential walkthrough (for `travel-agent`)

The first time `travel-agent` runs in a project folder, walk through **every credential the plugin can use
across any trip type, in one pass** — not lazily, one key at a time, as a later stage happens to need it. `staycation-finder`/`vacation-planner` can still
reach this section lazily if a user invokes them directly without going through `travel-agent` first.

0. **Where the keys live.** If no store exists yet, or `credential_status.py` below prints a WARNING that the secrets folder is in a cloud-synced folder, ask the
   user the "Where the keys live" question above before anything else, and apply the answer (default, per-user local folder, existing store, or environment variables).
1. Run `python <plugin root>/skills/environment-setup/scripts/credential_status.py` from the project folder.
   It never reads or prints a value — only which named credentials are already set — so re-running the
   walkthrough on a later run **never re-prompts for a key that's already there.**
2. For each credential still missing, say in plain terms: what it unlocks, roughly what it costs (free tier vs
   paid, where known — this goes stale as providers change pricing, so point at the provider's own pricing page
   rather than quoting a number here), and where to get it:
   - **`GOOGLE_MAPS_API_KEY`** — unlocks almost everything (place matching, drive/walk/transit times, landmark
     distances). Free tier covers a normal trip's usage. `console.cloud.google.com` → APIs & Services →
     Credentials → Create API key, then enable Places API, Distance Matrix API and Geocoding API on it.
     **Known limit:** this plugin calls Google's legacy endpoints (Places API (Legacy), Distance Matrix API (Legacy), Geocoding). Google moved the first two to Legacy status on 1 March 2025 and does not offer them to Cloud projects created after that (see https://developers.google.com/maps/legacy). Projects that already had them enabled keep working. If your project cannot enable them, calls fail with `REQUEST_DENIED`; use an older project, and see the migration goal to Places API (New) and the Routes API.
   - **`APIFY_TOKEN`** (optional) — a licensed review provider, for a guest score built from more than the
     reviews the Places API returns per place (5 per call, about 10 with google_reviews10.py; fact-guard needs at least 20 rated reviews read to score a place). Paid per call — only set this up if the user has
     confirmed they want deeper review coverage for this trip (`requirements-intake`/`trip-constraints-intake`
     ask this explicitly; don't assume yes). Get a token from `apify.com`.
   - **`SERPAPI_API_KEY`** (optional, recommended for any trip with a flight) — see "SerpApi, step by step"
     below. Unlocks air legs in `transport-options` (fares, schedules, flight numbers from Google Flights) and
     20+ Google Maps reviews per place for the guest score (the free Places API stops at about 10).
   - **`OPENROUTESERVICE_API_KEY`** (optional) — an alternative to Google's Distance Matrix for
     `venue-verification`'s distance checks. Free tier at `openrouteservice.org/dev/#/signup`.
   - **`flightclaw` MCP** (recommended for any trip with a flight; not a plugin-managed secret, a separately
     registered MCP server) — see "flightclaw, step by step" below. A flight's time and fare are critical
     facts that need two independent sources; flightclaw (or the airline's own site) is the second one next
     to SerpApi.
   - **seats.aero API key** (optional, not plugin-managed) — real miles/award-seat search. The traveller's own
     account and key; this plugin never creates or holds one on their behalf.
3. **Explain the storage/security model in the user's own terms, every time, not just the first time**: each
   key is encrypted at rest (`.env.enc` + `.env.key`, both gitignored), decrypted only in-process when a script
   needs it, never printed, logged, or pasted into chat — Claude never sees the raw value, only the fact that a
   named key exists (see "Encrypted secrets" above for the full mechanics).
4. **A skipped key degrades gracefully, it doesn't hard-fail the whole run.** A trip with no review provider (`APIFY_TOKEN` or
   `SERPAPI_API_KEY`) has only the Places API's 5 to 10 reviews per place: too few for a guest score, and without a guest score a place gets no fit ranking either, so the report lists them in search order and says it is not ranked; a trip with no
   `OPENROUTESERVICE_API_KEY` just uses Google's Distance Matrix instead; a leg with no flight-fare need never
   touches `flightclaw` at all. Say plainly which capability is reduced and why, don't block on it.

### SerpApi, step by step

SerpApi runs Google Flights and Google Maps searches under its own licence and returns them as data, so the plugin
never scrapes those pages. Walk the user through this; they type the key themselves, Claude never sees it.

1. **Sign up** at `https://serpapi.com/users/sign_up`. The free plan gives a monthly allowance of searches; the
   current numbers are on `https://serpapi.com/pricing` (don't quote them from memory, they change). A trip
   typically uses a few searches per flight leg (one per date or route searched) and one per review page (about
   10 reviews each, so 2-3 per place).
2. **Copy the key** from `https://serpapi.com/manage-api-key`.
3. **Add it to the encrypted store** in the project folder. If there is no store yet, copy
   `scripts/.env.template` to `secrets/.env`, fill `SERPAPI_API_KEY=` (and any other keys), then run
   `encrypt_env.py`. If a store already exists, run `decrypt_env.py`, add the line `SERPAPI_API_KEY=<key>` to
   `secrets/.env`, then run `encrypt_env.py` again (it deletes the plaintext once the round trip is verified).
   The user edits `secrets/.env` in their own editor; Claude never opens it.
4. **Verify** with `python "<plugin root>/skills/environment-setup/scripts/credential_status.py" --verify`. It
   calls SerpApi's account endpoint, which costs no search, and prints the plan and searches left. It never
   prints the key.
5. **What uses it:** `transport-options/scripts/compare_modes.py air` (flights) and
   `review-synthesis/scripts/serpapi_reviews.py fetch` (reviews). Both stop with an error and write nothing if
   SerpApi refuses the request (bad key, allowance used up), so an outage is never mistaken for "no flights" or
   "no reviews".

**How it's secured:** the key lives only in `secrets/.env.enc` (encrypted, gitignored) with its decryption key in
`secrets/.env.key` (gitignored). A script decrypts it in memory for the one request and drops it. SerpApi takes the
key as a URL parameter, so `tools/serpapi.py` never prints, logs or puts a request URL in an error message.

### flightclaw, step by step

flightclaw is an MCP server for Google Flights search, date-grid price scans and price tracking. It is
not a key in the encrypted store; it is registered with Claude Code. Two ways to install it (from the project's own
README, `https://github.com/jackculpan/flightclaw`):

- **Hosted (simplest).** Run `claude mcp add --transport http flightclaw https://mcp.flightclaw.com/mcp`, then
  sign in through the OAuth prompt the first time a flightclaw tool is used. Nothing to install locally.
- **Local open-source server (no account).**
  1. `git clone https://github.com/jackculpan/flightclaw.git` into a folder of your choice.
  2. Create a virtual environment in it and install its dependencies (its `setup.sh` lists them:
     `pip install "flights==0.9.0" "mcp[cli]<2" fastmcp pydantic-settings`).
  3. Register it: `claude mcp add flightclaw --scope user -- <that venv's python> <clone folder>/server.py`.
  4. If every search comes back "No flights found", even for a busy route, the underlying `flights` library
     is out of date with Google's site. Upgrade it inside flightclaw's venv (`pip install --upgrade flights`)
     and restart Claude Code; `flight-research/SKILL.md` has the details, including what to do if the release
     still fails (a reviewed, pinned commit, never an unpinned branch).

**Verify:** `claude mcp get flightclaw` shows it connected, and `credential_status.py` lists it as registered.
A newly added server only appears after `/mcp` reconnect or a restart of the Claude Code session.

## 3. Playwright MCP (for browser-based search)

`listing-search` needs a real browser when the target platform has no public API (true of every Airbnb-style listing site). `research-delegation`/`venue-verification` need one too, as a last resort, for content that isn't reachable by a script call — a listicle page with no extractable structure, or a community site (e.g. Reddit) that's network-blocked for a plain fetch. This plugin doesn't bundle Playwright MCP itself — check, live, whether it's already available in this session:

1. Use `ToolSearch` with the keyword query `"playwright browser_navigate"` (never an exact-name `select:`: the tool is `mcp__playwright__browser_navigate` when registered with `claude mcp add`, and `mcp__plugin_playwright_playwright__browser_navigate` when it comes from the official plugin). Any tool whose name ends in `browser_navigate` counts. If a real tool schema comes back, it's already configured: move on.
2. If nothing comes back, tell the user Playwright MCP isn't registered and guide them to add it (do not attempt to install or register an MCP server yourself without the user's go-ahead, since it changes their Claude Code configuration):
   - Simplest: `claude mcp add playwright -- npx @playwright/mcp@latest` in their shell (requires Node.js installed), then restart the session.
   - Or, if their Claude Code build ships an official Playwright plugin/marketplace entry, point them at installing that instead.
3. Re-run the `ToolSearch` check after they confirm it's added, before starting `listing-search`.

## Gotchas

- **Never open, create, or `cat` a plaintext `.env`.** If one exists on disk mid-session (the user is running `decrypt_env.py` to add a key, or is mid-first-setup), leave it alone and wait for them to say they're done — don't run `encrypt_env.py` FOR them either, since you can't see whether their edit is actually finished.
- **Claude doesn't run `decrypt_env.py` unprompted.** It's the user's call when a new credential is needed; Claude can point out that a script is now failing for lack of a key (`travel_secrets.load_key` raises `FileNotFoundError` when there is no store and `MissingSecret`, a `KeyError`, for a name that is absent or blank in it) and name the exact env var needed, but the decrypt step itself is the user's to trigger.
- **A stale venv is a common false failure.** If a script fails with `ModuleNotFoundError`, check `sys.prefix != sys.base_prefix` first (are we even in a venv?) before assuming the package install is broken.
- **`TRAVEL_SECRETS_DIR` is a per-shell environment variable** — if a script run in a NEW terminal/session can't find secrets that worked a moment ago, the variable likely isn't set in that shell.
- **The Playwright MCP check must happen live, per session** — a tool being available last time doesn't mean it's available now; a fresh session may not have it registered.

## Done when

- `setup_check.py` reports all green before `listing-search` or `place-matching` runs.
- A live `ToolSearch` check (not a memory of a past session) confirms Playwright MCP tools are callable, before the first browser-based search.
- No plaintext `secrets/.env` remains on disk once `encrypt_env.py` has run successfully — it deletes this
  itself after verifying the round-trip, so a lingering plaintext file means either an unfinished
  decrypt→edit→encrypt cycle or a failed encryption attempt, not a step someone forgot (`setup_check.py`
  flags it either way).
- Each project has its own `.venv` with this plugin's `requirements.txt` installed into it — not a venv
  shared with another trip project, and not the bare system Python.
- On `travel-agent`'s first run in a project, every credential in the walkthrough above was named explicitly
  (what it unlocks, rough cost, where to get it) — not just "see environment-setup." On any later run,
  `credential_status.py` was checked before prompting for anything, and a key it reports as already set was
  never re-prompted for.
