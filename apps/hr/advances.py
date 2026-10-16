"""
Salary advances and loans: money paid out to an employee and taken back by
the pay runs.

Given: a posted payment out to the employee, booked against the advance
receivable (the recovery component's account), for the advance's amount.
Recovered: each pay run whose period ends on or after `recover_from` adds a
deduction line of the instalment, or what is left if less, credited to the
same receivable. A leaver's final slip takes all that is left. No slip
takes an advance past the deduction cap (DEDUCTION_CAP of the wages, the
owner's decision 20 on O28): what does not fit waits for the next run, and
a leaver's rest stays on the advance, a receivable from the former
employee as O28 has it.

The balance is never stored. It is the amount less what posted runs took:
a run voided after it recovered gives its instalment back to the balance by
being voided, and nothing else needs changing.

Reverses: an advance is voided only while no posted run has recovered from
it, and voiding it voids its payment too; its payment voided on its own is
refused the same way (a void follow-up), since what was taken back would
otherwise be taken for money never given.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.core.models import AuditModel, Company, documents_it_answers_to, lock_rows, serialised, to_date

from .models import Employee, hold_the_employee


class AdvanceKind(models.TextChoices):
    ADVANCE = "advance", "Salary advance"
    LOAN = "loan", "Loan"


class SalaryAdvance(AuditModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="advances")
    kind = models.CharField(max_length=16, choices=AdvanceKind.choices, default=AdvanceKind.ADVANCE)
    payment = models.OneToOneField(
        "accounting.Payment", on_delete=models.PROTECT, related_name="salary_advance",
        help_text="The money paid out: posted, to the employee, against the recovery component's account.")
    component = models.ForeignKey(
        "hr.PayComponent", on_delete=models.PROTECT, related_name="+",
        help_text="The deduction it is recovered by; its account is the advance receivable.")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    instalment = models.DecimalField(max_digits=18, decimal_places=2,
                                     help_text="What each month's pay takes back, however many runs pay the month, "
                                               "or what is left if less.")
    recover_from = models.DateField(help_text="The first pay period ending on or after this day recovers.")
    note = models.CharField(max_length=255, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["employee", "recover_from", "id"]
        permissions = [("void_salaryadvance", "Can void a salary advance or loan")]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="advance_amount_positive"),
            models.CheckConstraint(check=Q(instalment__gt=0), name="advance_instalment_positive"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.pk} to {self.employee} {self.amount}"

    @property
    def receivable_account(self):
        """
        The account it was given against: its payment's, posted and so fixed (review RP4). Every recovery is
        credited there, whatever the recovery component's account says later.
        """
        return self.payment.counterpart_account

    def recovered(self):
        """What posted runs have taken back of it."""
        from .payroll import PayRunStatus, PayslipLine

        return PayslipLine.objects.filter(advance=self, payslip__run__status=PayRunStatus.POSTED).aggregate(
            total=Sum("amount"))["total"] or Decimal("0.00")

    def taken_in_month(self, run):
        """What other posted runs whose period ends in `run`'s month took of it: an instalment is a month's."""
        import datetime

        from .payroll import PayRunStatus, PayslipLine

        first = run.period_end.replace(day=1)
        following = (first + datetime.timedelta(days=32)).replace(day=1)
        return PayslipLine.objects.filter(
            advance=self, payslip__run__status=PayRunStatus.POSTED, payslip__run__period_end__gte=first,
            payslip__run__period_end__lt=following).exclude(payslip__run=run).aggregate(
            total=Sum("amount"))["total"] or Decimal("0.00")

    def balance(self):
        """What is still owed: nothing once the advance, or its payment on its own, is voided (review RP8)."""
        if not self.stands():
            return Decimal("0.00")
        return self.amount - self.recovered()

    def stands(self):
        return self.voided_at is None and not self.payment.is_voided()

    def locked_before_it(self):
        """The person, as save() holds them first (hold_the_employee, O20)."""
        return documents_it_answers_to(self, "employee")

    def _check_the_payment(self):
        from apps.accounting.models import PaymentDirection

        payment, component = self.payment, self.component
        from .payroll import ComponentKind

        if component.kind != ComponentKind.DEDUCTION or component.liability_account_id is None:
            raise ValidationError(f"{component.code} is not a deduction with an account; an advance is "
                                  "recovered by one whose account is the advance receivable.")
        if not payment.posted or payment.is_voided():
            raise ValidationError("That payment is not posted, or has been voided: nothing was given.")
        if payment.direction != PaymentDirection.DISBURSEMENT:
            raise ValidationError("An advance is money paid out, not money received.")
        if payment.party_id != self.employee.party_id:
            raise ValidationError(f"That payment was made to {payment.party}, not {self.employee}.")
        if payment.counterpart_account_id != component.liability_account_id:
            raise ValidationError(
                f"That payment was booked against {payment.counterpart_account}; {component.code} recovers "
                f"into {component.liability_account}, so the two would never meet.")
        if payment.currency_id not in (None, Company.get().base_currency_id):
            raise ValidationError("An advance is given and recovered in the base currency.")
        if payment.amount != self.amount:
            raise ValidationError(f"The payment is {payment.amount} and the advance {self.amount}.")
        other = SalaryAdvance.objects.filter(payment_id=payment.pk).exclude(pk=self.pk).first()
        if other is not None:
            raise ValidationError(f"That payment already gave {other}.")

    @transaction.atomic
    def save(self, *args, **kwargs):
        hold_the_employee(*self.locked_before_it())
        self.recover_from = to_date(self.recover_from)
        stored = SalaryAdvance.objects.filter(pk=self.pk).first() if self.pk else None
        if stored is not None:
            if stored.voided_at is not None:
                raise ValidationError(f"{stored} has been voided; it is the record of what was given.")
            if (stored.employee_id, stored.payment_id, stored.amount, stored.component_id) != (
                    self.employee_id, self.payment_id, self.amount, self.component_id):
                raise ValidationError(
                    f"{stored} was given by its payment; who, what and how much stay. Void it and give another.")
        else:
            self._check_the_payment()
        if self.instalment is not None and self.amount is not None and self.instalment > self.amount:
            raise ValidationError("An instalment is not more than the advance.")
        super().save(*args, **kwargs)

    @transaction.atomic
    def delete(self, *args, **kwargs):
        hold_the_employee(*self.locked_before_it())
        from .payroll import PayslipLine

        line = PayslipLine.objects.filter(advance_id=self.pk).select_related("payslip__run").first()
        if line is not None:
            raise ValidationError(f"{line.payslip.run} has worked out a recovery of it; void it instead.")
        return super().delete(*args, **kwargs)

    def recovering_runs(self):
        from .payroll import PayRun, PayRunStatus

        return PayRun.objects.filter(status=PayRunStatus.POSTED, payslips__lines__advance=self).distinct()

    def held_for_void(self):
        """
        Its payment, then the person, then itself: the order a void of the payment on its own takes them in
        (Payment.void holds the payment, then refuse_void_of_a_recovered_advance the person and the advance).
        PayRun.post holds the person through its whole recovery, so a void decides only after it.
        """
        return [self.payment, *self.locked_before_it()]

    @serialised("voided_at", held_first="held_for_void")
    def void(self, on_date=None, memo=""):
        """Take it back as never given: refused once a posted run recovered from it; its payment is voided with it."""
        if self.voided_at is not None:
            raise ValidationError(f"{self} has already been voided.")
        run = self.recovering_runs().first()
        if run is not None:
            raise ValidationError(f"{run} has recovered {self.recovered()} of {self}. Void that run first: "
                                  "until then the money was given and is being taken back.")
        self.voided_at = timezone.now()
        super().save(update_fields=["voided_at", "updated_at"])
        if not self.payment.is_voided():
            self.payment.void(memo=memo or f"Void of {self}", on_date=on_date)
        return self


def advances_to_recover(slip):
    """The advances `slip` recovers from: given, standing, due by the end of its period, oldest first."""
    return [advance for advance in SalaryAdvance.objects.filter(
        employee=slip.employee, voided_at__isnull=True, recover_from__lte=slip.run.period_end,
    ).select_related("payment__journal_entry", "component").order_by("recover_from", "pk") if advance.stands()]


def refuse_void_of_a_recovered_advance(payment, on_date):
    """
    A void follow-up: the payment that gave an advance runs have recovered from is not voided under them.

    Decided under the person's lock (review RP1), the one PayRun.post holds from re-working its slips to
    its entry: read without it, a run that had checked the advance stood committed after this read, and
    docked A1 2,000 of an advance whose payment was voided. Then the advance itself, read again.
    """
    advance = SalaryAdvance.objects.filter(payment=payment).first()
    if advance is None:
        return
    hold_the_employee(*advance.locked_before_it())
    lock_rows(advance)
    run = advance.recovering_runs().first()
    if run is not None:
        raise ValidationError(f"This payment gave {advance}, and {run} has recovered {advance.recovered()} "
                              "of it. Void that run first.")
