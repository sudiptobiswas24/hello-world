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

from django.db import models
from django.utils import timezone

from apps.core.models import to_date

COVERED = ("micro", "small")
# MSMED Act s.15: never more than 45 days from acceptance; with nothing agreed,
# before the appointed day, 15 days on (s.2(b)); an objection is made in
# writing within 15 days of delivery, or acceptance is deemed on delivery.
AGREED_AT_MOST, UNAGREED, TO_OBJECT = 45, 15, 15


def acceptance(receipt):
    """
    The day a delivery was accepted (MSMED Act s.2(b) Explanation): the day
    of delivery, unless the company objected in writing within 15 days of
    it, and then the day the supplier removed the objection; None while it
    stands. A routine inspection, passed or not, is no objection (O160): a
    QC pass on 20 June does not move goods delivered on 10 June.
    """
    if receipt.objected_on is None:
        return to_date(receipt.receipt_date)
    return to_date(receipt.objection_removed_on)


def lots(bill):
    """
    [(accepted on, amount)]: what each delivery a bill is for comes to,
    by the day that delivery was accepted. A bill for several deliveries
    owes each on its own day (O160): 6 received on 1 June and 4 on 20 June,
    billed together on 25 June, are 600 from 1 June and 400 from 20 June.

    An order line's deliveries are billed oldest first: what earlier bills
    took of them is passed over. A quantity billed beyond what has come,
    and a line for no order (a charge, a service), run from the bill's own
    date. Amounts are the bill's total shared by line value and quantity,
    each cumulative figure rounded, so they add to the total exactly.
    """
    from .models import BillLine, GoodsReceiptLine

    billed, total = to_date(bill.bill_date), bill.total()
    taxes = bill.line_tax_amounts()
    parts = []  # (day, value) before the share is rounded
    for line in bill.lines.all():
        value = line.subtotal() + sum((amount for tax, amount in taxes.get(line, []) if not tax.reverse_charge),
                                      Decimal("0"))
        if not line.order_line_id or not line.quantity:
            parts.append((billed, value))
            continue
        before = BillLine.objects.filter(
            order_line_id=line.order_line_id, bill__posted=True, bill__debits__isnull=True,
            bill__is_prepayment=False,
        ).exclude(bill=bill).filter(
            models.Q(bill__bill_date__lt=billed) | models.Q(bill__bill_date=billed, bill__pk__lt=bill.pk))
        passed = sum((quantity for quantity in before.values_list("quantity", flat=True)), Decimal("0"))
        left = line.quantity
        for received in GoodsReceiptLine.objects.filter(
                order_line_id=line.order_line_id, receipt__posted=True, receipt__reverses__isnull=True,
        ).select_related("receipt").order_by("receipt__receipt_date", "receipt__pk", "pk"):
            if left <= 0:
                break
            here = received.quantity_received
            skip = min(passed, here)
            passed, here = passed - skip, here - skip
            take = min(here, left)
            if take > 0:
                parts.append((acceptance(received.receipt), value * take / line.quantity))
                left -= take
        if left > 0:
            parts.append((billed, value * left / line.quantity))
    whole = sum((value for _, value in parts), Decimal("0"))
    if not parts or whole <= 0:
        return [(billed, total)]
    by_day, running, given = {}, Decimal("0"), Decimal("0")
    for day, value in parts:
        running += value
        upto = (total * running / whole).quantize(Decimal("0.01"))
        by_day[day] = by_day.get(day, Decimal("0")) + upto - given
        given = upto
    return sorted(by_day.items(), key=lambda row: (row[0] is None, row[0] or datetime.date.max))


def _act_day(bill, accepted):
    if accepted is None:
        return None  # an objection stands: the Act has not started the clock
    if bill.payment_terms_id and bill.due_date:
        return min(to_date(bill.due_date), accepted + datetime.timedelta(days=AGREED_AT_MOST))
    return accepted + datetime.timedelta(days=UNAGREED)


def msme_schedule(bill):
    """
    [(due, amount)]: each delivery's part of the bill and the day the Act
    says it is paid by: the day agreed (its terms), never more than 45 days
    from that delivery's acceptance; 15 days where nothing was agreed. None
    for a part whose objection stands.
    """
    rows = {}
    for accepted, amount in lots(bill):
        day = _act_day(bill, accepted)
        rows[day] = rows.get(day, Decimal("0")) + amount
    return sorted(rows.items(), key=lambda row: (row[0] is None, row[0] or datetime.date.max))


def msme_due(bill):
    """
    The day the Act says a bill is paid by in full: its last part's day.
    Net 60 on a bill of 1 June for goods received that day is due on
    16 July, not 31 July. None while an objection to a part stands.
    """
    days = [day for day, _ in msme_schedule(bill)]
    return None if not days or None in days else max(days)


def earlier_of(rows, act, settled):
    """
    The terms' installments ([{due_date, amount}]) and the Act's parts
    ([(due or None, amount)]) laid over each other, money in order: each
    rupee is due on the earlier of the two days that claim it. Money
    received is applied to the earliest first, as installment_schedule does.
    """
    out, act = [], [[day, amount] for day, amount in act]
    i = 0
    for row in rows:
        left = row["amount"]
        while left > 0:
            day, amount = act[i] if i < len(act) else (None, left)
            take = min(left, amount)
            due = min(row["due_date"], day) if (day and row["due_date"]) else (row["due_date"] or day)
            if out and out[-1]["due_date"] == due:
                out[-1]["amount"] += take
            else:
                out.append({"due_date": due, "amount": take})
            left -= take
            if i < len(act):
                act[i][1] -= take
                if act[i][1] <= 0:
                    i += 1
        if row["amount"] <= 0:
            out.append({"due_date": row["due_date"], "amount": row["amount"]})
    remaining = Decimal(settled)
    for row in out:
        applied = min(remaining, row["amount"])
        remaining -= applied
        row["settled"], row["outstanding"] = applied, row["amount"] - applied
    return out


def _standing(bill, day):
    """[(paid on, amount)] of payments against the bill made by `day` that still stand."""
    return [(to_date(row.payment.payment_date), row.amount) for row in bill.payment_allocations.all()
            if not row.payment.is_voided() and to_date(row.payment.payment_date) <= day]


def msme_bills(start, end, as_of=None):
    """
    Bills dated `start` to `end` from micro and small vendors, a row for
    each delivery's part of each: when it was due under the Act, when it was
    paid, how late, and what was still unpaid on `as_of` past its date (the
    year-end figure 43B(h) adds back). A part whose objection stands has no
    due day yet: `due_pending`, and no days late.
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
        # What settles it other than money paid out (debit notes, tax
        # deducted, a discount) is counted whenever it happened.
        total = bill.total()
        other = total - bill.amount_due() - bill.amount_paid()
        udyam = getattr(getattr(bill.vendor, "tax_profile", None), "udyam_number", "")
        # One row per delivery's part, each late from its own day (O180), the
        # oldest settled first: 600 due 16 Jul and 400 due 4 Aug, paid together
        # on 10 Aug, are 25 and 6 days late, not one bill 6 days late.
        money = ([(None, other)] if other > 0 else []) + sorted(_standing(bill, as_of), key=lambda row: row[0])
        for due, amount in msme_schedule(bill):
            left, paid_on = amount, None
            while left > 0 and money:
                day, held = money[0]
                take = min(left, held)
                left -= take
                if day is not None and take > 0:
                    paid_on = max(paid_on or day, day)
                if held - take > 0:
                    money[0] = (day, held - take)
                else:
                    money.pop(0)
            unpaid = max(left, Decimal("0"))
            paid_on = paid_on if unpaid <= 0 else None
            rows.append({
                "bill": bill.pk, "number": bill.number, "vendor": bill.vendor.name,
                "category": bill.msme_category, "udyam": udyam,
                "bill_date": to_date(bill.bill_date), "total": total, "amount": amount,
                # An objection that stands: the Act's clock has not started for
                # this delivery, so it is pending, not on time.
                "due": due, "due_pending": due is None, "paid_on": paid_on,
                "days_late": None if due is None else max(((paid_on or as_of) - due).days, 0),
                "unpaid": unpaid,
                "at_risk": unpaid if due is not None and as_of > due else Decimal("0"),
            })
    return rows
