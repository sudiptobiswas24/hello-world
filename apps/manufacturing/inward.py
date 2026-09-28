"""
Material a customer sends to be worked on: job work coming in.

The customer's granules are on the premises and not on the books. They
arrive against the customer's own challan into a warehouse held for
that customer (`Warehouse.held_for`), at no value, and from there go
only into that customer's runs. What is not used goes back on a material
return that names the receipt it came in on, which is what the
customer's own ITC-04 has to match.

**Nothing here is valued**, so nothing here posts to the ledger. The
runs that use the material carry what the plant adds to it, conversion
and its own additives, and that is what the sale of the conversion
recovers.

**The register is derived**: received, consumed by runs (net of what
runs handed back), returned, and on hand, per customer and item, from
the documents. It foots, or the difference is shown. A receipt still
partly unused a year on is flagged. The one-year return is the
customer's obligation under the job-work rules, but they will ask the
plant, and the plant should see it coming.

**Goods made from one customer's material do not go to another.** A
delivery's batches are walked back through the runs that made them.
Only batches can be walked: an untracked item's shipment cannot be
traced to a run, and is not checked.
"""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, DocumentSequence, to_date
from apps.inventory.availability import check_available

ZERO = Decimal("0")
KEEP_FOR = datetime.timedelta(days=365)
MAX_DEPTH = 6


def _q(value):
    return format(Decimal(value).normalize(), "f")


def _check_store(warehouse, customer):
    if warehouse.held_for_id != customer.pk:
        raise ValidationError(
            f"{warehouse} is not held for {customer}. A customer's material is kept "
            "apart from the company's stock and from every other customer's."
        )


def _movement(line, warehouse, quantity, reference, notes):
    from apps.inventory.models import MovementType, StockMovement

    return StockMovement.objects.create(
        item=line.item, warehouse=warehouse,
        movement_type=MovementType.RECEIPT if quantity > 0 else MovementType.ISSUE,
        uom=line.item.uom, quantity=quantity, unit_cost=ZERO, lot=line.lot,
        occurred_at=timezone.now(), reference=reference, notes=notes[:255],
    )


class _Posted(AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    customer = models.ForeignKey("core.Party", on_delete=models.PROTECT, related_name="+")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT,
                                  related_name="+")
    posted = models.BooleanField(default=False, editable=False)
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_reason = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        abstract = True

    def __str__(self):
        return self.number or f"Draft {self._meta.verbose_name} {self.pk}"

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk, posted=True).exists() \
                and not getattr(self, "_writing", False):
            raise ValidationError(f"{self} is posted. Void it and raise another.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.posted:
            raise ValidationError(f"{self} is posted; void it.")
        return super().delete(*args, **kwargs)

    def _write(self, fields):
        self._writing = True
        try:
            self.save(update_fields=fields + ["updated_at"])
        finally:
            self._writing = False


class _Line(AuditModel):
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    lot = models.ForeignKey("inventory.Lot", null=True, blank=True, on_delete=models.PROTECT,
                            related_name="+")
    quantity = models.DecimalField(max_digits=18, decimal_places=4,
                                   help_text="In the item's stocking unit.")

    class Meta:
        abstract = True

    def document(self):
        raise NotImplementedError

    def save(self, *args, **kwargs):
        if self.document().posted:
            raise ValidationError(f"{self.document()} is posted; its lines do not change.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.document().posted:
            raise ValidationError(f"{self.document()} is posted; its lines do not change.")
        return super().delete(*args, **kwargs)

    def _check(self):
        if self.quantity is None or self.quantity <= 0:
            raise ValidationError(f"{self.item}: a quantity is more than nothing.")
        if self.lot_id and self.lot.item_id != self.item_id:
            raise ValidationError(f"{self.lot} is a batch of {self.lot.item}, not {self.item}.")
        if self.item.tracking != "none" and not self.lot_id:
            raise ValidationError(f"{self.item} moves by batch; name one.")


class CustomerMaterialReceipt(_Posted):
    received_on = models.DateField()
    their_challan = models.CharField(
        max_length=32, help_text="The customer's own challan number: what their ITC-04 "
                                 "reports and what every return is matched against.")
    their_challan_date = models.DateField()

    class Meta:
        ordering = ["-received_on", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""),
                                    name="customer_material_receipt_number_unique"),
            # Posted ones only: a draft is somebody part-way through typing,
            # and two of those are refused, with the reason, when the
            # second is posted rather than as a database error on save.
            models.UniqueConstraint(fields=["customer", "their_challan"],
                                    condition=Q(posted=True, voided_at__isnull=True),
                                    name="one_receipt_per_customer_challan"),
        ]

    @transaction.atomic
    def post(self):
        from apps.inventory.locking import lock_positions

        if self.posted:
            raise ValidationError(f"{self} is already posted.")
        _check_store(self.warehouse, self.customer)
        if not self.their_challan.strip():
            raise ValidationError("Give the customer's challan number.")
        if to_date(self.their_challan_date) > to_date(self.received_on):
            raise ValidationError("Their challan cannot be dated after the goods arrived.")
        clash = CustomerMaterialReceipt.objects.filter(
            customer=self.customer, their_challan=self.their_challan.strip(),
            voided_at__isnull=True, posted=True).exclude(pk=self.pk).first()
        if clash is not None:
            raise ValidationError(
                f"{self.customer}'s challan {self.their_challan} came in on {clash} already."
            )
        lines = list(self.lines.select_related("item", "lot"))
        if not lines:
            raise ValidationError("A receipt with nothing on it received nothing.")
        for line in lines:
            line._check()
        lock_positions((line.item, self.warehouse) for line in lines)
        self.received_on = to_date(self.received_on)
        self.number = DocumentSequence.next_for(
            "manufacturing.customer_material_receipt", self.received_on,
            name="Customer Material Receipts", prefix="CMR-")
        for line in lines:
            line.stock_movement = _movement(line, self.warehouse, line.quantity, self.number,
                                            f"{self.customer}'s challan {self.their_challan}")
            super(_Line, line).save(update_fields=["stock_movement", "updated_at"])
        self.posted, self.posted_at = True, timezone.now()
        self.their_challan = self.their_challan.strip()
        self._write(["number", "received_on", "their_challan", "posted", "posted_at"])

    @transaction.atomic
    def void(self, reason):
        """Withdraw a receipt entered in error, while all of it is still on the shelf."""
        from apps.inventory.locking import lock_positions

        if not self.posted or self.voided_at is not None:
            raise ValidationError(f"{self} is not a standing receipt.")
        if not (reason or "").strip():
            raise ValidationError("Say why the receipt is withdrawn.")
        lines = list(self.lines.select_related("item", "lot"))
        if CustomerMaterialReturnLine.objects.filter(
                receipt_line__in=lines, material_return__posted=True,
                material_return__voided_at__isnull=True).exists():
            raise ValidationError(f"Material has gone back against {self}; void that first.")
        lock_positions((line.item, self.warehouse) for line in lines)
        for line in lines:
            on_hand = line.lot.on_hand_at(self.warehouse) \
                if line.lot_id else line.item.on_hand_at(self.warehouse)
            if on_hand < line.quantity:
                raise ValidationError(
                    f"Only {on_hand} of {line.item} is still on the shelf; the rest has "
                    f"gone into runs. {self} cannot be withdrawn from under them."
                )
        for line in lines:
            _movement(line, self.warehouse, -line.quantity, self.number, f"Void of {self.number}")
        self.voided_at, self.voided_reason = timezone.now(), reason.strip()
        self._write(["voided_at", "voided_reason"])


class CustomerMaterialReceiptLine(_Line):
    receipt = models.ForeignKey(CustomerMaterialReceipt, on_delete=models.CASCADE,
                                related_name="lines")
    declared_value = models.DecimalField(
        max_digits=18, decimal_places=2, default=ZERO,
        help_text="The value on the customer's challan. Not booked: it is theirs, and "
                  "kept only for the returns and the e-way bills that must state it.")
    stock_movement = models.ForeignKey("inventory.StockMovement", null=True, blank=True,
                                       on_delete=models.PROTECT, related_name="+",
                                       editable=False)

    class Meta:
        ordering = ["receipt", "id"]
        constraints = [
            models.CheckConstraint(check=Q(quantity__gt=0) & Q(declared_value__gte=0),
                                   name="customer_material_receipt_line_sensible"),
        ]

    def __str__(self):
        return f"{self.quantity} {self.item} on {self.receipt}"

    def document(self):
        return self.receipt

    def returned(self):
        return sum((line.quantity for line in self.returns.filter(
            material_return__posted=True, material_return__voided_at__isnull=True)), ZERO)


class CustomerMaterialReturn(_Posted):
    returned_on = models.DateField()

    class Meta:
        ordering = ["-returned_on", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""),
                                    name="customer_material_return_number_unique"),
        ]

    @transaction.atomic
    def post(self):
        from apps.inventory.locking import lock_positions

        if self.posted:
            raise ValidationError(f"{self} is already posted.")
        _check_store(self.warehouse, self.customer)
        lines = list(self.lines.select_related("item", "lot", "receipt_line__receipt"))
        if not lines:
            raise ValidationError("A return with nothing on it returns nothing.")
        lock_positions((line.item, self.warehouse) for line in lines)
        asked = defaultdict(lambda: ZERO)
        for line in lines:
            line._check()
            received = line.receipt_line
            if received.receipt.customer_id != self.customer_id or not received.receipt.posted \
                    or received.receipt.voided_at is not None:
                raise ValidationError(f"{received.receipt} is not a standing receipt from "
                                      f"{self.customer}.")
            if received.item_id != line.item_id or received.lot_id != line.lot_id:
                def what(row):
                    return row.item.sku + (f" batch {row.lot.code}" if row.lot_id else "")
                raise ValidationError(
                    f"{received.receipt.their_challan} brought in {what(received)}; this "
                    f"line sends back {what(line)}."
                )
            asked[received.pk] += line.quantity
            left = received.quantity - received.returned()
            if asked[received.pk] > left:
                raise ValidationError(
                    f"{received} has {_q(left)} left to return against it, not "
                    f"{_q(asked[received.pk])}."
                )
        for line in lines:
            check_available(line.item, self.warehouse, line.quantity, lot=line.lot,
                            action="go back to the customer")
        self.returned_on = to_date(self.returned_on)
        self.number = DocumentSequence.next_for(
            "manufacturing.customer_material_return", self.returned_on,
            name="Customer Material Returns", prefix="CMX-")
        for line in lines:
            line.stock_movement = _movement(line, self.warehouse, -line.quantity, self.number,
                                            f"Back to {self.customer} against "
                                            f"{line.receipt_line.receipt.their_challan}")
            super(_Line, line).save(update_fields=["stock_movement", "updated_at"])
        self.posted, self.posted_at = True, timezone.now()
        self._write(["number", "returned_on", "posted", "posted_at"])

    @transaction.atomic
    def void(self, reason):
        if not self.posted or self.voided_at is not None:
            raise ValidationError(f"{self} is not a standing return.")
        if not (reason or "").strip():
            raise ValidationError("Say why the return is withdrawn.")
        for line in self.lines.select_related("item", "lot"):
            _movement(line, self.warehouse, line.quantity, self.number, f"Void of {self.number}")
        self.voided_at, self.voided_reason = timezone.now(), reason.strip()
        self._write(["voided_at", "voided_reason"])


class CustomerMaterialReturnLine(_Line):
    material_return = models.ForeignKey(CustomerMaterialReturn, on_delete=models.CASCADE,
                                        related_name="lines")
    receipt_line = models.ForeignKey(CustomerMaterialReceiptLine, on_delete=models.PROTECT,
                                     related_name="returns",
                                     help_text="What came in on the customer's challan that "
                                               "this sends back.")
    stock_movement = models.ForeignKey("inventory.StockMovement", null=True, blank=True,
                                       on_delete=models.PROTECT, related_name="+",
                                       editable=False)

    class Meta:
        ordering = ["material_return", "id"]
        constraints = [
            models.CheckConstraint(check=Q(quantity__gt=0),
                                   name="customer_material_return_line_positive"),
        ]

    def __str__(self):
        return f"{self.quantity} {self.item} on {self.material_return}"

    def document(self):
        return self.material_return


def register(customer, as_of=None):
    """Per item: received, consumed, returned, on hand, and whether they foot."""
    from apps.inventory.models import Warehouse

    from .orders import MaterialIssueLine

    as_of = to_date(as_of) or timezone.localdate()
    stores = list(Warehouse.objects.filter(held_for=customer))
    rows = defaultdict(lambda: {"received": ZERO, "consumed": ZERO, "returned": ZERO})
    items = {}
    receipts = CustomerMaterialReceiptLine.objects.filter(
        receipt__customer=customer, receipt__posted=True,
        receipt__voided_at__isnull=True).select_related("item", "receipt").order_by(
        "receipt__received_on", "id")
    for line in receipts:
        rows[line.item_id]["received"] += line.quantity
        items[line.item_id] = line.item
    for line in CustomerMaterialReturnLine.objects.filter(
            material_return__customer=customer, material_return__posted=True,
            material_return__voided_at__isnull=True).select_related("item"):
        rows[line.item_id]["returned"] += line.quantity
        items[line.item_id] = line.item
    for line in MaterialIssueLine.objects.filter(
            issue__warehouse__in=stores, issue__posted=True,
            issue__voided_at__isnull=True).select_related("item", "uom", "issue"):
        rows[line.item_id]["consumed"] += line.stock_quantity() * line.issue.sign()
        items[line.item_id] = line.item
    result = []
    for item_id, row in rows.items():
        item = items[item_id]
        on_hand = sum((Decimal(item.on_hand_at(store)) for store in stores), ZERO)
        expected = row["received"] - row["consumed"] - row["returned"]
        result.append(row | {
            "item": item, "on_hand": on_hand, "unexplained": on_hand - expected,
            "overdue": _overdue(receipts, item_id, row["consumed"] + row["returned"], as_of),
        })
    return sorted(result, key=lambda row: row["item"].sku)


def _overdue(receipts, item_id, gone, as_of):
    """Receipts, oldest first, still partly here after a year: what went out
    is taken from the oldest first."""
    late = []
    for line in receipts:
        if line.item_id != item_id:
            continue
        used = min(gone, line.quantity)
        gone -= used
        left = line.quantity - used
        if left > 0 and as_of - line.receipt.received_on > KEEP_FOR:
            late.append({"receipt": line.receipt.number,
                         "their_challan": line.receipt.their_challan,
                         "received_on": line.receipt.received_on, "left": left})
    return late


def ownership_problems(customer, lots):
    """Batches made, at any depth, from material held for somebody else."""
    from .demand import runs_that_made

    problems, seen = [], set()

    def walk(lot, depth):
        if lot.pk in seen or depth > MAX_DEPTH:
            return
        seen.add(lot.pk)
        for run in runs_that_made(lot):
            # Net of what the run handed back: material drawn and returned
            # whole went into nothing, and is no reason to stop a shipment.
            kept = defaultdict(lambda: ZERO)
            owners, drawn = {}, defaultdict(lambda: ZERO)
            for issue in run.posted_issues():
                owner = issue.warehouse.held_for
                for line in issue.lines.select_related("lot", "uom", "item"):
                    quantity = line.stock_quantity() * issue.sign()
                    if owner is not None and owner.pk != customer.pk:
                        kept[owner.pk] += quantity
                        owners[owner.pk] = owner
                    if line.lot_id:
                        drawn[line.lot] += quantity
            for owner_pk, quantity in kept.items():
                if quantity > 0:
                    problems.append(
                        f"{lot.code} was made by {run.number} from {owners[owner_pk]}'s "
                        f"material; it is theirs to have back, not {customer}'s."
                    )
            for source, quantity in drawn.items():
                if quantity > 0:
                    walk(source, depth + 1)

    for lot in lots:
        walk(lot, 0)
    return list(dict.fromkeys(problems))
