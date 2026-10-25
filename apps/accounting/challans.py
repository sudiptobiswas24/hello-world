"""
Paying tax held for the government over to it by challan: TDS the company
deducted from vendors, TCS it collected from buyers. One mechanism, so a
fix to either is a fix to both (mistake 5): the TDS challan's locks were
found missing once (O64), and a second copy would have needed finding again.
"""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.models import correction_date, lock_rows, to_date

from .models import JournalEntry, JournalLine


from apps.core.models import takes_back  # noqa: E402  (O354: a correction step)

def pay_over(section, waiting, live, bank_account, date, challan_number, what, memo, make_challan,
             signed=lambda row: row.amount):
    """
    Pay over the rows of `waiting` (a queryset of one model, each with an
    `amount`, a `challan` and the `posted_account` it was owed into) that
    `live` keeps, under `section`: Dr each row's kept payable account / Cr
    bank. Never the section's account as it stands: moved after a row
    posted, the challan cleared an account that never held the tax (review
    F3). `make_challan(amount, entry)` creates the challan the rows are then
    marked paid by.

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
        list(model.objects.select_for_update(no_key=True).filter(pk__in=list(waiting.values_list("pk", flat=True)))
             .order_by("pk").values_list("pk", flat=True))
        owed = list(live(waiting))
        if not owed:
            raise ValidationError(f"Nothing {what} is waiting to be paid over.")
        latest = max(to_date(row.date) for row in owed)
        if to_date(date) < latest:
            # Paid over before it was held: a challan dated 1 September for tax collected on the 10th (review F16).
            raise ValidationError(f"A challan pays over tax already {what.split(' ', 1)[0]}: the last of it is dated "
                                  f"{latest:%d %b %Y}, after {to_date(date):%d %b %Y}.")
        by_account = defaultdict(Decimal)
        for row in owed:
            by_account[row.posted_account_id] += signed(row)
        total = sum(by_account.values(), Decimal("0"))
        if total <= 0:
            raise ValidationError(
                f"What is waiting under {section.code} nets to {total}: more was given back than collected, "
                "and nothing is paid over. It is set against a later month's.")
        entry = JournalEntry.objects.create(date=to_date(date), reference=challan_number, memo=memo)
        for account_id, amount in sorted(by_account.items()):
            if amount:
                JournalLine.objects.create(entry=entry, account_id=account_id, debit=max(amount, Decimal("0")),
                                           credit=max(-amount, Decimal("0")), description=memo)
        JournalLine.objects.create(entry=entry, account=bank_account, credit=total, description=memo)
        entry.post()
        challan = make_challan(total, entry)
        model.objects.filter(pk__in=[row.pk for row in owed]).update(challan=challan)
        return challan


@takes_back
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
