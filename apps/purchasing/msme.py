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
# MSMED Act s.15: never more than 45 days from acceptance; with nothing agreed,
# before the appointed day, 15 days on (s.2(b)); an objection is made in
# writing within 15 days of delivery, or acceptance is deemed on delivery.
AGREED_AT_MOST, UNAGREED, TO_OBJECT = 45, 15, 15


def accepted_on(bill):
    """
    The day the goods a bill is for were accepted (MSMED Act s.2): the day
    they were cleared out of inspection, where one was kept and came
    within the 15 days an objection is made in; else the day they were
    received. The receipt is the one the bill follows: the latest of its
    order lines' received on or before its date, or, billed ahead of its
    goods, the first after. A bill for nothing received (a service, a
    line typed with no order) runs from its own date.
    """
    from .models import GoodsReceipt, ReceiptInspection

    billed = to_date(bill.bill_date)
    order_lines = [line.order_line_id for line in bill.lines.all() if line.order_line_id]
    if not order_lines:
        return billed
    receipts = GoodsReceipt.objects.filter(posted=True, reverses__isnull=True,
                                           lines__order_line__in=order_lines).distinct()
    receipt = (receipts.filter(receipt_date__lte=billed).order_by("-receipt_date", "-pk").first()
               or receipts.order_by("receipt_date", "pk").first())
    if receipt is None:
        return billed
    received = to_date(receipt.receipt_date)
    cleared = [to_date(day) for day in ReceiptInspection.objects.filter(
        receipt_line__receipt=receipt, receipt_line__order_line__in=order_lines, accepted=True,
    ).values_list("inspected_on", flat=True)]
    cleared = max(cleared) if cleared else None
    if cleared is not None and received < cleared <= received + datetime.timedelta(days=TO_OBJECT):
        return cleared
    return received


def msme_due(bill):
    """
    The day the Act says a bill is paid by: the day agreed (its terms), and
    never more than 45 days from acceptance; 15 days where nothing was
    agreed. Net 60 on a bill of 1 June for goods received that day is due
    on 16 July, not 31 July.
    """
    start = accepted_on(bill)
    if bill.payment_terms_id and bill.due_date:
        return min(to_date(bill.due_date), start + datetime.timedelta(days=AGREED_AT_MOST))
    return start + datetime.timedelta(days=UNAGREED)


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
    # The category the bill was booked under, not the vendor's today (Bill.msme_category).
    bills = Bill.objects.filter(
        posted=True, debits__isnull=True, is_prepayment=False, bill_date__gte=to_date(start),
        bill_date__lte=to_date(end), msme_category__in=COVERED,
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
            "category": bill.msme_category, "udyam": getattr(getattr(bill.vendor, "tax_profile", None), "udyam_number", ""),
            "bill_date": to_date(bill.bill_date), "total": total, "due": due, "paid_on": paid_on,
            "days_late": late, "unpaid": unpaid,
            "at_risk": unpaid if unpaid > 0 and as_of > due else Decimal("0"),
        })
    return rows
