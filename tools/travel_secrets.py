"""Shared credential loader for every agent's and skill's scripts. Never reads a plaintext .env and never
prints a secret.

Every API key (Google Maps, OpenRouteService, a review-scraping provider, ...) lives in ONE encrypted env:
a `.env.enc` + `.env.key` pair, created by the `environment-setup` skill. Default location: a `secrets/`
folder in the CURRENT project (the folder you're running the search from). Override with the environment
variable TRAVEL_SECRETS_DIR to point at a different folder instead -- for example an encrypted-env folder
you already maintain for another project, so every project on your machine can share one Google Maps key
instead of encrypting a fresh one each time.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills' / 'environment-setup' / 'scripts'))
from secrets_util import load_secrets, secrets_dir  # noqa: E402,F401  (secrets_dir is re-exported for callers)

class MissingSecret(KeyError):
    """The named secret is absent from the store, or present but blank (the template's default)."""
    def __str__(self):
        return self.args[0] if self.args else 'missing secret'


def load_key(name: str) -> str:
    """Load one named secret (e.g. 'GOOGLE_MAPS_API_KEY'). An environment variable of that name wins when it is set and not blank (how cloud
    sessions, CI and containers hand a plugin its keys, on any operating system); otherwise it comes from the encrypted env. Never print the result."""
    env = os.environ.get(name)
    if env and env.strip():
        return env.strip()
    d = secrets_dir()
    enc, key = d / '.env.enc', d / '.env.key'
    if not enc.exists() or not key.exists():
        raise FileNotFoundError(
            f"No encrypted secrets found at {d}. Run the environment-setup skill first "
            f"(python <plugin root>/skills/environment-setup/scripts/setup_check.py), or set TRAVEL_SECRETS_DIR "
            f"to an existing encrypted-env folder from another project.")
    value = load_secrets(enc, key).get(name)
    if value is None or not str(value).strip():
        raise MissingSecret(f"{name} is not set in the encrypted store at {d} (absent or blank): add it with decrypt_env.py, "
                            f"edit, then encrypt_env.py")
    return value
