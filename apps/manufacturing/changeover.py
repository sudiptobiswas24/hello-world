"""
What it costs to change a machine from one run to the next, and which
order of runs costs least.

A flat setup time on a routing is a fiction on the machines where
changeover is the business. A printing press washing down from white
to yellow loses twenty minutes; from black to white it loses two hours,
because every trace of black shows on white. The same two runs cost a
different amount depending on which goes first, and a plan that books
a flat setup against each cannot see the difference — so it cannot see
that the order the runs arrived in is the dearest one.

**Families, per machine.** What matters on a press is the print; on the
laminator, the coating; on the cutting line, the size. So an item
belongs to a family on a particular work centre, not in general, and
the families live here rather than on the item, which is inventory's
and knows nothing about machines.

**Same family, no changeover.** That is what a family is: things that
can follow each other on this machine without stopping it. If two runs
of one family still need a wash-down, they are not one family.

**The rules fall back, most particular first.** From this family to that
one; from this family to anything; from anything to that one; from
anything to anything; and, with no rule at all or an item nobody has
put in a family, the operation's own flat setup — which is exactly
what the plant had before any of this, so nothing changes until
somebody fills the matrix in.

**The sequencer proposes; it does not move runs.** It reads the queue on
a machine as planned, reads what the machine last ran, and offers the
order that changes over least — the way planning emits reschedule
messages rather than re-dating orders nobody asked it to touch. It
never proposes an order dearer than the one planned, and it names the
runs its proposal makes later, because a sequence that saves two hours
of wash-down and misses a customer's lorry is not a saving.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from apps.core.models import AuditModel

ZERO = Decimal("0")


class SetupFamily(AuditModel):
    """Which family an item belongs to on one work centre."""

    item = models.ForeignKey(
        "inventory.Item", on_delete=models.CASCADE, related_name="+",
    )
    work_centre = models.ForeignKey(
        "manufacturing.WorkCentre", on_delete=models.CASCADE,
        related_name="setup_families",
    )
    family = models.CharField(
        max_length=32,
        help_text="What this machine has to be changed for — 'WHITE-2C' on a "
                  "press, '1000D-NATURAL' on an extruder. Items in one family "
                  "follow each other here with no changeover.",
    )

    class Meta:
        ordering = ["work_centre", "family", "item"]
        constraints = [
            models.UniqueConstraint(
                fields=["item", "work_centre"],
                name="one_setup_family_per_item_and_centre",
            ),
            models.CheckConstraint(
                check=~Q(family=""), name="setup_family_is_named",
            ),
        ]

    def __str__(self):
        return f"{self.item.sku} is {self.family} on {self.work_centre.code}"


class ChangeoverRule(AuditModel):
    """
    Minutes to change a work centre from one family to another.

    Blank at either end means any family. A rule from a family to
    itself is refused rather than stored: same family is no changeover
    by definition, and a rule saying otherwise would be a second answer
    to a question already answered.
    """

    work_centre = models.ForeignKey(
        "manufacturing.WorkCentre", on_delete=models.CASCADE,
        related_name="changeover_rules",
    )
    from_family = models.CharField(max_length=32, blank=True)
    to_family = models.CharField(max_length=32, blank=True)
    minutes = models.DecimalField(max_digits=10, decimal_places=2)
    purge_kg = models.DecimalField(
        max_digits=10, decimal_places=3, default=Decimal("0"),
        help_text="Polymer run through to clear the old colour or recipe out of an "
                  "extruder: material spent on the change, not only minutes.")
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["work_centre", "from_family", "to_family"]
        constraints = [
            models.UniqueConstraint(
                fields=["work_centre", "from_family", "to_family"],
                name="one_changeover_rule_per_pair",
            ),
            models.CheckConstraint(
                check=Q(minutes__gte=0) & Q(purge_kg__gte=0),
                name="changeover_not_negative",
            ),
            models.CheckConstraint(
                check=Q(from_family="") | Q(to_family="")
                | ~Q(from_family=F("to_family")),
                name="changeover_is_between_two_families",
            ),
        ]

    def __str__(self):
        return (
            f"{self.work_centre.code}: {self.from_family or 'any'} → "
            f"{self.to_family or 'any'}, {self.minutes} min"
        )


def family_of(item, work_centre):
    """
    The item's family on this machine, or '' where nobody has said —
    including when there is no item, which is how an unknown last run
    reads.
    """
    row = SetupFamily.objects.filter(item=item, work_centre=work_centre).first()
    return row.family if row is not None else ""


def changeover(work_centre, previous_item, next_item, flat_setup):
    """
    (minutes, purge kg) to go from running `previous_item` to `next_item`.

    The flat setup where nothing better is known: a machine whose last
    run is unknown, an item in no family, a pair no rule covers — and
    no purge, since nothing says there is one.
    """
    flat = (Decimal(flat_setup or 0), ZERO)
    before = family_of(previous_item, work_centre)
    after = family_of(next_item, work_centre)
    # An unknown last run and an item nobody has put in a family both
    # land here, and both mean "not known" — not "any family". A
    # catch-all rule is a statement about families; an item outside
    # every family is exactly what it cannot speak for.
    if not before or not after:
        return flat
    if before == after:
        return ZERO, ZERO
    rules = {
        (row.from_family, row.to_family): (row.minutes, row.purge_kg)
        for row in ChangeoverRule.objects.filter(
            work_centre=work_centre,
            from_family__in=(before, ""), to_family__in=(after, ""),
        )
    }
    for key in ((before, after), (before, ""), ("", after), ("", "")):
        if key in rules:
            return rules[key]
    return flat


def changeover_minutes(work_centre, previous_item, next_item, flat_setup):
    """Minutes to go from running `previous_item` to `next_item` here."""
    return changeover(work_centre, previous_item, next_item, flat_setup)[0]


# -- the queue on a machine --------------------------------------------


def on_the_machine(work_centre, machine=None):
    """
    What the machine last ran, from its last posted booking.

    Derived rather than stored: a voided booking hands the answer back
    to the one before it, which a "current family" field would have had
    to remember to do.
    """
    from .orders import TimeBooking

    bookings = TimeBooking.objects.filter(
        operation__work_centre=work_centre, posted=True,
        voided_at__isnull=True,
    )
    if machine is not None:
        bookings = bookings.filter(machine=machine)
    last = bookings.select_related("work_order__item").order_by(
        "-booking_date", F("started_at").desc(nulls_last=True), "-id"
    ).first()
    return last.work_order.item if last is not None else None


def queue_on(work_centre, machine=None):
    """
    Released runs waiting for this machine, in the order they are
    planned: nothing booked against them yet, earliest planned start
    first, then earliest due.
    """
    from .orders import WorkOrderOperation, WorkOrderStatus

    operations = WorkOrderOperation.objects.filter(
        work_centre=work_centre, is_outside=False,
        work_order__status=WorkOrderStatus.RELEASED,
    ).exclude(
        bookings__posted=True, bookings__voided_at__isnull=True,
    ).select_related("work_order", "work_order__item")
    if machine is not None:
        operations = operations.filter(machine=machine)
    return sorted(
        operations.distinct(),
        key=lambda op: (
            op.work_order.scheduled_start is None,
            op.work_order.scheduled_start or op.work_order.created_at.date(),
            op.work_order.scheduled_end is None,
            op.work_order.scheduled_end or op.work_order.created_at.date(),
            op.pk,
        ),
    )


def last_in_line(work_centre):
    """
    What a run planned now would follow on this machine: the last run
    released to it and not yet started, or, with nothing waiting, what
    it last ran.

    Planning's view of a new run is that it joins the end of the queue.
    Its changeover is from whatever is there, not a flat average — a
    white run planned behind a queue of black ones pays the wash-down.

    One tail for the whole centre: on a bank of twelve looms the run
    lands on one of them, and which one is the dispatcher's choice,
    not planning's. The approximation is the queue as a whole.
    """
    queue = queue_on(work_centre)
    if queue:
        return queue[-1].work_order.item
    return on_the_machine(work_centre)


def _walk(work_centre, current, operations):
    """Each run with the changeover it costs coming after the one before."""
    rows = []
    previous = current
    total, purged = ZERO, ZERO
    for operation in operations:
        item = operation.work_order.item
        minutes, purge = changeover(work_centre, previous, item, operation.setup_minutes)
        rows.append({
            "operation": operation,
            "item": item,
            "family": family_of(item, work_centre),
            "changeover_minutes": minutes,
            "purge_kg": purge,
        })
        total += minutes
        purged += purge
        previous = item
    return rows, total, purged


def _due(operation):
    return operation.work_order.scheduled_end


def _nearest_first(work_centre, current, operations):
    """
    Each next run the one cheapest to change to, earliest due breaking
    a tie.

    A heuristic, and said so: the exact answer is a search over every
    order, which is factorial in the queue. It is always compared with
    the planned order before it is offered, so it can fail to find a
    saving but can never be offered as one when it is not.
    """
    left = list(operations)
    order = []
    previous = current
    while left:
        def cost(op):
            # Minutes first; between equal minutes, the one that purges less.
            minutes, purge = changeover(
                work_centre, previous, op.work_order.item, op.setup_minutes
            )
            due = _due(op)
            return (minutes, purge, due is None, due or 0, op.pk)

        chosen = min(left, key=cost)
        left.remove(chosen)
        order.append(chosen)
        previous = chosen.work_order.item
    return order


def sequence(work_centre, machine=None):
    """
    The queue on a machine as planned, and the order that changes over
    least.

    Returns the machine's last item, both sequences with the changeover
    before each run, the minutes the proposal saves, and the runs it
    moves later — the ones somebody should look at before taking it.
    """
    if machine is not None:
        machine.check_in(work_centre)
    current = on_the_machine(work_centre, machine)
    planned = queue_on(work_centre, machine)
    planned_rows, planned_total, planned_purge = _walk(work_centre, current, planned)
    candidate = _nearest_first(work_centre, current, planned)
    proposed_rows, proposed_total, proposed_purge = _walk(work_centre, current, candidate)
    # Better is fewer minutes, or as few and less purged.
    better = (proposed_total, proposed_purge) < (planned_total, planned_purge)
    if not better:
        proposed_rows, proposed_total, proposed_purge = (
            planned_rows, planned_total, planned_purge)
        candidate = planned
    position = {op.pk: index for index, op in enumerate(planned)}
    moved_later = [
        op for index, op in enumerate(candidate)
        if index > position[op.pk]
    ]
    return {
        "work_centre": work_centre,
        "machine": machine,
        "on_the_machine": current,
        "planned": planned_rows,
        "planned_minutes": planned_total,
        "proposed": proposed_rows,
        "proposed_minutes": proposed_total,
        "saved_minutes": planned_total - proposed_total,
        "planned_purge_kg": planned_purge,
        "proposed_purge_kg": proposed_purge,
        "saved_purge_kg": planned_purge - proposed_purge,
        "moved_later": moved_later,
        "note": None if better else (
            "No order found that changes over less than the one planned."
        ),
    }
