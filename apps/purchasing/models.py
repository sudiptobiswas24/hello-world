import datetime
from collections import defaultdict
from decimal import ROUND_CEILING, Decimal

from django.conf import settings
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
from apps.core.approvals import ApprovableMixin, ApprovalStatus
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
from apps.inventory.models import (
    Item,
    MovementType,
    StockMovement,
    Warehouse,
    plan_putaway,
)
from apps.accounting.settlement import (
    amount_overdue,
    installment_schedule,
    oldest_overdue,
    post_settlement_fx,
)
from apps.inventory.valuation import (
    cogs_account_for,
    grni_account,
    inventory_account_for,
    post_inventory_entry,
)

from .pricing import preferred_vendor, resolve_lead_time, resolve_purchase_price


def _require_employee_role(party):
    if party and not party.role_assignments.filter(role=PartyRole.EMPLOYEE).exists():
        raise ValidationError(f"{party} does not have the Employee role.")


def _require_vendor_role(party):
    if party and not party.role_assignments.filter(role=PartyRole.VENDOR).exists():
        raise ValidationError(f"{party} does not have the Vendor role.")


class VendorPrice(AuditModel):
    """
    A price this vendor has agreed for this item — the purchase-side
    mirror of a sales price list.

    Quantity breaks and validity dates are the point. Without them
    "agreed price" means whatever was typed on the last order, which is
    exactly what the three-way match is supposed to be checking against.
    """

    vendor = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="vendor_prices")
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="vendor_prices")
    currency = models.ForeignKey(
        Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    min_quantity = models.DecimalField(
        max_digits=18, decimal_places=4, default=Decimal("0"),
        help_text="Smallest order this price applies to — the quantity break.",
    )
    vendor_item_code = models.CharField(
        max_length=64, blank=True,
        help_text="The vendor's own part number, which is what their invoice will quote.",
    )
    lead_time_days = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Days from order to delivery, as agreed."
    )
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    is_preferred = models.BooleanField(
        default=False, help_text="Buy this item from this vendor by default."
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["item", "vendor", "-min_quantity"]
        constraints = [
            models.CheckConstraint(check=Q(unit_price__gte=0), name="vendor_price_not_negative"),
            models.CheckConstraint(
                check=Q(min_quantity__gte=0), name="vendor_price_min_quantity_not_negative"
            ),
        ]

    def __str__(self):
        return f"{self.item} from {self.vendor} @ {self.unit_price}"

    def clean(self):
        _require_vendor_role(self.vendor)
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValidationError("valid_to cannot be before valid_from.")

    def covers(self, on_date=None):
        on_date = to_date(on_date) or timezone.now().date()
        if self.valid_from and on_date < self.valid_from:
            return False
        if self.valid_to and on_date > self.valid_to:
            return False
        return True

    def covers_quantity(self, quantity):
        if quantity is None:
            return self.min_quantity <= 0
        return Decimal(quantity) >= self.min_quantity


class RfqStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SENT = "sent", "Sent"
    AWARDED = "awarded", "Awarded"
    CANCELLED = "cancelled", "Cancelled"


class RequestForQuotation(AuditModel):
    """
    The same requirement put to several vendors, so their answers can be
    compared before anyone commits.

    Not a purchase order in a different state, which is how Odoo models
    it. An RFQ goes to *many* vendors at once, collects prices that are
    not agreements, and ends in one of them being awarded while the rest
    are declined. Folding that into a purchase order would mean either a
    fake order per vendor — every one of which pollutes the open-order
    reports — or a single order whose vendor keeps changing, which loses
    the comparison that was the point.
    """

    number = models.CharField(max_length=32, blank=True, editable=False)
    requisition = models.ForeignKey(
        "PurchaseRequisition", null=True, blank=True, on_delete=models.PROTECT,
        related_name="rfqs",
    )
    issue_date = models.DateField()
    response_due = models.DateField(null=True, blank=True)
    currency = models.ForeignKey(
        Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=RfqStatus.choices, default=RfqStatus.DRAFT)

    class Meta:
        ordering = ["-issue_date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""), name="unique_rfq_number"
            )
        ]

    def __str__(self):
        return self.number or f"RFQ-draft-{self.pk}"

    @transaction.atomic
    def issue(self):
        if self.status != RfqStatus.DRAFT:
            raise ValidationError(f"This RFQ is already {self.get_status_display().lower()}.")
        if not self.lines.exists():
            raise ValidationError("Cannot issue an RFQ with no lines.")
        if not self.invited.exists():
            raise ValidationError("Cannot issue an RFQ with no vendors invited.")
        if not self.number:
            self.number = DocumentSequence.next_for(
                "purchasing.rfq", self.issue_date,
                name="Requests for Quotation", prefix="RFQ-",
            )
        self.status = RfqStatus.SENT
        self.save(update_fields=["number", "status", "updated_at"])

    def cancel(self):
        if self.status == RfqStatus.AWARDED:
            raise ValidationError("This RFQ has been awarded; cancel the purchase order.")
        if self.status == RfqStatus.CANCELLED:
            raise ValidationError("This RFQ is already cancelled.")
        self.status = RfqStatus.CANCELLED
        self.save(update_fields=["status", "updated_at"])

    def comparison(self):
        """
        The quotes side by side, per line, cheapest flagged.

        Cheapest is flagged rather than chosen. Lead time, quality and
        who answers the phone are not in this table, and a system that
        picked for you would be pretending otherwise.
        """
        invited = list(self.invited.select_related("vendor"))
        rows = []
        for line in self.lines.all():
            quotes = {
                quote.invitation_id: quote
                for quote in line.quotes.select_related("invitation__vendor")
            }
            priced = [
                (invitation, quotes[invitation.pk])
                for invitation in invited if invitation.pk in quotes
            ]
            best = min(
                (quote.unit_price for _, quote in priced), default=None
            )
            rows.append({
                "line": line,
                "item": line.item,
                "quantity": line.quantity,
                "quotes": [
                    {
                        "vendor": invitation.vendor,
                        "invitation": invitation,
                        "unit_price": quote.unit_price,
                        "total": round_money(quote.unit_price * line.quantity),
                        "lead_time_days": quote.lead_time_days,
                        "is_cheapest": quote.unit_price == best,
                    }
                    for invitation, quote in priced
                ],
                "missing": [
                    invitation.vendor for invitation in invited
                    if invitation.pk not in quotes
                ],
            })
        return rows

    def vendor_totals(self):
        """
        What each vendor would cost for the whole requirement.

        Only vendors who quoted *every* line get a total. A part-quote
        compared against a full one is not a comparison, and showing it
        as a smaller number is actively misleading.
        """
        totals = {}
        for invitation in self.invited.select_related("vendor"):
            quotes = {quote.line_id: quote for quote in invitation.quotes.all()}
            lines = list(self.lines.all())
            if len(quotes) != len(lines):
                totals[invitation] = None
                continue
            totals[invitation] = sum(
                (round_money(quotes[line.pk].unit_price * line.quantity) for line in lines),
                Decimal("0"),
            )
        return totals

    @transaction.atomic
    def award(self, invitation, order_date=None, record_prices=False):
        """
        Give the business to one vendor, at the prices they quoted.

        Quoted prices are optionally written back as agreed vendor prices,
        because a price won in competition is exactly the kind that should
        be checked against next time — but only on request, since a quote
        for one order is not always an ongoing agreement.
        """
        if self.status != RfqStatus.SENT:
            raise ValidationError("Only an issued RFQ can be awarded.")
        if invitation.rfq_id != self.pk:
            raise ValidationError("That vendor was not invited to this RFQ.")
        quotes = {quote.line_id: quote for quote in invitation.quotes.all()}
        lines = list(self.lines.all())
        missing = [line for line in lines if line.pk not in quotes]
        if missing:
            raise ValidationError(
                f"{invitation.vendor} did not quote for {missing[0].item}; award a vendor "
                "who quoted the whole requirement, or split the RFQ."
            )

        order_date = to_date(order_date) or timezone.now().date()
        order = PurchaseOrder.objects.create(
            vendor=invitation.vendor, order_date=order_date,
            reference=self.number, currency=self.currency,
        )
        for line in lines:
            quote = quotes[line.pk]
            PurchaseOrderLine.objects.create(
                order=order, item=line.item, uom=line.uom,
                requisition_line=line.requisition_line,
                quantity=line.quantity, unit_price=quote.unit_price,
                expected_date=(
                    order_date + datetime.timedelta(days=quote.lead_time_days)
                    if quote.lead_time_days else None
                ),
            )
            if record_prices:
                VendorPrice.objects.create(
                    vendor=invitation.vendor, item=line.item, currency=self.currency,
                    unit_price=quote.unit_price, min_quantity=line.quantity,
                    lead_time_days=quote.lead_time_days, valid_from=order_date,
                )

        invitation.awarded = True
        invitation.save(update_fields=["awarded", "updated_at"])
        self.status = RfqStatus.AWARDED
        self.save(update_fields=["status", "updated_at"])
        return order


class RfqLine(AuditModel):
    rfq = models.ForeignKey(RequestForQuotation, related_name="lines", on_delete=models.CASCADE)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="rfq_lines")
    uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    requisition_line = models.ForeignKey(
        "PurchaseRequisitionLine", null=True, blank=True, on_delete=models.PROTECT,
        related_name="rfq_lines",
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(check=Q(quantity__gt=0), name="rfq_line_quantity_positive"),
        ]

    def __str__(self):
        return f"{self.item} x{self.quantity}"


class RfqInvitation(AuditModel):
    """One vendor's involvement in an RFQ."""

    rfq = models.ForeignKey(RequestForQuotation, related_name="invited", on_delete=models.CASCADE)
    vendor = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="rfq_invitations")
    sent_at = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    declined = models.BooleanField(default=False)
    awarded = models.BooleanField(default=False)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["vendor__name"]
        constraints = [
            models.UniqueConstraint(fields=["rfq", "vendor"], name="one_invitation_per_vendor"),
        ]

    def __str__(self):
        return f"{self.vendor} on {self.rfq}"

    def clean(self):
        _require_vendor_role(self.vendor)

    def decline(self, note=""):
        """A vendor saying no is an answer, and worth keeping."""
        if self.quotes.exists():
            raise ValidationError("This vendor has already quoted.")
        self.declined = True
        self.responded_at = timezone.now()
        self.notes = note[:255] or self.notes
        self.save(update_fields=["declined", "responded_at", "notes", "updated_at"])

    def quote(self, line, unit_price, lead_time_days=None, notes=""):
        if self.declined:
            raise ValidationError("This vendor declined to quote.")
        if line.rfq_id != self.rfq_id:
            raise ValidationError("That line belongs to a different RFQ.")
        quote, _ = RfqQuote.objects.update_or_create(
            invitation=self, line=line,
            defaults={
                "unit_price": Decimal(unit_price),
                "lead_time_days": lead_time_days,
                "notes": notes,
            },
        )
        if not self.responded_at:
            self.responded_at = timezone.now()
            self.save(update_fields=["responded_at", "updated_at"])
        return quote


class RfqQuote(AuditModel):
    """What one vendor said one line would cost."""

    invitation = models.ForeignKey(
        RfqInvitation, related_name="quotes", on_delete=models.CASCADE
    )
    line = models.ForeignKey(RfqLine, related_name="quotes", on_delete=models.CASCADE)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    lead_time_days = models.PositiveSmallIntegerField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["line", "unit_price"]
        constraints = [
            models.CheckConstraint(check=Q(unit_price__gte=0), name="rfq_quote_not_negative"),
            models.UniqueConstraint(
                fields=["invitation", "line"], name="one_quote_per_vendor_and_line"
            ),
        ]

    def __str__(self):
        return f"{self.invitation.vendor} @ {self.unit_price}"


class RequisitionStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    ORDERED = "ordered", "Ordered"
    CANCELLED = "cancelled", "Cancelled"


class PurchaseRequisition(AuditModel):
    """
    Someone asking for something, before there is a purchase order.

    The gap this fills is a control one, not a convenience one. Approval
    on the purchase order asks "may we commit this money" at the moment
    the buyer is already negotiating with a vendor. The question that
    actually needs answering first is "does the company want this at
    all", and it needs answering by the person who owns the budget, not
    by the person who found the supplier.

    It is deliberately not a purchase order in a different state: a
    requisition names no vendor, carries no agreed price and no
    commitment, and the answer to it may be "buy it from someone else"
    or "no".
    """

    number = models.CharField(max_length=32, blank=True, editable=False)
    requested_by = models.ForeignKey(
        Party, on_delete=models.PROTECT, related_name="requisitions",
        help_text="The employee asking.",
    )
    request_date = models.DateField()
    needed_by = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=RequisitionStatus.choices, default=RequisitionStatus.DRAFT
    )
    justification = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )
    decided_at = models.DateTimeField(null=True, blank=True, editable=False)
    decision_note = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["-request_date", "-id"]
        permissions = [("decide_purchaserequisition", "Can approve or reject requisitions")]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""), name="unique_requisition_number"
            )
        ]

    def __str__(self):
        return f"{self.number or f'REQ-draft-{self.pk}'} for {self.requested_by}"

    def clean(self):
        _require_employee_role(self.requested_by)

    def estimated_total(self):
        return sum((line.estimated_value() for line in self.lines.all()), Decimal("0"))

    @transaction.atomic
    def submit(self):
        if self.status != RequisitionStatus.DRAFT:
            raise ValidationError(
                f"This requisition is already {self.get_status_display().lower()}."
            )
        if not self.lines.exists():
            raise ValidationError("Cannot submit a requisition with no lines.")
        if not self.number:
            self.number = DocumentSequence.next_for(
                "purchasing.requisition", self.request_date,
                name="Purchase Requisitions", prefix="REQ-",
            )
        self.status = RequisitionStatus.SUBMITTED
        self.save(update_fields=["number", "status", "updated_at"])

    def _decide(self, status, by, note):
        if self.status != RequisitionStatus.SUBMITTED:
            raise ValidationError("Only a submitted requisition can be decided.")
        self.status = status
        self.decided_by = by
        self.decided_at = timezone.now()
        self.decision_note = note[:255]
        self.save(update_fields=[
            "status", "decided_by", "decided_at", "decision_note", "updated_at",
        ])

    def approve(self, by=None, note=""):
        self._decide(RequisitionStatus.APPROVED, by, note)

    def reject(self, by=None, note=""):
        """
        Rejecting needs a reason. A requisition that comes back with no
        explanation gets resubmitted unchanged, which wastes everyone's
        time twice.
        """
        if not note:
            raise ValidationError("Say why the requisition was rejected.")
        self._decide(RequisitionStatus.REJECTED, by, note)

    def cancel(self):
        if self.status == RequisitionStatus.ORDERED:
            raise ValidationError(
                "This requisition has been ordered. Cancel the purchase order instead."
            )
        if self.status == RequisitionStatus.CANCELLED:
            raise ValidationError("This requisition is already cancelled.")
        self.status = RequisitionStatus.CANCELLED
        self.save(update_fields=["status", "updated_at"])

    def suggested_vendors(self):
        """Who each line could be bought from, from the agreed prices."""
        return {
            line: line.suggested_vendor or preferred_vendor(line.item, self.request_date)
            for line in self.lines.all()
        }

    @transaction.atomic
    def create_order(self, vendor, order_date=None, lines=None):
        """
        Turn the approved request into an order with a chosen vendor.

        Only approved requisitions convert, and only lines not already
        ordered: a requisition may legitimately be split across vendors,
        which is exactly why the vendor is not chosen when the request is
        made.
        """
        if self.status not in (RequisitionStatus.APPROVED, RequisitionStatus.ORDERED):
            raise ValidationError("Only an approved requisition can be ordered.")
        _require_vendor_role(vendor)

        selected = [
            line for line in (lines if lines is not None else self.lines.all())
            if line.quantity_ordered() < line.quantity
        ]
        if not selected:
            raise ValidationError("Every line on this requisition has already been ordered.")

        order = PurchaseOrder.objects.create(
            vendor=vendor,
            order_date=to_date(order_date) or timezone.now().date(),
            reference=self.number,
        )
        for line in selected:
            remaining = line.quantity - line.quantity_ordered()
            PurchaseOrderLine.objects.create(
                order=order, requisition_line=line, item=line.item, uom=line.uom,
                expense_account=line.expense_account,
                quantity=remaining,
                unit_price=resolve_purchase_price(
                    line.item, vendor, quantity=remaining,
                    currency=order.currency, on_date=order.order_date,
                ) or line.estimated_price,
                expected_date=self.needed_by,
            )

        if all(
            line.quantity_ordered() >= line.quantity for line in self.lines.all()
        ):
            self.status = RequisitionStatus.ORDERED
            self.save(update_fields=["status", "updated_at"])
        return order


class PurchaseRequisitionLine(AuditModel):
    requisition = models.ForeignKey(
        PurchaseRequisition, related_name="lines", on_delete=models.CASCADE
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="requisition_lines")
    uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    estimated_price = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="What the requester thinks it costs — an estimate, not an agreed price.",
    )
    expense_account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Which budget this is asking to spend. Carried to the order line.",
    )
    suggested_vendor = models.ForeignKey(
        Party, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Who the requester had in mind, if anyone. Not binding.",
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                check=Q(quantity__gt=0), name="requisition_line_quantity_positive"
            ),
        ]

    def __str__(self):
        return f"{self.item} x{self.quantity}"

    def estimated_value(self):
        price = self.estimated_price
        if price is None and self.suggested_vendor_id:
            price = resolve_purchase_price(
                self.item, self.suggested_vendor, quantity=self.quantity
            )
        return round_money(self.quantity * (price or Decimal("0")))

    def quantity_ordered(self):
        """How much of this request has become a real order, net of cancellations."""
        return self.order_lines.exclude(
            order__status=OrderStatus.CANCELLED
        ).aggregate(total=models.Sum("quantity"))["total"] or Decimal("0")


class SubcontractComponent(AuditModel):
    """
    A component the company supplies so the vendor can make the line's
    item.

    This is a narrow slice of subcontracting on purpose: there is no
    manufacturing module here, so there are no routings, no operations
    and no work centres. What it does cover is the part that touches
    purchasing and the ledger — components leaving, a finished item
    arriving, and its cost being what the components cost plus what the
    vendor charged.
    """

    order_line = models.ForeignKey(
        "PurchaseOrderLine", related_name="components", on_delete=models.CASCADE
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="supplied_to")
    quantity_per = models.DecimalField(
        max_digits=18, decimal_places=4,
        help_text="How many of this component go into one of the finished item.",
    )

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                check=Q(quantity_per__gt=0), name="component_quantity_positive"
            ),
            models.UniqueConstraint(
                fields=["order_line", "item"], name="one_component_row_per_item"
            ),
        ]

    def __str__(self):
        return f"{self.quantity_per} x {self.item}"

    def save(self, *args, **kwargs):
        """
        A component is specified against one of the finished item, which
        means one of its stocking units. An order line written in another
        unit would multiply every component by a factor nobody wrote down.

        The question belongs here and not on the order line: a line is
        created before its components are attached, so at line-save time
        there is nothing yet to say the line is subcontracted.
        """
        line = self.order_line
        if line.item_id and line.uom_id and line.uom_id != line.item.uom_id:
            raise ValidationError(
                f"{line.item} is ordered in {line.uom} on this line but its components "
                f"are specified per {line.item.uom}. Order it in {line.item.uom}."
            )
        super().save(*args, **kwargs)


class BlanketStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    CONFIRMED = "confirmed", "Confirmed"
    CLOSED = "closed", "Closed"


class BlanketOrder(AuditModel):
    """
    A negotiated commitment — an agreed price and volume over a period,
    drawn down by releases rather than delivered in one go.

    Standard in distribution and manufacturing procurement, and until now
    inexpressible: the commitment either lived in somebody's filing
    cabinet or was faked as a purchase order that never fully received.
    Faking it is worse than it sounds, because a purchase order that
    stays open forever silently skews every receipt and billing report
    that reads open orders.
    """

    number = models.CharField(max_length=32, blank=True, editable=False)
    vendor = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="blanket_orders")
    reference = models.CharField(max_length=64, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    currency = models.ForeignKey(
        Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    status = models.CharField(
        max_length=16, choices=BlanketStatus.choices, default=BlanketStatus.DRAFT
    )

    class Meta:
        ordering = ["-start_date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""), name="unique_blanket_order_number"
            )
        ]

    def __str__(self):
        return f"{self.number or f'BPO-draft-{self.pk}'} {self.vendor}"

    def clean(self):
        _require_vendor_role(self.vendor)
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError("A blanket order cannot end before it starts.")

    def save(self, *args, **kwargs):
        if self._state.adding and self.vendor_id and not self.currency_id:
            self.currency = self.vendor.default_currency
        super().save(*args, **kwargs)

    def total(self):
        return sum((line.committed_value() for line in self.lines.all()), Decimal("0"))

    def covers(self, on_date):
        on_date = to_date(on_date)
        return self.start_date <= on_date <= self.end_date

    @transaction.atomic
    def confirm(self):
        if self.status != BlanketStatus.DRAFT:
            raise ValidationError(f"This agreement is already {self.get_status_display().lower()}.")
        if not self.lines.exists():
            raise ValidationError("Cannot confirm an agreement with no lines.")
        if not self.number:
            self.number = DocumentSequence.next_for(
                "purchasing.blanket", self.start_date,
                name="Blanket Orders", prefix="BPO-",
            )
        self.status = BlanketStatus.CONFIRMED
        self.save(update_fields=["number", "status", "updated_at"])

    def close(self):
        """
        End the agreement early. Releases already made stand — they are
        purchase orders in their own right, and the vendor has committed
        against them.
        """
        if self.status == BlanketStatus.CLOSED:
            raise ValidationError("This agreement is already closed.")
        self.status = BlanketStatus.CLOSED
        self.save(update_fields=["status", "updated_at"])

    @transaction.atomic
    def release(self, quantities, order_date=None, expected_date=None):
        """
        Call off part of the commitment as a real purchase order.

        `quantities` is {blanket_line: quantity}. The price comes from the
        agreement, not from today's vendor price: the whole point of
        committing to a volume is that the price is fixed for it.
        """
        order_date = to_date(order_date) or timezone.now().date()
        if self.status != BlanketStatus.CONFIRMED:
            raise ValidationError("Only a confirmed agreement can be released against.")
        if not self.covers(order_date):
            raise ValidationError(
                f"This agreement runs {self.start_date:%d %b %Y} to {self.end_date:%d %b %Y}; "
                f"{order_date:%d %b %Y} is outside it."
            )

        selected = [(line, Decimal(quantity)) for line, quantity in quantities.items()
                    if Decimal(quantity) > 0]
        if not selected:
            raise ValidationError("Nothing to release.")
        for line, quantity in selected:
            if line.blanket_id != self.pk:
                raise ValidationError("That line belongs to a different agreement.")
            if quantity > line.quantity_remaining():
                raise ValidationError(
                    f"Only {line.quantity_remaining()} of {line.item} is left on this "
                    f"agreement; cannot release {quantity}."
                )

        order = PurchaseOrder.objects.create(
            vendor=self.vendor,
            order_date=order_date,
            reference=self.reference,
            currency=self.currency,
        )
        for line, quantity in selected:
            order_line = PurchaseOrderLine.objects.create(
                order=order, blanket_line=line, item=line.item, uom=line.uom,
                quantity=quantity, unit_price=line.unit_price,
                expected_date=expected_date,
            )
            order_line.taxes.set(line.taxes.all())
        return order


class BlanketOrderLine(TaxedLineMixin, AuditModel):
    blanket = models.ForeignKey(BlanketOrder, related_name="lines", on_delete=models.CASCADE)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="blanket_lines")
    uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")
    taxes = models.ManyToManyField(Tax, blank=True, related_name="blanket_lines")

    def party_for_tax(self):
        return self.blanket.vendor

    def __str__(self):
        return f"{self.item} x{self.quantity}"

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(quantity__gt=0), name="blanket_line_quantity_positive"
            ),
        ]

    def committed_value(self):
        return self.net_amount()

    def quantity_released(self):
        """
        Committed volume already called off, net of cancelled releases.

        A cancelled release gives its volume back: the agreement is a
        commitment to buy, and an order that was called off and then
        called back off again was never bought.
        """
        return self.order_lines.exclude(
            order__status=OrderStatus.CANCELLED
        ).aggregate(total=models.Sum("quantity"))["total"] or Decimal("0")

    def quantity_remaining(self):
        return self.quantity - self.quantity_released()

    def is_fully_released(self):
        return self.quantity_released() >= self.quantity


class PurchaseApprovalPolicy(AuditModel):
    """
    The thresholds beyond which committing money needs a second pair of
    eyes.

    The mirror of the sales discount policy, and the more important half:
    a sales order gives away margin, a purchase order spends cash. A
    clerk who may raise an order may raise it for any amount, and no role
    check anywhere notices.

    Every threshold is optional, so switching this on does not
    retroactively block every order in the system.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    max_order_value = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="Orders above this total need approval.",
    )
    max_line_value = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="Catches one very large line inside an otherwise ordinary order.",
    )
    require_approval_without_vendor_price = models.BooleanField(
        default=False,
        help_text="Need approval when a line's price was typed in rather than taken "
                  "from an agreed vendor price.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "purchase approval policies"

    def __str__(self):
        return self.name

    @classmethod
    def active(cls):
        return cls.objects.filter(is_active=True).first()

    def authorised_groups(self, amount):
        """The groups whose limit reaches this amount."""
        return [tier.group for tier in self.tiers.all() if tier.covers(amount)]


class Budget(AuditModel):
    """
    What may be spent on an account over a period, and what is already
    spoken for.

    The point is commitment, not the limit. A ledger tells you what has
    been spent; by then the money is gone. A budget that only counts
    posted bills reports a department as healthy right up to the month a
    year of purchase orders lands on it at once.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="budgets",
        help_text="The expense account this budget governs.",
    )
    start_date = models.DateField()
    end_date = models.DateField()
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-start_date", "code"]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="budget_amount_positive"),
        ]

    def __str__(self):
        return f"{self.code} {self.name}"

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError("A budget cannot end before it starts.")

    def covers(self, on_date):
        on_date = to_date(on_date)
        return self.start_date <= on_date <= self.end_date

    @classmethod
    def for_account(cls, account, on_date):
        if account is None:
            return None
        on_date = to_date(on_date) or timezone.now().date()
        return cls.objects.filter(
            account=account, is_active=True,
            start_date__lte=on_date, end_date__gte=on_date,
        ).first()

    def spent(self):
        """
        Posted bills coded to this budget, less debit notes.

        Read from the bills rather than from the ledger account, because
        the ledger is the wrong place to ask. Stocked goods post to
        inventory and GRNI and only reach an expense account when they
        are sold, so a budget watching the account would report every
        stock purchase as free — which is precisely the spend a
        purchasing budget exists to govern. The same reason revenue is
        reported from invoices rather than from the revenue account.
        """
        lines = BillLine.objects.filter(
            bill__posted=True,
            bill__bill_date__gte=self.start_date,
            bill__bill_date__lte=self.end_date,
        ).select_related("bill", "charge")
        total = Decimal("0")
        for line in lines:
            if self._account_for(line) != self.account:
                continue
            # A debit note gives the money back to the budget it came from.
            sign = Decimal("-1") if line.bill.is_debit_note() else Decimal("1")
            total += sign * line.net_amount()
        return total

    def committed(self):
        """
        Confirmed orders not yet billed — agreed, and not yet in the
        ledger.

        Counted from the order rather than the bill because that is the
        moment the company loses the ability to change its mind for free.
        """
        lines = PurchaseOrderLine.objects.filter(
            order__status=OrderStatus.CONFIRMED,
            order__order_date__gte=self.start_date,
            order__order_date__lte=self.end_date,
        ).select_related("order", "item")
        total = Decimal("0")
        for line in lines:
            if self._account_for(line) != self.account:
                continue
            unbilled = max(line.quantity - line.quantity_billed(), Decimal("0"))
            if unbilled <= 0:
                continue
            share = unbilled / line.quantity if line.quantity else Decimal("0")
            total += round_money(line.net_amount() * share)
        return total

    def requested(self):
        """Approved requisitions not yet ordered — asked for, not yet agreed."""
        lines = PurchaseRequisitionLine.objects.filter(
            requisition__status=RequisitionStatus.APPROVED,
            requisition__request_date__gte=self.start_date,
            requisition__request_date__lte=self.end_date,
        ).select_related("requisition", "item")
        total = Decimal("0")
        for line in lines:
            if self._requisition_account(line) != self.account:
                continue
            outstanding = max(line.quantity - line.quantity_ordered(), Decimal("0"))
            if outstanding <= 0 or line.estimated_price is None:
                continue
            total += round_money(outstanding * line.estimated_price)
        return total

    def available(self):
        return self.amount - self.spent() - self.committed() - self.requested()

    def _account_for(self, line):
        if line.expense_account_id:
            return line.expense_account
        if line.is_charge():
            return line.charge.expense_account
        return None

    def _requisition_account(self, line):
        # The requester codes the line to an account, the same way the
        # order line is coded. Without one it belongs to no budget rather
        # than silently to all of them.
        return line.expense_account


class ReorderRule(AuditModel):
    """
    When to buy more of something, and how much.

    This is the loop that connects purchasing to the rest of the system.
    Without it, `preferred_vendor()` and the agreed `lead_time_days` were
    each read in exactly one place and did nothing the rest of the time —
    configuration that looked like a feature.
    """

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="reorder_rules")
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.CASCADE, related_name="reorder_rules"
    )
    minimum = models.DecimalField(
        max_digits=18, decimal_places=4,
        help_text="Order when projected stock falls to or below this.",
    )
    target = models.DecimalField(
        max_digits=18, decimal_places=4,
        help_text="Order enough to bring projected stock back up to this.",
    )
    multiple_of = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True,
        help_text="Round the order up to a whole case, pallet or minimum order quantity.",
    )
    vendor = models.ForeignKey(
        Party, null=True, blank=True, on_delete=models.PROTECT, related_name="reorder_rules",
        help_text="Buy from this vendor. Left empty, the preferred or cheapest agreed "
                  "price decides.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["item", "warehouse"]
        constraints = [
            models.CheckConstraint(check=Q(minimum__gte=0), name="reorder_minimum_not_negative"),
            models.UniqueConstraint(
                fields=["item", "warehouse"], name="one_reorder_rule_per_item_and_warehouse"
            ),
        ]

    def __str__(self):
        return f"{self.item} at {self.warehouse}: {self.minimum} / {self.target}"

    def clean(self):
        if self.target is not None and self.minimum is not None and self.target < self.minimum:
            raise ValidationError("The target cannot be below the minimum.")
        if self.vendor_id:
            _require_vendor_role(self.vendor)

    def on_order(self):
        """
        Confirmed purchases not yet received, into this warehouse or
        without a warehouse yet named.

        Counted because ordering again for stock already on its way is
        how a reorder rule turns one shortage into two months of excess.
        """
        lines = PurchaseOrderLine.objects.filter(
            item=self.item, order__status=OrderStatus.CONFIRMED, charge__isnull=True
        )
        return sum(
            (max(line.quantity - line.quantity_received(), Decimal("0")) for line in lines),
            Decimal("0"),
        )

    def committed(self):
        """Confirmed sales not yet shipped from this warehouse."""
        from apps.sales.models import OrderStatus as SalesOrderStatus
        from apps.sales.models import SalesOrderLine

        lines = SalesOrderLine.objects.filter(
            item=self.item, order__status=SalesOrderStatus.CONFIRMED, charge__isnull=True
        )
        return sum(
            (max(line.quantity - line.quantity_shipped(), Decimal("0")) for line in lines),
            Decimal("0"),
        )

    def projected(self):
        """
        What will be on hand once everything already agreed has happened.

        On hand alone would reorder for stock that is spoken for, and
        ignore stock already inbound.
        """
        return (
            Decimal(self.item.available_at(self.warehouse))
            + self.on_order()
            - self.committed()
        )

    def shortfall(self):
        projected = self.projected()
        if projected > self.minimum:
            return Decimal("0")
        return self.target - projected

    def suggested_quantity(self):
        shortfall = self.shortfall()
        if shortfall <= 0:
            return Decimal("0")
        if not self.multiple_of:
            return shortfall
        multiples = (shortfall / self.multiple_of).to_integral_value(rounding=ROUND_CEILING)
        return multiples * self.multiple_of

    def suggested_vendor(self, on_date=None):
        return self.vendor or preferred_vendor(self.item, on_date)


class ApprovalTier(AuditModel):
    """
    Who may sign off up to what.

    A policy with thresholds but no tiers asks "does this need approval"
    and never "from whom", so one approve() served a team leader and the
    board alike. The amount that triggers a second pair of eyes and the
    seniority of that pair are different questions, and only the first
    was being asked.
    """

    policy = models.ForeignKey(
        "PurchaseApprovalPolicy", on_delete=models.CASCADE, related_name="tiers"
    )
    group = models.ForeignKey(
        "auth.Group", on_delete=models.PROTECT, related_name="+",
        help_text="Members of this group may approve up to the limit.",
    )
    up_to_amount = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="Leave empty for no ceiling — the final tier.",
    )

    class Meta:
        ordering = ["up_to_amount", "id"]
        constraints = [
            models.CheckConstraint(
                check=Q(up_to_amount__isnull=True) | Q(up_to_amount__gt=0),
                name="approval_tier_limit_positive",
            ),
            models.UniqueConstraint(
                fields=["policy", "group"], name="one_tier_per_group_and_policy"
            ),
        ]

    def __str__(self):
        ceiling = self.up_to_amount if self.up_to_amount is not None else "unlimited"
        return f"{self.group} up to {ceiling}"

    def covers(self, amount):
        return self.up_to_amount is None or amount <= self.up_to_amount


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


class PurchaseOrder(TaxedDocumentMixin, ApprovableMixin, AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    vendor = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="purchase_orders")
    order_date = models.DateField()
    reference = models.CharField(
        max_length=64, blank=True, help_text="The vendor's own quote or reference, if any."
    )
    status = models.CharField(max_length=16, choices=OrderStatus.choices, default=OrderStatus.DRAFT)
    currency = models.ForeignKey(Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    shipping_note = models.CharField(
        max_length=255, blank=True,
        help_text="Where the vendor should deliver, when it is not to the company.",
    )
    drop_ship_for = models.ForeignKey(
        "sales.SalesOrder", null=True, blank=True, on_delete=models.PROTECT,
        related_name="drop_ship_orders",
        help_text="Set when this order exists only to ship a sales order direct from "
                  "the vendor.",
    )
    subcontract_warehouse = models.ForeignKey(
        Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Where components sit while the subcontractor holds them. Still the "
                  "company's stock — it has only moved, not been sold.",
    )
    bill_policy = models.CharField(
        max_length=16, choices=BillPolicy.choices, default=BillPolicy.RECEIVED,
        help_text="Accept a bill for the whole order, or only for what has actually arrived.",
    )

    class Meta:
        ordering = ["-order_date", "-id"]
        permissions = [
            ("approve_purchaseorder", "Can approve orders that breach the spend policy"),
        ]
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

    def approval_reasons(self):
        reasons = []
        policy = PurchaseApprovalPolicy.active()
        if policy is None:
            return reasons

        total = self.total()
        if policy.max_order_value is not None and total > policy.max_order_value:
            reasons.append(
                f"The order is {total}, above the {policy.max_order_value} limit."
            )
        if policy.max_line_value is not None:
            for line in self.lines.all():
                if line.total() > policy.max_line_value:
                    reasons.append(
                        f"'{line.label()}' is {line.total()}, above the "
                        f"{policy.max_line_value} line limit."
                    )
        for line in self.lines.all():
            account = line.expense_account or (
                line.charge.expense_account if line.is_charge() else None
            )
            budget = Budget.for_account(account, self.order_date)
            if budget is None:
                continue
            if line.net_amount() > budget.available():
                reasons.append(
                    f"'{line.label()}' is {line.net_amount()} against {budget.available()} "
                    f"left on budget {budget.code}."
                )

        if policy.require_approval_without_vendor_price:
            for line in self.lines.all():
                if line.is_charge() or line.has_agreed_price():
                    continue
                reasons.append(
                    f"'{line.label()}' was priced by hand, not from an agreed "
                    "vendor price."
                )
        return reasons

    def can_be_approved(self):
        if self.status == OrderStatus.CANCELLED:
            raise ValidationError("A cancelled order cannot be approved.")
        return True

    def check_approver(self, by):
        """
        Refuse an approver whose authority does not reach this order.

        A policy with no tiers authorises anyone, so switching tiers on is
        a deliberate act rather than something that silently locks every
        buyer out on the day someone saves a policy.
        """
        policy = PurchaseApprovalPolicy.active()
        if policy is None or not policy.tiers.exists():
            return
        groups = policy.authorised_groups(self.total())
        if by is None:
            raise ValidationError(
                f"An order of {self.total()} needs a named approver from "
                + ", ".join(str(group) for group in groups) + "."
            )
        if by.is_superuser:
            return
        if not by.groups.filter(pk__in=[group.pk for group in groups]).exists():
            raise ValidationError(
                f"{by} cannot approve {self.total()}. That needs "
                + (", ".join(str(group) for group in groups) or "a higher limit than any "
                   "tier grants") + "."
            )

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
        if self.approval_status() == ApprovalStatus.PENDING:
            raise ValidationError(
                "This order needs approval before it can be confirmed: "
                + " ".join(self.approval_reasons())
            )
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
                expense_account=line.expense_account,
            )
            bill_line.taxes.set(line.taxes.all())
        return bill

    def is_drop_ship(self):
        return self.drop_ship_for_id is not None

    @classmethod
    @transaction.atomic
    def create_for_drop_ship(cls, sales_order, vendor, order_date=None, lines=None):
        """
        Raise a purchase order that ships straight from the vendor to the
        customer.

        This is the one place the two trading modules touch, and the
        direction is deliberate: the purchase order exists *because of*
        the sales order and would be meaningless without it, so
        Purchasing holds the pointer. Sales still knows nothing about
        Purchasing, so the dependency stays acyclic and the rule that
        neither module reaches sideways into the other's internals
        survives — this reaches into its documents, by reference, only.
        """
        from apps.sales.models import OrderStatus as SalesOrderStatus

        if sales_order.status != SalesOrderStatus.CONFIRMED:
            raise ValidationError("Only a confirmed sales order can be drop-shipped.")
        _require_vendor_role(vendor)

        selected = [
            line for line in (lines if lines is not None else sales_order.lines.all())
            if not line.is_charge() and line.quantity_shipped() < line.quantity
        ]
        if not selected:
            raise ValidationError("There is nothing left on this order to drop-ship.")

        order_date = to_date(order_date) or timezone.now().date()
        order = cls.objects.create(
            vendor=vendor, order_date=order_date,
            reference=sales_order.number, drop_ship_for=sales_order,
            shipping_note=f"Deliver direct to {sales_order.customer}",
        )
        for line in selected:
            remaining = line.quantity - line.quantity_shipped()
            PurchaseOrderLine.objects.create(
                order=order, sales_order_line=line, item=line.item, uom=line.uom,
                quantity=remaining,
                unit_price=resolve_purchase_price(
                    line.item, vendor, quantity=remaining,
                    currency=order.currency, on_date=order_date,
                ) or line.item.average_cost() or Decimal("0"),
            )
        return order

    @transaction.atomic
    def issue_components(self, from_warehouse, occurred_at=None):
        """
        Send the components out to the subcontractor.

        They move warehouse; they do not leave the company. Stock at a
        subcontractor is still stock you own and still stock you can lose,
        and writing it off on despatch would hide both facts. A transfer
        keeps the value on the books where it belongs.
        """
        if self.status != OrderStatus.CONFIRMED:
            raise ValidationError("Only a confirmed order can issue components.")
        if self.subcontract_warehouse_id is None:
            raise ValidationError(
                "Set a subcontract warehouse before issuing components; the stock has to "
                "sit somewhere it can still be counted."
            )
        occurred_at = occurred_at or timezone.now()

        moved = []
        for line in self.lines.filter(charge__isnull=True):
            for component in line.components.select_related("item"):
                quantity = round_money(component.quantity_per * line.quantity)
                if quantity <= 0 or not component.item.track_inventory:
                    continue
                cost = component.item.removal_unit_cost(from_warehouse, quantity)
                for warehouse, movement_type, signed in (
                    (from_warehouse, MovementType.TRANSFER_OUT, -quantity),
                    (self.subcontract_warehouse, MovementType.TRANSFER_IN, quantity),
                ):
                    StockMovement.objects.create(
                        item=component.item, warehouse=warehouse,
                        movement_type=movement_type, uom=component.item.uom,
                        quantity=signed,
                        unit_cost=cost, reference=self.number,
                        occurred_at=occurred_at,
                        notes=f"Components to subcontractor for {self.number}",
                    )
                moved.append((component.item, quantity))
        if not moved:
            raise ValidationError("This order has no components to issue.")
        return moved

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
    requisition_line = models.ForeignKey(
        "PurchaseRequisitionLine", null=True, blank=True, on_delete=models.PROTECT,
        related_name="order_lines",
        help_text="Set when this line fulfils a requisition, so the request can be "
                  "traced from ask to receipt.",
    )
    sales_order_line = models.ForeignKey(
        "sales.SalesOrderLine", null=True, blank=True, on_delete=models.PROTECT,
        related_name="drop_ship_lines",
        help_text="The customer line this drop-ship fulfils.",
    )
    blanket_line = models.ForeignKey(
        "BlanketOrderLine", null=True, blank=True, on_delete=models.PROTECT,
        related_name="order_lines",
        help_text="Set when this line calls off part of a blanket agreement.",
    )
    expected_date = models.DateField(
        null=True, blank=True,
        help_text="When the vendor said it would arrive — what on-time is measured against.",
    )
    inspect_on_receipt = models.BooleanField(
        default=False,
        help_text="Land these goods in quarantine until someone accepts them.",
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
            models.CheckConstraint(
                check=Q(quantity__gt=0), name="po_line_quantity_positive"
            ),
        ]

    def __str__(self):
        return f"{self.label()} x{self.quantity}"

    def save(self, *args, **kwargs):
        if self.is_charge() and not self.expense_account_id:
            self.expense_account = self.charge.account_for(is_sale=False)
        if self.item_id and self.uom_id:
            # Refuse a unit the item cannot be counted in now, while the
            # line is still a draft, rather than when the goods are on the
            # dock and the order is confirmed.
            self.item.check_uom(self.uom)
        if self.unit_price is None and not self.is_charge() and self.item_id:
            self.unit_price = resolve_purchase_price(
                self.item, self.order.vendor, quantity=self.quantity,
                currency=self.order.currency, on_date=self.order.order_date,
            )
        if self.unit_price is None:
            raise ValidationError(
                f"No agreed price for {self.label()} from {self.order.vendor}: add a "
                "vendor price, or give the line an explicit unit price."
            )
        # A confirmed line could be edited below what had already been
        # received or billed, silently breaking every drawdown guard that
        # reads it. Sales has had this since its own audit; the purchase
        # side went without.
        if self.pk:
            previous = PurchaseOrderLine.objects.filter(pk=self.pk).first()
            if previous is not None:
                if self.blanket_line_id:
                    # quantity_released() already counts this line's stored
                    # value, so compare against the agreement net of it.
                    others = self.blanket_line.quantity_released() - previous.quantity
                    if others + self.quantity > self.blanket_line.quantity:
                        raise ValidationError(
                            f"The agreement commits {self.blanket_line.quantity} of "
                            f"{self.item}; releasing {others + self.quantity} would "
                            "exceed it."
                        )
                committed = max(self.quantity_received(), self.quantity_billed())
                if self.quantity < committed:
                    raise ValidationError(
                        f"{committed} of this line has already been received or billed; "
                        "the quantity cannot drop below that."
                    )
                repriced = (
                    self.unit_price != previous.unit_price
                    or self.quantity != previous.quantity
                    or self.discount_percent != previous.discount_percent
                )
                if repriced and self.order.approved_at:
                    self.order.withdraw_approval()
                if self.unit_price != previous.unit_price and self.quantity_billed() > 0:
                    raise ValidationError(
                        "This line has been billed; its price can no longer change. The "
                        "agreed price is what the three-way match checks against, so "
                        "moving it would retrospectively approve whatever was billed."
                    )
        # A line added to an approved order changes the thing that was
        # approved just as surely as re-pricing one. Nothing caught this
        # because a new line has no previous version to compare against.
        if self._state.adding and self.order_id and self.order.approved_at:
            self.order.withdraw_approval()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.order_id and self.order.approved_at:
            self.order.withdraw_approval()
        if self.quantity_received() or self.quantity_billed():
            raise ValidationError(
                "This line has been received or billed and can no longer be removed."
            )
        super().delete(*args, **kwargs)

    def requires_inspection(self):
        """
        Whether goods on this line must be inspected before they are
        available.

        Set per line so a vendor on probation, or one troublesome part,
        can be inspected without quarantining everything the company
        buys.
        """
        return self.inspect_on_receipt

    def is_subcontract(self):
        return self.components.exists()

    def component_cost(self, warehouse):
        """What one finished unit's components are worth."""
        return sum(
            (round_money(component.quantity_per
                         * component.item.average_cost_at(warehouse))
             for component in self.components.select_related("item")),
            Decimal("0"),
        )

    def agreed_price(self):
        """What the vendor has agreed for this line, if anything."""
        if self.is_charge() or not self.item_id:
            return None
        return resolve_purchase_price(
            self.item, self.order.vendor, quantity=self.quantity,
            currency=self.order.currency, on_date=self.order.order_date,
        )

    def has_agreed_price(self):
        return self.agreed_price() is not None

    def price_against_agreement(self):
        """
        How far this line's price sits above what was agreed.

        Positive means the order is being placed for more than the vendor
        committed to — usually because nobody checked, occasionally
        because the agreement has lapsed.
        """
        agreed = self.agreed_price()
        if agreed is None or self.unit_price is None:
            return None
        return self.unit_price - agreed

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
                    debits.append((account, round_money(net * rate), label))
                else:
                    for item_pk, amount in shares.items():
                        account = inventory_account_for(Item.objects.get(pk=item_pk))
                        debits.append((account, round_money(amount * rate), f"{label} (landed)"))
            else:
                debits.append((account, round_money(net * rate), label))

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
                uom=item.uom,
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
    expense_account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Where a line lands when it expenses. A stocked line that a "
                  "receipt accrued for clears GRNI instead and needs none.",
    )
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

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(quantity__gt=0), name="bill_line_quantity_positive"
            ),
        ]

    def landed_cost_allocated(self):
        return sum(
            (application.amount
             for application in self.landed_cost_applications.all()
             if not application.is_released()),
            Decimal("0"),
        )

    def landed_cost_unallocated(self):
        return self.net_amount() - self.landed_cost_allocated()

    @transaction.atomic
    def allocate_landed_cost(self, receipt_lines, on_date=None):
        """
        Spread this charge over goods received on *other* bills.

        Split by the value of what was received, which is the defensible
        default: a container's freight follows the value it carried when
        nothing better is known. Weight or volume would be better and the
        system does not hold either.

        The charge already expensed when its own bill posted, since there
        was nothing on that bill to absorb it, so this moves it: Dr
        inventory / Cr the expense it landed in.
        """
        on_date = to_date(on_date) or timezone.now().date()
        if not self.bill.posted:
            raise ValidationError("Only a posted bill can be allocated.")
        if not (self.is_charge() and self.charge.capitalise_into_inventory):
            raise ValidationError(
                f"'{self.label()}' is not a charge that capitalises into stock."
            )

        lines = [line for line in receipt_lines]
        for line in lines:
            if not line.receipt.posted or line.receipt.is_return():
                raise ValidationError(
                    "Landed cost can only be applied to goods actually received."
                )
            if not line.order_line.item.track_inventory:
                raise ValidationError(
                    f"{line.order_line.item} is not stocked; there is nothing to add "
                    "the cost to."
                )
        if not lines:
            raise ValidationError("Name the goods this cost should land on.")

        total = self.landed_cost_unallocated()
        if total <= 0:
            raise ValidationError("This charge has already been allocated in full.")

        values = [
            round_money(line.quantity_received * (line.order_line.unit_price or Decimal("0")))
            for line in lines
        ]
        basis = sum(values, Decimal("0"))
        if basis <= 0:
            raise ValidationError("Those receipt lines have no value to spread the cost over.")

        applications, remaining = [], total
        for index, (line, value) in enumerate(zip(lines, values)):
            is_last = index == len(lines) - 1
            share = remaining if is_last else round_money(total * value / basis)
            remaining -= share
            if share <= 0:
                continue
            applications.append(self._apply_landed_cost(line, share, on_date))
        return applications

    def _apply_landed_cost(self, receipt_line, amount, on_date):
        item = receipt_line.order_line.item
        memo = f"Landed cost from {self.bill.number} onto {item}"
        entry = JournalEntry.objects.create(
            date=on_date, reference=self.bill.number, memo=memo
        )
        JournalLine.objects.create(
            entry=entry, account=inventory_account_for(item),
            debit=amount, description=memo[:255],
        )
        JournalLine.objects.create(
            entry=entry, account=self.posted_account or self.expense_account,
            credit=amount, description=memo[:255],
        )
        entry.post()

        movement = StockMovement.objects.create(
            item=item, warehouse=receipt_line.warehouse,
            movement_type=MovementType.ADJUSTMENT, uom=item.uom,
            lot=receipt_line.lot, bin=receipt_line.bin,
            # Landed cost belongs to the goods it was incurred on, and the
            # receipt says which those were. Under FIFO that decides which
            # layer it raises; spreading it over the shelf would misprice
            # everything that was already standing there.
            adjusts=receipt_line.stock_movement,
            quantity=Decimal("0"),
            value_adjustment=amount, reference=self.bill.number,
            occurred_at=timezone.now(), notes=memo,
        )
        return LandedCostApplication.objects.create(
            charge_line=self, receipt_line=receipt_line, amount=amount,
            date=on_date, journal_entry=entry, stock_movement=movement,
        )

    @transaction.atomic
    def capitalise_as_asset(self, category, name="", in_service_date=None,
                            life_months=None, salvage_value=Decimal("0")):
        """
        Turn this purchase into a fixed asset.

        A machine is neither stock nor an expense: valuing it as stock
        would relieve it on a sale that never comes, and expensing it puts
        a decade of value into one month's profit. Capitalising moves the
        cost off wherever the bill put it and onto the asset account.

        One asset per unit, because assets are tracked, disposed of and
        depreciated individually — a line for three machines is three
        assets, not one worth three times as much.
        """
        from apps.assets.models import AssetCategory, FixedAsset

        if not self.bill.posted:
            raise ValidationError("Only a posted bill can be capitalised.")
        if self.assets.exists():
            raise ValidationError("This line has already been capitalised.")
        if self.quantity != self.quantity.to_integral_value():
            raise ValidationError(
                "Capitalise whole units; a fraction of an asset cannot be disposed of."
            )

        unit_cost = round_money(self.net_amount() / self.quantity)
        memo = f"Capitalised from {self.bill.number}"
        source = self.posted_account or self.expense_account

        created = []
        remaining = self.net_amount()
        count = int(self.quantity)
        for index in range(count):
            cost = remaining if index == count - 1 else unit_cost
            remaining -= cost
            asset = FixedAsset.objects.create(
                name=name or self.label(),
                category=category,
                vendor=self.bill.vendor,
                bill_line=self,
                acquisition_date=self.bill.bill_date,
                in_service_date=to_date(in_service_date),
                cost=cost,
                salvage_value=Decimal(salvage_value),
                life_months=life_months or category.default_life_months,
            )
            entry = JournalEntry.objects.create(
                date=self.bill.bill_date, reference=self.bill.number, memo=memo
            )
            JournalLine.objects.create(
                entry=entry, account=category.asset_account,
                debit=cost, description=memo[:255],
            )
            JournalLine.objects.create(
                entry=entry, account=source, credit=cost, description=memo[:255],
            )
            entry.post()
            created.append(asset)
        return created

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
        if self.expense_account_id:
            return self.expense_account
        # Only a line that actually expenses needs an expense account, and
        # this is the first point that is known: until clears_grni() has
        # been asked, a stocked line looks like it needs one and does not.
        # Demanding it when the bill is built makes a company that buys
        # nothing but stock configure an account it never posts to.
        account = Company.get().default_purchase_expense_account
        if account is None:
            raise ValidationError(
                f"{self.label()} expenses rather than clearing an accrual, but has "
                "no expense account and the company has no default purchase expense "
                "account configured."
            )
        return account

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


@transaction.atomic
def draw_consignment(item, from_warehouse, to_warehouse, quantity, payable_account,
                     unit_price=None, on_date=None):
    """
    Take consigned stock into ownership and raise the bill for it.

    Drawing is the moment of purchase. Until then the goods are the
    vendor's, sitting on the company's floor; afterwards they are stock
    the company owns and owes for. Both facts have to land together, or
    the stock appears without a liability or the liability without the
    stock.
    """
    on_date = to_date(on_date) or timezone.now().date()
    vendor = from_warehouse.consignment_vendor
    if vendor is None:
        raise ValidationError(f"{from_warehouse} does not hold consignment stock.")
    if to_warehouse.consignment_vendor_id:
        raise ValidationError("Drawing into another consignment warehouse owns nothing.")

    quantity = Decimal(quantity)
    on_hand = Decimal(item.on_hand_at(from_warehouse))
    if quantity <= 0:
        raise ValidationError("Draw a positive quantity.")
    if quantity > on_hand:
        raise ValidationError(
            f"Only {on_hand} of {item} is on consignment at {from_warehouse}."
        )

    price = unit_price
    if price is None:
        price = resolve_purchase_price(item, vendor, quantity=quantity, on_date=on_date)
    if price is None:
        raise ValidationError(
            f"No agreed price for {item} from {vendor}; consignment cannot be drawn at "
            "a price nobody has stated."
        )
    price = Decimal(price)

    order = PurchaseOrder.objects.create(
        vendor=vendor, order_date=on_date,
        reference=f"Consignment draw from {from_warehouse.code}",
    )
    order_line = PurchaseOrderLine.objects.create(
        order=order, item=item, uom=item.uom, quantity=quantity, unit_price=price,
    )
    order.confirm()

    receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=on_date)
    GoodsReceiptLine.objects.create(
        receipt=receipt, order_line=order_line, warehouse=to_warehouse,
        quantity_received=quantity,
    )
    receipt.post()

    # The goods were already standing here, so the consignment warehouse
    # gives them up rather than the vendor shipping them again.
    StockMovement.objects.create(
        item=item, warehouse=from_warehouse, movement_type=MovementType.ISSUE,
        uom=order_line.uom, quantity=-quantity, unit_cost=Decimal("0"),
        reference=order.number,
        occurred_at=timezone.now(),
        notes=f"Drawn into ownership on {order.number}",
    )

    bill = order.create_bill(payable_account, bill_date=on_date)
    bill.post()
    return order, bill


def consignment_on_hand(warehouse=None, vendor=None):
    """What the company is holding that it does not own."""
    warehouses = Warehouse.objects.exclude(consignment_vendor__isnull=True)
    if warehouse is not None:
        warehouses = warehouses.filter(pk=warehouse.pk)
    if vendor is not None:
        warehouses = warehouses.filter(consignment_vendor=vendor)

    rows = []
    for store in warehouses.select_related("consignment_vendor"):
        for item in Item.objects.filter(movements__warehouse=store).distinct():
            quantity = Decimal(item.on_hand_at(store))
            if quantity <= 0:
                continue
            rows.append({
                "warehouse": store,
                "vendor": store.consignment_vendor,
                "item": item,
                "quantity": quantity,
            })
    return sorted(rows, key=lambda row: -row["quantity"])


def reorder_suggestions(warehouse=None, on_date=None):
    """
    Everything that has fallen to its reorder point, with what to buy and
    from whom.
    """
    on_date = to_date(on_date) or timezone.now().date()
    rules = ReorderRule.objects.filter(is_active=True).select_related("item", "warehouse")
    if warehouse is not None:
        rules = rules.filter(warehouse=warehouse)

    rows = []
    for rule in rules:
        quantity = rule.suggested_quantity()
        if quantity <= 0:
            continue
        vendor = rule.suggested_vendor(on_date)
        rows.append({
            "rule": rule,
            "item": rule.item,
            "warehouse": rule.warehouse,
            "on_hand": Decimal(rule.item.available_at(rule.warehouse)),
            "on_order": rule.on_order(),
            "committed": rule.committed(),
            "projected": rule.projected(),
            "quantity": quantity,
            "vendor": vendor,
            "unit_price": (
                resolve_purchase_price(rule.item, vendor, quantity=quantity, on_date=on_date)
                if vendor else None
            ),
            "lead_time_days": (
                resolve_lead_time(rule.item, vendor, quantity=quantity, on_date=on_date)
                if vendor else None
            ),
        })
    return sorted(rows, key=lambda row: row["projected"])


@transaction.atomic
def raise_reorder_requisition(requested_by, warehouse=None, on_date=None, rows=None):
    """
    Turn the suggestions into a requisition somebody has to approve.

    A requisition rather than a purchase order on purpose. A rule that
    fires on stale data, or a min/max nobody has revisited since the
    product changed, should cost a conversation and not a delivery — and
    the approval step already exists.
    """
    on_date = to_date(on_date) or timezone.now().date()
    rows = rows if rows is not None else reorder_suggestions(warehouse, on_date)
    if not rows:
        raise ValidationError("Nothing has fallen to its reorder point.")

    requisition = PurchaseRequisition.objects.create(
        requested_by=requested_by, request_date=on_date,
        justification="Raised automatically from reorder rules.",
    )
    for row in rows:
        PurchaseRequisitionLine.objects.create(
            requisition=requisition, item=row["item"], uom=row["item"].uom,
            quantity=row["quantity"], estimated_price=row["unit_price"],
            suggested_vendor=row["vendor"],
            notes=f"Projected {row['projected']} against a minimum of {row['rule'].minimum}",
        )
    return requisition


def _receipt_dates(order_line):
    """[(receipt_date, quantity)] for real receipts, returns excluded."""
    return [
        (to_date(line.receipt.receipt_date), line.quantity_received)
        for line in order_line.receipt_lines.filter(
            receipt__posted=True, receipt__reverses__isnull=True
        ).select_related("receipt")
    ]


def vendor_performance(start=None, end=None, vendor=None):
    """
    How each vendor actually behaved: on time, in full, at the agreed
    price.

    Every input already existed — receipt dates against expected dates,
    received against ordered, billed price against ordered price. Nobody
    had put them together, which meant the only way to know a vendor was
    consistently late was to have noticed.

    On-time is measured per receipt against the line's expected date,
    weighted by quantity, so one late pallet out of ten does not read the
    same as ten late pallets. Lines with no expected date are left out of
    the on-time figure rather than counted as on time — a vendor who was
    never given a date cannot be late, and scoring them as punctual would
    flatter them.
    """
    start, end = to_date(start), to_date(end)
    lines = PurchaseOrderLine.objects.select_related("order__vendor", "item").exclude(
        order__status=OrderStatus.CANCELLED
    ).filter(charge__isnull=True)
    if vendor is not None:
        lines = lines.filter(order__vendor=vendor)
    if start:
        lines = lines.filter(order__order_date__gte=start)
    if end:
        lines = lines.filter(order__order_date__lte=end)

    rows = {}
    for line in lines:
        party = line.order.vendor
        row = rows.setdefault(party.pk, {
            "vendor": party,
            "order_lines": 0,
            "quantity_ordered": Decimal("0"),
            "quantity_received": Decimal("0"),
            "dated_quantity": Decimal("0"),
            "on_time_quantity": Decimal("0"),
            "late_days_weighted": Decimal("0"),
            "price_variance": Decimal("0"),
            "open_lines": 0,
        })
        row["order_lines"] += 1
        row["quantity_ordered"] += line.quantity
        received = line.quantity_received()
        row["quantity_received"] += received
        if received < line.quantity:
            row["open_lines"] += 1

        if line.expected_date:
            for receipt_date, quantity in _receipt_dates(line):
                row["dated_quantity"] += quantity
                late = (receipt_date - to_date(line.expected_date)).days
                if late <= 0:
                    row["on_time_quantity"] += quantity
                else:
                    row["late_days_weighted"] += Decimal(late) * quantity

        for bill_line in line.bill_lines.filter(
            bill__posted=True, bill__debits__isnull=True
        ):
            row["price_variance"] += round_money(
                (bill_line.unit_price - (line.unit_price or Decimal("0")))
                * bill_line.quantity
            )

    results = []
    for row in rows.values():
        dated = row.pop("dated_quantity")
        on_time = row.pop("on_time_quantity")
        late_weighted = row.pop("late_days_weighted")
        ordered = row["quantity_ordered"]
        results.append({
            **row,
            # None rather than 100%: a vendor never given a date cannot be
            # late, and scoring them punctual would flatter them.
            "on_time_rate": (
                round_money(on_time / dated * Decimal("100")) if dated else None
            ),
            "average_days_late": (
                round_money(late_weighted / (dated - on_time))
                if dated - on_time > 0 else Decimal("0")
            ),
            "fill_rate": (
                round_money(row["quantity_received"] / ordered * Decimal("100"))
                if ordered else None
            ),
        })
    return sorted(results, key=lambda row: -row["quantity_ordered"])


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

        if self.purchase_order.is_drop_ship():
            return self._post_drop_ship(lines, is_return=is_return)

        valued = []
        received = {}
        for line in lines:
            # Services and non-stocked items must never touch stock levels.
            if not line.order_line.item.track_inventory:
                continue
            # Consignment stock is on the premises and not on the books.
            # It moves, so the quantity is recorded; it is not owned, so
            # no value and no liability are. Booking it would put the
            # vendor's inventory on the company's balance sheet and accrue
            # a bill nobody owes yet.
            if line.warehouse.consignment_vendor_id:
                self._move_consignment(line, is_return)
                continue
            movement_type = MovementType.ISSUE if is_return else MovementType.RECEIPT
            quantity = -line.quantity_received if is_return else line.quantity_received
            unit_cost = line.order_line.unit_price
            # Where the goods land. suggest_putaway() existed and nothing
            # called it, so a binned warehouse refused every receipt that
            # did not name a shelf by hand. The answer is written back to
            # the line, because a line that does not record where its
            # goods went cannot send them back from there — which is how
            # auto-put-away silently broke the return to vendor.
            landed_in = plan_putaway(
                line.order_line.item,
                line.arrived_at() if is_return else line.warehouse.first_receipt_step(),
                line.bin,
            )
            if line.bin_id != getattr(landed_in, "pk", None):
                line.bin = landed_in
            # A subcontracted item is worth what the components cost plus
            # what the vendor charged to assemble them. Valuing it at the
            # vendor's charge alone would report a part built from 100 of
            # components as worth the 5 of labour.
            component_cost = Decimal("0")
            if line.order_line.is_subcontract():
                if is_return:
                    # Sending the assemblies back sends their inputs back
                    # too. Reversing only the finished item would destroy
                    # the components, which the vendor still has.
                    component_cost = self._restore_components(line)
                else:
                    component_cost = self._consume_components(line)
                unit_cost = unit_cost + component_cost
            # Where the goods actually land: the first step of the
            # destination's receipt route, which for most warehouses is
            # the destination itself. A return takes them back off
            # whichever shelf they are really on, so the answer is frozen
            # onto the line.
            landed = (
                line.arrived_at() if is_return
                else line.warehouse.first_receipt_step()
            )
            if not is_return and line.landed_warehouse_id != landed.pk:
                line.landed_warehouse = landed
            movement = StockMovement.objects.create(
                item=line.order_line.item,
                warehouse=landed,
                movement_type=movement_type,
                # Both numbers are per order-line unit — the received
                # quantity and the agreed price — so the movement restates
                # them together and the total stays the total.
                uom=line.order_line.uom,
                lot=line.lot,
                bin=landed_in,
                quantity=quantity,
                unit_cost=unit_cost,
                reference=self.reference or self.number,
                occurred_at=timezone.now(),
                notes=(
                    f"{'Return for' if is_return else 'Receipt for'} "
                    f"{self.purchase_order} ({self.number})"
                ),
            )
            line.stock_movement = movement
            super(GoodsReceiptLine, line).save(
                update_fields=[
                    "stock_movement", "bin", "landed_warehouse", "updated_at",
                ]
            )
            # Only the vendor's charge hits the ledger: the component
            # value has merely moved from one item to another inside the
            # same inventory account, so posting it again would double it.
            valued.append((
                line.order_line.item,
                line.quantity_received * (unit_cost - component_cost),
            ))
            # An item costed at standard books the standard and throws the
            # difference to variance, which needs the quantity in stocking
            # units — the value alone cannot say how many arrived.
            received[line.order_line.item] = received.get(
                line.order_line.item, Decimal("0")
            ) + line.order_line.item.to_stock_quantity(
                line.quantity_received, line.order_line.uom
            )

        post_inventory_entry(
            valued,
            date=self.receipt_date,
            reference=self.reference or self.number,
            memo=f"{'Return to vendor for' if is_return else 'Goods received for'} {self.purchase_order}",
            direction="in",
            reverse=is_return,
            quantities=received,
        )

        self.posted = True
        self.posted_at = timezone.now()
        super(GoodsReceipt, self).save(
            update_fields=["number", "receipt_date", "posted", "posted_at", "updated_at"]
        )

    @transaction.atomic
    def _post_drop_ship(self, lines, is_return=False):
        """
        The goods went from the vendor straight to the customer and never
        touched a warehouse.

        So there is no stock movement to make — inventing one would
        create quantity the company never held and then relieve it again
        — and the cost goes straight to cost of sales rather than being
        capitalised and immediately released. The matching sales delivery
        is raised here too, because the customer has been shipped whether
        or not anyone in this company noticed.
        """
        from apps.sales.models import Delivery, DeliveryLine

        # Dr cost of sales / Cr goods received not invoiced. Neither
        # direction post_inventory_entry offers is this one: a drop-ship
        # skips the inventory account entirely, which is the whole point,
        # so the entry is built here rather than bent out of a helper
        # that assumes stock was held.
        memo = (
            f"Drop-ship {'returned' if is_return else 'to customer'} for "
            f"{self.purchase_order}"
        )
        totals = defaultdict(Decimal)
        for line in lines:
            item = line.order_line.item
            if not item.track_inventory:
                continue
            value = round_money(line.quantity_received * line.order_line.unit_price)
            if value:
                totals[cogs_account_for(item)] += value

        if totals:
            accrual = grni_account()
            entry = JournalEntry.objects.create(
                date=self.receipt_date, reference=self.reference or self.number, memo=memo
            )
            for account, value in totals.items():
                JournalLine.objects.create(
                    entry=entry, account=account,
                    debit=Decimal("0") if is_return else value,
                    credit=value if is_return else Decimal("0"),
                    description=memo[:255],
                )
            JournalLine.objects.create(
                entry=entry, account=accrual,
                debit=sum(totals.values()) if is_return else Decimal("0"),
                credit=Decimal("0") if is_return else sum(totals.values()),
                description=memo[:255],
            )
            entry.post()

        sales_lines = [line for line in lines if line.order_line.sales_order_line_id]
        if sales_lines and is_return:
            # The customer sent it back to the vendor, so the sales
            # delivery is reversed on the sales side, where returns belong.
            original = Delivery.objects.filter(
                sales_order=self.purchase_order.drop_ship_for,
                is_drop_ship=True, posted=True, reverses__isnull=True,
            ).order_by("-id").first()
            if original and not original.reversed_by.exists():
                original.create_return(credit_invoices=False)
        elif sales_lines:
            delivery = Delivery.objects.create(
                sales_order=self.purchase_order.drop_ship_for,
                delivery_date=self.receipt_date,
                reference=self.number,
                is_drop_ship=True,
            )
            for line in sales_lines:
                DeliveryLine.objects.create(
                    delivery=delivery, order_line=line.order_line.sales_order_line,
                    warehouse=line.warehouse, quantity_shipped=line.quantity_received,
                )
            delivery.post()

        self.posted = True
        self.posted_at = timezone.now()
        super(GoodsReceipt, self).save(
            update_fields=["number", "receipt_date", "posted", "posted_at", "updated_at"]
        )

    def quarantined_lines(self):
        # Where the goods are, not where they are going: a route that
        # sends them through inspection puts them in quarantine while the
        # line still names the shelf they are destined for.
        return [line for line in self.lines.all() if line.quarantined_quantity() > 0]

    def awaiting_inspection(self):
        """{receipt_line: quantity} still sitting in quarantine."""
        return {
            line: line.quantity_uninspected()
            for line in self.quarantined_lines()
            if line.quantity_uninspected() > 0
        }

    @transaction.atomic
    def accept(self, warehouse, quantities=None, occurred_at=None):
        """
        Clear inspected goods into a warehouse they can be shipped from.

        A transfer, not a receipt: the goods were already received, owned
        and valued when they arrived. Receiving them again would book the
        purchase twice, which is what happens if quarantine is modelled
        as "not yet received" rather than "not yet cleared".
        """
        if not self.posted:
            raise ValidationError("Only a posted receipt has goods to accept.")
        if warehouse.is_quarantine:
            raise ValidationError("Accepting goods into quarantine clears nothing.")
        occurred_at = occurred_at or timezone.now()

        selected = self._inspection_selection(quantities)
        moved = []
        for line, quantity in selected:
            item = line.order_line.item
            cost = item.removal_unit_cost(line.warehouse, quantity)
            # The cost is per stocking unit, so the quantity has to
            # be too: a transfer valued at one unit and counted in another
            # moves more value out of a warehouse than it moves in.
            moving = item.to_stock_quantity(quantity, line.order_line.uom)
            for target, movement_type, signed in (
                (line.warehouse, MovementType.TRANSFER_OUT, -moving),
                (warehouse, MovementType.TRANSFER_IN, moving),
            ):
                StockMovement.objects.create(
                    item=item, warehouse=target, movement_type=movement_type,
                    uom=item.uom, lot=line.lot,
                    # Quarantine and the shelf it clears to are different
                    # places, so the bin only follows to the bin the caller
                    # named — not the one it sat in under inspection.
                    bin=(line.bin if target.pk == line.warehouse_id else None),
                    quantity=signed, unit_cost=cost, reference=self.number,
                    occurred_at=occurred_at,
                    notes=f"Accepted from inspection on {self.number}",
                )
            ReceiptInspection.objects.create(
                receipt_line=line, quantity=quantity, accepted=True,
                inspected_on=to_date(occurred_at), warehouse=warehouse,
            )
            moved.append((item, quantity))
        return moved

    @transaction.atomic
    def reject(self, quantities=None, note="", debit_bills=True):
        """
        Send failed goods back to the vendor.

        Recorded as an inspection *and* a return, because those are two
        facts: that the goods failed, which is vendor performance, and
        that they left, which is stock and money.
        """
        if not self.posted:
            raise ValidationError("Only a posted receipt has goods to reject.")
        selected = self._inspection_selection(quantities)
        for line, quantity in selected:
            ReceiptInspection.objects.create(
                receipt_line=line, quantity=quantity, accepted=False,
                inspected_on=timezone.now().date(), note=note,
            )
        return self.create_return(
            quantities={line: quantity for line, quantity in selected},
            debit_bills=debit_bills,
        )

    def _inspection_selection(self, quantities):
        if quantities is None:
            selected = [
                (line, line.quantity_uninspected()) for line in self.quarantined_lines()
            ]
            selected = [(line, quantity) for line, quantity in selected if quantity > 0]
        else:
            selected = [(line, Decimal(quantity)) for line, quantity in quantities.items()
                        if Decimal(quantity) > 0]
            for line, quantity in selected:
                if line.receipt_id != self.pk:
                    raise ValidationError("That line belongs to a different receipt.")
                if quantity > line.quantity_uninspected():
                    raise ValidationError(
                        f"Only {line.quantity_uninspected()} of {line.order_line.item} is "
                        f"awaiting inspection; cannot decide {quantity}."
                    )
        if not selected:
            raise ValidationError("There is nothing awaiting inspection on this receipt.")
        return selected

    def _move_consignment(self, line, is_return):
        quantity = -line.quantity_received if is_return else line.quantity_received
        StockMovement.objects.create(
            item=line.order_line.item, warehouse=line.warehouse,
            movement_type=MovementType.ISSUE if is_return else MovementType.RECEIPT,
            uom=line.order_line.uom, lot=line.lot, bin=line.bin, quantity=quantity,
            unit_cost=Decimal("0"),
            reference=self.reference or self.number,
            occurred_at=timezone.now(),
            notes=(
                f"{'Consignment returned' if is_return else 'Consignment delivered'} "
                f"for {self.purchase_order}"
            ),
        )

    def _restore_components(self, line):
        """Put the components back where they were when a return reverses
        a subcontract receipt, and return their per-unit cost."""
        order = self.purchase_order
        warehouse = order.subcontract_warehouse
        per_unit = Decimal("0")
        for component in line.order_line.components.select_related("item"):
            if not component.item.track_inventory:
                continue
            quantity = round_money(component.quantity_per * line.quantity_received)
            cost = component.item.average_cost_at(warehouse) or (
                component.item.average_cost()
            ) or (component.item.standard_cost or Decimal("0"))
            StockMovement.objects.create(
                item=component.item, warehouse=warehouse,
                movement_type=MovementType.RECEIPT, uom=component.item.uom,
                quantity=quantity, unit_cost=cost,
                reference=self.number,
                occurred_at=timezone.now(),
                notes=f"Components returned with {self.number}",
            )
            per_unit += round_money(component.quantity_per * cost)
        return per_unit

    def _consume_components(self, line):
        """
        Take the components the subcontractor used out of their warehouse,
        and return what one finished unit's worth of them cost.
        """
        order = self.purchase_order
        if order.subcontract_warehouse_id is None:
            raise ValidationError(
                f"{order} has subcontracted lines but no subcontract warehouse; the "
                "components have nowhere to be consumed from."
            )
        warehouse = order.subcontract_warehouse
        per_unit = Decimal("0")
        for component in line.order_line.components.select_related("item"):
            if not component.item.track_inventory:
                continue
            used = round_money(component.quantity_per * line.quantity_received)
            cost = component.item.removal_unit_cost(warehouse, used)
            on_hand = component.item.on_hand_at(warehouse)
            if used > on_hand:
                raise ValidationError(
                    f"The subcontractor is short {used - on_hand} of {component.item}; "
                    "issue the components before receiving the finished item."
                )
            StockMovement.objects.create(
                item=component.item, warehouse=warehouse,
                movement_type=MovementType.ISSUE, uom=component.item.uom,
                quantity=-used, unit_cost=cost,
                reference=self.number,
                occurred_at=timezone.now(),
                notes=f"Consumed by subcontractor for {self.number}",
            )
            per_unit += round_money(component.quantity_per * cost)
        return per_unit

    def _debit_returned_goods(self):
        """
        Debit the bills that paid for the returned goods.

        A receipt's quantities may have been spread over several bills, so
        the returned quantity is allocated oldest-bill-first and one debit
        note is raised per affected bill — the mirror of how a customer
        return credits its invoices.

        Without this, sending goods back reversed the stock and left the
        company still owing the vendor for them until somebody separately
        remembered.
        """
        allocations = defaultdict(dict)
        for line in self.lines.all():
            remaining = line.quantity_received
            bill_lines = BillLine.objects.filter(
                order_line=line.order_line,
                bill__posted=True,
                bill__debits__isnull=True,
            ).order_by("bill__bill_date", "bill_id")
            for bill_line in bill_lines:
                if remaining <= 0:
                    break
                available = bill_line.quantity_debitable()
                if available <= 0:
                    continue
                taken = min(available, remaining)
                per_bill = allocations[bill_line.bill]
                per_bill[bill_line] = per_bill.get(bill_line, Decimal("0")) + taken
                remaining -= taken

        return [
            bill.create_debit_note(
                memo=f"Goods returned on {self.number}", quantities=quantities
            )
            for bill, quantities in allocations.items()
        ]

    @transaction.atomic
    def create_return(self, quantities=None, debit_bills=True):
        """
        Send goods back. By default all of them; pass `quantities` as
        {receipt_line: quantity} to send back part, which is what a
        partial fault actually looks like.

        Whole-receipt-only was the last place a correction could not be
        partial. Returning ten units to send back three, then re-receiving
        seven, loses the receipt date on the seven and books three stock
        movements where one belongs.
        """
        if not self.posted:
            raise ValidationError("Only a posted goods receipt can be returned.")
        if self.reverses_id:
            raise ValidationError("Cannot return a return.")

        if quantities is None:
            selected = [(line, line.quantity_returnable()) for line in self.lines.all()]
            selected = [(line, quantity) for line, quantity in selected if quantity > 0]
        else:
            selected = [(line, Decimal(quantity)) for line, quantity in quantities.items()
                        if Decimal(quantity) > 0]
            for line, quantity in selected:
                if line.receipt_id != self.pk:
                    raise ValidationError("That line belongs to a different receipt.")
                if quantity > line.quantity_returnable():
                    raise ValidationError(
                        f"Only {line.quantity_returnable()} of {line.order_line.item} is "
                        f"left to return; cannot return {quantity}."
                    )
        if not selected:
            raise ValidationError("There is nothing left on this receipt to return.")

        return_receipt = GoodsReceipt.objects.create(
            purchase_order=self.purchase_order,
            receipt_date=timezone.now().date(),
            reference=self.reference,
            reverses=self,
        )
        for line, quantity in selected:
            GoodsReceiptLine.objects.create(
                receipt=return_receipt,
                order_line=line.order_line,
                reverses_line=line,
                lot=line.lot,
                bin=line.bin,
                warehouse=line.warehouse,
                # Off the shelf the goods are actually on. A routed
                # receipt leaves them in the bay or in inspection while
                # the line still names the shelf they were destined for,
                # and taking them from there would send back stock that
                # never got there.
                landed_warehouse=line.current_step(),
                quantity_received=quantity,
            )
        return_receipt.post()
        return_receipt.debit_notes_created = (
            return_receipt._debit_returned_goods() if debit_bills else []
        )
        return return_receipt


class GoodsReceiptLine(AuditModel):
    receipt = models.ForeignKey(GoodsReceipt, related_name="lines", on_delete=models.CASCADE)
    order_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name="receipt_lines")
    reverses_line = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="return_lines",
        help_text="On a return line, the receipt line being sent back.",
    )
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+")
    bin = models.ForeignKey(
        "inventory.StorageBin", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
        help_text="Which shelf to put the goods on. Left blank in a binned "
                  "warehouse, the receipt puts them next to the same item and "
                  "records where.",
    )
    lot = models.ForeignKey(
        "inventory.Lot", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Which batch arrived. Required when the item is tracked: the "
                  "receipt is where a batch enters the company, and if it is not "
                  "recorded here there is nothing to trace it from.",
    )
    quantity_received = models.DecimalField(max_digits=18, decimal_places=4)
    stock_movement = models.ForeignKey(
        "inventory.StockMovement", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
        help_text="What this line wrote into the stock ledger. Kept because under "
                  "FIFO a landed cost has to raise the layer these goods created, "
                  "not an average of every layer on the shelf.",
    )
    landed_warehouse = models.ForeignKey(
        Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        editable=False,
        help_text="Where the goods actually arrived, which is the first step of the "
                  "destination's receipt route and not always the destination. "
                  "Frozen, because a return has to take them back off the shelf "
                  "they are really on.",
    )

    def arrived_at(self):
        """Where the goods landed — the destination, if nothing says otherwise."""
        return self.landed_warehouse or self.warehouse

    def route_steps(self):
        return self.warehouse.receipt_steps()

    def quantity_advanced_from(self, warehouse):
        total = Decimal("0")
        for move in self.route_moves.filter(from_warehouse=warehouse):
            total += move.quantity
        return total

    def quantity_advanced_to(self, warehouse):
        total = Decimal("0")
        for move in self.route_moves.filter(to_warehouse=warehouse):
            total += move.quantity
        return total

    def quantity_at(self, warehouse):
        """
        How much of this line is sitting at one step of its route.

        Derived from the moves rather than stored, so a half-advanced
        line cannot claim to be somewhere it is not.
        """
        arrived = (
            self.quantity_received if warehouse.pk == self.arrived_at().pk
            else Decimal("0")
        )
        return (
            arrived
            + self.quantity_advanced_to(warehouse)
            - self.quantity_advanced_from(warehouse)
        )

    def current_step(self):
        """
        Where this line's goods are now, not where they landed.

        The first step on the route still holding any of them. Defaulting
        to where they landed instead meant the second hop looked at an
        empty bay and reported nothing to move, which is what happens
        when a position is assumed rather than asked.
        """
        for step in self.route_steps():
            if step is not None and self.quantity_at(step) > 0:
                return step
        return self.arrived_at()

    def quarantined_quantity(self):
        """How much of this line is sitting somewhere it cannot ship from."""
        total = Decimal("0")
        for step in self.route_steps():
            if step is not None and step.is_quarantine:
                total += self.quantity_at(step)
        return total

    def next_step_from(self, warehouse=None):
        warehouse = warehouse or self.current_step()
        return self.warehouse.next_receipt_step(warehouse)

    @transaction.atomic
    def advance(self, quantity=None, from_warehouse=None, occurred_at=None):
        """
        Move goods to the next place on their way in.

        A transfer, because that is what it is: the goods were received,
        owned and valued when they arrived, and moving them between the
        bay and the shelf changes where they are and not what they are
        worth. Reusing StockTransfer also means the reverse path, the
        batch and bin handling and the value arithmetic are the ones
        already written and tested, rather than a second set that drifts.

        Clearing an inspection is `GoodsReceipt.accept()` and not this:
        goods leave quarantine when somebody has looked at them, which is
        a decision with its own record.
        """
        from apps.inventory.models import StockTransfer, StockTransferLine

        if not self.receipt.posted:
            raise ValidationError("Only a posted receipt has goods to move.")
        origin = from_warehouse or self.current_step()
        if origin.is_quarantine:
            raise ValidationError(
                f"{origin} holds goods awaiting inspection. Accept or reject them on "
                "the receipt; they do not simply move on."
            )
        destination = self.warehouse.next_receipt_step(origin)
        if destination is None:
            raise ValidationError(
                f"{self.order_line.item} is already at {origin}, the end of its route."
            )
        here = self.quantity_at(origin)
        quantity = Decimal(quantity) if quantity is not None else here
        if quantity <= 0:
            raise ValidationError("Nothing to move.")
        if quantity > here:
            raise ValidationError(
                f"Only {here} of {self.order_line.item} is at {origin}; cannot move "
                f"{quantity}."
            )

        transfer = StockTransfer.objects.create(
            transfer_date=to_date(occurred_at) or timezone.now().date(),
            from_warehouse=origin, to_warehouse=destination,
            reference=self.receipt.number,
            memo=f"Put away from {origin.code} for {self.receipt.number}",
        )
        StockTransferLine.objects.create(
            transfer=transfer, item=self.order_line.item,
            uom=self.order_line.item.uom, lot=self.lot,
            from_bin=self.bin if origin.pk == self.arrived_at().pk else None,
            quantity=self.order_line.item.to_stock_quantity(
                quantity, self.order_line.uom
            ),
        )
        transfer.post()
        return ReceiptRouteMove.objects.create(
            receipt_line=self, from_warehouse=origin, to_warehouse=destination,
            quantity=quantity, transfer=transfer,
        )

    def quantity_inspected(self):
        """How much of this line has been accepted or rejected."""
        return self.inspections.aggregate(
            total=models.Sum("quantity")
        )["total"] or Decimal("0")

    def quantity_uninspected(self):
        """
        How much is waiting to be looked at.

        Measured from what is actually in quarantine rather than from
        what was received, because a routed line reaches inspection a
        pallet at a time and the rest is still in the bay.
        """
        return self.quarantined_quantity() - self.quantity_inspected()

    def quantity_returned(self):
        """How much of this line posted returns have already sent back."""
        return self.return_lines.filter(receipt__posted=True).aggregate(
            total=models.Sum("quantity_received")
        )["total"] or Decimal("0")

    def quantity_returnable(self):
        return self.quantity_received - self.quantity_returned()

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(quantity_received__gt=0), name="received_quantity_positive"
            ),
        ]

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


class LandedCostApplication(AuditModel):
    """
    A capitalised charge from one bill applied to goods received on
    another.

    The same-bill path only works when the carrier invoices on the
    vendor's bill, which for anything imported is the rare case: freight,
    duty and the customs broker arrive as three separate bills, weeks
    apart, from three parties who never met. Without this they expense,
    and the stock is carried at less than it cost to land.
    """

    charge_line = models.ForeignKey(
        BillLine, on_delete=models.PROTECT, related_name="landed_cost_applications"
    )
    receipt_line = models.ForeignKey(
        GoodsReceiptLine, on_delete=models.PROTECT, related_name="landed_costs"
    )
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    date = models.DateField()
    journal_entry = models.ForeignKey(
        JournalEntry, on_delete=models.PROTECT, related_name="+", editable=False
    )
    stock_movement = models.ForeignKey(
        StockMovement, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )
    released_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
        help_text="Set when this allocation was released.",
    )

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="landed_cost_amount_positive"),
        ]

    def __str__(self):
        return f"{self.charge_line} -> {self.receipt_line} ({self.amount})"

    def is_released(self):
        return bool(self.released_entry_id)

    @transaction.atomic
    def release(self, on_date=None):
        """
        Undo the allocation: take the value back out of stock and return
        it to the expense it came from.

        Written in the same sitting as the allocation, because a costing
        decision made weeks after the goods arrived is exactly the kind
        that gets revised.
        """
        if self.is_released():
            raise ValidationError("This allocation has already been released.")
        entry = self.journal_entry.create_reversal(
            entry_date=to_date(on_date) or timezone.now().date(),
            memo=f"Landed cost released from {self.receipt_line.order_line.item}",
        )
        StockMovement.objects.create(
            item=self.receipt_line.order_line.item,
            warehouse=self.receipt_line.warehouse,
            movement_type=MovementType.ADJUSTMENT,
            uom=self.receipt_line.order_line.item.uom,
            lot=self.receipt_line.lot, bin=self.receipt_line.bin,
            adjusts=self.receipt_line.stock_movement,
            quantity=Decimal("0"),
            value_adjustment=-self.amount,
            reference=self.charge_line.bill.number,
            occurred_at=timezone.now(),
            notes=f"Landed cost released from {self.charge_line.bill.number}",
        )
        self.released_entry = entry
        self.save(update_fields=["released_entry", "updated_at"])
        return entry


class ReceiptRouteMove(AuditModel):
    """
    One hop a receipt line made on its way in.

    Recorded rather than recomputed, so a line half put away can say how
    much is still in the bay. It holds the transfer it produced rather
    than describing it — a second record that merely describes the first
    is a record that can disagree with it.
    """

    receipt_line = models.ForeignKey(
        GoodsReceiptLine, on_delete=models.CASCADE, related_name="route_moves"
    )
    from_warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="+"
    )
    to_warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="+"
    )
    quantity = models.DecimalField(
        max_digits=18, decimal_places=4, help_text="In the order line's unit."
    )
    transfer = models.ForeignKey(
        "inventory.StockTransfer", on_delete=models.PROTECT, related_name="+",
        editable=False,
    )

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                check=Q(quantity__gt=0), name="receipt_route_move_quantity_positive"
            ),
        ]

    def __str__(self):
        return (
            f"{self.quantity} {self.from_warehouse.code}->{self.to_warehouse.code}"
        )


class ReceiptInspection(AuditModel):
    """
    One decision about goods held for inspection.

    Kept as a record rather than a flag because "these failed" is vendor
    performance data, and a flag flipped back to accepted after a rework
    would erase the fact that they ever failed.
    """

    receipt_line = models.ForeignKey(
        GoodsReceiptLine, on_delete=models.PROTECT, related_name="inspections"
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    accepted = models.BooleanField()
    inspected_on = models.DateField()
    warehouse = models.ForeignKey(
        Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Where accepted goods were cleared to.",
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-inspected_on", "-id"]
        constraints = [
            models.CheckConstraint(check=Q(quantity__gt=0), name="inspection_quantity_positive"),
        ]

    def __str__(self):
        verdict = "accepted" if self.accepted else "rejected"
        return f"{self.quantity} {verdict} on {self.inspected_on}"
