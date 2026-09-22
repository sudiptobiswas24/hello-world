"""
Scheduling a run against a machine that is already busy.

Every date this plan produced until now assumed the loom was free.
The lead time was the routing's minutes divided by the machine's open
hours, which answers "how long does this run take" and not "when can
this run happen" — and on a plant with twelve looms and a full order
book those are different questions with different answers. Two orders
needing the same loom in the same week both came back with dates
neither could keep.

**Backward from the date it is wanted.** A run is scheduled by
walking back from the day it must be finished, taking whatever the
machine has free on each day until the work is used up. The day the
work runs out is the day the run starts. That is the real release
date, and it is later than the naive one whenever the machine has
other work on it — which is the case worth knowing about.

**Operations chain.** A routing's last operation finishes on the
date the run is wanted; the one before it must finish when that one
starts. Scheduled in reverse sequence, each against its own machine,
which is what `feeds_from` already asserts about how work moves.

**Committed load is spread, and said to be.** A work order that says
it runs from the 4th to the 9th does not say which of those days the
loom is actually turning, so its minutes are spread evenly across the
working days of its window. That is an approximation and the only
honest one available: the alternative is to invent a day.

**A released run with no dates is reported, not booked.** Booking it
somewhere would make up the fact the run is missing; ignoring it
silently would have a planner promise a machine that is not free. So
it is counted beside the plan rather than inside it, the same stance
`capacity_report` already takes.
"""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError

MINUTES_PER_HOUR = Decimal("60")
ZERO = Decimal("0")

# How far back a run may be pushed before the answer stops being a
# date and starts being "this plant cannot do this". A year of
# backlog on one machine is not a schedule.
MAX_BACKLOG_DAYS = 365


class LoadBook:
    """
    What every machine has left, day by day, as the plan uses it up.

    Mutable on purpose. The whole failure this fixes is two runs being
    told the same hours are free, so the book has to remember what the
    last answer spent.
    """

    def __init__(self, warehouse, planned_on, horizon_end):
        self.warehouse = warehouse
        self.planned_on = planned_on
        self.horizon_end = horizon_end
        self._calendars = {}
        self._capacity = {}
        self._booked = defaultdict(lambda: ZERO)
        self.unscheduled = defaultdict(lambda: ZERO)
        self._load_commitments()

    # -- the machine's own days -----------------------------------------

    def calendar(self, centre):
        if centre.pk not in self._calendars:
            self._calendars[centre.pk] = centre.calendar()
        return self._calendars[centre.pk]

    def capacity_minutes(self, centre, day):
        """What this machine can do on this day, before any bookings."""
        if not self.calendar(centre).is_working(day):
            return ZERO
        if centre.pk not in self._capacity:
            self._capacity[centre.pk] = (
                Decimal(centre.available_hours_per_day) * MINUTES_PER_HOUR
            )
        return self._capacity[centre.pk]

    def free(self, centre, day):
        taken = self._booked[(centre.pk, day)]
        return max(self.capacity_minutes(centre, day) - taken, ZERO)

    def book(self, centre, day, minutes):
        self._booked[(centre.pk, day)] += minutes

    def booked(self, centre, day):
        return self._booked[(centre.pk, day)]

    # -- what is already on the machines --------------------------------

    def _load_commitments(self):
        """
        Every live run's operations, spread over the days it says it
        runs.

        Draft runs count as well as released ones, for the same reason
        they count as supply: a run somebody has decided on is going
        to occupy a machine whether or not anybody has released it.
        """
        from apps.manufacturing.orders import WorkOrderOperation, WorkOrderStatus

        operations = (
            WorkOrderOperation.objects
            .filter(
                work_order__status__in=(
                    WorkOrderStatus.DRAFT, WorkOrderStatus.RELEASED
                ),
                work_order__warehouse=self.warehouse,
            )
            .select_related("work_centre", "work_order")
        )
        for operation in operations:
            minutes = operation.planned_minutes or ZERO
            if minutes <= 0:
                continue
            order = operation.work_order
            start, end = order.scheduled_start, order.scheduled_end
            if start is None or end is None or end < start:
                # Real work that will happen sometime, and nothing says
                # when. Counted beside the plan rather than booked on a
                # day this code chose for it.
                self.unscheduled[operation.work_centre_id] += minutes
                continue
            days = [
                day for day in _days_between(start, end)
                if self.calendar(operation.work_centre).is_working(day)
            ]
            if not days:
                self.unscheduled[operation.work_centre_id] += minutes
                continue
            share = minutes / len(days)
            for day in days:
                self.book(operation.work_centre, day, share)

    # -- scheduling ------------------------------------------------------

    def take_backwards(self, centre, minutes, finish_by, floor):
        """
        Walk back from `finish_by` taking free hours until the work is
        used up, and say which day that was.

        Returns the start day and whether the walk ran out of room.
        Running out is not an error: a machine that cannot fit the work
        before the plan's own date is exactly the thing a planner needs
        telling, and refusing to answer would hide it.
        """
        remaining = Decimal(minutes)
        if remaining <= 0:
            return finish_by, False
        day = finish_by
        calendar = self.calendar(centre)
        while remaining > 0:
            if day < floor:
                return day, True
            if calendar.is_working(day):
                take = min(self.free(centre, day), remaining)
                if take > 0:
                    self.book(centre, day, take)
                    remaining -= take
            if remaining > 0:
                day -= datetime.timedelta(days=1)
        return day, False


def _days_between(start, end):
    day = start
    while day <= end:
        yield day
        day += datetime.timedelta(days=1)


def schedule_backwards(book, operations, quantity, uom, finish_by, floor):
    """
    Place a whole routing so that its last operation ends on
    `finish_by`, and say when the first one has to start.

    In reverse sequence, each operation finishing when the next one
    starts. The bottleneck is reported as the machine that held the
    work longest, because that is the one worth adding capacity to and
    the one a planner should be told about when a date slips.
    """
    cursor = finish_by
    overloaded = False
    bottleneck = None
    longest = Decimal("-1")
    spans = []
    for operation in sorted(operations, key=lambda o: o.sequence, reverse=True):
        minutes = operation.minutes_for(quantity, uom)
        start, ran_out = book.take_backwards(
            operation.work_centre, minutes, cursor, floor
        )
        overloaded = overloaded or ran_out
        spans.append({
            "operation": operation, "work_centre": operation.work_centre,
            "minutes": minutes, "start": start, "finish": cursor,
        })
        if minutes > longest:
            longest, bottleneck = minutes, operation.work_centre
        cursor = start
    return {
        "start": cursor,
        "finish": finish_by,
        "bottleneck": bottleneck,
        "overloaded": overloaded,
        "spans": list(reversed(spans)),
    }


def schedule_make(book, bom, quantity, uom, needed_by, planned_on,
                  queue_days=0):
    """
    When a run of `quantity` has to start to be finished by
    `needed_by`, given what the machines already have on them.

    Falls back to nothing when the bill of materials has no routing:
    without operations there is no machine to be busy, and the caller's
    stated default is the honest answer.
    """
    if bom is None or bom.routing_id is None:
        return None
    operations = list(bom.routing.operations.select_related("work_centre"))
    if not operations:
        return None
    floor = planned_on - datetime.timedelta(days=MAX_BACKLOG_DAYS)
    # The queue allowance comes off the finish date before anything is
    # scheduled, so it is time the run is given rather than time the
    # machines are asked to find.
    finish_by = needed_by - datetime.timedelta(days=int(queue_days))
    return schedule_backwards(
        book, operations, quantity, uom, finish_by, floor
    )


def load_profile(book, centres, start, end):
    """
    What each machine is being asked to do against what it can, week
    by week.

    Weekly rather than daily because that is the grain a planner acts
    on: a loom over its hours on one Tuesday and under them on the
    Wednesday is not a problem, and reporting it as one buries the
    week that really is full.
    """
    rows = []
    for centre in centres:
        weeks = defaultdict(lambda: {"available": ZERO, "booked": ZERO})
        for day in _days_between(start, end):
            monday = day - datetime.timedelta(days=day.weekday())
            weeks[monday]["available"] += book.capacity_minutes(centre, day)
            weeks[monday]["booked"] += book.booked(centre, day)
        for monday in sorted(weeks):
            week = weeks[monday]
            rows.append({
                "work_centre": centre,
                "week_beginning": monday,
                "available_minutes": week["available"],
                "booked_minutes": week["booked"],
                "spare_minutes": week["available"] - week["booked"],
                "utilisation_percent": (
                    week["booked"] / week["available"] * Decimal("100")
                    if week["available"] else None
                ),
                "unscheduled_minutes": book.unscheduled.get(centre.pk, ZERO),
            })
    return rows


def overloaded_weeks(book, centres, start, end):
    """Weeks where a machine is asked for more than it has, worst first."""
    rows = [
        row for row in load_profile(book, centres, start, end)
        if row["spare_minutes"] < 0
    ]
    return sorted(rows, key=lambda row: row["spare_minutes"])
