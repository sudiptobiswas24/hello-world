"""
The polymer price variation clause: a sack priced on polymer at one
figure, billed up or down as a published polymer price moves.

A woven-sack price is mostly polymer, and a customer on a long order
agrees a clause rather than a new price every fortnight: the price
stands on an index at a base value, and when the index moves by more
than the agreed threshold, every sack dispatched carries the move times
the polymer in it.

**The index is dated, never edited.** A value is what was published
from its date; a correction is a new row. A delivery is varied at the
index in force on the day it was dispatched.

**The clause is on the order line**, where the price is: its index, the
base value the price stood on, the polymer kilogrammes in one unit, the
share of the move passed on, and the threshold inside which nothing
moves. Symmetric: a fall is credited as a rise is billed.

**Billed per period, delivery by delivery, once.** A variation bill
takes an order's dispatches in a window not already on a bill, and
freezes, for each, the index it used, the variation per unit and the
amount. A return is varied back at the index its delivery used, so
sending sacks back returns exactly what they were charged. The net of
each order line is billed on a supplementary invoice, or — where the
index fell — credited on a credit note against the order's latest
invoice; both are drafted for review, taxed as the order line is.

A bill is cancelled only while its documents are drafts, which frees
its deliveries for the next one. Once posted, it is corrected like any
invoice: by a credit note.
"""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounting.models import round_money
from apps.core.models import AuditModel, DocumentSequence, to_date

ZERO = Decimal("0")
HUNDRED = Decimal("100")
PER_UNIT = Decimal("0.000001")


class PriceIndex(AuditModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code

    def value_on(self, on_date):
        row = self.values.filter(valid_from__lte=to_date(on_date)).order_by("-valid_from").first()
        if row is None:
            raise ValidationError(f"{self} has no value published on or before {on_date}.")
        return row.value


class PriceIndexValue(AuditModel):
    index = models.ForeignKey(PriceIndex, on_delete=models.PROTECT, related_name="values")
    valid_from = models.DateField()
    value = models.DecimalField(max_digits=12, decimal_places=4,
                                help_text="Per kilogramme of polymer.")

    class Meta:
        ordering = ["index", "-valid_from"]
        constraints = [
            models.UniqueConstraint(fields=["index", "valid_from"],
                                    name="one_index_value_a_day"),
            models.CheckConstraint(check=Q(value__gt=0), name="index_value_positive"),
        ]

    def __str__(self):
        return f"{self.index} {self.value} from {self.valid_from}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("A published value is what it was; enter a new one from "
                                  "the date it changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if PriceVariationLine.objects.filter(index=self.index,
                                             delivery__delivery_date__gte=self.valid_from).exists():
            raise ValidationError(f"{self} has been billed on; it stays.")
        return super().delete(*args, **kwargs)


class PriceVariationClause(AuditModel):
    order_line = models.OneToOneField("sales.SalesOrderLine", on_delete=models.PROTECT,
                                      related_name="price_clause")
    index = models.ForeignKey(PriceIndex, on_delete=models.PROTECT, related_name="clauses")
    base_value = models.DecimalField(
        max_digits=12, decimal_places=4,
        help_text="The index value the order's price stood on.")
    polymer_kg_per_unit = models.DecimalField(
        max_digits=12, decimal_places=6,
        help_text="Polymer in one unit of the line — kilogrammes a sack.")
    pass_through_percent = models.DecimalField(max_digits=6, decimal_places=2,
                                               default=Decimal("100"))
    threshold_percent = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("0"),
        help_text="A move of the index within this share of the base changes nothing.")

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(base_value__gt=0) & Q(polymer_kg_per_unit__gt=0)
                & Q(pass_through_percent__gt=0) & Q(pass_through_percent__lte=100)
                & Q(threshold_percent__gte=0),
                name="price_clause_figures_sensible"),
        ]

    def __str__(self):
        return f"{self.index} clause on {self.order_line}"

    def save(self, *args, **kwargs):
        if PriceVariationLine.objects.filter(order_line_id=self.order_line_id).exists():
            raise ValidationError(f"{self} has been billed on; its terms are as agreed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if PriceVariationLine.objects.filter(order_line_id=self.order_line_id).exists():
            raise ValidationError(f"{self} has been billed on; it stays.")
        return super().delete(*args, **kwargs)

    def variation_per_unit(self, index_value):
        """What one unit moves by at this index value: nothing inside the threshold."""
        move = index_value - self.base_value
        if abs(move) <= self.base_value * self.threshold_percent / HUNDRED:
            return ZERO
        return (move * self.polymer_kg_per_unit * self.pass_through_percent
                / HUNDRED).quantize(PER_UNIT)


class PriceVariationBill(AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    order = models.ForeignKey("sales.SalesOrder", on_delete=models.PROTECT,
                              related_name="price_variation_bills")
    start = models.DateField()
    end = models.DateField()
    invoice = models.OneToOneField("sales.Invoice", null=True, blank=True,
                                   on_delete=models.PROTECT, related_name="+", editable=False)
    credit_note = models.OneToOneField("sales.Invoice", null=True, blank=True,
                                       on_delete=models.PROTECT, related_name="+", editable=False)
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-end", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""),
                                    name="price_variation_bill_number_unique"),
        ]

    def __str__(self):
        return self.number

    def total(self):
        return sum((line.amount for line in self.lines.all()), ZERO)

    @transaction.atomic
    def cancel(self):
        """Only while its documents are drafts; frees its deliveries."""
        if self.cancelled_at is not None:
            raise ValidationError(f"{self} is already cancelled.")
        documents = [doc for doc in (self.invoice, self.credit_note) if doc is not None]
        if any(doc.posted for doc in documents):
            raise ValidationError(f"{self} has been issued; correct it with a credit note.")
        self.cancelled_at = timezone.now()
        self.invoice = self.credit_note = None
        self.save(update_fields=["cancelled_at", "invoice", "credit_note", "updated_at"])
        for doc in documents:
            doc.delete()


class PriceVariationLine(AuditModel):
    bill = models.ForeignKey(PriceVariationBill, on_delete=models.CASCADE, related_name="lines")
    delivery = models.ForeignKey("sales.Delivery", on_delete=models.PROTECT, related_name="+")
    delivery_line = models.ForeignKey("sales.DeliveryLine", on_delete=models.PROTECT,
                                      related_name="+")
    order_line = models.ForeignKey("sales.SalesOrderLine", on_delete=models.PROTECT,
                                   related_name="+")
    index = models.ForeignKey(PriceIndex, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=18, decimal_places=4,
                                   help_text="Negative for a return.")
    index_value = models.DecimalField(max_digits=12, decimal_places=4)
    variation_per_unit = models.DecimalField(max_digits=14, decimal_places=6)
    amount = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        ordering = ["bill", "id"]
        constraints = [
            # Signed on purpose - a return is negative, a fall is negative -
            # but the amount's sign is the quantity's times the variation's:
            # a return billed the wrong way round is refused here.
            models.CheckConstraint(
                check=~Q(quantity=0) & (
                    Q(amount=0)
                    | (Q(quantity__gt=0) & Q(variation_per_unit__gt=0) & Q(amount__gt=0))
                    | (Q(quantity__gt=0) & Q(variation_per_unit__lt=0) & Q(amount__lt=0))
                    | (Q(quantity__lt=0) & Q(variation_per_unit__gt=0) & Q(amount__lt=0))
                    | (Q(quantity__lt=0) & Q(variation_per_unit__lt=0) & Q(amount__gt=0))),
                name="price_variation_amount_signed_as_quantity_times_rate"),
        ]

    def __str__(self):
        return f"{self.delivery} {self.quantity} x {self.variation_per_unit}"


def _billed(delivery_line):
    """The standing variation row of a delivery line, if it was billed."""
    return PriceVariationLine.objects.filter(
        delivery_line=delivery_line, bill__cancelled_at__isnull=True).first()


def rows_for(order, start, end):
    """What a bill for this window would say, row by row: nothing written."""
    from .models import DeliveryLine

    start, end = to_date(start), to_date(end)
    if end < start:
        raise ValidationError(f"A window from {start} to {end} runs backwards.")
    rows = []
    lines = DeliveryLine.objects.filter(
        delivery__sales_order=order, delivery__posted=True,
        delivery__delivery_date__gte=start, delivery__delivery_date__lte=end,
        order_line__price_clause__isnull=False,
    ).select_related("delivery", "order_line__price_clause__index",
                     "reverses_line__delivery").order_by("delivery__delivery_date", "id")
    for line in lines:
        if _billed(line) is not None:
            continue
        clause = line.order_line.price_clause
        if line.reverses_line_id is None:
            value = clause.index.value_on(line.delivery.delivery_date)
            per_unit = clause.variation_per_unit(value)
            quantity = line.quantity_shipped
        else:
            # Back at what its delivery was charged, billed or not yet.
            original = _billed(line.reverses_line)
            if original is not None:
                value, per_unit = original.index_value, original.variation_per_unit
            else:
                value = clause.index.value_on(line.reverses_line.delivery.delivery_date)
                per_unit = clause.variation_per_unit(value)
            quantity = -line.quantity_shipped
        rows.append({
            "delivery": line.delivery, "delivery_line": line, "order_line": line.order_line,
            "index": clause.index, "quantity": quantity, "index_value": value,
            "variation_per_unit": per_unit, "amount": round_money(quantity * per_unit),
        })
    return rows


@transaction.atomic
def bill_variation(order, start, end, receivable_account, invoice_date=None):
    """Write a bill for the window, and draft its invoice and credit note."""
    from .models import Invoice, InvoiceLine, SalesOrder

    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    rows = rows_for(order, start, end)
    if not rows:
        raise ValidationError(f"Nothing dispatched on {order} between {start} and {end} "
                              "under a price clause is left to vary.")
    invoice_date = to_date(invoice_date) or timezone.localdate()
    bill = PriceVariationBill.objects.create(
        order=order, start=to_date(start), end=to_date(end),
        number=DocumentSequence.next_for("sales.price_variation", invoice_date,
                                         name="Price variation bills", prefix="PV-"))
    net = defaultdict(lambda: ZERO)
    for row in rows:
        PriceVariationLine.objects.create(bill=bill, **row)
        net[row["order_line"]] += row["amount"]

    def draft(credits=None):
        return Invoice.objects.create(
            customer=order.customer, invoice_date=invoice_date,
            reference=f"{bill.number} {order.number}"[:64], sales_order=order,
            receivable_account=receivable_account, currency=order.currency,
            payment_terms=order.payment_terms, billing_address=order.billing_address,
            shipping_address=order.shipping_address, sales_rep=order.sales_rep,
            credits=credits)

    for sign in (1, -1):
        lines = [(line, amount) for line, amount in net.items() if amount * sign > 0]
        if not lines:
            continue
        credits = None
        if sign < 0:
            credits = order.invoices.filter(posted=True, credits__isnull=True,
                                            is_down_payment=False).order_by(
                "-invoice_date", "-id").first()
            if credits is None:
                raise ValidationError(f"The polymer price fell, but nothing on {order} has "
                                      "been invoiced yet to credit it against.")
        document = draft(credits)
        for line, amount in lines:
            row = InvoiceLine.objects.create(
                invoice=document, quantity=Decimal("1"), unit_price=abs(amount),
                description=(f"Polymer price variation, {line.label()}, "
                             f"{bill.start} to {bill.end} ({bill.number})")[:255],
                revenue_account=line.revenue_account)
            row.taxes.set(line.taxes.all())
        if sign > 0:
            bill.invoice = document
        else:
            bill.credit_note = document
    bill.save(update_fields=["invoice", "credit_note", "updated_at"])
    return bill
