"""
A schedule that repeats — every week, month, quarter or year — anchored
to the day it started on, so a series begun on the 31st does not clamp
to the 28th in February and then stay there. Recurring invoices (sales)
and recurring journals (accounting) both run on it.
"""

import calendar
import datetime

from django.db import models


class RecurrenceInterval(models.TextChoices):
    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"
    QUARTERLY = "quarterly", "Quarterly"
    YEARLY = "yearly", "Yearly"


def add_interval(start, interval, count=1, anchor_day=None):
    """
    Advance a date by `count` intervals.

    `anchor_day` is the day the series is really anchored to. Without it a
    schedule starting on the 31st clamps to the 28th in February and then
    stays there — the billing date silently walks backwards. Anchoring
    means Jan 31 -> Feb 28 -> Mar 31.
    """
    if interval == RecurrenceInterval.WEEKLY:
        return start + datetime.timedelta(weeks=count)
    months = {
        RecurrenceInterval.MONTHLY: 1,
        RecurrenceInterval.QUARTERLY: 3,
        RecurrenceInterval.YEARLY: 12,
    }[interval] * count
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(anchor_day or start.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def still_to_take(schedule, due_by=None, expected=None):
    """
    Whether `schedule`, read again under its lock, still holds what the
    caller came to take. A run takes what is due by its day: a second run
    at the same moment found the first had taken it and took the next
    period's instead, a month early. A person takes the one their screen
    showed: two at once took two. The run is answered "nothing left", the
    person in words.
    """
    from django.core.exceptions import ValidationError

    from apps.core.models import to_date

    if expected is not None and schedule.next_run_date != to_date(expected):
        raise ValidationError(f"That one is taken already: the next is due on {schedule.next_run_date}.")
    return due_by is None or schedule.is_due(due_by)


def run_date(schedule, on_date=None):
    """
    The day a schedule's run is taken on (O284), for every schedule (journals, invoices): `on_date`, or
    its next run; never before the schedule starts, never a day still to come. Not even the start
    itself while it is to come, as correction_date would allow a correction: a schedule takes what
    has happened, and a rent accrual or an invoice for next year has not.
    """
    from apps.core.models import correction_date, day_that_has_come, to_date

    refused = f"{schedule.code} is not taken on"
    day = correction_date(to_date(on_date) or schedule.next_run_date, schedule.start_date, refused,
                          "the schedule starts")
    return day_that_has_come(day, refused)


def follow_the_start(schedule):
    """
    Asked in every schedule's save() (journals, invoices): a start_date edit moves next_run_date with it
    (O355). The next run stayed where it was, so a start moved past it left a next run before the start,
    which run_date refuses: every run refused, and next_run_date is not editable. Decided:

    - not yet run (its next run on or before its start as stored): the next run is the new start;
    - run already (its next run past its start): the start is where the runs taken were anchored, so it
      does not move; the edit is refused in words.

    A schedule left with its next run before its start by an edit made before this is put right by any
    save.
    """
    from django.core.exceptions import ValidationError

    from apps.core.models import to_date

    start = to_date(schedule.start_date)
    kept = (None if schedule._state.adding or schedule.pk is None else
            type(schedule)._base_manager.filter(pk=schedule.pk).values("start_date", "next_run_date").first())
    if kept is not None and kept["start_date"] != start:
        if kept["next_run_date"] is not None and kept["next_run_date"] > kept["start_date"]:
            raise ValidationError({"start_date": [
                f"{schedule.code} has run since {kept['start_date']:%d %b %Y}, its next run on "
                f"{kept['next_run_date']:%d %b %Y}: the start its runs were taken from does not move. "
                "Stop this schedule and begin another from the new day."]})
        schedule.next_run_date = start
    elif schedule.next_run_date is not None and start and schedule.next_run_date < start:
        schedule.next_run_date = start
