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

from django.db.models import Sum

from apps.accounting.models import JournalLine, round_money
from apps.core.models import to_date

CEILING = Decimal("2000000")


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
