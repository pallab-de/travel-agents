#!/usr/bin/env python
"""Encrypt secrets/.env into secrets/.env.enc, then DELETE the plaintext secrets/.env on success.

Run this yourself, from the project's root folder, after creating/editing secrets/.env (by hand, or via
decrypt_env.py's "add a new credential" workflow — see that script and the environment-setup SKILL.md).
"<plugin root>" is the folder this plugin is installed or cloned into:
    <venv>/bin/python "<plugin root>/skills/environment-setup/scripts/encrypt_env.py"   (Linux/macOS)
    <venv>\\Scripts\\python "<plugin root>\\skills\\environment-setup\\scripts\\encrypt_env.py"   (Windows)

The secrets folder is ./secrets by default, or the folder named by TRAVEL_SECRETS_DIR if that is set.

No passphrase to remember: a random key is generated once into secrets/.env.key (gitignored) and reused on
later runs. Delete secrets/.env.key if you ever want to rotate it (you'll need to re-run this script
afterwards to produce a matching .env.enc).

The plaintext secrets/.env is deleted automatically the moment encryption succeeds — verified by immediately
decrypting .env.enc back and comparing it to what was just encrypted, not just assumed from a lack of
exception. If that round-trip check fails for any reason, the plaintext is deliberately left in place (with
an error explaining why) rather than risk deleting your only copy of a key you just typed in.

By convention, YOU run this script, on the plaintext secrets/.env — Claude should not be creating or editing
that plaintext file directly, and should not open it. Nothing here requires interactivity, so it isn't
technically blocked from running it either way, but the point of this whole scheme is that a secret only
ever exists in memory, briefly on disk while you're actively editing it, or in this encrypted form.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from secrets_util import secrets_dir, decrypt_bytes, encrypt_file, generate_key, load_key, write_key  # noqa: E402

SECRETS_DIR = secrets_dir()
TEMPLATE_PATH = Path(__file__).resolve().parent / '.env.template'
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
    if [x for x in sys.argv[1:] if x != '--replace']:
        print(f'unknown argument(s) {sys.argv[1:]}; the only option is --replace (nothing was done)')
        return 2
    if not ENV_PATH.exists():
        print(f"No .env found at {ENV_PATH}.")
        print(f"Copy {TEMPLATE_PATH} to {ENV_PATH} and fill in your keys first, or run "
              f"decrypt_env.py first if you're adding a key to an already-encrypted store.")
        return 1

    SECRETS_DIR.mkdir(exist_ok=True)
    if KEY_PATH.exists():
        print(f"Reusing existing {KEY_PATH.name}.")
    else:
        write_key(KEY_PATH, generate_key())
        print(f"Generated new {KEY_PATH.name}.")

    key = load_key(KEY_PATH)
    plaintext_before = ENV_PATH.read_bytes()
    if ENC_PATH.exists() and '--replace' not in sys.argv[1:]:
        from secrets_util import parse_env_bytes
        try:
            kept = set(parse_env_bytes(decrypt_bytes(ENC_PATH.read_bytes(), key)))
        except ValueError:
            print(f"REFUSED: {ENC_PATH.name} exists but cannot be decrypted with {KEY_PATH.name} (wrong key or corrupt file). "
                  f"Encrypting now would destroy it. Restore the right key, or pass --replace to overwrite on purpose.")
            return 1
        dropped = sorted(kept - set(parse_env_bytes(plaintext_before)))
        if dropped:
            print(f"REFUSED: the new {ENV_PATH.name} lacks {len(dropped)} key(s) the encrypted store holds ({', '.join(dropped)}). "
                  f"Run decrypt_env.py first so the plaintext starts from what is stored, or pass --replace to drop them on purpose.")
            return 1
    encrypt_file(ENV_PATH, ENC_PATH, key)

    # Round-trip verify before deleting the only plaintext copy - belt-and-braces against a corrupt write,
    # a wrong key, or a full disk truncating the .enc file.
    try:
        roundtrip = decrypt_bytes(ENC_PATH.read_bytes(), key)
    except ValueError as e:
        print(f"ERROR: wrote {ENC_PATH} but could not decrypt it back - {e}")
        print(f"{ENV_PATH.name} was NOT deleted. Do not proceed; re-run this script, or check disk space.")
        return 1
    if roundtrip != plaintext_before:
        print(f"ERROR: {ENC_PATH} did not decrypt back to what was just encrypted - refusing to delete "
              f"{ENV_PATH.name}. This should not happen; please report it rather than working around it.")
        return 1

    print(f"Wrote {ENC_PATH} ({ENC_PATH.stat().st_size} bytes), verified it decrypts back correctly.")
    ENV_PATH.unlink()
    print(f"Deleted plaintext {ENV_PATH.name} - nothing needs it on disk now; {ENC_PATH.name} + "
          f"{KEY_PATH.name} are the only copies of your keys from here on.")
    print(f"{KEY_PATH.name} and {ENC_PATH.name} should both be gitignored - verify with:")
    print(f"  git check-ignore -v {KEY_PATH} {ENC_PATH}")
    return 0


def selftest() -> int:
    import subprocess, tempfile
    me = str(Path(__file__).resolve())
    cases = []
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td) / 'secrets'; sd.mkdir()
        env = dict(os.environ, TRAVEL_SECRETS_DIR=str(sd))
        run = lambda *a: subprocess.run([sys.executable, me, *a], env=env, capture_output=True, text=True, cwd=td)
        (sd / '.env').write_text('A=1\nB=2\n', encoding='utf-8')
        cases.append(('a first encryption succeeds and deletes the plaintext', run().returncode == 0 and not (sd / '.env').exists()))
        (sd / '.env').write_text('A=1\n', encoding='utf-8')
        r = run()
        cases.append(('a smaller key set over an existing store is REFUSED', r.returncode == 1 and 'REFUSED' in r.stdout and (sd / '.env').exists()))
        cases.append(('--replace drops keys on purpose', run('--replace').returncode == 0))
        (sd / '.env').write_text('A=1\nC=3\n', encoding='utf-8')
        (sd / '.env.key').write_bytes(generate_key())
        r = run()
        cases.append(('an undecryptable store is never overwritten', r.returncode == 1 and 'cannot be decrypted' in r.stdout))
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(selftest() if sys.argv[1:] == ['selftest'] else main())
