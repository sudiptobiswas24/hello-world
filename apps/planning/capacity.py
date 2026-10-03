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

**Finite: no machine time before today.** A run that cannot be fitted
between today and the day it is wanted is not booked on days that
have already gone — that made the hours it really needs look free to
the next order planned. It is booked forward from today on what the
machines actually have left, and the day it can really be finished is
said beside the day it was wanted. Its release date stays the day it
should have started, which has passed: the lateness is on the order,
not hidden by it.

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
        self._machine_lists = {}
        self._listed = {}
        self._booked = defaultdict(lambda: ZERO)
        # Minutes booked on a set of a bank's machines, keyed by the set:
        # the ones that can take a product the others cannot.
        self._pooled = defaultdict(lambda: ZERO)
        self._needs = {}
        self.unscheduled = defaultdict(lambda: ZERO)
        self.maintenance = defaultdict(lambda: ZERO)
        self._last = {}
        self._load_commitments()
        self._load_maintenance()

    # -- the machine's own days -----------------------------------------

    def calendar(self, centre):
        if centre.pk not in self._calendars:
            self._calendars[centre.pk] = centre.calendar()
        return self._calendars[centre.pk]

    def capacity_minutes(self, centre, day):
        """
        What this bank can do on this day, before any bookings.

        Summed over its machines where it lists any, each on its own
        pattern: a bank of eleven continuous looms and one kept on
        days is not twelve continuous looms, and the plan that says it
        is will promise a date on the strength of a loom that is
        switched off. A bank with no machines listed is its own single
        machine and falls back to its own hours, which is how every
        centre behaved before machines existed.

        Not cached per centre any more, because it is no longer one
        number: a bank's minutes now depend on the day, since its
        machines can be on different patterns.
        """
        machines = self._machines(centre)
        if machines or self._on_machines(centre):
            return sum((m.minutes_on(day) for m in machines), ZERO)
        if not self.calendar(centre).is_working(day):
            return ZERO
        if centre.pk not in self._capacity:
            self._capacity[centre.pk] = (
                Decimal(centre.available_hours_per_day) * MINUTES_PER_HOUR
            )
        return self._capacity[centre.pk]

    def _on_machines(self, centre):
        if centre.pk not in self._listed:
            self._listed[centre.pk] = centre.runs_on_machines()
        return self._listed[centre.pk]

    def _machines(self, centre):
        if centre.pk not in self._machine_lists:
            self._machine_lists[centre.pk] = centre.machine_list()
        return self._machine_lists[centre.pk]

    def free(self, centre, day, pool=None):
        """
        Minutes left on the bank, and where the work can only go on some
        of its machines, on those too.

        Booked against both: lined sacks on the one BCS that inserts
        liners cannot exceed that BCS, and everything together cannot
        exceed the bank. For one such set that is exactly whether a
        day's work fits; for several overlapping ones (sizes, colours)
        it is a bound the day must meet, not a proof that it does.
        """
        taken = self._booked[(centre.pk, day)]
        left = max(self.capacity_minutes(centre, day) - taken, ZERO)
        if pool is None:
            return left
        room = sum((machine.minutes_on(day) for machine in pool), ZERO)
        return min(left, max(room - self._pooled[(_key(pool), day)], ZERO))

    def book(self, centre, day, minutes, pool=None):
        self._booked[(centre.pk, day)] += minutes
        if pool is not None:
            self._pooled[(_key(pool), day)] += minutes

    def booked(self, centre, day):
        return self._booked[(centre.pk, day)]

    def pool_for(self, centre, bom):
        """
        The bank's machines that can make what `bom` makes, where that is
        not all of them; None where it is, or the bank lists no machines.
        An empty pool is a product no machine here can take.
        """
        from apps.manufacturing.machines import requirements

        machines = self._machines(centre)
        if not machines or bom is None:
            # None listed, the bank is its own one machine; listed and none
            # in service, there is no time to pool and capacity says so.
            return None
        if bom.pk not in self._needs:
            self._needs[bom.pk] = requirements(bom)
        able = tuple(machine for machine in machines
                     if not machine.refuses(self._needs[bom.pk]))
        return None if len(able) == len(machines) else able

    def checkpoint(self):
        """What is booked and what each machine last ran, to go back to."""
        return dict(self._booked), dict(self._pooled), dict(self._last)

    def rollback(self, mark):
        booked, pooled, last = mark
        self._booked = defaultdict(lambda: ZERO, booked)
        self._pooled = defaultdict(lambda: ZERO, pooled)
        self._last = dict(last)

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
            .select_related("work_centre", "work_order__bom", "machine")
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
            # On the machine it was put on, or on those that can make it.
            pool = ((operation.machine,) if operation.machine_id
                    else self.pool_for(operation.work_centre, order.bom))
            for day in days:
                self.book(operation.work_centre, day, share, pool)
        self._load_drafts()

    def _load_drafts(self):
        """
        Draft runs decided on and not yet released: firmed from the plan,
        or committed from the master schedule.

        A run's operations are only written when it is released, so a
        draft has none, and these were missed — a firmed run's hours read
        as free to the next order planned. Timed from its routing as a
        release would time it, at the achieved speed, for what it will
        start with, and spread over its dates like any other.
        """
        from apps.manufacturing.orders import WorkOrder, WorkOrderStatus

        drafts = WorkOrder.objects.filter(
            status=WorkOrderStatus.DRAFT, warehouse=self.warehouse, bom__isnull=False,
        ).exclude(operations__isnull=False).select_related("bom", "uom", "routing")
        for order in drafts:
            routing = order.routing or order.bom.routing
            if routing is None:
                continue
            quantity = order.bom.start_for(order.quantity_ordered)
            if order.uom_id != order.bom.uom_id:
                quantity = order.uom.convert_to(quantity, order.bom.uom)
            for operation in routing.operations.select_related("work_centre"):
                if operation.is_outside:
                    continue
                minutes = operation.minutes_for(quantity, order.bom.uom, bom=order.bom)
                start, end = order.scheduled_start, order.scheduled_end
                days = [] if start is None or end is None or end < start else [
                    day for day in _days_between(start, end)
                    if self.calendar(operation.work_centre).is_working(day)
                ]
                if not days:
                    self.unscheduled[operation.work_centre_id] += minutes
                    continue
                pool = self.pool_for(operation.work_centre, order.bom)
                for day in days:
                    self.book(operation.work_centre, day, minutes / len(days), pool)

    def _load_maintenance(self):
        """
        Services still on the board, booked on the day they are due.

        A dated job rather than a rule, and on its own day rather than
        spread, because unlike a run a service does say which day it
        happens. Only open jobs: one already done is history, and its
        hours are in the downtime ledger where every other stoppage
        goes.

        Planned maintenance is capacity nobody may schedule a run
        into. Leaving it out had the plan run a loom straight through
        a service the plant fully intended to do, and then blame the
        lateness on the loom.
        """
        from apps.manufacturing.maintenance import MaintenanceJob

        # Open ones only: done is history, and cancelled never happens.
        jobs = MaintenanceJob.objects.open().select_related("work_centre")
        for job in jobs:
            if job.planned_minutes <= 0:
                continue
            self.book(job.work_centre, job.due_on, job.planned_minutes,
                      (job.machine,) if job.machine_id else None)
            self.maintenance[job.work_centre_id] += job.planned_minutes

    # -- what each machine will have just run ----------------------------

    def run_minutes(self, operation, item, quantity, uom, bom=None):
        """
        Minutes for this operation of `item`, with the changeover from
        what the machine will have run just before — and the machine
        then counts as having run `item`.

        A planned run joins the end of the queue: the first follows the
        last released run (or what the machine last ran), and each one
        planned after it follows the one before. Runs of one item
        planned together therefore change over once, which is how a
        planner would load them.
        """
        from apps.manufacturing.changeover import changeover_minutes, last_in_line

        centre = operation.work_centre
        if centre.pk not in self._last:
            self._last[centre.pk] = last_in_line(centre)
        setup = changeover_minutes(
            centre, self._last[centre.pk], item, operation.setup_minutes
        )
        self._last[centre.pk] = item
        return operation.minutes_for(quantity, uom, setup=setup, bom=bom)

    # -- scheduling ------------------------------------------------------

    def take_backwards(self, centre, minutes, finish_by, floor, pool=None):
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
                take = min(self.free(centre, day, pool), remaining)
                if take > 0:
                    self.book(centre, day, take, pool)
                    remaining -= take
            if remaining > 0:
                day -= datetime.timedelta(days=1)
        return day, False


    def take_forwards(self, centre, minutes, start, ceiling, pool=None):
        """
        Walk forward from `start` taking free hours until the work is
        used up; (first day worked, last day worked, ran out).
        """
        remaining = Decimal(minutes)
        if remaining <= 0:
            return start, start, False
        day, first = start, None
        calendar = self.calendar(centre)
        while remaining > 0:
            if day > ceiling:
                return first or start, day, True
            if calendar.is_working(day):
                take = min(self.free(centre, day, pool), remaining)
                if take > 0:
                    self.book(centre, day, take, pool)
                    remaining -= take
                    first = first or day
            if remaining > 0:
                day += datetime.timedelta(days=1)
        return first or start, day, False


def _key(pool):
    return frozenset(machine.pk for machine in pool)


def _days_between(start, end):
    day = start
    while day <= end:
        yield day
        day += datetime.timedelta(days=1)


def schedule_backwards(book, operations, quantity, uom, finish_by, floor, item, bom=None):
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
        if operation.is_outside:
            # The vendor's days, counted in the world's time and not
            # booked on any machine of ours. The work has to be back by
            # the time the next step starts, so it has to leave that
            # many days earlier. Never the bottleneck: that is the
            # machine worth adding capacity to, and a vendor is not
            # one — a vendor who is too slow is a buying decision.
            start = cursor - datetime.timedelta(
                days=int(operation.outside_lead_days)
            )
            spans.append({
                "operation": operation, "work_centre": None,
                "minutes": ZERO, "start": start, "finish": cursor,
                "outside": True,
            })
            cursor = start
            continue
        minutes = book.run_minutes(operation, item, quantity, uom, bom)
        start, ran_out = book.take_backwards(
            operation.work_centre, minutes, cursor, floor,
            book.pool_for(operation.work_centre, bom),
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


def schedule_forwards(book, operations, quantity, uom, start, ceiling, item, bom=None):
    """
    Place a whole routing from `start` on, each operation starting when
    the one before it finishes, and say when the last one ends.
    """
    cursor, first, ran_out = start, None, False
    bottleneck, longest = None, Decimal("-1")
    spans = []
    for operation in sorted(operations, key=lambda o: o.sequence):
        if operation.is_outside:
            finish = cursor + datetime.timedelta(days=int(operation.outside_lead_days))
            spans.append({"operation": operation, "work_centre": None, "minutes": ZERO,
                          "start": cursor, "finish": finish, "outside": True})
            first = first or cursor
            cursor = finish
            continue
        minutes = book.run_minutes(operation, item, quantity, uom, bom)
        began, finish, short = book.take_forwards(operation.work_centre, minutes, cursor,
                                                  ceiling, book.pool_for(operation.work_centre,
                                                                         bom))
        ran_out = ran_out or short
        spans.append({"operation": operation, "work_centre": operation.work_centre,
                      "minutes": minutes, "start": began, "finish": finish})
        first = first or began
        if minutes > longest:
            longest, bottleneck = minutes, operation.work_centre
        cursor = finish
    return {"start": first or start, "finish": cursor, "bottleneck": bottleneck,
            "ran_out": ran_out, "spans": spans}


def schedule_make(book, bom, quantity, uom, needed_by, planned_on,
                  queue_days=0):
    """
    When a run of `quantity` has to start to be finished by
    `needed_by`, given what the machines already have on them — and
    which way it is made.

    The bill's own routing while it can be on time; otherwise the first
    alternate that can; and where none can, the one ready soonest. Each
    is tried on the book and taken back before the next, so only the
    routing chosen keeps its hours.

    Falls back to nothing when the bill of materials has no routing:
    without operations there is no machine to be busy, and the caller's
    stated default is the honest answer.
    """
    from apps.manufacturing.routing import routings_for

    best = None
    for routing in routings_for(bom):
        mark = book.checkpoint()
        placed = _schedule_on(book, bom, routing, quantity, uom, needed_by, planned_on,
                              queue_days)
        if placed is None:
            continue
        placed["routing"] = routing
        if not placed["overloaded"]:
            return placed
        book.rollback(mark)
        if best is None or placed["expected"] < best["expected"]:
            best = placed
    if best is None:
        return None
    # None on time: the soonest, booked again for real.
    placed = _schedule_on(book, bom, best["routing"], quantity, uom, needed_by, planned_on,
                          queue_days)
    placed["routing"] = best["routing"]
    return placed


def _schedule_on(book, bom, routing, quantity, uom, needed_by, planned_on, queue_days):
    operations = list(routing.operations.select_related("work_centre"))
    if not operations:
        return None
    # The quantity is what must be DELIVERED; the machines are asked
    # for what must be STARTED. Every planning function that takes a
    # bill and a quantity reads it that way, so that a plant expecting
    # to reject three per cent books three per cent more loom and
    # promises a date it can keep.
    quantity = bom.start_for(quantity)
    # The queue allowance comes off the finish date before anything is
    # scheduled, so it is time the run is given rather than time the
    # machines are asked to find.
    finish_by = needed_by - datetime.timedelta(days=int(queue_days))
    mark = book.checkpoint()
    placed = schedule_backwards(
        book, operations, quantity, uom, finish_by, planned_on, bom.item, bom
    )
    placed["expected"] = needed_by
    if not placed["overloaded"]:
        return placed
    # It does not fit between today and when it is wanted. Nothing is
    # booked on days already gone: forward from today, on what the
    # machines have left, and the day it can really be ready is said.
    book.rollback(mark)
    ceiling = planned_on + datetime.timedelta(days=MAX_BACKLOG_DAYS)
    forward = schedule_forwards(book, operations, quantity, uom, planned_on, ceiling,
                                bom.item, bom)
    worked = (forward["finish"] - forward["start"]).days
    return {
        # When it should have started to be on time: gone, and said so.
        "start": finish_by - datetime.timedelta(days=worked),
        "finish": finish_by,
        "can_start": forward["start"],
        "expected": forward["finish"] + datetime.timedelta(days=int(queue_days)),
        "bottleneck": forward["bottleneck"],
        "overloaded": True,
        "beyond_a_year": forward["ran_out"],
        "spans": forward["spans"],
    }


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
                "maintenance_minutes": book.maintenance.get(centre.pk, ZERO),
            })
    return rows


def overloaded_weeks(book, centres, start, end):
    """Weeks where a machine is asked for more than it has, worst first."""
    rows = [
        row for row in load_profile(book, centres, start, end)
        if row["spare_minutes"] < 0
    ]
    return sorted(rows, key=lambda row: row["spare_minutes"])
