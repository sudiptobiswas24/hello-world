"""
The detailed schedule: which machine runs what, from when to when.

Planning answers "can this run be done by the day it is wanted" in
whole days per bank. The floor asks a different question at six in the
morning: what goes on loom 14 next, and when does it come off. This
answers that, and it is the first place in the system a run's
operations get a time of day.

**Forward, finite, in order of when runs are due.** Every released
run's remaining operations are placed one at a time, the run due
soonest first. An operation waits for the step before it on the same
run to finish; then it goes on whichever machine in its bank would
finish it earliest, counting the changeover from what that machine
last ran or was last given. An outside step takes its vendor's lead
days and no machine of ours.

**What has started stays where it started.** An operation with time
booked against it keeps its machine and is given only its remaining
minutes; moving a half-woven roll to another loom is not a scheduling
decision anybody makes. One already given a machine — by a committed
schedule or by hand — keeps that too: the dispatcher decided, and a
rebuild does not quietly overrule them.

**A machine's day** starts when the plant's first shift starts and runs
for the machine's hours, on the days its calendar works. A 24-hour loom
therefore runs continuously; an eight-hour stitching line runs from the
morning shift's start for eight hours.

**Proposed until committed.** `build()` returns a schedule and changes
nothing; `commit()` writes each operation's machine and planned times,
which is the dispatcher's decision, taken on purpose, not a side effect
of somebody looking at the board.
"""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

ZERO = Decimal("0")
MINUTE = datetime.timedelta(minutes=1)
FAR = datetime.date(9999, 12, 31)
LOOKAHEAD_DAYS = 3 * 366


def _day_start():
    from .shifts import Shift

    first = Shift.objects.filter(is_active=True).order_by("starts_at").first()
    return first.starts_at if first else datetime.time(0, 0)


class Clock:
    """A machine's working time: which minutes it runs on."""

    def __init__(self, calendar, hours, day_start):
        self.calendar = calendar
        self.minutes = int(Decimal(hours) * 60)
        self.day_start = day_start

    def _window(self, day):
        start = datetime.datetime.combine(day, self.day_start)
        return start, start + datetime.timedelta(minutes=self.minutes)

    def windows(self, moment):
        """Working windows from the one `moment` falls in, onwards."""
        day = moment.date() - datetime.timedelta(days=1)
        for _ in range(LOOKAHEAD_DAYS):
            if self.minutes and self.calendar.is_working(day):
                start, end = self._window(day)
                if end > moment:
                    yield max(start, moment), end
            day += datetime.timedelta(days=1)
        raise ValueError("No working time within three years.")

    def advance(self, moment, minutes):
        """The moment `minutes` of working time after `moment`, and when that work began."""
        remaining = Decimal(minutes)
        began = None
        for start, end in self.windows(moment):
            if began is None:
                began = start
            if remaining <= 0:
                return began, start
            span = Decimal((end - start) / MINUTE)
            if remaining <= span:
                return began, start + datetime.timedelta(minutes=float(remaining))
            remaining -= span
        raise ValueError("unreachable")


class Resource:
    """One machine, or a bank with none listed standing in for one."""

    def __init__(self, centre, machine, day_start, start_at):
        from .changeover import on_the_machine

        self.centre, self.machine = centre, machine
        owner = machine or centre
        hours = machine.hours_per_day() if machine else centre.available_hours_per_day
        self.clock = Clock(owner.calendar(), hours, day_start)
        self.free_at = start_at
        self.last_item = on_the_machine(centre, machine)

    @property
    def code(self):
        return self.machine.code if self.machine else self.centre.code


def _remaining(operation, started_quantity):
    """Minutes an inside operation still needs, and whether it has begun."""
    booked = operation.minutes_booked()
    if operation.quantity_completed() >= started_quantity:
        return ZERO, True
    return max((operation.planned_minutes or ZERO) - booked, ZERO), booked > 0


def build(start_at=None):
    """
    The schedule, as rows of {operation, machine, start, finish,
    changeover, late}, and nothing written anywhere.
    """
    from .changeover import changeover
    from .machines import requirements
    from .tooling import tooling_ready
    from .orders import WorkOrder, WorkOrderStatus

    start_at = timezone.localtime(start_at or timezone.now()).replace(tzinfo=None, second=0,
                                                                      microsecond=0)
    day_start = _day_start()
    resources = {}

    def pool(centre):
        if centre.pk not in resources:
            machines = centre.machine_list()
            resources[centre.pk] = [
                Resource(centre, machine, day_start, start_at) for machine in machines
            ] or [Resource(centre, None, day_start, start_at)]
        return resources[centre.pk]

    orders = sorted(
        WorkOrder.objects.filter(status=WorkOrderStatus.RELEASED)
        .select_related("item").prefetch_related("operations__work_centre",
                                                 "operations__machine"),
        key=lambda order: (order.scheduled_end or FAR, order.scheduled_start or FAR, order.pk),
    )
    rows = []

    def held(operation, order, reason):
        rows.append({"operation": operation, "order": order, "machine": operation.machine,
                     "resource": None, "start": None, "finish": None, "changeover": ZERO,
                     "outside": operation.is_outside, "held": reason})

    for order in orders:
        ready = start_at
        waiting_on = None
        needs = requirements(order.bom)
        if order.scheduled_start:
            ready = max(ready, datetime.datetime.combine(order.scheduled_start, day_start))
        started = order.started_quantity()
        begun = any(_remaining(operation, started)[1] for operation in order.operations.all()
                    if not operation.is_outside)
        if not begun:
            # A printed sack not yet begun waits for its cylinders.
            tools, why = tooling_ready(order.bom, start_at.date())
            if tools is None:
                for operation in order.operations.order_by("sequence"):
                    held(operation, order, f"{why}; it cannot be printed.")
                continue
            if tools > start_at.date():
                ready = max(ready, datetime.datetime.combine(tools, day_start))
        for operation in order.operations.order_by("sequence"):
            if waiting_on is not None:
                # A step after a held one cannot be timed; it is listed as
                # waiting rather than dropped from the board.
                held(operation, order, f"Waits for step {waiting_on}, which is held.")
                continue
            if operation.is_outside:
                if operation.quantity_back() >= started:
                    continue
                finish = ready + datetime.timedelta(days=int(operation.outside_lead_days))
                rows.append({"operation": operation, "order": order, "machine": None,
                             "start": ready, "finish": finish, "changeover": ZERO,
                             "outside": True})
                ready = finish
                continue
            minutes, begun = _remaining(operation, started)
            if minutes <= 0 and begun:
                continue
            candidates = pool(operation.work_centre)
            reasons = [resource.machine.refuses(needs) for resource in candidates
                       if resource.machine is not None]
            able = [resource for resource in candidates
                    if resource.machine is None or not resource.machine.refuses(needs)]
            if not able:
                held(operation, order, f"No machine at {operation.work_centre.code} can take "
                                       f"it: {'; '.join(reasons)}.")
                waiting_on = operation.sequence
                continue
            candidates = able
            moved_from = None
            if operation.machine_id:
                # Started there, or put there by the dispatcher: it stays.
                pinned = [r for r in candidates if r.machine and
                          r.machine.pk == operation.machine_id]
                if not pinned and begun:
                    # Half a roll is on a loom now out of use. Moving it is
                    # not a scheduling decision, so it is held and said.
                    held(operation, order, f"Started on {operation.machine.code}, which is "
                                           "out of use; it is not moved to another machine.")
                    waiting_on = operation.sequence
                    continue
                if not pinned:
                    moved_from = operation.machine.code
                candidates = pinned or candidates
            best = None
            for resource in candidates:
                change, purge = (ZERO, ZERO) if begun else changeover(
                    operation.work_centre, resource.last_item, order.item,
                    operation.setup_minutes,
                )
                work = minutes if begun else minutes - operation.setup_minutes + change
                earliest = max(resource.free_at, ready)
                began, finish = resource.clock.advance(earliest, max(work, ZERO))
                key = (finish, resource.code)
                if best is None or key < best[0]:
                    best = (key, resource, began, finish, change, purge)
            _, resource, began, finish, change, purge = best
            resource.free_at = finish
            resource.last_item = order.item
            rows.append({"operation": operation, "order": order, "machine": resource.machine,
                         "resource": resource.code, "start": began, "finish": finish,
                         "changeover": change, "purge_kg": purge, "outside": False,
                         "moved_from": moved_from})
            ready = finish
    for row in rows:
        row.setdefault("held", None)
        row.setdefault("purge_kg", ZERO)
        row.setdefault("moved_from", None)
        if row["held"]:
            row["late"] = None
            continue
        due = row["order"].scheduled_end
        row["late"] = due is not None and row["finish"].date() > due
        row["start"] = timezone.make_aware(row["start"])
        row["finish"] = timezone.make_aware(row["finish"])
    return rows


@transaction.atomic
def commit(rows):
    """Write the schedule onto the operations: the dispatcher's decision."""
    written = 0
    for row in rows:
        if row["held"]:
            continue
        operation = row["operation"]
        operation.planned_start = row["start"]
        operation.planned_finish = row["finish"]
        fields = ["planned_start", "planned_finish", "updated_at"]
        if row["machine"] is not None and operation.machine_id != row["machine"].pk:
            operation.machine = row["machine"]
            fields.append("machine")
        operation.save(update_fields=fields)
        written += 1
    return written


def dispatch_list(rows):
    """The schedule by machine, each machine's work in the order it runs."""
    board = defaultdict(list)
    for row in rows:
        if row["held"]:
            board["held"].append(row)
            continue
        board["outside" if row["outside"] else row["resource"]].append(row)
    return {code: items if code == "held" else sorted(items, key=lambda row: row["start"])
            for code, items in board.items()}
