"""
Which days count as worked.

Leave arithmetic is wrong without this and quietly so. A week off from
Monday to Friday is five days; the same request spanning a weekend is
still five. Counting calendar days overcharges everybody who takes a
holiday across a Sunday, and counting them the same way for a part-timer
overcharges them twice.

Public holidays live here rather than being hard-coded because they
differ by country, change every year, and are the difference between an
entitlement that reconciles and one that an employee disputes.
"""

import datetime

from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import AuditModel

# ISO weekday numbers, so 1 is Monday and 7 is Sunday — the same
# convention date.isoweekday() uses, so nothing has to convert.
DEFAULT_WORKING_DAYS = "12345"


class PublicHoliday(AuditModel):
    """
    A day nobody works, so nobody spends holiday on it.

    Scoped by region because a company with an office in two countries
    has two sets, and applying one to both is how somebody loses a day
    of their entitlement to a holiday they never had.
    """

    name = models.CharField(max_length=255)
    date = models.DateField()
    region = models.CharField(
        max_length=32, blank=True,
        help_text="Country or office code. Blank means it applies to everyone.",
    )

    class Meta:
        ordering = ["date", "region"]
        constraints = [
            models.UniqueConstraint(
                fields=["date", "region"], name="one_public_holiday_per_date_and_region"
            ),
        ]
        indexes = [models.Index(fields=["date"])]

    def __str__(self):
        return f"{self.date} {self.name}" + (f" ({self.region})" if self.region else "")


def holidays_between(start, end, region=""):
    """
    Public holidays in a range, for a region.

    A blank region on the holiday means everyone, so a regional employee
    gets their own plus the company-wide ones — never only one set.
    """
    rows = PublicHoliday.objects.filter(date__gte=start, date__lte=end)
    rows = rows.filter(models.Q(region="") | models.Q(region=region))
    return {row.date for row in rows}


def parse_working_days(pattern):
    """
    "12345" to {1, 2, 3, 4, 5}.

    Refuses rather than guessing at anything else: a pattern nobody can
    read is a pattern that silently counts the wrong days, and a
    part-timer's entitlement is computed from it.
    """
    pattern = (pattern or "").strip()
    if not pattern:
        raise ValidationError("A working pattern must name at least one day.")
    days = set()
    for character in pattern:
        if character not in "1234567":
            raise ValidationError(
                f"'{character}' is not a weekday. Use 1 for Monday through 7 for Sunday."
            )
        days.add(int(character))
    return days


def working_days(start, end, pattern=DEFAULT_WORKING_DAYS, region=""):
    """
    How many working days a range covers, both ends included.

    Public holidays are removed only when they fall on a day this person
    would otherwise have worked. Removing one that lands on their day off
    would hand a part-timer a day they were never going to work.
    """
    if end < start:
        raise ValidationError("A range cannot end before it starts.")
    days = parse_working_days(pattern)
    holidays = holidays_between(start, end, region)
    total = 0
    current = start
    while current <= end:
        if current.isoweekday() in days and current not in holidays:
            total += 1
        current += datetime.timedelta(days=1)
    return total


def month_end(day):
    """The last day of the month `day` falls in."""
    if day.month == 12:
        return datetime.date(day.year, 12, 31)
    return datetime.date(day.year, day.month + 1, 1) - datetime.timedelta(days=1)


def completed_months(start, end):
    """
    How many whole months a window contains.

    A month counts once its last day is reached, so somebody who joined
    on the fifteenth accrues that month in full at the end of it. That is
    how leave policies are actually written, and the alternative —
    accruing by the day — is a different rule wearing the same name.
    """
    if end < start:
        return 0
    count, cursor = 0, start
    while cursor <= end:
        last = month_end(cursor)
        if last <= end:
            count += 1
        cursor = last + datetime.timedelta(days=1)
    return count
