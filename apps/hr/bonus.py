"""
The statutory bonus for an accounting year: who is owed it, the year's
statement, its declaration and its payout (known limit 19).

The law, as the code reads it (Payment of Bonus Act 1965, carried into the
Code on Wages 2019 ss.26 to 39; the dated figures are statutory.FigureKind,
confirmed by the Controller, never constants here):

- The accounting year is 1 April to 31 March (s.2(1) of the Act).
- An employee is owed a bonus who has worked at least thirty working days
  in the year (s.8; Code s.26(1)): LEAST_DAYS. The days are the paid days
  each posted slip kept (working days employed less unpaid leave and
  absences): paid leave counts as worked (s.14).
- It is a per cent of the wages earned in the year, the same for everyone,
  declared once the year has closed, from the dated least (8.33) to the
  dated most (20) (ss.10, 11; Code s.26(1), (3)); never less than 100
  rupees for someone owed one (s.10; Code s.26(1)): LEAST_BONUS.
- The wages counted are each month's basic and dearness allowance up to the
  calculation ceiling (the higher of the dated 7,000 and the minimum wage),
  and only of months whose wages were within the dated eligibility ceiling
  (21,000): s.12 and s.2(13); Code s.26(1), (2). The pay run works that out
  month by month for the accrual and keeps the wages it counted on the line
  (kept_terms["bonus_wages"]); the statement adds up what was kept.

Each month's run accrues the bonus at the rate on the person's pay (the
BONUS component, an employer cost into bonus payable). The statement sets
what is owed at the declared per cent against what was accrued; the
declaration books the difference, person by person (more owed: an expense;
less, or nothing for someone under thirty days: taken back), and keeps
every figure it posted at. The payout is an earning that debits bonus
payable (BONUS-PAID), put on the pay of a month by the declaration and
paid by that month's run.

Nothing here is stored that can be worked out: what was paid is read off
the posted runs, so a payout run voided leaves the bonus owed again.
Reverses: the declaration is voided (never edited or deleted), which is
refused while a payout of it stands on a posted run; its payout rows not
yet paid are taken off pay with it, or on their own (take_off_pay). A pay
run of a declared year is neither posted nor voided until the declaration
is voided: the declaration posted at what those runs came to.
"""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.accounting.models import round_money
from apps.core.models import AuditModel, correction_date, lock_rows, serialised, takes_back, to_date

from .statutory import BONUS_CEILING, FigureKind, read_figure

# Payment of Bonus Act 1965 s.8, Code on Wages 2019 s.26(1): thirty working days in the accounting year.
LEAST_DAYS = 30
# s.10 of the Act, s.26(1) of the Code: 8.33 per cent of the wages earned or one hundred rupees, the higher.
LEAST_BONUS = Decimal("100.00")
# s.19 of the Act, s.39 of the Code: paid within eight months of the close of the accounting year.
PAY_WITHIN_MONTHS = 8
BONUS_WAGES = "bonus_wages"


def accounting_year(year):
    """(first day, last day) of the accounting year that starts in calendar year `year`: 1 April to 31 March."""
    return datetime.date(year, 4, 1), datetime.date(year + 1, 3, 31)


def year_name(year):
    return f"{year}-{(year + 1) % 100:02d}"


def bonus_owed(days, wages, percent):
    """What is owed on `wages` counted over `days` worked, at `percent`: nothing under thirty days."""
    if days < LEAST_DAYS or wages <= 0:
        return Decimal("0.00")
    return max(round_money(wages * percent / Decimal("100")), LEAST_BONUS)


def standing_declaration(year):
    return BonusDeclaration.objects.filter(year=year, voided_at__isnull=True).first()


def _worked_out(year):
    """
    Each person's year off the posted runs whose period ends in it: days
    worked, wages counted, accrued, and where their last accrual landed.
    """
    from .payroll import Payslip, PayRunStatus, PayslipLine

    start, end = accounting_year(year)
    people = {}

    def of(employee):
        return people.setdefault(employee.pk, {
            "employee": employee, "days": Decimal("0"), "wages": Decimal("0.00"), "accrued": Decimal("0.00"),
            "expense": None, "payable": None, "notes": []})

    posted = Q(run__status=PayRunStatus.POSTED, run__period_end__gte=start, run__period_end__lte=end)
    for slip in Payslip.objects.filter(posted).select_related("employee__party").order_by("run__period_end", "pk"):
        person = of(slip.employee)
        if slip.kept_paid_days is None:
            person["notes"].append("a slip from before its days were kept counts none")
        person["days"] += slip.kept_paid_days or Decimal("0")
    lines = PayslipLine.objects.filter(
        Q(kept_terms__has_key=BONUS_WAGES) | Q(component__ceiling_figure=BONUS_CEILING),
        payslip__run__status=PayRunStatus.POSTED, payslip__run__period_end__gte=start,
        payslip__run__period_end__lte=end,
    ).select_related("payslip__employee__party").order_by("payslip__run__period_end", "payslip_id", "pk")
    counted = set()
    for line in lines:
        person = of(line.payslip.employee)
        person["accrued"] += line.amount
        person["expense"], person["payable"] = line.posted_account_id, line.posted_liability_account_id
        kept = (line.kept_terms or {}).get(BONUS_WAGES)
        if kept is None:
            # Posted before the wages were kept: worked back from the amount, and said.
            person["wages"] += round_money(line.amount * 100 / line.rate) if line.rate else Decimal("0.00")
            note = "wages worked back from the accrual of a slip posted before they were kept"
            if note not in person["notes"]:
                person["notes"].append(note)
        elif (line.payslip_id, line.component_id) not in counted:
            # Kept once for the slip's component, on each of its lines (a rate changed inside the month is two).
            counted.add((line.payslip_id, line.component_id))
            person["wages"] += Decimal(kept)
    return people


def bonus_statement(year, percent=None):
    """
    The year's statement: each person paid in it, the days they worked,
    the wages counted, the bonus owed at `percent` (the standing
    declaration's, or else the dated least), what has been accrued, the
    difference, and what has been paid out. Derived each time.
    """
    start, end = accounting_year(year)
    declaration = standing_declaration(year)
    note = ""
    if percent is None and declaration is not None:
        percent = declaration.percent
    elif percent is None:
        try:
            percent = read_figure("Bonus", FigureKind.BONUS_MINIMUM_PERCENT, end)[0]
        except ValidationError as refused:
            # The statement still opens: what was worked and accrued is known; what is owed waits for the figure.
            note = f"Nothing owed is shown: {refused.messages[0]}"
    percent = Decimal(percent) if percent is not None else None
    kept = {line.employee_id: line for line in declaration.lines.select_related("payout")} if declaration else {}
    rows = []
    for person in sorted(_worked_out(year).values(), key=lambda p: p["employee"].employee_number):
        employee, line = person["employee"], kept.get(person["employee"].pk)
        owed = bonus_owed(person["days"], person["wages"], percent) if percent is not None else None
        accrued = person["accrued"] + (line.difference if line is not None else Decimal("0.00"))
        paid = line.paid() if line is not None else Decimal("0.00")
        why = ""
        if person["wages"] <= 0:
            why = "No month's wages were within the eligibility ceiling"
        elif person["days"] < LEAST_DAYS:
            why = f"Worked under {LEAST_DAYS} days"
        rows.append({
            "employee": employee.pk, "number": employee.employee_number, "name": employee.party.name,
            "days": person["days"], "wages": person["wages"], "owed": owed, "accrued": accrued,
            "difference": (owed - accrued) if owed is not None else None, "paid": paid,
            "to_pay": (owed - paid) if line is not None else None,
            "on_pay": line.payout.effective_from if line is not None and line.payout_id else None,
            "why": "; ".join([why] * bool(why) + person["notes"]),
            "expense": person["expense"], "payable": person["payable"]})
    total = lambda name: sum((row[name] for row in rows if row[name] is not None), Decimal("0.00"))  # noqa: E731
    month = end.month + PAY_WITHIN_MONTHS
    pay_by = datetime.date(end.year + (month - 1) // 12, (month - 1) % 12 + 1, 30)
    return {
        "year": year, "name": year_name(year), "from": start, "to": end, "percent": percent,
        "declaration": declaration.pk if declaration is not None else None,
        "declared_on": declaration.date if declaration is not None else None,
        "pay_by": pay_by, "rows": rows, "note": note,
        "total": {name: total(name) for name in ("wages", "owed", "accrued", "difference", "paid")},
    }


def people_without(component, day):
    """
    Everyone in service on `day` who is paid one of `component`'s base
    components and has no row of `component` in force that day, by
    employee number: who an accrual set up for everybody has missed.
    """
    from .models import Employee
    from .payroll import EmployeeCompensation

    day = to_date(day)
    in_force = EmployeeCompensation.objects.filter(effective_from__lte=day).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=day))
    waged = set(in_force.filter(component__in=component.base_components.all()).values_list("employee_id", flat=True))
    given = set(in_force.filter(component=component).values_list("employee_id", flat=True))
    # Active employees, as go_live_check's other lines count them (statutory_check.statutory_gaps).
    return [person for person in Employee.objects.filter(pk__in=waged - given, employment_status="active")
            .select_related("party").order_by("employee_number") if person.is_employed_on(day)]


def give_to_everyone(component, rate, day, note):
    """A row of `component` at `rate` for each of people_without(component, day), from the month `day` is in."""
    from .payroll import EmployeeCompensation

    day = to_date(day)
    given = []
    for person in people_without(component, day):
        EmployeeCompensation.objects.create(
            employee=person, component=component, amount=rate,
            effective_from=max(day.replace(day=1), to_date(person.hire_date)), note=note[:255])
        given.append(person)
    return given


def bonus_components():
    """The active accruals under the bonus rule: an employer's cost taken of wages up to the bonus ceiling."""
    from .payroll import ComponentKind, PayComponent

    return list(PayComponent.objects.filter(is_active=True, ceiling_figure=BONUS_CEILING,
                                            kind=ComponentKind.EMPLOYER_COST).order_by("code"))


def give_statutory_accruals(day=None):
    """
    Give the bonus accrual to everyone in service who is paid its wages and
    does not have it, at the dated least per cent (the plant types a higher
    rate on the person's pay when it declares one), and the gratuity
    provision (GRATUITY) at 4.81%, the Act's 15/26 of a month a year. Sentences saying what
    was done, for setup_payroll and setup_statutory, which both call this:
    whichever runs last finds the component and the figure.
    """
    from .statutory import figure_standing

    day = to_date(day) or timezone.localdate()
    said = []
    least = figure_standing(FigureKind.BONUS_MINIMUM_PERCENT, day)
    for component in bonus_components():
        if least is None:
            if not people_without(component, day):
                continue
            said.append(f"{component.code} is given to nobody yet: the bonus's least per cent is not loaded "
                        "(python manage.py setup_statutory, which gives it once it is).")
            continue
        given = give_to_everyone(component, least.value, day,
                                 f"The statutory bonus accrued at the least per cent, {least.value.normalize():f}")
        if given:
            said.append(f"{component.code} at {least.value.normalize():f}% given to {len(given)}: "
                        + ", ".join(person.employee_number for person in given))
    from .gratuity import PROVISION_CODE, PROVISION_PERCENT
    from .payroll import PayComponent

    provision = PayComponent.objects.filter(code=PROVISION_CODE, is_active=True).first()
    if provision is not None:
        given = give_to_everyone(provision, PROVISION_PERCENT, day,
                                 "The gratuity provision, 15/26 of a month's basic and DA a year")
        if given:
            said.append(f"{provision.code} at {PROVISION_PERCENT}% given to {len(given)}: "
                        + ", ".join(person.employee_number for person in given))
    return said


def refuse_a_run_of_a_declared_year(run, doing):
    """A pay run of a year whose bonus is declared is not posted or voided under it: shared by both paths."""
    start = to_date(run.period_end)
    year = start.year if start.month >= 4 else start.year - 1
    declaration = standing_declaration(year)
    if declaration is not None:
        raise ValidationError(
            f"{declaration} was worked out on the pay runs of {year_name(year)}, and {run} is one of them: it is "
            f"not {doing} while that stands. Void the declaration first, and declare again after.")


class BonusDeclaration(AuditModel):
    """
    The bonus declared for one accounting year, posted as made: it books
    the difference between what is owed at its per cent and what the
    year's runs accrued, and keeps each person's figures. Voided, never
    edited or deleted.
    """

    year = models.PositiveSmallIntegerField(
        help_text="The accounting year, by the calendar year it starts in: 2025 is 1 April 2025 to 31 March 2026.")
    percent = models.DecimalField(max_digits=7, decimal_places=4,
                                  help_text="The bonus declared, per cent of the wages counted.")
    date = models.DateField(null=True, blank=True,
                            help_text="The day it is declared and booked: the plant's today if left empty.")
    memo = models.CharField(max_length=255, blank=True)
    kept_terms = models.JSONField(null=True, blank=True, editable=False,
                                  help_text="The dated least and most per cent it was declared within.")
    journal_entry = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="+", editable=False)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_entry = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+", editable=False)

    class Meta:
        ordering = ["-year", "-id"]
        permissions = [("void_bonusdeclaration", "Can void a year's bonus declaration")]
        constraints = [
            models.UniqueConstraint(fields=["year"], condition=Q(voided_at__isnull=True),
                                    name="one_bonus_declaration_stands_for_a_year"),
            models.CheckConstraint(check=Q(percent__gt=0), name="bonus_declaration_percent_positive"),
        ]

    def __str__(self):
        return f"Bonus for {year_name(self.year)} at {self.percent.normalize():f}%"

    def stands(self):
        return self.voided_at is None

    def _runs(self):
        from .payroll import PayRun

        start, end = accounting_year(self.year)
        return list(PayRun.objects.filter(period_end__gte=start, period_end__lte=end).order_by("pk"))

    @transaction.atomic
    def save(self, *args, **kwargs):
        from apps.accounting.models import JournalEntry, JournalLine

        from .payroll import PayRunStatus

        if self.pk is not None:
            raise ValidationError(f"{self} is the record of what was declared; void it and declare again.")
        if self.year is None or self.percent is None:
            raise ValidationError({"year": "Say the accounting year and the per cent declared."})
        self.percent = Decimal(self.percent)
        start, end = accounting_year(self.year)
        if end >= timezone.localdate():
            raise ValidationError({"year": (
                f"{year_name(self.year)} closes on {end:%d %b %Y}: a bonus is declared on a year that has closed. "
                "Until then the statement shows what has been accrued.")})
        # Every run of the year, held before any of it is read: one posting meanwhile waits, and is then
        # refused under this declaration (refuse_a_run_of_a_declared_year).
        runs = self._runs()
        if runs:
            lock_rows(*runs)
        if standing_declaration(self.year) is not None:
            raise ValidationError({"year": (
                f"{standing_declaration(self.year)} stands. Void it before declaring {year_name(self.year)} again.")})
        open_runs = [run for run in self._runs() if run.status in (PayRunStatus.DRAFT, PayRunStatus.CALCULATED)]
        if open_runs:
            raise ValidationError({"year": (
                f"{len(open_runs)} pay run(s) of {year_name(self.year)} are still to post or delete, the first "
                f"{open_runs[0].number or open_runs[0].name or open_runs[0].pk}: the bonus is declared on the "
                "year's wages as posted.")})
        least, least_term = read_figure("Bonus", FigureKind.BONUS_MINIMUM_PERCENT, end)
        most, most_term = read_figure("Bonus", FigureKind.BONUS_MAXIMUM_PERCENT, end)
        if not least <= self.percent <= most:
            raise ValidationError({"percent": (
                f"The law's bonus on {end:%d %b %Y} is from {least.normalize():f}% to {most.normalize():f}% of "
                f"wages, and {self.percent.normalize():f}% is outside it.")})
        self.kept_terms = {FigureKind.BONUS_MINIMUM_PERCENT.value: least_term,
                           FigureKind.BONUS_MAXIMUM_PERCENT.value: most_term}
        self.date = correction_date(self.date, end + datetime.timedelta(days=1),
                                    f"The bonus for {year_name(self.year)} is not declared on",
                                    "the year after it began")
        statement = bonus_statement(self.year, self.percent)
        if not statement["rows"]:
            raise ValidationError({"year": f"No pay run was posted for {year_name(self.year)}: nobody is owed a bonus."})
        super().save(*args, **kwargs)
        moved = defaultdict(lambda: Decimal("0.00"))
        for row in statement["rows"]:
            if row["difference"] and not (row["expense"] and row["payable"]):
                raise ValidationError(
                    f"{row['number']} {row['name']} is owed {row['owed']} and no bonus was accrued on their pay, so "
                    "there is no account to book it to. Give them the bonus component and a run that accrues it.")
            BonusDeclarationLine.objects.create(
                declaration=self, employee_id=row["employee"], days_worked=row["days"], wages_counted=row["wages"],
                owed=row["owed"], accrued=row["accrued"], difference=row["difference"],
                expense_account_id=row["expense"] if row["difference"] else None,
                payable_account_id=row["payable"])
            moved[(row["expense"], row["payable"])] += row["difference"]
        if any(moved.values()):
            label = f"{self}: owed less accrued"[:255]
            entry = JournalEntry.objects.create(date=self.date, reference=f"BONUS-{year_name(self.year)}",
                                                memo=self.memo or label)
            for (expense, payable), amount in sorted(moved.items(), key=lambda item: (item[0][0] or 0, item[0][1] or 0)):
                if not amount:
                    continue
                # More owed than accrued is an expense into bonus payable; less is taken back out of it. Written
                # directly: a pay run's posting books only what a slip's lines hold, and a line is never negative.
                up, down = (amount, Decimal("0.00")) if amount > 0 else (Decimal("0.00"), -amount)
                JournalLine.objects.create(entry=entry, account_id=expense, debit=up, credit=down, description=label)
                JournalLine.objects.create(entry=entry, account_id=payable, debit=down, credit=up, description=label)
            entry.post()
            self.journal_entry = entry
            super().save(update_fields=["journal_entry", "updated_at"])

    def delete(self, *args, **kwargs):
        raise ValidationError(f"{self} is posted: void it instead.")

    def _payout_component(self, line):
        from .payroll import ComponentBasis, ComponentKind, PayComponent

        found = list(PayComponent.objects.filter(
            is_active=True, kind=ComponentKind.EARNING, basis=ComponentBasis.FIXED,
            expense_account_id=line.payable_account_id).order_by("pk"))
        if len(found) != 1:
            raise ValidationError(
                f"{'No' if not found else len(found)} active fixed earning(s) are charged to "
                f"{line.payable_account}, the bonus payable: the payout needs exactly one (BONUS-PAID, which "
                "python manage.py setup_payroll seeds).")
        return found[0]

    @serialised("voided_at")
    def put_on_pay(self, month):
        """
        Put what is still to pay on the pay of the month `month` falls in:
        a row of the payout earning for each person owed, for that month
        alone. The month's run pays it and debits bonus payable.
        """
        from .payroll import EmployeeCompensation

        if self.voided_at is not None:
            raise ValidationError(f"{self} has been voided: nothing of it is paid.")
        month = to_date(month)
        if month is None:
            raise ValidationError({"month": "Say the month whose pay it goes on, by any day in it."})
        first = month.replace(day=1)
        last = (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
        if last < self.date:
            raise ValidationError({"month": (
                f"{self} was declared on {self.date:%d %b %Y}; it is not paid on the pay of {first:%B %Y}, before it.")})
        made = []
        for line in self.lines.select_related("employee__party", "payable_account").order_by("employee__employee_number"):
            if line.owed <= 0 or line.payout_id is not None:
                continue
            component = self._payout_component(line)
            taken = EmployeeCompensation.objects.filter(
                employee=line.employee, component=component, effective_from__lte=last,
            ).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=first)).first()
            if taken is not None:
                raise ValidationError(
                    f"{line.employee} already has {component.code} on their pay in {first:%B %Y} ({taken}): two "
                    "payouts in one month could not be told apart. Choose another month.")
            line.payout = EmployeeCompensation.objects.create(
                employee=line.employee, component=component, amount=line.owed, effective_from=first,
                effective_to=last, note=f"{self}"[:255])
            line.save(update_fields=["payout", "updated_at"])
            made.append(line)
        if not made:
            raise ValidationError(f"Nothing of {self} is left to put on pay: every bonus owed is on a month's pay.")
        return made

    def _take_off(self, refuse_paid):
        """The payout rows no posted run has paid, taken off pay; a paid one refuses when `refuse_paid`."""
        taken = []
        for line in self.lines.select_related("payout__component", "employee__party").exclude(payout=None):
            run = line.paid_by()
            if run is not None:
                if refuse_paid:
                    raise ValidationError(
                        f"{run} paid {line.employee} {line.owed} of {self}. Void that run before voiding the "
                        "declaration it paid.")
                continue
            payout, line.payout = line.payout, None
            line.save(update_fields=["payout", "updated_at"])
            payout.delete()
            taken.append(line)
        return taken

    @serialised("voided_at")
    def take_off_pay(self):
        """The reverse of put_on_pay: every payout row not yet paid by a posted run comes off pay."""
        if self.voided_at is not None:
            raise ValidationError(f"{self} has been voided: nothing of it is on pay.")
        taken = self._take_off(refuse_paid=False)
        if not taken:
            raise ValidationError(f"Nothing of {self} is on pay and unpaid: a payout a posted run paid is taken "
                                  "back by voiding that run.")
        return taken

    @serialised("voided_at")
    @takes_back
    def void(self, on_date=None, memo=""):
        """Not declared after all: its entry reversed and its unpaid payouts off pay; refused while one is paid."""
        if self.voided_at is not None:
            raise ValidationError(f"{self} has already been voided.")
        runs = self._runs()
        if runs:
            lock_rows(*runs)
        self._take_off(refuse_paid=True)
        if self.journal_entry_id is not None:
            on_date = correction_date(on_date, self.date, f"{self} is not voided on", "it was declared")
            self.voided_entry = self.journal_entry.create_reversal(entry_date=on_date,
                                                                  memo=memo or f"Void of {self}"[:255])
        self.voided_at = timezone.now()
        super().save(update_fields=["voided_at", "voided_entry", "updated_at"])
        return self


class BonusDeclarationLine(AuditModel):
    """What the declaration posted for one person: every figure as it stood, and the row that pays it."""

    declaration = models.ForeignKey(BonusDeclaration, on_delete=models.CASCADE, related_name="lines")
    employee = models.ForeignKey("hr.Employee", on_delete=models.PROTECT, related_name="+")
    days_worked = models.DecimalField(max_digits=7, decimal_places=2)
    wages_counted = models.DecimalField(max_digits=18, decimal_places=2)
    owed = models.DecimalField(max_digits=18, decimal_places=2)
    accrued = models.DecimalField(max_digits=18, decimal_places=2,
                                  help_text="What the year's runs had accrued when it was declared.")
    difference = models.DecimalField(max_digits=18, decimal_places=2,
                                     help_text="Owed less accrued, booked by the declaration: below nothing, taken back.")
    expense_account = models.ForeignKey("accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="+", help_text="Where the difference was charged.")
    payable_account = models.ForeignKey("accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="+", help_text="The bonus payable it is owed in.")
    payout = models.ForeignKey("hr.EmployeeCompensation", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="+", help_text="The row that puts it on a month's pay.")

    class Meta:
        ordering = ["declaration", "employee__employee_number"]
        constraints = [models.UniqueConstraint(fields=["declaration", "employee"], name="bonus_line_once_a_person")]

    def __str__(self):
        return f"{self.declaration}: {self.employee} {self.owed}"

    def save(self, *args, **kwargs):
        if self.pk is not None and set(kwargs.get("update_fields") or ()) != {"payout", "updated_at"}:
            raise ValidationError(f"{self} is what was declared; void the declaration and declare again.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(f"{self} is posted with its declaration: void that instead.")

    def _paying_lines(self):
        from .payroll import PayRunStatus, PayslipLine

        if self.payout_id is None:
            return PayslipLine.objects.none()
        return PayslipLine.objects.filter(
            payslip__employee_id=self.employee_id, component_id=self.payout.component_id,
            payslip__run__status=PayRunStatus.POSTED, payslip__run__period_start__lte=self.payout.effective_to,
            payslip__run__period_end__gte=self.payout.effective_from)

    def paid(self):
        """What posted runs have paid of it: read off them, so a run voided leaves it owed again."""
        # To the paisa: a sum comes back at the column's scale on PostgreSQL and bare (8400) on SQLite.
        return round_money(self._paying_lines().aggregate(paid=Sum("amount"))["paid"] or Decimal("0.00"))

    def paid_by(self):
        line = self._paying_lines().select_related("payslip__run").first()
        return line.payslip.run if line is not None else None
