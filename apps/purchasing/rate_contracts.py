"""
Polymer bought on circulars (L16): the mirror of the sales price clause (apps/sales/price_variation.py).

A rate contract with a polymer supplier names the items, the quantity and the period, and prices on
an index, the polymer maker's circular for the grade, at the index less an agreed discount a
kilogramme. An order line on the contract takes the index in force on the order's date, kept on the
line, and the receipt is costed at the order's price as any other.

The makers revise a circular back-dated. What was already received was then bought at another price,
and the supplier sends a rate-difference note for a window: a rise as a supplementary bill, a fall as
a credit note, which the books meet with a debit note against the supplier's latest bill. Entered here
against the contract, it is worked receipt line by receipt line: the index now in force on its order's
date against the one the line kept, by the one arithmetic the sales clause uses (variation_per_unit),
times the kilogrammes received. Each document is drafted for review, taxed as the order line was, with
the supplier's note number as its reference.

When that document posts, the value lands on the receipts it varied through land_on_receipts (quantity
basis, one line at a time): shelf, run or cost of sales, by what is left of each; the share of goods
returned to the supplier stays in purchase price variance, where the note's document put it. A receipt
line is varied once, its index kept on the note; a second note for the same window finds nothing. A
draft is cancelled, which frees its receipts; once its document is posted, it is corrected like any
bill.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.accounting.models import Account, round_money
from apps.core.models import (
    AuditModel, Company, DocumentSequence, Party, lock_rows, numbered_in_one_transaction, numbering_first,
    serialised, takes_back, to_date,
)

ZERO = Decimal("0")


def kg_per_unit(item, uom):
    """Kilogrammes in one `uom` of `item`, through its own unit conversion."""
    from apps.manufacturing.daily import kilogramme

    kg = kilogramme()
    if kg is None:
        raise ValidationError("There is no kilogramme unit to price polymer in.")
    return item.to_stock_quantity(Decimal("1"), uom) / item.unit_factor(kg)


class PurchaseRateContract(AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    vendor = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="rate_contracts")
    index = models.ForeignKey("sales.PriceIndex", on_delete=models.PROTECT, related_name="purchase_contracts",
                              help_text="The maker's circular the price follows.")
    discount_per_kg = models.DecimalField(max_digits=12, decimal_places=4, default=ZERO,
                                          help_text="Agreed off the index, a kilogramme.")
    valid_from = models.DateField()
    valid_to = models.DateField()
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-valid_from", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""), name="rate_contract_number_unique"),
            models.CheckConstraint(check=Q(valid_to__gte=models.F("valid_from")), name="rate_contract_period_forward"),
            models.CheckConstraint(check=Q(discount_per_kg__gte=0), name="rate_contract_discount_not_negative"),
        ]

    def __str__(self):
        return self.number or f"Rate contract {self.pk}"

    @numbered_in_one_transaction
    def save(self, *args, **kwargs):
        from .models import _require_vendor_role

        if not self._state.adding and self.order_lines.exists():
            stored = PurchaseRateContract.objects.get(pk=self.pk)
            if (stored.vendor_id, stored.index_id, stored.discount_per_kg, stored.valid_from) != (
                    self.vendor_id, self.index_id, self.discount_per_kg, to_date(self.valid_from)):
                raise ValidationError(f"{stored} has been ordered on; its terms are as agreed.")
            if to_date(self.valid_to) < max(line.order.order_date for line in stored.order_lines.select_related("order")):
                raise ValidationError({"valid_to": "Orders stand on it after that day."})
        _require_vendor_role(self.vendor)
        if not self.number:
            self.number = DocumentSequence.next_for("purchasing.rate_contract", to_date(self.valid_from),
                                                    name="Purchase rate contracts", prefix="PRC-")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.order_lines.exists():
            raise ValidationError(f"{self} has been ordered on; it stays.")
        self.lines.all().delete()
        return super().delete(*args, **kwargs)


class RateContractLine(AuditModel):
    contract = models.ForeignKey(PurchaseRateContract, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    quantity_kg = models.DecimalField(max_digits=18, decimal_places=3)

    class Meta:
        ordering = ["contract", "id"]
        constraints = [
            models.UniqueConstraint(fields=["contract", "item"], name="one_rate_contract_line_per_item"),
            models.CheckConstraint(check=Q(quantity_kg__gt=0), name="rate_contract_quantity_positive"),
        ]

    def __str__(self):
        return f"{self.contract} {self.item} {self.quantity_kg} kg"

    def ordered_kg(self, but=None):
        from .models import PurchaseOrderLine

        lines = PurchaseOrderLine.objects.filter(rate_contract=self.contract, item=self.item).select_related("uom", "item")
        if but is not None and but.pk:
            lines = lines.exclude(pk=but.pk)
        return sum((line.quantity * kg_per_unit(line.item, line.uom) for line in lines), ZERO)

    def save(self, *args, **kwargs):
        if not self._state.adding and self.contract.order_lines.filter(item_id=self.item_id).exists():
            stored = RateContractLine.objects.get(pk=self.pk)
            if stored.item_id != self.item_id or self.quantity_kg < self.ordered_kg():
                raise ValidationError({"quantity_kg": "More than that is ordered on it already."})
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.contract.order_lines.filter(item_id=self.item_id).exists():
            raise ValidationError(f"{self} has been ordered on; it stays.")
        return super().delete(*args, **kwargs)


def contract_for(order, item):
    """The supplier's rate contract for `item` standing on the order's day, if one is."""
    day = to_date(order.order_date)
    return PurchaseRateContract.objects.filter(vendor_id=order.vendor_id, valid_from__lte=day, valid_to__gte=day,
                                               lines__item=item).order_by("-valid_from", "-pk").first()


def price_on_contract(order_line):
    """
    An order line on a rate contract: the contract's vendor, item, period and quantity, and its price,
    the index on the order's date less the discount, for each order unit's kilogrammes. The index value
    is kept on the line once taken; later saves keep it.
    """
    contract = order_line.rate_contract
    order = order_line.order
    lock_rows(contract)
    if contract.vendor_id != order.vendor_id:
        raise ValidationError({"rate_contract": f"{contract} is with {contract.vendor}, not {order.vendor}."})
    day = to_date(order.order_date)
    if not (contract.valid_from <= day <= contract.valid_to):
        raise ValidationError({"rate_contract": f"{contract} runs {contract.valid_from} to {contract.valid_to}; "
                                                f"the order is dated {day}."})
    line = contract.lines.filter(item_id=order_line.item_id).first()
    if line is None:
        raise ValidationError({"rate_contract": f"{order_line.item} is not on {contract}."})
    per_unit = kg_per_unit(order_line.item, order_line.uom)
    if line.ordered_kg(but=order_line) + order_line.quantity * per_unit > line.quantity_kg:
        left = line.quantity_kg - line.ordered_kg(but=order_line)
        raise ValidationError({"quantity": f"{contract} has {format(left.normalize(), 'f')} kg of "
                                           f"{order_line.item} left to order."})
    if order_line.contract_index_value is None:
        order_line.contract_index_value = contract.index.value_on(day)
    # The price column keeps the paisa; the index taken is kept to four places beside it.
    order_line.unit_price = round_money((order_line.contract_index_value - contract.discount_per_kg) * per_unit)


class NoteStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    LANDED = "landed", "Landed"
    CANCELLED = "cancelled", "Cancelled"


class RateDifferenceNote(AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    contract = models.ForeignKey(PurchaseRateContract, on_delete=models.PROTECT, related_name="rate_differences")
    start = models.DateField(help_text="Receipts on or after this day are varied.")
    end = models.DateField(help_text="…and on or before this day.")
    note_date = models.DateField(default=timezone.localdate)
    supplier_reference = models.CharField(max_length=64,
                                          help_text="The supplier's supplementary invoice or credit note number.")
    supplier_note_date = models.DateField(null=True, blank=True)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", editable=False,
                                help_text="Where the difference sits until it lands: purchase price variance.")
    status = models.CharField(max_length=12, choices=NoteStatus.choices, default=NoteStatus.DRAFT, editable=False)
    bill = models.OneToOneField("purchasing.Bill", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="rate_difference_raised", editable=False,
                                help_text="The supplementary bill, where the circular rose.")
    debit_note = models.OneToOneField("purchasing.Bill", null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="rate_difference_credited", editable=False,
                                      help_text="The debit note, where the circular fell.")
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-note_date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""), name="rate_difference_number_unique"),
            models.CheckConstraint(check=Q(end__gte=models.F("start")), name="rate_difference_window_forward"),
        ]

    def __str__(self):
        return self.number or f"Rate difference {self.pk}"

    def total(self):
        return (self.lines.aggregate(total=Sum("amount"))["total"] or ZERO).quantize(Decimal("0.01"))

    def documents(self):
        return [doc for doc in (self.bill, self.debit_note) if doc is not None]

    def rows(self):
        """What the note would vary: each receipt line on the contract in the window not varied already."""
        from .models import GoodsReceiptLine

        rows = []
        lines = GoodsReceiptLine.objects.filter(
            order_line__rate_contract=self.contract, receipt__posted=True, receipt__reverses__isnull=True,
            receipt__receipt_date__gte=self.start, receipt__receipt_date__lte=self.end,
        ).select_related("receipt", "order_line__order", "order_line__item", "order_line__uom").order_by(
            "receipt__receipt_date", "pk")
        from apps.sales.price_variation import variation_per_unit, varied_amount

        for line in lines:
            if RateDifferenceLine.objects.filter(receipt_line=line, note__cancelled_at__isnull=True).exists():
                continue
            order_line = line.order_line
            was = order_line.contract_index_value
            now = self.contract.index.value_on(order_line.order.order_date)
            per_kg = variation_per_unit(was, now, Decimal("1"))
            if not per_kg:
                continue
            kg = (line.quantity_received * kg_per_unit(order_line.item, order_line.uom)).quantize(Decimal("0.001"))
            rows.append({"receipt_line": line, "order_line": order_line, "quantity_kg": kg, "index_was": was,
                         "index_now": now, "per_kg": per_kg, "amount": varied_amount(kg, per_kg)})
        return rows

    @numbered_in_one_transaction
    def save(self, *args, **kwargs):
        if not self._state.adding:
            if not kwargs.get("update_fields"):
                raise ValidationError(f"{self} stands as raised: cancel it and raise another.")
            super().save(*args, **kwargs)
            return
        numbering_first()
        lock_rows(self.contract)
        self.start, self.end, self.note_date = to_date(self.start), to_date(self.end), to_date(self.note_date)
        if self.end < self.start:
            raise ValidationError({"end": "The window runs backwards."})
        if self.note_date > timezone.localdate():
            raise ValidationError({"note_date": "That day has not come."})
        if not (self.supplier_reference or "").strip():
            raise ValidationError({"supplier_reference": "Give the supplier's note number."})
        account = Company.get().purchase_price_variance_account
        if account is None:
            raise ValidationError("The company has no purchase price variance account to hold the difference.")
        self.account = account
        rows = self.rows()
        if not rows:
            raise ValidationError(f"Nothing received on {self.contract} between {self.start} and {self.end} "
                                  "is left to vary: it is varied already, or the circular stands where it was.")
        if self.note_date < max(row["receipt_line"].receipt.receipt_date for row in rows):
            raise ValidationError({"note_date": "The note is dated after the goods it varies came."})
        self.number = DocumentSequence.next_for("purchasing.rate_difference", self.note_date,
                                                name="Rate-difference notes", prefix="RDN-")
        super().save(*args, **kwargs)
        for row in rows:
            RateDifferenceLine.objects.create(note=self, **row)
        self._draft(rows)

    def _draft(self, rows):
        """The supplementary bill for what rose, the debit note for what fell, each taxed as its order line."""
        from collections import defaultdict

        from .models import Bill, BillLine

        net = defaultdict(lambda: ZERO)
        for row in rows:
            net[row["order_line"]] += row["amount"]
        vendor = self.contract.vendor
        for sign in (1, -1):
            lines = [(line, amount) for line, amount in net.items() if amount * sign > 0]
            if not lines:
                continue
            credits = None
            if sign < 0:
                credits = Bill.objects.filter(vendor=vendor, posted=True, debits__isnull=True, is_prepayment=False,
                                              lines__order_line__rate_contract=self.contract).order_by(
                    "-bill_date", "-id").first()
                if credits is None:
                    raise ValidationError(f"The circular fell, but {vendor} has billed nothing on {self.contract} "
                                          "to debit.")
            payable = (credits.payable_account if credits else
                       Bill.objects.filter(vendor=vendor, posted=True).order_by("-bill_date").values_list(
                           "payable_account", flat=True).first())
            payable = payable or Company.get().default_payable_account_id
            document = Bill.objects.create(
                created_by=self.created_by, updated_by=self.updated_by, vendor=vendor, bill_date=self.note_date,
                reference=(credits.reference if credits else self.supplier_reference)[:64],
                payable_account_id=payable.pk if hasattr(payable, "pk") else payable,
                debits=credits,
                supplier_note_number=self.supplier_reference if credits else "",
                supplier_note_date=self.supplier_note_date if credits else None,
                currency=lines[0][0].order.currency)
            for order_line, amount in lines:
                line = BillLine.objects.create(
                    bill=document, quantity=Decimal("1"), unit_price=abs(amount), expense_account=self.account,
                    description=(f"Polymer rate difference, {order_line.item}, {self.start:%d.%m.%Y} to "
                                 f"{self.end:%d.%m.%Y} ({self.number}, {self.supplier_reference})")[:255])
                line.taxes.set(order_line.taxes.all())
            if sign > 0:
                self.bill = document
            else:
                self.debit_note = document
        super().save(update_fields=["bill", "debit_note", "updated_at"])

    def delete(self, *args, **kwargs):
        raise ValidationError("A rate-difference note is cancelled, never deleted.")

    @serialised("status", "cancelled_at")
    @takes_back
    def cancel(self):
        """Only while its documents are drafts; frees its receipts for the next note."""
        if self.status != NoteStatus.DRAFT:
            raise ValidationError(f"{self} is {self.get_status_display().lower()}.")
        documents = self.documents()
        lock_rows(*documents)
        if any(document.posted for document in documents):
            raise ValidationError(f"{self}'s document is posted; correct it with a debit note.")
        self.cancelled_at = timezone.now()
        self.status = NoteStatus.CANCELLED
        self.bill = self.debit_note = None
        super().save(update_fields=["status", "cancelled_at", "bill", "debit_note", "updated_at"])
        for document in documents:
            document.delete()

    @transaction.atomic
    @serialised("status")
    def land(self, document):
        """Its document posted: each receipt line takes its amount, quantity basis, where its goods are now."""
        from .models import land_on_receipts

        if self.status != NoteStatus.DRAFT:
            return
        sign = Decimal("1") if document.pk == self.bill_id else Decimal("-1")
        # The document says what the supplier's note says, or nothing lands: a draft bill cut from 45,000.00 to
        # 15,000.00 still landed 45,000.00 on the goods and left 30,000.00 on the holding account for good.
        wanted = sum((abs(line.amount) for line in self.lines.all() if line.amount * sign > 0), Decimal("0"))
        if document.subtotal() != wanted:
            raise ValidationError(
                f"{document} says {document.subtotal()} and {self} says {wanted}: a rate-difference note's "
                "document is not edited. Cancel the note and draft it again from the supplier's figures.")
        for line in self.lines.select_related("receipt_line__receipt", "receipt_line__order_line__item").order_by("pk"):
            if line.amount * sign <= 0:
                continue
            land_on_receipts([line.receipt_line], line.amount, "quantity", document.bill_date, reference=self.number,
                             account=self.account, source={"rate_difference": self})
        if all(doc.posted for doc in self.documents()):
            self.status = NoteStatus.LANDED
            super().save(update_fields=["status", "updated_at"])


class RateDifferenceLine(AuditModel):
    note = models.ForeignKey(RateDifferenceNote, on_delete=models.PROTECT, related_name="lines")
    receipt_line = models.ForeignKey("purchasing.GoodsReceiptLine", on_delete=models.PROTECT, related_name="+")
    order_line = models.ForeignKey("purchasing.PurchaseOrderLine", on_delete=models.PROTECT, related_name="+")
    quantity_kg = models.DecimalField(max_digits=18, decimal_places=3)
    index_was = models.DecimalField(max_digits=12, decimal_places=4, help_text="What the order line was priced at.")
    index_now = models.DecimalField(max_digits=12, decimal_places=4, help_text="The circular as revised.")
    per_kg = models.DecimalField(max_digits=14, decimal_places=6)
    amount = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        ordering = ["note", "id"]
        constraints = [
            models.CheckConstraint(check=~Q(amount=0) & Q(quantity_kg__gt=0), name="rate_difference_line_moves"),
        ]

    def __str__(self):
        return f"{self.note} {self.receipt_line}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(f"{self.note} stands as raised.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(f"{self.note} is cancelled, never cut.")


def _land_rate_differences(bill):
    """Bill.post's follow-up: a rate-difference note's document posted lands its value (AFTER_BILL_POSTED)."""
    for note in RateDifferenceNote.objects.filter(Q(bill=bill) | Q(debit_note=bill)):
        note.land(bill)
