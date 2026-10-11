"""
Call-offs: a customer's schedule against one order line.

A cement plant orders five lakh sacks for the season and then calls
them off a lorry-load a week. The line says how many and at what price;
the call-offs say when. Without them the plan saw five lakh sacks due on
one day and built the season's stock in the first month.

**What is still owed, and by when, is derived.** Shipments meet the
oldest call-off first, the rest of each call-off is owed on its date,
and whatever has not been called yet is owed on the line's own date
(the end of the contract, on a season's order). Nothing records which
lorry met which call-off, so a correction to a delivery moves the
answer with it.

**The call-offs never add up to more than the line.** A schedule
calling off more than was ordered is a second order nobody priced.
Refused when the call-off is written and when the line is cut below
it — the same rule from both sides.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum

from apps.core.models import AuditModel, lock_rows

ZERO = Decimal("0")


def plain(value):
    """100, not 100.0000: a sum comes back at the column's scale on PostgreSQL."""
    return format(Decimal(value).normalize(), "f")


class CallOff(AuditModel):
    line = models.ForeignKey("sales.SalesOrderLine", on_delete=models.CASCADE,
                             related_name="call_offs")
    due_on = models.DateField()
    quantity = models.DecimalField(max_digits=18, decimal_places=4,
                                   help_text="In the line's unit.")
    reference = models.CharField(max_length=64, blank=True,
                                 help_text="The customer's own call-off number.")

    class Meta:
        ordering = ["line", "due_on", "id"]
        constraints = [
            models.CheckConstraint(check=Q(quantity__gt=0), name="call_off_quantity_positive"),
        ]

    def __str__(self):
        return f"{self.line}: {self.quantity} on {self.due_on}"

    def locked_before_it(self):
        """
        Its line, as save() takes it: an edit or a delete through the API
        holds the line before the call-off, as deleting the line holds it
        before its call-offs (apps.core.models.lock_for_change).
        """
        return [self.line]

    @transaction.atomic
    def save(self, *args, **kwargs):
        # What is left on the line is weighed below: two call-offs at once
        # each found the same 30 left and together called off 40. Re-read
        # under the lock, so a line cut a moment ago is the one weighed.
        lock_rows(*self.locked_before_it())
        if self.line.is_charge():
            raise ValidationError(f"{self.line} is a charge; there is nothing to call off.")
        others = self.line.call_offs.exclude(pk=self.pk).aggregate(
            total=Sum("quantity"))["total"] or ZERO
        if others + self.quantity > self.line.quantity:
            raise ValidationError(
                f"{self.line} is for {plain(self.line.quantity)}; {plain(others)} is called "
                f"off already, so {plain(self.quantity)} more is "
                f"{plain(others + self.quantity - self.line.quantity)} over. "
                "Raise the line first.")
        super().save(*args, **kwargs)


def called_off(line):
    return line.call_offs.aggregate(total=Sum("quantity"))["total"] or ZERO


def open_schedule(line):
    """
    [(date, quantity in the line's unit)] still owed: each call-off less
    what shipments have already met, oldest first, then the uncalled
    balance on the line's own date. Nothing once the line is met or
    closed short.
    """
    # Met or closed short, nothing is owed: every call-off is met and
    # there is no rest.
    owed = line.quantity_open()
    met = line.quantity - owed
    rows = []
    for call in line.call_offs.order_by("due_on", "id"):
        taken = min(call.quantity, met)
        met -= taken
        if call.quantity > taken:
            rows.append((call.due_on, call.quantity - taken))
    rest = owed - sum((quantity for _day, quantity in rows), ZERO)
    if rest > 0:
        rows.append((line.promised_date(), rest))
    return rows
