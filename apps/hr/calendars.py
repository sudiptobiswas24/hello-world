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

# A plant shut for longer than this is not on holiday, it is closed, and
# a date computed across it would be fiction. Long enough to cross the
# longest festival shutdown an Indian plant takes and still refuse an
# empty working pattern rather than looping for ever.
MAX_SHUT_DAYS = 60


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


class WorkingCalendar:
    """
    Which days a pattern works, and the arithmetic that runs on them.

    Built as an object rather than a shelf of functions because the
    callers that need it most — a plan offsetting a lead time for every
    item it touches, a capacity report walking a window — ask the same
    question hundreds of times against the same pattern and the same
    holiday list. A function would go to the database once per question.
    This loads the holidays once and answers from memory.

    It deliberately does not know about shifts. A shift says how many
    hours a machine runs on a day it runs at all; this says which days
    those are. Conflating them gives a plant that works Saturdays at
    half capacity no way to say so.
    """

    def __init__(self, pattern=DEFAULT_WORKING_DAYS, region="", holidays=None):
        self.pattern = pattern or DEFAULT_WORKING_DAYS
        self.region = region
        self.days = parse_working_days(self.pattern)
        if holidays is None:
            rows = PublicHoliday.objects.filter(
                models.Q(region="") | models.Q(region=region)
            )
            holidays = {row.date for row in rows}
        self.holidays = holidays

    def __str__(self):
        return f"{self.pattern}" + (f" ({self.region})" if self.region else "")

    def days_a_week(self):
        """How many days of seven this pattern works."""
        return len(self.days)

    def is_working(self, day):
        return day.isoweekday() in self.days and day not in self.holidays

    def count(self, start, end):
        """Working days in a range, both ends included."""
        if end < start:
            raise ValidationError("A range cannot end before it starts.")
        total = 0
        current = start
        while current <= end:
            if self.is_working(current):
                total += 1
            current += datetime.timedelta(days=1)
        return total

    def next_working(self, day, forwards=True):
        """`day` itself if it is worked, otherwise the next one that is."""
        step = datetime.timedelta(days=1 if forwards else -1)
        guard = 0
        while not self.is_working(day):
            day += step
            guard += 1
            if guard > MAX_SHUT_DAYS:
                raise ValidationError(
                    f"No working day found within {MAX_SHUT_DAYS} days of "
                    f"{day}. A pattern of '{self.pattern}' with these holidays "
                    "never works, so no date can be computed from it."
                )
        return day

    def offset_back(self, end, days):
        """
        The working day `days` working days before `end`.

        Zero means `end` itself, pulled back to a working day if it is
        not one: a run wanted on a Sunday has to be finished by Friday,
        and saying "Sunday" would have the plant promising a date it
        does not work.
        """
        return self._offset(end, days, forwards=False)

    def offset_forward(self, start, days):
        """The working day `days` working days after `start`."""
        return self._offset(start, days, forwards=True)

    def _offset(self, anchor, days, forwards):
        if days < 0:
            raise ValidationError("An offset cannot be a negative number of days.")
        step = datetime.timedelta(days=1 if forwards else -1)
        day = self.next_working(anchor, forwards=forwards)
        remaining = int(days)
        guard = 0
        while remaining > 0:
            day += step
            guard += 1
            if guard > MAX_SHUT_DAYS * (remaining + 1):
                raise ValidationError(
                    f"Counting {days} working days from {anchor} ran past "
                    "every date worth considering. Check the working pattern."
                )
            if self.is_working(day):
                remaining -= 1
        return day


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
