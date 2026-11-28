"""
Budgets: what the plant agreed to spend and to earn, by account and, where
it matters who spends it, by cost centre — read against what the books
say was posted.

A budget spans a window (a quarter, a financial year) and its lines name
an income or expense account, optionally a centre, and an amount for the
whole window. The report prorates each line to the day asked about by
days elapsed, so a half-spent quarter reads as on track rather than as
half the budget unspent, and puts beside it what posted: an expense
line's debits less credits, an income line's credits less debits.

A line without a centre is the account's whole, centred lines included.
An account whose lines all name centres has whatever posted to it under
no centre or another centre shown as a row of its own with no budget, and
an account nothing budgeted that posted anyway is listed as unbudgeted:
neither disappears into a total.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone

from apps.core.models import AuditModel, to_date

PAISA = Decimal("0.01")
TENTH = Decimal("0.1")
ZERO = Decimal("0.00")


class Budget(AuditModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    start_date = models.DateField()
    end_date = models.DateField()
    note = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-start_date", "code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def clean(self):
        self._check_span()

    def save(self, *args, **kwargs):
        self._check_span()
        super().save(*args, **kwargs)

    def _check_span(self):
        if self.start_date and self.end_date and to_date(self.end_date) < to_date(self.start_date):
            raise ValidationError({"end_date": ["A budget cannot end before it starts."]})

    def days(self):
        return (to_date(self.end_date) - to_date(self.start_date)).days + 1


class BudgetLine(AuditModel):
    budget = models.ForeignKey(Budget, related_name="lines", on_delete=models.CASCADE)
    account = models.ForeignKey("accounting.Account", related_name="budget_lines", on_delete=models.PROTECT)
    cost_centre = models.ForeignKey(
        "accounting.CostCentre", null=True, blank=True, related_name="budget_lines", on_delete=models.PROTECT,
        help_text="Left empty, the line is the account's whole, centred spend included.",
    )
    amount = models.DecimalField(max_digits=18, decimal_places=2, help_text="For the budget's whole span.")

    class Meta:
        ordering = ["account__code", "cost_centre__code"]
        constraints = [
            models.CheckConstraint(check=Q(amount__gte=0), name="budget_line_amount_not_negative"),
            models.UniqueConstraint(fields=["budget", "account", "cost_centre"],
                                    name="one_budget_line_per_account_and_centre"),
            # NULL is distinct from NULL to the database, so the uncentred
            # line is made unique on its own.
            models.UniqueConstraint(fields=["budget", "account"], condition=Q(cost_centre__isnull=True),
                                    name="one_budget_line_per_account_uncentred"),
        ]

    def __str__(self):
        where = f" / {self.cost_centre.code}" if self.cost_centre_id else ""
        return f"{self.budget.code}: {self.account.code}{where} {self.amount}"

    def clean(self):
        self._check_account()

    def save(self, *args, **kwargs):
        self._check_account()
        super().save(*args, **kwargs)

    def _check_account(self):
        from .models import AccountType

        if self.account_id and self.account.account_type not in (AccountType.INCOME, AccountType.EXPENSE):
            raise ValidationError({"account": [
                f"Budgets are for income and expense accounts, and {self.account} is of type "
                f"{self.account.get_account_type_display().lower()}."]})


def _share(amount, elapsed, days):
    return (amount * elapsed / days).quantize(PAISA, rounding=ROUND_HALF_UP) if days else ZERO


def _percent(actual, amount):
    return (actual * 100 / amount).quantize(TENTH, rounding=ROUND_HALF_UP) if amount else None


def budget_report(budget, as_of=None):
    """
    Each line of the budget against what posted from its start to `as_of`
    (today, and never past its end): the amount, the share of it the days
    so far account for, the actual, what is left and the percentage used.
    Then what posted that no line covers.
    """
    from .models import Account, AccountType, JournalLine

    start, end = to_date(budget.start_date), to_date(budget.end_date)
    upto = min(to_date(as_of) or timezone.localdate(), end)
    days = budget.days()
    elapsed = max((upto - start).days + 1, 0)

    moved = {}  # (account, centre) -> signed amount, as the account's type reads it
    by_account = {}
    kinds = {}
    if elapsed:
        grouped = JournalLine.objects.filter(
            entry__posted=True, entry__date__gte=start, entry__date__lte=upto,
            account__account_type__in=[AccountType.INCOME, AccountType.EXPENSE],
        ).values("account", "account__account_type", "cost_centre").annotate(debit=Sum("debit"), credit=Sum("credit"))
        for row in grouped:
            debit, credit = row["debit"] or ZERO, row["credit"] or ZERO
            signed = (credit - debit) if row["account__account_type"] == AccountType.INCOME else (debit - credit)
            signed = signed.quantize(PAISA)
            moved[(row["account"], row["cost_centre"])] = signed
            by_account[row["account"]] = by_account.get(row["account"], ZERO) + signed
            kinds[row["account"]] = row["account__account_type"]

    lines = list(budget.lines.select_related("account", "cost_centre"))
    accounts = {line.account_id: line.account for line in lines}
    accounts.update(Account.objects.in_bulk([pk for pk in by_account if pk not in accounts]))

    def row(account, centre, amount, actual, line=None, unbudgeted=False, name=None):
        return {
            "line": line, "kind": account.account_type, "account": account.pk,
            "account_code": account.code, "account_name": account.name,
            "centre": centre.pk if centre else None, "centre_code": centre.code if centre else "",
            "centre_name": name if name is not None else (centre.name if centre else ""),
            "amount": amount, "to_date": _share(amount, elapsed, days), "actual": actual,
            "remaining": amount - actual, "used_percent": _percent(actual, amount),
            "over": actual > amount, "ahead": actual > _share(amount, elapsed, days), "unbudgeted": unbudgeted,
        }

    rows = []
    covered = {}  # account -> the centred lines' actuals, and whether an uncentred line exists
    for line in lines:
        if line.cost_centre_id is None:
            actual = by_account.get(line.account_id, ZERO)
            covered.setdefault(line.account_id, {"centred": ZERO, "whole": False})["whole"] = True
        else:
            actual = moved.get((line.account_id, line.cost_centre_id), ZERO)
            covered.setdefault(line.account_id, {"centred": ZERO, "whole": False})["centred"] += actual
        rows.append(row(line.account, line.cost_centre, line.amount, actual, line=line.pk))
    for account_id, seen in covered.items():
        if not seen["whole"]:
            residual = by_account.get(account_id, ZERO) - seen["centred"]
            if residual:
                rows.append(row(accounts[account_id], None, ZERO, residual, unbudgeted=True, name="Other centres or none"))
    for account_id, actual in sorted(by_account.items(), key=lambda item: accounts[item[0]].code):
        if account_id not in covered and actual:
            rows.append(row(accounts[account_id], None, ZERO, actual, unbudgeted=True))

    totals = {}
    for kind in (AccountType.EXPENSE, AccountType.INCOME):
        mine = [r for r in rows if r["kind"] == kind]
        totals[kind] = {
            "amount": sum((r["amount"] for r in mine if not r["unbudgeted"]), ZERO),
            "to_date": sum((r["to_date"] for r in mine if not r["unbudgeted"]), ZERO),
            "actual": sum((r["actual"] for r in mine if not r["unbudgeted"]), ZERO),
            "unbudgeted": sum((r["actual"] for r in mine if r["unbudgeted"]), ZERO),
        }
    return {
        "budget": budget.pk, "code": budget.code, "name": budget.name, "start": start, "end": end,
        "as_of": upto, "days": days, "elapsed": elapsed, "rows": rows, "totals": totals,
    }
