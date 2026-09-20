from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from django.db.models import Q

from apps.accounting.mixins import TaxedDocumentMixin, TaxedLineMixin
from apps.accounting.models import (
    Account,
    ChargeType,
    JournalEntry,
    JournalLine,
    Payment,
    PaymentDirection,
    Tax,
    round_money,
)
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
from apps.accounting.settlement import (
    amount_overdue,
    installment_schedule,
    oldest_overdue,
    post_settlement_fx,
)
from apps.inventory.valuation import inventory_account_for, post_inventory_entry


def _require_vendor_role(party):
    if party and not party.role_assignments.filter(role=PartyRole.VENDOR).exists():
        raise ValidationError(f"{party} does not have the Vendor role.")


class FulfilmentStatus(models.TextChoices):
    NONE = "none", "None"
    PARTIAL = "partial", "Partial"
    FULL = "full", "Full"


class SettlementStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    UNPAID = "unpaid", "Unpaid"
    PARTIAL = "partial", "Partially paid"
    PAID = "paid", "Paid"


class BillPolicy(models.TextChoices):
    RECEIVED = "received", "Bill what has been received"
    ORDERED = "ordered", "Bill the whole order"


class OrderStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    CONFIRMED = "confirmed", "Confirmed"
    CANCELLED = "cancelled", "Cancelled"


class PurchaseOrder(TaxedDocumentMixin, AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    vendor = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="purchase_orders")
    order_date = models.DateField()
    reference = models.CharField(
        max_length=64, blank=True, help_text="The vendor's own quote or reference, if any."
    )
    status = models.CharField(max_length=16, choices=OrderStatus.choices, default=OrderStatus.DRAFT)
    currency = models.ForeignKey(Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    bill_policy = models.CharField(
        max_length=16, choices=BillPolicy.choices, default=BillPolicy.RECEIVED,
        help_text="Accept a bill for the whole order, or only for what has actually arrived.",
    )

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
        # Charge lines never arrive, so counting them would pin an
        # otherwise complete order at PARTIAL forever.
        lines = [line for line in self.lines.all() if not line.is_charge()]
        if not lines:
            return FulfilmentStatus.FULL
        if all(line.quantity_received() <= 0 for line in lines):
            return FulfilmentStatus.NONE
        if all(line.is_fully_received() for line in lines):
            return FulfilmentStatus.FULL
        return FulfilmentStatus.PARTIAL

    def bill_status(self):
        lines = list(self.lines.all())
        if not lines or all(line.quantity_billed() <= 0 for line in lines):
            return FulfilmentStatus.NONE
        if all(line.is_fully_billed() for line in lines):
            return FulfilmentStatus.FULL
        return FulfilmentStatus.PARTIAL

    @transaction.atomic
    def create_bill(self, payable_account, bill_date=None, reference=""):
        """
        Draft a bill for whatever this order still owes the vendor,
        carrying prices, discounts and taxes across.

        Calling it twice bills the remainder, not the whole order again —
        the same drawdown Sales needed after an order was billed three
        times for one delivery.
        """
        if self.status != OrderStatus.CONFIRMED:
            raise ValidationError("Only a confirmed order can be billed.")

        outstanding = [
            (line, line.quantity_billable())
            for line in self.lines.all()
            if line.quantity_billable() > 0
        ]
        if not outstanding:
            if self.bill_policy == BillPolicy.RECEIVED and any(
                line.quantity_unbilled() > 0 for line in self.lines.all()
            ):
                raise ValidationError(
                    "Nothing has been received that isn't already billed. This order is "
                    "billed on receipt, so book the goods in first."
                )
            raise ValidationError("This order is already fully billed.")

        bill = Bill.objects.create(
            vendor=self.vendor,
            bill_date=bill_date or timezone.now().date(),
            reference=reference,
            purchase_order=self,
            payable_account=payable_account,
            currency=self.currency,
        )
        for line, remaining in outstanding:
            bill_line = BillLine.objects.create(
                bill=bill,
                order_line=line,
                item=line.item,
                charge=line.charge,
                description=line.description or line.label(),
                quantity=remaining,
                unit_price=line.unit_price,
                discount_percent=line.discount_percent,
                expense_account=line.expense_account or self.vendor_expense_account(),
            )
            bill_line.taxes.set(line.taxes.all())
        return bill

    def add_charge(self, charge, amount, description="", quantity=Decimal("1")):
        """
        Put freight, handling or a surcharge from the vendor on this order.

        The charge's default taxes come across, because the commonest way
        to get freight wrong is to leave it untaxed where the jurisdiction
        taxes it at the same rate as the goods.
        """
        line = PurchaseOrderLine.objects.create(
            order=self, charge=charge, description=description or charge.name,
            quantity=Decimal(quantity), unit_price=round_money(Decimal(amount)),
            expense_account=charge.account_for(is_sale=False),
        )
        line.taxes.set(charge.taxes.all())
        return line

    def prepayments(self):
        """Posted prepayment bills raised against this order."""
        return self.bills.filter(is_prepayment=True, posted=True)

    def prepayment_total(self):
        return sum((bill.total() for bill in self.prepayments()), Decimal("0"))

    @transaction.atomic
    def create_prepayment_bill(
        self, payable_account, amount=None, percent=None, bill_date=None, description=""
    ):
        """
        Record a vendor's request for money up front, before anything
        arrives.

        The line debits the vendor-prepayment *asset*, not an expense:
        handing money over does not consume it, and expensing goods still
        sitting on the vendor's dock understates assets and overstates
        costs for as long as the order stays open. The cost lands later,
        on the real bill, and the prepayment is drawn down against it.

        It is also the only honest way to pay ahead on an order that bills
        on receipt — the mirror of the customer deposit on the sales side,
        and for the same reason.
        """
        if self.status != OrderStatus.CONFIRMED:
            raise ValidationError("Only a confirmed order can take a prepayment.")
        if (amount is None) == (percent is None):
            raise ValidationError("Give a prepayment either an amount or a percent, not both.")

        order_total = self.total()
        if percent is not None:
            percent = Decimal(percent)
            if percent <= 0 or percent > 100:
                raise ValidationError("A prepayment percent must be between 0 and 100.")
            amount = round_money(order_total * percent / Decimal("100"))
        amount = round_money(Decimal(amount))
        if amount <= 0:
            raise ValidationError("A prepayment must be for a positive amount.")

        already = self.prepayment_total()
        if already + amount > order_total:
            raise ValidationError(
                f"Prepayments of {already} are already on this order; taking {amount} more "
                f"would exceed the order total of {order_total}."
            )

        account = Company.get().vendor_prepayment_account
        if account is None:
            raise ValidationError("The company has no vendor prepayment account configured.")

        bill = Bill.objects.create(
            vendor=self.vendor,
            bill_date=bill_date or timezone.now().date(),
            purchase_order=self,
            payable_account=payable_account,
            currency=self.currency,
            is_prepayment=True,
        )
        BillLine.objects.create(
            bill=bill,
            description=description or f"Prepayment on order {self.number or self.pk}",
            quantity=Decimal("1"),
            unit_price=amount,
            expense_account=account,
        )
        return bill

    def vendor_expense_account(self):
        """
        Fallback for a line with no expense account of its own.

        Only ever reached by a non-stocked line, since stocked goods clear
        GRNI instead, but the field is non-null so something has to answer.
        """
        account = Company.get().default_purchase_expense_account
        if account is None:
            raise ValidationError(
                "This line has no expense account and the company has no default "
                "purchase expense account configured."
            )
        return account


class PurchaseOrderLine(TaxedLineMixin, AuditModel):
    order = models.ForeignKey(PurchaseOrder, related_name="lines", on_delete=models.CASCADE)
    item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT,
        related_name="purchase_order_lines",
    )
    charge = models.ForeignKey(
        ChargeType, null=True, blank=True, on_delete=models.PROTECT, related_name="%(class)ss",
        help_text="Set instead of an item when this line is freight, handling or similar.",
    )
    description = models.CharField(max_length=255, blank=True)
    uom = models.ForeignKey(
        UnitOfMeasure, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    expense_account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Where a non-stocked line lands when this order is billed.",
    )
    taxes = models.ManyToManyField(Tax, blank=True, related_name="purchase_order_lines")

    def party_for_tax(self):
        return self.order.vendor

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(item__isnull=False, charge__isnull=True)
                | Q(item__isnull=True, charge__isnull=False),
                name="po_line_is_item_or_charge",
            ),
        ]

    def __str__(self):
        return f"{self.label()} x{self.quantity}"

    def save(self, *args, **kwargs):
        if self.is_charge() and not self.expense_account_id:
            self.expense_account = self.charge.account_for(is_sale=False)
        if self.unit_price is None:
            raise ValidationError(
                f"Give {self.label()} a unit price; a purchase order has no price list to "
                "fall back on — the price is whatever the vendor quoted."
            )
        # A confirmed line could be edited below what had already been
        # received or billed, silently breaking every drawdown guard that
        # reads it. Sales has had this since its own audit; the purchase
        # side went without.
        if self.pk:
            previous = PurchaseOrderLine.objects.filter(pk=self.pk).first()
            if previous is not None:
                committed = max(self.quantity_received(), self.quantity_billed())
                if self.quantity < committed:
                    raise ValidationError(
                        f"{committed} of this line has already been received or billed; "
                        "the quantity cannot drop below that."
                    )
                if self.unit_price != previous.unit_price and self.quantity_billed() > 0:
                    raise ValidationError(
                        "This line has been billed; its price can no longer change. The "
                        "agreed price is what the three-way match checks against, so "
                        "moving it would retrospectively approve whatever was billed."
                    )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.quantity_received() or self.quantity_billed():
            raise ValidationError(
                "This line has been received or billed and can no longer be removed."
            )
        super().delete(*args, **kwargs)

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

    def quantity_billed(self):
        """Net quantity billed: posted bills minus posted debit notes."""
        billed = self.bill_lines.filter(
            bill__posted=True, bill__debits__isnull=True
        ).aggregate(total=models.Sum("quantity"))["total"] or Decimal("0")
        debited = self.bill_lines.filter(
            bill__posted=True, bill__debits__isnull=False
        ).aggregate(total=models.Sum("quantity"))["total"] or Decimal("0")
        return billed - debited

    def quantity_unbilled(self):
        return self.quantity - self.quantity_billed()

    def quantity_billable(self):
        """
        What may be billed right now — the third leg of the three-way
        match.

        Under a 'received' policy this is capped by what actually arrived.
        Paying for goods that have not turned up is the company's own
        money going out for something it does not have, which is why this
        defaults to the careful side where Sales defaults to the
        convenient one.
        """
        unbilled = self.quantity_unbilled()
        # A charge never arrives in a warehouse, so waiting for a receipt
        # that will never come would strand the freight on the order.
        if self.order.bill_policy != BillPolicy.RECEIVED or self.is_charge():
            return unbilled
        return min(unbilled, self.quantity_received() - self.quantity_billed())

    def is_fully_billed(self):
        return self.quantity_billed() >= self.quantity

    def quantity_billed_not_held(self):
        """
        Quantity billed that the company no longer has — goods sent back
        after they were billed for.

        Returning billed goods is legitimate: that is what you do with
        faulty stock. What is not legitimate is leaving it invisible. The
        return cannot be blocked (the goods physically went), so the gap
        is reported instead, and clearing it means raising a debit note.
        """
        return max(self.quantity_billed() - self.quantity_received(), Decimal("0"))


class Bill(TaxedDocumentMixin, AuditModel):
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
    is_prepayment = models.BooleanField(
        default=False, editable=False,
        help_text="Money paid to the vendor up front, held as an asset until the "
                  "goods arrive.",
    )
    settlement_discount_amount = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True, editable=False,
        help_text="Early-settlement discount taken against this bill.",
    )
    settlement_discount_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )

    class Meta:
        ordering = ["-bill_date", "-id"]
        permissions = [("post_bill", "Can post bills and issue debit notes")]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""), name="unique_bill_number"
            ),
            # The vendor's own invoice number, once, per vendor. Paying the
            # same invoice twice because it arrived by post and by email is
            # the single commonest way money leaves an AP department by
            # accident, and nothing else here would have caught it. Debit
            # notes are excluded: they deliberately carry their bill's
            # reference.
            models.UniqueConstraint(
                fields=["vendor", "reference"],
                condition=Q(debits__isnull=True) & ~Q(reference=""),
                name="one_bill_per_vendor_reference",
            ),
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
        if self.is_prepayment and not self.purchase_order_id:
            raise ValidationError("A prepayment must be against a purchase order.")
        if self.is_prepayment and self.debits_id:
            raise ValidationError("A debit note cannot also be a prepayment.")
        if self.debits_id and self.debits.vendor_id != self.vendor_id:
            raise ValidationError("A debit note must be for the same vendor as the bill it corrects.")

    def amount_paid(self):
        # A voided payment is money that never arrived — a bounced cheque,
        # a recalled transfer. Its allocation stays on record as history,
        # but nothing counts it any more.
        return sum(
            (allocation.amount
             for allocation in self.payment_allocations.all()
             if not allocation.payment.is_voided()),
            Decimal("0"),
        )

    def amount_debited(self):
        """Value of posted debit notes issued against this bill."""
        return sum(
            (note.total() for note in self.debit_notes.filter(posted=True)), Decimal("0")
        )

    def amount_prepaid(self):
        """Prepayments drawn down against this bill."""
        return sum(
            (application.amount for application in self.prepayment_applications.all()),
            Decimal("0"),
        )

    def prepayment_applied(self):
        """On a prepayment bill, how much of it has been drawn down."""
        return sum(
            (application.amount for application in self.applications.all()), Decimal("0")
        )

    def prepayment_unapplied(self):
        if not self.is_prepayment:
            return Decimal("0")
        return self.total() - self.prepayment_applied()

    @transaction.atomic
    def apply_prepayment(self, prepayment, amount=None, on_date=None):
        """
        Draw a prepayment down against this bill.

        Dr accounts payable / Cr vendor prepayments: the asset is consumed
        because the goods have now arrived, and only the difference is
        still owed.

        Deliberately independent of whether the prepayment bill was
        actually *paid*. Unpaid, the two payables simply sit side by side
        and still add up to what is owed; requiring payment first would
        block the real bill on the company's own slow payment run.
        """
        on_date = to_date(on_date) or timezone.now().date()
        if not self.posted:
            raise ValidationError("Only a posted bill can draw down a prepayment.")
        if self.is_prepayment or self.is_debit_note():
            raise ValidationError("A prepayment or debit note cannot draw down a prepayment.")
        if not prepayment.is_prepayment or not prepayment.posted:
            raise ValidationError("Only a posted prepayment bill can be drawn down.")
        if prepayment.vendor_id != self.vendor_id:
            raise ValidationError("That prepayment belongs to a different vendor.")
        if prepayment.currency_id != self.currency_id:
            raise ValidationError(
                "The prepayment and the bill are in different currencies; drawing one "
                "down against the other would silently write off the difference."
            )

        available = min(prepayment.prepayment_unapplied(), self.amount_due())
        amount = round_money(Decimal(amount)) if amount is not None else available
        if amount <= 0:
            raise ValidationError("There is nothing left to draw down.")
        if amount > available:
            raise ValidationError(
                f"Only {available} can be drawn down here "
                f"({prepayment.prepayment_unapplied()} left on the prepayment, "
                f"{self.amount_due()} due on the bill)."
            )

        account = Company.get().vendor_prepayment_account
        if account is None:
            raise ValidationError("The company has no vendor prepayment account configured.")

        rate = self.exchange_rate or Decimal("1")
        base_amount = round_money(amount * rate)
        memo = f"Prepayment {prepayment.number} applied to {self.number}"
        entry = JournalEntry.objects.create(date=on_date, reference=self.number, memo=memo)
        JournalLine.objects.create(
            entry=entry, account=self.payable_account, party=self.vendor,
            debit=base_amount, description=memo,
        )
        JournalLine.objects.create(
            entry=entry, account=account, party=self.vendor,
            credit=base_amount, description=memo,
        )
        entry.post()

        return PrepaymentApplication.objects.create(
            bill=self, prepayment=prepayment, amount=amount, date=on_date, journal_entry=entry
        )

    def apply_available_prepayments(self, on_date=None):
        """Draw down every prepayment still outstanding on this bill's order."""
        if not self.purchase_order_id or self.is_prepayment or self.is_debit_note():
            return []
        applied = []
        for prepayment in self.purchase_order.prepayments().order_by("bill_date", "pk"):
            if self.amount_due() <= 0:
                break
            if prepayment.prepayment_unapplied() <= 0:
                continue
            applied.append(self.apply_prepayment(prepayment, on_date=on_date))
        return applied

    def discount_due_date(self):
        """The last day an early-settlement discount can be taken."""
        if not self.payment_terms_id or not self.bill_date:
            return None
        return self.payment_terms.discount_due_date(to_date(self.bill_date))

    def settlement_discount(self):
        """What the company saves by paying early, if the terms offer it."""
        if not self.payment_terms_id:
            return Decimal("0")
        return self.payment_terms.discount_amount(self.total())

    def discount_is_available(self, as_of=None):
        deadline = self.discount_due_date()
        if not self.posted or self.is_debit_note() or not deadline:
            return False
        if self.settlement_discount_amount:
            return False
        return (to_date(as_of) or timezone.now().date()) <= deadline

    @transaction.atomic
    def take_settlement_discount(self, on_date=None, force=False):
        """
        Take the early-payment discount the vendor's terms offer.

        The mirror of Invoice.apply_settlement_discount, and inert for
        exactly as long: PaymentTerms has modelled 2/10 net 30 since the
        kernel, Sales learned to honour it, and the purchase side never
        did — so discounts the company was entitled to simply went
        unclaimed, which is money left on the table every month.

        Dr Accounts payable / Cr settlement discount received.
        """
        on_date = to_date(on_date) or timezone.now().date()
        if not force and not self.discount_is_available(on_date):
            raise ValidationError(
                "No settlement discount is available on this bill at that date."
            )
        amount = self.settlement_discount()
        if amount <= 0:
            raise ValidationError("These payment terms offer no settlement discount.")
        if amount > self.amount_due():
            raise ValidationError(
                f"Only {self.amount_due()} is outstanding; a discount of {amount} would "
                "take the bill below zero. Pay less, or settle the bill first."
            )

        account = Company.get().settlement_discount_received_account
        if account is None:
            raise ValidationError(
                "The company has no settlement discount received account configured."
            )

        rate = self.exchange_rate or Decimal("1")
        base_amount = round_money(amount * rate)
        memo = f"Settlement discount on {self.number}"
        entry = JournalEntry.objects.create(
            date=on_date, reference=self.number, memo=memo
        )
        JournalLine.objects.create(
            entry=entry, account=self.payable_account, party=self.vendor,
            debit=base_amount, description=memo,
        )
        JournalLine.objects.create(
            entry=entry, account=account, party=self.vendor,
            credit=base_amount, description=memo,
        )
        entry.post()

        self.settlement_discount_amount = amount
        self.settlement_discount_entry = entry
        super(Bill, self).save(update_fields=[
            "settlement_discount_amount", "settlement_discount_entry", "updated_at",
        ])
        return entry

    def amount_absorbed(self):
        """
        On a debit note: how much of it went to reducing its bill's balance
        rather than becoming cash the vendor owes back.

        Notes are absorbed oldest-first, because that is the order they
        were agreed in and a later note cannot retroactively take the
        earlier one's place against the bill.
        """
        if not self.is_debit_note():
            return Decimal("0")
        bill = self.debits
        capacity = max(bill.total() - bill.amount_paid(), Decimal("0"))
        used = Decimal("0")
        for note in bill.debit_notes.filter(posted=True).order_by("bill_date", "pk"):
            share = min(note.total(), max(capacity - used, Decimal("0")))
            if note.pk == self.pk:
                return share
            used += share
        return Decimal("0")

    def refund_due(self):
        """
        On a debit note: cash the vendor owes back, over and above clearing
        the bill.

        Debit-noting a bill that has already been paid is normal — you pay,
        then find the goods were faulty. The money has left, so the vendor
        owes it back; that is a balance on the note, not a negative one on
        the bill.
        """
        if not self.is_debit_note():
            return Decimal("0")
        refunded = sum(
            (allocation.amount for allocation in self.payment_allocations.all()),
            Decimal("0"),
        )
        return self.total() - self.amount_absorbed() - refunded

    def amount_due(self):
        if self.is_debit_note():
            return self.refund_due()
        # Debit notes can take a bill to zero but never below it. Beyond
        # that the money has already gone out, so what is left is cash owed
        # back, which lives on the note — a bill reading minus fifty says
        # the company owes a negative amount, which is not a thing.
        paid = (
            self.amount_paid()
            + (self.settlement_discount_amount or Decimal("0"))
            + self.amount_prepaid()
        )
        offset = min(self.amount_debited(), max(self.total() - paid, Decimal("0")))
        return self.total() - paid - offset

    def settlement_status(self):
        if not self.posted:
            return SettlementStatus.DRAFT
        if self.amount_due() <= 0:
            return SettlementStatus.PAID
        if self.amount_paid() or self.amount_debited():
            return SettlementStatus.PARTIAL
        return SettlementStatus.UNPAID

    def installments(self):
        """
        What falls due when, with money already received applied to the
        earliest installment first.

        A term with no installment lines gives a single row, so a plain
        net-30 document behaves exactly as it always did.
        """
        return installment_schedule(
            terms=self.payment_terms if self.payment_terms_id else None,
            document_date=self.bill_date,
            total=self.total(),
            settled=self.total() - self.amount_due(),
        )

    def amount_overdue(self, as_of=None):
        """
        How much is actually late — not the whole balance.

        On a 50/50 term the deposit can be weeks overdue while the balance
        is not due for another month. Chasing the full amount would be
        wrong, and chasing nothing would be worse.
        """
        if not self.posted or self.amount_due() <= 0:
            return Decimal("0")
        return amount_overdue(self.installments(), to_date(as_of) or timezone.now().date())

    def is_overdue(self, as_of=None):
        if not self.posted or self.amount_due() <= 0:
            return False
        return self.amount_overdue(as_of) > 0

    def days_overdue(self, as_of=None):
        """Days since the *earliest* installment that is still unpaid."""
        as_of = to_date(as_of) or timezone.now().date()
        if not self.is_overdue(as_of):
            return 0
        oldest = oldest_overdue(self.installments(), as_of)
        return (as_of - oldest["due_date"]).days if oldest else 0

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
        self._check_duplicate_reference()
        super().save(*args, **kwargs)

    def _check_duplicate_reference(self):
        """
        A readable error in front of the database constraint.

        The constraint is the real control — it holds whatever route the
        row arrives by — but an IntegrityError tells an AP clerk nothing,
        and this is the one they will hit most.
        """
        if self.is_debit_note() or not self.reference or not self.vendor_id:
            return
        clash = Bill.objects.filter(
            vendor_id=self.vendor_id, reference=self.reference, debits__isnull=True
        ).exclude(pk=self.pk).first()
        if clash is not None:
            raise ValidationError(
                f"{self.vendor} invoice '{self.reference}' is already on file as "
                f"{clash}. Paying the same invoice twice is what this prevents; if it "
                "really is a second invoice, give it the vendor's own distinct number."
            )

    def delete(self, *args, **kwargs):
        if self.posted:
            raise ValidationError("Posted bills cannot be deleted. Issue a debit note instead.")
        super().delete(*args, **kwargs)

    def _build_journal_entry(self, rate, reverse=False):
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
            memo=f"{'Debit note' if reverse else 'Bill'} {self.number} "
                 f"{'for' if reverse else 'from'} {self.vendor}",
        )

        # A capitalised charge debits the inventory the goods landed in,
        # not an expense: the freight is part of what the stock cost to get
        # here, and cost of sales is wrong by that amount if it is not.
        landed = defaultdict(Decimal)
        for charge_line, item, _warehouse, amount in self.landed_cost_allocations():
            landed[(charge_line.pk, item.pk)] += amount

        debits = []
        variance_total = Decimal("0")
        for line in lines:
            label = line.description or str(line.item)
            net = line.net_amount()
            account = line.posting_account()
            if line.posted_account_id != account.pk:
                line.posted_account = account
                super(BillLine, line).save(update_fields=["posted_account", "updated_at"])
            if line.clears_grni():
                # Clear the accrual at exactly what the receipt booked —
                # quantity billed at the *order* price. Clearing it at the
                # billed price instead leaves GRNI holding the difference
                # forever, which is how a supposedly self-clearing account
                # silently accumulates a balance nobody can explain.
                accrued = round_money(line.quantity * line.accrued_unit_cost())
                accrued -= round_money(accrued * line.discount_percent / Decimal("100"))
                debits.append((account, round_money(accrued * rate), label))
                variance_total += net - accrued
            elif line.is_charge() and line.charge.capitalise_into_inventory:
                shares = {
                    item_pk: amount for (charge_pk, item_pk), amount in landed.items()
                    if charge_pk == line.pk
                }
                if not shares:
                    # Nothing on this bill to absorb it — a freight-only
                    # bill, say. Expense it rather than refuse.
                    debits.append((line.expense_account, round_money(net * rate), label))
                else:
                    for item_pk, amount in shares.items():
                        account = inventory_account_for(Item.objects.get(pk=item_pk))
                        debits.append((account, round_money(amount * rate), f"{label} (landed)"))
            else:
                debits.append((line.expense_account, round_money(net * rate), label))

        if variance_total:
            # Purchase price variance goes to the P&L rather than revaluing
            # stock: by the time the bill arrives the goods may already have
            # been sold, and chasing the difference through a weighted
            # average that has since moved on costs more than it is worth.
            account = Company.get().purchase_price_variance_account
            if account is None:
                raise ValidationError(
                    f"This bill differs from the agreed price by {variance_total} and the "
                    "company has no purchase price variance account configured."
                )
            debits.append((account, round_money(variance_total * rate), "Price variance"))

        # Input tax is an asset, not a cost: VAT paid to a vendor is
        # reclaimable, so it is debited to the tax's paid_account rather
        # than buried in the expense. A vendor the fiscal position
        # zero-rates or reverse-charges contributes nothing here, which is
        # the whole point of running the taxes through effective_taxes().
        tax_totals = defaultdict(Decimal)
        line_taxes = self.line_tax_amounts()
        for line in lines:
            for tax, amount in line_taxes.get(line, ()):
                if not amount:
                    continue
                if not tax.applies_to_purchases():
                    raise ValidationError(f"Tax {tax.code} is not configured for purchases.")
                account = tax.account_for(is_sale=False)
                if account is None:
                    raise ValidationError(f"Tax {tax.code} has no paid account.")
                tax_totals[account] += amount
        for account, amount in tax_totals.items():
            debits.append((account, round_money(amount * rate), "Tax"))

        payable_total = sum(amount for _, amount, _ in debits)
        JournalLine.objects.create(
            entry=entry,
            account=self.payable_account,
            party=self.vendor,
            debit=payable_total if reverse else Decimal("0"),
            credit=Decimal("0") if reverse else payable_total,
            description=f"{'Debit note' if reverse else 'Bill'} {self.number}",
        )
        for account, amount, description in debits:
            if not amount:
                continue
            # A favourable variance — the vendor billed less than agreed —
            # is a negative debit, which a journal line cannot hold. It is
            # the same fact written on the other side.
            positive = amount if amount > 0 else Decimal("0")
            negative = -amount if amount < 0 else Decimal("0")
            JournalLine.objects.create(
                entry=entry, account=account, party=self.vendor,
                debit=negative if reverse else positive,
                credit=positive if reverse else negative,
                description=description,
            )
        return entry

    @transaction.atomic
    def post(self, memo=None, apply_prepayments=True):
        if self.posted:
            raise ValidationError("This bill is already posted.")

        self.bill_date = to_date(self.bill_date)
        if not self.is_debit_note() and self.total() <= 0:
            raise ValidationError(
                "This bill has no value to post. Give its lines a quantity and price."
            )
        if not self.is_debit_note():
            self._check_against_order()
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
            entry = self._build_journal_entry(self.exchange_rate, reverse=True)
            # A note that happens to give back every line in full is
            # additionally linked as a reversal, exactly as Sales does, so
            # the common case still reads as one entry undoing another.
            if self._is_full_debit_of(original):
                entry.reverses = original.journal_entry
                entry.save(update_fields=["reverses"])
            entry.post()
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

        if not self.is_debit_note():
            self._record_landed_cost()

        # Draw down the order's prepayments automatically. Leaving it to
        # the caller means the day someone forgets, the vendor is paid the
        # full amount on top of money already sent, and the prepayment sits
        # as an asset nobody ever clears.
        if apply_prepayments:
            self.apply_available_prepayments(on_date=self.bill_date)

    def _record_landed_cost(self):
        """
        Write the allocation into the stock ledger as value-only movements.

        Without this the GL says the stock is worth more and
        average_cost() does not, which is the exact drift this codebase
        derives valuation to avoid — and the next sale would post a COGS
        that disagrees with the inventory it relieved.
        """
        for charge_line, item, warehouse, amount in self.landed_cost_allocations():
            StockMovement.objects.create(
                item=item,
                warehouse=warehouse,
                movement_type=MovementType.ADJUSTMENT,
                quantity=Decimal("0"),
                value_adjustment=amount,
                reference=self.number,
                occurred_at=timezone.now(),
                notes=f"Landed cost from {self.number}: {charge_line.label()}",
            )

    def landed_cost_lines(self):
        """Charge lines on this bill that capitalise into stock value."""
        return [
            line for line in self.lines.all()
            if line.is_charge() and line.charge.capitalise_into_inventory
        ]

    def landed_cost_allocations(self):
        """
        [(bill_line, item, warehouse, amount)] spreading capitalised
        charges over the goods they brought in.

        Allocated by value across the stocked lines of the same bill, then
        across the receipts those lines drew on, in proportion to the
        quantity each warehouse took. Splitting by warehouse matters
        because per-warehouse valuation is a real number here, not a
        rollup — dumping the whole charge on one site would skew it.

        A capitalised charge with nothing on the bill to absorb it falls
        back to being expensed. Refusing would block a legitimate
        freight-only bill, and inventing an allocation across goods this
        bill says nothing about would be worse.
        """
        charges = self.landed_cost_lines()
        if not charges:
            return []

        absorbers = [
            (line, line.net_amount()) for line in self.lines.all()
            if not line.is_charge() and line.clears_grni() and line.net_amount() > 0
        ]
        absorbable = sum((amount for _, amount in absorbers), Decimal("0"))
        if absorbable <= 0:
            return []

        allocations = []
        for charge_line in charges:
            remaining = charge_line.net_amount()
            for index, (line, amount) in enumerate(absorbers):
                is_last = index == len(absorbers) - 1
                share = remaining if is_last else round_money(
                    charge_line.net_amount() * amount / absorbable
                )
                remaining -= share
                if share <= 0:
                    continue
                allocations.extend(
                    self._split_across_warehouses(charge_line, line, share)
                )
        return allocations

    def _split_across_warehouses(self, charge_line, goods_line, amount):
        receipts = []
        if goods_line.order_line_id:
            receipts = [
                (receipt_line.warehouse, receipt_line.quantity_received)
                for receipt_line in goods_line.order_line.receipt_lines.filter(
                    receipt__posted=True, receipt__reverses__isnull=True
                )
            ]
        if not receipts:
            return []

        total = sum((quantity for _, quantity in receipts), Decimal("0"))
        rows, remaining = [], amount
        for index, (warehouse, quantity) in enumerate(receipts):
            is_last = index == len(receipts) - 1
            share = remaining if is_last else round_money(amount * quantity / total)
            remaining -= share
            if share:
                rows.append((charge_line, goods_line.item, warehouse, share))
        return rows

    def _check_against_order(self):
        """
        The match itself: quantity against the order and the receipt,
        price against the order.

        Without the order_line link a vendor could bill the same delivery
        three times and nothing would notice — the identical defect that
        turned up in Sales, where one 1,000 order was invoiced for 3,000.
        """
        tolerance = Company.get().purchase_price_tolerance_percent or Decimal("0")
        for line in self.lines.all():
            if not line.order_line_id:
                continue
            order_line = line.order_line
            already = order_line.quantity_billed()
            if already + line.quantity > order_line.quantity:
                raise ValidationError(
                    f"Billing {line.quantity} of {order_line.item} would exceed the ordered "
                    f"quantity ({order_line.quantity}; {already} already billed)."
                )
            # A charge never arrives, so the receipt leg of the match does
            # not apply to it — the quantity and price legs still do.
            if order_line.order.bill_policy == BillPolicy.RECEIVED and not order_line.is_charge():
                received = order_line.quantity_received()
                if already + line.quantity > received:
                    raise ValidationError(
                        f"Only {received} of {order_line.item} has been received and "
                        f"{already} is already billed; this order is billed on receipt, "
                        f"so {line.quantity} cannot be billed yet."
                    )
            ordered_price = order_line.unit_price or Decimal("0")
            if line.unit_price > ordered_price:
                allowed = round_money(ordered_price * (Decimal("100") + tolerance) / Decimal("100"))
                if line.unit_price > allowed:
                    raise ValidationError(
                        f"{order_line.item} was ordered at {ordered_price} but billed at "
                        f"{line.unit_price}, beyond the {tolerance}% tolerance. Agree a "
                        "revised price on the order, or query the bill."
                    )

    def match_report(self):
        """Ordered / received / billed per line, for anyone checking a bill."""
        rows = []
        for line in self.lines.all():
            order_line = line.order_line
            rows.append({
                "line": line,
                "item": line.item,
                "quantity_billed": line.quantity,
                "quantity_ordered": order_line.quantity if order_line else None,
                "quantity_received": order_line.quantity_received() if order_line else None,
                "price_ordered": order_line.unit_price if order_line else None,
                "price_billed": line.unit_price,
                "price_variance": (
                    line.unit_price - order_line.unit_price if order_line else None
                ),
                "matched": bool(order_line),
                "billed_not_held": (
                    order_line.quantity_billed_not_held() if order_line else None
                ),
            })
        return rows

    def _is_full_debit_of(self, original):
        """True when this note gives back every line of `original` in full."""
        debited = defaultdict(Decimal)
        for line in self.lines.all():
            if not line.debits_line_id:
                return False
            debited[line.debits_line_id] += line.quantity
        original_lines = list(original.lines.all())
        if len(debited) != len(original_lines):
            return False
        return all(debited.get(line.pk) == line.quantity for line in original_lines)

    @transaction.atomic
    def create_debit_note(self, memo="", quantities=None):
        """
        Debit this bill. By default the whole thing; pass `quantities` as
        {bill_line: quantity} to give back part of it, which is what a
        partial goods return needs.

        Sales has had partial credit notes since its first pass. The
        purchase side could only ever reverse a bill in full, so a vendor
        who short-shipped one line of ten had to have the entire bill
        cancelled and re-entered.
        """
        if not self.posted:
            raise ValidationError("Only a posted bill can be corrected with a debit note.")
        if self.debits_id:
            raise ValidationError("Cannot issue a debit note against a debit note.")

        if quantities is None:
            selected = [(line, line.quantity_debitable()) for line in self.lines.all()]
            selected = [(line, quantity) for line, quantity in selected if quantity > 0]
        else:
            selected = [(line, quantity) for line, quantity in quantities.items() if quantity > 0]
            for line, quantity in selected:
                if line.bill_id != self.pk:
                    raise ValidationError("That line belongs to a different bill.")
                if quantity > line.quantity_debitable():
                    raise ValidationError(
                        f"Only {line.quantity_debitable()} of '{line}' is left to debit; "
                        f"cannot debit {quantity}."
                    )
        if not selected:
            raise ValidationError("Nothing to debit.")

        debit_note = Bill.objects.create(
            vendor=self.vendor,
            bill_date=timezone.now().date(),
            reference=self.reference,
            currency=self.currency,
            payment_terms=self.payment_terms,
            payable_account=self.payable_account,
            debits=self,
        )
        for line, quantity in selected:
            note_line = BillLine.objects.create(
                bill=debit_note,
                order_line=line.order_line,
                debits_line=line,
                item=line.item,
                charge=line.charge,
                description=line.description,
                quantity=quantity,
                unit_price=line.unit_price,
                discount_percent=line.discount_percent,
                expense_account=line.expense_account,
            )
            note_line.taxes.set(line.taxes.all())
        debit_note.post(memo=memo)
        return debit_note


class BillLine(TaxedLineMixin, AuditModel):
    bill = models.ForeignKey(Bill, related_name="lines", on_delete=models.CASCADE)
    debits_line = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="debit_lines",
        help_text="On a debit note line, the bill line being given back.",
    )
    order_line = models.ForeignKey(
        PurchaseOrderLine, null=True, blank=True, on_delete=models.PROTECT,
        related_name="bill_lines",
        help_text="Set when this line bills a purchase order line, so the order "
                  "cannot be billed twice for the same goods.",
    )
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="bill_lines")
    charge = models.ForeignKey(
        ChargeType, null=True, blank=True, on_delete=models.PROTECT, related_name="%(class)ss",
        help_text="Set instead of an item when this line is freight, handling or similar.",
    )
    description = models.CharField(max_length=255, blank=True)
    expense_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    posted_account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        editable=False,
        help_text="Where this line actually landed when the bill posted, frozen so a "
                  "debit note gives it back to the same place.",
    )
    taxes = models.ManyToManyField(Tax, blank=True, related_name="bill_lines")

    def party_for_tax(self):
        return self.bill.vendor

    def __str__(self):
        return f"{self.label()} x{self.quantity}"

    def quantity_debited(self):
        """How much of this line posted debit notes have already given back."""
        return self.debit_lines.filter(bill__posted=True).aggregate(
            total=models.Sum("quantity")
        )["total"] or Decimal("0")

    def quantity_debitable(self):
        return self.quantity - self.quantity_debited()

    def unbilled_receipt_quantity(self):
        """
        How much of this item has been received but not yet billed — the
        accrual this line could be clearing.

        Tied as precisely as the bill allows: to the order line when it
        names one, else to the bill's order, else to the item across every
        order. The loose ends matter because a bill entered by hand
        against goods that genuinely arrived still has to clear their
        accrual; only a bill with no receipt behind it anywhere should
        expense.
        """
        if not (self.item_id and self.item.track_inventory):
            return Decimal("0")

        if self.order_line_id:
            candidates = [self.order_line]
        else:
            lines = PurchaseOrderLine.objects.filter(item_id=self.item_id)
            if self.bill.purchase_order_id:
                lines = lines.filter(order_id=self.bill.purchase_order_id)
            candidates = list(lines)

        return sum(
            (max(line.quantity_received() - line.quantity_billed(), Decimal("0"))
             for line in candidates),
            Decimal("0"),
        )

    def clears_grni(self):
        """
        True when this line clears an accrual a goods receipt actually made.

        A bill for stocked goods that never came through a receipt has
        nothing to clear, and debiting GRNI anyway leaves a balance that
        nothing will ever offset — stock is created by receiving it, never
        by being billed for it.

        A debit note follows whatever its original line did. Recomputing
        would give the wrong answer: the original bill still counts as
        billed at the moment the note posts, so the accrual looks used up
        and the note would hand the money back to a different account than
        it took it from.
        """
        if self.debits_line_id:
            grni = Company.get().grni_account
            return bool(grni and self.debits_line.posted_account_id == grni.pk)
        # Once the line has posted, where it went is a fact, not something
        # to recompute: by then its own bill counts as billed, so the
        # accrual it cleared looks used up and every later reader — landed
        # cost, a debit note, a report — would get the opposite answer.
        if self.posted_account_id:
            grni = Company.get().grni_account
            return bool(grni and self.posted_account_id == grni.pk)
        return self.unbilled_receipt_quantity() > 0

    def accrued_unit_cost(self):
        """
        The price the receipt accrued at — the order price, not the bill's.

        Only knowable when the line names an order line. Without that link
        there is no agreed price to compare against, so the accrual is
        cleared at the billed amount and no variance is computed; tying
        bills to orders is what makes the variance visible.
        """
        if self.debits_line_id:
            return self.debits_line.accrued_unit_cost()
        return self.order_line.unit_price if self.order_line_id else self.unit_price

    def posting_account(self):
        """
        Stocked goods were already capitalised into Inventory when they were
        received, so the bill clears that accrual rather than expensing the
        cost a second time. Services and non-stocked lines expense directly.
        """
        if self.debits_line_id and self.debits_line.posted_account_id:
            return self.debits_line.posted_account
        if self.clears_grni():
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


class PrepaymentApplication(AuditModel):
    """
    One drawdown of a prepayment bill against a real bill.

    Modelled like BillPayment rather than as a negative line: the bill
    total should say what was bought, not what is left to pay after
    netting, or every cost report has to unpick the difference.
    """

    bill = models.ForeignKey(
        Bill, on_delete=models.PROTECT, related_name="prepayment_applications"
    )
    prepayment = models.ForeignKey(Bill, on_delete=models.PROTECT, related_name="applications")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    date = models.DateField()
    journal_entry = models.ForeignKey(
        JournalEntry, on_delete=models.PROTECT, related_name="+", editable=False
    )

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="prepayment_application_positive"),
            models.UniqueConstraint(
                fields=["bill", "prepayment"], name="one_application_per_bill_and_prepayment"
            ),
        ]

    def __str__(self):
        return f"{self.prepayment} -> {self.bill} ({self.amount})"


class BillPayment(AuditModel):
    """
    Applies part (or all) of a Payment to a Bill — the mirror of Sales'
    InvoicePayment.

    The ledger entry was already made when the payment posted; this
    records *which* bills that money settles, which is what makes an AP
    aging report and a payment run possible. Accounting owns Payment and
    may not import Purchasing, so the allocation lives on this side
    pointing back.
    """

    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="payment_allocations")
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="bill_allocations")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    fx_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
        help_text="Realised exchange difference posted when this allocation was made.",
    )

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["bill", "payment"], name="one_allocation_per_bill_and_payment"
            ),
            models.CheckConstraint(
                check=Q(amount__gt=0), name="bill_allocation_amount_positive"
            ),
        ]

    def __str__(self):
        return f"{self.payment} -> {self.bill} ({self.amount})"

    @staticmethod
    def allocated_for(payment, excluding=None):
        allocations = BillPayment.objects.filter(payment=payment)
        if excluding is not None and excluding.pk:
            allocations = allocations.exclude(pk=excluding.pk)
        return allocations.aggregate(total=models.Sum("amount"))["total"] or Decimal("0")

    @staticmethod
    def unallocated_for(payment):
        return payment.amount - BillPayment.allocated_for(payment)

    def clean(self):
        if not self.payment_id or not self.bill_id:
            return
        if not self.payment.posted:
            raise ValidationError("Only a posted payment can be allocated.")
        if self.payment.is_voided():
            raise ValidationError("This payment has been voided and cannot be allocated.")
        if not self.bill.posted:
            raise ValidationError("Only a posted bill can be settled.")
        # A debit note is money owed back *to* the company, so it is
        # settled by the vendor paying up — a receipt, never another
        # disbursement.
        if self.bill.is_debit_note():
            if self.payment.direction != PaymentDirection.RECEIPT:
                raise ValidationError(
                    "A debit note is refunded by the vendor with a receipt, not a payment."
                )
        elif self.payment.direction != PaymentDirection.DISBURSEMENT:
            raise ValidationError("Only a disbursement can settle a vendor bill.")
        if self.payment.party_id != self.bill.vendor_id:
            raise ValidationError("The payment and the bill belong to different parties.")
        # Settling across currencies would need FX gain/loss postings that
        # don't exist yet; treating 100 USD as 100 EUR silently writes off
        # the difference, so refuse rather than guess.
        if self.payment.currency_id != self.bill.currency_id:
            raise ValidationError(
                f"The payment is in {self.payment.currency or 'no currency'} but the bill "
                f"is in {self.bill.currency or 'no currency'}; cross-currency settlement "
                "is not supported."
            )

        available = self.payment.amount - BillPayment.allocated_for(self.payment, excluding=self)
        if self.amount > available:
            raise ValidationError(
                f"Only {available} of this payment is unallocated; cannot apply {self.amount}."
            )

        # amount_due() already means "refund still owed" on a debit note,
        # so one ceiling serves both directions.
        outstanding = self.bill.amount_due() + (
            BillPayment.objects.filter(pk=self.pk).first().amount if self.pk else Decimal("0")
        )
        if self.amount > outstanding:
            raise ValidationError(
                f"The bill only has {outstanding} outstanding; cannot apply {self.amount}."
            )

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.full_clean()
        # Re-posting rather than adjusting: an allocation can be re-pointed
        # or re-sized after the fact, and the exchange difference it caused
        # has to move with it or the control account keeps the old one.
        if self.fx_entry_id:
            self.fx_entry.create_reversal(
                memo=f"Re-stating exchange difference on {self}"
            )
            self.fx_entry = None
        super().save(*args, **kwargs)
        self._post_fx()

    def _post_fx(self):
        entry = post_settlement_fx(
            party=self.bill.vendor,
            control_account=self.bill.payable_account,
            amount=self.amount,
            document_rate=self.bill.exchange_rate,
            payment_rate=self.payment.exchange_rate,
            date=to_date(self.payment.payment_date),
            reference=self.bill.number,
            memo=f"Exchange difference settling {self.bill.number}",
            is_receivable=False,
        )
        if entry is not None:
            self.fx_entry = entry
            super(BillPayment, self).save(update_fields=["fx_entry", "updated_at"])

    @transaction.atomic
    def delete(self, *args, **kwargs):
        if self.fx_entry_id:
            self.fx_entry.create_reversal(
                memo=f"Releasing exchange difference on {self}"
            )
        super().delete(*args, **kwargs)


def billed_not_held(vendor=None):
    """
    Every order line billed for goods the company no longer holds.

    The AP control question after a return: what have we paid for, or
    agreed to pay for, that went back to the vendor and was never
    credited? Each row is a debit note waiting to be raised.
    """
    lines = PurchaseOrderLine.objects.select_related("order__vendor", "item")
    if vendor is not None:
        lines = lines.filter(order__vendor=vendor)

    rows = []
    for line in lines:
        gap = line.quantity_billed_not_held()
        if gap <= 0:
            continue
        rows.append({
            "order": line.order,
            "vendor": line.order.vendor,
            "line": line,
            "item": line.item,
            "quantity": gap,
            "value": round_money(gap * (line.unit_price or Decimal("0"))),
        })
    return sorted(rows, key=lambda row: -row["value"])


def vendor_balance(vendor):
    """
    Net position with this vendor: what is owed on bills, less cash they
    owe back on debit notes.

    Signed, so a vendor who has been overpaid reads negative rather than
    silently as zero.
    """
    bills = Bill.objects.filter(
        vendor=vendor, posted=True, debits__isnull=True
    ).prefetch_related("lines__taxes", "payment_allocations__payment", "debit_notes__lines__taxes")
    owed = sum((bill.amount_due() for bill in bills), Decimal("0"))

    notes = Bill.objects.filter(
        vendor=vendor, posted=True, debits__isnull=False
    ).prefetch_related("lines__taxes", "payment_allocations__payment", "debits__lines__taxes")
    refundable = sum((note.refund_due() for note in notes), Decimal("0"))
    return owed - refundable


AGING_BUCKETS = ((1, 30), (31, 60), (61, 90))


def ap_aging(as_of=None):
    """
    Outstanding vendor bills bucketed by how overdue they are — the mirror
    of ar_aging(), and the thing a company looks at before deciding what
    it can afford to pay this week.
    """
    as_of = to_date(as_of) or timezone.now().date()
    buckets = {"current": [], "1-30": [], "31-60": [], "61-90": [], "90+": []}

    bills = Bill.objects.filter(posted=True, debits__isnull=True).prefetch_related(
        "lines__taxes", "payment_allocations", "debit_notes__lines__taxes"
    )
    for bill in bills:
        if bill.amount_due() <= 0:
            continue
        # A row per outstanding installment, for the reason ar_aging gives.
        for row in bill.installments():
            if row["outstanding"] <= 0:
                continue
            days = (as_of - row["due_date"]).days
            if days <= 0:
                key = "current"
            elif days > 90:
                key = "90+"
            else:
                key = next(f"{lo}-{hi}" for lo, hi in AGING_BUCKETS if lo <= days <= hi)
            buckets[key].append({
                "bill": bill,
                "due_date": row["due_date"],
                "days_overdue": max(days, 0),
                "amount_due": row["outstanding"],
            })

    return {
        key: {
            "count": len(entries),
            "total": sum((entry["amount_due"] for entry in entries), Decimal("0")),
            "bills": entries,
        }
        for key, entries in buckets.items()
    }


def payment_run(due_by=None, vendor=None):
    """
    What is payable by a date, grouped by vendor.

    The AP counterpart of dunning: rather than chasing money in, it says
    what has to go out and by when, so a payment batch is a decision
    someone makes from a list rather than from whichever bill happens to
    be on top of the pile.
    """
    due_by = to_date(due_by) or timezone.now().date()
    bills = Bill.objects.filter(posted=True, debits__isnull=True).select_related(
        "vendor", "currency"
    ).prefetch_related("lines__taxes", "payment_allocations__payment", "debit_notes__lines__taxes")
    if vendor is not None:
        bills = bills.filter(vendor=vendor)

    rows = {}
    for bill in bills:
        if bill.amount_due() <= 0:
            continue
        # What falls due by the date, not the whole bill: an installment
        # term means part of a bill can be payable now and the rest not
        # for another month, and paying it all early is the company's
        # cash, given away for nothing.
        due = sum(
            (row["outstanding"] for row in bill.installments() if row["due_date"] <= due_by),
            Decimal("0"),
        )
        if due <= 0:
            continue
        # Bills in different currencies cannot be added together, so a
        # vendor billed in two currencies gets a row for each.
        key = (bill.vendor_id, bill.currency_id)
        row = rows.setdefault(key, {
            "vendor": bill.vendor, "currency": bill.currency,
            "total": Decimal("0"), "bills": [],
        })
        row["total"] += due
        row["bills"].append({
            "bill": bill, "due_date": bill.due_date, "amount_due": due,
            "days_overdue": bill.days_overdue(due_by),
        })
    return sorted(rows.values(), key=lambda row: -row["total"])


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
        # In save() rather than only clean(): receipts are built in code,
        # where nothing calls full_clean() for us.
        if self.order_line_id and self.order_line.is_charge():
            raise ValidationError(
                f"'{self.order_line.charge}' is a charge, not goods; nothing arrives for it."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.receipt.posted:
            raise ValidationError(
                "Cannot delete a line on a posted goods receipt. Create a return instead."
            )
        super().delete(*args, **kwargs)
