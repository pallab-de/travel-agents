#!/usr/bin/env python
"""Decrypt secrets/.env.enc back into a plaintext secrets/.env, so you can add or change a key by hand.

This is the other half of encrypt_env.py's cycle. The normal flow when a new credential is needed (a new
provider key, or rotating an existing one) is:

    1. Run THIS script — it writes plaintext secrets/.env from the current secrets/.env.enc.
    2. Edit secrets/.env yourself, in your own editor, to add/change whatever key you need. (By convention
       Claude does not open or edit this plaintext file — see the environment-setup SKILL.md.)
    3. Run encrypt_env.py again — it re-encrypts with the SAME secrets/.env.key (so nothing that already
       reads from this store breaks), verifies the round-trip, then deletes the plaintext automatically.

The plaintext window between steps 1 and 3 is real but short and self-cleaning: nothing here leaves it lying
around waiting for you to remember to delete it — encrypt_env.py does that the moment it succeeds. If you
decrypt and then decide not to proceed, just delete secrets/.env yourself; secrets/.env.enc is untouched
either way (this script never writes to it, only reads it).

Run from the project's own folder ("<plugin root>" is the folder this plugin is installed or cloned into):
    <venv>/bin/python "<plugin root>/skills/environment-setup/scripts/decrypt_env.py"   (Linux/macOS)
    <venv>\\Scripts\\python "<plugin root>\\skills\\environment-setup\\scripts\\decrypt_env.py"   (Windows)

The secrets folder is ./secrets by default, or the folder named by TRAVEL_SECRETS_DIR if that is set.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from secrets_util import secrets_dir, env_line, write_private, load_secrets  # noqa: E402

SECRETS_DIR = secrets_dir()
ENV_PATH = SECRETS_DIR / '.env'
ENC_PATH = SECRETS_DIR / '.env.enc'
KEY_PATH = SECRETS_DIR / '.env.key'


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def main() -> int:
    if sys.argv[1:] in (['-h'], ['--help']):
        print(__doc__)
        return 0
    if [x for x in sys.argv[1:] if x != '--force']:
        print(f'unknown argument(s) {sys.argv[1:]}; the only option is --force (nothing was done)')
        return 2
    if not ENC_PATH.exists() or not KEY_PATH.exists():
        print(f"No encrypted secrets found at {SECRETS_DIR} ({ENC_PATH.name}/{KEY_PATH.name} missing).")
        print("Nothing to decrypt yet - if this is a first-time setup, copy .env.template to "
              f"{ENV_PATH} and fill it in directly, then run encrypt_env.py.")
        return 1

    # Fail-closed: a plaintext .env already on disk may hold edits that were never re-encrypted. Overwriting it
    # would silently destroy them, so the run stops unless the owner passes --force after checking that file.
    if ENV_PATH.exists() and '--force' not in sys.argv:
        print(f"REFUSED: {ENV_PATH} already exists. It may hold edits that encrypt_env.py has not saved yet. "
              f"Run encrypt_env.py first (it saves and removes the plaintext), or, if you are sure the file is "
              f"expendable, re-run this with --force.")
        return 1

    secrets = load_secrets(ENC_PATH, KEY_PATH)  # in memory only, not printed
    lines = [env_line(k, v) for k, v in secrets.items()]
    write_private(ENV_PATH, (chr(10).join(lines) + chr(10)).encode('utf-8'))   # as sensitive as the key: locked down before it is filled

    print(f"Wrote plaintext {ENV_PATH} with {len(secrets)} key(s) from {ENC_PATH.name}.")
    print("Edit it now to add/change whatever key you need, THEN run encrypt_env.py to re-encrypt and")
    print("automatically delete this plaintext copy again. Don't leave this window open longer than you")
    print("need to - it's a real plaintext credentials file for as long as it exists on disk.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
