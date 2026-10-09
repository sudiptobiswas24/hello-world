"""
The half of the audit checklist a machine can run.

Everything here corresponds to a shape that has actually produced a
defect in this codebase. Nothing is here because it sounded like good
practice: a check that has never caught anything is noise that trains
people to ignore the output.
"""

import ast
import inspect
import re
import textwrap
from pathlib import Path

from django.apps import apps as django_apps
from django.core.management.base import BaseCommand
from django.db import models

OUR_APPS = (
    "core", "accounting", "inventory", "sales", "purchasing", "hr",
    "assets", "manufacturing", "quality", "planning", "gst",
)

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
        findings += self.movements_in_another_unit(labels, sources)
        findings += self.unlocked_open_runs(labels, sources)
        findings += self.outbound_at_a_posted_figure(labels, sources)
        findings += self.unserialised_state_changes(labels, sources)
        findings += self.untested_corrections(all_code, all_tests)
        findings += self.mutable_posted_documents(labels, sources)
        findings += self.unconstrained_numbers(labels)
        findings += self.unsigned_money(labels)
        findings += self.greenwich_dates(labels, sources)
        findings += self.unsettable_fields(labels)
        findings += self.undeclared_actions(labels)
        findings += self.unscoped_party_reads(labels)
        findings += self.admin_only_rules(labels)
        findings += self.dead_class_attributes(labels, sources)
        findings += self.entries_kept_past_the_edit_guard(labels, sources)
        findings += self.kept_settings_read_live(labels, sources)
        findings += self.corrections_dated_without_the_rule(labels, sources)
        findings += self.posted_as_calculated(labels, sources)
        findings += self.shelf_read_before_lock(labels, sources)
        findings += self.per_unit_withdrawal_rates(labels, sources)
        findings += self.movements_written_around_save(labels, sources)

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
        "planning.parse_month": "called by the CSV importer in apps/imports, which is not audited",
        "core.yes_no": "a CSV cell reader (csvrows.py) the importer in apps/imports uses; the punch file does not",
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

    # Stock movements written in a unit other than their item's, with why
    # the quantity and the cost are both in that unit.
    WRITTEN_IN_ANOTHER_UNIT = {
        "purchasing.GoodsReceipt.post": "the received quantity and the agreed price are both "
                                        "per order-line unit, and the movement restates the pair",
        "purchasing.GoodsReceipt._move_consignment": "at no cost: the unit carries no value",
        "purchasing.draw_consignment": "at no cost: the unit carries no value",
    }

    def movements_in_another_unit(self, labels, sources):
        """
        A stock movement takes its quantity and its cost in the unit it
        names, and restates the pair into the item's stocking unit. Named
        in the document's unit with a cost per stocking unit, the pair is
        restated a factor off: half a tonne of tape went onto the shelf at
        45.86 while the ledger took 45,855.67. A movement is written in its
        item's own unit, or says here why its pair is in another.
        """
        findings, used = [], set()
        for label in labels:
            for path, text in sorted(sources[label].items()):
                if "test" in path.name or "migrations" in path.parts:
                    continue
                if "StockMovement.objects.create" not in text:
                    continue

                def walk(node, scope):
                    for child in ast.iter_child_nodes(node):
                        if isinstance(child, (ast.ClassDef, ast.FunctionDef)):
                            walk(child, scope + [child.name])
                            continue
                        if isinstance(child, ast.Call) and ast.unparse(child.func) == "StockMovement.objects.create":
                            given = {k.arg: ast.unparse(k.value) for k in child.keywords if k.arg}
                            item, uom = given.get("item"), given.get("uom")
                            if uom != f"{item}.uom":
                                key = ".".join([label] + scope)
                                if key in self.WRITTEN_IN_ANOTHER_UNIT:
                                    used.add(key)
                                else:
                                    findings.append((
                                        "movement in another unit",
                                        f"{label}/{path.name}:{child.lineno} writes {item} in {uom}: "
                                        f"write it in {item}.uom, with the quantity and the cost "
                                        "per that unit, or say why here.",
                                    ))
                        walk(child, scope)

                walk(ast.parse(text), [])
        findings += [("stale exemption", f"{key} writes its movements in its item's unit now; "
                                         "take it off WRITTEN_IN_ANOTHER_UNIT.")
                     for key in sorted(set(self.WRITTEN_IN_ANOTHER_UNIT) - used)
                     if key.split(".")[0] in labels]
        return findings

    # Outbound movements that never ask what leaving costs, with why that
    # is right for them.
    OUTBOUND_UNPRICED = {
        "manufacturing._movement": "a customer's own material, held and handed back at no cost",
        "purchasing.draw_consignment": "a vendor's consignment, held at no cost until bought",
        "purchasing.GoodsReceipt._move_consignment": "a vendor's consignment, at no cost",
    }

    def outbound_at_a_posted_figure(self, labels, sources):
        """
        A movement that takes stock off the shelf, written by a function
        that never asks cost_of_removing(). The replay takes off the
        shelf's own figure whatever the movement says, so the ledger has
        to be told that figure: voids that wrote back the posted cost left
        the inventory account 20,855.67, 390.76 and 816.33 away from the
        shelf, and a re-batch's void moved the shelf's value with no
        journal at all.
        """
        findings, used = [], set()
        asks = ("cost_of_removing", "unit_cost_for")
        for label in labels:
            for path, text in sorted(sources[label].items()):
                if "test" in path.name or "migrations" in path.parts:
                    continue
                if "StockMovement.objects.create" not in text:
                    continue

                def walk(node, scope):
                    for child in ast.iter_child_nodes(node):
                        if isinstance(child, (ast.ClassDef, ast.FunctionDef)):
                            walk(child, scope + [child])
                            continue
                        if isinstance(child, ast.Call) and ast.unparse(child.func) == "StockMovement.objects.create":
                            given = {k.arg: ast.unparse(k.value) for k in child.keywords if k.arg}
                            kind = given.get("movement_type", "")
                            function = next((s for s in reversed(scope) if isinstance(s, ast.FunctionDef)), None)
                            if ("ISSUE" in kind or "TRANSFER_OUT" in kind) and function is not None \
                                    and not any(word in ast.unparse(function) for word in asks):
                                key = ".".join([label] + [s.name for s in scope])
                                if key in self.OUTBOUND_UNPRICED:
                                    used.add(key)
                                else:
                                    findings.append((
                                        "outbound at a posted figure",
                                        f"{label}/{path.name}:{child.lineno} {function.name}() takes "
                                        f"{given.get('item')} off the shelf without asking "
                                        "cost_of_removing(): the ledger and the shelf will differ "
                                        "by whatever the shelf has moved.",
                                    ))
                        walk(child, scope)

                walk(ast.parse(text), [])
        findings += [("stale exemption", f"{key} asks what leaving costs now, or no longer takes "
                                         "stock off; take it off OUTBOUND_UNPRICED.")
                     for key in sorted(set(self.OUTBOUND_UNPRICED) - used)
                     if key.split(".")[0] in labels]
        return findings

    def unlocked_open_runs(self, labels, sources):
        """
        A run asked whether it is open by something not holding it. Every
        posting held its run before asking; the voids asked through a
        helper that did not, and a void that read "released" while the
        close summed the run, document and all, committed its reversal
        into work in progress after the close had cleared it. A function
        that asks an order or a run is_open() holds it there: lock_rows(),
        or a select_for_update of its own.
        """
        findings = []
        for label in labels:
            for path, text in sorted(sources[label].items()):
                if "test" in path.name or "migrations" in path.parts or ".is_open()" not in text:
                    continue
                for fn in ast.walk(ast.parse(text)):
                    if not isinstance(fn, ast.FunctionDef):
                        continue
                    body = ast.unparse(fn)
                    if "lock_rows(" in body or "_lock(" in body or "select_for_update" in body:
                        continue
                    for node in ast.walk(fn):
                        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                                and node.func.attr == "is_open"
                                and re.search(r"(order|run)$", ast.unparse(node.func.value))):
                            findings.append((
                                "open run read unheld",
                                f"{label}/{path.name}:{node.lineno} {fn.name}() asks "
                                f"{ast.unparse(node.func.value)} whether it is open without "
                                "holding it: lock_rows() it first, as every posting does.",
                            ))
        return findings

    # Not state changes: the guards themselves, and what Python calls.
    NOT_A_STEP = re.compile(r"^(save|delete|clean|clean_fields|full_clean|validate_\w*|__\w+__)$")
    # State changes that take no row lock of their own, and why that is right.
    SERIALISED_ELSEWHERE = {
        "accounting.PostedTaxDocumentMixin.record_taxes": "the last step of its document's post, which holds the lock",
        "hr.Employee.issue_pin": "the database's unique key on the PIN's digest refuses a shared PIN",
        "inventory.StockAdjustmentLine.post": "called only by its adjustment's post, which holds the adjustment",
        "inventory.StockAdjustmentLine.reverse": "called only by its adjustment's void, which holds the adjustment",
        "inventory.StockTransferLine.move": "called only by its transfer's dispatch and receive, which hold the transfer "
                                            "and lock every shelf it moves between",
        "inventory.StockTransferStep.reverse": "called only by its transfer's cancel, which holds the transfer",
        "manufacturing.Complaint.settle": "the credit note is credit_claim's, which holds the invoice and its room",
        "purchasing.GoodsReceiptLine.advance": "decides only on stock: it holds every shelf on the route before reading "
                                               "what is where, and changes nothing on the line",
        "sales.Invoice.email_to_customer": "decides nothing a send changes: a second press is a second mail",
        "sales.Lead.email_them": "decides nothing a send changes: a second press is a second mail",
        "sales.Delivery.record_receipt": "the customer's own receipt, written as given: a second writes the same fact",
        "sales.Delivery.record_transport": "the lorry's papers, written as given: a second writes the same fact",
        "sales.Quotation.accept": "its conversion, _convert_to_order, holds the quote and asks again",
    }

    def unserialised_state_changes(self, labels, sources):
        """
        A step a record takes that reads its state and then writes, with
        no row lock.

        Two people pressing the same button both read "not yet" and both
        go: two orders from one accepted quote, two backorders from one
        delivery, a transfer's stock taken off the shelf twice, a
        settlement discount written off twice. A test suite is single
        threaded and never sees it, so this asks the code: a state change
        must be @serialised, take lock_rows itself, or say here why not.
        """
        findings, exempted = [], set()
        for label in labels:
            for path, text in sorted(sources[label].items()):
                if "test" in path.name or "migrations" in path.parts or "management" in path.parts:
                    continue
                tree = ast.parse(text)
                for cls in (node for node in tree.body if isinstance(node, ast.ClassDef)):
                    bases = {getattr(base, "id", getattr(base, "attr", "")) for base in cls.bases}
                    if not any(base in ("AuditModel", "Model") or base.endswith(("Model", "Mixin")) for base in bases) \
                            or any(base.endswith(("ViewSet", "Serializer", "Admin")) for base in bases):
                        continue
                    methods = {node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)}
                    text_of = {name: ast.unparse(node) for name, node in methods.items()}

                    def locked(name):
                        decorators = {ast.unparse(d.func if isinstance(d, ast.Call) else d)
                                      for d in methods[name].decorator_list}
                        # Not lock_positions: it holds shelves, not the record, and dispatch() read its own
                        # status before taking it.
                        return "serialised" in decorators or "lock_rows(" in text_of[name]

                    # What writes with no lock, counting what a method hands to the class's own
                    # helpers: dispatch() wrote nothing itself and everything through
                    # _move_out(), and was missed for it. A helper that locks is a locked write,
                    # whoever calls it: reject() decides nothing _decide() does not ask again.
                    writes = {name for name in methods if not locked(name) and re.search(
                        r"\.(save|create|update|post|delete|bulk_create)\(", text_of[name])}
                    grew = True
                    while grew:
                        grew = False
                        for name in methods:
                            if name not in writes and not locked(name) and any(
                                    re.search(rf"\bself\.{callee}\(", text_of[name]) for callee in writes):
                                writes.add(name)
                                grew = True
                    for fn in methods.values():
                        # Every public step, whatever it is called: a list of verbs let commit() by.
                        # A private helper is asked through the steps that call it.
                        if fn.name.startswith("_") or self.NOT_A_STEP.match(fn.name):
                            continue
                        decorators = {ast.unparse(d.func if isinstance(d, ast.Call) else d) for d in fn.decorator_list}
                        if decorators & {"property", "classmethod", "staticmethod"}:
                            continue
                        # Decides (refuses on what it read) and writes: a step that only adds a
                        # line decides nothing, and the line's own save guards it.
                        if fn.name not in writes or "raise " not in text_of[fn.name]:
                            continue
                        key = f"{label}.{cls.name}.{fn.name}"
                        if key in self.SERIALISED_ELSEWHERE:
                            exempted.add(key)
                            continue
                        findings.append((
                            "unserialised state change",
                            f"{key} ({path.name}:{fn.lineno}) decides and writes with no row lock — two "
                            "presses of the same button both go. @serialised it, lock_rows, or say why not.",
                        ))
        # And a reason outlives nothing: an exemption for a step no longer found is taken off the list.
        findings += [("stale exemption", f"{key} is exempted from the row lock but is no longer a step that needs one.")
                     for key in sorted(set(self.SERIALISED_ELSEWHERE) - exempted)
                     if key.split(".")[0] in labels]
        return findings

    # Fields the API deliberately does not take, with the reason. Anything
    # else a model lets a person set and a serializer leaves out is dropped
    # by DRF without a word.
    SET_BY_THE_SYSTEM = {
        "inventory.Item.template": "variants are generated from their template",
        "manufacturing.FabricRoll.station": "the station that weighed it says so",
        "manufacturing.FabricRoll.weighed_by": "the operator's PIN at the station",
        "manufacturing.FabricRoll.weighed_at": "the station's clock",
        "manufacturing.FabricRoll.woven_by": "the weaver's PIN at the station",
        "manufacturing.FabricRoll.shift": "derived from the moment it was weighed",
        "manufacturing.FabricRoll.shift_date": "derived from the moment it was weighed",
        "manufacturing.FabricRoll.core_type": "the station's tare, chosen there",
        "manufacturing.FabricRoll.weight_source": "the station records how it weighed",
        "manufacturing.FabricRoll.approved_by": "a supervisor's PIN at the station",
        "manufacturing.FabricRoll.override_reason": "given with that PIN at the station",
        "manufacturing.FabricRoll.override_note": "given with that PIN at the station",
        "purchasing.BillLine.debits_line": "set by the debit note that corrects it",
        "purchasing.GoodsReceiptLine.reverses_line": "set by the return",
        "sales.DeliveryLine.reverses_line": "set by the return",
        "purchasing.PurchaseOrderLine.requisition_line": "set when a requisition is ordered",
        "purchasing.PurchaseOrderLine.sales_order_line": "set by drop-shipping a sale",
        "purchasing.PurchaseOrderLine.blanket_line": "set when a blanket order is called off",
        "core.SavedFilter.user": "a kept view is its keeper's: whoever is signed in",
    }

    def unsettable_fields(self, labels):
        """
        A field a person may set on the model that the API drops.

        DRF ignores a field its serializer does not list, without a word.
        A leave request made over the API never named its allowance, and
        nobody's balance went down; a receipt could not name the lot it
        took in. Found by review, 26 models at once.
        """
        from django.urls import get_resolver

        serializers = {}

        def walk(patterns):
            for pattern in patterns:
                if hasattr(pattern, "url_patterns"):
                    walk(pattern.url_patterns)
                    continue
                view = getattr(pattern.callback, "cls", None)
                actions = getattr(pattern.callback, "actions", None) or {}
                serializer = getattr(view, "serializer_class", None) if view else None
                if serializer is not None and "post" in actions:
                    serializers[serializer] = True

        walk(get_resolver().url_patterns)
        findings = []
        skip = {"id", "created_at", "updated_at", "created_by", "updated_by"}
        for serializer in serializers:
            model = getattr(getattr(serializer, "Meta", None), "model", None)
            if model is None or model._meta.app_label not in labels:
                continue
            declared = set(serializer().fields)
            for field in model._meta.fields:
                key = f"{model._meta.label}.{field.name}"
                if (field.editable and not field.primary_key and field.name not in skip
                        and field.name not in declared and key not in self.SET_BY_THE_SYSTEM):
                    findings.append((
                        "unsettable field",
                        f"{key} can be set on the model but {serializer.__name__} drops it "
                        "without a word.",
                    ))
        return findings

    WRITES = {"post", "put", "patch", "delete"}

    def undeclared_actions(self, labels, viewsets=None):
        """
        An @action that writes, on a viewset ModelPermissions guards, that
        its action_permission_map does not name.

        DRF maps every POST to add_<model>. Fifteen actions on one record
        named nothing and so took only that: the Inspector voided the
        calibration that had put their own inspection in doubt, and anyone
        who could ask for leave cancelled anyone's. Unnamed, an action now
        takes change_<model> (core.permissions); this asks its author to
        say which, as a decision usually has a right of its own.
        """
        from apps.core.permissions import ModelPermissions

        findings = []
        for view in self._routed_viewsets() if viewsets is None else viewsets:
            queryset = getattr(view, "queryset", None)
            if queryset is None or queryset.model._meta.app_label not in labels:
                continue
            if not any(isinstance(cls, type) and issubclass(cls, ModelPermissions)
                       for cls in getattr(view, "permission_classes", ())):
                continue
            declared = getattr(view, "action_permission_map", {})
            for extra in view.get_extra_actions():
                writes = {method for method in extra.mapping if method in self.WRITES}
                named = declared.get(extra.__name__)
                if isinstance(named, dict):
                    writes -= {method.lower() for method, permission in named.items() if permission}
                elif named:
                    writes = set()
                if writes:
                    findings.append((
                        "action without a declared permission",
                        f"{view.__module__}.{view.__name__}.{extra.__name__} writes ({', '.join(sorted(writes))}) "
                        "and action_permission_map names no permission for it: say which.",
                    ))
        return findings

    # Read unscoped on purpose or reported to the module that owns it, with
    # the reason. An entry the check no longer needs is reported as stale.
    UNSCOPED_PARTY_READS_REPORTED = {
        "inventory.WarehouseViewSet": "stores: a warehouse held for a customer (held_for) names them to every rep",
    }
    SCOPE_WORDS = ("scoped(", "carried_by(", "for_rep(", "rep_limit(")
    SCOPE_MIXINS = ("CustomerScopedMixin", "OwnedMixin")

    def unscoped_party_reads(self, labels, viewsets=None, readers=None):
        """
        A viewset that reads a model naming a party (a foreign key to
        core.Party) which the Sales Rep may view, with no party scope in
        its queryset.

        A rep sees their own customers and nobody else's, and the rule
        held only where someone remembered it: a rep read another rep's
        customer's PAN in the party tax profiles, and the notes, files and
        history hung on one, which ask the same queryset. `readers` is the
        rep's permissions (setup_roles' Sales Rep).
        """
        from apps.core.models import Party

        if readers is None:
            from apps.core.management.commands.setup_roles import ROLES

            readers = set(ROLES["Sales Rep"])
        findings, seen = [], set()
        for view in self._routed_viewsets() if viewsets is None else viewsets:
            queryset = getattr(view, "queryset", None)
            if queryset is None or not (hasattr(view, "list") or hasattr(view, "retrieve")):
                continue
            meta = queryset.model._meta
            if meta.app_label not in labels or f"{meta.app_label}.view_{meta.model_name}" not in readers:
                continue
            if not any(field.is_relation and field.related_model is Party for field in meta.fields):
                continue
            key = f"{view.__module__.split('.')[1]}.{view.__name__}"
            if self._scoped_view(view):
                continue
            seen.add(key)
            if key in self.UNSCOPED_PARTY_READS_REPORTED:
                continue
            findings.append((
                "party read past the rep scope",
                f"{key} serves {meta.label}, which names a party and which a rep may view, with no party scope "
                "in its queryset (apps/core/scoping.py scoped()).",
            ))
        if viewsets is None:
            for key in sorted(key for key in set(self.UNSCOPED_PARTY_READS_REPORTED) - seen
                              if key.split(".")[0] in labels):
                findings.append(("stale exemption", f"{key} is scoped now, or gone: take it off the list."))
        return findings

    def _scoped_view(self, view):
        if any(cls.__name__ in self.SCOPE_MIXINS for cls in view.__mro__):
            return True
        for cls in view.__mro__:
            if "get_queryset" in cls.__dict__:
                source = inspect.getsource(cls.__dict__["get_queryset"])
                return any(word in source for word in self.SCOPE_WORDS)
        return False

    @staticmethod
    def _routed_viewsets():
        from django.urls import get_resolver

        found = []

        def walk(patterns):
            for pattern in patterns:
                if hasattr(pattern, "url_patterns"):
                    walk(pattern.url_patterns)
                    continue
                view = getattr(pattern.callback, "cls", None)
                if view is not None and hasattr(view, "get_extra_actions") and view not in found:
                    found.append(view)

        walk(get_resolver().url_patterns)
        return found

    # What reads a shelf to decide on it, and what holds the shelf.
    SHELF_READS = {"on_hand_at", "available_at", "reserved_at", "check_available", "cost_of_removing"}
    SHELF_LOCKS = {"lock_position", "lock_positions"}
    # Reads made before the lock on purpose, with the reason.
    READ_BEFORE_LOCK_ON_PURPOSE = {}

    def shelf_read_before_lock(self, labels, sources):
        """
        A function that holds a shelf and read it first.

        The lock is taken so two documents cannot both read the same
        shelf, decide, and write; a read before it decides on what the
        other may already be changing. A delivery checked what was on hand
        and free and only then held the shelf, its comment saying it held
        it before anything read: two orders each shipped the last ten.
        Asked of each function that takes the lock, by where its first
        read and its first lock are.
        """
        findings = []
        for label in labels:
            for path, text in sorted(sources[label].items()):
                if "test" in path.name or "migrations" in path.parts or "management" in path.parts:
                    continue
                for fn in ast.walk(ast.parse(text)):
                    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    reads, locks = [], []
                    for node in ast.walk(fn):
                        if isinstance(node, ast.Call):
                            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                            if name in self.SHELF_READS:
                                reads.append(node.lineno)
                            elif name in self.SHELF_LOCKS:
                                locks.append(node.lineno)
                    key = f"{label}/{path.name}:{fn.name}"
                    if reads and locks and min(reads) < min(locks) and key not in self.READ_BEFORE_LOCK_ON_PURPOSE:
                        findings.append((
                            "shelf read before its lock",
                            f"{key} reads the shelf at line {min(reads)} and holds it only at line "
                            f"{min(locks)}; two at once both decide on what they read. Lock first.",
                        ))
        return findings

    # Who may still ask for a withdrawal's cost per unit, and why.
    PER_UNIT_RATE_ALLOWED = {
        "inventory/costing.py": "where the rate is defined",
        "inventory/models.py": "the Item method that hands the rate on",
        "manufacturing/bom.py": "a planned cost: nothing is booked or moved at it",
    }

    def per_unit_withdrawal_rates(self, labels, sources):
        """
        A withdrawal priced at a rate rather than at the total it takes.

        cost_of_removing() is a total: under FIFO the units span layers,
        under specific identification the batch decides, and a rate
        multiplied back up is not what the shelf gives up. Clearing three
        cases out of inspection at a rate for three eaches turned 960 of
        stock into 840; a write-down at a four-place rate booked 3,333.30
        where the shelf gave up 3,333.33. A move between shelves is
        move_stock(); a posting books the total.
        """
        findings = []
        for label in labels:
            for path, text in sorted(sources[label].items()):
                if "test" in path.name or "migrations" in path.parts or "management" in path.parts:
                    continue
                key = f"{label}/{path.name}"
                if key in self.PER_UNIT_RATE_ALLOWED:
                    continue
                for match in re.finditer(r"\b(removal_unit_cost|unit_cost_for)\(", text):
                    line = text.count("\n", 0, match.start()) + 1
                    findings.append((
                        "per-unit withdrawal rate",
                        f"{key}:{line} asks {match.group(1)}(); book what cost_of_removing() "
                        "says leaving takes, the total, or move it with move_stock().",
                    ))
        return findings

    def movements_written_around_save(self, labels, sources):
        """
        Stock movements written or changed without StockMovement.save().

        Its save() is where every movement is restated in the stocking unit
        and refused below zero, out of a batch or a bin that has not got
        it, unless the warehouse allows it; a bulk insert or a queryset
        update goes round all of it, and round the append-only rule.
        """
        findings = []
        shape = re.compile(r"StockMovement\.objects\b[^\n]*\.(bulk_create|bulk_update|update)\(")
        for label in labels:
            for path, text in sorted(sources[label].items()):
                if "test" in path.name or "migrations" in path.parts or "management" in path.parts:
                    continue
                for match in shape.finditer(text):
                    line = text.count("\n", 0, match.start()) + 1
                    findings.append((
                        "movement written around save",
                        f"{label}/{path.name}:{line} writes stock movements with {match.group(1)}(), "
                        "past the checks StockMovement.save() makes. Create them one by one.",
                    ))
        return findings

    # Files whose matches are not moments in UTC, with the reason.
    LOCAL_ALREADY = {
        "manufacturing/dispatch.py": "build() turns start_at into naive plant time first",
        "core/audit_invariants.py": "the check's own description",
    }

    GREENWICH = re.compile(r"\bnow\(\)\.date\(\)|\b\w+_at\.date\(\)")

    def greenwich_dates(self, labels, sources):
        """
        A date taken from a moment in UTC rather than at the plant.

        `timezone.now().date()` and `posted_at.date()` read the day in
        Greenwich: in Kolkata everything between midnight and 05:30 lands
        on the day before. A test suite running on UTC cannot see it, so
        it is checked here. The plant's day is `timezone.localdate()`, or
        `to_date()` of the moment.
        """
        findings = []
        for label in labels:
            for path, text in sorted(sources[label].items()):
                if "test" in path.name or "migrations" in path.parts:
                    continue
                if f"{label}/{path.name}" in self.LOCAL_ALREADY:
                    continue
                for number, line in enumerate(text.splitlines(), 1):
                    if self.GREENWICH.search(line):
                        findings.append((
                            "greenwich date",
                            f"{label}/{path.name}:{number} takes the UTC day of a moment; "
                            "use timezone.localdate() or to_date().",
                        ))
        return findings

    def dead_class_attributes(self, labels, sources):
        """
        A class body that says one name twice keeps only the second, and
        whatever the first said is not so. The journal entry serializer
        assigned its read-only fields twice, the second without the ones
        the first protected, so an entry made by hand could claim to
        reverse a payment's and block its void; a viewset lost an action's
        permission the same way. A test class that names two tests alike
        runs one of them.
        """
        findings = []
        for label in labels:
            for path, text in sorted(sources[label].items()):
                if "migrations" in path.parts:
                    continue
                for node in ast.walk(ast.parse(text)):
                    if not isinstance(node, ast.ClassDef):
                        continue
                    said = {}
                    for statement in node.body:
                        for name in self._names_bound(statement):
                            if name in said:
                                findings.append((
                                    "dead class attribute",
                                    f"{label}/{path.name}:{said[name]} {node.name}.{name} is said again at line "
                                    f"{statement.lineno}, and only the second counts.",
                                ))
                            said[name] = statement.lineno
        return findings

    @staticmethod
    def _names_bound(statement):
        if isinstance(statement, ast.Assign):
            return [target.id for target in statement.targets if isinstance(target, ast.Name)]
        if isinstance(statement, ast.AnnAssign) and statement.value is not None and isinstance(statement.target, ast.Name):
            return [statement.target.id]
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # A property's setter, and an overload, name the function again on purpose.
            for decorator in statement.decorator_list:
                if isinstance(decorator, ast.Attribute) and decorator.attr in ("setter", "deleter", "getter"):
                    return []
                if (decorator.id if isinstance(decorator, ast.Name) else getattr(decorator, "attr", "")) == "overload":
                    return []
            return [statement.name]
        return []

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
    # -- shape 1, mirror gap between the admin and everywhere else ----------
    # Rules clean() asks that save() leaves alone on purpose, with why.
    ADMIN_ONLY_ON_PURPOSE = {
        "accounting.JournalLine": "a manual line is refused by its serializer, both sides at once by the "
                                  "database; posting a zero-value document writes a zero line on purpose",
    }

    def admin_only_rules(self, labels):
        """
        A model whose clean() states a rule its save() never runs.

        Django's admin calls full_clean(); DRF's serializers and the code
        that builds documents never do. A probe found 33 rules held only
        in the admin: an order to a party that is no customer, a delivery
        line off another order, an account under itself. clean() may call
        checks of its own; each must be one save() calls too, or save()
        must call clean() itself.
        """
        findings = []
        for label in labels:
            for model in django_apps.get_app_config(label).get_models():
                clean = model.__dict__.get("clean")
                if clean is None or f"{label}.{model.__name__}" in self.ADMIN_ONLY_ON_PURPOSE:
                    continue
                saved = set()
                for cls in model.__mro__:
                    if cls is models.Model:
                        break
                    if "save" in cls.__dict__:
                        saved = self._self_calls(cls.__dict__["save"])
                        break
                if saved & {"clean", "full_clean"}:
                    continue
                body = ast.parse(textwrap.dedent(inspect.getsource(clean))).body[0].body
                body = [node for node in body
                        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))]
                if body and all(self._is_self_call(node, saved) for node in body):
                    continue
                findings.append((
                    "rule only the admin runs",
                    f"{label}.{model.__name__}.clean() asks something its save() does not: "
                    "move it into a check both call.",
                ))
        return findings

    @staticmethod
    def _self_calls(function):
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        return {node.func.attr for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"}

    @staticmethod
    def _is_self_call(node, saved):
        return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and isinstance(node.value.func.value, ast.Name) and node.value.func.value.id == "self"
                and node.value.func.attr in saved)

    # Posted documents still deletable, found when the check was written
    # and reported to the module that owns each rather than fixed from
    # outside it. Each comes off when its guard lands: an entry the check
    # no longer needs is reported as stale.
    DELETABLE_REPORTED = {
        "inventory.StockAdjustment": "stores: its movements and entry would stay",
        "inventory.StockCount": "stores: a sheet with no difference is the only evidence",
        "quality.Inspection": "quality: the batch would read as never inspected",
    }

    def mutable_posted_documents(self, labels, sources):
        """
        A posted document that can still be edited is not posted, and nor
        is one that can still be deleted: its stock movements and its
        entry stay, with nothing left to say why. Its lines refusing their
        own delete does not cover it, because they go with it by cascade,
        which never asks them. A supervisor deleted a posted issue, entry
        and booking over the API, and only save() was being asked here.
        """
        findings, reported = [], set()
        for label in labels:
            code = non_test_text(sources[label])
            for model in django_apps.get_app_config(label).get_models():
                # Posted is a flag on some documents and a status on others
                # (a pay run, a remittance). Either can be deleted; the
                # edit guard is asked of the flag, which is all it reads.
                flagged = "posted" in {f.name for f in model._meta.get_fields()}
                status = next((f for f in model._meta.fields if f.name == "status"), None)
                if not flagged and not (status is not None and "posted" in dict(status.choices or ())):
                    continue
                body = self._class_body(code, model.__name__)
                if body is None:
                    continue
                key = f"{label}.{model.__name__}"
                for method, shape, refusing in (
                    ("save", "mutable posted document", "edits"),
                    ("delete", "deletable posted document", "deletion"),
                ):
                    if method == "save" and not flagged:
                        continue
                    if self._guards_posted(model, code, method):
                        continue
                    if method == "delete" and key in self.DELETABLE_REPORTED:
                        reported.add(key)
                        continue
                    findings.append((
                        shape,
                        f"{key} can be posted but has no {method}() guard refusing "
                        f"{refusing} once posted.",
                    ))
        findings += [("stale exemption", f"{key} refuses deletion now; take it off DELETABLE_REPORTED.")
                     for key in sorted(set(self.DELETABLE_REPORTED) - reported)
                     if key.split(".")[0] in labels]
        return findings

    def posted_as_calculated(self, labels, sources):
        """
        A document calculated and then posted, whose post() does not work it
        out again. Everything a calculation read can move before it posts:
        June's run was posted at 4,400.00 after unpaid leave made it
        3,400.00, and two fortnights calculated before either posted both
        paid September's piece work. post() asks again by calling
        check_current(), work_out() or calculate().
        """
        findings = []
        for label in labels:
            documents = {model.__name__ for model in django_apps.get_app_config(label).get_models()}
            for path, text in sorted(sources[label].items()):
                if "test" in path.name or "migrations" in path.parts:
                    continue
                for owner in ast.walk(ast.parse(text)):
                    if not isinstance(owner, ast.ClassDef) or owner.name not in documents:
                        continue
                    methods = {node.name: node for node in owner.body if isinstance(node, ast.FunctionDef)}
                    if "calculate" not in methods or "post" not in methods:
                        continue
                    body = ast.get_source_segment(text, methods["post"]) or ""
                    if not any(call in body for call in ("check_current(", "work_out(", "calculate(")):
                        findings.append((
                            "posted as calculated",
                            f"{label}.{owner.name}.post ({path.name}:{methods['post'].lineno}) posts what "
                            "calculate() worked out without working it out again.",
                        ))
        return findings

    def _guards_posted(self, model, code, method):
        # The guard may be inherited from an abstract base in the same
        # app; a document is guarded if any class it is built from
        # carries one. Each still has to say posted and raise.
        for cls in model.__mro__:
            if cls is models.Model:
                break
            found = self._class_body(code, cls.__name__)
            guard = found and self._method_body(found, method)
            if guard and "posted" in guard and "raise" in guard:
                return True
        return False

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
        "inventory.StockValuationSnapshot.quantity": (
            "a fold of movements that may have gone below zero"
        ),
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

    # -- shape 1: a draft still editable after it posted something -------
    def entries_kept_past_the_edit_guard(self, labels, sources):
        """
        An entry a record keeps, set by a step that leaves its status alone.

        A record's save() guard is usually keyed to its status: a draft may
        change, an issued one may not. A fixed asset capitalised from a bill
        keeps the entry that put its cost on the asset account and stays a
        draft, so the guard let a capitalised draft be repriced to 15,000
        against 12,000 posted, or moved off the account it was posted to.
        Asked of the code: wherever an entry such a record keeps is set
        without its status changing in the same step, its save() must name
        that entry, or what the entry posted can be edited away.
        """
        from apps.accounting.models import JournalEntry

        steps = []
        for label in OUR_APPS:
            for path, text in sorted(sources[label].items()):
                if "test" in path.name or "migrations" in path.parts or "management" in path.parts:
                    continue
                for node in ast.parse(text).body:
                    owner = node.name if isinstance(node, ast.ClassDef) else ""
                    functions = node.body if isinstance(node, ast.ClassDef) else [node]
                    for fn in functions:
                        if not isinstance(fn, ast.FunctionDef):
                            continue
                        assigned = {target.attr for statement in ast.walk(fn) if isinstance(statement, ast.Assign)
                                    for each in statement.targets
                                    for target in (each.elts if isinstance(each, ast.Tuple) else [each])
                                    if isinstance(target, ast.Attribute)}
                        steps.append((path, owner, fn.name, ast.unparse(fn), assigned))
        findings = []
        for label in labels:
            code = non_test_text(sources[label])
            for model in django_apps.get_app_config(label).get_models():
                if "status" not in {field.name for field in model._meta.fields}:
                    continue
                kept = [field.name for field in model._meta.fields
                        if field.is_relation and field.related_model is JournalEntry]
                body = self._class_body(code, model.__name__) or ""
                guard = self._method_body(body, "save") or ""
                for name in kept:
                    if re.search(rf"\b{name}(_id)?\b", guard):
                        continue
                    for path, owner, function, text, assigned in steps:
                        if owner != model.__name__ and not re.search(rf"\b{model.__name__}\b", text):
                            continue
                        if name in assigned and "status" not in assigned:
                            findings.append((
                                "draft that posted",
                                f"{label}.{model.__name__}.{name} is set by {owner + '.' if owner else ''}"
                                f"{function} ({path.name}) with the status left as it was, and save() "
                                "never names it: what that entry posted can still be edited.",
                            ))
                            break
        return findings

    # -- shape 4: a setting read live by a later posting ------------------
    def kept_settings_read_live(self, labels, sources):
        """
        A field a record keeps from a related one, read through the relation instead.

        A fixed asset's disposal read its category's accounts as they stood that
        day: the category moved to new ones, and the disposal took 12,000 off an
        account that never held the lathe while the old plant account kept it for
        good. The asset now keeps what it stands on, and says so in `KEPT_FROM`
        ({relation: (field, ...)}); reading one of those through the relation is
        reading what the next record gets. Any record that keeps copies can
        declare them and be held to it.
        """
        findings = []
        code = "\n".join(non_test_text({path: text for path, text in sources[label].items()
                                        if "migrations" not in path.parts}) for label in OUR_APPS)
        for label in labels:
            for model in django_apps.get_app_config(label).get_models():
                for relation, names in (getattr(model, "KEPT_FROM", None) or {}).items():
                    for name in names:
                        for match in re.finditer(rf"\.{relation}\.{name}\b", code):
                            line = code[:match.start()].count("\n") + 1
                            findings.append((
                                "kept setting read live",
                                f"{label}.{model.__name__} keeps its own {name}, but "
                                f"`{code.splitlines()[line - 1].strip()[:90]}` reads its {relation}'s.",
                            ))
        return findings

    # -- shape 1: a correction dated before what it corrects, or ahead ------
    # Steps that reverse an entry on a day they are given and do not yet ask
    # correction_date(), with why. Each is a step another fix owns; the list
    # only shrinks, as a step moved onto the rule makes its line stale.
    NOT_YET_ON_THE_RULE = "another module's step, not yet on correction_date(): named in the fixed-asset fix's report"
    DATED_ELSEWHERE = {
        "accounting.Payment.void": "refuses a day before the payment and a day to come in its own words, "
                                   "written before the rule",
        **dict.fromkeys((
            "accounting.BankStatementLine.reverse_posting", "inventory.StockAdjustment.void",
            "sales.Invoice.recover_write_off", "sales.InvoicePayment.release_exchange_difference",
            "sales.CustomerTds.reverse", "purchasing.BillPayment.release_exchange_difference",
            "purchasing.TdsDeduction.reverse", "purchasing.TdsChallan.void",
            "manufacturing.WorkOrder.reopen", "manufacturing.MaterialIssue.void",
            "manufacturing.ProductionEntry.void", "manufacturing.TimeBooking.void",
            "manufacturing.OutsideMovement.void"), NOT_YET_ON_THE_RULE),
    }

    # An entry is taken back by create_reversal(), or written by hand
    # naming the entry it undoes: the landed-cost release posts its own.
    REVERSES = re.compile(r"\.create_reversal\(|\b(reverses|undoing)=")

    def corrections_dated_without_the_rule(self, labels, sources):
        """
        A step that reverses an entry on the day it is given, never asking correction_date().

        A lathe bought on 1 January was disposed of on 15 December before, and its
        capitalisation undone on 1 December: the plant account stood at -12,000 over the
        year end. Dated on a day still to come, a correction has the document read done
        while the books do not. apps.core.models.correction_date() refuses both, and a step
        that takes `on_date` and reverses an entry asks it, or says here why not.
        """
        findings, exempted = [], set()
        for label in labels:
            for path, text in sorted(sources[label].items()):
                if "test" in path.name or "migrations" in path.parts or "management" in path.parts:
                    continue
                for cls in (node for node in ast.walk(ast.parse(text)) if isinstance(node, ast.ClassDef)):
                    for fn in (node for node in cls.body if isinstance(node, ast.FunctionDef)):
                        body = ast.unparse(fn)
                        if "on_date" not in {arg.arg for arg in fn.args.args + fn.args.kwonlyargs} \
                                or not self.REVERSES.search(body) or "correction_date(" in body:
                            continue
                        key = f"{label}.{cls.name}.{fn.name}"
                        if key in self.DATED_ELSEWHERE:
                            exempted.add(key)
                            continue
                        findings.append((
                            "correction dated anywhere",
                            f"{key} ({path.name}:{fn.lineno}) reverses an entry on the day it is given and "
                            "never asks correction_date(): before what it takes back, or on a day to come, "
                            "the document and the books disagree.",
                        ))
        findings += [("stale exemption", f"{key} is exempted from correction_date() but is no longer a step "
                                         "that needs it.")
                     for key in sorted(set(self.DATED_ELSEWHERE) - exempted) if key.split(".")[0] in labels]
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
        match = re.search(rf"^class {name}[(:]", code, re.M)
        if not match:
            return None
        rest = code[match.start():]
        following = re.search(r"^class ", rest[1:], re.M)
        return rest[: following.start() + 1] if following else rest
