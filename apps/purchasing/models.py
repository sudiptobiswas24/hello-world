from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.accounting.models import Account, JournalEntry, JournalLine
from apps.core.models import AuditModel, Currency, Party, PartyRole, UnitOfMeasure
from apps.inventory.models import Item, MovementType, StockMovement, Warehouse


def _require_vendor_role(party):
    if party and not party.role_assignments.filter(role=PartyRole.VENDOR).exists():
        raise ValidationError(f"{party} does not have the Vendor role.")


class OrderStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    CONFIRMED = "confirmed", "Confirmed"
    CANCELLED = "cancelled", "Cancelled"


class PurchaseOrder(AuditModel):
    vendor = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="purchase_orders")
    order_date = models.DateField()
    reference = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, choices=OrderStatus.choices, default=OrderStatus.DRAFT)
    currency = models.ForeignKey(Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-order_date", "-id"]

    def __str__(self):
        return f"PO-{self.pk} {self.vendor}"

    def clean(self):
        _require_vendor_role(self.vendor)

    def total(self):
        return sum((line.subtotal() for line in self.lines.all()), Decimal("0"))


class PurchaseOrderLine(AuditModel):
    order = models.ForeignKey(PurchaseOrder, related_name="lines", on_delete=models.CASCADE)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="purchase_order_lines")
    uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)

    def __str__(self):
        return f"{self.item} x{self.quantity}"

    def subtotal(self):
        return self.quantity * self.unit_price

    def quantity_received(self):
        """Net quantity received so far: posted receipts minus posted returns."""
        received = self.receipt_lines.filter(
            receipt__posted=True, receipt__reverses__isnull=True
        ).aggregate(total=models.Sum("quantity_received"))["total"] or Decimal("0")
        returned = self.receipt_lines.filter(
            receipt__posted=True, receipt__reverses__isnull=False
        ).aggregate(total=models.Sum("quantity_received"))["total"] or Decimal("0")
        return received - returned

    def is_fully_received(self):
        return self.quantity_received() >= self.quantity


class Bill(AuditModel):
    """
    Vendor bill — the Purchasing mirror of Sales' Invoice. Posting builds
    a balanced JournalEntry (Dr Expense per line / Cr Accounts Payable)
    via Accounting; Purchasing never writes ledger rows itself. A posted
    bill is immutable; the only correction path is a debit note
    (create_debit_note), which — exactly like Invoice.create_credit_note —
    delegates to JournalEntry.create_reversal() instead of a bespoke
    correction mechanism.
    """

    vendor = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="bills")
    bill_date = models.DateField()
    reference = models.CharField(max_length=64, blank=True)
    purchase_order = models.ForeignKey(
        PurchaseOrder, null=True, blank=True, on_delete=models.PROTECT, related_name="bills"
    )
    payable_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    debits = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="debit_notes",
        help_text="Set when this bill is a debit note correcting another bill.",
    )
    journal_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False
    )
    posted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-bill_date", "-id"]

    def __str__(self):
        kind = "DN" if self.debits_id else "BILL"
        return f"{kind}-{self.pk} {self.vendor}"

    def clean(self):
        _require_vendor_role(self.vendor)
        if self.debits_id and self.debits.vendor_id != self.vendor_id:
            raise ValidationError("A debit note must be for the same vendor as the bill it corrects.")

    def total(self):
        return sum((line.subtotal() for line in self.lines.all()), Decimal("0"))

    def _was_posted_in_db(self):
        if not self.pk:
            return False
        return Bill.objects.filter(pk=self.pk, posted=True).exists()

    def save(self, *args, **kwargs):
        if self._was_posted_in_db():
            raise ValidationError("This bill is posted and immutable. Issue a debit note instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.posted:
            raise ValidationError("Posted bills cannot be deleted. Issue a debit note instead.")
        super().delete(*args, **kwargs)

    def _build_journal_entry(self):
        lines = list(self.lines.all())
        if not lines:
            raise ValidationError("Cannot post a bill with no lines.")
        entry = JournalEntry.objects.create(
            date=self.bill_date,
            reference=self.reference,
            memo=f"Bill BILL-{self.pk} from {self.vendor}",
        )
        JournalLine.objects.create(
            entry=entry,
            account=self.payable_account,
            party=self.vendor,
            credit=self.total(),
            description=f"Bill BILL-{self.pk}",
        )
        for line in lines:
            JournalLine.objects.create(
                entry=entry,
                account=line.expense_account,
                party=self.vendor,
                debit=line.subtotal(),
                description=line.description or str(line.item),
            )
        return entry

    @transaction.atomic
    def post(self, memo=None):
        if self.posted:
            raise ValidationError("This bill is already posted.")
        if self.debits_id:
            original = self.debits
            if not original.posted or not original.journal_entry_id:
                raise ValidationError("Cannot post a debit note against an unposted bill.")
            entry = original.journal_entry.create_reversal(
                entry_date=self.bill_date,
                memo=memo or f"Debit note DN-{self.pk} for BILL-{original.pk}",
            )
        else:
            entry = self._build_journal_entry()
            entry.post()

        self.journal_entry = entry
        self.posted = True
        self.posted_at = timezone.now()
        super(Bill, self).save(update_fields=["journal_entry", "posted", "posted_at", "updated_at"])

    @transaction.atomic
    def create_debit_note(self, memo=""):
        if not self.posted:
            raise ValidationError("Only a posted bill can be corrected with a debit note.")
        if self.debits_id:
            raise ValidationError("Cannot issue a debit note against a debit note.")

        debit_note = Bill.objects.create(
            vendor=self.vendor,
            bill_date=timezone.now().date(),
            reference=self.reference,
            payable_account=self.payable_account,
            debits=self,
        )
        for line in self.lines.all():
            BillLine.objects.create(
                bill=debit_note,
                item=line.item,
                description=line.description,
                quantity=line.quantity,
                unit_price=line.unit_price,
                expense_account=line.expense_account,
            )
        debit_note.post(memo=memo)
        return debit_note


class BillLine(AuditModel):
    bill = models.ForeignKey(Bill, related_name="lines", on_delete=models.CASCADE)
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="bill_lines")
    description = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    expense_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")

    def __str__(self):
        return f"{self.item or self.description} x{self.quantity}"

    def subtotal(self):
        return self.quantity * self.unit_price

    def save(self, *args, **kwargs):
        if self.bill_id and Bill.objects.filter(pk=self.bill_id, posted=True).exists():
            raise ValidationError(
                "Cannot modify a line on a posted bill. Issue a debit note instead."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.bill.posted:
            raise ValidationError(
                "Cannot delete a line on a posted bill. Issue a debit note instead."
            )
        super().delete(*args, **kwargs)


class GoodsReceipt(AuditModel):
    """
    Closes the loop between Purchasing and Inventory: posting a receipt
    creates real StockMovement rows. Same posted/immutable/reversal
    pattern as JournalEntry/Invoice/Bill — a mistaken receipt is corrected
    with create_return(), which reverses the whole receipt (same lines,
    opposite stock effect), never by editing a posted receipt.

    Only whole-receipt reversal is supported, not partial-quantity
    returns — that mirrors how JournalEntry.create_reversal() and the
    Sales/Purchasing credit/debit notes work, and keeps this from needing
    its own separate partial-correction design.
    """

    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="goods_receipts")
    receipt_date = models.DateField()
    reference = models.CharField(max_length=64, blank=True)
    reverses = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by"
    )
    posted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-receipt_date", "-id"]

    def __str__(self):
        kind = "RETURN" if self.reverses_id else "GR"
        return f"{kind}-{self.pk} for {self.purchase_order}"

    def _was_posted_in_db(self):
        if not self.pk:
            return False
        return GoodsReceipt.objects.filter(pk=self.pk, posted=True).exists()

    def save(self, *args, **kwargs):
        if self._was_posted_in_db():
            raise ValidationError(
                "This goods receipt is posted and immutable. Create a return instead."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.posted:
            raise ValidationError("Posted goods receipts cannot be deleted. Create a return instead.")
        super().delete(*args, **kwargs)

    @transaction.atomic
    def post(self):
        if self.posted:
            raise ValidationError("This goods receipt is already posted.")
        lines = list(self.lines.all())
        if not lines:
            raise ValidationError("Cannot post a goods receipt with no lines.")

        is_return = bool(self.reverses_id)
        if is_return and not self.reverses.posted:
            raise ValidationError("Cannot return an unposted goods receipt.")

        if not is_return:
            for line in lines:
                already_received = line.order_line.quantity_received()
                if already_received + line.quantity_received > line.order_line.quantity:
                    raise ValidationError(
                        f"Receiving {line.quantity_received} of {line.order_line.item} would "
                        f"exceed the ordered quantity ({line.order_line.quantity}; "
                        f"{already_received} already received)."
                    )

        for line in lines:
            movement_type = MovementType.ISSUE if is_return else MovementType.RECEIPT
            quantity = -line.quantity_received if is_return else line.quantity_received
            StockMovement.objects.create(
                item=line.order_line.item,
                warehouse=line.warehouse,
                movement_type=movement_type,
                quantity=quantity,
                reference=self.reference or f"GR-{self.pk}",
                occurred_at=timezone.now(),
                notes=(
                    f"{'Return for' if is_return else 'Receipt for'} "
                    f"{self.purchase_order} (GR-{self.pk})"
                ),
            )

        self.posted = True
        self.posted_at = timezone.now()
        super(GoodsReceipt, self).save(update_fields=["posted", "posted_at", "updated_at"])

    @transaction.atomic
    def create_return(self):
        if not self.posted:
            raise ValidationError("Only a posted goods receipt can be returned.")
        if self.reverses_id:
            raise ValidationError("Cannot return a return.")
        if self.reversed_by.exists():
            raise ValidationError("This goods receipt has already been returned.")

        return_receipt = GoodsReceipt.objects.create(
            purchase_order=self.purchase_order,
            receipt_date=timezone.now().date(),
            reference=self.reference,
            reverses=self,
        )
        for line in self.lines.all():
            GoodsReceiptLine.objects.create(
                receipt=return_receipt,
                order_line=line.order_line,
                warehouse=line.warehouse,
                quantity_received=line.quantity_received,
            )
        return_receipt.post()
        return return_receipt


class GoodsReceiptLine(AuditModel):
    receipt = models.ForeignKey(GoodsReceipt, related_name="lines", on_delete=models.CASCADE)
    order_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name="receipt_lines")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+")
    quantity_received = models.DecimalField(max_digits=18, decimal_places=4)

    def __str__(self):
        return f"{self.order_line.item} x{self.quantity_received} @ {self.warehouse}"

    def clean(self):
        if self.order_line_id and self.receipt_id and self.order_line.order_id != self.receipt.purchase_order_id:
            raise ValidationError("This line's order_line must belong to the receipt's purchase_order.")
        if self.quantity_received is not None and self.quantity_received <= 0:
            raise ValidationError("quantity_received must be positive.")

    def save(self, *args, **kwargs):
        if self.receipt_id and GoodsReceipt.objects.filter(pk=self.receipt_id, posted=True).exists():
            raise ValidationError(
                "Cannot modify a line on a posted goods receipt. Create a return instead."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.receipt.posted:
            raise ValidationError(
                "Cannot delete a line on a posted goods receipt. Create a return instead."
            )
        super().delete(*args, **kwargs)
