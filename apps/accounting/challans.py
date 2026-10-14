"""
Paying tax held for the government over to it by challan: TDS the company
deducted from vendors, TCS it collected from buyers. One mechanism, so a
fix to either is a fix to both (mistake 5): the TDS challan's locks were
found missing once (O64), and a second copy would have needed finding again.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.models import correction_date, lock_rows, to_date

from .models import JournalEntry, JournalLine


def pay_over(section, waiting, live, bank_account, date, challan_number, what, memo, make_challan,
             signed=lambda row: row.amount):
    """
    Pay over the rows of `waiting` (a queryset of one model, each with an
    `amount` and a `challan`) that `live` keeps, under `section` (with its
    `payable_account`): Dr payable / Cr bank. `make_challan(amount, entry)`
    creates the challan the rows are then marked paid by.

    The bank is held as money inside the transaction that moves it; the
    section is locked first, then the rows in key order, and they are read
    again under the lock: read unlocked, a deduction was reversed as the
    challan paid it over, and 700 was paid for nothing (O64).
    """
    from .money import refuse_as_money_account

    with transaction.atomic():
        refuse_as_money_account(bank_account)
        lock_rows(section)
        model = waiting.model
        list(model.objects.select_for_update().filter(pk__in=list(waiting.values_list("pk", flat=True)))
             .order_by("pk").values_list("pk", flat=True))
        owed = list(live(waiting))
        if not owed:
            raise ValidationError(f"Nothing {what} is waiting to be paid over.")
        if section.payable_account_id is None:
            raise ValidationError(f"{section} has no payable account.")
        total = sum((signed(row) for row in owed), Decimal("0"))
        if total <= 0:
            raise ValidationError(
                f"What is waiting under {section.code} nets to {total}: more was given back than collected, "
                "and nothing is paid over. It is set against a later month's.")
        entry = JournalEntry.objects.create(date=to_date(date), reference=challan_number, memo=memo)
        JournalLine.objects.create(entry=entry, account=section.payable_account, debit=total, description=memo)
        JournalLine.objects.create(entry=entry, account=bank_account, credit=total, description=memo)
        entry.post()
        challan = make_challan(total, entry)
        model.objects.filter(pk__in=[row.pk for row in owed]).update(challan=challan)
        return challan


def void_challan(challan, on_date=None):
    """
    The payment over did not go through: the tax is owed again, and the
    challan's rows wait for another. On the challan's day by default, never
    before it nor on a day still to come (correction_date). The caller
    holds the challan (@serialised).
    """
    if challan.voided_entry_id:
        raise ValidationError("This challan has already been voided.")
    day = correction_date(on_date or challan.date, challan.date, f"Challan {challan} is not voided on",
                          "it was paid")
    challan.voided_entry = challan.journal_entry.create_reversal(
        entry_date=day, memo=f"Voided: {challan.journal_entry.memo}")
    challan.save(update_fields=["voided_entry", "updated_at"])
    return challan.voided_entry
