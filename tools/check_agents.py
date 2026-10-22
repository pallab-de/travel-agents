#!/usr/bin/env python
"""Cross-check each agent's frontmatter skill list against the skills its body names (a development check, run before a
pull request).

  python tools/check_agents.py          exit 1 if an agent's body names a skill its frontmatter doesn't list, lists a skill
                                        that doesn't exist, or lists one twice
  python tools/check_agents.py selftest

A skill an agent's steps depend on must be preloaded, or the agent runs that stage from memory. Likewise an agent whose
steps name Playwright or flightclaw must grant that MCP server in `tools:` (a `tools:` list excludes every MCP tool not
named in it).
"""
import re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def frontmatter_skills(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return [], text
    block = m.group(1)
    skills, grab = [], False
    for line in block.splitlines():
        if re.match(r"skills:\s*$", line):
            grab = True
        elif grab and line.strip().startswith("- "):
            skills.append(line.strip()[2:].strip())
        elif grab and not line.startswith(" "):
            grab = False
    return skills, text[m.end():]


def check(agent_text, known):
    skills, body = frontmatter_skills(agent_text)
    problems = [f"lists {s!r}, which is not a skill" for s in skills if s not in known]
    problems += [f"lists {s!r} twice" for s in sorted({s for s in skills if skills.count(s) > 1})]
    named = {n for n in re.findall(r"`([a-z][a-z-]+)`", body) if n in known}
    problems += [f"names `{n}` in its steps but doesn't list it in its frontmatter" for n in sorted(named - set(skills))]
    tools = next((l for l in agent_text.split("---")[1].splitlines() if l.startswith("tools:")), "") if agent_text.startswith("---") else ""
    if tools:
        for needle, grant in (("playwright", "mcp__playwright"), ("flightclaw", "mcp__flightclaw")):
            if needle in body.lower() and grant not in tools:
                problems.append(f"names {needle} in its steps but `tools:` doesn't grant {grant}")
    return problems


def main():
    if sys.argv[1:] in (['-h'], ['--help']):
        print(__doc__)
        return 0
    known = {p.name for p in (ROOT / "skills").iterdir() if (p / "SKILL.md").exists()}
    bad = 0
    for f in sorted((ROOT / "agents").glob("*.md")):
        for pr in check(f.read_text(encoding="utf-8"), known):
            print(f"{f.name}: {pr}")
            bad += 1
    print("agents OK" if not bad else f"{bad} problem(s)")
    return 1 if bad else 0


def selftest():
    known = {"fact-guard", "report-builder"}
    ok_text = "---\nname: a\nskills:\n  - fact-guard\n---\nRun `fact-guard`.\n"
    cases = [("a consistent agent passes", check(ok_text, known) == []),
             ("a skill named in the body but not listed fails", bool(check(ok_text.replace("  - fact-guard\n", "  - report-builder\n"), known))),
             ("a listed skill that doesn't exist fails", bool(check(ok_text.replace("fact-guard\n---", "nope\n---").replace("  - fact-guard", "  - nope"), known))),
             ("an agent naming flightclaw without granting its MCP server fails",
              any("flightclaw" in x for x in check(ok_text.replace("skills:", "tools: Read\nskills:").replace("Run", "Use flightclaw. Run"), known))),
             ("an agent granting the server passes",
              check(ok_text.replace("skills:", "tools: Read, mcp__flightclaw\nskills:").replace("Run", "Use flightclaw. Run"), known) == []),
             ("a duplicate entry fails", bool(check(ok_text.replace("  - fact-guard\n", "  - fact-guard\n  - fact-guard\n"), known)))]
    for name, ok in cases:
        print(("PASS " if ok else "FAIL ") + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest() if sys.argv[1:] == ["selftest"] else main())
