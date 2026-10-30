"""
A claim for goods short on the weighbridge (L17).

The receipt compares the weighbridge's net with the invoice or challan weight (GoodsReceipt.
weight_comparison). A shortage beyond the item's tolerance is a claim: against the supplier or the
transporter, chosen on the claim, for a quantity no more than the shortage, valued at the receipt's
own cost (what it booked per stocking unit, total first and divided last), frozen as it is raised.

The receipt stays as posted; the claim is settled through the books:

- against the supplier, by a debit note on the bill that billed the goods: their invoice said more
  than came, so the bill took more into goods-received-not-invoiced than the receipt put there, and
  the note takes it back out (Bill.create_debit_note, which keeps every rule a debit note has);
- against the transporter, by a recovery: the claims-recoverable account (with the transporter on the
  line) against goods received not invoiced, at the claim's value.

Either needs the supplier to have billed the short goods: until then nothing was paid for what did
not come, and a settlement would leave the accrual in credit. A withdrawn claim settles nothing; a
recovery withdrawn is reversed, dated by correction_date.
"""

from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone

from apps.accounting.models import JournalEntry, JournalLine, round_money
from apps.core.models import (
    AuditModel, Company, DocumentSequence, Party, correction_date, lock_rows, numbered_in_one_transaction,
    serialised, takes_back, to_date,
)


class ClaimAgainst(models.TextChoices):
    SUPPLIER = "supplier", "Supplier"
    TRANSPORTER = "transporter", "Transporter"


class ClaimStatus(models.TextChoices):
    OPEN = "open", "Open"
    SETTLED = "settled", "Settled"
    WITHDRAWN = "withdrawn", "Withdrawn"


class WeightClaim(AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    receipt = models.ForeignKey("purchasing.GoodsReceipt", on_delete=models.PROTECT, related_name="weight_claims")
    against = models.CharField(max_length=12, choices=ClaimAgainst.choices)
    transporter = models.ForeignKey(Party, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    raised_on = models.DateField(default=timezone.localdate)
    quantity = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True,
        help_text="In the item's stocking unit; blank claims the whole shortage not claimed already.")
    value = models.DecimalField(max_digits=18, decimal_places=2, editable=False, default=Decimal("0"),
                                help_text="At the receipt's own cost, frozen as the claim is raised.")
    note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=12, choices=ClaimStatus.choices, default=ClaimStatus.OPEN, editable=False)
    debit_note = models.ForeignKey("purchasing.Bill", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="weight_claims", editable=False)
    recovery_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="+", editable=False)
    recovery_reversal = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
                                          related_name="+", editable=False)
    settled_on = models.DateField(null=True, blank=True, editable=False)
    withdrawn_on = models.DateField(null=True, blank=True, editable=False)
    withdraw_reason = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["-raised_on", "-id"]
        permissions = [("settle_weightclaim", "Can settle a weight claim by debit note or recovery")]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""), name="weight_claim_number_unique"),
            models.CheckConstraint(check=Q(quantity__gt=0) | Q(quantity__isnull=True),
                                   name="weight_claim_quantity_positive"),
            models.CheckConstraint(check=Q(value__gte=0), name="weight_claim_value_not_negative"),
        ]

    def __str__(self):
        return self.number or f"Weight claim {self.pk}"

    def locked_before_it(self):
        from .models import PurchaseOrder

        return [PurchaseOrder(pk=self.receipt.purchase_order_id)]

    def _lines(self):
        return list(self.receipt.lines.select_related("order_line__item", "order_line__uom").order_by("pk"))

    def claimed_elsewhere(self):
        return (WeightClaim.objects.filter(receipt_id=self.receipt_id)
                .exclude(status=ClaimStatus.WITHDRAWN).exclude(pk=self.pk)
                .aggregate(total=Sum("quantity"))["total"] or Decimal("0"))

    @numbered_in_one_transaction
    def save(self, *args, **kwargs):
        if not self._state.adding:
            stored = WeightClaim.objects.filter(pk=self.pk).first()
            updating = set(kwargs.get("update_fields") or ())
            steps = {"status", "debit_note", "recovery_entry", "recovery_reversal", "settled_on", "withdrawn_on",
                     "withdraw_reason", "updated_at"}
            if stored is not None and not (updating and updating <= steps):
                if stored.status != ClaimStatus.OPEN or (stored.receipt_id, stored.quantity, stored.against) != (
                        self.receipt_id, self.quantity, self.against):
                    raise ValidationError(f"{stored.number} is raised as it stands: withdraw it and raise another.")
            super().save(*args, **kwargs)
            return
        # Raised: asked here, where the API's create reaches, under the order's lock (two claims at once).
        from .models import PurchaseOrder

        receipt = self.receipt
        lock_rows(PurchaseOrder(pk=receipt.purchase_order_id), receipt)
        if not receipt.posted or receipt.reverses_id:
            raise ValidationError("A claim is raised on a posted receipt of goods in.")
        if self.against == ClaimAgainst.TRANSPORTER and self.transporter_id is None:
            raise ValidationError({"transporter": "Say which transporter the claim is against."})
        if self.against == ClaimAgainst.SUPPLIER:
            self.transporter = None
        comparison = receipt.weight_comparison()
        if comparison is None or comparison["shortage"] is None:
            raise ValidationError("The receipt was not weighed against an invoice or challan weight.")
        if not comparison["beyond_tolerance"]:
            raise ValidationError(
                f"{receipt.number} is short {format(comparison['shortage'].normalize(), 'f')} {comparison['unit']}, "
                f"within the item's {comparison['tolerance_percent'].normalize():f}% "
                f"({format(comparison['allowance'].normalize(), 'f')} {comparison['unit']}): no claim.")
        lines = self._lines()
        places = Decimal("0.0001")
        left = (comparison["shortage"] - self.claimed_elsewhere()).quantize(places, rounding=ROUND_HALF_UP)
        quantity = left if self.quantity is None else Decimal(str(self.quantity))
        if left <= 0:
            raise ValidationError(f"The shortage on {receipt.number} is claimed already.")
        if quantity > left:
            raise ValidationError({"quantity": f"Only {format(left.normalize(), 'f')} {comparison['unit']} of the "
                                               "shortage is left to claim."})
        self.quantity = quantity
        # The receipt's own cost, total first: what its lines booked, over what they received.
        booked = sum((line.quantity_received * line.accrued_unit_cost for line in lines), Decimal("0"))
        self.value = round_money(booked * quantity / comparison["received"])
        self.raised_on = to_date(self.raised_on) or timezone.localdate()
        if self.raised_on < to_date(receipt.receipt_date):
            raise ValidationError({"raised_on": "A claim is raised after the goods came."})
        self.number = DocumentSequence.next_for("purchasing.weight_claim", self.raised_on,
                                                name="Weight claims", prefix="WCL-")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("A claim is withdrawn, never deleted.")

    def _quantity_on_order(self, order_line):
        """The claim in the order line's unit, which the bill counts in."""
        item = order_line.item
        per = item.unit_factor(order_line.uom)
        quantity = self.quantity / per
        if quantity != quantity.quantize(Decimal("0.0001")):
            raise ValidationError(f"{format(self.quantity.normalize(), 'f')} {item.uom} is no whole number of "
                                  f"{order_line.uom} to four places; claim a quantity that is.")
        return quantity.quantize(Decimal("0.0001"))

    def _billed_short(self, order_line, quantity):
        """The supplier billed the short goods: billed less received, less what transporters already made good."""
        recovered = WeightClaim.objects.filter(
            receipt__lines__order_line=order_line, status=ClaimStatus.SETTLED, against=ClaimAgainst.TRANSPORTER,
        ).exclude(pk=self.pk).distinct()
        made_good = sum((claim._quantity_on_order(order_line) for claim in recovered), Decimal("0"))
        room = order_line.quantity_billed() - order_line.quantity_received() - made_good
        if quantity > room:
            raise ValidationError(
                f"{order_line.order} has {format(room.normalize(), 'f')} {order_line.uom} billed beyond what "
                f"came: bill the supplier's invoice first, then settle the claim against it.")

    @serialised("status", held_first=True)
    def settle(self, bill_line=None, on_date=None, supplier_note_number=""):
        """Against the supplier: a debit note on `bill_line`. Against the transporter: a recovery."""
        if self.status != ClaimStatus.OPEN:
            raise ValidationError(f"{self.number} is {self.get_status_display().lower()}.")
        lines = self._lines()
        order_line = lines[0].order_line
        lock_rows(order_line)
        quantity = self._quantity_on_order(order_line)
        self._billed_short(order_line, quantity)
        if self.against == ClaimAgainst.SUPPLIER:
            if bill_line is None:
                raise ValidationError({"bill_line": "Name the bill line the supplier's invoice billed it on."})
            if bill_line.order_line_id != order_line.pk or bill_line.bill.debits_id or not bill_line.bill.posted:
                raise ValidationError({"bill_line": "That is not a posted bill line for these goods."})
            note = bill_line.bill.create_debit_note(
                memo=f"Weight claim {self.number} on {self.receipt.number}", quantities={bill_line: quantity},
                supplier_note_number=supplier_note_number, on_date=on_date)
            self.debit_note = note
            self.settled_on = to_date(note.bill_date)
        else:
            account = Company.get().claims_recoverable_account
            grni = Company.get().grni_account
            if account is None or grni is None:
                raise ValidationError("Set the company's claims-recoverable and goods-received-not-invoiced "
                                      "accounts first.")
            day = correction_date(on_date, self.raised_on, f"{self.number} is not recovered on", "it was raised")
            label = f"Weight claim {self.number}: {self.transporter} for {self.receipt.number}"[:255]
            entry = JournalEntry.objects.create(date=day, reference=self.number, memo=label)
            JournalLine.objects.create(entry=entry, account=account, party=self.transporter, debit=self.value,
                                       description=label)
            JournalLine.objects.create(entry=entry, account=grni, credit=self.value, description=label)
            entry.post()
            self.recovery_entry = entry
            self.settled_on = day
        self.status = ClaimStatus.SETTLED
        super().save(update_fields=["status", "debit_note", "recovery_entry", "settled_on", "updated_at"])
        return self

    @serialised("status", held_first=True)
    @takes_back
    def withdraw(self, reason="", on_date=None):
        """
        Not pursued. An open claim settles nothing. A recovery is reversed; a debit note is the
        supplier's document and is not undone from here.
        """
        if self.status == ClaimStatus.WITHDRAWN:
            raise ValidationError(f"{self.number} is withdrawn already.")
        if not (reason or "").strip():
            raise ValidationError({"reason": "Say why the claim is withdrawn."})
        if self.debit_note_id:
            raise ValidationError(f"{self.number} was settled by {self.debit_note}: the supplier holds that note.")
        if self.recovery_entry_id:
            entry = self.recovery_entry
            day = correction_date(on_date, entry.date, f"{self.number} is not withdrawn on", "it was recovered")
            self.recovery_reversal = entry.create_reversal(entry_date=day, memo=f"Withdrawn: {entry.memo}"[:255])
            self.withdrawn_on = day
        else:
            self.withdrawn_on = correction_date(on_date, self.raised_on, f"{self.number} is not withdrawn on",
                                                "it was raised")
        self.status = ClaimStatus.WITHDRAWN
        self.withdraw_reason = reason.strip()[:255]
        super().save(update_fields=["status", "recovery_reversal", "withdrawn_on", "withdraw_reason", "updated_at"])
        return self
