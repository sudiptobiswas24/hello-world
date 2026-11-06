"""
What a former employee owes back, and how it is settled (O215).

A leaver's final pay takes back no more than the deduction cap allows, and
the rest is a receivable from the former employee: the final run debits
the component's recoverable account against their party (O28). That line
of the run's entry is the record of what is owed and where it stands; the
account is read off it, never off the component, whose account can change
after the run posted.

Settled two ways, each a record of its own here:
- a receipt: money paid back, a posted payment in from the former employee
  against that same account, as an advance is given by a posted payment out;
- a write-off: given up on, a loss to the company's bad-debt account,
  decided by the Controller (hr.write_off_shortfall) with a reason.

What is still owed is never stored: the shortfall less the settlements that
stand. Reverses: a settlement is voided, never edited or deleted. A
receipt's void voids its payment, and its payment voided on its own (a
cheque that bounced) leaves the receipt standing for nothing, so it is owed
again either way. A write-off's void reverses its entry. The run that left
the shortfall is not voided while a settlement of it stands (PayRun.void):
voided under one, the receivable would read below nothing.

An advance or loan the final pay could not take all of (O262) is settled
the same two ways, a settlement naming the advance beside the final slip:
what is left of it, against the account it was given against, the
advance's own receivable. What is left is the advance's balance(), which
counts the settlements that stand. The advance, and the payment that gave
it, are not voided while one does (SalaryAdvance.void).
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, Company, correction_date, lock_rows, serialised

from .models import hold_the_employee


from apps.core.models import takes_back  # noqa: E402  (O354: a correction step)

class SettlementKind(models.TextChoices):
    RECEIPT = "receipt", "Paid back"
    WRITE_OFF = "write_off", "Written off"


def owed_back_line(slip):
    """The line of `slip`'s run entry that made its shortfall a receivable, or None."""
    from apps.accounting.models import JournalLine

    from .payroll import PayRunStatus

    if not slip.recoverable or slip.run.status != PayRunStatus.POSTED or slip.run.journal_entry_id is None:
        return None
    # The only debit against the person in a run's entry: an advance recovered is a credit.
    return JournalLine.objects.filter(entry_id=slip.run.journal_entry_id, party_id=slip.employee.party_id,
                                      debit__gt=0).select_related("account").order_by("pk").first()


def owed_back(slip):
    """What the former employee still owes of `slip`'s shortfall: nothing unless its run stands posted."""
    if owed_back_line(slip) is None:
        return Decimal("0.00")
    settled = sum((settlement.amount for settlement in slip.settlements.all()
                   if settlement.advance_id is None and settlement.stands()), Decimal("0.00"))
    return slip.recoverable - settled


def owed_elsewhere(account, employee, leaving_out):
    """
    What every other document of `employee`'s still owes on `account` (O270): each advance of theirs given
    against it, by its balance(), and each final slip's shortfall owed into it, by owed_back(); `leaving_out`
    (the advance or the slip a settlement settles) left out. The person's balance on the account less this
    is what the books show owed on that one document.
    """
    from .advances import SalaryAdvance
    from .payroll import Payslip, PayRunStatus

    total = Decimal("0.00")
    for advance in SalaryAdvance.objects.filter(employee=employee, voided_at__isnull=True,
                                                payment__counterpart_account=account).select_related("payment"):
        if advance != leaving_out:
            total += advance.balance()
    for slip in Payslip.objects.filter(employee=employee, recoverable__gt=0, run__status=PayRunStatus.POSTED,
                                       ).select_related("run", "employee"):
        line = owed_back_line(slip) if slip != leaving_out else None
        if line is not None and line.account_id == account.pk:
            total += owed_back(slip)
    return total


def hold_the_payer(payment):
    """
    A payment check (accounting.register_payment_check) that refuses nothing: money received from an
    employee, current or former, holds them as it posts (O270), the row a write-off of what they owe
    holds before it reads the books. Unheld, a receipt of 300.00 posting as a write-off of 300.00 read a
    receivable of 400.00 both stood, and it read -200.00. A check is where this goes because it runs
    inside post()'s transaction, after the payment's own row is held and before the money moves; no
    argument of post() says "hold the payer". Money paid out to them only adds to what they owe, so a
    write-off read beside it errs low, and is not held: a salary paid holds the slip, not the person.
    """
    from apps.accounting.models import PaymentDirection

    from .models import Employee

    if payment.direction != PaymentDirection.RECEIPT or payment.party_id is None:
        return None
    people = list(Employee.objects.filter(party_id=payment.party_id).order_by("pk"))
    if people:
        hold_the_employee(*people)
    return None


class ShortfallSettlement(AuditModel):
    payslip = models.ForeignKey(
        "hr.Payslip", null=True, blank=True, on_delete=models.PROTECT, related_name="settlements",
        help_text="The final slip whose shortfall this settles, or which left the advance owed. Empty only for "
                  "an advance of someone who left before any run paid them (O262).")
    kind = models.CharField(max_length=16, choices=SettlementKind.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    advance = models.ForeignKey(
        "hr.SalaryAdvance", null=True, blank=True, on_delete=models.PROTECT, related_name="settlements",
        help_text="An advance or loan the final pay could not take all of: this settles what is left of it, "
                  "not the slip's shortfall (O262).")
    payment = models.OneToOneField(
        "accounting.Payment", null=True, blank=True, on_delete=models.PROTECT, related_name="shortfall_settlement",
        help_text="A receipt: the money paid back, posted, from the former employee, against the account the "
                  "shortfall was owed into.")
    date = models.DateField(null=True, blank=True,
                            help_text="A write-off: the day it is given up on, the plant's today if left empty. "
                                      "A receipt is dated by its payment.")
    reason = models.CharField(max_length=255, blank=True, help_text="Why it is written off; a write-off needs one.")
    posted_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False,
        help_text="A write-off: the bad-debt account it was charged to, as it stood.")
    journal_entry = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="+", editable=False)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_entry = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+", editable=False)

    class Meta:
        ordering = ["payslip", "id"]
        permissions = [
            ("write_off_shortfall", "Can write off what a former employee owes"),
            ("void_shortfallsettlement", "Can void a former employee's receipt or write-off"),
        ]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="shortfall_settlement_amount_positive"),
            models.CheckConstraint(check=Q(payslip__isnull=False) | Q(advance__isnull=False),
                                   name="shortfall_settlement_settles_something"),
            models.CheckConstraint(
                check=(Q(kind="receipt", payment__isnull=False) | Q(kind="write_off", payment__isnull=True)),
                name="shortfall_settlement_receipt_has_its_payment"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.pk} of {self.amount} for {self.person()}"

    def person(self):
        """Whose debt it settles: the slip's, or for a leaver no run paid, the advance's (O262)."""
        return self.payslip.employee if self.payslip_id else self.advance.employee

    def owed_since(self):
        """
        The day it was first owed by a former employee, which nothing settling it precedes: the final run's
        pay date, or, for someone who left before any run paid them, their leaving date (O262).
        """
        if self.payslip_id:
            return self.payslip.run.pay_date
        return self.advance.employee.termination_date

    def _reference(self):
        return self.payslip.run.number if self.payslip_id else (self.advance.payment.number or "")

    def stands(self):
        if self.voided_at is not None:
            return False
        return self.kind != SettlementKind.RECEIPT or not self.payment.is_voided()

    def held_first(self):
        """The run, then the person: the order PayRun.post and PayRun.void take them in."""
        return [self.payslip.run] if self.payslip_id else []

    def _the_advance(self, slip):
        """What is left of the advance, and the account it was given against: read under the person's lock."""
        from .advances import SalaryAdvance
        from .payroll import PayRunStatus

        advance = SalaryAdvance.objects.select_related("payment", "employee").get(pk=self.advance_id)
        if slip is None:
            return self._the_advance_of_a_leaver_no_run_paid(advance)
        if advance.employee_id != slip.employee_id:
            raise ValidationError({"advance": f"{advance} was given to {advance.employee}, not {slip.employee}."})
        if slip.run.status != PayRunStatus.POSTED or not slip.is_final():
            raise ValidationError({"payslip": (
                f"That slip is not {slip.employee}'s final pay, posted: what is left of an advance is recovered "
                "by the runs until then.")})
        left = advance.balance()
        if left <= 0:
            raise ValidationError({"advance": f"Nothing is left of {advance} to settle."})
        return advance.receivable_account, left

    def _the_advance_of_a_leaver_no_run_paid(self, advance):
        """
        O262's reverse path: someone who left before any run paid them has no final slip, and owes all that
        is left of the advance. Settled without a slip once they have left, and only while no posted run
        paid them at all; one that did is their final pay, and the settlement names it.
        """
        from .payroll import Payslip, PayRunStatus

        person = advance.employee
        left_on = person.termination_date
        if left_on is None or left_on > timezone.localdate():
            raise ValidationError({"payslip": (
                f"{person} has not left: what is left of {advance} is recovered by their pay runs.")})
        paid = Payslip.objects.filter(employee=person, run__status=PayRunStatus.POSTED).order_by(
            "-run__period_end", "-pk").first()
        if paid is not None:
            raise ValidationError({"payslip": (
                f"{paid.run} paid {person}: settle what is left of {advance} against their final slip.")})
        left = advance.balance()
        if left <= 0:
            raise ValidationError({"advance": f"Nothing is left of {advance} to settle."})
        return advance.receivable_account, left

    def _check_the_receipt(self, account):
        from apps.accounting.models import PaymentDirection

        payment, employee = self.payment, self.person()
        if not payment.posted or payment.is_voided():
            raise ValidationError("That payment is not posted, or has been voided: nothing was paid back.")
        if payment.direction != PaymentDirection.RECEIPT:
            raise ValidationError("Paying back is money received, not money paid out.")
        if payment.party_id != employee.party_id:
            raise ValidationError(f"That payment came from {payment.party}, not {employee}.")
        if payment.counterpart_account_id != account.pk:
            raise ValidationError(
                f"That payment was booked against {payment.counterpart_account}; {employee} owes into "
                f"{account}, so the two would never meet.")
        if payment.currency_id not in (None, Company.get().base_currency_id):
            raise ValidationError("What a former employee owes is paid back in the base currency.")
        if payment.amount != self.amount:
            raise ValidationError(f"The payment is {payment.amount} and the receipt {self.amount}.")
        correction_date(payment.payment_date, self.owed_since(),
                        f"{employee} did not pay back on", "it was owed from")

    def _write_off(self, account):
        from apps.accounting.models import JournalEntry, JournalLine

        if not self.reason.strip():
            raise ValidationError({"reason": "A write-off says why it is given up on."})
        # Not past what the books still show the person owing on the account: money paid back and posted,
        # not yet matched as a receipt, is not given up on as well. Asked of the settlements alone, a
        # write-off of 300.00 stood beside a 300.00 payment, and the receivable read -200.00 (g22 gate).
        # And on this document alone (O270): the person's balance on the account less what their other
        # advances and shortfalls still owe there. Asked of the whole balance, a write-off of a 5,000 advance
        # passed against 7,860.00 that was 3,860.00 another advance's and 1,000.00 paid back unmatched.
        # Read under the person's lock, which a receipt from them holds as it posts (hold_the_payer).
        employee = self.person()
        on_the_books = sum((row.debit - row.credit for row in JournalLine.objects.filter(
            account=account, party=employee.party, entry__posted=True)), Decimal("0.00")) - owed_elsewhere(
            account, employee, self.advance if self.advance_id is not None else self.payslip)
        if self.amount > on_the_books:
            raise ValidationError({"amount": (
                f"{employee} has paid back what is not yet matched to {self.advance or 'this slip'}: "
                f"the books show "
                f"{max(on_the_books, Decimal('0.00'))} still owed, not {self.amount}. Match the payment as a "
                "receipt first.")})
        loss = Company.get().bad_debt_account
        if loss is None:
            raise ValidationError("The company has no bad-debt account to write it off to. Set it, then write off.")
        self.date = correction_date(self.date, self.owed_since(),
                                    f"{employee}'s shortfall is not written off on",
                                    "it was owed from")
        label = f"Written off, owed back by {employee}: {self.reason}"[:255]
        entry = JournalEntry.objects.create(date=self.date, reference=self._reference(), memo=label)
        JournalLine.objects.create(entry=entry, account=loss, debit=self.amount, description=label)
        JournalLine.objects.create(entry=entry, account=account, party=employee.party,
                                   credit=self.amount, description=label)
        entry.post()
        self.posted_account, self.journal_entry = loss, entry

    @transaction.atomic
    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError(f"{self} is the record of what was settled; void it and record another.")
        if self.payslip_id is None and self.advance_id is None:
            raise ValidationError({"payslip": "Say which final slip, or which advance, it settles."})
        slip = self.payslip
        lock_rows(*self.held_first())
        # The person, as a receipt from them holds them as it posts (O270), slip or none.
        hold_the_employee(self.person())
        if self.advance_id is not None:
            account, left = self._the_advance(slip)
            if self.amount is not None and self.amount > left:
                raise ValidationError({"amount": f"{self.person()} owes {left} on {self.advance}, not {self.amount}."})
        else:
            line = owed_back_line(slip)
            if line is None:
                raise ValidationError(f"{slip.employee} owes nothing back on that slip: its run left no shortfall, "
                                      "or has been voided.")
            account, left = line.account, owed_back(slip)
            if self.amount is not None and self.amount > left:
                raise ValidationError({"amount": f"{slip.employee} owes {left} back on that slip, not {self.amount}."})
        if self.kind == SettlementKind.RECEIPT:
            if self.payment_id is None:
                raise ValidationError({"payment": "A receipt is the payment that brought the money back."})
            self._check_the_receipt(account)
            self.date = self.payment.payment_date
        elif self.kind == SettlementKind.WRITE_OFF:
            if self.payment_id is not None:
                raise ValidationError({"payment": "A write-off is money not received; it has no payment."})
            self._write_off(account)
        else:
            raise ValidationError({"kind": "Paid back or written off."})
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(f"{self} is posted: void it instead.")

    @serialised("voided_at", held_first="held_first")
    @takes_back
    def void(self, on_date=None, memo=""):
        """Owed again: a receipt's payment is voided with it, a write-off's entry reversed."""
        if self.voided_at is not None:
            raise ValidationError(f"{self} has already been voided.")
        if self.kind == SettlementKind.WRITE_OFF:
            on_date = correction_date(on_date, self.date, f"{self} is not voided on", "it was written off")
            self.voided_entry = self.journal_entry.create_reversal(entry_date=on_date,
                                                                  memo=memo or f"Void of {self}"[:255])
        self.voided_at = timezone.now()
        super().save(update_fields=["voided_at", "voided_entry", "updated_at"])
        if self.kind == SettlementKind.RECEIPT and not self.payment.is_voided():
            self.payment.void(memo=memo or f"Void of {self}", on_date=on_date)
        return self
