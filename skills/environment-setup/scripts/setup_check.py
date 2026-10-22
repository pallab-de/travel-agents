#!/usr/bin/env python
"""Checks (and reports, never silently assumes) the state of: a venv with this plugin's requirements
installed, and an encrypted secrets store. Does NOT check Playwright MCP (that's a live, in-session check —
see the environment-setup SKILL.md). Safe to run repeatedly; never reads or prints a secret value.

Run from the project's own folder:
    python <path-to-this-plugin>/skills/environment-setup/scripts/setup_check.py
"""
import importlib.util, os, sys
from pathlib import Path

PROJECT_ROOT = Path.cwd()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from secrets_util import secrets_dir  # noqa: E402

_override = os.environ.get('TRAVEL_SECRETS_DIR')
SECRETS_DIR = secrets_dir()


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def secrets_ignored():
    """Ask git itself whether the secrets folder is ignored, wherever it is (so '!secrets/' or 'foo/secrets/' don't pass);
    outside any git work tree there is nothing to leak through git, and for the default folder a .gitignore line is read."""
    import subprocess
    where = SECRETS_DIR if SECRETS_DIR.exists() else SECRETS_DIR.parent
    try:
        inside = subprocess.run(['git', 'rev-parse', '--is-inside-work-tree'], cwd=where, capture_output=True, text=True)
    except OSError:
        inside = None
    if inside is not None and inside.returncode == 0:
        r = subprocess.run(['git', 'check-ignore', '-q', str(SECRETS_DIR / '.env.enc')], cwd=where)
        return r.returncode == 0
    if _override:
        return True   # not inside a git work tree: nothing here can be committed
    gi = PROJECT_ROOT / '.gitignore'
    lines = [l.strip() for l in gi.read_text(encoding='utf-8').splitlines()] if gi.exists() else []
    return any(l in ('secrets/', '/secrets/', 'secrets') for l in lines)


def check(label, ok, detail=''):
    print(('  [ok]  ' if ok else '  [ ]   ') + label + (('  -- ' + detail) if detail and not ok else ''))
    return ok


def main():
    if sys.argv[1:] in (['-h'], ['--help']):
        print(__doc__)
        return 0
    print(f"Checking environment (shared by every travel-agents skill) in: {PROJECT_ROOT}\n")
    all_ok = True

    in_venv = sys.prefix != sys.base_prefix
    all_ok &= check('Running inside a virtual environment', in_venv,
                     'Not in a venv. Create one: python -m venv .venv, then activate it and re-run this check.')

    for pkg in ('PIL', 'cryptography'):
        spec = importlib.util.find_spec(pkg)
        all_ok &= check(f'Package importable: {pkg}', spec is not None,
                         'Run: pip install -r requirements.txt (from the plugin root)')

    enc, key = SECRETS_DIR / '.env.enc', SECRETS_DIR / '.env.key'
    has_secrets = enc.exists() and key.exists()
    label = 'Encrypted secrets present (.env.enc + .env.key)' + (f' [via override: {SECRETS_DIR}]' if _override else ' (secrets/)')
    all_ok &= check(label, has_secrets,
                     f'Not found at {SECRETS_DIR}. Copy the .env.template next to this script to '
                     f'{SECRETS_DIR / ".env"}, fill in your keys, then run encrypt_env.py. '
                     f'Or set TRAVEL_SECRETS_DIR to an existing encrypted-env folder from another project.')

    plaintext = SECRETS_DIR / '.env'
    if plaintext.exists():
        all_ok = False
        print('  [!]   .env (plaintext) exists on disk at the secrets dir above. encrypt_env.py deletes this '
              'automatically on success, so its presence usually means either: an in-progress decrypt_env.py -> '
              'edit -> encrypt_env.py cycle that was not finished (run encrypt_env.py to complete it), or an '
              'encryption attempt that failed partway (re-run encrypt_env.py and read its output).')

    if _override:
        print(f'  [i]   Using TRAVEL_SECRETS_DIR override ({SECRETS_DIR}).')
    all_ok &= check('the secrets folder is gitignored (or outside any git repo)', secrets_ignored(),
                    'Add the secrets folder to .gitignore before committing anything.')

    print('\n' + ('Everything checked out.' if all_ok else 'Fix the items above, then re-run this check.'))
    return 0 if all_ok else 1


def selftest():
    import subprocess, tempfile
    me = str(Path(__file__).resolve())
    cases = []
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td) / 'secrets'; sd.mkdir()
        (sd / '.env').write_text('K=1', encoding='utf-8')
        (Path(td) / '.gitignore').write_text('# nothing', encoding='utf-8')
        env = {k: v for k, v in os.environ.items() if k != 'TRAVEL_SECRETS_DIR'}
        r = subprocess.run([sys.executable, me], env=env, capture_output=True, text=True, cwd=td)
        cases.append(('a plaintext .env and an unignored secrets/ exit 1', r.returncode == 1 and 'Everything checked out' not in r.stdout))
        (Path(td) / '.gitignore').write_text('!secrets/', encoding='utf-8')
        cases.append(("'!secrets/' is not an ignore rule", subprocess.run([sys.executable, '-c', 'import sys; sys.path.insert(0, %r); import setup_check as s; print(s.secrets_ignored())' % str(Path(me).parent)],
                      cwd=td, env=env, capture_output=True, text=True).stdout.strip() == 'False'))
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(selftest() if sys.argv[1:] == ['selftest'] else main())
