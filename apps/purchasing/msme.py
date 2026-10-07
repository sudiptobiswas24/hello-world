"""
Micro and small vendors paid in time.

The MSME Act gives a micro or small supplier payment within the days
agreed, never more than 45, or 15 where nothing was agreed. Since 2024 a
bill paid later is not deducted as an expense until the year it is paid
(section 43B(h)), so what stands unpaid past its date at the year's end
is added back to profit.

Derived each time from the bills and their payments; nothing is stored.
"""

import datetime
from decimal import Decimal

from django.utils import timezone

from apps.core.models import to_date

COVERED = ("micro", "small")


def msme_due(bill):
    """The day the Act says a bill is paid by."""
    start = to_date(bill.bill_date)
    if bill.payment_terms_id and bill.due_date:
        days = min((to_date(bill.due_date) - start).days, 45)
    else:
        days = 15
    return start + datetime.timedelta(days=days)


def _standing(bill, day):
    """[(paid on, amount)] of payments against the bill made by `day` that still stand."""
    return [(to_date(row.payment.payment_date), row.amount) for row in bill.payment_allocations.all()
            if not row.payment.is_voided() and to_date(row.payment.payment_date) <= day]


def msme_bills(start, end, as_of=None):
    """
    Bills dated `start` to `end` from micro and small vendors: when each was
    due under the Act, when it was paid, how late, and what was still
    unpaid on `as_of` past its date (the year-end figure 43B(h) adds back).
    """
    from .models import BILL_FIGURES, Bill

    as_of = to_date(as_of) or timezone.localdate()
    bills = Bill.objects.filter(
        posted=True, debits__isnull=True, is_prepayment=False, bill_date__gte=to_date(start),
        bill_date__lte=to_date(end), vendor__tax_profile__msme_category__in=COVERED,
    ).select_related("vendor__tax_profile", "payment_terms").prefetch_related(*BILL_FIGURES).order_by(
        "bill_date", "pk")
    rows = []
    for bill in bills:
        due = msme_due(bill)
        # What settles it other than money paid out (debit notes, tax
        # deducted, a discount) is counted whenever it happened.
        total = bill.total()
        other = total - bill.amount_due() - bill.amount_paid()
        paid = _standing(bill, as_of)
        unpaid = max(total - other - sum((amount for _, amount in paid), Decimal("0")), Decimal("0"))
        paid_on = max(day for day, _ in paid) if unpaid <= 0 and paid else None
        last = paid_on or as_of
        late = max((last - due).days, 0)
        rows.append({
            "bill": bill.pk, "number": bill.number, "vendor": bill.vendor.name,
            "category": bill.vendor.tax_profile.msme_category, "udyam": bill.vendor.tax_profile.udyam_number,
            "bill_date": to_date(bill.bill_date), "total": total, "due": due, "paid_on": paid_on,
            "days_late": late, "unpaid": unpaid,
            "at_risk": unpaid if unpaid > 0 and as_of > due else Decimal("0"),
        })
    return rows
