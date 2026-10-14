"""
Every register id a commit's subject names as fixed is off the register at
the tip:

    python3 scripts/gate/registercheck.py <pushed base> <gated tip>

A fix commit names the ids it closes in its subject ("O150: a complaint's
batches ..."), and the row comes off in a commit of the same push. g18 closed
O192, O193 and O194 and left their rows open, so the next wave's brief would
have sent an agent to fix them again. Documentation-only commits are skipped:
"Register O199-O201" names rows it has just opened.
"""
import re, subprocess, sys

base, tip = sys.argv[1], sys.argv[2]


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def ids(text):
    found = set()
    for first, last in re.findall(r"\bO(\d+)(?:-O?(\d+))?\b", text):
        found.update(f"O{n}" for n in range(int(first), int(last or first) + 1))
    return found


register = git("show", f"{tip}:docs/RISKS.md")
open_rows = set(re.findall(r"^\| (O\d+) \|", register.split("## Open defects", 1)[1].split("\n## ", 1)[0], re.M))
stale = []
for sha in git("log", "--format=%H", f"{base}..{tip}").split():
    if "Gate: documentation only" in git("log", "-1", "--format=%B", sha):
        continue
    subject = git("log", "-1", "--format=%s", sha).strip()
    stale += [(row, sha[:7], subject) for row in sorted(ids(subject) & open_rows)]
for row, sha, subject in stale:
    print(f"{row} is still open, though {sha} names it: {subject}")
print(f"register: {len(stale)} row(s) named fixed and still open")
sys.exit(1 if stale else 0)
