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
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, Company, correction_date, lock_rows, serialised

from .models import hold_the_employee


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
    settled = sum((settlement.amount for settlement in slip.settlements.all() if settlement.stands()),
                  Decimal("0.00"))
    return slip.recoverable - settled


class ShortfallSettlement(AuditModel):
    payslip = models.ForeignKey("hr.Payslip", on_delete=models.PROTECT, related_name="settlements",
                                help_text="The final slip whose shortfall this settles.")
    kind = models.CharField(max_length=16, choices=SettlementKind.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
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
            models.CheckConstraint(
                check=(Q(kind="receipt", payment__isnull=False) | Q(kind="write_off", payment__isnull=True)),
                name="shortfall_settlement_receipt_has_its_payment"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.pk} of {self.amount} for {self.payslip.employee}"

    def stands(self):
        if self.voided_at is not None:
            return False
        return self.kind != SettlementKind.RECEIPT or not self.payment.is_voided()

    def held_first(self):
        """The run, then the person: the order PayRun.post and PayRun.void take them in."""
        return [self.payslip.run]

    def _check_the_receipt(self, line):
        from apps.accounting.models import PaymentDirection

        payment, employee = self.payment, self.payslip.employee
        if not payment.posted or payment.is_voided():
            raise ValidationError("That payment is not posted, or has been voided: nothing was paid back.")
        if payment.direction != PaymentDirection.RECEIPT:
            raise ValidationError("Paying back is money received, not money paid out.")
        if payment.party_id != employee.party_id:
            raise ValidationError(f"That payment came from {payment.party}, not {employee}.")
        if payment.counterpart_account_id != line.account_id:
            raise ValidationError(
                f"That payment was booked against {payment.counterpart_account}; {employee} owes into "
                f"{line.account}, so the two would never meet.")
        if payment.currency_id not in (None, Company.get().base_currency_id):
            raise ValidationError("What a former employee owes is paid back in the base currency.")
        if payment.amount != self.amount:
            raise ValidationError(f"The payment is {payment.amount} and the receipt {self.amount}.")
        correction_date(payment.payment_date, self.payslip.run.pay_date,
                        f"{self.payslip.employee} did not pay back on", "the shortfall was owed from")

    def _write_off(self, line):
        from apps.accounting.models import JournalEntry, JournalLine

        if not self.reason.strip():
            raise ValidationError({"reason": "A write-off says why it is given up on."})
        # Not past what the books still show the person owing on the account: money paid back and posted,
        # not yet matched as a receipt, is not given up on as well. Asked of the settlements alone, a
        # write-off of 300.00 stood beside a 300.00 payment, and the receivable read -200.00 (g22 gate).
        on_the_books = sum((row.debit - row.credit for row in JournalLine.objects.filter(
            account=line.account, party=self.payslip.employee.party, entry__posted=True)), Decimal("0.00"))
        if self.amount > on_the_books:
            raise ValidationError({"amount": (
                f"{self.payslip.employee} has paid back what is not yet matched to this slip: the books show "
                f"{max(on_the_books, Decimal('0.00'))} still owed, not {self.amount}. Match the payment as a "
                "receipt first.")})
        loss = Company.get().bad_debt_account
        if loss is None:
            raise ValidationError("The company has no bad-debt account to write it off to. Set it, then write off.")
        self.date = correction_date(self.date, self.payslip.run.pay_date,
                                    f"{self.payslip.employee}'s shortfall is not written off on",
                                    "it was owed from")
        label = f"Written off, owed back by {self.payslip.employee}: {self.reason}"[:255]
        entry = JournalEntry.objects.create(date=self.date, reference=self.payslip.run.number, memo=label)
        JournalLine.objects.create(entry=entry, account=loss, debit=self.amount, description=label)
        JournalLine.objects.create(entry=entry, account=line.account, party=self.payslip.employee.party,
                                   credit=self.amount, description=label)
        entry.post()
        self.posted_account, self.journal_entry = loss, entry

    @transaction.atomic
    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError(f"{self} is the record of what was settled; void it and record another.")
        slip = self.payslip
        lock_rows(*self.held_first())
        hold_the_employee(slip.employee)
        line = owed_back_line(slip)
        if line is None:
            raise ValidationError(f"{slip.employee} owes nothing back on that slip: its run left no shortfall, "
                                  "or has been voided.")
        left = owed_back(slip)
        if self.amount is not None and self.amount > left:
            raise ValidationError({"amount": f"{slip.employee} owes {left} back on that slip, not {self.amount}."})
        if self.kind == SettlementKind.RECEIPT:
            if self.payment_id is None:
                raise ValidationError({"payment": "A receipt is the payment that brought the money back."})
            self._check_the_receipt(line)
            self.date = self.payment.payment_date
        elif self.kind == SettlementKind.WRITE_OFF:
            if self.payment_id is not None:
                raise ValidationError({"payment": "A write-off is money not received; it has no payment."})
            self._write_off(line)
        else:
            raise ValidationError({"kind": "Paid back or written off."})
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(f"{self} is posted: void it instead.")

    @serialised("voided_at", held_first="held_first")
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
