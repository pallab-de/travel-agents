"""Encrypt/decrypt a plaintext .env into a .env.enc + .env.key pair, using the `cryptography` package's Fernet
(symmetric, authenticated encryption). This module and encrypt_env.py (which hands it the plaintext file it is encrypting) are the only code in this plugin
that reads a plaintext .env file. Every other script gets a secret only through `load_secrets()` here, in memory, never printed.

On disk only `.env.key` (44 bytes, never printed) and `.env.enc` (unreadable without the key) exist. Both stay out of
version control: add `secrets/` to the project's .gitignore (setup_check.py verifies it).
"""
from __future__ import annotations
import os
import re
import sys
from pathlib import Path

def _crypto():
    """(Fernet, InvalidToken), imported on first use so that an environment variable (cloud, CI) and setup_check.py work without the package."""
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError as e:
        raise ImportError(
            "This skill needs the 'cryptography' package: pip install cryptography (or `pip install -r requirements.txt` "
            "from the plugin root, which includes it)."
        ) from e
    return Fernet, InvalidToken


def secrets_dir() -> Path:
    """The folder holding the encrypted store: $TRAVEL_SECRETS_DIR if set, else ./secrets in the current project."""
    d = os.environ.get('TRAVEL_SECRETS_DIR')
    return Path(d) if d else Path.cwd() / 'secrets'


SYNC_MARKERS = (("OneDrive", "OneDrive"), ("Dropbox", "Dropbox"), ("iCloud", "iCloud Drive"), ("com~apple~CloudDocs", "iCloud Drive"),
                ("Mobile Documents", "iCloud Drive"), ("Google Drive", "Google Drive"), ("GoogleDrive", "Google Drive"), ("pCloud", "pCloud"),
                ("Nextcloud", "Nextcloud"), ("Box Sync", "Box"))


def synced_folder(path) -> str | None:
    """The cloud-sync service whose folder `path` sits in (OneDrive, Dropbox, iCloud Drive, Google Drive, ...), or None. Judged from the folder names
    in the path and from the sync roots the services announce in the environment. A hint, not a guarantee: a mapped or custom-named sync folder is not seen."""
    try:
        p = Path(path).expanduser().resolve()
    except OSError:
        p = Path(path)
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        root = os.environ.get(var)
        if root:
            try:
                p.relative_to(Path(root).resolve())
                return "OneDrive"
            except (ValueError, OSError):
                pass
    for part in p.parts:
        for marker, name in SYNC_MARKERS:
            if part == marker or part.startswith(marker + " -") or part.startswith(marker + "-"):
                return name
    return None


def recommended_dir() -> Path:
    """A per-user folder that no sync service watches: %LOCALAPPDATA%\\travel-agents\\secrets (Windows), ~/Library/Application Support/travel-agents/secrets
    (macOS), $XDG_CONFIG_HOME or ~/.config /travel-agents/secrets (Linux)."""
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "travel-agents" / "secrets"


def generate_key() -> bytes:
    return _crypto()[0].generate_key()


def _restrict_permissions(path: Path) -> None:
    """Make the key/enc file readable only by the current user. On Windows, chmod cannot do this, so use icacls to
    remove inherited ACLs and grant only the current user. Raises if the restriction cannot be applied."""
    import os, subprocess, sys
    if sys.platform.startswith('win'):
        user = os.environ.get('USERNAME')
        if not user:
            raise OSError('cannot restrict the key file: USERNAME is not set')
        subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', f'{user}:F'],
                       check=True, capture_output=True)
    else:
        path.chmod(0o600)


def write_private(path: Path, data: bytes) -> None:
    """Write `data` to a file only the current user can read, restricting the file BEFORE the secret goes into it: on
    POSIX it is created 0600; on Windows it is created empty, locked down with icacls, then filled."""
    import os, sys
    path = Path(path)
    if sys.platform.startswith('win'):
        path.write_bytes(b'')
        _restrict_permissions(path)
        with open(path, 'r+b') as f:
            f.write(data)
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)   # the mode applies at creation only: lock an existing, looser file down before writing
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        path.chmod(0o600)


def env_line(key: str, value: str) -> str:
    """KEY=value as parse_env_bytes will read it back unchanged: a value with spaces, a # or quotes is quoted."""
    if value and not re.search(r"[\s#\"']", value):
        return f"{key}={value}"
    if '"' not in value:
        return f'{key}="{value}"'
    if "'" not in value:
        return f"{key}='{value}'"
    return f"{key}={value}"


def write_key(key_path: Path, key: bytes) -> None:
    write_private(key_path, key)


def load_key(key_path: Path) -> bytes:
    return key_path.read_bytes().strip()


def encrypt_file(src_path: Path, dest_path: Path, key: bytes) -> None:
    """Encrypt src_path (plaintext .env) into dest_path (a Fernet token)."""
    plaintext = src_path.read_bytes()
    token = _crypto()[0](key).encrypt(plaintext)
    write_private(dest_path, token)


def decrypt_bytes(blob: bytes, key: bytes) -> bytes:
    """Decrypt a Fernet token. Raises ValueError on a wrong or corrupt key/data."""
    try:
        Fernet, InvalidToken = _crypto()
        return Fernet(key).decrypt(blob)
    except InvalidToken as exc:
        raise ValueError("Decryption failed: wrong key, or the .env.enc file is corrupt.") from exc


def parse_env_bytes(raw: bytes) -> dict[str, str]:
    """Parse KEY=VALUE lines (skipping comments/blank lines) into a dict."""
    result: dict[str, str] = {}
    for line in raw.decode('utf-8-sig').splitlines():   # a BOM (PowerShell 5.1 writes one) is not part of the first key
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = re.sub(r'^export\s+', '', key.strip())   # `export KEY=value` is the key KEY
        value = value.strip()
        quoted = re.fullmatch(r"""(["'])(.*?)\1\s*(?:#.*)?""", value)   # "value" or 'value', with an optional trailing comment
        if quoted:
            value = quoted.group(2)
        else:
            value = re.split(r'\s+#', value, maxsplit=1)[0].rstrip()   # an unquoted ' # comment' is not part of the key
        result[key] = value
    return result


def load_secrets(enc_path: Path, key_path: Path) -> dict[str, str]:
    """Decrypt enc_path using key_path and return the parsed secrets dict, in memory only.
    Caller must not print this dict or any value from it."""
    key = load_key(key_path)
    blob = enc_path.read_bytes()
    plaintext = decrypt_bytes(blob, key)
    return parse_env_bytes(plaintext)


def selftest() -> int:
    key = generate_key()
    cases = [('quotes and inline comments are not part of a value', parse_env_bytes(b'K="v"\nJ=w # c\nL=\'x y\'\nM=a#b') == {'K': 'v', 'J': 'w', 'L': 'x y', 'M': 'a#b'}),
             ('a blank value stays blank', parse_env_bytes(b'K=\n') == {'K': ''}),
             ('a round trip returns what went in', decrypt_bytes(_crypto()[0](key).encrypt(b'A=1'), key) == b'A=1')]
    try:
        decrypt_bytes(b'not a token', key)
        cases.append(('a corrupt store raises', False))
    except ValueError:
        cases.append(('a corrupt store raises', True))
    cases.append(('a quoted value with a trailing comment loses the quotes; `export KEY=...` is KEY', parse_env_bytes(b'A="x y" # note\nexport B=2') == {'A': 'x y', 'B': '2'}))
    import tempfile
    tmp = Path(tempfile.gettempdir())
    cases.append(('a folder inside OneDrive / Dropbox / iCloud / Google Drive is called synced; a plain local folder is not',
                  synced_folder(tmp / 'OneDrive' / 'Documents' / 'Trip' / 'secrets') == 'OneDrive' and synced_folder(tmp / 'Dropbox' / 'trip') == 'Dropbox'
                  and synced_folder(tmp / 'Mobile Documents' / 'com~apple~CloudDocs' / 't') == 'iCloud Drive' and synced_folder(tmp / '.config' / 'travel-agents' / 'secrets') is None))
    cases.append(('the recommended folder is per-user and not in a sync folder', synced_folder(recommended_dir()) is None and recommended_dir().name == 'secrets'))
    cases.append(('a leading BOM is not part of the first key', parse_env_bytes(b'\xef\xbb\xbfA=1\nB=2') == {'A': '1', 'B': '2'}))
    vals = {'A': 'plain', 'B': 'has space', 'C': 'x # y', 'D': 'q"uote'}
    cases.append(('values with spaces, # and quotes survive a write and a read', parse_env_bytes(chr(10).join(env_line(k, v) for k, v in vals.items()).encode()) == vals))
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / 'k'
        write_private(f, b'secret')
        mode_ok = True
        if not sys.platform.startswith('win'):
            mode_ok = (f.stat().st_mode & 0o077) == 0
        cases.append(('a private file holds the data and is not readable by others', f.read_bytes() == b'secret' and mode_ok))
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


if __name__ == '__main__':
    import sys
    raise SystemExit(selftest() if sys.argv[1:] == ['selftest'] else 0)
