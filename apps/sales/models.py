import calendar
import datetime
import logging
from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounting.models import (
    ChargeType,
    Account,
    JournalEntry,
    JournalLine,
    Payment,
    PaymentDirection,
    Tax,
    compute_taxes,
    round_money,
)
from apps.core.models import (
    Address,
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

from apps.accounting.mixins import TaxedDocumentMixin, TaxedLineMixin

from apps.accounting.settlement import (
    amount_overdue,
    installment_schedule,
    oldest_overdue,
    post_settlement_fx,
)

from .pricing import resolve_price

logger = logging.getLogger(__name__)


def _require_customer_role(party):
    if party and not party.role_assignments.filter(role=PartyRole.CUSTOMER).exists():
        raise ValidationError(f"{party} does not have the Customer role.")


class SettlementStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    UNPAID = "unpaid", "Unpaid"
    PARTIAL = "partial", "Partially paid"
    PAID = "paid", "Paid"
    WRITTEN_OFF = "written_off", "Written off"


class PriceList(AuditModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    currency = models.ForeignKey(
        Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="price_lists"
    )
    is_default = models.BooleanField(
        default=False, help_text="Used for any customer without a list of their own."
    )
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValidationError("valid_to cannot be before valid_from.")

    def covers(self, on_date):
        on_date = to_date(on_date)
        if self.valid_from and on_date < self.valid_from:
            return False
        if self.valid_to and on_date > self.valid_to:
            return False
        return True


class PriceListItem(AuditModel):
    price_list = models.ForeignKey(PriceList, on_delete=models.CASCADE, related_name="entries")
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="price_entries")
    min_quantity = models.DecimalField(
        max_digits=18, decimal_places=4, default=Decimal("1"),
        help_text="Volume break: this price applies from this quantity upwards.",
    )
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        ordering = ["price_list", "item", "-min_quantity"]
        constraints = [
            models.UniqueConstraint(
                fields=["price_list", "item", "min_quantity"], name="unique_price_break"
            ),
            models.CheckConstraint(check=Q(min_quantity__gt=0), name="price_break_quantity_positive"),
            models.CheckConstraint(check=Q(unit_price__gte=0), name="price_not_negative"),
        ]

    def __str__(self):
        return f"{self.item} @ {self.unit_price} (from {self.min_quantity})"


class ApprovalPolicy(AuditModel):
    """
    The thresholds beyond which a sales order needs a second pair of eyes.

    RBAC answers "who may confirm an order"; it says nothing about how
    much they may give away while doing it. A rep with permission to
    confirm can discount 90% and sell below cost, and no role check
    anywhere notices.

    Each threshold is optional — a blank one is not enforced, rather than
    silently defaulting to something that would block every order the
    day this is switched on.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    max_discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Any line discounted above this needs approval.",
    )
    min_margin_percent = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Gross margin over the order, against weighted average cost. "
                  "Catches the discount that a percentage limit misses: a cheap "
                  "item sold at a small discount can still be sold at a loss.",
    )
    max_order_value = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="Orders above this value need approval regardless of margin.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "approval policies"

    def __str__(self):
        return self.name

    @classmethod
    def active(cls):
        return cls.objects.filter(is_active=True).first()


class ApprovalStatus(models.TextChoices):
    NOT_REQUIRED = "not_required", "Not required"
    PENDING = "pending", "Awaiting approval"
    APPROVED = "approved", "Approved"


class CustomerProfile(AuditModel):
    """
    Sales-side settings for a Party. Held here rather than on core.Party for
    the same reason PartyTaxProfile lives in Accounting: the kernel must not
    depend on the modules built on top of it.
    """

    party = models.OneToOneField(Party, on_delete=models.CASCADE, related_name="customer_profile")
    price_list = models.ForeignKey(
        PriceList, null=True, blank=True, on_delete=models.SET_NULL, related_name="customers",
        help_text="Overrides the default price list for this customer.",
    )
    credit_limit = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="Maximum this customer may owe. Blank means no limit.",
    )

    def __str__(self):
        return f"Sales profile for {self.party}"


class InvoicePolicy(models.TextChoices):
    ORDERED = "ordered", "Bill what was ordered"
    DELIVERED = "delivered", "Bill what was delivered"


class FulfilmentStatus(models.TextChoices):
    NONE = "none", "Nothing yet"
    PARTIAL = "partial", "Partially"
    FULL = "full", "Fully"


class OrderStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    CONFIRMED = "confirmed", "Confirmed"
    CANCELLED = "cancelled", "Cancelled"


class SalesOrder(TaxedDocumentMixin, AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    customer = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="sales_orders")
    order_date = models.DateField()
    reference = models.CharField(
        max_length=64, blank=True, help_text="The customer's own PO number, if any."
    )
    status = models.CharField(max_length=16, choices=OrderStatus.choices, default=OrderStatus.DRAFT)
    currency = models.ForeignKey(Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    payment_terms = models.ForeignKey(
        PaymentTerms, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    billing_address = models.ForeignKey(
        Address, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    shipping_address = models.ForeignKey(
        Address, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    sales_rep = models.ForeignKey(
        Party, null=True, blank=True, on_delete=models.PROTECT, related_name="%(class)s_sales",
        help_text="The employee credited with this sale.",
    )
    invoice_policy = models.CharField(
        max_length=16, choices=InvoicePolicy.choices, default=InvoicePolicy.ORDERED,
        help_text="Bill the whole order up front, or only what has shipped.",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)
    approval_note = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["-order_date", "-id"]
        permissions = [
            ("approve_order", "Can approve orders that breach the discount policy"),
        ]
        constraints = [
            # Drafts all carry an empty number until confirmed, so uniqueness
            # can only apply once one has been assigned.
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""), name="unique_sales_order_number"
            )
        ]

    def __str__(self):
        return f"{self.number or f'SO-draft-{self.pk}'} {self.customer}"

    def clean(self):
        _require_customer_role(self.customer)

    def save(self, *args, **kwargs):
        if self._state.adding:
            self._apply_customer_defaults()
        super().save(*args, **kwargs)

    def _apply_customer_defaults(self):
        if not self.customer_id:
            return
        customer = self.customer
        self.currency = self.currency or customer.default_currency
        self.payment_terms = self.payment_terms or customer.payment_terms
        self.billing_address = self.billing_address or customer.billing_address()
        self.shipping_address = self.shipping_address or customer.shipping_address()

    def credit_limit_breach(self):
        """
        How far this order would put the customer over their limit, or None.

        Returned rather than raised: over the limit is now one approval
        reason among several, not a separate special case with its own
        ungated bypass flag.
        """
        profile = CustomerProfile.objects.filter(party=self.customer).first()
        limit = profile.credit_limit if profile else None
        if limit is None:
            return None
        # committed_balance already counts this order once it is confirmed;
        # at confirmation time it isn't yet, so add it explicitly.
        exposure = committed_balance(self.customer) + self.total()
        return (exposure, limit) if exposure > limit else None

    def cost_total(self):
        """
        What this order's goods cost, at weighted average across warehouses.

        Lines with no cost to speak of — charges, and items never received
        — are left out of both sides, so an order of pure services doesn't
        read as 100% margin and trip nothing, or as 0% margin and trip
        everything.
        """
        total = Decimal("0")
        for line in self.lines.all():
            if line.is_charge() or not line.item_id:
                continue
            cost = line.item.average_cost()
            if not cost:
                continue
            total += round_money(cost * line.quantity)
        return total

    def costed_revenue(self):
        """Net revenue of the lines that cost_total() could price."""
        total = Decimal("0")
        for line in self.lines.all():
            if line.is_charge() or not line.item_id:
                continue
            if not line.item.average_cost():
                continue
            total += line.net_amount()
        return total

    def margin_percent(self):
        """Gross margin over the costed lines, or None if nothing is costed."""
        revenue = self.costed_revenue()
        if revenue <= 0:
            return None
        return round_money((revenue - self.cost_total()) / revenue * Decimal("100"))

    def approval_reasons(self):
        """
        Every policy threshold this order breaches, in plain words.

        A list rather than a boolean because an approver needs to know
        what they are approving, and because an order that trips three
        limits should say so rather than reveal them one at a time as
        each is fixed.
        """
        reasons = []
        breach = self.credit_limit_breach()
        if breach:
            exposure, limit = breach
            reasons.append(
                f"{self.customer} would be {exposure} against a credit limit of {limit}."
            )

        policy = ApprovalPolicy.active()
        if policy is None:
            return reasons

        if policy.max_discount_percent is not None:
            for line in self.lines.all():
                if line.discount_percent > policy.max_discount_percent:
                    reasons.append(
                        f"'{line.label()}' is discounted {line.discount_percent}%, above the "
                        f"{policy.max_discount_percent}% limit."
                    )
        if policy.max_order_value is not None and self.total() > policy.max_order_value:
            reasons.append(
                f"The order is {self.total()}, above the {policy.max_order_value} limit."
            )
        if policy.min_margin_percent is not None:
            margin = self.margin_percent()
            if margin is not None and margin < policy.min_margin_percent:
                reasons.append(
                    f"Gross margin is {margin}%, below the {policy.min_margin_percent}% floor."
                )
        return reasons

    def requires_approval(self):
        return bool(self.approval_reasons())

    def approval_status(self):
        if self.approved_at:
            return ApprovalStatus.APPROVED
        return ApprovalStatus.PENDING if self.requires_approval() else ApprovalStatus.NOT_REQUIRED

    def approve(self, by=None, note=""):
        """
        Record that someone accepted the breach. Gated by
        sales.approve_order at the API and admin layer.
        """
        if self.status == OrderStatus.CANCELLED:
            raise ValidationError("A cancelled order cannot be approved.")
        if self.approved_at:
            raise ValidationError("This order has already been approved.")
        if not self.requires_approval():
            raise ValidationError("This order breaches no policy; it needs no approval.")
        self.approved_by = by
        self.approved_at = timezone.now()
        self.approval_note = note or "; ".join(self.approval_reasons())[:255]
        self.save(update_fields=["approved_by", "approved_at", "approval_note", "updated_at"])

    def withdraw_approval(self):
        """Drop an approval, so a re-priced order has to be looked at again."""
        if not self.approved_at:
            return
        self.approved_by = None
        self.approved_at = None
        self.approval_note = ""
        self.save(update_fields=["approved_by", "approved_at", "approval_note", "updated_at"])

    def cancel(self):
        """Cancel an order that hasn't been acted on yet."""
        if self.status == OrderStatus.CANCELLED:
            raise ValidationError("This order is already cancelled.")
        for line in self.lines.all():
            if line.quantity_shipped() or line.quantity_invoiced():
                raise ValidationError(
                    "This order has been shipped or invoiced and cannot be cancelled. "
                    "Return the goods or issue a credit note instead."
                )
        self.status = OrderStatus.CANCELLED
        self.save(update_fields=["status", "updated_at"])

    def invoice_status(self):
        lines = list(self.lines.all())
        if not lines or all(line.quantity_invoiced() <= 0 for line in lines):
            return FulfilmentStatus.NONE
        if all(line.is_fully_invoiced() for line in lines):
            return FulfilmentStatus.FULL
        return FulfilmentStatus.PARTIAL

    def delivery_status(self):
        # Charge lines are never shipped, so counting them would pin an
        # otherwise complete order at PARTIAL forever.
        lines = [line for line in self.lines.all() if not line.is_charge()]
        if not lines:
            return FulfilmentStatus.FULL
        if all(line.quantity_shipped() <= 0 for line in lines):
            return FulfilmentStatus.NONE
        if all(line.is_fully_shipped() for line in lines):
            return FulfilmentStatus.FULL
        return FulfilmentStatus.PARTIAL

    @transaction.atomic
    def confirm(self):
        if self.status == OrderStatus.CONFIRMED:
            raise ValidationError("This order is already confirmed.")
        if self.status == OrderStatus.CANCELLED:
            raise ValidationError("A cancelled order cannot be confirmed.")
        if not self.lines.exists():
            raise ValidationError("Cannot confirm an order with no lines.")
        # No bypass flag. The old ignore_credit_limit= was reachable by
        # anyone who could confirm an order at all — which is the rep whose
        # discount the limit exists to check — and left no record that
        # anyone had decided anything.
        if self.approval_status() == ApprovalStatus.PENDING:
            raise ValidationError(
                "This order needs approval before it can be confirmed: "
                + " ".join(self.approval_reasons())
            )
        if not self.number:
            self.number = DocumentSequence.next_for(
                "sales.order", self.order_date, name="Sales Orders", prefix="SO-"
            )
        self.status = OrderStatus.CONFIRMED
        self.save(update_fields=["number", "status", "updated_at"])

    @transaction.atomic
    def create_invoice(self, receivable_account, invoice_date=None):
        """
        Draft an invoice for whatever is still uninvoiced on this order,
        carrying taxes and discounts across. Calling it twice bills the
        remainder, not the whole order again.
        """
        if self.status != OrderStatus.CONFIRMED:
            raise ValidationError("Only a confirmed order can be invoiced.")

        outstanding = [
            (line, line.quantity_invoiceable())
            for line in self.lines.all()
            if line.quantity_invoiceable() > 0
        ]
        if not outstanding:
            if self.invoice_policy == InvoicePolicy.DELIVERED and any(
                line.quantity_uninvoiced() > 0 for line in self.lines.all()
            ):
                raise ValidationError(
                    "Nothing has shipped that isn't already invoiced. This order bills on "
                    "delivery, so ship the goods first."
                )
            raise ValidationError("This order is already fully invoiced.")

        invoice = Invoice.objects.create(
            customer=self.customer,
            invoice_date=invoice_date or timezone.now().date(),
            reference=self.reference,
            sales_order=self,
            receivable_account=receivable_account,
            currency=self.currency,
            payment_terms=self.payment_terms,
            billing_address=self.billing_address,
            shipping_address=self.shipping_address,
            sales_rep=self.sales_rep,
        )
        for line, remaining in outstanding:
            invoice_line = InvoiceLine.objects.create(
                invoice=invoice,
                order_line=line,
                item=line.item,
                charge=line.charge,
                description=line.description or line.label(),
                quantity=remaining,
                unit_price=line.unit_price,
                discount_percent=line.discount_percent,
                revenue_account=line.revenue_account,
            )
            invoice_line.taxes.set(line.taxes.all())
        return invoice

    def add_charge(self, charge, amount, description="", quantity=Decimal("1")):
        """
        Put freight, handling or a surcharge on this order.

        The charge's default taxes come across, because the commonest way
        to get freight wrong is to bill it untaxed when the jurisdiction
        taxes it at the same rate as the goods.
        """
        line = SalesOrderLine.objects.create(
            order=self, charge=charge, description=description or charge.name,
            quantity=Decimal(quantity), unit_price=round_money(Decimal(amount)),
            revenue_account=charge.account_for(is_sale=True),
        )
        line.taxes.set(charge.taxes.all())
        return line

    def deposits(self):
        """Posted down-payment invoices raised against this order."""
        return self.invoices.filter(is_down_payment=True, posted=True)

    def deposit_total(self):
        return sum((deposit.total() for deposit in self.deposits()), Decimal("0"))

    @transaction.atomic
    def create_down_payment_invoice(
        self, receivable_account, amount=None, percent=None, invoice_date=None, description=""
    ):
        """
        Bill the customer up front, before anything ships.

        The line credits the customer-deposit *liability*, not revenue:
        taking the money does not earn it, and recognising revenue against
        goods still sitting in the warehouse overstates income and
        understates what the company owes. The revenue lands later, on the
        real invoice, and the deposit is drawn down against it.

        This is also the only honest way to bill ahead on an order that
        invoices on delivery.
        """
        if self.status != OrderStatus.CONFIRMED:
            raise ValidationError("Only a confirmed order can take a down payment.")
        if (amount is None) == (percent is None):
            raise ValidationError("Give a down payment either an amount or a percent, not both.")

        order_total = self.total()
        if percent is not None:
            percent = Decimal(percent)
            if percent <= 0 or percent > 100:
                raise ValidationError("A down payment percent must be between 0 and 100.")
            amount = round_money(order_total * percent / Decimal("100"))
        amount = round_money(Decimal(amount))
        if amount <= 0:
            raise ValidationError("A down payment must be for a positive amount.")

        already = self.deposit_total()
        if already + amount > order_total:
            raise ValidationError(
                f"Down payments of {already} are already on this order; taking {amount} more "
                f"would exceed the order total of {order_total}."
            )

        account = Company.get().customer_deposit_account
        if account is None:
            raise ValidationError("The company has no customer deposit account configured.")

        invoice = Invoice.objects.create(
            customer=self.customer,
            invoice_date=invoice_date or timezone.now().date(),
            reference=self.reference,
            sales_order=self,
            receivable_account=receivable_account,
            currency=self.currency,
            payment_terms=self.payment_terms,
            billing_address=self.billing_address,
            shipping_address=self.shipping_address,
            sales_rep=self.sales_rep,
            is_down_payment=True,
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            description=description or f"Down payment on order {self.number or self.pk}",
            quantity=Decimal("1"),
            unit_price=amount,
            revenue_account=account,
        )
        return invoice


class SalesOrderLine(TaxedLineMixin, AuditModel):
    order = models.ForeignKey(SalesOrder, related_name="lines", on_delete=models.CASCADE)
    item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="sales_order_lines"
    )
    charge = models.ForeignKey(
        ChargeType, null=True, blank=True, on_delete=models.PROTECT, related_name="%(class)ss",
        help_text="Set instead of an item when this line bills freight, handling or similar.",
    )
    description = models.CharField(max_length=255, blank=True)
    uom = models.ForeignKey(
        UnitOfMeasure, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    revenue_account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    taxes = models.ManyToManyField(Tax, blank=True, related_name="sales_order_lines")

    def party_for_tax(self):
        return self.order.customer

    class Meta:
        constraints = [
            models.CheckConstraint(check=Q(quantity__gt=0), name="order_line_quantity_positive"),
            models.CheckConstraint(check=Q(unit_price__gte=0), name="order_line_price_not_negative"),
            models.CheckConstraint(
                check=Q(item__isnull=False, charge__isnull=True)
                | Q(item__isnull=True, charge__isnull=False),
                name="order_line_is_item_or_charge",
            ),
        ]

    def __str__(self):
        return f"{self.label()} x{self.quantity}"

    def save(self, *args, **kwargs):
        if self.is_charge():
            if not self.revenue_account_id:
                self.revenue_account = self.charge.account_for(is_sale=True)
            if self.unit_price is None:
                raise ValidationError(
                    f"Give the {self.charge} charge an explicit amount; a charge has no "
                    "price list to fall back on."
                )
        elif self.unit_price is None:
            self.unit_price = resolve_price(
                self.item,
                customer=self.order.customer,
                quantity=self.quantity,
                currency=self.order.currency,
                on_date=self.order.order_date,
            )
            if self.unit_price is None:
                raise ValidationError(
                    f"No price found for {self.item}: set one on the item, add it to a "
                    "price list, or give the line an explicit unit price."
                )
        # Defect: a confirmed line could be edited below what had already
        # shipped or been invoiced, silently breaking the drawdown guards.
        if self.pk:
            previous = SalesOrderLine.objects.filter(pk=self.pk).first()
            if previous is not None:
                committed = max(self.quantity_shipped(), self.quantity_invoiced())
                if self.quantity < committed:
                    raise ValidationError(
                        f"{committed} of this line has already been shipped or invoiced; "
                        f"the quantity cannot drop below that."
                    )
                if self.unit_price != previous.unit_price and self.quantity_invoiced() > 0:
                    raise ValidationError(
                        "This line has been invoiced; its price can no longer change. "
                        "Issue a credit note instead."
                    )
                # An approval covers the order someone looked at. Re-price
                # it afterwards and the approval is for something else.
                repriced = (
                    self.unit_price != previous.unit_price
                    or self.quantity != previous.quantity
                    or self.discount_percent != previous.discount_percent
                )
                if repriced and self.order.approved_at:
                    self.order.withdraw_approval()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.quantity_shipped() or self.quantity_invoiced():
            raise ValidationError(
                "This line has been shipped or invoiced and can no longer be removed."
            )
        super().delete(*args, **kwargs)

    def quantity_shipped(self):
        """Net quantity shipped: posted deliveries minus posted customer returns."""
        shipped = self.delivery_lines.filter(
            delivery__posted=True, delivery__reverses__isnull=True
        ).aggregate(total=models.Sum("quantity_shipped"))["total"] or Decimal("0")
        returned = self.delivery_lines.filter(
            delivery__posted=True, delivery__reverses__isnull=False
        ).aggregate(total=models.Sum("quantity_shipped"))["total"] or Decimal("0")
        return shipped - returned

    def is_fully_shipped(self):
        return self.quantity_shipped() >= self.quantity

    def quantity_invoiced(self):
        """Net quantity invoiced: posted invoices minus posted credit notes."""
        invoiced = self.invoice_lines.filter(
            invoice__posted=True, invoice__credits__isnull=True
        ).aggregate(total=models.Sum("quantity"))["total"] or Decimal("0")
        credited = self.invoice_lines.filter(
            invoice__posted=True, invoice__credits__isnull=False
        ).aggregate(total=models.Sum("quantity"))["total"] or Decimal("0")
        return invoiced - credited

    def quantity_uninvoiced(self):
        return self.quantity - self.quantity_invoiced()

    def quantity_invoiceable(self):
        """
        What may be billed right now. Under a 'delivered' policy that is
        capped by what has actually shipped — billing goods still sitting
        in the warehouse is how customers end up paying for nothing.
        """
        uninvoiced = self.quantity_uninvoiced()
        # A charge has nothing to ship, so waiting for a delivery that will
        # never come would strand the freight on the order forever.
        if self.order.invoice_policy != InvoicePolicy.DELIVERED or self.is_charge():
            return uninvoiced
        return min(uninvoiced, self.quantity_shipped() - self.quantity_invoiced())

    def is_fully_invoiced(self):
        return self.quantity_invoiced() >= self.quantity


class Invoice(TaxedDocumentMixin, AuditModel):
    """
    Sales invoice. Posting creates a balanced JournalEntry (Dr Accounts
    Receivable / Cr Revenue / Cr tax accounts) via Accounting — Sales
    never writes ledger rows itself. Once posted, an invoice is immutable
    exactly like a JournalEntry: the only way to correct one is a credit
    note, which reuses JournalEntry.create_reversal() rather than
    inventing its own correction logic.
    """

    number = models.CharField(max_length=32, blank=True, editable=False)
    customer = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="invoices")
    invoice_date = models.DateField()
    due_date = models.DateField(null=True, blank=True, editable=False)
    reference = models.CharField(max_length=64, blank=True)
    sales_order = models.ForeignKey(
        SalesOrder, null=True, blank=True, on_delete=models.PROTECT, related_name="invoices"
    )
    receivable_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
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
    billing_address = models.ForeignKey(
        Address, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    shipping_address = models.ForeignKey(
        Address, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    sales_rep = models.ForeignKey(
        Party, null=True, blank=True, on_delete=models.PROTECT, related_name="%(class)s_sales",
        help_text="The employee credited with this sale.",
    )
    credits = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="credit_notes",
        help_text="Set when this invoice is a credit note correcting another invoice.",
    )
    journal_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False
    )
    posted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(
        null=True, blank=True, editable=False,
        help_text="When this was last emailed to the customer.",
    )
    settlement_discount_amount = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True, editable=False,
        help_text="Early-settlement discount written off against this invoice.",
    )
    settlement_discount_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )
    is_down_payment = models.BooleanField(
        default=False, editable=False,
        help_text="Money taken up front against an order, held as a liability "
                  "until the goods are delivered.",
    )
    written_off_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0"), editable=False,
        help_text="Receivable judged uncollectable and charged to bad debt.",
    )

    class Meta:
        ordering = ["-invoice_date", "-id"]
        permissions = [
            ("post_invoice", "Can post invoices and issue credit notes"),
            ("write_off_invoice", "Can write a receivable off to bad debt"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""), name="unique_invoice_number"
            )
        ]

    def __str__(self):
        if self.number:
            return f"{self.number} {self.customer}"
        kind = "CN" if self.credits_id else "INV"
        return f"{kind}-draft-{self.pk} {self.customer}"

    def is_credit_note(self):
        return bool(self.credits_id)

    def render_pdf(self):
        from .documents import render_invoice_pdf

        return render_invoice_pdf(self)

    def recipient_email(self):
        contact = self.customer.primary_contact()
        if contact and contact.email:
            return contact.email
        return self.customer.email or ""

    def email_to_customer(self, to=None, subject=None, body=None):
        """Send the invoice as a PDF attachment. Returns the address used."""
        from django.core.mail import EmailMessage

        if not self.posted:
            raise ValidationError("Only a posted invoice can be sent.")
        recipient = to or self.recipient_email()
        if not recipient:
            raise ValidationError(
                f"{self.customer} has no email address on the party or its primary contact."
            )

        company = Company.get()
        kind = "Credit note" if self.is_credit_note() else "Invoice"
        message = EmailMessage(
            subject=subject or f"{kind} {self.number} from {company.name}",
            body=body or (
                f"Dear {self.customer.name},\n\n"
                f"Please find {kind.lower()} {self.number} attached"
                + (f", due {self.due_date:%d %b %Y}" if self.due_date and not self.is_credit_note() else "")
                + f".\n\nRegards,\n{company.name}\n"
            ),
            to=[recipient],
        )
        message.attach(f"{self.number}.pdf", self.render_pdf(), "application/pdf")
        message.send()

        self.sent_at = timezone.now()
        super(Invoice, self).save(update_fields=["sent_at", "updated_at"])
        return recipient

    def discount_due_date(self):
        """The last day an early-settlement discount can be taken."""
        if not self.payment_terms_id or not self.invoice_date:
            return None
        return self.payment_terms.discount_due_date(to_date(self.invoice_date))

    def settlement_discount(self):
        """What the customer saves by paying early, if the terms offer it."""
        if not self.payment_terms_id:
            return Decimal("0")
        return self.payment_terms.discount_amount(self.total())

    def discount_is_available(self, as_of=None):
        deadline = self.discount_due_date()
        if not self.posted or self.is_credit_note() or not deadline:
            return False
        if self.settlement_discount_amount:
            return False
        return (to_date(as_of) or timezone.now().date()) <= deadline

    @transaction.atomic
    def apply_settlement_discount(self, on_date=None, force=False):
        """
        Write off the early-settlement discount.

        Without this the terms are decorative: a customer on 2/10 net 30
        who pays the discounted amount leaves a small balance outstanding
        forever, and dunning chases them for it.
        """
        on_date = to_date(on_date) or timezone.now().date()
        if not force and not self.discount_is_available(on_date):
            raise ValidationError(
                "No settlement discount is available on this invoice at that date."
            )
        amount = self.settlement_discount()
        if amount <= 0:
            raise ValidationError("These payment terms offer no settlement discount.")

        account = Company.get().settlement_discount_account
        if account is None:
            raise ValidationError(
                "The company has no settlement discount account configured."
            )

        rate = self.exchange_rate or Decimal("1")
        base_amount = round_money(amount * rate)
        entry = JournalEntry.objects.create(
            date=on_date, reference=self.number,
            memo=f"Settlement discount on {self.number}",
        )
        JournalLine.objects.create(
            entry=entry, account=account, party=self.customer,
            debit=base_amount, description=f"Settlement discount {self.number}",
        )
        JournalLine.objects.create(
            entry=entry, account=self.receivable_account, party=self.customer,
            credit=base_amount, description=f"Settlement discount {self.number}",
        )
        entry.post()

        self.settlement_discount_amount = amount
        self.settlement_discount_entry = entry
        super(Invoice, self).save(
            update_fields=["settlement_discount_amount", "settlement_discount_entry", "updated_at"]
        )
        return entry

    @transaction.atomic
    def write_off(self, amount=None, on_date=None, reason=""):
        """
        Charge an uncollectable receivable to bad debt expense.

        Not a credit note. A credit note reverses revenue, which says the
        sale did not happen; a write-off says it did happen and the money
        never arrived. Those are different facts and they land in
        different places on the P&L. Without this, dunning escalates to
        nothing and a dead invoice ages in AR forever, overstating assets.

        Dr Bad debt expense / Cr Accounts receivable.
        """
        on_date = to_date(on_date) or timezone.now().date()
        if not self.posted:
            raise ValidationError("Only a posted invoice can be written off.")
        if self.is_credit_note():
            raise ValidationError("A credit note cannot be written off.")

        due = self.amount_due()
        if due <= 0:
            raise ValidationError("This invoice has nothing left to write off.")
        amount = round_money(Decimal(amount)) if amount is not None else due
        if amount <= 0:
            raise ValidationError("A write-off must be for a positive amount.")
        if amount > due:
            raise ValidationError(
                f"Cannot write off {amount} against an invoice with {due} outstanding."
            )

        account = Company.get().bad_debt_account
        if account is None:
            raise ValidationError("The company has no bad debt account configured.")

        rate = self.exchange_rate or Decimal("1")
        base_amount = round_money(amount * rate)
        memo = f"Bad debt write-off {self.number}"
        entry = JournalEntry.objects.create(
            date=on_date, reference=self.number,
            memo=f"{memo}: {reason}" if reason else memo,
        )
        JournalLine.objects.create(
            entry=entry, account=account, party=self.customer,
            debit=base_amount, description=memo,
        )
        JournalLine.objects.create(
            entry=entry, account=self.receivable_account, party=self.customer,
            credit=base_amount, description=memo,
        )
        entry.post()

        InvoiceWriteOff.objects.create(
            invoice=self, amount=amount, date=on_date, reason=reason, journal_entry=entry
        )
        self.written_off_amount = self.written_off_amount + amount
        super(Invoice, self).save(update_fields=["written_off_amount", "updated_at"])
        return entry

    @transaction.atomic
    def recover_write_off(self, write_off, on_date=None):
        """
        Undo a write-off because the customer paid after all.

        Reversing the original entry rather than posting a fresh one keeps
        the correction path identical to every other posted document: the
        write-off stays on record, visibly reversed, instead of being
        quietly netted to nothing by an unrelated entry.
        """
        if write_off.invoice_id != self.pk:
            raise ValidationError("That write-off belongs to a different invoice.")
        if write_off.recovered_entry_id:
            raise ValidationError("That write-off has already been recovered.")

        entry = write_off.journal_entry.create_reversal(
            entry_date=to_date(on_date) or timezone.now().date(),
            memo=f"Bad debt recovered {self.number}",
        )
        write_off.recovered_entry = entry
        write_off.save(update_fields=["recovered_entry", "updated_at"])
        self.written_off_amount = self.written_off_amount - write_off.amount
        super(Invoice, self).save(update_fields=["written_off_amount", "updated_at"])
        return entry

    def amount_written_off(self):
        return self.written_off_amount or Decimal("0")

    def amount_deposited(self):
        """Down payments drawn down against this invoice."""
        return sum(
            (application.amount for application in self.deposit_applications.all()), Decimal("0")
        )

    def deposit_applied(self):
        """On a down-payment invoice, how much of it has been drawn down."""
        return sum(
            (application.amount for application in self.applications.all()), Decimal("0")
        )

    def deposit_unapplied(self):
        if not self.is_down_payment:
            return Decimal("0")
        return self.total() - self.deposit_applied()

    @transaction.atomic
    def apply_deposit(self, deposit, amount=None, on_date=None):
        """
        Draw a down payment down against this invoice.

        Dr customer deposits / Cr accounts receivable: the liability is
        discharged because the goods have now been delivered, and the
        customer only owes the difference.

        Deliberately independent of whether the deposit invoice was
        actually *paid*. Unpaid, the two receivables simply stay open side
        by side and still add up to what the customer owes; requiring
        payment first would block the final invoice on a slow payer for
        no accounting reason.
        """
        on_date = to_date(on_date) or timezone.now().date()
        if not self.posted:
            raise ValidationError("Only a posted invoice can draw down a deposit.")
        if self.is_down_payment or self.is_credit_note():
            raise ValidationError("A down payment or credit note cannot draw down a deposit.")
        if not deposit.is_down_payment or not deposit.posted:
            raise ValidationError("Only a posted down-payment invoice can be drawn down.")
        if deposit.customer_id != self.customer_id:
            raise ValidationError("That deposit belongs to a different customer.")
        if deposit.currency_id != self.currency_id:
            raise ValidationError(
                "The deposit and the invoice are in different currencies; drawing one down "
                "against the other would silently write off the difference."
            )

        available = min(deposit.deposit_unapplied(), self.amount_due())
        amount = round_money(Decimal(amount)) if amount is not None else available
        if amount <= 0:
            raise ValidationError("There is nothing left to draw down.")
        if amount > available:
            raise ValidationError(
                f"Only {available} can be drawn down here "
                f"({deposit.deposit_unapplied()} left on the deposit, "
                f"{self.amount_due()} due on the invoice)."
            )

        account = Company.get().customer_deposit_account
        if account is None:
            raise ValidationError("The company has no customer deposit account configured.")

        rate = self.exchange_rate or Decimal("1")
        base_amount = round_money(amount * rate)
        memo = f"Down payment {deposit.number} applied to {self.number}"
        entry = JournalEntry.objects.create(date=on_date, reference=self.number, memo=memo)
        JournalLine.objects.create(
            entry=entry, account=account, party=self.customer,
            debit=base_amount, description=memo,
        )
        JournalLine.objects.create(
            entry=entry, account=self.receivable_account, party=self.customer,
            credit=base_amount, description=memo,
        )
        entry.post()

        return DepositApplication.objects.create(
            invoice=self, deposit=deposit, amount=amount, date=on_date, journal_entry=entry
        )

    def apply_available_deposits(self, on_date=None):
        """Draw down every deposit still outstanding on this invoice's order."""
        if not self.sales_order_id or self.is_down_payment or self.is_credit_note():
            return []
        applied = []
        for deposit in self.sales_order.deposits().order_by("invoice_date", "pk"):
            if self.amount_due() <= 0:
                break
            if deposit.deposit_unapplied() <= 0:
                continue
            applied.append(self.apply_deposit(deposit, on_date=on_date))
        return applied

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

    def amount_credited(self):
        """Value of posted credit notes issued against this invoice."""
        return sum(
            (note.total() for note in self.credit_notes.filter(posted=True)), Decimal("0")
        )

    def amount_due(self):
        return (
            self.total() - self.amount_paid() - self.amount_credited()
            - (self.settlement_discount_amount or Decimal("0"))
            - self.amount_written_off()
            - self.amount_deposited()
        )

    def settlement_status(self):
        if not self.posted:
            return SettlementStatus.DRAFT
        if self.amount_due() <= 0:
            # Cleared by giving up on the money is not the same as cleared
            # by being paid, and a collections report needs to tell them
            # apart even though the balance reads zero either way.
            if self.amount_written_off() > 0:
                return SettlementStatus.WRITTEN_OFF
            return SettlementStatus.PAID
        if self.amount_paid() or self.amount_credited() or self.amount_written_off():
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
            document_date=self.invoice_date,
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

    def clean(self):
        _require_customer_role(self.customer)
        if self.is_down_payment and not self.sales_order_id:
            raise ValidationError("A down payment must be against a sales order.")
        if self.is_down_payment and self.credits_id:
            raise ValidationError("A credit note cannot also be a down payment.")
        if self.credits_id and self.credits.customer_id != self.customer_id:
            raise ValidationError("A credit note must be for the same customer as the invoice it credits.")

    def _was_posted_in_db(self):
        if not self.pk:
            return False
        return Invoice.objects.filter(pk=self.pk, posted=True).exists()

    def save(self, *args, **kwargs):
        if self._was_posted_in_db():
            raise ValidationError(
                "This invoice is posted and immutable. Issue a credit note instead."
            )
        if self._state.adding:
            self._apply_customer_defaults()
        super().save(*args, **kwargs)

    def _apply_customer_defaults(self):
        if not self.customer_id:
            return
        customer = self.customer
        self.currency = self.currency or customer.default_currency
        self.payment_terms = self.payment_terms or customer.payment_terms
        self.billing_address = self.billing_address or customer.billing_address()
        self.shipping_address = self.shipping_address or customer.shipping_address()

    def delete(self, *args, **kwargs):
        if self.posted:
            raise ValidationError("Posted invoices cannot be deleted. Issue a credit note instead.")
        super().delete(*args, **kwargs)

    def _rate_for_posting(self):
        if self.currency is None:
            return Decimal("1")
        return self.currency.rate_on(self.invoice_date)

    def _build_journal_entry(self, rate, reverse=False):
        """
        Build the ledger entry in base currency. `reverse` swaps the sides,
        which is what a credit note posts.

        The counterpart lines are computed first and the receivable is set to
        their exact sum: rounding each converted line independently can leave
        the two sides a cent apart, which would make a legitimate document
        unpostable.
        """
        lines = list(self.lines.all())
        if not lines:
            raise ValidationError("Cannot post an invoice with no lines.")

        entry = JournalEntry.objects.create(
            date=self.invoice_date,
            reference=self.number or self.reference,
            memo=f"Invoice {self.number} for {self.customer}",
        )

        credits = []
        for line in lines:
            if line.revenue_account_id is None:
                raise ValidationError(f"Line '{line}' has no revenue account.")
            credits.append((line.revenue_account, round_money(line.net_amount() * rate),
                            line.description or str(line.item)))

        tax_totals = defaultdict(Decimal)
        line_taxes = self.line_tax_amounts()
        for line in lines:
            for tax, amount in line_taxes.get(line, ()):
                if not amount:
                    continue
                if not tax.applies_to_sales():
                    raise ValidationError(f"Tax {tax.code} is not configured for sales.")
                account = tax.account_for(is_sale=True)
                if account is None:
                    raise ValidationError(f"Tax {tax.code} has no collected account.")
                tax_totals[account] += amount
        for account, amount in tax_totals.items():
            credits.append((account, round_money(amount * rate), "Tax"))

        receivable_total = sum(amount for _, amount, _ in credits)
        JournalLine.objects.create(
            entry=entry,
            account=self.receivable_account,
            party=self.customer,
            credit=receivable_total if reverse else Decimal("0"),
            debit=Decimal("0") if reverse else receivable_total,
            description=f"{'Credit note' if reverse else 'Invoice'} {self.number}",
        )
        for account, amount, description in credits:
            if amount:
                JournalLine.objects.create(
                    entry=entry, account=account, party=self.customer,
                    debit=amount if reverse else Decimal("0"),
                    credit=Decimal("0") if reverse else amount,
                    description=description,
                )
        return entry

    def _is_full_credit_of(self, original):
        """True when this credit note gives back every line of `original` in full."""
        credited = defaultdict(Decimal)
        for line in self.lines.all():
            if not line.credits_line_id:
                return False
            credited[line.credits_line_id] += line.quantity
        original_lines = list(original.lines.all())
        if len(credited) != len(original_lines):
            return False
        return all(credited.get(line.pk) == line.quantity for line in original_lines)

    @transaction.atomic
    def post(self, memo=None, apply_deposits=True):
        if self.posted:
            raise ValidationError("This invoice is already posted.")

        self.invoice_date = to_date(self.invoice_date)
        if not self.is_credit_note() and self.total() <= 0:
            raise ValidationError(
                "This invoice has no value to post. Give its lines a quantity and price."
            )
        if not self.is_credit_note():
            for line in self.lines.all():
                if not line.order_line_id:
                    continue
                already = line.order_line.quantity_invoiced()
                if already + line.quantity > line.order_line.quantity:
                    raise ValidationError(
                        f"Invoicing {line.quantity} of {line.order_line.item} would exceed the "
                        f"ordered quantity ({line.order_line.quantity}; {already} already invoiced)."
                    )
        if not self.number:
            if self.is_credit_note():
                self.number = DocumentSequence.next_for(
                    "sales.credit_note", self.invoice_date, name="Credit Notes", prefix="CN-"
                )
            else:
                self.number = DocumentSequence.next_for(
                    "sales.invoice", self.invoice_date, name="Customer Invoices", prefix="INV-"
                )

        self.exchange_rate = self._rate_for_posting()
        self.due_date = (
            self.payment_terms.due_date(self.invoice_date)
            if self.payment_terms_id else self.invoice_date
        )

        if self.is_credit_note():
            original = self.credits
            if not original.posted or not original.journal_entry_id:
                raise ValidationError("Cannot post a credit note against an unposted invoice.")
            # Credit at the rate the invoice was billed at, not today's, so a
            # credit note can't book a spurious FX gain against itself.
            self.exchange_rate = original.exchange_rate or Decimal("1")
            entry = self._build_journal_entry(self.exchange_rate, reverse=True)
            if self._is_full_credit_of(original):
                entry.reverses = original.journal_entry
                entry.save(update_fields=["reverses"])
            entry.post()
        else:
            entry = self._build_journal_entry(self.exchange_rate)
            entry.post()

        self.journal_entry = entry
        self.posted = True
        self.posted_at = timezone.now()
        super(Invoice, self).save(
            update_fields=[
                "number", "invoice_date", "due_date", "exchange_rate", "journal_entry",
                "posted", "posted_at", "updated_at",
            ]
        )

        # Draw down the order's deposits automatically. Leaving this to the
        # caller means the day someone forgets, the customer is billed the
        # full amount on top of money they have already handed over — and
        # the deposit sits as a liability nobody ever clears.
        if apply_deposits:
            self.apply_available_deposits(on_date=self.invoice_date)

    @transaction.atomic
    def create_credit_note(self, memo="", quantities=None):
        """
        Credit this invoice. By default the whole thing; pass
        `quantities` as {invoice_line: quantity} to credit part of it, which
        is what a partial goods return needs.
        """
        if not self.posted:
            raise ValidationError("Only a posted invoice can be credited.")
        if self.is_credit_note():
            raise ValidationError("Cannot issue a credit note against a credit note.")

        if quantities is None:
            selected = [(line, line.quantity) for line in self.lines.all()]
        else:
            selected = [(line, quantity) for line, quantity in quantities.items() if quantity > 0]
            for line, quantity in selected:
                if line.invoice_id != self.pk:
                    raise ValidationError("That line belongs to a different invoice.")
                if quantity > line.quantity_creditable():
                    raise ValidationError(
                        f"Only {line.quantity_creditable()} of '{line}' is left to credit; "
                        f"cannot credit {quantity}."
                    )
        if not selected:
            raise ValidationError("Nothing to credit.")

        credit_note = Invoice.objects.create(
            customer=self.customer,
            invoice_date=timezone.now().date(),
            reference=self.reference,
            receivable_account=self.receivable_account,
            currency=self.currency,
            payment_terms=self.payment_terms,
            billing_address=self.billing_address,
            shipping_address=self.shipping_address,
            sales_rep=self.sales_rep,
            credits=self,
        )
        for line, quantity in selected:
            credit_line = InvoiceLine.objects.create(
                invoice=credit_note,
                order_line=line.order_line,
                credits_line=line,
                item=line.item,
                charge=line.charge,
                description=line.description,
                quantity=quantity,
                unit_price=line.unit_price,
                discount_percent=line.discount_percent,
                revenue_account=line.revenue_account,
            )
            credit_line.taxes.set(line.taxes.all())
        credit_note.post(memo=memo)
        return credit_note


class InvoiceLine(TaxedLineMixin, AuditModel):
    invoice = models.ForeignKey(Invoice, related_name="lines", on_delete=models.CASCADE)
    order_line = models.ForeignKey(
        SalesOrderLine, null=True, blank=True, on_delete=models.PROTECT,
        related_name="invoice_lines",
        help_text="Set when this line bills a sales order line, so the order can't be billed twice.",
    )
    credits_line = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="credit_lines",
        help_text="On a credit note line, the invoice line being credited.",
    )
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="invoice_lines")
    charge = models.ForeignKey(
        ChargeType, null=True, blank=True, on_delete=models.PROTECT, related_name="%(class)ss",
        help_text="Set instead of an item when this line bills freight, handling or similar.",
    )
    description = models.CharField(max_length=255, blank=True)
    revenue_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    taxes = models.ManyToManyField(Tax, blank=True, related_name="invoice_lines")

    def party_for_tax(self):
        return self.invoice.customer

    def __str__(self):
        return f"{self.label()} x{self.quantity}"

    def quantity_credited(self):
        """How much of this line has already been credited by posted credit notes."""
        return self.credit_lines.filter(invoice__posted=True).aggregate(
            total=models.Sum("quantity")
        )["total"] or Decimal("0")

    def quantity_creditable(self):
        return self.quantity - self.quantity_credited()

    def save(self, *args, **kwargs):
        if self.invoice_id and Invoice.objects.filter(pk=self.invoice_id, posted=True).exists():
            raise ValidationError(
                "Cannot modify a line on a posted invoice. Issue a credit note instead."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.invoice.posted:
            raise ValidationError(
                "Cannot delete a line on a posted invoice. Issue a credit note instead."
            )
        super().delete(*args, **kwargs)


class InvoicePayment(AuditModel):
    """
    Applies part (or all) of a Payment to an Invoice. The ledger entry was
    already made when the payment posted — this records *which* invoices
    that money settles, which is what makes an aging report possible.

    Allocations stay editable after the fact: re-applying a payment to a
    different invoice is a bookkeeping correction, not a ledger change.
    """

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payment_allocations")
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="invoice_allocations")
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
                fields=["invoice", "payment"], name="one_allocation_per_invoice_and_payment"
            ),
            models.CheckConstraint(check=Q(amount__gt=0), name="allocation_amount_positive"),
        ]

    def __str__(self):
        return f"{self.payment} -> {self.invoice} ({self.amount})"

    @staticmethod
    def allocated_for(payment, excluding=None):
        allocations = InvoicePayment.objects.filter(payment=payment)
        if excluding is not None and excluding.pk:
            allocations = allocations.exclude(pk=excluding.pk)
        return allocations.aggregate(total=models.Sum("amount"))["total"] or Decimal("0")

    @staticmethod
    def unallocated_for(payment):
        return payment.amount - InvoicePayment.allocated_for(payment)

    def clean(self):
        if not self.payment_id or not self.invoice_id:
            return
        if not self.payment.posted:
            raise ValidationError("Only a posted payment can be allocated.")
        if self.payment.is_voided():
            raise ValidationError("This payment has been voided and cannot be allocated.")
        if not self.invoice.posted:
            raise ValidationError("Only a posted invoice can be settled.")
        # A credit note is money owed *to* the customer, so it is settled by
        # paying them (a disbursement), never by receiving more money.
        if self.invoice.is_credit_note():
            if self.payment.direction != PaymentDirection.DISBURSEMENT:
                raise ValidationError(
                    "A credit note is refunded with a disbursement, not a receipt."
                )
        elif self.payment.direction != PaymentDirection.RECEIPT:
            raise ValidationError("Only a receipt can settle a customer invoice.")
        if self.payment.party_id != self.invoice.customer_id:
            raise ValidationError("The payment and the invoice belong to different parties.")
        # Settling across currencies would need FX gain/loss postings that
        # don't exist yet; treating 100 USD as 100 EUR silently writes off
        # the difference, so refuse rather than guess.
        if self.payment.currency_id != self.invoice.currency_id:
            raise ValidationError(
                f"The payment is in {self.payment.currency or 'no currency'} but the invoice "
                f"is in {self.invoice.currency or 'no currency'}; cross-currency settlement "
                "is not supported."
            )

        available = self.payment.amount - InvoicePayment.allocated_for(self.payment, excluding=self)
        if self.amount > available:
            raise ValidationError(
                f"Only {available} of this payment is unallocated; cannot apply {self.amount}."
            )

        outstanding = self.invoice.amount_due() + (
            InvoicePayment.objects.filter(pk=self.pk).first().amount if self.pk else Decimal("0")
        )
        if self.amount > outstanding:
            raise ValidationError(
                f"The invoice only has {outstanding} outstanding; cannot apply {self.amount}."
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
            party=self.invoice.customer,
            control_account=self.invoice.receivable_account,
            amount=self.amount,
            document_rate=self.invoice.exchange_rate,
            payment_rate=self.payment.exchange_rate,
            date=to_date(self.payment.payment_date),
            reference=self.invoice.number,
            memo=f"Exchange difference settling {self.invoice.number}",
            is_receivable=True,
        )
        if entry is not None:
            self.fx_entry = entry
            super(InvoicePayment, self).save(update_fields=["fx_entry", "updated_at"])

    @transaction.atomic
    def delete(self, *args, **kwargs):
        if self.fx_entry_id:
            self.fx_entry.create_reversal(
                memo=f"Releasing exchange difference on {self}"
            )
        super().delete(*args, **kwargs)


class DepositApplication(AuditModel):
    """
    One drawdown of a down-payment invoice against a real invoice.

    Modelled like InvoicePayment rather than as a negative line on the
    invoice: the invoice total should say what was sold, not what is left
    to collect after netting, or every revenue report has to unpick the
    difference.
    """

    invoice = models.ForeignKey(
        Invoice, on_delete=models.PROTECT, related_name="deposit_applications"
    )
    deposit = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="applications")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    date = models.DateField()
    journal_entry = models.ForeignKey(
        JournalEntry, on_delete=models.PROTECT, related_name="+", editable=False
    )

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="deposit_application_positive"),
            models.UniqueConstraint(
                fields=["invoice", "deposit"], name="one_application_per_invoice_and_deposit"
            ),
        ]

    def __str__(self):
        return f"{self.deposit} -> {self.invoice} ({self.amount})"


class InvoiceWriteOff(AuditModel):
    """
    One occasion on which part of a receivable was judged uncollectable.

    A row per event rather than a single amount on the invoice: partial
    write-offs are normal (settle at 40c in the dollar, write off the
    rest), each one needs its own date, reason and ledger entry, and a
    recovery has to name which one it undoes.
    """

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="write_offs")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    date = models.DateField()
    reason = models.CharField(max_length=255, blank=True)
    journal_entry = models.ForeignKey(
        JournalEntry, on_delete=models.PROTECT, related_name="+", editable=False
    )
    recovered_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
        help_text="Set when the debt was recovered and this write-off reversed.",
    )

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="write_off_amount_positive"),
        ]

    def __str__(self):
        return f"Write-off {self.amount} on {self.invoice}"

    def is_recovered(self):
        return bool(self.recovered_entry_id)


def bad_debt_report(start=None, end=None):
    """Write-offs in a period, netting recoveries off the total."""
    write_offs = InvoiceWriteOff.objects.select_related("invoice__customer")
    if start:
        write_offs = write_offs.filter(date__gte=to_date(start))
    if end:
        write_offs = write_offs.filter(date__lte=to_date(end))

    rows = {}
    for write_off in write_offs:
        customer = write_off.invoice.customer
        row = rows.setdefault(
            customer.pk,
            {"customer": customer, "written_off": Decimal("0"),
             "recovered": Decimal("0"), "net": Decimal("0")},
        )
        row["written_off"] += write_off.amount
        if write_off.is_recovered():
            row["recovered"] += write_off.amount
        row["net"] = row["written_off"] - row["recovered"]
    return sorted(rows.values(), key=lambda row: -row["net"])


def outstanding_balance(customer):
    """What this customer currently owes across all posted invoices."""
    invoices = Invoice.objects.filter(
        customer=customer, posted=True, credits__isnull=True
    ).prefetch_related("lines__taxes", "payment_allocations__payment", "credit_notes__lines__taxes")
    return sum((invoice.amount_due() for invoice in invoices), Decimal("0"))


def committed_balance(customer):
    """
    Total exposure: what is owed, plus what has been promised but not yet
    billed. A limit that counted only posted invoices would wave through
    any number of confirmed orders.
    """
    uninvoiced = Decimal("0")
    orders = SalesOrder.objects.filter(
        customer=customer, status=OrderStatus.CONFIRMED
    ).prefetch_related("lines__taxes")
    for order in orders:
        for line in order.lines.all():
            remaining = line.quantity_uninvoiced()
            if remaining <= 0:
                continue
            share = remaining / line.quantity if line.quantity else Decimal("0")
            uninvoiced += round_money(line.total() * share)

    # A down payment is billed against an order whose lines are still
    # uninvoiced, so the same money appears in both halves of the sum.
    # Counting it twice would use up a customer's credit limit for taking
    # money from them up front, which is backwards.
    deposits = Invoice.objects.filter(
        customer=customer, posted=True, is_down_payment=True
    ).prefetch_related("lines__taxes", "applications")
    outstanding_deposits = sum(
        (deposit.deposit_unapplied() for deposit in deposits), Decimal("0")
    )
    return outstanding_balance(customer) + uninvoiced - outstanding_deposits


class StatementEntry:
    """One movement on a customer statement."""

    __slots__ = ("date", "kind", "reference", "description", "debit", "credit", "balance")

    def __init__(self, date, kind, reference, description, debit=None, credit=None):
        self.date = date
        self.kind = kind
        self.reference = reference
        self.description = description
        self.debit = debit or Decimal("0")
        self.credit = credit or Decimal("0")
        self.balance = Decimal("0")

    def __repr__(self):
        return f"<{self.kind} {self.reference} {self.debit or -self.credit}>"


def _statement_currency(customer, currency):
    """
    A statement adds up movements, so they all have to be in one currency.

    Same stance as cross-currency settlement: refuse rather than quietly
    sum 100 USD and 100 EUR into 200 of nothing.
    """
    if currency is not None:
        return currency
    used = set(
        Invoice.objects.filter(customer=customer, posted=True)
        .values_list("currency_id", flat=True)
        .distinct()
    )
    if len(used) > 1:
        raise ValidationError(
            f"{customer} has posted invoices in more than one currency; ask for a "
            "statement in a specific currency."
        )
    return Currency.objects.filter(pk=used.pop()).first() if used else None


def customer_statement(customer, as_of=None, since=None, currency=None):
    """
    An open-item statement: every movement on the customer's account, with
    a running balance that foots to what they owe.

    Open-item rather than balance-forward. B2B customers reconcile by
    matching invoices to remittances, and a balance-forward statement
    (opening balance, period movements, closing balance) throws away the
    invoice-level detail that makes that possible. `since` still gives an
    opening balance, so the period view is available without losing it.

    Deliberately derived rather than a stored document: a statement is a
    view of the ledger at a date, and storing one would create a second
    copy of the truth that goes stale the moment anything settles.
    """
    as_of = to_date(as_of) or timezone.now().date()
    since = to_date(since)
    currency = _statement_currency(customer, currency)

    entries = []
    invoices = (
        Invoice.objects.filter(customer=customer, posted=True, currency=currency)
        .prefetch_related(
            "lines__taxes", "payment_allocations__payment", "write_offs",
            "deposit_applications__deposit",
        )
    )
    for invoice in invoices:
        date = to_date(invoice.invoice_date)
        if date > as_of:
            continue
        if invoice.is_credit_note():
            entries.append(StatementEntry(
                date, "Credit note", invoice.number,
                f"Credit against {invoice.credits.number}", credit=invoice.total(),
            ))
            continue

        kind = "Down payment" if invoice.is_down_payment else "Invoice"
        entries.append(StatementEntry(
            date, kind, invoice.number, invoice.reference or "", debit=invoice.total()
        ))

        for allocation in invoice.payment_allocations.all():
            paid_on = to_date(allocation.payment.payment_date)
            if paid_on > as_of:
                continue
            entries.append(StatementEntry(
                paid_on, "Payment", allocation.payment.number or "",
                f"Against {invoice.number}", credit=allocation.amount,
            ))
        for application in invoice.deposit_applications.all():
            if to_date(application.date) > as_of:
                continue
            entries.append(StatementEntry(
                to_date(application.date), "Deposit applied", application.deposit.number,
                f"Against {invoice.number}", credit=application.amount,
            ))
        for write_off in invoice.write_offs.all():
            if to_date(write_off.date) <= as_of:
                entries.append(StatementEntry(
                    to_date(write_off.date), "Written off", invoice.number,
                    write_off.reason or "", credit=write_off.amount,
                ))
            # A recovery puts the debt back, so it has to appear or the
            # statement stops footing to what the customer actually owes.
            if write_off.is_recovered() and to_date(write_off.recovered_entry.date) <= as_of:
                entries.append(StatementEntry(
                    to_date(write_off.recovered_entry.date), "Write-off reversed",
                    invoice.number, "", debit=write_off.amount,
                ))
        if invoice.settlement_discount_amount and invoice.settlement_discount_entry_id:
            discounted_on = to_date(invoice.settlement_discount_entry.date)
            if discounted_on <= as_of:
                entries.append(StatementEntry(
                    discounted_on, "Settlement discount", invoice.number, "",
                    credit=invoice.settlement_discount_amount,
                ))

    entries.sort(key=lambda entry: (entry.date, entry.kind, entry.reference))

    opening = Decimal("0")
    shown = []
    running = Decimal("0")
    for entry in entries:
        if since and entry.date < since:
            opening += entry.debit - entry.credit
            continue
        shown.append(entry)
    running = opening
    for entry in shown:
        running += entry.debit - entry.credit
        entry.balance = running

    return {
        "customer": customer,
        "currency": currency,
        "as_of": as_of,
        "since": since,
        "opening_balance": opening,
        "entries": shown,
        "closing_balance": running,
        "overdue": sum(
            (invoice.amount_due() for invoice in invoices if invoice.is_overdue(as_of)),
            Decimal("0"),
        ),
    }


def render_statement_pdf(statement):
    from .documents import render_statement_pdf as _render

    return _render(statement)


def email_statement(customer, as_of=None, since=None, currency=None, to=None):
    """Send a customer their statement as a PDF. Returns the address used."""
    from django.core.mail import EmailMessage

    statement = customer_statement(customer, as_of=as_of, since=since, currency=currency)
    recipient = to or _statement_recipient(customer)
    if not recipient:
        raise ValidationError(
            f"{customer} has no email address on the party or its primary contact."
        )

    company = Company.get()
    message = EmailMessage(
        subject=f"Statement of account from {company.name}",
        body=(
            f"Dear {customer.name},\n\n"
            f"Please find your statement as at {statement['as_of']:%d %b %Y} attached. "
            f"The balance outstanding is {statement['closing_balance']}.\n\n"
            f"Regards,\n{company.name}\n"
        ),
        to=[recipient],
    )
    message.attach(
        f"statement-{customer.code}-{statement['as_of']:%Y%m%d}.pdf",
        render_statement_pdf(statement),
        "application/pdf",
    )
    message.send()
    return recipient


def _statement_recipient(customer):
    contact = customer.primary_contact()
    if contact and contact.email:
        return contact.email
    return customer.email or ""


def send_statements(as_of=None, since=None, customers=None, send=True):
    """
    Statement run: every customer with a balance gets one.

    Returns (sent, skipped). Skipped means no email address — reported
    rather than swallowed, the same as dunning, because a customer who is
    silently never sent a statement is a customer who never pays.
    """
    as_of = to_date(as_of) or timezone.now().date()
    if customers is None:
        customers = Party.objects.filter(
            role_assignments__role=PartyRole.CUSTOMER
        ).distinct()

    sent, skipped = [], []
    for customer in customers:
        try:
            statement = customer_statement(customer, as_of=as_of, since=since)
        except ValidationError:
            skipped.append(customer)
            continue
        if statement["closing_balance"] <= 0:
            continue
        if send and not _statement_recipient(customer):
            skipped.append(customer)
            continue
        if send:
            email_statement(customer, as_of=as_of, since=since,
                            currency=statement["currency"])
        sent.append(statement)
    return sent, skipped


AGING_BUCKETS = ((1, 30), (31, 60), (61, 90))


def ar_aging(as_of=None):
    """
    Outstanding customer invoices bucketed by how overdue they are.

    Note: amount_due is computed per invoice in Python rather than
    annotated in SQL, so this is fine for reporting over thousands of
    invoices but would need an annotated query at much larger volumes.
    """
    as_of = to_date(as_of) or timezone.now().date()
    buckets = {"current": [], "1-30": [], "31-60": [], "61-90": [], "90+": []}

    invoices = (
        Invoice.objects.filter(posted=True, credits__isnull=True)
        .prefetch_related("lines__taxes", "payment_allocations__payment", "credit_notes__lines__taxes")
    )
    for invoice in invoices:
        if invoice.amount_due() <= 0:
            continue
        # A row per outstanding installment, not per invoice: on a 50/50
        # term the deposit can be badly overdue while the balance is not
        # due for another month, and showing one line for the whole
        # invoice would put all of it in the wrong bucket either way.
        for row in invoice.installments():
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
                "invoice": invoice,
                "due_date": row["due_date"],
                "days_overdue": max(days, 0),
                "amount_due": row["outstanding"],
            })

    return {
        key: {
            "count": len(entries),
            "total": sum((entry["amount_due"] for entry in entries), Decimal("0")),
            "invoices": entries,
        }
        for key, entries in buckets.items()
    }


class Delivery(AuditModel):
    """
    Outbound shipment — the mirror of Purchasing's GoodsReceipt. Posting
    creates negative StockMovement rows so selling stock actually
    decrements it. Same posted/immutable/reverse pattern as everything
    else: a wrong shipment is corrected with create_return(), which puts
    the goods back rather than editing history.
    """

    number = models.CharField(max_length=32, blank=True, editable=False)
    sales_order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name="deliveries")
    delivery_date = models.DateField()
    reference = models.CharField(max_length=64, blank=True)
    shipping_address = models.ForeignKey(
        Address, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    reverses = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by",
        help_text="Set when this is a customer return of an earlier delivery.",
    )
    backorder_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="backorders",
        help_text="Set when this delivery carries what an earlier shipment left behind.",
    )
    posted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "deliveries"
        ordering = ["-delivery_date", "-id"]
        permissions = [("post_delivery", "Can post deliveries and customer returns")]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""), name="unique_delivery_number"
            )
        ]

    def __str__(self):
        kind = "RET" if self.reverses_id else "DO"
        return f"{self.number or f'{kind}-draft-{self.pk}'} for {self.sales_order}"

    def is_return(self):
        return bool(self.reverses_id)

    def _was_posted_in_db(self):
        if not self.pk:
            return False
        return Delivery.objects.filter(pk=self.pk, posted=True).exists()

    def save(self, *args, **kwargs):
        if self._was_posted_in_db():
            raise ValidationError(
                "This delivery is posted and immutable. Create a customer return instead."
            )
        if self._state.adding and not self.shipping_address_id and self.sales_order_id:
            self.shipping_address = self.sales_order.shipping_address
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.posted:
            raise ValidationError(
                "Posted deliveries cannot be deleted. Create a customer return instead."
            )
        super().delete(*args, **kwargs)

    @transaction.atomic
    def post(self):
        if self.posted:
            raise ValidationError("This delivery is already posted.")
        lines = list(self.lines.all())
        if not lines:
            raise ValidationError("Cannot post a delivery with no lines.")

        is_return = self.is_return()
        if is_return and not self.reverses.posted:
            raise ValidationError("Cannot return an unposted delivery.")
        if not is_return and self.sales_order.status != OrderStatus.CONFIRMED:
            raise ValidationError(
                f"Only a confirmed order can be shipped; this one is {self.sales_order.status}."
            )

        if not is_return:
            for line in lines:
                already_shipped = line.order_line.quantity_shipped()
                if already_shipped + line.quantity_shipped > line.order_line.quantity:
                    raise ValidationError(
                        f"Shipping {line.quantity_shipped} of {line.order_line.item} would "
                        f"exceed the ordered quantity ({line.order_line.quantity}; "
                        f"{already_shipped} already shipped)."
                    )
                item = line.order_line.item
                if item.track_inventory and not line.warehouse.allow_negative_stock:
                    on_hand = item.on_hand_at(line.warehouse)
                    if line.quantity_shipped > on_hand:
                        raise ValidationError(
                            f"Only {on_hand} of {item} on hand at {line.warehouse}; "
                            f"cannot ship {line.quantity_shipped}. Allow negative stock on the "
                            f"warehouse if backorders are expected."
                        )

        self.delivery_date = to_date(self.delivery_date)
        if not self.number:
            self.number = DocumentSequence.next_for(
                "sales.delivery" if not is_return else "sales.customer_return",
                self.delivery_date,
                name="Deliveries" if not is_return else "Customer Returns",
                prefix="DO-" if not is_return else "RET-",
            )

        valued = []
        for line in lines:
            # Services and non-stocked items must never touch stock levels.
            item = line.order_line.item
            if not item.track_inventory:
                continue
            movement_type = MovementType.RECEIPT if is_return else MovementType.ISSUE
            quantity = line.quantity_shipped if is_return else -line.quantity_shipped
            # A return reverses at the cost the original shipment used, so the
            # two entries cancel exactly instead of drifting with the average.
            unit_cost = line.unit_cost
            if unit_cost is None:
                unit_cost = item.average_cost_at(line.warehouse)
                line.unit_cost = unit_cost
                super(DeliveryLine, line).save(update_fields=["unit_cost", "updated_at"])
            StockMovement.objects.create(
                item=item,
                warehouse=line.warehouse,
                movement_type=movement_type,
                quantity=quantity,
                unit_cost=unit_cost,
                reference=self.number,
                occurred_at=timezone.now(),
                notes=(
                    f"{'Customer return for' if is_return else 'Delivery for'} "
                    f"{self.sales_order} ({self.number})"
                ),
            )
            valued.append((item, line.quantity_shipped * unit_cost))

        post_inventory_entry(
            valued,
            date=self.delivery_date,
            reference=self.number,
            memo=(
                f"{'Customer return for' if is_return else 'Cost of goods sold for'} "
                f"{self.sales_order} ({self.number})"
            ),
            direction="out",
            reverse=is_return,
        )

        self.posted = True
        self.posted_at = timezone.now()
        super(Delivery, self).save(
            update_fields=["number", "delivery_date", "posted", "posted_at", "updated_at"]
        )

    def _credit_returned_goods(self):
        """
        Credit the invoices that billed the returned goods. A delivery's
        quantities may have been spread over several invoices, so the
        returned quantity is allocated oldest-invoice-first, and one credit
        note is raised per affected invoice.
        """
        allocations = defaultdict(dict)
        for line in self.lines.all():
            remaining = line.quantity_shipped
            invoice_lines = InvoiceLine.objects.filter(
                order_line=line.order_line,
                invoice__posted=True,
                invoice__credits__isnull=True,
            ).order_by("invoice__invoice_date", "invoice_id")
            for invoice_line in invoice_lines:
                if remaining <= 0:
                    break
                available = invoice_line.quantity_creditable()
                if available <= 0:
                    continue
                taken = min(available, remaining)
                per_invoice = allocations[invoice_line.invoice]
                per_invoice[invoice_line] = per_invoice.get(invoice_line, Decimal("0")) + taken
                remaining -= taken

        return [
            invoice.create_credit_note(
                memo=f"Goods returned on {self.number}", quantities=quantities
            )
            for invoice, quantities in allocations.items()
        ]

    def shortfall(self):
        """
        What this delivery's order lines still owe the customer, as
        {order_line: quantity}. A partial shipment leaves a remainder that
        should be a document someone can see and plan against, not an
        implicit gap between two numbers.
        """
        outstanding = {}
        for line in self.lines.all():
            remaining = line.order_line.quantity - line.order_line.quantity_shipped()
            if remaining > 0:
                outstanding[line.order_line] = remaining
        return outstanding

    @transaction.atomic
    def create_backorder(self, delivery_date=None):
        """Raise a draft delivery for whatever this shipment left behind."""
        if not self.posted:
            raise ValidationError("Only a posted delivery can leave a backorder.")
        if self.is_return():
            raise ValidationError("A return does not leave a backorder.")
        if self.backorders.exists():
            raise ValidationError("This delivery already has a backorder.")

        outstanding = self.shortfall()
        if not outstanding:
            raise ValidationError("This delivery was complete; there is nothing on backorder.")

        backorder = Delivery.objects.create(
            sales_order=self.sales_order,
            delivery_date=delivery_date or timezone.now().date(),
            reference=self.reference,
            shipping_address=self.shipping_address,
            backorder_of=self,
        )
        for order_line, remaining in outstanding.items():
            DeliveryLine.objects.create(
                delivery=backorder, order_line=order_line,
                warehouse=self.lines.filter(order_line=order_line).first().warehouse,
                quantity_shipped=remaining,
            )
        return backorder

    @transaction.atomic
    def create_return(self, credit_invoices=True):
        """
        Take goods back: reverse the stock movement and, unless this is a
        replacement rather than a refund, credit whatever was invoiced for
        them. The credit notes raised are attached to the returned delivery
        as `credit_notes_created`.
        """
        if not self.posted:
            raise ValidationError("Only a posted delivery can be returned.")
        if self.is_return():
            raise ValidationError("Cannot return a return.")
        if self.reversed_by.exists():
            raise ValidationError("This delivery has already been returned.")

        customer_return = Delivery.objects.create(
            sales_order=self.sales_order,
            delivery_date=timezone.now().date(),
            reference=self.reference,
            shipping_address=self.shipping_address,
            reverses=self,
        )
        for line in self.lines.all():
            DeliveryLine.objects.create(
                delivery=customer_return,
                order_line=line.order_line,
                warehouse=line.warehouse,
                quantity_shipped=line.quantity_shipped,
                unit_cost=line.unit_cost,
            )
        customer_return.post()
        customer_return.credit_notes_created = (
            self._credit_returned_goods() if credit_invoices else []
        )
        return customer_return


class DeliveryLine(AuditModel):
    delivery = models.ForeignKey(Delivery, related_name="lines", on_delete=models.CASCADE)
    order_line = models.ForeignKey(
        SalesOrderLine, on_delete=models.PROTECT, related_name="delivery_lines"
    )
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+")
    quantity_shipped = models.DecimalField(max_digits=18, decimal_places=4)
    unit_cost = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True, editable=False,
        help_text="Weighted average cost at the moment of shipping, frozen so a return reverses exactly.",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(check=Q(quantity_shipped__gt=0), name="shipped_quantity_positive")
        ]

    def __str__(self):
        return f"{self.order_line.label()} x{self.quantity_shipped} from {self.warehouse}"

    def clean(self):
        if self.order_line_id and self.order_line.is_charge():
            raise ValidationError(
                f"'{self.order_line.charge}' is a charge, not goods; there is nothing to ship."
            )
        if self.order_line_id and self.delivery_id and (
            self.order_line.order_id != self.delivery.sales_order_id
        ):
            raise ValidationError("This line's order_line must belong to the delivery's sales_order.")

    def save(self, *args, **kwargs):
        if self.delivery_id and Delivery.objects.filter(pk=self.delivery_id, posted=True).exists():
            raise ValidationError(
                "Cannot modify a line on a posted delivery. Create a customer return instead."
            )
        # In save() rather than only clean(): deliveries are built in code,
        # where nothing calls full_clean() for us.
        if self.order_line_id and self.order_line.is_charge():
            raise ValidationError(
                f"'{self.order_line.charge}' is a charge, not goods; there is nothing to ship."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.delivery.posted:
            raise ValidationError(
                "Cannot delete a line on a posted delivery. Create a customer return instead."
            )
        super().delete(*args, **kwargs)


class DunningLevel(AuditModel):
    """
    One step in the chase sequence: how overdue an invoice must be before
    this reminder applies, and what it says. Levels are ordered by
    days_overdue, and an invoice only ever advances — the same reminder is
    never sent twice for the same invoice.
    """

    name = models.CharField(max_length=64, unique=True)
    days_overdue = models.PositiveIntegerField(
        help_text="Send once the invoice is at least this many days past due."
    )
    subject = models.CharField(
        max_length=200, default="Reminder: invoice {number} is overdue",
        help_text="Supports {number}, {customer}, {days}, {amount}.",
    )
    body = models.TextField(
        default=(
            "Dear {customer},\n\n"
            "Invoice {number} for {amount} was due on {due_date} and is now {days} days "
            "overdue.\n\nPlease arrange payment.\n"
        ),
        help_text="Supports {number}, {customer}, {days}, {amount}, {due_date}.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["days_overdue"]
        constraints = [
            models.UniqueConstraint(fields=["days_overdue"], name="unique_dunning_threshold")
        ]

    def __str__(self):
        return f"{self.name} (day {self.days_overdue})"

    def render(self, invoice, days_overdue):
        context = {
            "number": invoice.number,
            "customer": invoice.customer.name,
            "days": days_overdue,
            "amount": f"{invoice.amount_due():,.2f}",
            "due_date": invoice.due_date.strftime("%d %b %Y") if invoice.due_date else "",
        }
        return self.subject.format(**context), self.body.format(**context)


class DunningNotice(AuditModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="dunning_notices")
    level = models.ForeignKey(DunningLevel, on_delete=models.PROTECT, related_name="notices")
    days_overdue = models.PositiveIntegerField()
    amount_due = models.DecimalField(max_digits=18, decimal_places=2)
    sent_to = models.EmailField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["invoice", "level"], name="one_notice_per_invoice_and_level"
            )
        ]

    def __str__(self):
        return f"{self.level.name} for {self.invoice}"


def run_dunning(as_of=None, send=True):
    """
    Walk overdue invoices and raise the reminders that are now due.

    An invoice gets each level at most once, and only the highest level it
    has reached — jumping from nothing to the 60-day notice shouldn't also
    send the 7-day one.
    """
    as_of = to_date(as_of) or timezone.now().date()
    levels = list(DunningLevel.objects.filter(is_active=True).order_by("-days_overdue"))
    if not levels:
        return []

    notices = []
    undeliverable = []
    invoices = (
        Invoice.objects.filter(posted=True, credits__isnull=True)
        .prefetch_related("lines__taxes", "payment_allocations__payment", "credit_notes__lines__taxes",
                          "dunning_notices")
    )
    for invoice in invoices:
        days = invoice.days_overdue(as_of)
        if days <= 0 or invoice.amount_due() <= 0:
            continue
        already_sent = {notice.level_id for notice in invoice.dunning_notices.all()}
        due_level = next(
            (level for level in levels if days >= level.days_overdue and level.pk not in already_sent),
            None,
        )
        if due_level is None:
            continue

        recipient = invoice.recipient_email()
        if send and not recipient:
            # Recording a notice we never delivered would mark this level
            # done and the customer would never be chased at it again.
            undeliverable.append(invoice)
            continue

        notice = DunningNotice.objects.create(
            invoice=invoice, level=due_level, days_overdue=days,
            # What is actually late, not the whole balance. On a 50/50
            # term, chasing a customer for a balance that is not due for
            # another month is how you lose the argument about the half
            # that is.
            amount_due=invoice.amount_overdue(as_of),
        )
        if send:
            from django.core.mail import EmailMessage

            subject, body = due_level.render(invoice, days)
            EmailMessage(subject=subject, body=body, to=[recipient]).send()
            notice.sent_to = recipient
            notice.sent_at = timezone.now()
            notice.save(update_fields=["sent_to", "sent_at", "updated_at"])
        notices.append(notice)

    if undeliverable:
        logger.warning(
            "Dunning skipped %d overdue invoice(s) with no email address: %s",
            len(undeliverable), ", ".join(invoice.number for invoice in undeliverable),
        )
    return notices


def revenue_report(date_from=None, date_to=None, group_by="customer"):
    """
    Net revenue over a period, from posted invoices less credit notes.

    Reads the documents rather than the ledger so it can group by customer
    or item, which the ledger doesn't record per line.
    """
    date_from = to_date(date_from)
    date_to = to_date(date_to)

    # A down payment credits a liability, not revenue — counting it here
    # would book the sale twice, once on the deposit and once on the
    # invoice that draws it down.
    invoices = Invoice.objects.filter(posted=True, is_down_payment=False).prefetch_related(
        "lines__taxes", "lines__item"
    ).select_related("customer")
    if date_from:
        invoices = invoices.filter(invoice_date__gte=date_from)
    if date_to:
        invoices = invoices.filter(invoice_date__lte=date_to)

    totals = defaultdict(lambda: {"net": Decimal("0"), "tax": Decimal("0"), "quantity": Decimal("0")})
    for invoice in invoices:
        # A credit note reduces revenue, so its lines count negative.
        sign = Decimal("-1") if invoice.is_credit_note() else Decimal("1")
        # Document-level tax rounding puts a line's tax somewhere other
        # than compute_taxes() would put it, so read the document's own
        # allocation or this report drifts from the invoice by pennies.
        line_taxes = invoice.line_tax_amounts()
        for line in invoice.lines.all():
            if group_by == "customer":
                key = str(invoice.customer)
            elif group_by == "item":
                # A charge groups under the charge, not its free-text
                # description, so "Shipping" and "Shipping (expedited)"
                # don't land in separate rows.
                if line.item_id:
                    key = str(line.item)
                elif line.is_charge():
                    key = str(line.charge)
                else:
                    key = line.description or "—"
            elif group_by == "month":
                key = invoice.invoice_date.strftime("%Y-%m")
            else:
                raise ValueError(f"Unsupported grouping: {group_by}")
            bucket = totals[key]
            bucket["net"] += sign * line.net_amount()
            bucket["tax"] += sign * sum(
                (amount for _, amount in line_taxes.get(line, ())), Decimal("0")
            )
            bucket["quantity"] += sign * line.quantity

    return [
        {
            "key": key,
            "net": amounts["net"],
            "tax": amounts["tax"],
            "gross": amounts["net"] + amounts["tax"],
            "quantity": amounts["quantity"],
        }
        for key, amounts in sorted(totals.items(), key=lambda pair: -pair[1]["net"])
    ]


class QuotationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SENT = "sent", "Sent"
    ACCEPTED = "accepted", "Accepted"
    DECLINED = "declined", "Declined"
    EXPIRED = "expired", "Expired"
    SUPERSEDED = "superseded", "Superseded by a revision"


class Quotation(TaxedDocumentMixin, AuditModel):
    """
    A priced offer that hasn't been committed to. Kept separate from
    SalesOrder rather than folded in as another status: a quotation can
    expire and be declined, neither of which an order does, and an order
    carries fulfilment state a quote has no business having.
    """

    number = models.CharField(max_length=32, blank=True, editable=False)
    customer = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="quotations")
    quotation_date = models.DateField()
    valid_until = models.DateField(
        null=True, blank=True, help_text="After this date the quote can no longer be accepted."
    )
    reference = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=16, choices=QuotationStatus.choices, default=QuotationStatus.DRAFT
    )
    currency = models.ForeignKey(
        Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    payment_terms = models.ForeignKey(
        PaymentTerms, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    billing_address = models.ForeignKey(
        Address, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    shipping_address = models.ForeignKey(
        Address, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    sales_order = models.ForeignKey(
        SalesOrder, null=True, blank=True, on_delete=models.PROTECT,
        related_name="quotations", editable=False,
        help_text="The order this quote became, once accepted.",
    )
    sales_rep = models.ForeignKey(
        Party, null=True, blank=True, on_delete=models.PROTECT, related_name="%(class)s_sales",
        help_text="The employee credited with this sale.",
    )
    revision = models.PositiveSmallIntegerField(default=1, editable=False)
    revision_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT,
        related_name="revisions", editable=False,
        help_text="The quotation this one revises.",
    )
    sent_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-quotation_date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""), name="unique_quotation_number"
            )
        ]

    def __str__(self):
        return f"{self.number or f'QT-draft-{self.pk}'} {self.customer}"

    def clean(self):
        _require_customer_role(self.customer)
        if self.valid_until and self.valid_until < to_date(self.quotation_date):
            raise ValidationError("valid_until cannot be before the quotation date.")

    # Once a quote has left the building it is a record of what the customer
    # was told; changing it means issuing a revision, not editing history.
    SETTLED_STATUSES = (
        QuotationStatus.SENT,
        QuotationStatus.ACCEPTED,
        QuotationStatus.SUPERSEDED,
    )

    def _is_settled_in_db(self):
        if not self.pk:
            return False
        return Quotation.objects.filter(
            pk=self.pk, status__in=self.SETTLED_STATUSES
        ).exists()

    # The only fields the accept()/send paths themselves write afterwards.
    INTERNAL_FIELDS = {"number", "status", "sales_order", "sent_at", "updated_at"}

    def save(self, *args, **kwargs):
        updating = set(kwargs.get("update_fields") or [])
        internal_only = bool(updating) and updating <= self.INTERNAL_FIELDS
        if self._is_settled_in_db() and not internal_only:
            raise ValidationError(
                "This quotation has already gone to the customer and records what they "
                "were told. Use create_revision() to change it."
            )
        if self._state.adding and self.customer_id:
            customer = self.customer
            self.currency = self.currency or customer.default_currency
            self.payment_terms = self.payment_terms or customer.payment_terms
            self.billing_address = self.billing_address or customer.billing_address()
            self.shipping_address = self.shipping_address or customer.shipping_address()
        super().save(*args, **kwargs)

    def render_pdf(self):
        from .documents import render_quotation_pdf

        return render_quotation_pdf(self)

    def recipient_email(self):
        contact = self.customer.primary_contact()
        if contact and contact.email:
            return contact.email
        return self.customer.email or ""

    def email_to_customer(self, to=None, subject=None, body=None):
        """Send the quote as a PDF and mark it sent."""
        from django.core.mail import EmailMessage

        if not self.lines.exists():
            raise ValidationError("Cannot send a quotation with no lines.")
        recipient = to or self.recipient_email()
        if not recipient:
            raise ValidationError(
                f"{self.customer} has no email address on the party or its primary contact."
            )
        self._assign_number()
        company = Company.get()
        message = EmailMessage(
            subject=subject or f"Quotation {self.number} from {company.name}",
            body=body or (
                f"Dear {self.customer.name},\n\n"
                f"Please find quotation {self.number} attached"
                + (f", valid until {self.valid_until:%d %b %Y}" if self.valid_until else "")
                + f".\n\nRegards,\n{company.name}\n"
            ),
            to=[recipient],
        )
        message.attach(f"{self.number}.pdf", self.render_pdf(), "application/pdf")
        message.send()

        self.status = QuotationStatus.SENT
        self.sent_at = timezone.now()
        self.save(update_fields=["number", "status", "sent_at", "updated_at"])
        return recipient

    def has_expired(self, as_of=None):
        if not self.valid_until:
            return False
        return (to_date(as_of) or timezone.now().date()) > self.valid_until

    def base_number(self):
        """The number without its revision suffix."""
        return self.number.split("-R")[0] if self.number else ""

    def _assign_number(self):
        if self.number:
            return
        if self.revision_of_id:
            # A revision keeps the original's number and adds its revision.
            self.number = f"{self.revision_of.base_number()}-R{self.revision}"
        else:
            self.number = DocumentSequence.next_for(
                "sales.quotation", self.quotation_date, name="Quotations", prefix="QT-"
            )

    @transaction.atomic
    def create_revision(self, quotation_date=None, valid_until=None):
        """
        Supersede this quote with a fresh, editable copy. Resending with
        different terms has to leave a trail: the customer was told one
        thing and is now being told another, and both need to be on record.
        """
        if self.status == QuotationStatus.DRAFT:
            raise ValidationError(
                "This quotation hasn't gone to the customer yet — edit it directly rather "
                "than revising it."
            )
        if self.status == QuotationStatus.ACCEPTED:
            raise ValidationError("An accepted quotation cannot be revised; it became an order.")
        if self.status == QuotationStatus.SUPERSEDED:
            raise ValidationError(
                f"This quotation was already superseded by {self.revisions.first()}."
            )
        self._assign_number()

        revision = Quotation.objects.create(
            customer=self.customer,
            quotation_date=quotation_date or timezone.now().date(),
            valid_until=valid_until if valid_until is not None else self.valid_until,
            reference=self.reference,
            currency=self.currency,
            payment_terms=self.payment_terms,
            billing_address=self.billing_address,
            shipping_address=self.shipping_address,
            sales_rep=self.sales_rep,
            revision=self.revision + 1,
            revision_of=self,
        )
        for line in self.lines.all():
            revision_line = QuotationLine.objects.create(
                quotation=revision, item=line.item, charge=line.charge,
                description=line.description, uom=line.uom,
                quantity=line.quantity, unit_price=line.unit_price,
                discount_percent=line.discount_percent,
                revenue_account=line.revenue_account,
            )
            revision_line.taxes.set(line.taxes.all())

        self.status = QuotationStatus.SUPERSEDED
        super(Quotation, self).save(update_fields=["number", "status", "updated_at"])
        return revision

    @transaction.atomic
    def mark_sent(self):
        """Record that the quote went out by some other route (post, in person)."""
        if self.status not in (QuotationStatus.DRAFT, QuotationStatus.SENT):
            raise ValidationError(f"A {self.get_status_display().lower()} quote cannot be sent.")
        if not self.lines.exists():
            raise ValidationError("Cannot send a quotation with no lines.")
        self._assign_number()
        self.status = QuotationStatus.SENT
        self.sent_at = timezone.now()
        self.save(update_fields=["number", "status", "sent_at", "updated_at"])

    @transaction.atomic
    def decline(self):
        if self.status in (QuotationStatus.ACCEPTED, QuotationStatus.DECLINED):
            raise ValidationError(
                f"This quote is already {self.get_status_display().lower()}."
            )
        self.status = QuotationStatus.DECLINED
        self.save(update_fields=["status", "updated_at"])

    def accept(self, order_date=None, approve_as=None):
        """Turn an accepted quote into a confirmed sales order."""
        if self.status == QuotationStatus.ACCEPTED:
            raise ValidationError("This quotation has already been accepted.")
        if self.status == QuotationStatus.DECLINED:
            raise ValidationError("A declined quotation cannot be accepted.")
        if self.status == QuotationStatus.SUPERSEDED:
            raise ValidationError(
                "This quotation was superseded by a revision; accept that one instead."
            )
        if not self.lines.exists():
            raise ValidationError("Cannot accept a quotation with no lines.")
        # Recording the expiry has to happen outside the transaction below:
        # raising inside it would roll the status change straight back.
        if self.has_expired(order_date):
            self.status = QuotationStatus.EXPIRED
            self.save(update_fields=["status", "updated_at"])
            raise ValidationError(
                f"This quotation expired on {self.valid_until:%d %b %Y}. Re-quote instead."
            )
        return self._convert_to_order(order_date, approve_as)

    @transaction.atomic
    def _convert_to_order(self, order_date, approve_as=None):
        self._assign_number()
        order = SalesOrder.objects.create(
            customer=self.customer,
            order_date=order_date or timezone.now().date(),
            reference=self.reference,
            currency=self.currency,
            payment_terms=self.payment_terms,
            billing_address=self.billing_address,
            shipping_address=self.shipping_address,
            sales_rep=self.sales_rep,
        )
        for line in self.lines.all():
            order_line = SalesOrderLine.objects.create(
                order=order, item=line.item, charge=line.charge,
                description=line.description, uom=line.uom,
                quantity=line.quantity, unit_price=line.unit_price,
                discount_percent=line.discount_percent,
                revenue_account=line.revenue_account,
            )
            order_line.taxes.set(line.taxes.all())
        # Accepting creates and confirms in one step, so an order that
        # breaches policy has to be approved as part of it. approve_as is
        # the approving user, gated by sales.approve_order at the edge —
        # the same decision the old bypass flag made invisibly.
        if approve_as is not None and order.requires_approval():
            order.approve(by=approve_as, note="Approved on quotation acceptance")
        order.confirm()

        self.sales_order = order
        self.status = QuotationStatus.ACCEPTED
        self.save(update_fields=["number", "sales_order", "status", "updated_at"])
        return order


class QuotationLine(TaxedLineMixin, AuditModel):
    quotation = models.ForeignKey(Quotation, related_name="lines", on_delete=models.CASCADE)
    item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="quotation_lines"
    )
    charge = models.ForeignKey(
        ChargeType, null=True, blank=True, on_delete=models.PROTECT, related_name="%(class)ss",
        help_text="Set instead of an item when this line bills freight, handling or similar.",
    )
    description = models.CharField(max_length=255, blank=True)
    uom = models.ForeignKey(
        UnitOfMeasure, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    revenue_account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    taxes = models.ManyToManyField(Tax, blank=True, related_name="quotation_lines")

    def party_for_tax(self):
        return self.quotation.customer

    class Meta:
        constraints = [
            models.CheckConstraint(check=Q(quantity__gt=0), name="quote_line_quantity_positive"),
            models.CheckConstraint(check=Q(unit_price__gte=0), name="quote_line_price_not_negative"),
            models.CheckConstraint(
                check=Q(item__isnull=False, charge__isnull=True)
                | Q(item__isnull=True, charge__isnull=False),
                name="quote_line_is_item_or_charge",
            ),
        ]

    def __str__(self):
        return f"{self.label()} x{self.quantity}"

    def _quotation_is_settled(self):
        return self.quotation_id and Quotation.objects.filter(
            pk=self.quotation_id, status__in=Quotation.SETTLED_STATUSES
        ).exists()

    def delete(self, *args, **kwargs):
        if self._quotation_is_settled():
            raise ValidationError(
                "This quotation has gone to the customer; revise it instead of editing it."
            )
        super().delete(*args, **kwargs)

    def save(self, *args, **kwargs):
        if self._quotation_is_settled():
            raise ValidationError(
                "This quotation has gone to the customer; revise it instead of editing it."
            )
        if self.is_charge():
            if not self.revenue_account_id:
                self.revenue_account = self.charge.account_for(is_sale=True)
            if self.unit_price is None:
                raise ValidationError(
                    f"Give the {self.charge} charge an explicit amount; a charge has no "
                    "price list to fall back on."
                )
        elif self.unit_price is None:
            self.unit_price = resolve_price(
                self.item,
                customer=self.quotation.customer,
                quantity=self.quantity,
                currency=self.quotation.currency,
                on_date=self.quotation.quotation_date,
            )
            if self.unit_price is None:
                raise ValidationError(
                    f"No price found for {self.item}: set one on the item, add it to a "
                    "price list, or give the line an explicit unit price."
                )
        super().save(*args, **kwargs)


def _require_employee_role(party):
    if party and not party.role_assignments.filter(role=PartyRole.EMPLOYEE).exists():
        raise ValidationError(f"{party} does not have the Employee role.")


class CommissionBasis(models.TextChoices):
    INVOICED = "invoiced", "What was invoiced"
    PAID = "paid", "What was collected"


class CommissionPlan(AuditModel):
    """
    How a rep is paid. Basis matters: paying on invoiced revenue rewards
    booking a sale, paying on collected cash rewards it actually being
    paid for — a real difference when customers are slow.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    percent = models.DecimalField(
        max_digits=5, decimal_places=2, help_text="Commission rate, e.g. 2.50 for 2.5%."
    )
    basis = models.CharField(
        max_length=16, choices=CommissionBasis.choices, default=CommissionBasis.INVOICED
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(check=Q(percent__gte=0), name="commission_percent_not_negative")
        ]

    def __str__(self):
        return f"{self.name} ({self.percent}% of {self.get_basis_display().lower()})"

    def commission_on(self, amount):
        return round_money(amount * self.percent / Decimal("100"))


class SalesRep(AuditModel):
    """A Party with the EMPLOYEE role who carries a commission plan."""

    party = models.OneToOneField(Party, on_delete=models.CASCADE, related_name="sales_rep_profile")
    plan = models.ForeignKey(
        CommissionPlan, null=True, blank=True, on_delete=models.PROTECT, related_name="reps"
    )
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"Sales rep {self.party}"

    def clean(self):
        _require_employee_role(self.party)


def commission_report(date_from=None, date_to=None):
    """
    Commission earned per rep over a period.

    Each rep is measured on their own plan's basis: invoiced reps on the
    net value of what they billed, collected reps on money actually
    received against those invoices. Credit notes reduce both.
    """
    date_from = to_date(date_from)
    date_to = to_date(date_to)

    rows = []
    for rep in SalesRep.objects.filter(is_active=True).select_related("party", "plan"):
        if rep.plan is None or not rep.plan.is_active:
            continue

        # A down payment is not a sale, so it earns no commission on either
        # basis; the commission falls due on the invoice that draws it down.
        invoices = Invoice.objects.filter(
            posted=True, sales_rep=rep.party, is_down_payment=False
        ).prefetch_related("lines__taxes", "payment_allocations__payment")

        basis_amount = Decimal("0")
        if rep.plan.basis == CommissionBasis.INVOICED:
            scoped = invoices
            if date_from:
                scoped = scoped.filter(invoice_date__gte=date_from)
            if date_to:
                scoped = scoped.filter(invoice_date__lte=date_to)
            for invoice in scoped:
                sign = Decimal("-1") if invoice.is_credit_note() else Decimal("1")
                basis_amount += sign * invoice.subtotal()
        else:
            # Money in against this rep's invoices, less money handed back
            # on their credit notes: a refunded sale earns no commission.
            for invoice in invoices:
                sign = Decimal("-1") if invoice.is_credit_note() else Decimal("1")
                for allocation in invoice.payment_allocations.all():
                    paid_on = allocation.payment.payment_date
                    if date_from and paid_on < date_from:
                        continue
                    if date_to and paid_on > date_to:
                        continue
                    basis_amount += sign * allocation.amount

        if not basis_amount:
            continue
        rows.append({
            "rep": str(rep.party),
            "plan": rep.plan.code,
            "basis": rep.plan.basis,
            "basis_amount": round_money(basis_amount),
            "percent": rep.plan.percent,
            "commission": rep.plan.commission_on(basis_amount),
        })
    return sorted(rows, key=lambda row: -row["commission"])


class RecurrenceInterval(models.TextChoices):
    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"
    QUARTERLY = "quarterly", "Quarterly"
    YEARLY = "yearly", "Yearly"


def add_interval(start, interval, count=1, anchor_day=None):
    """
    Advance a date by `count` intervals.

    `anchor_day` is the day the series is really anchored to. Without it a
    schedule starting on the 31st clamps to the 28th in February and then
    stays there — the billing date silently walks backwards. Anchoring
    means Jan 31 -> Feb 28 -> Mar 31.
    """
    if interval == RecurrenceInterval.WEEKLY:
        return start + datetime.timedelta(weeks=count)
    months = {
        RecurrenceInterval.MONTHLY: 1,
        RecurrenceInterval.QUARTERLY: 3,
        RecurrenceInterval.YEARLY: 12,
    }[interval] * count
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(anchor_day or start.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


class RecurringInvoice(AuditModel):
    """
    A template that issues the same invoice on a schedule — a retainer, a
    subscription, a maintenance contract. Generation is driven by
    next_run_date rather than by recomputing from the start each time, so
    a run that is late catches up one invoice at a time instead of
    silently skipping periods.
    """

    code = models.CharField(max_length=32, unique=True)
    customer = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="recurring_invoices")
    receivable_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    currency = models.ForeignKey(
        Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    payment_terms = models.ForeignKey(
        PaymentTerms, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    sales_rep = models.ForeignKey(
        Party, null=True, blank=True, on_delete=models.PROTECT, related_name="recurring_sales"
    )
    interval = models.CharField(
        max_length=16, choices=RecurrenceInterval.choices, default=RecurrenceInterval.MONTHLY
    )
    interval_count = models.PositiveSmallIntegerField(
        default=1, help_text="Every N intervals, e.g. 2 monthly = every other month."
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True, help_text="Blank runs indefinitely.")
    next_run_date = models.DateField(null=True, blank=True)
    auto_post = models.BooleanField(
        default=False, help_text="Post generated invoices immediately instead of leaving drafts."
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(check=Q(interval_count__gt=0), name="interval_count_positive")
        ]

    def __str__(self):
        return f"{self.code} ({self.customer})"

    def clean(self):
        _require_customer_role(self.customer)
        if self.end_date and self.end_date < to_date(self.start_date):
            raise ValidationError("end_date cannot be before start_date.")

    def save(self, *args, **kwargs):
        if self.next_run_date is None:
            self.next_run_date = to_date(self.start_date)
        if self._state.adding and self.customer_id:
            self.currency = self.currency or self.customer.default_currency
            self.payment_terms = self.payment_terms or self.customer.payment_terms
        super().save(*args, **kwargs)

    def has_finished(self):
        return bool(self.end_date and self.next_run_date and self.next_run_date > self.end_date)

    @transaction.atomic
    def generate_one(self, on_date=None):
        """Issue the next invoice in the series and advance the schedule."""
        if not self.is_active:
            raise ValidationError("This schedule is not active.")
        if not self.lines.exists():
            raise ValidationError("This schedule has no lines to invoice.")
        if self.has_finished():
            raise ValidationError("This schedule has reached its end date.")

        invoice_date = to_date(on_date) or self.next_run_date
        invoice = Invoice.objects.create(
            customer=self.customer, invoice_date=invoice_date,
            receivable_account=self.receivable_account, currency=self.currency,
            payment_terms=self.payment_terms, sales_rep=self.sales_rep,
            reference=self.code,
        )
        for line in self.lines.all():
            invoice_line = InvoiceLine.objects.create(
                invoice=invoice, item=line.item, description=line.description,
                quantity=line.quantity, unit_price=line.unit_price,
                discount_percent=line.discount_percent, revenue_account=line.revenue_account,
            )
            invoice_line.taxes.set(line.taxes.all())

        if self.auto_post:
            invoice.post()

        self.next_run_date = add_interval(
            self.next_run_date, self.interval, self.interval_count,
            anchor_day=to_date(self.start_date).day,
        )
        self.save(update_fields=["next_run_date", "updated_at"])
        return invoice


class RecurringInvoiceLine(TaxedLineMixin, AuditModel):
    schedule = models.ForeignKey(
        RecurringInvoice, related_name="lines", on_delete=models.CASCADE
    )
    item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="recurring_lines"
    )
    description = models.CharField(max_length=255, blank=True)
    revenue_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    taxes = models.ManyToManyField(Tax, blank=True, related_name="recurring_lines")

    class Meta:
        constraints = [
            models.CheckConstraint(check=Q(quantity__gt=0), name="recurring_quantity_positive"),
        ]

    def party_for_tax(self):
        return self.schedule.customer

    def __str__(self):
        return f"{self.item or self.description} x{self.quantity}"


def generate_due_invoices(as_of=None):
    """
    Issue every invoice now due across all active schedules.

    A schedule that is several periods behind catches up one invoice per
    period rather than issuing a single lump: each period genuinely
    happened and should be billed separately.
    """
    as_of = to_date(as_of) or timezone.now().date()
    issued = []
    for schedule in RecurringInvoice.objects.filter(is_active=True).prefetch_related("lines"):
        if not schedule.lines.exists():
            continue
        while (
            schedule.next_run_date
            and schedule.next_run_date <= as_of
            and not schedule.has_finished()
        ):
            issued.append(schedule.generate_one())
    return issued
