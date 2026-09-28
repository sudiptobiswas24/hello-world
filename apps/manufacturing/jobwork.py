"""
Goods out to a job worker, on a challan: what was sent, what came back,
what is still out, and when it stops being job work.

The outside step on a routing already books the vendor's charge into
the run. What it did not do was let the goods leave the gate. Under
GST, inputs sent for job work go out on a delivery challan, not an
invoice; they have to come back within a year (three for capital
goods) or the sending is treated as a supply on the day it went; and
what went and what came back is reported on ITC-04. None of that could
be answered while the fabric at the laminator was a quantity nobody had
written down.

**A challan moves no stock.** The goods on it were issued to the run
and are in work in progress; the challan is the document that lets
them out of the building, not a transfer of stock. Their value on it
is the value the law asks for, a declared figure, and posting it writes
nothing to the ledger.

**What came back is allocated, not stored.** The vendor's returns are
the outside movements the goods receipt already posts. They are laid
against a step's challans oldest first, as the goods physically go, and
work sent back to the vendor for rework reopens the latest line it was
laid against. Recomputed each time from immutable records, so it gives
the same answer every time it is asked.

**Losses at the job worker are recorded, not assumed.** The trim a
laminator keeps is a quantity somebody states, with a date, and it is
what ITC-04 reports as losses and wastes.

**Once a step has challans, nothing comes back that was not sent.**
Checked when a vendor's work is booked. A step with no challan at all
is a plant that has not started using them, and is left as it was.
"""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, DocumentSequence, to_date

ZERO = Decimal("0")
INPUTS_DAYS = 365
CAPITAL_GOODS_DAYS = 3 * 365


# Asked before a challan is voided, by modules that hold something against
# it (an e-way bill). Each raises to refuse.
CHALLAN_VOID_GUARDS = []


def register_challan_void_guard(guard):
    if guard not in CHALLAN_VOID_GUARDS:
        CHALLAN_VOID_GUARDS.append(guard)


class JobWorkChallan(AuditModel):
    number = models.CharField(max_length=32, blank=True)
    job_worker = models.ForeignKey("core.Party", on_delete=models.PROTECT,
                                   related_name="job_work_challans")
    challan_date = models.DateField()
    vehicle = models.CharField(max_length=32, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    job_worker_gstin = models.CharField(
        max_length=15, blank=True, editable=False,
        help_text="As it stood when the challan was issued.",
    )
    job_worker_state = models.CharField(max_length=2, blank=True, editable=False)
    posted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["challan_date", "id"]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""),
                                    name="job_work_challan_number_unique"),
        ]

    def __str__(self):
        return self.number or f"Draft challan {self.pk}"

    def save(self, *args, **kwargs):
        # Issuing and voiding write through the base save; anything else
        # touching an issued challan is an edit, and a challan is paper
        # that has already left the gate.
        if self.pk and JobWorkChallan.objects.filter(pk=self.pk, posted=True).exists():
            raise ValidationError(f"{self} is issued. Void it and issue another.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # The mirror of save(): an issued challan left the gate and is on
        # ITC-04. Voiding withdraws it and keeps the record.
        if JobWorkChallan.objects.filter(pk=self.pk, posted=True).exists():
            raise ValidationError(f"{self} is issued. Void it; it cannot be deleted.")
        return super().delete(*args, **kwargs)

    @transaction.atomic
    def post(self):
        """Issue the challan: numbered, the job worker's registration frozen."""
        from .orders import WorkOrderStatus

        if self.posted:
            raise ValidationError(f"{self} is already issued.")
        lines = list(self.lines.select_related("operation__work_order__item"))
        if not lines:
            raise ValidationError("A challan with nothing on it sends nothing.")
        self.challan_date = to_date(self.challan_date)
        for line in lines:
            order = line.operation.work_order
            if order.status != WorkOrderStatus.RELEASED:
                raise ValidationError(f"{order} is not running; nothing of it can go out.")
            others = sum(
                (other.quantity for other in JobWorkLine.objects.filter(
                    operation=line.operation, challan__posted=True,
                    challan__voided_at__isnull=True)),
                ZERO,
            )
            ceiling = order.maximum_output()
            if others + line.quantity > ceiling:
                raise ValidationError(
                    f"{order} holds at most {ceiling}, and {others + line.quantity} of it "
                    "would be out on challans."
                )
        from apps.accounting.models import PartyTaxProfile

        # Read afresh rather than through the party's cached relation: a
        # challan freezes the registration as it stands now, and a party
        # object that has been asked before remembers what it was.
        profile = PartyTaxProfile.objects.filter(party_id=self.job_worker_id).first()
        self.job_worker_gstin = profile.gstin if profile else ""
        self.job_worker_state = (profile.gst_state or "") if profile else ""
        self.number = DocumentSequence.next_for(
            "manufacturing.job_work_challan", self.challan_date,
            name="Job Work Challans", prefix="JWC-",
        )
        self.posted = True
        self.posted_at = timezone.now()
        super().save(update_fields=[
            "number", "challan_date", "job_worker_gstin", "job_worker_state",
            "posted", "posted_at", "updated_at",
        ])

    @transaction.atomic
    def void(self):
        """
        Withdraw a challan issued in error. Only while what is still out
        on the step covers what has come back without it: a challan the
        returns were laid against cannot be taken away from under them.
        """
        if not self.posted or self.voided_at is not None:
            raise ValidationError(f"{self} is not an issued challan.")
        for guard in CHALLAN_VOID_GUARDS:
            guard(self)
        for line in self.lines.all():
            if line.losses.filter(voided_at__isnull=True).exists():
                raise ValidationError(f"Losses are recorded against {line}.")
            remaining = sum(
                (other.quantity for other in JobWorkLine.objects.filter(
                    operation=line.operation, challan__posted=True,
                    challan__voided_at__isnull=True).exclude(challan=self)),
                ZERO,
            )
            if line.operation.quantity_back() > remaining:
                raise ValidationError(
                    f"Work has come back against {self}; it cannot be withdrawn."
                )
        self.voided_at = timezone.now()
        super().save(update_fields=["voided_at", "updated_at"])


class JobWorkLine(AuditModel):
    challan = models.ForeignKey(JobWorkChallan, on_delete=models.CASCADE, related_name="lines")
    operation = models.ForeignKey(
        "manufacturing.WorkOrderOperation", on_delete=models.PROTECT,
        related_name="challan_lines",
        help_text="The outside step the goods go out for.",
    )
    description = models.CharField(max_length=255)
    hsn_code = models.CharField(max_length=8)
    quantity = models.DecimalField(
        max_digits=18, decimal_places=4,
        help_text="In the run's own unit, the unit the vendor's returns are counted in.",
    )
    value = models.DecimalField(
        max_digits=18, decimal_places=2,
        help_text="The taxable value the challan declares. Not posted anywhere: "
                  "the goods are in work in progress already.",
    )
    tax_rate = models.DecimalField(
        max_digits=5, decimal_places=2,
        help_text="The GST rate the goods would bear if supplied — reported on "
                  "ITC-04, though none is charged.",
    )
    is_capital_goods = models.BooleanField(
        default=False,
        help_text="Capital goods have three years to come back; inputs have one.",
    )

    class Meta:
        ordering = ["challan", "id"]
        constraints = [
            models.CheckConstraint(check=Q(quantity__gt=0) & Q(value__gte=0)
                                   & Q(tax_rate__gte=0), name="job_work_line_sensible"),
        ]

    def __str__(self):
        return f"{self.challan}: {self.description}"

    def save(self, *args, **kwargs):
        from apps.accounting.gst import validate_hsn

        if JobWorkChallan.objects.filter(pk=self.challan_id, posted=True).exists():
            raise ValidationError(f"{self.challan} is issued; its lines are fixed.")
        if not self.operation.is_outside:
            raise ValidationError(
                f"{self.operation} is done on our own machines; nothing goes out for it."
            )
        self.hsn_code = validate_hsn(self.hsn_code)
        if not self.hsn_code:
            raise ValidationError("A challan line needs its HSN code.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if JobWorkChallan.objects.filter(pk=self.challan_id, posted=True).exists():
            raise ValidationError(f"{self.challan} is issued; its lines are fixed.")
        return super().delete(*args, **kwargs)

    def due_back_by(self):
        days = CAPITAL_GOODS_DAYS if self.is_capital_goods else INPUTS_DAYS
        return self.challan.challan_date + datetime.timedelta(days=days)


class JobWorkLoss(AuditModel):
    """Material the job worker kept as trim or waste, as stated on a date."""

    line = models.ForeignKey(JobWorkLine, on_delete=models.PROTECT, related_name="losses")
    loss_date = models.DateField()
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    note = models.CharField(max_length=255, blank=True)
    voided_at = models.DateTimeField(
        null=True, blank=True, editable=False,
        help_text="Withdrawn as recorded in error. Kept, and counted nowhere.",
    )

    class Meta:
        ordering = ["loss_date", "id"]
        constraints = [
            models.CheckConstraint(check=Q(quantity__gt=0), name="job_work_loss_positive"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding and not getattr(self, "_voiding", False):
            raise ValidationError("A recorded loss is a fact; void it and record the right one.")
        if not self._state.adding:
            super().save(*args, **kwargs)
            return
        line = self.line
        if not line.challan.posted or line.challan.voided_at is not None:
            raise ValidationError(f"{line.challan} is not an issued challan.")
        if to_date(self.loss_date) < line.challan.challan_date:
            raise ValidationError(
                f"{line.challan} went out on {line.challan.challan_date}; nothing on "
                f"it was lost at the job worker on {self.loss_date}."
            )
        state = allocation(line.operation)[line.pk]
        if self.quantity > state["outstanding"]:
            raise ValidationError(
                f"Only {state['outstanding']} of {line} is still out; {self.quantity} "
                "cannot have been lost there."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("A recorded loss is a fact; void it rather than delete it.")

    @transaction.atomic
    def void(self):
        """Withdraw a loss recorded in error; the goods count as still out again."""
        if self.voided_at is not None:
            raise ValidationError("This loss is already withdrawn.")
        self.voided_at = timezone.now()
        self._voiding = True
        try:
            self.save(update_fields=["voided_at", "updated_at"])
        finally:
            # Left set, the next save of this object would pass the guard too.
            self._voiding = False


# -- what came back against what -----------------------------------------


def live_lines(operation, as_of=None):
    lines = JobWorkLine.objects.filter(
        operation=operation, challan__posted=True, challan__voided_at__isnull=True,
    )
    if as_of is not None:
        lines = lines.filter(challan__challan_date__lte=as_of)
    return list(lines.select_related("challan").order_by("challan__challan_date", "challan_id", "id"))


def live_losses(line, as_of=None):
    losses = line.losses.filter(voided_at__isnull=True)
    if as_of is not None:
        losses = losses.filter(loss_date__lte=as_of)
    return losses


def allocation(operation, as_of=None):
    """
    {line id: {sent, back, lost, outstanding, events}} for a step.

    The vendor's movements, in the order they happened, laid against the
    step's challan lines: work back fills the oldest line with something
    still out; work sent back for rework reopens the latest line that
    was filled. `events` is [(movement, quantity)] per line, negative
    for rework sent back. As of a day, only what had happened by then.
    """
    lines = live_lines(operation, as_of)
    state = {
        line.pk: {
            "line": line, "sent": line.quantity, "back": ZERO,
            "lost": sum((loss.quantity for loss in live_losses(line, as_of)), ZERO),
            "events": [],
        }
        for line in lines
    }
    filled = []  # (line id, quantity) in the order it was laid
    movements = operation.outside_receipts()
    if as_of is not None:
        movements = movements.filter(movement_date__lte=as_of)
    movements = movements.order_by("movement_date", "id")
    for movement in movements:
        quantity = movement.quantity
        if movement.is_return:
            while quantity > 0 and filled:
                line_id, laid = filled.pop()
                take = min(laid, quantity)
                state[line_id]["back"] -= take
                state[line_id]["events"].append((movement, -take))
                quantity -= take
                if laid > take:
                    filled.append((line_id, laid - take))
            continue
        for line in lines:
            row = state[line.pk]
            room = row["sent"] - row["back"] - row["lost"]
            if room <= 0:
                continue
            take = min(room, quantity)
            row["back"] += take
            row["events"].append((movement, take))
            filled.append((line.pk, take))
            quantity -= take
            if quantity <= 0:
                break
    for row in state.values():
        row["outstanding"] = row["sent"] - row["back"] - row["lost"]
    return state


def check_back_was_sent(operation, quantity):
    """
    Refuse work back that was never sent, once a step uses challans.

    Called by the goods receipt before it books the vendor's work.
    """
    lines = live_lines(operation)
    if not lines:
        return
    sent = sum((line.quantity for line in lines), ZERO)
    lost = sum((loss.quantity for line in lines for loss in live_losses(line)), ZERO)
    back = operation.quantity_back()
    if back + Decimal(quantity) > sent - lost:
        raise ValidationError(
            f"{operation} has {sent - lost - back} still out on challans, so "
            f"{quantity} cannot have come back. Issue a challan for what was sent."
        )


def still_out(as_of=None, within_days=30):
    """
    Every challan line with goods still out, with the day they must be
    back by, soonest first — and whether that day has passed, when the
    sending is treated as a supply on the day the goods left.
    """
    as_of = to_date(as_of) or timezone.localdate()
    operations = {
        line.operation_id: line.operation
        for line in JobWorkLine.objects.filter(
            challan__posted=True, challan__voided_at__isnull=True,
        ).select_related("operation")
    }
    rows = []
    for operation in operations.values():
        for row in allocation(operation, as_of).values():
            if row["outstanding"] <= 0:
                continue
            line = row["line"]
            due = line.due_back_by()
            rows.append({
                "line": line, "challan": line.challan, "outstanding": row["outstanding"],
                "due_back_by": due, "overdue": due < as_of,
                "due_soon": as_of <= due <= as_of + datetime.timedelta(days=within_days),
            })
    return sorted(rows, key=lambda row: (row["due_back_by"], row["challan"].number))
