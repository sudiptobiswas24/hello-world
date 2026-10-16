"""
Does the system still agree with itself?

Every figure here is derived, so the books can be asked to prove
themselves: debits equal credits; each posted entry balances; the
receivable and payable control accounts hold exactly what the open
documents say; the stock's value is the inventory accounts' balance; no
shelf is below nothing; nothing was posted into a month after it was
closed; the invoice numbers run without a gap. A disagreement is a
defect, or a hand journal to a control account, and either carries
forward until somebody sees it: the inbox counts them for whoever holds
`core.check_health`, and the Health screen shows which and by how much.

The server's own state is the other half: the database answers, the
schema is current, the disk has room, last night's backup exists.
`/healthz/` says only whether all of that holds, for the container and
whatever watches it; the screen says what.

Nothing here is stored: a probe reads the live figures each time, and
the inbox keeps the last answer for ten minutes so a home page does not
replay the stock ledger on every open.
"""

import datetime
import os
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db.models import DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

MONEY = DecimalField(max_digits=18, decimal_places=2)
ZERO = Decimal("0")
SHOW = 10  # rows named on the screen; the count says the rest
KEEP_FOR = 10 * 60  # seconds an answer serves the inbox
BACKUP_STALE_AFTER = datetime.timedelta(hours=26)  # nightly, with a late night's grace
DISK_LOW_MB = 1024


@dataclass(frozen=True)
class Probe:
    key: str
    label: str
    run: Callable  # (day) -> finding


def _finding(ok, count=0, detail="", rows=(), href=""):
    """ok is True, False, or None when it could not be proved either way."""
    return {"ok": ok, "count": count, "detail": detail, "rows": list(rows), "href": href}


def _money(value):
    return f"{value:,.2f}"


def _is_base(document):
    """Booked at the base currency's rate of one: its figures are the ledger's."""
    rate = document.exchange_rate
    return rate is None or Decimal(rate) == 1


def _ledger_balance(account):
    """Debits less credits of everything posted to `account`, to date."""
    from apps.accounting.models import JournalLine

    totals = JournalLine.objects.filter(entry__posted=True, account=account).aggregate(
        debit=Coalesce(Sum("debit"), Value(ZERO), output_field=MONEY),
        credit=Coalesce(Sum("credit"), Value(ZERO), output_field=MONEY))
    return totals["debit"] - totals["credit"]


def _standing_payments(account):
    """Posted, unvoided payments booked against `account` with money applied to nothing."""
    from apps.accounting.models import Payment
    from apps.accounting.settlement import unapplied

    rows = unapplied(Payment.objects.filter(posted=True, voided_entry__isnull=True, counterpart_account=account)
                     ).select_related("journal_entry").prefetch_related("journal_entry__reversed_by")
    return [payment for payment in rows if not payment.is_voided()]


def _trial_balance(day):
    from apps.accounting.reports import trial_balance

    report = trial_balance(as_of=day)
    if report["balanced"]:
        return _finding(True, detail="Debits equal credits.", href="/accounts/trial-balance")
    return _finding(False, 1, f"Closing debits {_money(report['closing_debit'])} and credits "
                    f"{_money(report['closing_credit'])} differ: the ledger itself is broken.",
                    href="/accounts/trial-balance")


def _entries_balanced(day):
    from apps.accounting.models import JournalEntry

    unbalanced = (JournalEntry.objects.filter(posted=True)
                  .annotate(debits=Coalesce(Sum("lines__debit"), Value(ZERO), output_field=MONEY),
                            credits=Coalesce(Sum("lines__credit"), Value(ZERO), output_field=MONEY))
                  .exclude(debits=F("credits")).order_by("-date", "-id"))
    rows = [{"label": f"{entry.date} {entry.memo or entry.reference or f'entry {entry.pk}'}: "
                      f"Dr {_money(entry.debits)}, Cr {_money(entry.credits)}",
             "href": f"/accounts/journals/{entry.pk}"} for entry in unbalanced[:SHOW]]
    count = unbalanced.count()
    return _finding(count == 0, count,
                    "Every posted entry balances." if not count else f"{count} posted entries do not balance.",
                    rows, "/accounts/journals")


def _control_account(label, account, ledger, documents, payments, foreign_open, href):
    """One control account against the open documents booked to it."""
    expected = documents + payments
    difference = ledger - expected
    row = {"label": f"{account.code} {account.name}: ledger {_money(ledger)}, documents {_money(expected)}",
           "href": href}
    if difference == 0:
        return True, row
    row["label"] += f", difference {_money(difference)}"
    if foreign_open:
        row["label"] += (f"; {foreign_open} open in other currencies, whose rounding this cannot prove")
        return None, row
    return False, row


def _agreement(what, accounts, href):
    """Combine the per-account answers into one finding."""
    states = [state for state, _ in accounts]
    rows = [row for _, row in accounts]
    disagree = sum(1 for state in states if state is False)
    unproved = sum(1 for state in states if state is None)
    if disagree:
        detail = f"{disagree} control account(s) differ from the {what}."
    elif unproved:
        detail = f"{unproved} control account(s) cannot be proved: documents in other currencies are open on them."
    else:
        detail = f"Every control account holds what the {what} say." if accounts else f"No {what} posted yet."
    return _finding(False if disagree else (None if unproved else True), disagree, detail, rows, href)


def _receivables(day):
    from apps.accounting.models import Account, PaymentDirection
    from apps.core.models import Company
    from apps.sales.models import INVOICE_FIGURES, Invoice, not_paid_in_full

    company = Company.get()
    used = set(Invoice.objects.filter(posted=True).values_list("receivable_account", flat=True))
    if company.default_receivable_account_id:
        used.add(company.default_receivable_account_id)
    open_invoices = list(not_paid_in_full(Invoice.objects.filter(posted=True, receivable_account__in=used))
                         .select_related("credits").prefetch_related(*INVOICE_FIGURES))
    accounts = []
    for account in Account.objects.filter(pk__in=used).order_by("code"):
        documents, foreign_open = ZERO, 0
        for invoice in open_invoices:
            if invoice.receivable_account_id != account.pk:
                continue
            due = invoice.amount_due()
            if not _is_base(invoice):
                foreign_open += 1 if due else 0
                continue
            documents += -due if invoice.is_credit_note() else due
        payments = ZERO
        for payment in _standing_payments(account):
            if not _is_base(payment):
                foreign_open += 1
                continue
            sign = -1 if payment.direction == PaymentDirection.RECEIPT else 1
            payments += sign * payment.unallocated
        accounts.append(_control_account("receivables", account, _ledger_balance(account), documents, payments,
                                         foreign_open, "/sales/aging"))
    return _agreement("invoices", accounts, "/sales/aging")


def _payables(day):
    from apps.accounting.models import Account, PaymentDirection
    from apps.core.models import Company
    from apps.purchasing.models import BILL_FIGURES, Bill, not_paid_in_full

    company = Company.get()
    used = set(Bill.objects.filter(posted=True).values_list("payable_account", flat=True))
    if company.default_payable_account_id:
        used.add(company.default_payable_account_id)
    open_bills = list(not_paid_in_full(Bill.objects.filter(posted=True, payable_account__in=used))
                      .select_related("debits").prefetch_related(*BILL_FIGURES))
    accounts = []
    for account in Account.objects.filter(pk__in=used).order_by("code"):
        documents, foreign_open = ZERO, 0
        for bill in open_bills:
            if bill.payable_account_id != account.pk:
                continue
            due = bill.amount_due()
            if not _is_base(bill):
                foreign_open += 1 if due else 0
                continue
            documents += -due if bill.is_debit_note() else due
        payments = ZERO
        for payment in _standing_payments(account):
            if not _is_base(payment):
                foreign_open += 1
                continue
            sign = -1 if payment.direction == PaymentDirection.DISBURSEMENT else 1
            payments += sign * payment.unallocated
        # A payable is a credit balance: the ledger's sign is turned round.
        accounts.append(_control_account("payables", account, -_ledger_balance(account), documents, payments,
                                         foreign_open, "/purchasing/aging"))
    return _agreement("bills", accounts, "/purchasing/aging")


def _stock(day):
    from apps.inventory.reports import reconcile_to_ledger

    report = reconcile_to_ledger(day)
    off = [row for row in report["rows"] if row["difference"]]
    rows = [{"label": f"{row['account'].code} {row['account'].name}: stock {_money(row['stock_value'])}, "
                      f"ledger {_money(row['ledger_balance'])}, difference {_money(row['difference'])}",
             "href": "/stores/value"} for row in off[:SHOW]]
    unvalued = report["unvalued_items"]
    if off:
        detail = f"{len(off)} inventory account(s) differ from the stock on the shelves."
    elif unvalued:
        detail = (f"{len(unvalued)} item(s) have no inventory account, so their stock was not counted: "
                  + ", ".join(str(item) for item in unvalued[:SHOW]))
    else:
        detail = "The stock's value is the inventory accounts' balance."
    return _finding(False if off else (None if unvalued else True), len(off), detail, rows, "/stores/value")


def _negative_stock(day):
    from apps.inventory.reports import negative_stock

    unexpected = negative_stock(day)["unexpected"]
    rows = [{"label": f"{row['item']} at {row['warehouse']}: {format(row['quantity'].normalize(), 'f')}",
             "href": "/stores/negative"} for row in unexpected[:SHOW]]
    return _finding(not unexpected, len(unexpected),
                    "No shelf holds less than nothing." if not unexpected
                    else f"{len(unexpected)} shelf position(s) hold less than nothing where that is not allowed.",
                    rows, "/stores/negative")


def _closed_periods(day):
    from apps.accounting.models import AccountingPeriod, JournalEntry

    rows, count = [], 0
    for period in AccountingPeriod.objects.filter(closed=True, closed_at__isnull=False).order_by("start_date"):
        late = JournalEntry.objects.filter(posted=True, date__range=(period.start_date, period.end_date),
                                           posted_at__gt=period.closed_at).order_by("date", "id")
        found = late.count()
        count += found
        rows += [{"label": f"{entry.date} {entry.memo or entry.reference or f'entry {entry.pk}'}, "
                           f"posted {timezone.localtime(entry.posted_at):%Y-%m-%d %H:%M} into {period.name}, "
                           f"closed {timezone.localtime(period.closed_at):%Y-%m-%d %H:%M}",
                  "href": f"/accounts/journals/{entry.pk}"} for entry in late[:SHOW - len(rows)]]
    return _finding(count == 0, count,
                    "Nothing was posted into a closed month after it closed." if not count
                    else f"{count} entries were posted into a closed month after it closed.",
                    rows, "/accounts/periods")


def _invoice_numbers(day):
    """
    A tax invoice's numbers must run without a gap (a post that failed
    after taking a number, a row deleted from the database). GSTR-1 asks
    for every number not issued; this says which.
    """
    from apps.core.models import DocumentSequence
    from apps.sales.models import Invoice

    missing = []
    numbers = list(Invoice.objects.exclude(number="").values_list("number", flat=True))
    for code in ("sales.invoice", "sales.credit_note"):
        sequence = DocumentSequence.objects.filter(code=code).first()
        if sequence is None:
            continue
        pattern = re.compile(rf"^{re.escape(sequence.prefix)}(?:(\d+)-)?(\d+){re.escape(sequence.suffix)}$")
        used = defaultdict(set)
        for number in numbers:
            match = pattern.match(number)
            if match:
                used[int(match.group(1)) if match.group(1) else None].add(int(match.group(2)))
        if sequence.reset_yearly:
            counters = [(counter.year, counter.next_number) for counter in sequence.years.all()]
        else:
            counters = [(None, sequence.next_number)]
            used = {None: set().union(*used.values())} if used else {}
        for year, next_number in counters:
            taken = used.get(year) or set()
            if not taken:
                continue  # nothing issued yet: nothing to run without a gap
            missing += [(sequence, year, number) for number in range(min(taken), next_number) if number not in taken]
    rows = [{"label": sequence._format(number, year or day.year), "href": "/sales/invoices"}
            for sequence, year, number in missing[:SHOW]]
    return _finding(not missing, len(missing),
                    "Invoice and credit note numbers run without a gap." if not missing
                    else f"{len(missing)} number(s) were taken and never issued.", rows, "/sales/invoices")


PROBES = [
    Probe("trial_balance", "Trial balance", _trial_balance),
    Probe("entries_balanced", "Every posted entry balances", _entries_balanced),
    Probe("receivables", "Receivables agree with the invoices", _receivables),
    Probe("payables", "Payables agree with the bills", _payables),
    Probe("stock", "Stock agrees with the ledger", _stock),
    Probe("negative_stock", "No shelf below nothing", _negative_stock),
    Probe("closed_periods", "Closed months stayed closed", _closed_periods),
    Probe("invoice_numbers", "Invoice numbers run without a gap", _invoice_numbers),
]


def integrity(day=None, fresh=False):
    """
    {checked_at, findings: [{key, label, ok, count, detail, rows, href}]}
    for the plant's day, each probe answering for itself; one that fails
    to run is a finding too, never a blank. The answer serves for ten
    minutes unless `fresh`.
    """
    day = day or timezone.localdate()
    key = f"health.integrity.{day}"
    if not fresh:
        kept = cache.get(key)
        if kept is not None:
            return kept
    findings = []
    for probe in PROBES:
        try:
            finding = probe.run(day)
        except Exception as error:  # noqa: BLE001 - a check that crashes is itself a finding
            finding = _finding(False, 1, f"The check could not run: {type(error).__name__}: {error}")
        findings.append({"key": probe.key, "label": probe.label, **finding})
    report = {"checked_at": timezone.now(), "findings": findings}
    cache.set(key, report, KEEP_FOR)
    return report


def backups(now=None):
    """The newest backup in BACKUP_DIR and how old it is; `configured` False where none is named."""
    directory = settings.BACKUP_DIR
    if not directory:
        return {"configured": False, "directory": "", "newest": "", "age_hours": None, "stale": False}
    now = now or timezone.now()
    try:
        names = [name for name in os.listdir(directory) if name.startswith("erp_") and name.endswith(".dump")]
    except OSError:
        names = []
    if not names:
        return {"configured": True, "directory": str(directory), "newest": "", "age_hours": None, "stale": True}
    newest = max(names, key=lambda name: os.path.getmtime(os.path.join(directory, name)))
    written = datetime.datetime.fromtimestamp(os.path.getmtime(os.path.join(directory, newest)),
                                              tz=datetime.timezone.utc)
    age = now - written
    return {"configured": True, "directory": str(directory), "newest": newest,
            "age_hours": round(age.total_seconds() / 3600, 1), "stale": age > BACKUP_STALE_AFTER}


def _migrations_pending():
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    return len(executor.migration_plan(executor.loader.graph.leaf_nodes()))


def server():
    """The machine's side: database, schema, disk, backups; `ok` and `problems` sum it up."""
    problems = []
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        database = "ok"
    except Exception as error:  # noqa: BLE001 - whatever the driver raises is the answer
        database = f"{type(error).__name__}: {error}"[:200]
        problems.append("the database does not answer")
    pending = None
    if database == "ok":
        pending = _migrations_pending()
        if pending:
            problems.append(f"{pending} migration(s) not applied")
    where = settings.MEDIA_ROOT if os.path.isdir(settings.MEDIA_ROOT) else settings.BASE_DIR
    free_mb = shutil.disk_usage(where).free // (1024 * 1024)
    if free_mb < DISK_LOW_MB:
        problems.append(f"{free_mb} MB of disk left")
    kept = backups()
    if kept["configured"] and kept["stale"]:
        problems.append("no backup in the last day" if kept["newest"] else "no backup found")
    return {"ok": not problems, "problems": problems, "database": database, "migrations_pending": pending,
            "disk_free_mb": free_mb, "disk_low": free_mb < DISK_LOW_MB, "backups": kept}
