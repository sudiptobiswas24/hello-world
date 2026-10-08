"""
After one full gate on the newest commit, write every unpushed commit's
Gate line and replay the chain with identical trees. Pushing is a
separate, explicit step.

    python3 scripts/gate/finalize.py <pushed base sha> <gated tip sha> <gate prefix>

Run it in the gate worktree ($GATE_TREE, default this repository), which
must be clean. Each commit in base..tip carries the placeholder line
"Gate: {GATE}" exactly once. The tip gets the full Gate line; the others
say they were gated with it. The replayed tip's tree must equal the
gated one, or nothing is printed as done.
"""
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
TREE = os.environ.get("GATE_TREE", str(HERE.parent.parent))
base, tip, prefix = sys.argv[1:4]


def git(*args):
    done = subprocess.run(["git", "-C", TREE, "-c", "user.name=Claude", "-c", "user.email=noreply@anthropic.com",
                           *args], capture_output=True, text=True)
    if done.returncode:
        raise SystemExit(f"git {' '.join(args)} failed:\n{done.stdout}\n{done.stderr}")
    return done.stdout.strip()


line = subprocess.run(["python3", str(HERE / "gateline.py"), prefix], capture_output=True, text=True)
if line.returncode or not line.stdout.startswith("Gate: "):
    raise SystemExit(f"no Gate line: {line.stdout}{line.stderr}")
full = line.stdout.strip()
if git("status", "--short"):
    raise SystemExit(f"{TREE} is not clean")
commits = git("log", "--reverse", "--format=%H", f"{base}..{tip}").split()
subject = git("log", "-1", "--format=%s", tip)
git("checkout", "-q", base)
for sha in commits:
    body = git("log", "-1", "--format=%B", sha)
    if body.count("Gate: {GATE}") != 1:
        raise SystemExit(f"{sha[:7]} has no single Gate placeholder")
    gate = full if sha == tip else (
        f"Gate: not run on this commit by itself. Gated once with the {len(commits) - 1} commits pushed with it, "
        f"at the newest of them (\"{subject}\"): {full[len('Gate: '):]}")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as message:
        message.write(body.replace("Gate: {GATE}", gate) + "\n")
    git("cherry-pick", sha)
    git("commit", "-q", "--amend", "-F", message.name)
new_tip = git("rev-parse", "HEAD")
if subprocess.run(["git", "-C", TREE, "diff", "--quiet", tip, new_tip]).returncode:
    raise SystemExit("the replayed tip's tree differs from the gated one")
print("gated tree, new tip", new_tip)
print(git("log", "--format=%h %s", f"{base}..{new_tip}"))
