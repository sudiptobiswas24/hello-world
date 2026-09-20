"""
Moving stock between warehouses.

`MovementType.TRANSFER_IN` and `TRANSFER_OUT` existed from the start and
only purchasing wrote them, for subcontracting and for accepting goods
out of quarantine. Inventory itself could not move a pallet from one
site to another.

Nothing here posts to the ledger. The inventory account is chosen per
item, not per warehouse, so a transfer moves stock between shelves
without moving a penny between accounts — and inventing an entry that
debits and credits the same account would be noise in the general
ledger pretending to be information.

What it must not do is move value. The despatching leg is priced by the
replay at the source's running weighted average; the receiving leg has
to add back exactly that, to the cent, or every transfer quietly
revalues the company's stock.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, DocumentSequence, to_date

from .models import Item, MovementType, StockMovement, Warehouse


class TransferStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    IN_TRANSIT = "in_transit", "In transit"
    RECEIVED = "received", "Received"
    CANCELLED = "cancelled", "Cancelled"


class StockTransfer(AuditModel):
    """
    Stock leaving one warehouse for another.

    Two shapes, because warehouses come in two kinds. A move between
    racks on one site lands the moment it is made, so `post()` does both
    legs at once. A move between sites takes days, and during those days
    the stock is neither at the origin — it has gone — nor at the
    destination, where it has not arrived. Naming a transit warehouse
    makes it a two-step: `dispatch()` then `receive()`, and in between
    the stock sits somewhere owned, valued, and unpickable.

    Pretending the second case is the first is how a warehouse ships
    goods it does not have: the destination shows stock that is still on
    a lorry.
    """

    number = models.CharField(max_length=32, blank=True)
    transfer_date = models.DateField()
    from_warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="transfers_out"
    )
    to_warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="transfers_in"
    )
    transit_warehouse = models.ForeignKey(
        Warehouse, null=True, blank=True, on_delete=models.PROTECT,
        related_name="transfers_through",
        help_text="Set for a move that takes time. The stock rests here between "
                  "leaving and arriving, instead of being in two places or none.",
    )
    status = models.CharField(
        max_length=16, choices=TransferStatus.choices, default=TransferStatus.DRAFT
    )
    reference = models.CharField(max_length=64, blank=True)
    memo = models.CharField(max_length=255, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True, editable=False)
    received_at = models.DateTimeField(null=True, blank=True, editable=False)
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-transfer_date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""), name="stock_transfer_number_unique"
            ),

        ]

    def __str__(self):
        return self.number or f"Draft transfer {self.pk}"

    def is_two_step(self):
        return self.transit_warehouse_id is not None

    def is_settled(self):
        return self.status in (TransferStatus.RECEIVED, TransferStatus.CANCELLED)

    def holding_warehouse(self):
        """Where the stock is while the transfer is open."""
        return self.transit_warehouse if self.is_two_step() else self.from_warehouse

    # Same building is allowed — shelf to shelf is a real move — so
    # "two warehouses" became "somewhere else", which is a fact about the
    # lines and not about this row. It is checked in _check_somewhere_else()
    # when the stock actually moves, where the lines exist to be read.

    # -- the outbound half ------------------------------------------------

    @transaction.atomic
    def dispatch(self, occurred_at=None):
        """Take the stock off the source shelf and put it in transit."""
        if not self.is_two_step():
            raise ValidationError(
                "This transfer has no transit warehouse, so there is nothing to "
                "despatch into. Post it instead."
            )
        return self._move_out(TransferStatus.IN_TRANSIT, occurred_at)

    @transaction.atomic
    def post(self, occurred_at=None):
        """Move the stock straight from one shelf to the other."""
        if self.is_two_step():
            raise ValidationError(
                "This transfer goes through a transit warehouse. Despatch it, then "
                "receive it when it arrives."
            )
        return self._move_out(TransferStatus.RECEIVED, occurred_at)

    def _move_out(self, next_status, occurred_at):
        if self.status != TransferStatus.DRAFT:
            raise ValidationError(f"This transfer is already {self.get_status_display().lower()}.")
        lines = list(self.lines.select_related("item", "uom"))
        if not lines:
            raise ValidationError("Cannot move a transfer with no lines.")

        self._check_warehouses()
        self._check_somewhere_else(lines)
        self.transfer_date = to_date(self.transfer_date)
        occurred_at = occurred_at or timezone.now()
        if not self.number:
            self.number = DocumentSequence.next_for(
                "inventory.transfer", self.transfer_date,
                name="Stock Transfers", prefix="TRF-",
            )

        target = self.transit_warehouse if self.is_two_step() else self.to_warehouse
        for line in lines:
            line.move(self, self.from_warehouse, target, line.stock_quantity(), occurred_at, leg="out")

        self.status = next_status
        self.dispatched_at = occurred_at
        if next_status == TransferStatus.RECEIVED:
            self.received_at = occurred_at
        super().save(update_fields=[
            "number", "transfer_date", "status", "dispatched_at", "received_at", "updated_at",
        ])
        return self

    def _check_somewhere_else(self, lines):
        """
        A transfer has to move stock somewhere it is not already.

        Two warehouses is the usual answer; one warehouse and two bins is
        the other. What it may not be is neither, which would write a
        matched pair of movements that cancel and call it a journey.
        """
        if self.from_warehouse_id != self.to_warehouse_id:
            return
        for line in lines:
            if line.from_bin_id is None or line.from_bin_id == line.to_bin_id:
                raise ValidationError(
                    f"{line.item} would end up where it started. A transfer within "
                    f"{self.from_warehouse} has to name a different bin to move to."
                )

    def _check_warehouses(self):
        for warehouse, role in (
            (self.from_warehouse, "from"), (self.to_warehouse, "to"),
        ):
            if warehouse.consignment_vendor_id:
                raise ValidationError(
                    f"{warehouse} holds {warehouse.consignment_vendor}'s stock, which is "
                    f"not the company's to transfer {role}."
                )
        if self.from_warehouse.is_quarantine:
            raise ValidationError(
                f"{self.from_warehouse} holds goods awaiting inspection; accept them "
                "before moving them elsewhere."
            )
        if self.is_two_step() and not self.transit_warehouse.is_transit:
            raise ValidationError(
                f"{self.transit_warehouse} is not marked as a transit warehouse. Stock "
                "resting there would look pickable."
            )

    # -- the inbound half -------------------------------------------------

    @transaction.atomic
    def receive(self, quantities=None, occurred_at=None):
        """
        Land the stock at its destination.

        `quantities` is {line: quantity in the line's unit} for a lorry
        that arrived with less than it left with. What is not received
        stays in transit, which is the truthful answer: it has not
        arrived and it is not lost.
        """
        if self.status != TransferStatus.IN_TRANSIT:
            raise ValidationError("Only a transfer in transit can be received.")
        occurred_at = occurred_at or timezone.now()
        selected = self._receipt_selection(quantities)
        for line, quantity in selected:
            line.move(
                self, self.transit_warehouse, self.to_warehouse, quantity, occurred_at,
                leg="in",
            )
        if all(line.quantity_outstanding() <= 0 for line in self.lines.all()):
            self.status = TransferStatus.RECEIVED
            self.received_at = occurred_at
            super().save(update_fields=["status", "received_at", "updated_at"])
        return selected

    def _receipt_selection(self, quantities):
        lines = list(self.lines.select_related("item", "uom"))
        if quantities is None:
            selected = [
                (line, line.quantity_outstanding()) for line in lines
                if line.quantity_outstanding() > 0
            ]
            if not selected:
                raise ValidationError("Every line on this transfer has already arrived.")
            return selected

        selected = []
        for line, quantity in quantities.items():
            quantity = Decimal(quantity)
            if quantity <= 0:
                continue
            if line.transfer_id != self.pk:
                raise ValidationError("That line belongs to a different transfer.")
            outstanding = line.quantity_outstanding()
            if quantity > outstanding:
                raise ValidationError(
                    f"Only {outstanding} {line.item.uom} of {line.item} is still in "
                    f"transit; cannot receive {quantity}."
                )
            selected.append((line, quantity))
        if not selected:
            raise ValidationError("Nothing to receive.")
        return selected

    # -- the reverse path -------------------------------------------------

    @transaction.atomic
    def cancel(self, occurred_at=None, memo=""):
        """
        Undo the transfer, putting every unit back where it came from.

        Written in the same sitting as the forward path. A transfer that
        can only go forwards leaves stock stranded in transit the first
        time a lorry turns round, and the only way out is a hand-written
        adjustment that nothing ties back to the move it is correcting.
        """
        if self.status == TransferStatus.CANCELLED:
            raise ValidationError("This transfer has already been cancelled.")
        if self.status == TransferStatus.DRAFT:
            raise ValidationError("A draft transfer has moved nothing; delete it instead.")

        occurred_at = occurred_at or timezone.now()
        label = memo or f"Cancellation of transfer {self.number}"
        for line in self.lines.select_related("item", "uom"):
            line.unwind(self, occurred_at, label)
        self.status = TransferStatus.CANCELLED
        self.cancelled_at = occurred_at
        super().save(update_fields=["status", "cancelled_at", "updated_at"])
        return self

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            previous = StockTransfer.objects.filter(pk=self.pk).first()
            if previous is not None and previous.status != TransferStatus.DRAFT:
                raise ValidationError(
                    "Cannot modify a transfer that has already moved stock. Cancel it "
                    "and raise another."
                )
        super().save(*args, **kwargs)


class StockTransferLine(AuditModel):
    transfer = models.ForeignKey(StockTransfer, related_name="lines", on_delete=models.CASCADE)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="transfer_lines")
    uom = models.ForeignKey(
        "core.UnitOfMeasure", on_delete=models.PROTECT, related_name="+",
        help_text="The unit this line is written in.",
    )
    lot = models.ForeignKey(
        "Lot", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Which batch is moving. Required when the item is tracked — a "
                  "transfer that cannot say which batch left has broken the trail.",
    )
    from_bin = models.ForeignKey(
        "StorageBin", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Which shelf it is coming off.",
    )
    to_bin = models.ForeignKey(
        "StorageBin", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Which shelf it is going on. On a two-step transfer this is where "
                  "it lands at the far end, not where it rests in transit.",
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                check=Q(quantity__gt=0), name="transfer_line_quantity_positive"
            ),
            models.UniqueConstraint(
                fields=["transfer", "item"], condition=Q(lot__isnull=True),
                name="one_transfer_line_per_untracked_item",
            ),
            models.UniqueConstraint(
                fields=["transfer", "item", "lot"], condition=Q(lot__isnull=False),
                name="one_transfer_line_per_item_lot",
            ),
        ]

    def __str__(self):
        return f"{self.quantity} {self.uom} {self.item.sku}"

    def stock_quantity(self):
        return self.item.to_stock_quantity(self.quantity, self.uom)

    def quantity_dispatched(self):
        return self._leg_total("out")

    def quantity_received(self):
        return self._leg_total("in")

    def quantity_outstanding(self):
        """Still in transit: sent and not yet arrived."""
        return self.quantity_dispatched() - self.quantity_received()

    def _leg_total(self, leg):
        """
        Signed sum of the hops on one leg.

        A reversing step carries the leg it undoes and a negative
        quantity, so cancelling a despatch takes the despatch back out of
        the total rather than adding to the opposite one — which would
        read as twenty units in transit after ten went nowhere.
        """
        total = Decimal("0")
        for step in self.steps.filter(leg=leg):
            total += step.quantity
        return total

    @transaction.atomic
    def move(self, transfer, source, destination, quantity, occurred_at, leg):
        """
        One hop: out of `source` and into `destination`, same value both
        ends.

        The despatching movement is priced by the replay at the source's
        running average, so the receiving one has to add back exactly
        that. A quantised unit cost multiplied back does not: the residue
        rides along as a value_adjustment, which is already what value
        with no quantity attached means here.
        """
        item = self.item
        if not item.track_inventory:
            raise ValidationError(f"{item} is not stocked, so there is nothing to move.")

        from_bin, to_bin = self._bins_for(transfer, source, destination)
        on_hand = (
            from_bin.on_hand(item) if from_bin is not None
            else (
                self.lot.on_hand_at(source) if self.lot_id is not None
                else item.on_hand_at(source)
            )
        )
        if quantity > on_hand and not source.allow_negative_stock:
            raise ValidationError(
                f"Only {on_hand} {item.uom} of "
                f"{self.lot.code if self.lot_id else item} at {source}; "
                f"cannot move {quantity}."
            )

        # What the source shelf actually gives up. Under FIFO that is
        # the oldest layers rather than an average of all of them, and
        # the receiving end has to add back the same figure or the move
        # revalues the company's stock.
        leaving = item.cost_of_removing(source, quantity)
        unit_cost = (leaving / quantity).quantize(Decimal("0.0001"))
        residue = (leaving - quantity * unit_cost).quantize(Decimal("0.0001")) or None

        label = (
            f"Transfer {transfer.number}: {source.code} to {destination.code}"
        )
        out = StockMovement.objects.create(
            item=item, warehouse=source, movement_type=MovementType.TRANSFER_OUT,
            uom=item.uom, lot=self.lot, bin=from_bin,
            quantity=-quantity, unit_cost=unit_cost,
            reference=transfer.number, occurred_at=occurred_at, notes=label,
        )
        into = StockMovement.objects.create(
            item=item, warehouse=destination, movement_type=MovementType.TRANSFER_IN,
            uom=item.uom, lot=self.lot, bin=to_bin,
            quantity=quantity, unit_cost=unit_cost,
            value_adjustment=residue,
            reference=transfer.number, occurred_at=occurred_at, notes=label,
        )
        return StockTransferStep.objects.create(
            line=self, leg=leg, quantity=quantity,
            source=source, destination=destination,
            out_movement=out, in_movement=into, occurred_at=occurred_at,
        )

    def _bins_for(self, transfer, source, destination):
        """
        Which shelf each end of this hop uses.

        A transit warehouse has no bin of its own on this line: the
        stock is on a lorry, and `to_bin` describes where it lands at the
        far end, not where it rests on the way.
        """
        from_bin = self.from_bin if source.pk == transfer.from_warehouse_id else None
        to_bin = self.to_bin if destination.pk == transfer.to_warehouse_id else None
        return from_bin, to_bin

    @transaction.atomic
    def unwind(self, transfer, occurred_at, label):
        """Reverse every step this line made, latest first."""
        undone = []
        # Original hops only, and only ones not already sent back. Latest
        # first, so a partial receipt is returned to transit before the
        # transit leg is returned to the origin.
        steps = list(
            self.steps.filter(reversed_step__isnull=True, reverses__isnull=True)
            .order_by("-id")
        )
        for step in steps:
            undone.append(step.reverse(self, occurred_at, label))
        return undone

    def save(self, *args, **kwargs):
        if self.transfer_id and not StockTransfer.objects.filter(
            pk=self.transfer_id, status=TransferStatus.DRAFT
        ).exists():
            raise ValidationError(
                "Cannot modify a line on a transfer that has already moved stock."
            )
        if self.uom_id is None and self.item_id:
            self.uom = self.item.uom
        if self.item_id and self.uom_id:
            self.item.check_uom(self.uom)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.transfer.status != TransferStatus.DRAFT:
            raise ValidationError(
                "Cannot delete a line on a transfer that has already moved stock."
            )
        super().delete(*args, **kwargs)


class StockTransferStep(AuditModel):
    """
    One hop actually made, and the two movements that made it.

    Recorded rather than recomputed. How much is still in transit is the
    difference between the hops out and the hops in, and a partial
    receipt followed by a cancellation has to put back what each hop
    moved — not what the line says it intended to move.
    """

    line = models.ForeignKey(StockTransferLine, related_name="steps", on_delete=models.CASCADE)
    leg = models.CharField(
        max_length=8,
        choices=(("out", "Out"), ("in", "In")),
        help_text="'out' left the origin, 'in' arrived at the destination.",
    )
    quantity = models.DecimalField(
        max_digits=18, decimal_places=4, help_text="In the item's stocking unit."
    )
    source = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+")
    destination = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+")
    out_movement = models.ForeignKey(
        StockMovement, on_delete=models.PROTECT, related_name="+", editable=False
    )
    in_movement = models.ForeignKey(
        StockMovement, on_delete=models.PROTECT, related_name="+", editable=False
    )
    reversed_step = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reverses",
        editable=False,
        help_text="On a reversing step, the step it undid.",
    )
    occurred_at = models.DateTimeField()

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.leg} {self.quantity} {self.source.code}->{self.destination.code}"

    @transaction.atomic
    def reverse(self, line, occurred_at, label):
        """
        Send this hop back the way it came, at the value it carried.

        Not at today's average: the replay prices the return journey's
        outbound leg at whatever the destination now holds, which after
        other receipts is a different number. The residue makes the two
        hops cancel to zero instead of revaluing stock every time a lorry
        turns round.
        """
        item = line.item
        moved_value = (self.quantity * (self.out_movement.unit_cost or Decimal("0"))) + (
            self.in_movement.value_adjustment or Decimal("0")
        )
        on_hand = (
            line.lot.on_hand_at(self.destination) if line.lot_id is not None
            else item.on_hand_at(self.destination)
        )
        if self.quantity > on_hand and not self.destination.allow_negative_stock:
            raise ValidationError(
                f"Only {on_hand} {item.uom} of {item} remains at {self.destination}; "
                f"cannot send back {self.quantity}."
            )
        back_out_value = item.cost_of_removing(self.destination, self.quantity)
        unit_cost = (back_out_value / self.quantity).quantize(Decimal("0.0001"))
        # Going back out, the replay removes quantity x average. Going back
        # in, it adds quantity x unit_cost. The pair has to net to exactly
        # what the original hop carried.
        # Leaving the destination, the replay takes off quantity x its
        # current average; it should take off what arrived. Arriving back
        # at the origin, it adds quantity x unit_cost; it should add the
        # same figure. Each leg carries the difference.
        out_residue = (back_out_value - moved_value).quantize(Decimal("0.0001")) or None
        residue = (moved_value - self.quantity * unit_cost).quantize(Decimal("0.0001")) or None
        # Back the way it came, shelf included: the out movement's bin is
        # where this hop put the stock, and the in movement's is where it
        # took it from.
        out = StockMovement.objects.create(
            item=item, warehouse=self.destination, movement_type=MovementType.TRANSFER_OUT,
            uom=item.uom, lot=line.lot, bin=self.in_movement.bin,
            quantity=-self.quantity, unit_cost=unit_cost,
            value_adjustment=out_residue,
            reference=line.transfer.number, occurred_at=occurred_at, notes=label,
        )
        into = StockMovement.objects.create(
            item=item, warehouse=self.source, movement_type=MovementType.TRANSFER_IN,
            uom=item.uom, lot=line.lot, bin=self.out_movement.bin,
            quantity=self.quantity, unit_cost=unit_cost,
            value_adjustment=residue,
            reference=line.transfer.number, occurred_at=occurred_at, notes=label,
        )
        return StockTransferStep.objects.create(
            line=line, leg=self.leg,
            quantity=-self.quantity,
            source=self.destination, destination=self.source,
            out_movement=out, in_movement=into,
            reversed_step=self, occurred_at=occurred_at,
        )
