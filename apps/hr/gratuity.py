"""
Gratuity owed under the Payment of Gratuity Act, worked out each time.

Fifteen days' wages for every year of service, a month's wages counted
as 26 days, on the wages last drawn; a part year of more than six months
counts as a year; paid after five years' continuous service, never more
than the ceiling. A provision built up month by month from past wages
understates it once wages rise, so this is what the provision is checked
against, not a replacement for it.
"""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.accounting.models import JournalLine, round_money
from apps.core.models import (AuditModel, Company, correction_date, documents_it_answers_to, lock_rows, serialised,
                              takes_back, to_date)

# Payment of Gratuity Act 1972 s.4(3), as notified by S.O. 1420(E) of 29 March 2018: 20 lakh rupees at most.
# A notified figure, held here as it always was; for the accountant to confirm and make dated (report).
CEILING = Decimal("2000000")
# s.4(1): payable on leaving after five years' continuous service, or on death or disablement whatever the service.
PAYABLE_AFTER_YEARS = 5
# s.4(2): fifteen days' wages a year, a month counted as 26 days: 15/26 of a month's wages a year, 15/26/12 =
# 4.8077 per cent of each month's, 4.81 to the hundredth. The Act's arithmetic, not a notified rate. A provision
# at it understates once wages rise (gratuity_due is what it is set against); no actuarial valuation is made.
PROVISION_PERCENT = Decimal("4.81")
PROVISION_CODE = "GRATUITY"


class LeavingReason(models.TextChoices):
    """Why the service ended (s.4(1)): (a) superannuation, (b) retirement or resignation, (c) death or disablement."""

    SUPERANNUATION = "superannuation", "Superannuation"
    RETIREMENT = "retirement", "Retirement"
    RESIGNATION = "resignation", "Resignation"
    TERMINATION = "termination", "Termination"
    DEATH = "death", "Death"
    DISABLEMENT = "disablement", "Disablement by accident or disease"


WITHOUT_FIVE_YEARS = (LeavingReason.DEATH, LeavingReason.DISABLEMENT)


def service(hired, on_date):
    """(completed years, months over) between two days."""
    months = (on_date.year - hired.year) * 12 + on_date.month - hired.month
    if on_date.day < hired.day:
        months -= 1
    return divmod(max(months, 0), 12)


def counted_years(hired, on_date):
    """Years the Act counts: completed ones, and one more for a part above six months."""
    years, months = service(hired, on_date)
    if months > 6 or (months == 6 and _days_past(hired, on_date, years * 12 + 6) > 0):
        years += 1
    return years


def _days_past(hired, on_date, months):
    """Days `on_date` lies beyond `hired` plus `months` whole months."""
    year, month = divmod(hired.month - 1 + months, 12)
    try:
        anniversary = datetime.date(hired.year + year, month + 1, hired.day)
    except ValueError:  # 31st of a shorter month: its last day
        anniversary = datetime.date(hired.year + year, month + 2, 1) - datetime.timedelta(days=1)
    return (on_date - anniversary).days


def wage_components():
    """
    What gratuity is on: basic and dearness allowance (s.2(s)), the fixed earnings marked as the ordinary rate.
    Fixed only: an hourly or piece component's amount is a rate, and gratuity_due adds amounts as a month's
    wages. A piece-rated worker's wages (s.4(2) proviso, three months' average) are not worked out (limit 19).
    """
    from .payroll import ComponentBasis, ComponentKind, PayComponent

    return list(PayComponent.objects.filter(is_ordinary_wages=True, kind=ComponentKind.EARNING,
                                            basis=ComponentBasis.FIXED).order_by("code"))


def gratuity_due(on_date, components, provision_account=None):
    """
    Each employee in service on `on_date`: years counted, wages last drawn
    (the fixed amounts of `components` in force that day), gratuity owed
    and whether it is yet payable; with the provision's balance to set the
    total against.
    """
    from .models import Employee
    from .payroll import EmployeeCompensation

    on_date = to_date(on_date)
    people = [employee for employee in Employee.objects.filter(hire_date__lte=on_date).select_related("party")
              .order_by("employee_number") if employee.is_employed_on(on_date)]
    wages_of = defaultdict(lambda: Decimal("0"))
    for row in EmployeeCompensation.objects.filter(
            employee__in=people, component__in=components, effective_from__lte=on_date):
        if row.covers(on_date):
            wages_of[row.employee_id] += row.amount
    rows = []
    for employee in people:
        wages = wages_of[employee.pk]
        hired = to_date(employee.hire_date)
        years = counted_years(hired, on_date)
        owed = min(round_money(wages * 15 / 26 * years), CEILING)
        completed, _months = service(hired, on_date)
        rows.append({"employee": employee.pk, "number": employee.employee_number, "name": employee.party.name,
                     "hired": hired, "years": years, "wages": wages, "owed": owed,
                     "payable": completed >= 5})
    total = sum((row["owed"] for row in rows), Decimal("0"))
    provided = None
    if provision_account is not None:
        sums = JournalLine.objects.filter(account=provision_account, entry__posted=True,
                                          entry__date__lte=on_date).aggregate(d=Sum("debit"), c=Sum("credit"))
        provided = round_money((sums["c"] or Decimal("0")) - (sums["d"] or Decimal("0")))
    return {"rows": rows, "total": total, "provided": provided,
            "short": (total - provided) if provided is not None else None}


def standing_payout(employee_id):
    """The gratuity payout that stands for the person, if any: neither it nor its payment voided."""
    return next((payout for payout in GratuityPayout.objects.filter(employee_id=employee_id)
                 .select_related("payment") if payout.stands()), None)


class GratuityPayout(AuditModel):
    """
    Gratuity paid to someone who has left, drawing the provision down: a
    posted payment out to them against the provision account their runs
    accrued into, of exactly what the Act owes on their leaving day. Every
    figure is kept as it was worked out. Reverse: void, which voids its
    payment; a payment voided on its own leaves it standing for nothing
    (stands()), so it is owed again either way. Never edited or deleted.
    """

    employee = models.ForeignKey("hr.Employee", on_delete=models.PROTECT, related_name="gratuity_payouts")
    reason = models.CharField(max_length=16, choices=LeavingReason.choices)
    payment = models.OneToOneField("accounting.Payment", on_delete=models.PROTECT, related_name="gratuity_payout",
                                   help_text="The money paid, posted, to the person, against the gratuity provision.")
    left_on = models.DateField(editable=False, help_text="The leaving day it was worked out on.")
    years = models.PositiveSmallIntegerField(editable=False, help_text="Years the Act counted.")
    wages = models.DecimalField(max_digits=18, decimal_places=2, editable=False,
                                help_text="The month's basic and dearness allowance last drawn.")
    amount = models.DecimalField(max_digits=18, decimal_places=2, editable=False)
    provision_account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, related_name="+",
                                          editable=False)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-id"]
        permissions = [("void_gratuitypayout", "Can void a gratuity payout")]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="gratuity_payout_amount_positive"),
            models.CheckConstraint(check=Q(wages__gt=0), name="gratuity_payout_wages_positive"),
        ]

    def __str__(self):
        return f"Gratuity {self.pk} of {self.amount} to {self.employee}"

    def stands(self):
        return self.voided_at is None and not self.payment.is_voided()

    def locked_before_it(self):
        """The person, as save() holds them (hold_the_employee, O20)."""
        return documents_it_answers_to(self, "employee")

    def held_for_void(self):
        """
        Its payment, then the person, then itself: the order SalaryAdvance's void takes them in, and the order
        save() takes them in, which reads the payment's state under its lock before deciding.
        """
        return [self.payment, *self.locked_before_it()]

    @transaction.atomic
    def save(self, *args, **kwargs):
        from apps.accounting.models import PaymentDirection

        from .models import hold_the_employee
        from .payroll import ComponentKind, PayRunStatus, PayslipLine

        from apps.accounting.settlement import allocations_of

        if self.pk is not None:
            raise ValidationError(f"{self} is the record of what was paid; void it and pay again.")
        # The payment's state is decided on below: held and read again first, then the person (held_for_void).
        lock_rows(self.payment)
        hold_the_employee(*self.locked_before_it())
        employee = type(self.employee).objects.select_related("party").get(pk=self.employee_id)
        left = employee.termination_date
        if left is None or left > timezone.localdate():
            raise ValidationError({"employee": f"{employee} has not left: gratuity is paid on leaving (s.4(1))."})
        hired = to_date(employee.hire_date)
        completed, _months = service(hired, left)
        if completed < PAYABLE_AFTER_YEARS and self.reason not in WITHOUT_FIVE_YEARS:
            raise ValidationError({"reason": (
                f"{employee} served {completed} complete year(s); gratuity is paid after {PAYABLE_AFTER_YEARS} years' "
                "continuous service, or on death or disablement (Payment of Gratuity Act s.4(1)).")})
        standing = standing_payout(employee.pk)
        if standing is not None:
            raise ValidationError({"employee": f"{standing} stands. Void it before paying {employee} again."})
        found = [r for r in gratuity_due(left, wage_components())["rows"] if r["employee"] == employee.pk]
        row = found[0] if found else None
        if row is None or row["owed"] <= 0:
            raise ValidationError({"employee": f"No basic or dearness allowance was in force for {employee} on {left}: "
                                               "nothing is owed on wages of nothing."})
        payment = self.payment
        if not payment.posted or payment.is_voided():
            raise ValidationError({"payment": "That payment is not posted, or has been voided: nothing was paid."})
        if payment.direction != PaymentDirection.DISBURSEMENT:
            raise ValidationError({"payment": "Gratuity is money paid out, not received."})
        if payment.party_id != employee.party_id:
            raise ValidationError({"payment": f"That payment went to {payment.party}, not {employee}."})
        if payment.currency_id not in (None, Company.get().base_currency_id):
            raise ValidationError({"payment": "Gratuity is paid in the base currency."})
        if allocations_of(payment):
            raise ValidationError({"payment": f"{payment.number} settles documents already: it was not this money."})
        accrued = PayslipLine.objects.filter(
            payslip__employee=employee, payslip__run__status=PayRunStatus.POSTED, kind=ComponentKind.EMPLOYER_COST,
            posted_liability_account_id=payment.counterpart_account_id, component__code=PROVISION_CODE).exists()
        if not accrued:
            raise ValidationError({"payment": (
                f"That payment was booked against {payment.counterpart_account}, and no run accrued {employee}'s "
                "gratuity there: pay it against the gratuity provision, so the provision is drawn down.")})
        wages = round_money(row["wages"])
        if payment.amount != row["owed"]:
            raise ValidationError({"payment": (
                f"{employee} is owed {row['owed']}: {row['years']} year(s) at 15/26 of {wages}. The payment "
                f"is {payment.amount}.")})
        correction_date(payment.payment_date, left, f"{employee}'s gratuity is not paid on", "they left")
        self.left_on, self.years, self.wages, self.amount = left, row["years"], wages, row["owed"]
        self.provision_account_id = payment.counterpart_account_id
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(f"{self} is posted: void it instead.")

    @serialised("voided_at", held_first="held_for_void")
    @takes_back
    def void(self, on_date=None, memo=""):
        """
        Owed again: its payment voided with it, the provision restored. Dated as the payment's void is
        (correction_date: not before it was paid, not on a day still to come), which refuses for both.
        """
        if self.voided_at is not None:
            raise ValidationError(f"{self} has already been voided.")
        self.voided_at = timezone.now()
        super().save(update_fields=["voided_at", "updated_at"])
        if not self.payment.is_voided():
            self.payment.void(memo=memo or f"Void of {self}", on_date=on_date)
        return self
