#!/usr/bin/env python
"""Run every offline check in the plugin: each script's `selftest`, the calibration fixtures' integrity, the agent
frontmatter check, and a compile of every .py file. No network, no key.

  python tools/run_selftests.py          exit 1 if anything fails

A script is included when its source defines a top-level function `selftest`; the standard idiom is `python <script> selftest`.
"""
import re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(args):
    r = subprocess.run([sys.executable, "-B", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT)
    lines = [l for l in (r.stdout + r.stderr).strip().splitlines() if l.strip()]
    return r.returncode, (lines[-1] if lines else "(no output)")


def main():
    if sys.argv[1:] in (['-h'], ['--help']):
        print(__doc__)
        return 0
    scripts = sorted(p for d in ("skills", "tools") for p in (ROOT / d).rglob("*.py")
                     if p != Path(__file__).resolve() and re.search(r"^def selftest\(", p.read_text(encoding="utf-8", errors="replace"), re.M))
    bad = 0
    for p in scripts:
        rc, last = run([str(p), "selftest"])
        ok = rc == 0 and "FAIL" not in last
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {p.relative_to(ROOT).as_posix():<70} {last}")
    for domain in ("stay", "itinerary"):
        rc, last = run([str(ROOT / "skills/claim-review/scripts/calibrate.py"), "verify", "--domain", domain])
        bad += rc != 0
        print(f"{'ok  ' if rc == 0 else 'FAIL'} calibration fixture {domain:<49} {last}")
    rc, last = run([str(ROOT / "tools/check_agents.py")])
    bad += rc != 0
    print(f"{'ok  ' if rc == 0 else 'FAIL'} agents {'':<62} {last}")
    for p in sorted(ROOT.rglob("*.py")):
        if ".git" in p.parts:
            continue
        try:
            compile(p.read_text(encoding="utf-8"), str(p), "exec")   # in memory: writes nothing into the repo
        except (SyntaxError, ValueError, OSError) as e:
            bad += 1
            print(f"FAIL compile {p.relative_to(ROOT).as_posix()}: {e}")
    print(f"\n{len(scripts)} selftests run; " + ("ALL PASS" if not bad else f"{bad} FAILED"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
