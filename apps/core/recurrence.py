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
