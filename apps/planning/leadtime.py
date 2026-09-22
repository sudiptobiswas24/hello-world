"""
How long before it is wanted somebody has to act.

The whole value of a dated plan is this offset. Without it a shortage
is reported on the day it bites, which is the one day nothing can be
done about it; with it the same shortage is reported on the day an
order still fixes it.

Two kinds of lead time, and they are not computed the same way.

**Bought** is somebody else's promise. It is on the agreed vendor
price as `lead_time_days`, and reading it from there rather than from
a field on the item is deliberate: the same granule is three days from
the local compounder and five weeks from the importer, and which one
is being asked decides the date.

**Made** is arithmetic this system can do. The routing says how many
minutes a quantity holds each machine and the work centre says how
many hours a day it is open, so the run time is known. Queue time is
not — nothing here models a machine's backlog against a calendar — so
it is a stated allowance rather than a computed figure, and a plant
that wants it computed should be told that is what `capacity_report`
is for.

Days are working days, counted against the plant's own pattern and the
public holiday list in `hr`. They were calendar days first, with a
note saying a working calendar "would want" building — and one already
existed, three modules away, read by payroll and by nobody else. A
lead time quietly counting Sundays goes unnoticed until a delivery
does, and a plant shut for a week of Diwali finds out the hard way.

The two lead times count differently, and it is worth saying why.
**Run time is the plant's calendar**, because a loom does not weave on
a day the plant is shut. **Purchase lead time is the vendor's promise
and is counted in calendar days**, because "five weeks from the
importer" is five weeks of the world's time, weekends included — the
vendor's own shutdowns are already inside the number they quoted. The
release date that comes out is then pulled back to a day this plant
works, since nobody raises a purchase order on a day the office is
closed.
"""

import datetime
from decimal import ROUND_CEILING, Decimal

from django.core.exceptions import ValidationError

from apps.hr.calendars import WorkingCalendar

MINUTES_PER_HOUR = Decimal("60")


def _whole_days(days):
    return int(Decimal(days).to_integral_value(rounding=ROUND_CEILING))


def plant_calendar(settings=None):
    """
    The days this plant works, for offsetting a run.

    On the planning settings rather than on each work centre, because
    this is the question "is the factory open", which a single machine
    does not get to answer differently. A machine that runs fewer days
    than the plant says so in its own pattern and the capacity report
    reads that; the plan offsets against the site.
    """
    from .models import PlanningSettings

    settings = settings or PlanningSettings.get()
    return WorkingCalendar(settings.working_days, settings.holiday_region)


def buy_lead_days(item, vendor, quantity=None, on_date=None, default=None):
    """
    Days this vendor has agreed to take, or the stated fallback.

    The fallback is required rather than defaulted to zero. Zero means
    "it appears the moment it is asked for", which no purchase does,
    and a plan built on it reports no shortage until the day of.
    """
    from apps.purchasing.pricing import resolve_lead_time

    agreed = None
    if vendor is not None:
        agreed = resolve_lead_time(item, vendor, quantity=quantity, on_date=on_date)
    if agreed is None:
        agreed = default
    if agreed is None:
        raise ValidationError(
            f"Nothing says how long {item} takes to buy: no agreed price names a "
            "lead time and no default is set. A plan cannot say when to order "
            "something whose lead time is unknown."
        )
    return _whole_days(agreed)


def make_run_days(bom, quantity, uom):
    """
    How long the routing holds this quantity, in days.

    Summed per work centre and divided by that centre's own open hours,
    not by a single plant-wide day: an extruder running three shifts
    and a stitching line running one turn the same minutes into very
    different numbers of days, and the plant knows which is which.

    Operations are treated as sequential — each finishes before the
    next starts — because that is what the routing's `feeds_from`
    already asserts about how work moves. Overlapping a run from
    machine to machine shortens this, sometimes by a lot, and nothing
    here knows the transfer batch size that would let it say by how
    much.
    """
    if bom is None or bom.routing_id is None:
        return None
    per_centre = {}
    for operation in bom.routing.operations.select_related("work_centre"):
        minutes = operation.minutes_for(quantity, uom)
        centre = operation.work_centre
        per_centre.setdefault(centre.pk, [centre, Decimal("0")])[1] += minutes
    days = Decimal("0")
    for centre, minutes in per_centre.values():
        # No guard on the divisor: `work_centre_hours_in_a_day` already
        # holds it strictly between zero and twenty-four, so a check
        # here would be a branch nothing can reach, implying a state
        # the database forbids.
        days += minutes / (
            Decimal(centre.available_hours_per_day) * MINUTES_PER_HOUR
        )
    return days


def make_lead_days(item, bom, quantity, uom, queue_days=0, default=None):
    """
    Run time plus a queue allowance, never less than a day.

    Never less than a day because a run booked to start and finish on
    the same date tells a planner nothing about when to release it, and
    because the shortest real run here still has to be set up, loaded
    and got off the machine.
    """
    run = make_run_days(bom, quantity, uom)
    if run is None:
        if default is None:
            raise ValidationError(
                f"{item} is made to a bill of materials with no routing, and no "
                "default make lead time is set, so nothing knows how long a run "
                "of it takes."
            )
        run = Decimal(default)
    return max(1, _whole_days(run + Decimal(queue_days)))


def offset(needed_by, days, calendar=None):
    """
    The date work has to start for `needed_by` to hold.

    Counted in working days when a calendar is given, and landing on a
    day the plant works either way: a release date the factory is shut
    on is a date nobody acts on, so the shortage sits until Monday and
    the plan never said so.
    """
    if calendar is None:
        return needed_by - datetime.timedelta(days=int(days))
    return calendar.offset_back(needed_by, int(days))


def calendar_offset(needed_by, days, calendar=None):
    """
    `days` calendar days earlier, then back to a day the plant works.

    For a vendor's promise, which is quoted in the world's time rather
    than in this plant's working days.
    """
    day = needed_by - datetime.timedelta(days=int(days))
    if calendar is None:
        return day
    return calendar.next_working(day, forwards=False)
