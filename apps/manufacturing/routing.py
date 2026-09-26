"""
How a thing is made, as against what it is made of.

The bill of materials says a sack takes 110 grammes of fabric. It says
nothing about the coating line, the two-colour press and the cutting
machine it passes through on the way, how long each holds it, or which
of them is the one everybody is waiting for. That is the routing, and
in a woven sack plant it is where the second quarter of the cost lives:
material is about three quarters of a sack and conversion — power,
labour, the depreciation on twelve looms — is most of the rest.

A routing hangs off the bill of materials rather than off the work
order, because it is a property of how the product is made and not of
one run of it. For a specification-driven product the specification
names it, so a computed bill of materials gets it the same way it gets
its components: worked out, not typed.

**Why the plant's stages are not all operations here.** Tape and fabric
are stocked items with their own bills of materials and their own work
orders — a roll of fabric is weighed, counted and valued on a shelf, so
it is not work in progress. So the extrusion routing has one operation
and the weaving routing has one. The sack's routing is the one with
several: laminate, print, cut, stitch, bale, all inside a single run,
because nothing between them is ever put on a shelf.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import AuditModel

MINUTES_PER_HOUR = Decimal("60")


class Routing(AuditModel):
    """An ordered list of operations, reusable across bills of materials."""

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def total_minutes(self, quantity, uom):
        """
        How long the whole routing holds `quantity`, setup included.

        `uom` is the unit the quantity is in and is required, for the
        same reason a stock movement's is: a line rated at 180 kilos an
        hour, asked how long fifty thousand PIECES take, answers eleven
        days and means nothing by it.
        """
        return sum(
            (operation.minutes_for(quantity, uom)
             for operation in self.operations.select_related("work_centre")),
            Decimal("0"),
        )

    def bottleneck(self, quantity, uom):
        """
        The operation the run waits on.

        The only one worth adding capacity to, and the one a planner
        should be told about when a date slips. Ties go to the earlier
        operation, because that is the one that delays everything after
        it as well.
        """
        slowest = None
        longest = Decimal("-1")
        for operation in self.operations.select_related("work_centre"):
            minutes = operation.minutes_for(quantity, uom)
            if minutes > longest:
                slowest, longest = operation, minutes
        return slowest


class RoutingOperation(AuditModel):
    """
    One machine holding the work for a while — or one vendor.

    Rate rather than duration: a run is quoted in units an hour on the
    shop floor ("the coating line does four hundred kilos an hour"), and
    a duration typed against a quantity goes stale the moment the
    quantity changes.

    **An outside operation is a step a vendor does.** A sack plant
    without its own coating line sends the fabric out to be laminated
    and gets it back, and the bag's routing is still laminate, print,
    cut, stitch on one run: there is no intermediate item, so the whole-
    item job work in purchasing — send components, receive a different
    finished item — does not fit. What such a step has is a lead time
    in the vendor's days rather than a rate on our machines, and a
    charge per unit rather than an hourly rate. It names no work centre,
    because it holds none of ours, and a stand-in "vendor" work centre
    would turn up in every capacity report, effectiveness table and
    maintenance list as a machine that is never busy.
    """

    routing = models.ForeignKey(
        Routing, on_delete=models.CASCADE, related_name="operations"
    )
    sequence = models.PositiveIntegerField(
        help_text="The order the work passes through. Not the primary key's "
                  "order: operations are inserted between existing ones all "
                  "the time, and renumbering them would rewrite history."
    )
    name = models.CharField(max_length=255)
    work_centre = models.ForeignKey(
        "WorkCentre", null=True, blank=True, on_delete=models.PROTECT,
        related_name="operations",
        help_text="The machine. Blank exactly when the step is done outside.",
    )
    is_outside = models.BooleanField(
        default=False,
        help_text="A vendor does this step. It takes the vendor's lead time "
                  "and charge instead of our machine time.",
    )
    outside_lead_days = models.PositiveIntegerField(
        default=0,
        help_text="Days the work is away, door to door — in the world's days "
                  "rather than this plant's working days, because the vendor "
                  "keeps their own calendar.",
    )
    outside_cost_per_unit = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True,
        help_text="The standard charge per unit of `rate_uom`. What the plan "
                  "and the standard cost use; what the vendor actually charges "
                  "arrives on the purchase order, and the difference is a "
                  "variance rather than a correction to this.",
    )
    setup_minutes = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0"),
        help_text="Once a run, whatever its size — threading a loom, washing "
                  "down a press between colours. The reason a plant hates "
                  "short runs, and invisible in any model that costs only by "
                  "the unit.",
    )
    units_per_hour = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True,
        help_text="The rate for THIS product on THIS machine. Blank falls back "
                  "to the work centre's nominal rate, which is right for a line "
                  "that runs everything at the same speed and wrong for one "
                  "that does not — so it is stated when it differs.",
    )
    rate_uom = models.ForeignKey(
        "core.UnitOfMeasure", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
        help_text="What the rate counts. Required when a rate is given, and "
                  "the whole point of it: one routing serves several bills of "
                  "materials, a kilo is not a sack, and a rate with no unit on "
                  "it is a number that will one day be read against the wrong "
                  "one.",
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["routing", "sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["routing", "sequence"],
                name="one_operation_per_routing_sequence",
            ),
            models.CheckConstraint(
                check=Q(setup_minutes__gte=0), name="setup_minutes_not_negative"
            ),
            # A rate of zero is a machine that never finishes, and the
            # division by it is where that shows up.
            models.CheckConstraint(
                check=Q(units_per_hour__isnull=True) | Q(units_per_hour__gt=0),
                name="operation_rate_positive",
            ),
            models.CheckConstraint(
                check=Q(units_per_hour__isnull=True)
                | Q(rate_uom__isnull=False),
                name="operation_rate_says_what_it_counts",
            ),
            # An inside step has a machine and no vendor terms; an
            # outside step has vendor terms and no machine, no rate and
            # no setup. Half of each is a step nothing can schedule: a
            # machine that is also away for a week, or a vendor with an
            # hourly rate on our loom.
            models.CheckConstraint(
                check=(
                    Q(is_outside=False)
                    & Q(work_centre__isnull=False)
                    & Q(outside_lead_days=0)
                    & Q(outside_cost_per_unit__isnull=True)
                ) | (
                    Q(is_outside=True)
                    & Q(work_centre__isnull=True)
                    & Q(units_per_hour__isnull=True)
                    & Q(setup_minutes=0)
                ),
                name="operation_is_inside_or_outside",
            ),
            models.CheckConstraint(
                check=Q(outside_cost_per_unit__isnull=True)
                | (Q(outside_cost_per_unit__gte=0) & Q(rate_uom__isnull=False)),
                name="outside_charge_says_what_it_counts",
            ),
        ]

    def __str__(self):
        if self.is_outside:
            return f"{self.sequence}. {self.name} (outside)"
        return f"{self.sequence}. {self.name} on {self.work_centre.code}"

    def rate(self, uom):
        """
        Units of `uom` an hour, from this operation or from the machine.

        Refused rather than guessed when neither says: a routing whose
        operation has no rate cannot be scheduled, and a run planned at
        an imaginary speed is a delivery date somebody will promise.

        Refused rather than converted when the rate counts something
        else entirely. A coating line quoted in kilogrammes, asked how
        many SACKS it does an hour, has no answer — and the plausible
        wrong one it used to give (fifty thousand pieces at a hundred
        and eighty kilos an hour: eleven days) is worse than none.
        """
        if self.is_outside:
            raise ValidationError(
                f"{self} is done by a vendor and has no rate on our machines. "
                "Ask it for its lead time and its charge instead."
            )
        if self.units_per_hour is not None:
            rate, rate_uom, source = self.units_per_hour, self.rate_uom, self
        else:
            rate, rate_uom, source = (
                self.work_centre.capacity_per_hour,
                self.work_centre.capacity_uom,
                self.work_centre,
            )
            if rate is None:
                raise ValidationError(
                    f"{self} has no rate and {self.work_centre} has no nominal "
                    "one either, so nothing here knows how long this operation "
                    "takes. Give the operation a rate."
                )
        if rate_uom is None:
            raise ValidationError(
                f"{source} is rated at {rate} an hour and does not say what it "
                f"counts, so it cannot be read against {uom}."
            )
        if rate_uom.pk == uom.pk:
            return rate
        try:
            return rate_uom.convert_to(rate, uom)
        except ValidationError:
            raise ValidationError(
                f"{source} is rated in {rate_uom} an hour and this run is "
                f"counted in {uom}, which is not the same kind of thing. A "
                "rate read against the wrong unit gives a plausible number and "
                "a date nobody can keep."
            )

    def outside_charge_for(self, quantity, uom):
        """
        The standard vendor charge for `quantity` of `uom`, or zero where
        the step is ours or its charge is not yet agreed.

        Zero rather than refused when no charge is stated, which is a
        choice and not an oversight: a plant that has not priced the
        vendor yet can still plan the run, and the whole charge then
        arrives as a variance at close — visible, and in the right
        account. Refused when the charge counts something the run is
        not counted in, for the same reason a rate is.
        """
        # No separate test for an inside step: the database refuses a
        # vendor charge on one (`operation_is_inside_or_outside`), so
        # its charge is always blank and this already answers nought.
        # A second check would be a branch nothing can reach, implying
        # a state the schema forbids.
        if self.outside_cost_per_unit is None:
            return Decimal("0")
        quantity = Decimal(quantity)
        if self.rate_uom_id != uom.pk:
            try:
                quantity = uom.convert_to(quantity, self.rate_uom)
            except ValidationError:
                raise ValidationError(
                    f"{self} is charged per {self.rate_uom} and this run is "
                    f"counted in {uom}, which is not the same kind of thing."
                )
        return quantity * self.outside_cost_per_unit

    def minutes_for(self, quantity, uom):
        """
        Setup plus run time for `quantity` of `uom`.

        Nought for an outside step: no machine of ours holds the work,
        and its time is the vendor's lead days, which are not minutes on
        anything this plant owns.
        """
        if self.is_outside:
            return Decimal("0")
        rate = self.rate(uom)
        return self.setup_minutes + (
            Decimal(quantity) / rate * MINUTES_PER_HOUR
        )


def capacity_report(work_centre, start, end):
    """
    What a machine is being asked to do in a window against what it can.

    Load is the planned minutes of every released run whose schedule
    touches the window, counted whole: a run does not half-occupy a
    loom, and a planner asking "can I take this order" is better served
    by a number that rounds against them.

    Available time is the days this machine actually works in this
    window, from its own pattern and the public holiday list — not a
    fraction of a nominal week. The fraction was here first, on the
    argument that a holiday is a company-wide fact this module had no
    business owning. The fact was already owned, in `hr`, and the
    average it was replaced by is wrong in exactly the windows a
    planner asks about: a long weekend has no capacity rather than
    three sevenths of a week's worth, and a plant shut for a festival
    has none at all.
    """
    from .orders import WorkOrderOperation, WorkOrderStatus

    if end < start:
        raise ValidationError(
            f"A window from {start} to {end} runs backwards, and the hours it "
            "reports would too."
        )
    booked = WorkOrderOperation.objects.filter(
        work_centre=work_centre,
        work_order__status=WorkOrderStatus.RELEASED,
    ).select_related("work_order")
    operations = booked.filter(
        work_order__scheduled_start__isnull=False,
        work_order__scheduled_end__isnull=False,
    ).exclude(
        Q(work_order__scheduled_end__lt=start)
        | Q(work_order__scheduled_start__gt=end)
    )
    # A released run with no dates on it is real work that will happen
    # sometime, and it belongs in neither this window nor every other
    # one. Counting it everywhere had a planner turning work away for a
    # machine that was standing idle; counting it nowhere hides it. So
    # it is reported beside the window rather than inside it.
    unscheduled = booked.filter(
        Q(work_order__scheduled_start__isnull=True)
        | Q(work_order__scheduled_end__isnull=True)
    )
    load = sum(
        (row.planned_minutes or Decimal("0") for row in operations), Decimal("0")
    )
    floating = sum(
        (row.planned_minutes or Decimal("0") for row in unscheduled), Decimal("0")
    )
    days = Decimal(work_centre.calendar().count(start, end))
    # Off the centre, which sums its machines where it has any. Days
    # stay the bank's, because "how many days was the shed open" is a
    # different question from "how many machine-minutes were in them"
    # and a bank whose looms run different patterns has one honest
    # answer to the first.
    available = work_centre.minutes_available(start, end)
    return {
        "work_centre": work_centre,
        "start": start,
        "end": end,
        "working_days": int(days),
        "load_minutes": load,
        "unscheduled_minutes": floating,
        "available_minutes": available,
        "spare_minutes": available - load,
        "utilisation_percent": (
            load / available * Decimal("100") if available else None
        ),
        "runs": sorted({row.work_order for row in operations}, key=lambda o: o.pk),
    }
