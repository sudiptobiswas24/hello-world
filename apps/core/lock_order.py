"""
The lock order as data, and under tests a sentinel that holds every
transaction to it.

apps.core.models writes the order down in words beside lock_rows(). Two
deadlocks got past it after it was written (O137, O169), each found by a
reviewer one pair at a time: a path took an order after a document, or a
second order after the stock it had moved. Here the order is a list, and
with settings.LOCK_ORDER_SENTINEL on (the test settings; never production)
every row a transaction locks is ranked as it is taken: lock_rows(),
lock_in_turn(), @serialised and any other select_for_update(). Taking a
row of an earlier rank after a later one raises LockOrderViolation, and
so does taking a row of one model after a later row of the same model:
two orders, an invoice and the note on it, are taken in key order.

A row already held is not taken again, so re-locking it is never a
violation; nor is a row this transaction made, which no other can see
until it commits (the second note of a return that debits two bills). A
model not in LOCK_ORDER is not checked: UNRANKED says why each one seen
locking beside these is left out.
"""
import json
import os
import threading
import traceback

# Earliest first; the position is the rank. Each model is its own rank, and
# rows of one model are taken in key order.
LOCK_ORDER = (
    # 1. a purchase order, then a sales order (a drop-ship receipt ships
    #    the customer's order)
    "purchasing.purchaseorder",
    "sales.salesorder",
    # 2. an order's lines
    "purchasing.purchaseorderline",
    "sales.salesorderline",
    # 3. the customer whose credit limit is weighed
    "core.party",
    # 4. the documents that move against an order: receipts, then
    #    deliveries (a drop-ship receipt makes the delivery), then bills,
    #    then invoices. A return is a delivery or receipt, a note a bill or
    #    invoice, and is made after the document it corrects: key order.
    "purchasing.goodsreceipt",
    "sales.delivery",
    "purchasing.bill",
    "sales.invoice",
    # 5. those documents' lines
    "purchasing.goodsreceiptline",
    "sales.deliveryline",
    "purchasing.billline",
    "sales.invoiceline",
)

RANK = {label: rank for rank, label in enumerate(LOCK_ORDER)}

# Seen locking in the same transactions as the models above (the survey of
# the suite, 10 October), and left unranked, each for its reason. Ranking
# one makes every path that takes it beside a trading document answer to
# the sentinel: the next to do is inventory.stockposition, seen both
# before and after sales orders, purchase orders, receipts and bill lines.
UNRANKED = {
    "inventory.stockposition": "taken both ways round the documents; open: docs/RISKS.md",
    "accounting.payment": "taken before the invoice or bill it settles, by every path seen; rank with the next audit",
    "accounting.journalentry": "a row the posting makes in the same transaction, then locked; a reversal takes its own",
    "core.documentsequence": "one row per document type, taken last by the document numbering it; ranks by type",
    "accounting.accountingperiod": "read under lock as a date check, never written by these paths",
    "accounting.account": "a control account, locked by payroll and banking paths, not trading ones",
}


class LockOrderViolation(AssertionError):
    """A transaction took a row the written lock order puts before one it already held."""


def _state(connection):
    state = getattr(connection, "_lock_order_state", None)
    if state is None:
        state = connection._lock_order_state = {"held": set(), "top": None, "taken": []}
    return state


def _in_a_transaction_of_its_own(connection):
    """Inside an atomic block the code opened, not only a TestCase's own."""
    return any(not getattr(block, "_from_testcase", False) for block in connection.atomic_blocks)


def _where():
    frames = [f for f in traceback.extract_stack()[:-3]
              if "/apps/" in f.filename and "lock_order.py" not in f.filename and "/tests" not in f.filename]
    return " < ".join(f"{f.filename.split('/apps/')[-1]}:{f.lineno} {f.name}" for f in reversed(frames[-4:]))


_SURVEY = os.environ.get("LOCK_ORDER_SURVEY")
_seen = set()
_survey_lock = threading.Lock()


def _survey(kind, *key):
    """Record a pair once per process (settings LOCK_ORDER_SURVEY=dir): what the ranks are decided from."""
    with _survey_lock:
        if (kind,) + key in _seen:
            return
        _seen.add((kind,) + key)
        with open(os.path.join(_SURVEY, f"{os.getpid()}.jsonl"), "a") as out:
            out.write(json.dumps([kind, *key, _where()]) + "\n")


def taken(connection, label, pks):
    """Rank the rows `label` `pks` (None: not known) just locked on `connection`."""
    if not connection.in_atomic_block or not _in_a_transaction_of_its_own(connection):
        return
    state = _state(connection)
    for pk in (pks if pks is not None else [None]):
        if (label, pk) in state["held"]:
            continue
        rank = RANK.get(label)
        if _SURVEY:
            for earlier in dict.fromkeys(state["taken"]):
                if earlier != label:
                    _survey("pair", earlier, label)
        if pk is not None:
            state["held"].add((label, pk))
        state["taken"].append(label)
        if rank is None:
            continue
        top = state["top"]
        mine = (rank, pk if pk is not None else 0)
        if top is not None and (rank < top[0] or (pk is not None and rank == top[0] and pk < top[1])):
            message = (f"{label} {pk} locked after {LOCK_ORDER[top[0]]} {top[1]}: "
                       f"against the lock order in apps.core.lock_order")
            if _SURVEY:
                _survey("violation", LOCK_ORDER[top[0]], label)
                continue
            raise LockOrderViolation(message)
        if top is None or mine > top:
            state["top"] = mine


def forget(connection):
    connection._lock_order_state = None


def install():
    """Rank every select_for_update() and forget at each transaction's end: tests only."""
    from django.db import transaction
    from django.db.models.sql import compiler
    from django.db.models.sql.constants import MULTI, SINGLE

    if getattr(compiler.SQLCompiler, "_lock_order_installed", False):
        return
    execute_sql = compiler.SQLCompiler.execute_sql

    def checked_execute_sql(self, result_type=MULTI, *args, **kwargs):
        result = execute_sql(self, result_type, *args, **kwargs)
        if not self.query.select_for_update or result_type not in (MULTI, SINGLE):
            return result
        pk_field, base = self.query.model._meta.pk, self.query.get_initial_alias()
        index = next((i for i, (expr, _sql, _alias) in enumerate(self.select or ())
                      if getattr(expr, "target", None) is pk_field and getattr(expr, "alias", None) == base), None)
        pks = None
        if result_type == MULTI:
            result = list(result)
            if index is not None:
                pks = [row[index] for chunk in result for row in chunk]
        elif index is not None:
            pks = [] if result is None else [result[index]]
        taken(self.connection, self.query.model._meta.label_lower, pks)
        return result

    exit_atomic = transaction.Atomic.__exit__

    def checked_exit(self, exc_type, exc_value, tb):
        try:
            return exit_atomic(self, exc_type, exc_value, tb)
        finally:
            connection = transaction.get_connection(self.using)
            if not _in_a_transaction_of_its_own(connection):
                forget(connection)

    from django.db import connections
    from django.db.models.signals import post_save

    def made(sender, instance, created, using, **kwargs):
        connection = connections[using]
        if created and connection.in_atomic_block and _in_a_transaction_of_its_own(connection):
            _state(connection)["held"].add((instance._meta.label_lower, instance.pk))

    post_save.connect(made, weak=False, dispatch_uid="apps.core.lock_order.made")
    compiler.SQLCompiler.execute_sql = checked_execute_sql
    compiler.SQLCompiler._lock_order_installed = True
    transaction.Atomic.__exit__ = checked_exit
