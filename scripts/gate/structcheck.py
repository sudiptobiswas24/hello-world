"""
After cherry-picking branches whose conflicts were resolved by keeping both
sides, check that every test class still holds the methods its sources
gave it:

    python3 scripts/gate/structcheck.py <tree> <base sha> <worktree>=<rev> ...

A conflict inside a class, kept both ways, splices one class into the
middle of another and still parses (g18: a quality test class lost four
methods). A method a branch removed on purpose is reported too; read each.
"""
import ast, subprocess, sys
G, base, srcs = sys.argv[1], sys.argv[2], sys.argv[3:]
def shape(text):
    out = {}
    for node in ast.parse(text).body:
        if isinstance(node, ast.ClassDef):
            out[node.name] = {n.name for n in node.body if isinstance(n, ast.FunctionDef)}
    return out
def show(rev, path, tree=None):
    r = subprocess.run(["git", "-C", tree or G, "show", f"{rev}:{path}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None
files = subprocess.run(["git", "-C", G, "diff", "--name-only", base, "HEAD"], capture_output=True, text=True).stdout.split()
files = [f for f in files if f.endswith(".py") and ("/tests" in f or f.endswith("audit_invariants.py"))]
bad = 0
for f in files:
    have = shape(open(f"{G}/{f}").read())
    want = {}
    for tree, tip in (s.split("=") for s in srcs):
        t = show(tip, f, tree)
        if t is None: continue
        for cls, meths in shape(t).items():
            want.setdefault(cls, set()).update(meths)
    b = show(base, f, "/home/user/hello-world")
    if b:
        for cls, meths in shape(b).items():
            want.setdefault(cls, set()).update(meths)
    for cls, meths in want.items():
        missing = meths - have.get(cls, set())
        if missing:
            bad += 1
            print(f"{f}: {cls} lacks {sorted(missing)[:5]}")
print("files checked", len(files), "mismatches", bad)
