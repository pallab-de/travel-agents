#!/usr/bin/env python
"""Last-line leak tripwire. Fails (exit 1) if anything about to be committed or published carries a secret or a personal detail.

  python tools/privacy_scan.py                 scan every file git tracks (what a push publishes)
  python tools/privacy_scan.py --staged        scan only what is staged for the next commit
  python tools/privacy_scan.py --history       scan every commit's added lines too (what a push publishes, including old commits)
  python tools/privacy_scan.py --install-hooks write .git/hooks/pre-commit and pre-push that run this scan

What it looks for, and never prints (a hit shows the file, line and kind, with the value masked):
  1. The REAL values in your encrypted secret stores (.env.enc + .env.key): the folders in --secrets-dir, $TRAVEL_SECRETS_DIR, ./secrets, and
     every sibling project folder of the repo that holds a store. Decrypted in memory only, to be compared, never shown.
  2. Key shapes: Google (AIza...), OpenAI-style (sk-...), GitHub (ghp_/gho_/github_pat_), Apify, AWS, Slack, private-key blocks, Fernet keys.
  3. Your private terms: one per line in .git/privacy-terms.txt (inside .git, so it is never tracked or pushed) or $PRIVACY_TERMS_FILE or --terms.
     Put your email, phone number, home address, full name and anything else that must never appear. Case-insensitive.
  4. Absolute user paths (C:\\Users\\name, /Users/name, /home/name) and e-mail addresses other than no-reply / example ones.

  privacy_scan.py selftest
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "environment-setup" / "scripts"))

SHAPES = [
    ("google-api-key", re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    ("openai-style-key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}")),
    ("apify-token", re.compile(r"\bapify_api_[A-Za-z0-9]{10,}")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("fernet-key", re.compile(r"(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-]{43}=(?![A-Za-z0-9=])")),
]
EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
OK_EMAIL = re.compile(r"noreply|no-reply|\.example$|\.test$|\.invalid$|@example\.|^git@|@users\.noreply\.github\.com$", re.I)
USER_PATH = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"'<>|]+|/Users/[a-z][^/\s\"']*|/home/[a-z][^/\s\"']*")
OK_USER = re.compile(r"<[^>]+>|\bname\b|\buser(name)?\b|\byou\b|\bme\b|\bsomeone\b|\bexample\b|\bxxx\b|\{[^}]*\}|%[^%]*%|\$", re.I)


def git(*args, cwd=None):
    r = subprocess.run(["git", *args], capture_output=True, cwd=cwd or ROOT)
    return r.stdout.decode("utf-8", errors="replace")


def mask(s):
    return (s[:2] + "***") if len(s) > 4 else "***"


def secret_values(dirs):
    """-> {value: where} from every encrypted store found, in memory only."""
    try:
        from secrets_util import load_secrets
    except Exception:
        return {}
    cands = [Path(d) for d in dirs]
    if os.environ.get("TRAVEL_SECRETS_DIR"):
        cands.append(Path(os.environ["TRAVEL_SECRETS_DIR"]))
    cands += [Path.cwd() / "secrets", ROOT / "secrets"]
    parent = ROOT.parent
    cands += [p for p in parent.iterdir() if p.is_dir()] if parent.exists() else []
    cands += [p / "secrets" for p in list(parent.iterdir())[:200] if p.is_dir()] if parent.exists() else []
    out, seen = {}, set()
    for d in cands:
        enc, key = d / ".env.enc", d / ".env.key"
        if (str(d) in seen) or not (enc.exists() and key.exists()):
            continue
        seen.add(str(d))
        try:
            for name, v in load_secrets(enc, key).items():
                v = str(v).strip()
                if len(v) >= 8 and not v.isdigit():
                    out[v] = f"{name} (store {d.name})"
        except Exception:
            continue
        try:   # the key file itself is a secret too
            kv = key.read_text(encoding="utf-8", errors="ignore").strip()
            if len(kv) >= 20:
                out[kv] = f"key file ({d.name})"
        except Exception:
            pass
    return out


def private_terms(extra_file):
    files = [Path(p) for p in (extra_file, os.environ.get("PRIVACY_TERMS_FILE")) if p]
    gd = git("rev-parse", "--git-dir").strip()
    if gd:
        files.append((ROOT / gd) / "privacy-terms.txt" if not Path(gd).is_absolute() else Path(gd) / "privacy-terms.txt")
    terms = []
    for f in files:
        if f.exists():
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                t = line.strip()
                if t and not t.startswith("#") and len(t) >= 4:
                    terms.append(t.lower())
    return terms


def scan_text(path, text, secrets, terms, owner_ok):
    hits = []
    low = text.lower()
    for v, where in secrets.items():
        if v in text:
            hits.append((path, text[:text.index(v)].count("\n") + 1, "REAL SECRET VALUE", where))
    for t in terms:
        i = low.find(t)
        if i >= 0:
            hits.append((path, text[:i].count("\n") + 1, "private term", mask(t)))
    for kind, rx in SHAPES:
        for m in rx.finditer(text):
            s = m.group(0)
            if kind == "fernet-key" and len(set(s)) < 12:
                continue
            hits.append((path, text[:m.start()].count("\n") + 1, kind, mask(s)))
    for m in EMAIL.finditer(text):
        e = m.group(0)
        if OK_EMAIL.search(e) or e.lower() in owner_ok or e.lower().endswith((".png", ".jpg", ".svg", ".js", ".py")):
            continue
        hits.append((path, text[:m.start()].count("\n") + 1, "e-mail address", mask(e)))
    for m in USER_PATH.finditer(text):
        tail = m.group(0)
        if OK_USER.search(tail):
            continue
        hits.append((path, text[:m.start()].count("\n") + 1, "absolute user path", mask(tail)))
    return hits


def files_to_scan(mode):
    if mode == "staged":
        names = [n for n in git("diff", "--cached", "--name-only", "--diff-filter=ACM").splitlines() if n]
        return [(n, git("show", f":{n}")) for n in names]
    out = []
    for n in [n for n in git("ls-files").splitlines() if n]:
        p = ROOT / n
        try:
            if p.is_file() and p.stat().st_size < 30_000_000:
                out.append((n, p.read_bytes().decode("utf-8", errors="ignore")))
        except OSError:
            continue
    return out


def history_texts():
    """Added lines of every commit, as one text per commit."""
    revs = git("rev-list", "--all").split()
    for r in revs:
        yield f"history:{r[:8]}", "\n".join(l[1:] for l in git("show", "--format=", "-U0", r).splitlines() if l.startswith("+") and not l.startswith("+++"))


def hook_body(arg):
    return '#!/bin/sh\n# installed by tools/privacy_scan.py --install-hooks\nexec python "$(git rev-parse --show-toplevel)/tools/privacy_scan.py" ' + arg + '\n'


def install_hooks():
    hooks = ROOT / git("rev-parse", "--git-path", "hooks").strip() if not Path(git("rev-parse", "--git-path", "hooks").strip()).is_absolute() else Path(git("rev-parse", "--git-path", "hooks").strip())
    hooks.mkdir(parents=True, exist_ok=True)
    for name, arg in (("pre-commit", "--staged"), ("pre-push", "--history")):
        p = hooks / name
        p.write_text(hook_body(arg), encoding="utf-8", newline="\n")
        try:
            p.chmod(0o755)
        except OSError:
            pass
        print(f"installed {p}")
    return 0


def run(a):
    secrets = secret_values(a.secrets_dir)
    terms = private_terms(a.terms)
    owner_ok = set()
    items = list(files_to_scan("staged" if a.staged else "tracked"))
    if a.history:
        items += list(history_texts())
    hits = []
    for path, text in items:
        hits += scan_text(path, text, secrets, terms, owner_ok)
    print(f"privacy_scan: {len(items)} texts, {len(secrets)} stored secret values and {len(terms)} private terms checked")
    if not terms:
        print("  note: no private terms configured; put your e-mail, phone, home address and full name, one per line, in .git/privacy-terms.txt")
    for path, line, kind, what in hits[:200]:
        print(f"  LEAK {path}:{line} [{kind}] {what}")
    print("privacy_scan: CLEAN" if not hits else f"privacy_scan: {len(hits)} LEAK(S) - do not commit or push")
    return 1 if hits else 0


def selftest():
    secrets = {"s3cr3t-VALUE-xyz": "TEST_KEY (store t)"}
    at, bs = "@", chr(92)   # built from parts so this file never contains a sample that the scan itself would flag
    terms = ["jane.roe" + at + "mail.test.org", "12 secret lane"]
    cases = []
    bad = ("k = '" + "AI" + "zaSyA-1234567890abcdefghijklmnopqrstuv'\nx = s3cr3t-VALUE-xyz\ncontact jane.roe" + at + "mail.test.org\nC:" + bs + "Users" + bs + "jroe" + bs + "Documents\n"
           "lives at 12 Secret Lane\nowner" + at + "gmail.com")
    kinds = {h[2] for h in scan_text("f", bad, secrets, terms, set())}
    cases.append(("a real secret, a key shape, a private term, a user path and a stray e-mail are all caught",
                  {"REAL SECRET VALUE", "google-api-key", "private term", "absolute user path", "e-mail address"} <= kinds))
    ok = ("contact me" + at + "example.com\n125727277+pallab-de" + at + "users.noreply.github.com\npath C:" + bs + "Users" + bs + "<name>" + bs + "Documents\n/home/you/x\nsha256 " + "ab" * 32)
    cases.append(("placeholders, no-reply addresses and hashes are not leaks", not scan_text("f", ok, secrets, terms, set())))
    masked = " ".join(str(x) for h in scan_text("f", bad, secrets, terms, set()) for x in h)
    cases.append(("a hit never prints the value", "s3cr3t-VALUE" not in masked and "jane.roe" not in masked and "AIzaSyA-1234" not in masked))
    failed = 0
    for name, passed in cases:
        print(("PASS " if passed else "FAIL ") + name)
        failed += not passed
    print("SELFTEST PASS (%d cases)" % len(cases) if not failed else "SELFTEST FAIL")
    return 1 if failed else 0


def main():
    if sys.argv[1:] == ["selftest"]:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--staged", action="store_true"); ap.add_argument("--history", action="store_true")
    ap.add_argument("--install-hooks", action="store_true"); ap.add_argument("--terms"); ap.add_argument("--secrets-dir", action="append", default=[])
    a = ap.parse_args()
    return install_hooks() if a.install_hooks else run(a)


if __name__ == "__main__":
    sys.exit(main())
