#!/usr/bin/env python
"""Reports which named credentials are present in the encrypted secrets store, by NAME only - never reads,
decrypts and prints a value, just whether each known key exists and is non-blank. Lets the first-run
walkthrough (environment-setup SKILL.md, "First-run credential walkthrough") skip a key that's
already set on a later run, instead of re-prompting for it every time.

Run from the project's own folder (same convention as setup_check.py):
    python <plugin root>/skills/environment-setup/scripts/credential_status.py [--verify]
--verify also proves SERPAPI_API_KEY works, using SerpApi's free account endpoint (no search is spent).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from travel_secrets import secrets_dir  # noqa: E402

# Every credential this plugin's SCRIPTS can use, by key name, with what it unlocks and whether it's a
# plugin-managed encrypted secret or something else entirely (an MCP server's own account, no plugin-held key).
KNOWN_CREDENTIALS = [
    ("GOOGLE_MAPS_API_KEY", "Places, Distance Matrix, Geocoding - drive/walk/transit times, place matching, landmark distances. Required for almost everything."),
    ("APIFY_TOKEN", "Licensed Google Maps review provider, for a guest score's reviews beyond the ~10 the free Places API returns per place. Optional, paid per call."),
    ("SERPAPI_API_KEY", "Flight fares and schedules (Google Flights) for transport-options' air legs, and 20+ Google Maps reviews per place for the guest score. Optional; free monthly allowance, then paid per search."),
    ("OPENROUTESERVICE_API_KEY", "An alternative to Google's Distance Matrix for venue-verification's distance checks. Optional, free tier available."),
]
# Not plugin-managed secrets at all - a separate account/registration, no .env.enc entry. Listed here so the
# walkthrough still MENTIONS them (every credential the plugin can use, not just the encrypted ones).
NOT_PLUGIN_MANAGED = [
    ("flightclaw MCP", "Flight search, date-grid price scans and price tracking - the second source for a flight's time and fare. Registered with 'claude mcp add' (environment-setup SKILL.md, 'flightclaw'), not an encrypted secret."),
    ("seats.aero API key", "Real miles/award-seat search across ~20-24 programmes. The traveller's own account and key (seats.aero Partner API) - not something this plugin creates or holds on their behalf."),
]


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


class StoreUnreadable(Exception):
    pass


def present_names():
    """Which plugin-managed credential NAMES currently resolve to a non-blank value. Returns an empty set,
    never an exception, if no encrypted store exists yet - that's just "nothing is set", not an error here."""
    d = secrets_dir()
    enc, key = d / ".env.enc", d / ".env.key"
    from_env = {name for name, _ in KNOWN_CREDENTIALS if (os.environ.get(name) or "").strip()}   # an environment variable of the same name counts (cloud, CI)
    if not (enc.exists() and key.exists()):
        return from_env
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from secrets_util import load_secrets
    try:
        values = load_secrets(enc, key)
    except Exception as e:   # a store that is there but unreadable is NOT an empty store
        raise StoreUnreadable(type(e).__name__) from e
    return from_env | {k for k, v in values.items() if str(v or "").strip()}


def flightclaw_status():
    """Whether Claude Code has a flightclaw MCP server registered: 'registered', 'not registered', or 'unknown'
    (the claude CLI isn't on PATH from here). Reads only the server's registration, nothing secret."""
    import shutil, subprocess
    exe = shutil.which("claude")
    if not exe:
        return "unknown (claude CLI not on PATH; check with: claude mcp get flightclaw)"
    try:
        r = subprocess.run([exe, "mcp", "get", "flightclaw"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown (claude mcp get flightclaw did not answer)"
    return "registered" if r.returncode == 0 and "flightclaw" in r.stdout else "not registered"


def verify_serpapi():
    """Prove SERPAPI_API_KEY works without spending a search (SerpApi's account endpoint is free). Prints the plan
    and searches left, never the key."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from secrets_util import load_secrets
    import serpapi
    d = secrets_dir()
    try:
        acct = serpapi.account(load_secrets(d / ".env.enc", d / ".env.key")["SERPAPI_API_KEY"])
    except Exception as e:   # SerpApiError carries no key; anything else is reported by type only
        return f"FAILED ({e if isinstance(e, serpapi.SerpApiError) else type(e).__name__})"
    return (f"works - plan {acct.get('plan_name', '?')}, {acct.get('total_searches_left', '?')} searches left "
            f"this month")


def main():
    if sys.argv[1:] in (['-h'], ['--help']):
        print(__doc__)
        return 0
    verify = "--verify" in sys.argv[1:]
    try:
        present = present_names()
    except StoreUnreadable as e:
        print(f"The encrypted store is present but cannot be decrypted ({e}): wrong .env.key, a corrupt .env.enc, or the "
              f"'cryptography' package is missing. Do NOT run encrypt_env.py over it. Restore the matching key, or ask the user.")
        return 1
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from secrets_util import synced_folder, recommended_dir
    where = synced_folder(secrets_dir())
    if where and (secrets_dir() / ".env.enc").exists():
        print(f"WARNING: the secrets folder {secrets_dir()} is inside {where}. The key file sits beside the encrypted file, so {where} now holds everything "
              f"needed to decrypt your keys. Ask the user whether to move it to a local-only folder such as {recommended_dir()} "
              f"(set TRAVEL_SECRETS_DIR to it), then rotate the keys that were stored here.")
    print("Plugin-managed credentials (encrypted secrets store, or an environment variable of the same name):")
    for name, what in KNOWN_CREDENTIALS:
        mark = "[ok]" if name in present else "[  ]"
        print(f"  {mark} {name:<28} {what}")
        if verify and name == "SERPAPI_API_KEY" and name in present:
            print(f"       verify: {verify_serpapi()}")
    print("\nNot plugin-managed (separate account/registration, no encrypted entry here):")
    for name, what in NOT_PLUGIN_MANAGED:
        state = f" [{flightclaw_status()}]" if name.startswith("flightclaw") else ""
        print(f"  [--] {name:<28} {what}{state}")
    missing = [n for n, _ in KNOWN_CREDENTIALS if n not in present]
    if missing:
        print(f"\n{len(missing)} plugin-managed credential(s) not set: {', '.join(missing)}")
        print("A trip that doesn't need one of these still works - the skill that needs it names the exact "
              "env var and degrades gracefully, it doesn't hard-fail the whole run.")
    else:
        print("\nEvery known plugin-managed credential is set.")
    return 0


def selftest():
    import os, subprocess, tempfile
    from secrets_util import generate_key
    me = str(Path(__file__).resolve())
    cases = []
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td) / "secrets"; sd.mkdir()
        env = dict(os.environ, TRAVEL_SECRETS_DIR=str(sd))
        run = lambda: subprocess.run([sys.executable, me], env=env, capture_output=True, text=True, cwd=td)
        r = run()
        cases.append(("no store at all reports everything missing, exit 0", r.returncode == 0 and "not set" in r.stdout))
        (sd / ".env.key").write_bytes(generate_key()); (sd / ".env.enc").write_bytes(b"corrupt")
        r = run()
        cases.append(("a corrupt store exits 1 and says it cannot be decrypted", r.returncode == 1 and "cannot be decrypted" in r.stdout))
    for name, ok in cases:
        print(("PASS " if ok else "FAIL ") + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest() if sys.argv[1:] == ["selftest"] else main())
