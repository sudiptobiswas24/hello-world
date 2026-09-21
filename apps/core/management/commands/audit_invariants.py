"""
The half of the audit checklist a machine can run.

Everything here corresponds to a shape that has actually produced a
defect in this codebase. Nothing is here because it sounded like good
practice: a check that has never caught anything is noise that trains
people to ignore the output.
"""

import re
from pathlib import Path

from django.apps import apps as django_apps
from django.core.management.base import BaseCommand
from django.db import models

OUR_APPS = ("core", "accounting", "inventory", "sales", "purchasing", "hr")

# Correction paths. A flow built only forwards is the single most
# productive defect shape in this codebase's history.
CORRECTION_METHODS = (
    "create_return", "create_credit_note", "create_debit_note", "void",
    "create_reversal", "recover_write_off", "withdraw_approval", "unmatch",
    "reopen", "unapply", "cancel",
)


def source_of(app_label):
    root = Path("apps") / app_label
    return {path: path.read_text(encoding="utf-8") for path in root.rglob("*.py")}


def app_sources():
    return {label: source_of(label) for label in OUR_APPS}


IMPORT_BLOCK = re.compile(
    r"^\s*(?:from\s+[\w.]+\s+)?import\s+(?:\([^)]*\)|[^\n]*)", re.M
)


def strip_imports(text):
    """
    The same text without its import statements.

    A module that re-exports its neighbours — which models.py does for
    every one of these — would otherwise make every helper in the app
    look used by something.
    """
    return IMPORT_BLOCK.sub("", text)


def non_test_text(sources):
    return "\n".join(
        text for path, text in sources.items() if "test" not in path.name
    )


def test_text(sources):
    return "\n".join(
        text for path, text in sources.items() if "test" in path.name
    )


class Command(BaseCommand):
    help = "Check the model-level invariants the audit checklist relies on."

    def add_arguments(self, parser):
        parser.add_argument(
            "--app", action="append", dest="apps",
            help="Limit to one or more app labels.",
        )

    def handle(self, *args, **options):
        labels = options["apps"] or list(OUR_APPS)
        sources = app_sources()
        all_code = "\n".join(non_test_text(s) for s in sources.values())
        all_tests = "\n".join(test_text(s) for s in sources.values())

        findings = []
        findings += self.unread_settings(all_code)
        findings += self.uncalled_helpers(labels, sources, all_code)
        findings += self.unlocked_stock_writers(labels, sources)
        findings += self.untested_corrections(all_code, all_tests)
        findings += self.mutable_posted_documents(labels, sources)
        findings += self.unconstrained_numbers(labels)
        findings += self.unsigned_money(labels)

        if not findings:
            self.stdout.write(self.style.SUCCESS("No invariant findings."))
            return
        for shape, detail in findings:
            self.stdout.write(f"{self.style.WARNING(shape)}: {detail}")
        self.stdout.write("")
        self.stdout.write(self.style.WARNING(f"{len(findings)} finding(s)."))

    # A helper nothing calls is not always a defect. These are the ones
    # that are genuinely for callers this repository does not contain,
    # with the reason, so the check stays worth reading.
    CALLED_FROM_OUTSIDE = {
        "core.to_date": "date coercion, used as an expression everywhere",
        "core.app_sources": "the auditor's own plumbing",
        "core.source_of": "the auditor's own plumbing",
        "core.non_test_text": "the auditor's own plumbing",
        "core.test_text": "the auditor's own plumbing",
        "core.exception_handler": "named in settings, never called by name",
        "inventory.describe_plan": "for a pick list a UI will render",
        "inventory.hours_by_account": "a report, for whatever asks",
    }

    # -- shape 2: inert feature ------------------------------------------
    def uncalled_helpers(self, labels, sources, code):
        """
        A module-level function nothing outside its own file calls.

        The shape that has now produced six findings here, and the one a
        probe cannot see: FEFO lot allocation, bin routing and put-away
        were each built, tested, and called by no document, so a delivery
        demanded the answers by hand and the algorithms never ran.

        Tests do not count as callers. A feature exercised only by its
        own tests is a feature the product does not use.
        """
        findings = []
        without_imports = strip_imports(code)
        for label in labels:
            for path, text in sorted(sources[label].items()):
                if "test" in path.name or path.name == "__init__.py":
                    continue
                if "migrations" in path.parts:
                    continue
                own_body = strip_imports(text)
                for match in re.finditer(r"^def ([a-z][a-z0-9_]*)\(", text, re.M):
                    name = match.group(1)
                    if name.startswith("_"):
                        continue
                    key = f"{label}.{name}"
                    if key in self.CALLED_FROM_OUTSIDE:
                        continue
                    # Every mention in non-test code. One means only
                    # the definition, and the function is dead.
                    #
                    # Mentions rather than calls, because
                    # `_run(stock_ledger, item)` passes the function by
                    # reference and never writes its name followed by a
                    # bracket — the first version of this check reported
                    # three such helpers as dead. Imports are stripped, or
                    # the re-export block in models.py would make every
                    # helper look used. And a caller in the same file
                    # counts: a helper used by its own neighbours is used,
                    # which the second version of this check denied and
                    # so flagged half the codebase.
                    mentions = len(re.findall(rf"\b{re.escape(name)}\b", without_imports))
                    if mentions <= 1:
                        findings.append((
                            "uncalled helper",
                            f"{key}() is defined in {path.name} and called by nothing "
                            "outside it — built, and not wired to anything.",
                        ))
        return findings

    # Files that write stock without holding a position, with the reason.
    WRITES_STOCK_UNLOCKED = {
        "inventory/locking.py": "defines the lock",
        "core/audit_invariants.py": "contains the phrase it searches for, not the call",
    }

    def unlocked_stock_writers(self, labels, sources):
        """
        A file that writes the stock ledger and never holds a position.

        Every posting path has the same shape — read what is on the
        shelf, decide, write — and between the read and the write another
        transaction can do the same. Two shipments of the last ten units
        both find ten and both post. No test can find this, because a
        test suite is single-threaded and the window never opens, so the
        check has to be mechanical or it is nothing.
        """
        findings = []
        for label in labels:
            for path, text in sorted(sources[label].items()):
                if "test" in path.name or "migrations" in path.parts:
                    continue
                key = f"{label}/{path.name}"
                if key in self.WRITES_STOCK_UNLOCKED:
                    continue
                if "StockMovement.objects.create" not in text:
                    continue
                # A call, not a mention: an import line names the helper
                # too, so matching the name alone passes a file that
                # imports it and never uses it. The first version of this
                # check did exactly that.
                if re.search(r"\block_positions?\(", text):
                    continue
                findings.append((
                    "unlocked stock writer",
                    f"{key} writes stock movements and never holds a position — "
                    "two documents can read the same shelf and both post.",
                ))
        return findings

    def unread_settings(self, code):
        """
        A settings field nothing reads is a feature that looks handled.

        Four of twenty-six findings were exactly this, including the same
        settlement discount twice.
        """
        company = django_apps.get_model("core", "Company")
        findings = []
        for field in company._meta.get_fields():
            if not isinstance(field, models.Field) or field.auto_created:
                continue
            if field.name in ("id", "name", "created_at", "updated_at"):
                continue
            # A read looks like company.field, .field_id, or "field" in a
            # values()/update_fields list.
            # The declaration reads "name = models.X(", which does not
            # match ".name", so any hit at all is a genuine read.
            pattern = rf"\.{re.escape(field.name)}(_id)?\b"
            hits = len(re.findall(pattern, code))
            if hits == 0:
                findings.append((
                    "inert setting",
                    f"Company.{field.name} is declared but never read — "
                    "configurable and doing nothing.",
                ))
        return findings

    # -- shape 1: mirror gap ---------------------------------------------
    def untested_corrections(self, code, tests):
        """Every correction path needs a test, or it was built forwards only."""
        findings = []
        for method in CORRECTION_METHODS:
            if not re.search(rf"def {method}\b", code):
                continue
            if re.search(rf"\b{method}\b", tests):
                continue
            if False:
                pass
            findings.append((
                "untested correction",
                f"{method}() exists but no test mentions it — a reverse path "
                "nobody has exercised.",
            ))
        return findings

    # -- shape 4/5: posted documents ------------------------------------
    def mutable_posted_documents(self, labels, sources):
        """A posted document that can still be edited is not posted."""
        findings = []
        for label in labels:
            code = non_test_text(sources[label])
            for model in django_apps.get_app_config(label).get_models():
                names = {f.name for f in model._meta.get_fields()}
                if "posted" not in names:
                    continue
                body = self._class_body(code, model.__name__)
                if body is None:
                    continue
                guard = self._method_body(body, "save")
                if guard is None or "posted" not in guard or "raise" not in guard:
                    findings.append((
                        "mutable posted document",
                        f"{label}.{model.__name__} has a posted flag but no save() "
                        "guard refusing edits once posted.",
                    ))
        return findings

    def unconstrained_numbers(self, labels):
        """
        Drafts all carry an empty number, so a plain unique index collides.
        A numbered document needs the partial constraint or it needs none.
        """
        findings = []
        for label in labels:
            for model in django_apps.get_app_config(label).get_models():
                try:
                    field = model._meta.get_field("number")
                except Exception:
                    continue
                if not isinstance(field, models.CharField) or field.unique:
                    continue
                has_partial = any(
                    isinstance(c, models.UniqueConstraint)
                    and "number" in (c.fields or ())
                    and c.condition is not None
                    for c in model._meta.constraints
                )
                if not has_partial:
                    findings.append((
                        "unconstrained number",
                        f"{label}.{model.__name__}.number has no partial unique "
                        "constraint — two documents can share a number.",
                    ))
        return findings

    # Signed by design, with the reason. Listing them here rather than
    # dropping the check keeps the decision visible.
    SIGNED_BY_DESIGN = {
        "inventory.StockMovement.quantity": "negative is an outbound movement",
        "accounting.BankStatementLine.amount": "negative is money leaving",
        "inventory.StockAdjustmentLine.quantity": "negative writes stock down",
        "inventory.StockTransferStep.quantity": "negative is a hop sent back",
    }

    def unsigned_money(self, labels):
        """An amount that may be negative where nothing expects one."""
        watched = ("amount", "quantity", "unit_price", "quantity_received",
                   "quantity_shipped")
        findings = []
        for label in labels:
            for model in django_apps.get_app_config(label).get_models():
                constrained = " ".join(
                    str(getattr(c, "check", "")) for c in model._meta.constraints
                )
                for field in model._meta.get_fields():
                    if not isinstance(field, models.DecimalField):
                        continue
                    if field.name not in watched or field.null:
                        continue
                    key = f"{label}.{model.__name__}.{field.name}"
                    if key in self.SIGNED_BY_DESIGN:
                        continue
                    if field.name not in constrained:
                        findings.append((
                            "unsigned money",
                            f"{label}.{model.__name__}.{field.name} has no check "
                            "constraint on its sign.",
                        ))
        return findings

    @staticmethod
    def _method_body(body, name):
        """
        The text of one method inside a class body.

        The first version of this check looked for the word "immutable"
        anywhere in the class, which a class could pass by saying so in
        its docstring and doing nothing — the exact failure this codebase
        keeps making. It asks for the guard now: a save() that reads
        `posted` and raises.
        """
        match = re.search(rf"^([ \t]+)def {name}\(", body, re.M)
        if not match:
            return None
        indent = len(match.group(1))
        newline = body.find("\n", match.end())
        if newline == -1:
            return ""
        rest = body[newline + 1:]
        # The method ends at the next line indented no further than its own
        # `def`. Searching from inside the signature instead would match the
        # signature itself and hand back an empty body, which every guard
        # then fails to contain.
        following = re.search(rf"^[ \t]{{0,{indent}}}[^ \t\n]", rest, re.M)
        return rest[: following.start()] if following else rest

    @staticmethod
    def _class_body(code, name):
        match = re.search(rf"^class {name}\(", code, re.M)
        if not match:
            return None
        rest = code[match.start():]
        following = re.search(r"^class ", rest[1:], re.M)
        return rest[: following.start() + 1] if following else rest
