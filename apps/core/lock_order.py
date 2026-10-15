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
model not in LOCK_ORDER is ranked against nothing, but its own rows are
still taken in key order: two landed costs onto the same two receipt
lines listed the other way round deadlocked on their stock positions, an
unranked model, unseen (O182).

It sees the lock wherever a statement takes one (O184): a
select_for_update(), and the tables it joins under select_related() (the
pks read back, or read again when the query names none, as
values_list("code") does); an update(), and so every save() of a row
that already stood; a delete(). Those three, new, are left out of its
view for the models UNSEEN names, each with its reason, and only for
them: a select_for_update() of any model is checked as it always was.
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
    # 3. the customer whose credit limit is weighed, then a payment it made
    #    or is paid: an allocation holds the payment before the invoice or
    #    bill it settles (every path the survey of 14 October saw)
    "core.party",
    "accounting.payment",
    # 3a. what a payment pays in payroll, after it (rereview 5, 6): Payment.void holds the payment
    #     and its follow-up the bank file or the slip; paying a file or a slip and returning a
    #     transfer take the money first (hr.payroll.hold_the_money_first), then the file, the
    #     transfer on it, then the slips.
    "hr.salarytransferfile",
    "hr.salarytransfer",
    "hr.payslip",
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
    # 6. the stock they move, last (O182): a delivery or receipt holds its
    #    lines before the shelves, and a return the invoices or bills it
    #    corrects before the goods come back. Rows in key order, so two
    #    landed costs onto the same two receipt lines take them alike.
    "inventory.stockposition",
    # 7. the period an entry is posted into, read under lock as the entry
    #    posts: after the stock the document moved
    "accounting.accountingperiod",
)

RANK = {label: rank for rank, label in enumerate(LOCK_ORDER)}

# Seen locking in the same transactions as the models above (the survey of
# the suite, 10 October), and left unranked, each for its reason. Ranking
# one makes every path that takes it beside a trading document answer to
# the sentinel: the next to do is inventory.stockposition, seen both
# before and after sales orders, purchase orders, receipts and bill lines.
UNRANKED = {
    "accounting.journalentry": "a row the posting makes in the same transaction, then locked; a reversal takes its "
                               "own. Seen both ways round sales.invoice (14 October), not ranked",
    "core.documentsequence": "one row per document type, taken by the document numbering it, before and after "
                             "documents and lines alike (14 October); ranks by type, not by document",
    "accounting.account": "a control account, locked by payroll and banking paths, not trading ones",
}


class LockOrderViolation(AssertionError):
    """A transaction took a row the written lock order puts before one it already held."""


class LockedOutsideATransaction(AssertionError):
    """
    A row locked where no transaction the code opened holds it.

    On PostgreSQL that is a 500 ("select_for_update cannot be used outside
    of a transaction"); on SQLite, which ignores select_for_update, nothing
    at all, so the suite on SQLite passed a job-work loss that PostgreSQL
    refused on its first call. Inside a TestCase only the test's own block
    is open, which no request in production has; that counts as outside.
    """


def _state(connection):
    state = getattr(connection, "_lock_order_state", None)
    if state is None:
        state = connection._lock_order_state = {"held": set(), "top": None, "taken": [], "keys": {}}
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


# The models whose locks by update(), save(), delete() or a join, and, for
# an unranked one, the key order of its own rows, the sentinel does not look
# at: what the survey of the whole suite on 14 October found there, each
# with its reason, for the register (docs/RISKS.md) to take off one by one
# (O184). A ranked model's select_for_update() is checked whatever this
# says, as it was before any of it was seen.
UNSEEN = {
    "core.address": "take_primary() clears a party's other primaries after the row it saves, rows of one model "
                    "out of key order; harmless, because every write of a party's addresses holds the party "
                    "first (locked_before_it, take_primary), so two never overlap (review F9)",
    "core.contact": "take_primary(), as core.address",
    "core.documentsequence": "a document numbering a second document type takes that type's row after its own, "
                             "in no fixed order between types (sales.Invoice.post)",
    "core.documentsequenceyear": "the year's counter of a document type, as core.documentsequence",
    "accounting.journalentry": "a work order's void reverses its documents' entries in the order it voids them "
                               "(manufacturing/orders.py WorkOrder.void)",
    "assets.fixedasset": "depreciate_all() holds each asset in the order it charges them, not key order",
    "inventory.item": "publishing a cost writes each item's standard in the order rolled up (costing.publish)",
    "manufacturing.billofmaterials": "a woven bag's save rebuilds its components' BOMs in the order built",
    "manufacturing.workorderoperation": "a dispatch commit writes the operations in the order dispatched",
    "manufacturing.materialissue": "a roll mount's void voids its issues in the order mounted",
    "manufacturing.standardcost": "roll_up() holds each component's cost in the order the tree is walked",
    "manufacturing.timebooking": "a station clock's void voids its bookings in the order booked",
    "quality.inspectionplan": "a woven bag's save rebuilds its plans in the order built",
}

# How a statement took its rows: a select_for_update() names them; the rest
# are what O184 found the sentinel could not see.
SELECTED, JOINED, UPDATED, DELETED = "select_for_update", "joined", "update", "delete"


def taken(connection, label, pks, how=SELECTED):
    """Rank the rows `label` `pks` (None: not known) just locked on `connection`, by `how`."""
    if not connection.in_atomic_block or not _in_a_transaction_of_its_own(connection):
        if how in (UPDATED, DELETED):
            return  # a statement of its own: its lock ends with it
        if _SURVEY:
            _survey("outside", label)
            return
        raise LockedOutsideATransaction(
            f"{label} locked outside any transaction the code opened, at {_where()}: "
            f"the lock is gone when the statement ends. Open one where the decision is made.")
    if label in UNSEEN and (how != SELECTED or label not in RANK):
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
            # Ranked against nothing, but its own rows in key order (O182).
            last = state["keys"].get(label)
            if pk is not None and last is not None and pk < last:
                message = (f"{label} {pk} locked after {label} {last} ({how}): rows of one model are "
                           f"taken in key order (apps.core.lock_order)")
                if _SURVEY:
                    _survey("violation", label, label, how)
                    continue
                raise LockOrderViolation(message)
            if pk is not None and (last is None or pk > last):
                state["keys"][label] = pk
            continue
        top = state["top"]
        mine = (rank, pk if pk is not None else 0)
        if top is not None and (rank < top[0] or (pk is not None and rank == top[0] and pk < top[1])):
            message = (f"{label} {pk} locked after {LOCK_ORDER[top[0]]} {top[1]} ({how}): "
                       f"against the lock order in apps.core.lock_order")
            if _SURVEY:
                _survey("violation", LOCK_ORDER[top[0]], label, how)
                continue
            raise LockOrderViolation(message)
        if top is None or mine > top:
            state["top"] = mine


def made(connection, label, pks):
    """Rows this transaction made: no other can see them until it commits, so taking them waits for nothing."""
    if connection.in_atomic_block and _in_a_transaction_of_its_own(connection):
        _state(connection)["held"].update((label, pk) for pk in pks if pk is not None)


def forget(connection):
    connection._lock_order_state = None


def _named_keys(query):
    """The pks a statement's filter names outright (pk=, pk__in=[...]): what save() and a delete send."""
    from django.db.models.lookups import Exact, In

    where = query.where
    if where.connector != "AND" or where.negated or len(where.children) != 1:
        return None
    lookup = where.children[0]
    if getattr(getattr(lookup, "lhs", None), "target", None) is not query.get_meta().pk:
        return None
    if isinstance(lookup, Exact) and not hasattr(lookup.rhs, "resolve_expression"):
        return [lookup.rhs]
    if isinstance(lookup, In) and isinstance(lookup.rhs, (list, tuple, set, frozenset)):
        return list(lookup.rhs)
    return None


def _keys_matched(compiler_, query, in_order=False):
    """The pks `query`'s filter matches, read with a plain select: the rows its UPDATE or DELETE will lock."""
    from django.db.models.sql.constants import MULTI
    from django.db.models.sql.query import Query

    named = _named_keys(query)
    if named is not None:
        return list(named) if in_order else sorted(named)
    select = query.chain(klass=Query)
    select.select_for_update = False
    select.select_related = False
    if not in_order:
        select.clear_ordering(force=True)
    select.extra = {}
    select.select = ()
    select.default_cols = False
    select.add_fields([query.get_meta().pk.name])
    # Unlogged: a test counting the queries a screen makes counts the screen's, not the sentinel's.
    connection, logged = compiler_.connection, compiler_.connection.force_debug_cursor
    connection.force_debug_cursor = False
    try:
        rows = select.get_compiler(compiler_.using).execute_sql(MULTI)
        keys = [row[0] for chunk in (rows or ()) for row in chunk]
        # A select_for_update() takes its rows in the order it reads them (its order_by): read back
        # in that order, not sorted, or a lock in descending key order passed as one in key order
        # (review F8). An UPDATE or DELETE has no order of its own: its rows count as taken in key order.
        return keys if in_order else sorted(keys)
    finally:
        connection.force_debug_cursor = logged


def _joined(compiler_, rows):
    """(label, pks) for each table a select_for_update() joins under select_related() and so locks too."""
    of = set(compiler_.query.select_for_update_of)
    found = []

    def walk(infos, path):
        for info in infos or ():
            field = info["field"]
            name = field.related_query_name() if info.get("reverse") else field.name
            here = f"{path}__{name}" if path else name
            model = info["model"]
            index = next((i for i in info["select_fields"]
                          if getattr(compiler_.select[i][0], "target", None) is model._meta.pk), None)
            if index is not None and (not of or here in of):
                found.append((model._meta.label_lower, [row[index] for row in rows if row[index] is not None]))
            walk(info.get("related_klass_infos"), here)

    walk((compiler_.klass_info or {}).get("related_klass_infos"), "")
    return found


def install():
    """Rank every row lock and forget at each transaction's end: tests only."""
    from django.db import connections, transaction
    from django.db.models import QuerySet
    from django.db.models.signals import post_save
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
        rows = []
        if result_type == MULTI:
            result = list(result)
            rows = [row for chunk in result for row in chunk]
        elif result is not None:
            rows = [result]
        if index is not None:
            pks = [row[index] for row in rows]
        else:
            # values_list("code"): the rows are locked all the same; read which (O184).
            try:
                pks = _keys_matched(self, self.query, in_order=True) if rows else []
            except Exception:  # an annotation or slice the plain select cannot carry: as before
                pks = None
        of = self.query.select_for_update_of
        if not of or "self" in of:
            taken(self.connection, self.query.model._meta.label_lower,
                  pks)
        for label, keys in sorted(_joined(self, rows), key=lambda pair: RANK.get(pair[0], len(RANK))):
            taken(self.connection, label, sorted(set(keys)), how=JOINED)
        return result

    def checked_writes(original, how):
        def checked(self, *args, **kwargs):
            connection = self.connection
            if connection.in_atomic_block and _in_a_transaction_of_its_own(connection) and \
                    self.query.model._meta.label_lower not in UNSEEN:
                taken(connection, self.query.model._meta.label_lower, _keys_matched(self, self.query), how=how)
            return original(self, *args, **kwargs)
        return checked

    enter_atomic = transaction.Atomic.__enter__

    def checked_enter(self):
        connection = transaction.get_connection(self.using)
        if not connection.in_atomic_block:
            # A transaction of its own from its first block. A commit hook (recall.lift) runs once
            # the transaction before it has committed but before Atomic.__exit__ returns, and so
            # inherited every row that one had held: a lift after a void of a recall was refused for
            # taking a lot below the one the void held, in a transaction it never shared (rereview 4).
            forget(connection)
        return enter_atomic(self)

    exit_atomic = transaction.Atomic.__exit__

    def checked_exit(self, exc_type, exc_value, tb):
        try:
            return exit_atomic(self, exc_type, exc_value, tb)
        finally:
            connection = transaction.get_connection(self.using)
            if not _in_a_transaction_of_its_own(connection):
                forget(connection)

    def on_save(sender, instance, created, using, **kwargs):
        if created:
            made(connections[using], instance._meta.label_lower, [instance.pk])

    bulk_create = QuerySet.bulk_create

    def checked_bulk_create(self, objs, *args, **kwargs):
        objs = bulk_create(self, objs, *args, **kwargs)
        made(connections[self.db], self.model._meta.label_lower, [obj.pk for obj in objs])
        return objs

    post_save.connect(on_save, weak=False, dispatch_uid="apps.core.lock_order.made")
    compiler.SQLCompiler.execute_sql = checked_execute_sql
    compiler.SQLUpdateCompiler.execute_sql = checked_writes(compiler.SQLUpdateCompiler.execute_sql, UPDATED)
    compiler.SQLDeleteCompiler.execute_sql = checked_writes(compiler.SQLDeleteCompiler.execute_sql, DELETED)
    QuerySet.bulk_create = checked_bulk_create
    compiler.SQLCompiler._lock_order_installed = True
    transaction.Atomic.__enter__ = checked_enter
    transaction.Atomic.__exit__ = checked_exit
