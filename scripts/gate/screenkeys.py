"""
What the screens send against what each action takes (O579).

    .venv/bin/python scripts/gate/screenkeys.py [screens]   (from the tree, as the ui lane runs it)

An action that reads its body by hand names the keys it takes (apps.core.api.takes_only) and refuses any other,
so a screen sending one more key than its action takes is refused on its first click. screenkeys.mjs reads
every write the office app and the station tablet make from oxc's syntax tree; this resolves each to its action
and compares. A write whose action or body cannot be read statically must be listed in REVIEWED, with what it
sends, after reading it by hand; one listed and no longer found fails too, so the list is read again when the
code under it changes. Prints "screens: ..." and exits 1 on anything refused, unread or stale.
"""
import ast
import collections
import inspect
import json
import os
import subprocess
import sys

TREE = os.getcwd()
sys.path.insert(0, TREE)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.urls import Resolver404, resolve  # noqa: E402

from apps.core.management.commands.audit_invariants import Command, _function_tree, _takes_only_list  # noqa: E402

# (file, action, keys sent): why it is right, read by hand. "?" is an action the URL does not name statically.
REVIEWED = {
    ("frontend/src/api/hooks.ts", "?", ()):
        "useAct's run: every call is read where it is made",
    ("frontend/src/views/RecordScreen.tsx", "?", ()):
        "the action dialog and the action button: each declared action's body or fields, read where declared (2)",
    ("frontend/src/views/RecordScreen.tsx", "?", ("extra",)):
        "a record's create, posted to createUrl ?? endpoint: the serializer's fields, not an action",
    ("frontend/src/views/ListView.tsx", "?", ()):
        "a list's own actions, each posted with an empty body",
    ("frontend/src/forms/Lines.tsx", "?", ()):
        "a line's create (POST to the lines' list): the serializer's fields",
    ("frontend/src/modules/money/PaymentForm.tsx", "?", ("amount", "payment")):
        "an allocation's create (POST to config.allocations)",
    ("frontend/src/modules/parties/NewParty.tsx", "?", ("addresses", "bank_accounts", "contacts", "party", "tax", "terms")):
        "the new party's create (POST to its list)",
    ("frontend/src/modules/stores/ScanInto.tsx", "?", ()):
        "the offline queue sending a held scan: its body is the one held at the count screen, read there",
    ("frontend/src/modules/accounts/Challans.tsx", "?",
     ("bank_account", "bsr_code", "challan_number", "date", "month", "section")):
        "a TDS or TCS challan's create, to ${kind.endpoint}pay/: both pay actions take all six (O573)",
    ("frontend/src/modules/plant/JobForm.tsx", "?", ("due_on", "fault", "notes", "planned_minutes", "technician")):
        "a maintenance job's create and save; the create adds SERVES (work_centre, machine), createOnly through a "
        "map: the serializer has all seven (O573)",
}
REVIEWED_COUNT = {("frontend/src/views/RecordScreen.tsx", "?", ()): 2,
                  ("frontend/src/modules/plant/JobForm.tsx", "?",
                   ("due_on", "fault", "notes", "planned_minutes", "technician")): 2}


def write_actions():
    by_path = collections.defaultdict(list)
    for view in Command()._routed_viewsets():
        if not view.__module__.startswith("apps."):
            continue
        for name in dir(view):
            function = getattr(view, name, None)
            if not hasattr(function, "mapping") or set(function.mapping) <= {"get"}:
                continue
            owner = next((cls for cls in view.__mro__ if name in vars(cls)), None)
            by_path[function.url_path].append((view, name, taken(owner, name, view)))
    return by_path


NO_BODY = frozenset({"<reads no body>"})


def taken(owner, name, view=None):
    """
    The keys an action's takes_only names. A list built at run time (a confirm's model FIGURES, a request
    serializer's fields) is worked out against the viewset's model and the serializer, as the action would.
    NO_BODY for an action that never reads its body; None for one that reads it through a serializer.
    """
    if owner is None:
        return None
    function = inspect.unwrap(vars(owner)[name])
    tree = _function_tree(function)
    listed = _takes_only_list(tree)
    if listed is not None:
        return listed
    call = next((node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and getattr(node.func, "id", None) == "takes_only"), None)
    if call is None:
        return None if "request.data" in inspect.getsource(function) else NO_BODY
    scope = dict(function.__globals__, self=view or owner)
    model = getattr(getattr(view or owner, "queryset", None), "model", None)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and isinstance(node.value, ast.Call):
            made = node.value.func
            if isinstance(made, ast.Attribute) and made.attr == "get_object" and model is not None:
                scope[node.targets[0].id] = model
            elif isinstance(made, ast.Name) and inspect.isclass(scope.get(made.id)):
                try:
                    scope[node.targets[0].id] = scope[made.id]()
                except Exception:
                    pass
    keys = {argument.value for argument in call.args[1:] if isinstance(argument, ast.Constant)}
    for argument in call.args[1:]:
        if isinstance(argument, ast.Starred):
            try:
                keys |= set(eval(compile(ast.Expression(argument.value), "<takes_only>", "eval"), scope))
            except Exception as error:
                raise SystemExit(f"screens: {owner.__name__}.{name}'s list could not be worked out ({error!r})")
    return keys


def made_keys(owner, name, view):
    """
    A create or a save that makes its serializer itself, given request.data (a follow-up's, on a plain ViewSet):
    that serializer's fields and the keys its context names in body_beside. None if it makes none, or more than one.
    """
    from apps.core.management.commands.audit_invariants import Command

    function = inspect.unwrap(vars(owner)[name])
    found = []
    for node in ast.walk(_function_tree(function)):
        given = next((keyword.value for keyword in getattr(node, "keywords", ()) if keyword.arg == "data"), None)
        if not (isinstance(node, ast.Call) and isinstance(given, ast.Attribute) and given.attr == "data"
                and getattr(given.value, "id", None) == "request" and isinstance(node.func, ast.Name)):
            continue
        fields = Command._fields_of(function.__globals__.get(node.func.id))
        if fields is None:
            return None
        for keyword in node.keywords:
            if keyword.arg == "context" and isinstance(keyword.value, ast.Dict):
                for key, value in zip(keyword.value.keys, keyword.value.values):
                    if getattr(key, "value", None) == "body_beside" and isinstance(value, (ast.Tuple, ast.List)):
                        fields |= {each.value for each in value.elts if isinstance(each, ast.Constant)}
        found.append(fields)
    return found[0] if len(found) == 1 else None


def action_at(url):
    try:
        match = resolve(url.split("?")[0])
    except Resolver404:
        return None
    view = getattr(match.func, "cls", None)
    actions = getattr(match.func, "actions", {}) or {}
    name = actions.get("post") or actions.get("patch") or actions.get("put")
    if view is None or name is None:
        return None
    owner = next((cls for cls in view.__mro__ if name in vars(cls)), None)
    if name in ("create", "update", "partial_update"):
        return view, name, record_keys(view, name, owner)
    return view, name, taken(owner, name, view)


def record_keys(view, name, owner):
    """
    What a create or a save takes (O573): a create read by hand names its keys (takes_only); otherwise the
    serializer's fields, which refuse any other key, and what the view reads beside them for that action
    (body_beside_its_serializer). None where the serializer cannot be worked out without a request.
    """
    from apps.core.api import RefusesWhatItWouldDrop

    if owner is not None and owner.__module__.startswith("apps."):
        listed = taken(owner, name, view)
        if listed is not None:
            return listed
        listed = made_keys(owner, name, view)
        if listed is not None:
            return listed
    made = view()
    made.action, made.request, made.kwargs, made.format_kwarg = name, None, {}, None
    try:
        serializer = made.get_serializer_class()
        fields = set(serializer(context={"view": made}).fields)
    except Exception:
        return None
    if not issubclass(serializer, RefusesWhatItWouldDrop):
        return None  # audit_invariants (serializers_off_the_shared_base) reports it
    return fields | set(getattr(view, "body_beside_its_serializer", {}).get(name, ())) \
        | RefusesWhatItWouldDrop.NOT_THE_RECORDS


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    scanned = sys.argv[1] if len(sys.argv) > 1 else TREE  # another copy of the screens, to prove a planted send fails
    scan = subprocess.run(["node", os.path.join(here, "screenkeys.mjs"), scanned], capture_output=True, text=True)
    if scan.returncode:
        print(scan.stderr[-2000:])
        print("screens: the scan failed")
        return 1
    rows = [json.loads(line) for line in scan.stdout.splitlines() if line.strip()]
    by_path = write_actions()
    problems, checked, reviewed = [], 0, collections.Counter()
    for row in rows:
        where = f"{row['file']}:{row['line']}"
        if row["shape"] == "unparsed":
            problems.append(f"{where} could not be parsed: {row['open']}")
            continue
        action, keys, url = row.get("action"), tuple(row.get("keys") or ()), row.get("url")
        if row.get("through"):
            continue  # a component's own post, read through each screen that uses it
        named = action not in (None, "?") and not action.startswith("$")
        found = None
        if url:
            found = action_at(url)
        elif named and (row.get("endpoint") or "").startswith("/api/"):
            endpoint = row["endpoint"]
            found = action_at(f"{endpoint}1/{action}/") or action_at(f"{endpoint}{action}/")
            if found and found[1] in ("create", "update", "partial_update"):
                found = None  # `${endpoint}void-theirs/` read as a record whose id is the action's name
        elif action is None and (row.get("endpoint") or "").startswith("/api/") and row.get("method"):
            # A POST to a list is its create, a PATCH or PUT to `${endpoint}${id}/` its save (O573).
            endpoint = row["endpoint"]
            found = action_at(endpoint if row["method"] == "POST" else f"{endpoint}1/")
        if found:
            view, name, listed = found
            if listed is None:
                continue  # an action that names no list, or a serializer that needs a request to be built
            if row["open"]:
                found = None  # its body is not all read: by hand, below
            else:
                checked += 1
                if listed is NO_BODY and keys:
                    problems.append(f"{where} sends {list(keys)} to {view.__name__}.{name}, which reads no body: "
                                    "each is dropped without a word.")
                elif listed is not NO_BODY and set(keys) - listed:
                    problems.append(f"{where} sends {sorted(set(keys) - listed)} to {view.__name__}.{name}, which "
                                    f"takes only {sorted(listed)}: refused on its first click.")
                continue
        if action is None and not row["shape"].startswith("record "):
            continue  # a POST to a list or a PATCH to a record this cannot resolve: a route that is not ours
        if row["shape"].startswith("record ") and not row["open"] and found is None:
            problems.append(f"{where} {row['shape']} to {row.get('endpoint')}: no create or save of ours answers it.")
            continue
        if not named or row["open"]:
            key = (row["file"], action or "?", keys)
            if key in REVIEWED:
                reviewed[key] += 1
                continue
            problems.append(f"{where} {row['shape']} {action}: sends {list(keys)}, and {row['open'] or 'its action'} "
                            "cannot be read here. Read it by hand, then add it to REVIEWED with why it is right.")
            continue
        candidates = [(view, name, listed) for view, name, listed in by_path.get(action, []) if listed is not None]
        if not candidates:
            continue  # no write action of that name: a create, a route, a GET
        checked += 1
        if not any(set(keys) <= listed for _, _, listed in candidates):
            problems.append(f"{where} sends {list(keys)} to {action}, and no action of that name takes them all: "
                            + "; ".join(f"{view.__name__}.{name} takes {sorted(listed)}" for view, name, listed in candidates))
    for key, why in REVIEWED.items():
        if reviewed[key] != REVIEWED_COUNT.get(key, 1):
            problems.append(f"REVIEWED {key} is found {reviewed[key]} time(s), not {REVIEWED_COUNT.get(key, 1)}: "
                            f"the code under it changed ({why}). Read it again and update the entry.")
    for problem in problems:
        print(problem)
    print(f"screens: {checked} writes checked against their actions, {sum(reviewed.values())} read by hand, "
          f"{len(problems)} refused, unread or stale")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
