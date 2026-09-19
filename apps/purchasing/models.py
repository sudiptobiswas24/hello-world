from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from django.db.models import Q

from apps.accounting.models import Account, JournalEntry, JournalLine, round_money
from apps.core.models import (
    AuditModel,
    Company,
    Currency,
    DocumentSequence,
    Party,
    PartyRole,
    PaymentTerms,
    UnitOfMeasure,
    to_date,
)
from apps.inventory.models import Item, MovementType, StockMovement, Warehouse
from apps.inventory.valuation import post_inventory_entry


def _require_vendor_role(party):
    if party and not party.role_assignments.filter(role=PartyRole.VENDOR).exists():
        raise ValidationError(f"{party} does not have the Vendor role.")


class FulfilmentStatus(models.TextChoices):
    NONE = "none", "None"
    PARTIAL = "partial", "Partial"
    FULL = "full", "Full"


class OrderStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    CONFIRMED = "confirmed", "Confirmed"
    CANCELLED = "cancelled", "Cancelled"


class PurchaseOrder(AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    vendor = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="purchase_orders")
    order_date = models.DateField()
    reference = models.CharField(
        max_length=64, blank=True, help_text="The vendor's own quote or reference, if any."
    )
    status = models.CharField(max_length=16, choices=OrderStatus.choices, default=OrderStatus.DRAFT)
    currency = models.ForeignKey(Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-order_date", "-id"]
        constraints = [
            # Drafts all carry an empty number until confirmed, so uniqueness
            # can only apply once one has been assigned.
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""), name="unique_purchase_order_number"
            )
        ]

    def __str__(self):
        return f"{self.number or f'PO-draft-{self.pk}'} {self.vendor}"

    def clean(self):
        _require_vendor_role(self.vendor)

    def save(self, *args, **kwargs):
        if self._state.adding and self.vendor_id and not self.currency_id:
            self.currency = self.vendor.default_currency
        super().save(*args, **kwargs)

    def total(self):
        return sum((line.subtotal() for line in self.lines.all()), Decimal("0"))

    @transaction.atomic
    def confirm(self):
        """
        Commit to the order. Until this happens it is a shopping list, and
        goods arriving against a shopping list are goods nobody agreed to
        buy — the purchasing mirror of confirming a sales order.
        """
        if self.status == OrderStatus.CONFIRMED:
            raise ValidationError("This order is already confirmed.")
        if self.status == OrderStatus.CANCELLED:
            raise ValidationError("A cancelled order cannot be confirmed.")
        if not self.lines.exists():
            raise ValidationError("Cannot confirm an order with no lines.")
        if not self.number:
            self.number = DocumentSequence.next_for(
                "purchasing.order", self.order_date, name="Purchase Orders", prefix="PO-"
            )
        self.status = OrderStatus.CONFIRMED
        self.save(update_fields=["number", "status", "updated_at"])

    def cancel(self):
        if self.status == OrderStatus.CANCELLED:
            raise ValidationError("This order is already cancelled.")
        for line in self.lines.all():
            if line.quantity_received():
                raise ValidationError(
                    "Goods have been received against this order and it cannot be cancelled. "
                    "Return them instead."
                )
        self.status = OrderStatus.CANCELLED
        self.save(update_fields=["status", "updated_at"])

    def receipt_status(self):
        lines = list(self.lines.all())
        if not lines or all(line.quantity_received() <= 0 for line in lines):
            return FulfilmentStatus.NONE
        if all(line.is_fully_received() for line in lines):
            return FulfilmentStatus.FULL
        return FulfilmentStatus.PARTIAL


class PurchaseOrderLine(AuditModel):
    order = models.ForeignKey(PurchaseOrder, related_name="lines", on_delete=models.CASCADE)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="purchase_order_lines")
    uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)

    def __str__(self):
        return f"{self.item} x{self.quantity}"

    def subtotal(self):
        return round_money(self.quantity * self.unit_price)

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

    number = models.CharField(max_length=32, blank=True, editable=False)
    vendor = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="bills")
    bill_date = models.DateField()
    due_date = models.DateField(null=True, blank=True, editable=False)
    reference = models.CharField(
        max_length=64, blank=True,
        help_text="The vendor's own invoice number — what they will quote when chasing payment.",
    )
    currency = models.ForeignKey(
        Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    exchange_rate = models.DecimalField(
        max_digits=18, decimal_places=8, null=True, blank=True, editable=False,
        help_text="Rate to the base currency captured at posting time.",
    )
    payment_terms = models.ForeignKey(
        PaymentTerms, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
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
        permissions = [("post_bill", "Can post bills and issue debit notes")]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""), name="unique_bill_number"
            )
        ]

    def __str__(self):
        if self.number:
            return f"{self.number} {self.vendor}"
        kind = "DN" if self.debits_id else "BILL"
        return f"{kind}-draft-{self.pk} {self.vendor}"

    def is_debit_note(self):
        return bool(self.debits_id)

    def _rate_for_posting(self):
        """
        The rate this bill converts at, frozen when it posts.

        A bill in a foreign currency that is revalued later would change
        what was already reported; freezing it is what makes the ledger
        reproducible.
        """
        if self.currency_id is None or self.currency.is_base:
            return Decimal("1")
        return self.currency.rate_on(self.bill_date)

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
        if self._state.adding and self.vendor_id:
            self.currency = self.currency or self.vendor.default_currency
            self.payment_terms = self.payment_terms or self.vendor.payment_terms
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.posted:
            raise ValidationError("Posted bills cannot be deleted. Issue a debit note instead.")
        super().delete(*args, **kwargs)

    def _build_journal_entry(self, rate):
        """
        Build the ledger entry in base currency.

        The debits are computed first and the payable is set to their exact
        sum: converting each line independently and rounding can leave the
        two sides a cent apart, which would make a legitimate bill
        unpostable.
        """
        lines = list(self.lines.all())
        if not lines:
            raise ValidationError("Cannot post a bill with no lines.")
        entry = JournalEntry.objects.create(
            date=self.bill_date,
            reference=self.reference or self.number,
            memo=f"Bill {self.number} from {self.vendor}",
        )

        debits = []
        for line in lines:
            debits.append((
                line.posting_account(),
                round_money(line.subtotal() * rate),
                line.description or str(line.item),
            ))

        payable_total = sum(amount for _, amount, _ in debits)
        JournalLine.objects.create(
            entry=entry,
            account=self.payable_account,
            party=self.vendor,
            credit=payable_total,
            description=f"Bill {self.number}",
        )
        for account, amount, description in debits:
            if amount:
                JournalLine.objects.create(
                    entry=entry, account=account, party=self.vendor,
                    debit=amount, description=description,
                )
        return entry

    @transaction.atomic
    def post(self, memo=None):
        if self.posted:
            raise ValidationError("This bill is already posted.")

        self.bill_date = to_date(self.bill_date)
        if not self.is_debit_note() and self.total() <= 0:
            raise ValidationError(
                "This bill has no value to post. Give its lines a quantity and price."
            )
        if not self.number:
            if self.is_debit_note():
                self.number = DocumentSequence.next_for(
                    "purchasing.debit_note", self.bill_date, name="Debit Notes", prefix="DN-"
                )
            else:
                self.number = DocumentSequence.next_for(
                    "purchasing.bill", self.bill_date, name="Vendor Bills", prefix="BILL-"
                )

        self.exchange_rate = self._rate_for_posting()
        self.due_date = (
            self.payment_terms.due_date(self.bill_date)
            if self.payment_terms_id else self.bill_date
        )

        if self.is_debit_note():
            original = self.debits
            if not original.posted or not original.journal_entry_id:
                raise ValidationError("Cannot post a debit note against an unposted bill.")
            # Debit at the rate the bill was booked at, never today's, so
            # correcting an old foreign-currency bill can't book an FX gain.
            self.exchange_rate = original.exchange_rate or Decimal("1")
            entry = original.journal_entry.create_reversal(
                entry_date=self.bill_date,
                memo=memo or f"Debit note {self.number} for {original.number}",
            )
        else:
            entry = self._build_journal_entry(self.exchange_rate)
            entry.post()

        self.journal_entry = entry
        self.posted = True
        self.posted_at = timezone.now()
        super(Bill, self).save(update_fields=[
            "number", "bill_date", "due_date", "exchange_rate", "journal_entry",
            "posted", "posted_at", "updated_at",
        ])

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
            currency=self.currency,
            payment_terms=self.payment_terms,
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

    def posting_account(self):
        """
        Stocked goods were already capitalised into Inventory when they were
        received, so the bill clears that accrual rather than expensing the
        cost a second time. Services and non-stocked lines expense directly.
        """
        if self.item_id and self.item.track_inventory:
            from apps.core.models import Company

            grni = Company.get().grni_account
            if grni is not None:
                return grni
        return self.expense_account

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

    number = models.CharField(max_length=32, blank=True, editable=False)
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
        permissions = [("post_goodsreceipt", "Can post goods receipts and returns")]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""), name="unique_goods_receipt_number"
            )
        ]

    def __str__(self):
        if self.number:
            return f"{self.number} for {self.purchase_order}"
        kind = "RETURN" if self.reverses_id else "GR"
        return f"{kind}-draft-{self.pk} for {self.purchase_order}"

    def is_return(self):
        return bool(self.reverses_id)

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

        self.receipt_date = to_date(self.receipt_date)
        if not is_return and self.purchase_order.status != OrderStatus.CONFIRMED:
            # Receiving against a draft order books stock and a GRNI
            # liability for goods nobody agreed to buy; against a cancelled
            # one, for goods that were called off.
            raise ValidationError(
                f"{self.purchase_order} is {self.purchase_order.get_status_display().lower()}; "
                "confirm it before receiving goods against it."
            )
        if not self.number:
            self.number = (
                DocumentSequence.next_for(
                    "purchasing.receipt_return", self.receipt_date,
                    name="Purchase Returns", prefix="PRTN-",
                )
                if is_return
                else DocumentSequence.next_for(
                    "purchasing.receipt", self.receipt_date,
                    name="Goods Receipts", prefix="GRN-",
                )
            )

        if not is_return:
            for line in lines:
                already_received = line.order_line.quantity_received()
                if already_received + line.quantity_received > line.order_line.quantity:
                    raise ValidationError(
                        f"Receiving {line.quantity_received} of {line.order_line.item} would "
                        f"exceed the ordered quantity ({line.order_line.quantity}; "
                        f"{already_received} already received)."
                    )

        valued = []
        for line in lines:
            # Services and non-stocked items must never touch stock levels.
            if not line.order_line.item.track_inventory:
                continue
            movement_type = MovementType.ISSUE if is_return else MovementType.RECEIPT
            quantity = -line.quantity_received if is_return else line.quantity_received
            unit_cost = line.order_line.unit_price
            StockMovement.objects.create(
                item=line.order_line.item,
                warehouse=line.warehouse,
                movement_type=movement_type,
                quantity=quantity,
                unit_cost=unit_cost,
                reference=self.reference or self.number,
                occurred_at=timezone.now(),
                notes=(
                    f"{'Return for' if is_return else 'Receipt for'} "
                    f"{self.purchase_order} ({self.number})"
                ),
            )
            valued.append((line.order_line.item, line.quantity_received * unit_cost))

        post_inventory_entry(
            valued,
            date=self.receipt_date,
            reference=self.reference or self.number,
            memo=f"{'Return to vendor for' if is_return else 'Goods received for'} {self.purchase_order}",
            direction="in",
            reverse=is_return,
        )

        self.posted = True
        self.posted_at = timezone.now()
        super(GoodsReceipt, self).save(
            update_fields=["number", "receipt_date", "posted", "posted_at", "updated_at"]
        )

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
