"""
Say which apps' migration-tagged tests a range of commits needs, and why:

    python3 scripts/gate/migneeded.py <base> <tip>     # run in the tree, or set GATE_TREE

One line an app, "need <app>: <why>" or "skip <app>: no migration change
since <base>", after a "range <base>..<tip>" line. The mig lane takes over
four hours for 51 tests and most pushes change no migration. <base> must be
a commit a whole gate has passed on (the pushed base): the skip rests on it.

An app needs its run when the range
  - adds, changes or removes a file under apps/<app>/migrations/;
  - changes a module one of its migrations imports (apps/core/ledger_log.py);
  - changes either of those for an app its migrations depend on: every
    dependent of a changed app, through the migration files' dependencies;
  - changes a file holding one of its migration-tagged tests;
  - changes, in a test module such a file itself imports, what it imports:
    the definition of a name it takes, anything that definition names in
    turn, or a line outside every function and class. A test added to
    apps/e2e/tests_races.py does not re-run sales because one sales
    migration test borrows `fixture` from it; a change to `fixture` does.
A changed test file runs its own app only: its dependents' migrations and
tests are as they were.
Every app needs it when the range touches the lock sentinel
(apps/core/lock_order.py, apps/core/apps.py which installs it, the locking
code in apps/core/models.py), config/, requirements.txt, manage.py or
scripts/gate itself: in g21 a sentinel change failed 24 migration tests and
no migration had changed. And every app needs it when the range cannot be
read: an unknown base, or one that is not an ancestor of the tip.

What this does not see: a migration-tagged test that fails because of
application code no migration imports. That code's own tests run in every
other lane; the tagged tests do not, so when in doubt run without GATE_BASE.

Needs git only, not Django: the workflow runs it before anything is installed.
"""
import os
import re
import subprocess
import sys

ALL_PATHS = ("apps/core/lock_order.py", "apps/core/apps.py", "requirements.txt", "manage.py")
ALL_DIRS = ("config/", "scripts/gate/")
# A changed line of apps/core/models.py, or the function it sits in, that is the locking code.
LOCKING = re.compile(r"lock|sentinel|serialis|atomic|select_for_update|transaction", re.I)
TAGGED = r"tag\(\s*[\"']migration[\"']"


def app_of(path):
    parts = path.split("/")
    return parts[1] if len(parts) > 2 and parts[0] == "apps" else None


def dependents(changed, deps):
    """Every app whose migrations depend, however far down, on one in `changed`: {app: the one it follows}."""
    found, frontier = {}, list(changed)
    while frontier:
        on = frontier.pop()
        for app, needs in deps.items():
            if on in needs and app not in changed and app not in found:
                found[app] = on
                frontier.append(app)
    return found


def decide(apps, changed, tagged, deps, imported=None, helpers=None, locking_touched=False):
    """
    The decision, over plain values so it can be tested without a repository.

    apps      every app with migrations or migration-tagged tests
    changed   the paths the range changed
    tagged    {path: app} the files holding migration-tagged tests
    helpers   {path: {apps whose tagged files import that test module}}: only those whose change
              reaches what is imported (reaches(), below)
    deps      {app: {apps its migrations depend on}}
    imported  {path: {apps whose migrations import that module}}
    locking_touched  the range changed locking code in apps/core/models.py

    Returns {app: why} for the apps that need their run. No app is ever dropped for being unknown.
    """
    imported, helpers = imported or {}, helpers or {}
    for path in changed:
        why = None
        if path in ALL_PATHS:
            why = f"{path} changed"
        elif path.startswith(ALL_DIRS) and not path.endswith(".md"):
            why = f"{path} changed"
        if why:
            return {app: f"{why}: every app runs" for app in apps}
    if locking_touched:
        return {app: "the locking code in apps/core/models.py changed: every app runs" for app in apps}
    need = {}
    for path in sorted(changed):  # the plainest reason first: a migration, then what it imports, then its tests
        app = app_of(path)
        if app and path.startswith(f"apps/{app}/migrations/"):
            need.setdefault(app, f"{path} changed")
    for path in sorted(changed):
        for user in sorted(imported.get(path, ())):
            need.setdefault(user, f"{path} changed, which its migrations import")
    for app, on in sorted(dependents(set(need), deps).items()):
        need[app] = f"its migrations depend on {on}, which changed"
    # A test file is not a migration: it runs its own app and nothing follows from it.
    for path in sorted(changed):
        if path in tagged:
            need.setdefault(tagged[path], f"{path} changed, which holds migration tests")
    for path in sorted(changed):
        for user in sorted(helpers.get(path, ())):
            need.setdefault(user, f"{path} changed, which its migration tests import")
    # An app nobody listed (a new one, its first migration) still gets its line.
    return {app: need[app] for app in sorted(set(apps) | set(need)) if app in need}


WHOLE = "*"  # every name of a module: imported whole, or in a form one line does not spell out


def hunks(diff):
    """(first, last, removal) in the new file for each change of a -U0 diff; a removal is the lines either side."""
    found = []
    for line in diff.splitlines():
        at = re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))? @@", line)
        if at:
            start, count = int(at.group(1)), int(at.group(2) or 1)
            found.append((start, start + count - 1, False) if count else (start, start + 1, True))
    return found


def reaches(source, names, changed_lines):
    """
    Whether a change to a test module reaches the `names` another file imports from it.

    It does when a changed line lies in the definition of one of the names, in a definition that one
    names in turn (a base class, a function it calls), or outside every top-level function and
    class (an import, a constant: anything may read those). It does when a name is not a top-level
    function or class here, and when the module cannot be parsed. Only a change wholly inside
    other functions and classes does not.
    """
    import ast
    if WHOLE in names:
        return True
    try:
        body = ast.parse(source).body
    except SyntaxError:
        return True
    spans = {}
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = min([node.lineno] + [d.lineno for d in node.decorator_list])
            if node.name in spans:  # defined twice: do not guess which one is meant
                return True
            spans[node.name] = (first, node.end_lineno, node)
    wanted, frontier = set(), list(names)
    while frontier:
        name = frontier.pop()
        if name in wanted:
            continue
        if name not in spans:
            return True
        wanted.add(name)
        frontier.extend(n.id for n in ast.walk(spans[name][2]) if isinstance(n, ast.Name) and n.id in spans)
        frontier.extend(n.value.id for n in ast.walk(spans[name][2]) if isinstance(n, ast.Attribute)
                        and isinstance(n.value, ast.Name) and n.value.id in spans)
    lines = source.splitlines()

    def holder(line):
        return next((name for name, (first, last, _) in spans.items() if first <= line <= last), None)

    for start, end, removal in changed_lines:
        if removal:  # nothing of it is left to read: unless it was inside one definition, it may have been anything
            if holder(start) is None or holder(start) != holder(end) or holder(start) in wanted:
                return True
            continue
        for line in range(start, min(end, len(lines)) + 1):
            text = lines[line - 1].strip()
            if holder(line) in wanted or (holder(line) is None and text and not text.startswith("#")):
                return True
    return False


class Unreadable(Exception):
    pass


def _git(tree, *args, ok=(0,)):
    done = subprocess.run(["git", "-C", tree, *args], capture_output=True, text=True)
    if done.returncode not in ok:
        raise Unreadable(f"git {' '.join(args)}: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout


def _grep(tree, rev, pattern, *paths):
    """[(path, matched text)] for `pattern` in `paths` at `rev`. Exit 1 is no match."""
    out = _git(tree, "grep", "-I", "-o", "-E", "-e", pattern, rev, "--", *paths, ok=(0, 1))
    return [tuple(line.split(":", 2)[1:]) for line in out.splitlines() if line.count(":") >= 2]


def _module_file(module, files):
    path = module.replace(".", "/")
    for candidate in (path + ".py", path + "/__init__.py"):
        if candidate in files:
            return candidate
    return None


def facts(tree, tip):
    """What the tip's files say: the apps, the tagged files with their helpers, the dependencies, the imports."""
    files = set(_git(tree, "ls-tree", "-r", "--name-only", tip, "--", "apps").splitlines())
    apps, deps = set(), {}
    for path in files:
        if "/migrations/" in path and path.endswith(".py"):
            apps.add(app_of(path))
    for path, text in _grep(tree, tip, r"\(\s*[\"'][a-z_0-9]+[\"'],\s*[\"'][0-9]{4}_", "apps/*/migrations/*.py"):
        other = re.search(r"[\"']([a-z_0-9]+)[\"']", text).group(1)
        if other != app_of(path):
            deps.setdefault(app_of(path), set()).add(other)
    imported = {}
    for path, text in _grep(tree, tip, r"^\s*(from|import) apps\.[A-Za-z_0-9.]+( import [A-Za-z_0-9, ]+)?",
                            "apps/*/migrations/*.py"):
        words = text.split()
        modules = [words[1]] + ([f"{words[1]}.{name.strip(',')}" for name in words[3:]] if len(words) > 3 else [])
        for module in modules:
            target = _module_file(module, files)
            if target and "/migrations/" not in target:
                imported.setdefault(target, set()).add(app_of(path))
    # The test modules each test file imports, so a changed tests_base reaches the tagged file built on it.
    uses = {}
    for path, text in _grep(tree, tip, r"^\s*(from [A-Za-z_0-9.]+ import .*|import apps\.[A-Za-z_0-9.]+.*)",
                            "apps/*.py"):
        if not os.path.basename(path).startswith("tests"):
            continue
        words = text.split()
        if words[0] == "import":  # import apps.x.tests_y: the whole module
            source, names, whole = words[1].strip(","), [], True
        else:
            source, rest = words[1], text.split(" import ", 1)[1].split("#")[0]
            # "import (" over several lines, "*" or a continuation: which names cannot be read off this line
            whole = any(mark in rest for mark in "(*\\")
            names = [] if whole else [name.split(" as ")[0].strip() for name in rest.split(",") if name.strip()]
        if source.startswith("."):
            source = os.path.dirname(path).replace("/", ".") + ("." + source.lstrip(".") if source.strip(".") else "")
        target = _module_file(source, files)
        if target and os.path.basename(target).startswith("tests"):
            taken = uses.setdefault(path, {}).setdefault(target, set())
            taken.update([WHOLE] if whole else names)
        for name in names:  # from . import tests_base
            target = _module_file(f"{source}.{name}", files)
            if target and os.path.basename(target).startswith("tests"):
                uses.setdefault(path, {}).setdefault(target, set()).add(WHOLE)
    tagged, helpers = {}, {}
    for path, _ in _grep(tree, tip, TAGGED, "apps/*.py"):
        apps.add(app_of(path))
        tagged[path] = app_of(path)
        # What the file itself imports, no further: followed all the way, sales' tagged files reach 70 test
        # modules through each other's fixtures, and any test change at all would run the lane.
        for each, names in uses.get(path, {}).items():
            if each != path:
                helpers.setdefault(each, {}).setdefault(app_of(path), set()).update(names)
    return apps, tagged, deps, imported, helpers


def needed(base, tip, tree="."):
    """({app: why needed}, [every app], base, tip) for base..tip in the repository at `tree`."""
    tip_sha = _git(tree, "rev-parse", "--verify", f"{tip}^{{commit}}").strip()
    apps, tagged, deps, imported, helpers = facts(tree, tip_sha)
    apps = sorted(apps)
    try:
        if not base.strip("0"):
            raise Unreadable("there is no base (a new branch)")
        base_sha = _git(tree, "rev-parse", "--verify", f"{base}^{{commit}}").strip()
        if subprocess.run(["git", "-C", tree, "merge-base", "--is-ancestor", base_sha, tip_sha]).returncode:
            raise Unreadable(f"{base_sha[:7]} is not an ancestor of {tip_sha[:7]}")
        changed = _git(tree, "diff", "--name-only", "--no-renames", base_sha, tip_sha).splitlines()
        # A file tagged at the base and not at the tip lost its tag or its tests: its app runs.
        for path, _ in _grep(tree, base_sha, TAGGED, "apps/*.py"):
            tagged.setdefault(path, app_of(path))
        reached = {}
        for path in changed:
            for app, names in helpers.get(path, {}).items():
                if reaches(_git(tree, "show", f"{tip_sha}:{path}"), names,
                           hunks(_git(tree, "diff", "-U0", base_sha, tip_sha, "--", path))):
                    reached.setdefault(path, set()).add(app)
        # A helper the base's tagged files imported and the tip no longer has: nothing is left to read.
        was = facts(tree, base_sha)[4]
        for path in set(changed) & set(was):
            if not _git(tree, "ls-tree", "--name-only", tip_sha, "--", path).strip():
                reached.setdefault(path, set()).update(was[path])
        helpers = reached
        locking = False
        if "apps/core/models.py" in changed:
            diff = _git(tree, "diff", "-U0", base_sha, tip_sha, "--", "apps/core/models.py")
            locking = any(LOCKING.search(line) for line in diff.splitlines()
                          if line.startswith(("+", "-", "@@")) and not line.startswith(("+++", "---")))
    except Unreadable as problem:
        return {app: f"the range cannot be read ({problem}): every app runs" for app in apps}, apps, base, tip_sha
    return decide(apps, changed, tagged, deps, imported, helpers, locking), apps, base_sha, tip_sha


def report(need, apps, base, tip):
    lines = [f"range {base}..{tip}"]
    for app in sorted(set(apps) | set(need)):
        lines.append(f"need {app}: {need[app]}" if app in need else
                     f"skip {app}: no migration change since {base[:7]}")
    return "\n".join(lines)


def read(text):
    """A report back as (base, tip, {needed apps}, {skipped apps}); the mig lane keeps it as needed.txt."""
    base = tip = None
    need, skip = set(), set()
    for line in text.splitlines():
        word, _, rest = line.partition(" ")
        if word == "range":
            base, _, tip = rest.partition("..")
        elif word in ("need", "skip"):
            (need if word == "need" else skip).add(rest.split(":")[0])
    if not base or not tip:
        raise ValueError("no range line")
    return base, tip, need, skip


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    try:
        print(report(*needed(sys.argv[1], sys.argv[2], os.environ.get("GATE_TREE", "."))))
    except Unreadable as problem:  # the tip itself: nothing can be said, and nothing may be skipped on it
        raise SystemExit(f"migneeded: {problem}")
