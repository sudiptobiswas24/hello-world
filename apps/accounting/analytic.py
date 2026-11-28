"""
Cost centres: the analytic view of the books. A ledger line that is a
cost may carry the centre that incurred it — the loom shed, the printing
line, the office, a vehicle — so expenses read by who spent them, not
only by what they were spent on. The account stays the account; the
centre is a second dimension on the line, stamped when the line is
posted (a bill line's own centre, a department's for its wages, a hand
journal's as typed) and never recomputed.

What has no centre shows as unallocated rather than disappearing, and
the report foots to the profit and loss account's expenses, so the
analytic view and the books cannot drift apart.
"""

from decimal import Decimal

from django.db import models
from django.db.models import Sum

from apps.core.models import AuditModel, to_date

ZERO = Decimal("0")
PAISA = Decimal("0.01")


class CostCentre(AuditModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


def costs_by_centre(start=None, end=None):
    """Posted expenses in a window by the centre that incurred them; what carries none is unallocated, and the total foots to the profit and loss account."""
    from .models import AccountType, JournalLine
    from .reports import profit_and_loss

    start, end = to_date(start), to_date(end)
    lines = JournalLine.objects.filter(entry__posted=True, account__account_type=AccountType.EXPENSE)
    if start:
        lines = lines.filter(entry__date__gte=start)
    if end:
        lines = lines.filter(entry__date__lte=end)
    grouped = lines.values("cost_centre", "cost_centre__code", "cost_centre__name").annotate(
        debit=Sum("debit"), credit=Sum("credit")).order_by("cost_centre__code")
    rows = []
    for row in grouped:
        # At the paisa the column stores: SQLite sums 80.00 to 80.
        amount = ((row["debit"] or ZERO) - (row["credit"] or ZERO)).quantize(PAISA)
        rows.append({"centre": row["cost_centre"], "code": row["cost_centre__code"] or "",
                     "name": row["cost_centre__name"] or "Unallocated", "amount": amount})
    rows.sort(key=lambda row: (row["centre"] is None, row["code"]))
    total = sum((row["amount"] for row in rows), PAISA * 0)
    statement = profit_and_loss(start, end)["expense_total"]
    return {"start": start, "end": end, "rows": rows, "total": total, "statement_expenses": statement,
            "foots": total == statement}
