"""
Stock adjustments and physical counts.

Everything that moved stock until now moved it as a side effect of
trading: a receipt, a delivery, a subcontract issue. Nothing could
record the difference between what the system says is on the shelf and
what is actually on it, which is the one stock document every warehouse
actually uses.

Two documents, one posting path. A count is a sheet of what was found;
posting it builds an adjustment from the variances and posts that. So
there is one place that writes movements, one place that writes the
ledger entry, and one reversal that undoes both — rather than two of
each, the second of which drifts from the first.

The ledger side is written here rather than through
`post_inventory_entry`, whose counterpart is GRNI going in and cost of
sales going out. An adjustment's counterpart is the reason it was made,
and no argument to that helper spells that.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounting.models import JournalEntry, JournalLine, round_money
from apps.core.models import AuditModel, DocumentSequence, to_date

from .models import Item, MovementType, StockMovement, Warehouse
from .valuation import inventory_account_for


class AdjustmentDirection(models.TextChoices):
    INCREASE = "increase", "Increase only"
    DECREASE = "decrease", "Decrease only"
    BOTH = "both", "Either"


class AdjustmentReason(AuditModel):
    """
    Why stock was written up or down, and where the other side lands.

    An adjustment with no reason is a number nobody can explain later,
    and "stock is 4 units lower and cost of sales absorbed it" tells an
    auditor nothing about whether the company has a theft problem or a
    counting problem.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    account = models.ForeignKey(
        "accounting.Account", on_delete=models.PROTECT, related_name="+",
        help_text="Where the other side of the adjustment posts — a shrinkage "
                  "expense, a write-off, a revaluation gain.",
    )
    direction = models.CharField(
        max_length=16, choices=AdjustmentDirection.choices,
        default=AdjustmentDirection.BOTH,
        help_text="Which way this reason may move stock. Shrinkage that can "
                  "create stock is not shrinkage.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def permits(self, quantity):
        if quantity > 0:
            return self.direction != AdjustmentDirection.DECREASE
        if quantity < 0:
            return self.direction != AdjustmentDirection.INCREASE
        return True


class StockAdjustment(AuditModel):
    """
    A deliberate change to what the books say is on the shelf.

    Posted is immutable, as everywhere else here: a wrong adjustment is
    voided, which reverses both the ledger entry and the movements, and
    a fresh one is raised. Editing would leave the journal entry
    describing quantities that no longer exist.
    """

    number = models.CharField(max_length=32, blank=True)
    adjustment_date = models.DateField()
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="adjustments")
    reason = models.ForeignKey(AdjustmentReason, on_delete=models.PROTECT, related_name="adjustments")
    memo = models.CharField(max_length=255, blank=True)
    posted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    journal_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        editable=False,
    )
    voided_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        editable=False,
        help_text="The reversal raised when this was voided, so a reader can see "
                  "what undid it rather than inferring it.",
    )
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    count = models.ForeignKey(
        "StockCount", null=True, blank=True, on_delete=models.PROTECT,
        related_name="adjustments", editable=False,
        help_text="Set when this adjustment was raised by posting a count sheet.",
    )

    class Meta:
        ordering = ["-adjustment_date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""), name="stock_adjustment_number_unique"
            ),
        ]

    def __str__(self):
        return self.number or f"Draft adjustment {self.pk}"

    def is_voided(self):
        return self.voided_at is not None

    def total_value(self):
        """Signed: positive when the adjustment adds value to the shelf."""
        return sum(
            (line.value() for line in self.lines.all()), Decimal("0")
        ).quantize(Decimal("0.01"))

    @transaction.atomic
    def post(self, memo=""):
        if self.posted:
            raise ValidationError("This adjustment is already posted.")
        lines = list(self.lines.select_related("item", "uom"))
        if not lines:
            raise ValidationError("Cannot post an adjustment with no lines.")

        self.adjustment_date = to_date(self.adjustment_date)
        occurred_at = timezone.now()
        if not self.number:
            self.number = DocumentSequence.next_for(
                "inventory.adjustment", self.adjustment_date,
                name="Stock Adjustments", prefix="ADJ-",
            )

        label = memo or self.memo or f"Stock adjustment {self.number} ({self.reason})"
        total = Decimal("0")
        for line in lines:
            if not self.reason.permits(line.quantity):
                raise ValidationError(
                    f"{self.reason} may not be used to "
                    f"{'increase' if line.quantity > 0 else 'decrease'} stock."
                )
            line.post(self, occurred_at, label)
            total += line.value()

        entry = self._post_ledger(lines, label)
        self.journal_entry = entry
        self.posted = True
        self.posted_at = occurred_at
        super().save(update_fields=[
            "number", "adjustment_date", "journal_entry", "posted", "posted_at", "updated_at",
        ])
        return entry

    def _post_ledger(self, lines, label):
        """
        Dr Inventory / Cr reason when stock is written up, and the other
        way round when it is written down.

        Each item's own inventory account is used, so an adjustment
        spanning two items with different accounts posts two pairs rather
        than netting them into one and losing which account moved.
        """
        by_account = {}
        for line in lines:
            account = inventory_account_for(line.item)
            by_account[account] = by_account.get(account, Decimal("0")) + line.value()

        rows = [
            (account, round_money(amount))
            for account, amount in by_account.items()
            if round_money(amount)
        ]
        if not rows:
            # A count that found exactly what the books said still posts
            # its movements-free self as a document; there is simply no
            # money to move, and an unbalanced empty entry is not a record.
            return None

        entry = JournalEntry.objects.create(
            date=self.adjustment_date, reference=self.number, memo=label[:255],
        )
        offset = Decimal("0")
        for account, amount in rows:
            if amount > 0:
                JournalLine.objects.create(
                    entry=entry, account=account, debit=amount, description=label[:255]
                )
            else:
                JournalLine.objects.create(
                    entry=entry, account=account, credit=-amount, description=label[:255]
                )
            offset += amount
        if offset > 0:
            JournalLine.objects.create(
                entry=entry, account=self.reason.account, credit=offset,
                description=label[:255],
            )
        else:
            JournalLine.objects.create(
                entry=entry, account=self.reason.account, debit=-offset,
                description=label[:255],
            )
        entry.post()
        return entry

    @transaction.atomic
    def void(self, on_date=None, memo=""):
        """
        Undo a posted adjustment: reverse the ledger entry and put the
        quantity back.

        Written in the same sitting as post(), because three of the last
        four defects found here were reverse paths that were never
        written.
        """
        if not self.posted:
            raise ValidationError("Only a posted adjustment can be voided.")
        if self.is_voided():
            raise ValidationError("This adjustment has already been voided.")

        on_date = to_date(on_date) or timezone.now().date()
        occurred_at = timezone.now()
        label = memo or f"Void of stock adjustment {self.number}"
        for line in self.lines.select_related("item", "uom"):
            line.reverse(self, occurred_at, label)
        if self.journal_entry_id:
            self.voided_entry = self.journal_entry.create_reversal(
                entry_date=on_date, memo=label
            )
        self.voided_at = occurred_at
        super().save(update_fields=["voided_entry", "voided_at", "updated_at"])
        return self.voided_entry

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            previous = StockAdjustment.objects.filter(pk=self.pk).first()
            if previous is not None and previous.posted:
                raise ValidationError(
                    "Cannot modify a posted adjustment. Void it and raise another."
                )
        super().save(*args, **kwargs)


class StockAdjustmentLine(AuditModel):
    adjustment = models.ForeignKey(
        StockAdjustment, related_name="lines", on_delete=models.CASCADE
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="adjustment_lines")
    uom = models.ForeignKey(
        "core.UnitOfMeasure", on_delete=models.PROTECT, related_name="+",
        help_text="The unit the quantity below is written in.",
    )
    quantity = models.DecimalField(
        max_digits=18, decimal_places=4,
        help_text="Signed: positive writes stock up, negative writes it down.",
    )
    unit_cost = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True,
        help_text="What a stocking unit is worth for this adjustment. Left blank "
                  "it is taken from the weighted average when the line posts, and "
                  "frozen there.",
    )
    movement = models.ForeignKey(
        StockMovement, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        editable=False,
        help_text="The movement this line wrote, so voiding reverses exactly what "
                  "was written rather than recomputing it.",
    )
    reversal_movement = models.ForeignKey(
        StockMovement, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        editable=False,
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                check=~Q(quantity=0), name="adjustment_line_quantity_not_zero"
            ),
        ]

    def __str__(self):
        return f"{self.quantity} {self.uom} {self.item.sku}"

    def stock_quantity(self):
        """The signed quantity in the item's stocking unit."""
        return self.item.to_stock_quantity(self.quantity, self.uom)

    def value(self):
        """
        Signed value this line moves, at the cost frozen when it posted.

        Recomputing from today's average would give a different answer
        every time the average moved, and the ledger entry it is supposed
        to explain was written once.
        """
        if self.unit_cost is None:
            return Decimal("0")
        return (self.stock_quantity() * self.unit_cost).quantize(Decimal("0.01"))

    def post(self, adjustment, occurred_at, label):
        item = self.item
        if not item.track_inventory:
            raise ValidationError(
                f"{item} is not stocked, so there is no quantity to adjust."
            )
        item.check_uom(self.uom)

        quantity = self.stock_quantity()
        if self.unit_cost is None:
            # An outbound movement is valued by the replay at the running
            # average, so the ledger entry has to use the same number or
            # the two records of what left the shelf disagree. Freezing it
            # is what makes the entry explainable a year later.
            self.unit_cost = item.average_cost_at(adjustment.warehouse)
            if quantity > 0 and not self.unit_cost:
                raise ValidationError(
                    f"There is no {item} at {adjustment.warehouse} to take a cost from. "
                    "Give the line an explicit unit cost."
                )
        on_hand = item.on_hand_at(adjustment.warehouse)
        if quantity < 0 and -quantity > on_hand and not adjustment.warehouse.allow_negative_stock:
            raise ValidationError(
                f"Only {on_hand} {item.uom} of {item} on hand at {adjustment.warehouse}; "
                f"cannot write off {-quantity}."
            )

        self.movement = StockMovement.objects.create(
            item=item,
            warehouse=adjustment.warehouse,
            movement_type=MovementType.ADJUSTMENT,
            uom=item.uom,
            quantity=quantity,
            # Both numbers are per stocking unit: the cost above came from
            # this ledger's own average, so the quantity has to match it.
            unit_cost=self.unit_cost,
            reference=adjustment.number,
            occurred_at=occurred_at,
            notes=label,
        )
        super().save(update_fields=["unit_cost", "movement", "updated_at"])
        return self.movement

    def reverse(self, adjustment, occurred_at, label):
        """
        Put back exactly what this line took, in quantity and in value.

        Quantity is the easy half. Value is not: the replay prices an
        outbound movement at the running weighted average, ignoring
        unit_cost, so undoing a write-up of 10 at 8 after the average has
        moved to 5.27 would take 52.73 off the shelf while the ledger
        reversal credits 80. The difference never clears.

        So the reversal carries the residue as a value_adjustment, which
        is already what "value with no quantity attached" means here. It
        is computed against the average as it stands now, which is the
        average the replay will use, because this movement is the latest
        one.
        """
        if self.movement_id is None:
            return None
        original = self.movement
        moved_value = (original.quantity * (original.unit_cost or Decimal("0"))) + (
            original.value_adjustment or Decimal("0")
        )
        quantity = -original.quantity
        residue = None
        if quantity < 0:
            on_hand = self.item.on_hand_at(adjustment.warehouse)
            if -quantity > on_hand and not adjustment.warehouse.allow_negative_stock:
                raise ValidationError(
                    f"Only {on_hand} {self.item.uom} of {self.item} remains at "
                    f"{adjustment.warehouse}; voiding this adjustment would take "
                    f"back {-quantity}."
                )
            average = self.item.average_cost_at(adjustment.warehouse)
            # The replay will take (leaving x average) off. It should take
            # off what this line put on, so the residue is the difference.
            residue = (
                (-quantity) * average - moved_value
            ).quantize(Decimal("0.01")) or None

        self.reversal_movement = StockMovement.objects.create(
            item=self.item,
            warehouse=adjustment.warehouse,
            movement_type=MovementType.ADJUSTMENT,
            uom=self.item.uom,
            quantity=quantity,
            unit_cost=original.unit_cost,
            value_adjustment=residue,
            reference=adjustment.number,
            occurred_at=occurred_at,
            notes=label,
        )
        super().save(update_fields=["reversal_movement", "updated_at"])
        return self.reversal_movement

    def save(self, *args, **kwargs):
        if self.adjustment_id and StockAdjustment.objects.filter(
            pk=self.adjustment_id, posted=True
        ).exists():
            raise ValidationError(
                "Cannot modify a line on a posted adjustment. Void it and raise another."
            )
        if self.uom_id is None and self.item_id:
            self.uom = self.item.uom
        if self.item_id and self.uom_id:
            self.item.check_uom(self.uom)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.adjustment.posted:
            raise ValidationError(
                "Cannot delete a line on a posted adjustment. Void it and raise another."
            )
        super().delete(*args, **kwargs)


class StockCount(AuditModel):
    """
    A sheet of what was actually found on the shelf.

    Counting and adjusting are deliberately separate. A count is
    evidence — somebody walked the aisle on a date and wrote numbers
    down — and the adjustment is the accounting consequence of it. Fold
    them into one document and the evidence is lost the moment the
    consequence is voided.

    The system quantity is frozen onto each line when the line is
    written, not read again at posting time. If something shipped
    between the walk and the posting, the variance on the sheet is no
    longer the variance in the system, and writing the shelf down by it
    would write off stock that legitimately left.
    """

    number = models.CharField(max_length=32, blank=True)
    count_date = models.DateField()
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="counts")
    reason = models.ForeignKey(
        AdjustmentReason, on_delete=models.PROTECT, related_name="counts",
        help_text="Where the variance posts. A count that finds less than the books "
                  "said is normally shrinkage.",
    )
    memo = models.CharField(max_length=255, blank=True)
    counted_by = models.CharField(max_length=255, blank=True)
    posted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-count_date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""), name="stock_count_number_unique"
            ),
        ]

    def __str__(self):
        return self.number or f"Draft count {self.pk}"

    def adjustment(self):
        """The adjustment this count raised, if it raised one."""
        return self.adjustments.first()

    @transaction.atomic
    def add(self, item, counted, uom=None):
        """
        Put an item on the sheet, freezing what the system says right now.

        Freezing here rather than at posting time is the whole point of
        the document: the sheet records a moment, and the moment is when
        somebody looked.
        """
        if self.posted:
            raise ValidationError("Cannot add to a posted count.")
        uom = uom or item.uom
        item.check_uom(uom)
        return StockCountLine.objects.create(
            count=self, item=item, uom=uom,
            counted_quantity=Decimal(counted),
            system_quantity=item.on_hand_at(self.warehouse),
        )

    @transaction.atomic
    def post(self, memo=""):
        """
        Turn the variances into one adjustment and post it.

        A count that agrees with the books posts nothing and is still a
        posted count: the evidence that somebody looked and found no
        difference is worth as much as the evidence that they did.
        """
        if self.posted:
            raise ValidationError("This count is already posted.")
        lines = list(self.lines.select_related("item", "uom"))
        if not lines:
            raise ValidationError("Cannot post a count with no lines.")

        self.count_date = to_date(self.count_date)
        if not self.number:
            self.number = DocumentSequence.next_for(
                "inventory.count", self.count_date,
                name="Stock Counts", prefix="CNT-",
            )

        stale = [
            line for line in lines
            if line.item.on_hand_at(self.warehouse) != line.system_quantity
        ]
        if stale:
            names = ", ".join(str(line.item) for line in stale[:3])
            raise ValidationError(
                f"Stock moved after this sheet was written ({names}). The variance on "
                "it is no longer the variance in the system; recount those lines."
            )

        variances = [line for line in lines if line.variance()]
        adjustment = None
        if variances:
            adjustment = StockAdjustment.objects.create(
                adjustment_date=self.count_date,
                warehouse=self.warehouse,
                reason=self.reason,
                memo=memo or self.memo or f"Stock count {self.number}",
                count=self,
            )
            for line in variances:
                StockAdjustmentLine.objects.create(
                    adjustment=adjustment,
                    item=line.item,
                    uom=line.item.uom,
                    quantity=line.variance(),
                    notes=(
                        f"Counted {line.counted_quantity} {line.uom}, "
                        f"books said {line.system_quantity} {line.item.uom}"
                    ),
                )
            adjustment.post()

        self.posted = True
        self.posted_at = timezone.now()
        super().save(update_fields=["number", "count_date", "posted", "posted_at", "updated_at"])
        return adjustment

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            previous = StockCount.objects.filter(pk=self.pk).first()
            if previous is not None and previous.posted:
                raise ValidationError(
                    "Cannot modify a posted count. The sheet is the evidence; void the "
                    "adjustment it raised if the numbers were wrong."
                )
        super().save(*args, **kwargs)


class StockCountLine(AuditModel):
    count = models.ForeignKey(StockCount, related_name="lines", on_delete=models.CASCADE)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="count_lines")
    uom = models.ForeignKey(
        "core.UnitOfMeasure", on_delete=models.PROTECT, related_name="+",
        help_text="The unit the counter counted in — cases on the pallet, "
                  "eaches on the shelf.",
    )
    counted_quantity = models.DecimalField(
        max_digits=18, decimal_places=4, help_text="What was found, in `uom`."
    )
    system_quantity = models.DecimalField(
        max_digits=18, decimal_places=4, editable=False,
        help_text="What the books said when this line was written, in the item's "
                  "stocking unit. Frozen: re-reading it at posting time would hide "
                  "anything that moved in between.",
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["count", "item"], name="one_count_line_per_item"
            ),
            models.CheckConstraint(
                check=Q(counted_quantity__gte=0), name="count_line_quantity_not_negative"
            ),
        ]

    def __str__(self):
        return f"{self.item.sku}: counted {self.counted_quantity} {self.uom}"

    def counted_in_stock_units(self):
        return self.item.to_stock_quantity(self.counted_quantity, self.uom)

    def variance(self):
        """
        Found minus expected, in the item's stocking unit.

        Positive means the shelf holds more than the books do.
        """
        return self.counted_in_stock_units() - self.system_quantity

    def save(self, *args, **kwargs):
        if self.count_id and StockCount.objects.filter(pk=self.count_id, posted=True).exists():
            raise ValidationError("Cannot modify a line on a posted count.")
        if self.uom_id is None and self.item_id:
            self.uom = self.item.uom
        if self.item_id and self.uom_id:
            self.item.check_uom(self.uom)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.count.posted:
            raise ValidationError("Cannot delete a line on a posted count.")
        super().delete(*args, **kwargs)
