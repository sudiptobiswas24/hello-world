import datetime
from collections import defaultdict
from decimal import ROUND_CEILING, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from django.db.models import Q

from apps.accounting.defaults import default_account
from apps.accounting.mixins import (
    PostedLineMixin,
    PostedTaxDocumentMixin,
    RecordedLineTax,
    TaxedDocumentMixin,
    TaxedLineMixin,
)
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
from apps.accounting.trade_terms import FreightTerms, Incoterm
from apps.core.approvals import ApprovableMixin, ApprovalStatus
from apps.core.models import (
    Extensible,
    AuditModel,
    Company,
    Currency,
    DocumentSequence,
    Party,
    PartyRole,
    PaymentTerms,
    UnitOfMeasure,
    lock_rows,
    only_one,
    prefetched,
    serialised,
    to_date,
)
from apps.inventory.models import (
    Item,
    MovementType,
    StockMovement,
    Warehouse,
    lock_position,
    lock_positions,
    plan_putaway,
)
from apps.accounting.settlement import (
    allocated_on,
    amount_overdue,
    installment_schedule,
    oldest_overdue,
    owed_beyond,
    post_drawdown,
    post_settlement_fx,
    refuse_other_control_account,
    booked_beside,
    settlement_discount_to_take,
    standing_on_account,
    withdraw_discount,
    undone_by_note,
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


def _names_another(row, field):
    """New, or `field` now names another party than the one stored: when a role is first known to matter."""
    if row._state.adding or row.pk is None:
        return True
    return not type(row)._base_manager.filter(pk=row.pk, **{f"{field}_id": getattr(row, f"{field}_id")}).exists()


def _while(document, statuses, change):
    """
    Refuse `change` unless the document stands in one of `statuses`, said
    in words. Asked of the stored status, not the one in hand: a line
    holds its parent as it was read, and two people's screens are not
    each other's.
    """
    if document.pk is None:
        return
    status = type(document)._base_manager.filter(pk=document.pk).values_list("status", flat=True).first()
    if status is not None and status not in statuses:
        label = dict(type(document)._meta.get_field("status").choices).get(status, status)
        raise ValidationError(f"{document} is {str(label).lower()}: {change}")


def _whole_save(kwargs):
    """A save of every field, which a document's own steps never make: they name theirs."""
    return kwargs.get("update_fields") is None


class VendorStanding(models.TextChoices):
    APPROVED = "approved", "Approved"
    TRIAL = "trial", "On trial: an order to them is approved first"
    BLOCKED = "blocked", "Blocked: no new order"


class VendorProfile(AuditModel):
    """
    Purchasing's settings for a vendor, the mirror of the customer's
    profile in sales: whether we buy from them at all, whether their
    money is held, how their goods travel to us, how long they take.
    A vendor with no profile is approved, holds nothing and states none.
    """

    party = models.OneToOneField(Party, on_delete=models.CASCADE, related_name="vendor_profile")
    standing = models.CharField(max_length=16, choices=VendorStanding.choices, default=VendorStanding.APPROVED)
    standing_reason = models.CharField(max_length=255, blank=True,
                                       help_text="Why they are on trial or blocked; whoever meets it is told.")
    payment_hold = models.BooleanField(
        default=False, help_text="Nothing is paid to them until it is lifted: a dispute, a quality claim.")
    payment_hold_reason = models.CharField(max_length=255, blank=True)
    lead_time_days = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="How long they usually take, where no agreed price for the item says: planning reads it.")
    # How their goods travel to us. A new order records them as they are that day, and its paper prints them.
    freight_terms = models.CharField(max_length=16, choices=FreightTerms.choices, blank=True)
    incoterm = models.CharField(max_length=3, choices=Incoterm.choices, blank=True, help_text="For an import.")
    port_of_loading = models.CharField(max_length=64, blank=True, help_text="For an import: Shanghai.")
    our_account_number = models.CharField(max_length=64, blank=True,
                                          help_text="What they call us: printed on our orders to them.")

    class Meta:
        permissions = [
            ("set_vendor_standing", "Can approve, put on trial or block a vendor"),
            ("hold_vendor_payments", "Can hold or release a vendor's payments"),
        ]

    def __str__(self):
        return f"Purchasing profile for {self.party}"

    def save(self, *args, **kwargs):
        self.standing_reason = self.standing_reason.strip()
        self.payment_hold_reason = self.payment_hold_reason.strip()
        if self.standing != VendorStanding.APPROVED and not self.standing_reason:
            raise ValidationError({"standing_reason": ["Say why they are on trial or blocked."]})
        if self.payment_hold and not self.payment_hold_reason:
            raise ValidationError({"payment_hold_reason": ["Say why their payments are held."]})
        super().save(*args, **kwargs)


def vendor_profile(party_id):
    return VendorProfile.objects.filter(party_id=party_id).first()


def refuse_held_payment(payment):
    """A payment out to a vendor whose payments are held: why it is refused, or None. Money in is taken."""
    if payment.direction != PaymentDirection.DISBURSEMENT:
        return None
    reason = VendorProfile.objects.filter(party_id=payment.party_id, payment_hold=True).values_list(
        "payment_hold_reason", flat=True).first()
    if reason is None:
        return None
    return f"{payment.party}'s payments are held ({reason}): nothing is paid to them until purchasing lifts it."


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
        self._check_terms()

    def save(self, *args, **kwargs):
        # In save() as well: ModelSerializer never calls clean(), and the
        # office writes through the API. Only the admin ever asked this.
        self._check_terms()
        with transaction.atomic():
            if self.is_preferred:
                # One vendor is preferred for an item: preferring another
                # hands it over, or preferred_vendor() picks between them by
                # the order the rows happen to come in. The same vendor's
                # other quantity breaks keep theirs.
                VendorPrice.objects.filter(item_id=self.item_id, is_preferred=True).exclude(
                    vendor_id=self.vendor_id).update(is_preferred=False, updated_at=timezone.now())
            super().save(*args, **kwargs)

    def _check_terms(self):
        if _names_another(self, "vendor"):
            _require_vendor_role(self.vendor)
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValidationError("valid_to cannot be before valid_from.")

    def covers(self, on_date=None):
        on_date = to_date(on_date) or timezone.localdate()
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

    def save(self, *args, **kwargs):
        if _whole_save(kwargs):
            _while(self, [RfqStatus.DRAFT], "a request is changed while it is a draft. Cancel it and ask again.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _while(self, [RfqStatus.DRAFT], "only a draft is deleted; an issued request is cancelled.")
        return super().delete(*args, **kwargs)

    @serialised("status")
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

    @serialised("status")
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

    @serialised("status")
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

        for line in lines:
            asked = line.requisition_line
            if asked is not None and line.quantity > asked.quantity - asked.quantity_ordered():
                raise ValidationError(
                    f"{asked.item} on {asked.requisition} was ordered in the meantime; "
                    "cancel that order, or cancel this request.")

        order_date = to_date(order_date) or timezone.localdate()
        order = PurchaseOrder.objects.create(
            vendor=invitation.vendor, order_date=order_date,
            reference=self.number, currency=self.currency,
        )
        for line in lines:
            quote = quotes[line.pk]
            PurchaseOrderLine.objects.create(
                order=order, item=line.item, uom=line.uom,
                requisition_line=line.requisition_line,
                warehouse=line.requisition_line.warehouse if line.requisition_line_id else None,
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

        for requisition in {line.requisition_line.requisition for line in lines if line.requisition_line_id}:
            requisition.mark_ordered()

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

    # Vendors quote on these lines and the award orders them: changed after
    # the request goes out, the quotes and the order read different things.
    LINES_FIXED = "its lines change while it is a draft, before anyone quotes on them."

    def save(self, *args, **kwargs):
        _while(self.rfq, [RfqStatus.DRAFT], self.LINES_FIXED)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _while(self.rfq, [RfqStatus.DRAFT], self.LINES_FIXED)
        return super().delete(*args, **kwargs)


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
        self._check_vendor()

    def save(self, *args, **kwargs):
        # In save() as well: ModelSerializer never calls clean(), and the
        # office writes through the API. Only the admin ever asked this.
        self._check_vendor()
        if self._state.adding:
            # A vendor may be asked late, while answers are still coming in.
            _while(self.rfq, [RfqStatus.DRAFT, RfqStatus.SENT],
                   "vendors are asked before it is awarded or cancelled.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _while(self.rfq, [RfqStatus.DRAFT], "a vendor asked is an answer owed; take them off only a draft.")
        return super().delete(*args, **kwargs)

    def _check_vendor(self):
        if _names_another(self, "vendor"):
            _require_vendor_role(self.vendor)

    @serialised("declined")
    def decline(self, note=""):
        """A vendor saying no is an answer, and worth keeping."""
        _while(self.rfq, [RfqStatus.SENT], "a vendor declines while the request is out.")
        if self.quotes.exists():
            raise ValidationError("This vendor has already quoted.")
        self.declined = True
        self.responded_at = timezone.now()
        self.notes = note[:255] or self.notes
        self.save(update_fields=["declined", "responded_at", "notes", "updated_at"])

    @serialised("declined", "responded_at")
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

    def save(self, *args, **kwargs):
        # A quote given while the request was out; after the award it is the
        # record of how the vendor was chosen, and moves no more.
        _while(self.invitation.rfq, [RfqStatus.SENT], "quotes are given while the request is out.")
        with transaction.atomic():
            # The mirror of decline(), which refuses once there is a quote: under the invitation's lock,
            # so a decline and a quote arriving together cannot both stand.
            lock_rows(self.invitation)
            if self.invitation.declined:
                raise ValidationError(f"{self.invitation.vendor} declined this request; a quote from them is not "
                                      "taken on it.")
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _while(self.invitation.rfq, [RfqStatus.SENT], "quotes are given while the request is out.")
        return super().delete(*args, **kwargs)


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
        self._check_requester()

    def save(self, *args, **kwargs):
        # In save() as well: ModelSerializer never calls clean(), and the
        # office writes through the API. Only the admin ever asked this.
        self._check_requester()
        if _whole_save(kwargs):
            # What was approved is what gets ordered: the approval-withdrawal
            # hole orders had, here before anyone could reach it.
            _while(self, [RequisitionStatus.DRAFT],
                   "a requisition is changed while it is a draft. Cancel it and ask again.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _while(self, [RequisitionStatus.DRAFT], "only a draft is deleted; a submitted one is cancelled.")
        return super().delete(*args, **kwargs)

    def _check_requester(self):
        if _names_another(self, "requested_by"):
            _require_employee_role(self.requested_by)

    def estimated_total(self):
        return sum((line.estimated_value() for line in self.lines.all()), Decimal("0"))

    @serialised("status")
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

    @serialised("status")
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

    @serialised("status")
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

    @serialised("status")
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
            order_date=to_date(order_date) or timezone.localdate(),
            reference=self.number,
        )
        for line in selected:
            remaining = line.quantity - line.quantity_ordered()
            PurchaseOrderLine.objects.create(
                order=order, requisition_line=line, item=line.item, uom=line.uom,
                expense_account=line.expense_account, warehouse=line.warehouse,
                quantity=remaining,
                unit_price=resolve_purchase_price(
                    line.item, vendor, quantity=remaining,
                    currency=order.currency, on_date=order.order_date,
                ) or line.estimated_price,
                expected_date=self.needed_by,
            )

        self.mark_ordered()
        return order

    def mark_ordered(self):
        """Ordered once every line is, by an order made from it or a request awarded on it."""
        if self.status == RequisitionStatus.APPROVED and all(
            line.quantity_ordered() >= line.quantity for line in self.lines.all()
        ):
            self.status = RequisitionStatus.ORDERED
            self.save(update_fields=["status", "updated_at"])

    @serialised("status")
    def create_rfq(self, lines=None, issue_date=None, response_due=None):
        """What is left of the approved request, out for quotes on one request for quotation."""
        return request_quotes(lines if lines is not None else self.lines.all(),
                              issue_date=issue_date, response_due=response_due)


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
    warehouse = models.ForeignKey(
        Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Where it is wanted. Carried to the order line, and what planning "
                  "counts it as coming to.",
    )

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                check=Q(quantity__gt=0), name="requisition_line_quantity_positive"
            ),
        ]

    def __str__(self):
        return f"{self.item} x{self.quantity}"

    LINES_FIXED = "its lines change while it is a draft; what is approved is what is ordered."

    def save(self, *args, **kwargs):
        _while(self.requisition, [RequisitionStatus.DRAFT], self.LINES_FIXED)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _while(self.requisition, [RequisitionStatus.DRAFT], self.LINES_FIXED)
        return super().delete(*args, **kwargs)

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

    def quantity_quoting(self):
        """How much of this request is out for quotes on a request not yet awarded or cancelled."""
        return self.rfq_lines.filter(rfq__status__in=[RfqStatus.DRAFT, RfqStatus.SENT]).aggregate(
            total=models.Sum("quantity"))["total"] or Decimal("0")

    def quantity_open(self):
        """What is left to order or to ask about: nothing or more."""
        return max(self.quantity - self.quantity_ordered() - self.quantity_quoting(), Decimal("0"))


def request_quotes(lines, issue_date=None, response_due=None):
    """
    One request for quotation for what is left of the approved
    requisition lines given: not ordered yet, not already out for
    quotes. The vendor each line had in mind, else the one with the
    agreed price, is invited; the buyer asks others on the request.
    Lines from several requisitions make one request, which then names
    none of them as its own; each line still names its own.
    """
    lines = list(lines)
    with transaction.atomic():
        if lines:
            lock_rows(*lines)
        wanted = []
        for line in lines:
            requisition = line.requisition
            if requisition.status != RequisitionStatus.APPROVED:
                raise ValidationError(
                    f"{requisition} is {requisition.get_status_display().lower()}; "
                    "only an approved requisition is put out for quotes.")
            left = line.quantity_open()
            if left > 0:
                wanted.append((line, left))
        if not wanted:
            raise ValidationError("Every line given is ordered already or out for quotes.")
        requisitions = []
        for line, _ in wanted:
            if line.requisition not in requisitions:
                requisitions.append(line.requisition)
        rfq = RequestForQuotation.objects.create(
            requisition=requisitions[0] if len(requisitions) == 1 else None,
            issue_date=to_date(issue_date) or timezone.localdate(),
            response_due=to_date(response_due),
            currency=Currency.objects.filter(is_base=True).first(),
            description="For " + ", ".join(requisition.number for requisition in requisitions),
        )
        vendors = []
        for line, left in wanted:
            RfqLine.objects.create(rfq=rfq, item=line.item, uom=line.uom, quantity=left,
                                   requisition_line=line, notes=line.notes)
            vendor = line.suggested_vendor or preferred_vendor(line.item, line.requisition.request_date)
            if vendor is not None and vendor not in vendors:
                vendors.append(vendor)
        for vendor in vendors:
            RfqInvitation.objects.create(rfq=rfq, vendor=vendor)
    return rfq


def open_requisition_lines():
    """
    Approved requisition lines with something left to order or to ask
    about, soonest needed first: [{line, ordered, quoting, open, vendor}],
    the vendor being the one the line had in mind or the agreed price's.
    """
    rows = []
    lines = PurchaseRequisitionLine.objects.filter(
        requisition__status=RequisitionStatus.APPROVED,
    ).select_related("requisition__requested_by", "item", "uom", "suggested_vendor").order_by(
        models.F("requisition__needed_by").asc(nulls_last=True), "requisition__number", "id")
    for line in lines:
        ordered, quoting = line.quantity_ordered(), line.quantity_quoting()
        left = line.quantity - ordered - quoting
        if left <= 0:
            continue
        rows.append({
            "line": line, "ordered": ordered, "quoting": quoting, "open": left,
            "vendor": line.suggested_vendor or preferred_vendor(line.item, line.requisition.request_date),
        })
    return rows


class SubcontractComponent(AuditModel):
    """
    A component the company supplies so the vendor can make the line's
    item.

    What this covers is the part that touches purchasing and the ledger:
    components leaving, a finished item arriving, and its cost being
    what the components cost plus what the vendor charged. The routing —
    which machine, for how long — stays with the job worker, and is
    theirs to run; their charge is what it cost.

    These rows used to be typed. That was right while there was no
    manufacturing module and wrong the day there was one: a bill of
    materials already says exactly what goes into a printed fabric, and
    a second hand-written copy of it on a purchase order is a stored
    derived fact that goes stale the first time the specification moves.
    A line that names a bill of materials gets these rows computed from
    it, and they then refuse to be edited by hand.
    """

    order_line = models.ForeignKey(
        "PurchaseOrderLine", related_name="components", on_delete=models.CASCADE
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="supplied_to")
    quantity_per = models.DecimalField(
        max_digits=18, decimal_places=6,
        help_text="How many of this component go into one of the finished item, "
                  "in the component's own stocking unit. Six places, not four: "
                  "a sack's sewing thread is about 0.0012 kg a bag, and four "
                  "places lose a fortieth of it on every sack.",
    )
    is_computed = models.BooleanField(
        default=False, editable=False,
        help_text="Set when a bill of materials works this row out. Such a row "
                  "refuses to be edited by hand: the edit would survive until "
                  "the next rebuild and no longer.",
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

    def delete(self, *args, **kwargs):
        if self.is_computed and not getattr(self, "_rebuilding", False):
            raise ValidationError(
                f"{self} is computed from {self.order_line.bom} and cannot be "
                "taken off on its own."
            )
        return super().delete(*args, **kwargs)

    def save(self, *args, **kwargs):
        """
        A component is specified against one of the finished item, which
        means one of its stocking units. An order line written in another
        unit would multiply every component by a factor nobody wrote down.

        The question belongs here and not on the order line: a line is
        created before its components are attached, so at line-save time
        there is nothing yet to say the line is subcontracted.
        """
        if self.is_computed and not getattr(self, "_rebuilding", False):
            raise ValidationError(
                f"{self} is computed from {self.order_line.bom}. Change the "
                "bill of materials, or take it off the line."
            )
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
        self._check_terms()

    def _check_terms(self):
        if _names_another(self, "vendor"):
            _require_vendor_role(self.vendor)
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError("A blanket order cannot end before it starts.")

    def save(self, *args, **kwargs):
        self._check_terms()
        if self._state.adding and self.vendor_id and not self.currency_id:
            self.currency = self.vendor.default_currency
        if _whole_save(kwargs):
            # The vendor and terms agreed are what releases are made under.
            _while(self, [BlanketStatus.DRAFT],
                   "an agreement is changed while it is a draft. Close it and agree another.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _while(self, [BlanketStatus.DRAFT], "only a draft is deleted; a confirmed agreement is closed.")
        return super().delete(*args, **kwargs)

    def total(self):
        return sum((line.committed_value() for line in self.lines.all()), Decimal("0"))

    def covers(self, on_date):
        on_date = to_date(on_date)
        return self.start_date <= on_date <= self.end_date

    @serialised("status")
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

    @serialised("status")
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

    @serialised("status")
    def release(self, quantities, order_date=None, expected_date=None):
        """
        Call off part of the commitment as a real purchase order.

        `quantities` is {blanket_line: quantity}. The price comes from the
        agreement, not from today's vendor price: the whole point of
        committing to a volume is that the price is fixed for it.
        """
        order_date = to_date(order_date) or timezone.localdate()
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

    # The price and volume agreed: releases already made were priced from
    # these, and the agreement is the record of what was committed.
    LINES_FIXED = "its lines change while it is a draft; releases are priced from them."

    def save(self, *args, **kwargs):
        _while(self.blanket, [BlanketStatus.DRAFT], self.LINES_FIXED)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _while(self.blanket, [BlanketStatus.DRAFT], self.LINES_FIXED)
        return super().delete(*args, **kwargs)

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
        Committed volume already called off, net of cancelled releases and
        of what a release closed short never brought.

        A cancelled release gives its volume back: the agreement is a
        commitment to buy, and an order that was called off and then
        called back off again was never bought. Nor was the rest of one
        the vendor closed short: 60 of 100 came, and the agreement went on
        counting all 100, so the 40 could never be called off again.
        """
        return sum((line.volume_called_off() for line in self.order_lines.exclude(
            order__status=OrderStatus.CANCELLED)), Decimal("0"))

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

    def save(self, *args, **kwargs):
        # One policy is in force: active() reads the first.
        with transaction.atomic():
            only_one(self, "is_active")
            super().save(*args, **kwargs)

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
        self._check_span()

    def save(self, *args, **kwargs):
        # In save() as well: ModelSerializer never calls clean(), and the
        # office writes through the API. Only the admin ever asked this.
        self._check_span()
        super().save(*args, **kwargs)

    def _check_span(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError("A budget cannot end before it starts.")

    def covers(self, on_date):
        on_date = to_date(on_date)
        return self.start_date <= on_date <= self.end_date

    @classmethod
    def for_account(cls, account, on_date):
        if account is None:
            return None
        on_date = to_date(on_date) or timezone.localdate()
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
        help_text="Round the order up to a whole case, pallet or bag size.",
    )
    minimum_order_quantity = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True,
        help_text="The least anybody will sell, or the least worth making. "
                  "Read by the plan: a vendor with a one-tonne minimum does "
                  "not deliver two hundred kilos because that is what the "
                  "arithmetic asked for.",
    )
    maximum_order_quantity = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True,
        help_text="The most that goes into one order — a silo, a mixer or a "
                  "lorry. A requirement larger than this is split into several "
                  "orders for the same date rather than one nobody can take.",
    )
    order_period_days = models.PositiveIntegerField(
        default=0,
        help_text="Order once for this many days of demand instead of once per "
                  "shortage. Nought is lot for lot, which is exact and raises "
                  "twelve orders for twelve daily call-offs; a week turns those "
                  "into two and carries a little stock for the privilege.",
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
            models.CheckConstraint(
                check=Q(minimum_order_quantity__isnull=True)
                | Q(minimum_order_quantity__gt=0),
                name="reorder_minimum_order_quantity_positive",
            ),
            models.CheckConstraint(
                check=Q(maximum_order_quantity__isnull=True)
                | Q(maximum_order_quantity__gt=0),
                name="reorder_maximum_order_quantity_positive",
            ),
            models.UniqueConstraint(
                fields=["item", "warehouse"], name="one_reorder_rule_per_item_and_warehouse"
            ),
        ]

    def __str__(self):
        return f"{self.item} at {self.warehouse}: {self.minimum} / {self.target}"

    def clean(self):
        self._check_levels()

    def save(self, *args, **kwargs):
        # In save() as well: ModelSerializer never calls clean(), and the
        # office writes through the API. Only the admin ever asked this.
        self._check_levels()
        super().save(*args, **kwargs)

    def _check_levels(self):
        if self.target is not None and self.minimum is not None and self.target < self.minimum:
            raise ValidationError("The target cannot be below the minimum.")
        least, most = self.minimum_order_quantity, self.maximum_order_quantity
        if least is not None and most is not None and most < least:
            raise ValidationError(
                f"The most that goes into one order ({most}) is less than the "
                f"least anybody will supply ({least}), so no order is possible."
            )
        if most is not None and self.multiple_of and most < self.multiple_of:
            raise ValidationError(
                f"One order holds at most {most} and the smallest whole unit "
                f"is {self.multiple_of}, so every order would be refused by "
                "one rule or the other."
            )
        if self.vendor_id and _names_another(self, "vendor"):
            _require_vendor_role(self.vendor)

    def on_order(self):
        """
        Confirmed purchases not yet received, into this warehouse or
        without a warehouse yet named.

        Counted because ordering again for stock already on its way is
        how a reorder rule turns one shortage into two months of excess.
        """
        lines = PurchaseOrderLine.objects.filter(
            item=self.item, order__status=OrderStatus.CONFIRMED, charge__isnull=True,
            # A drop-ship goes to the customer, never onto this shelf.
            order__drop_ship_for__isnull=True,
        ).filter(Q(warehouse=self.warehouse) | Q(warehouse__isnull=True))
        return sum((line.quantity_open() for line in lines), Decimal("0"))

    def committed(self):
        """Confirmed sales still to ship from this warehouse: not what a vendor drop-ships."""
        from apps.sales.models import OrderStatus as SalesOrderStatus
        from apps.sales.models import SalesOrderLine, quantities_awaited

        lines = list(SalesOrderLine.objects.filter(
            item=self.item, order__status=SalesOrderStatus.CONFIRMED, charge__isnull=True
        ).filter(Q(warehouse=self.warehouse) | Q(warehouse__isnull=True)))
        awaited = quantities_awaited(lines)
        return sum(
            (line.quantity_to_ship(awaited[line.pk]) for line in lines),
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


class PurchaseOrder(Extensible, TaxedDocumentMixin, ApprovableMixin, AuditModel):
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
    # The terms the order was placed on, as the vendor's stood that day: facts of the order, printed on it,
    # and what its bills take, never re-read from the vendor afterwards.
    payment_terms = models.ForeignKey(PaymentTerms, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    freight_terms = models.CharField(max_length=16, choices=FreightTerms.choices, blank=True)
    incoterm = models.CharField(max_length=3, choices=Incoterm.choices, blank=True)
    port_of_loading = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-order_date", "-id"]
        indexes = [models.Index(fields=["order_date", "id"], name="purchase_order_by_date")]
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

    def render_pdf(self):
        from .documents import render_purchase_order_pdf

        return render_purchase_order_pdf(self)

    def email_to_vendor(self, to=None, subject=None, body=None, user=None):
        """The order to the vendor, once confirmed. Returns the address used."""
        from apps.core.mail import send_document

        if self.status != OrderStatus.CONFIRMED:
            raise ValidationError("Only a confirmed order is sent to the vendor.")
        return send_document(self, self.vendor, "Purchase order", to=to, subject=subject, body=body, user=user)

    def __str__(self):
        return f"{self.number or f'PO-draft-{self.pk}'} {self.vendor}"

    def clean(self):
        self._check_vendor()

    def _check_vendor(self):
        if _names_another(self, "vendor"):
            _require_vendor_role(self.vendor)

    def save(self, *args, **kwargs):
        self._check_vendor()
        if self._state.adding and self.vendor_id:
            if not self.currency_id:
                self.currency = self.vendor.default_currency
            self.payment_terms = self.payment_terms or self.vendor.payment_terms
            profile = vendor_profile(self.vendor_id)
            if profile:
                self.freight_terms = self.freight_terms or profile.freight_terms
                self.incoterm = self.incoterm or profile.incoterm
                self.port_of_loading = self.port_of_loading or profile.port_of_loading
        elif self.pk:
            before = PurchaseOrder.objects.filter(pk=self.pk).values("freight_terms", "incoterm").first()
            if (before and (before["freight_terms"], before["incoterm"]) != (self.freight_terms, self.incoterm)
                    and GoodsReceipt.objects.filter(purchase_order=self, posted=True).exists()):
                # Goods have travelled, and been paid for in the freight, on the terms the order printed.
                raise ValidationError(f"{self} has goods in; its freight terms and Incoterm cannot change now.")
        super().save(*args, **kwargs)

    def approval_reasons(self):
        reasons = []
        profile = vendor_profile(self.vendor_id)
        if profile and profile.standing == VendorStanding.TRIAL:
            reasons.append(f"{self.vendor} is on trial ({profile.standing_reason}): an order to them is approved first.")
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

    @serialised("status")
    @serialised("status")
    def confirm(self):
        """
        Commit to the order. Until this happens it is a shopping list, and
        goods arriving against a shopping list are goods nobody agreed to
        buy — the purchasing mirror of confirming a sales order, and like it
        one at a time: two clicks confirmed twice and spent two numbers.
        """
        profile = vendor_profile(self.vendor_id)
        if profile and profile.standing == VendorStanding.BLOCKED:
            raise ValidationError(f"{self.vendor} is blocked ({profile.standing_reason}): no new order is "
                                  "placed with them until purchasing lifts it.")
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

    @serialised("status")
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
        # What a requisition asked for is no longer on order, so it is open
        # to order again: "ordered" was a stored fact this cancel unmade.
        PurchaseRequisition.objects.filter(
            status=RequisitionStatus.ORDERED, lines__order_lines__order=self,
        ).update(status=RequisitionStatus.APPROVED, updated_at=timezone.now())
        # A drop-ship called off: the customer's goods are this plant's to
        # send again, and its shelf's to hold for them.
        for line in self.lines.filter(sales_order_line__isnull=False).select_related("sales_order_line"):
            line.sales_order_line.reclaim_stock()

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
    def create_receipt(self, receipt_date=None, warehouse=None):
        """
        What is still to come on this order, as a draft receipt to cut down
        to what is on the lorry, name the batches of, and post. The mirror
        of sales' SalesOrder.create_delivery(): one waiting at a time, as a
        second draft for the same goods would receive them twice on paper.

        Each line arrives at the warehouse it names, else `warehouse`; a
        line with neither is refused by name rather than guessed at.
        """
        lock_rows(self)
        if self.status != OrderStatus.CONFIRMED:
            raise ValidationError("Only a confirmed order can be received against.")
        waiting = self.goods_receipts.filter(posted=False, reverses__isnull=True).first()
        if waiting is not None:
            raise ValidationError(
                f"Receipt {waiting.number or 'draft'} for this order is already waiting. "
                "Post it, or delete it, first.")
        owed = [(line, line.quantity_open()) for line in self.lines.select_related("item")]
        owed = [(line, quantity) for line, quantity in owed if quantity > 0]
        if not owed:
            raise ValidationError("Nothing is left to receive on this order.")
        for line, _ in owed:
            if not line.warehouse_id and warehouse is None:
                raise ValidationError({"warehouse": [
                    f"Say which warehouse {line.label()} arrives at: the line names none."]})
        receipt = GoodsReceipt.objects.create(
            purchase_order=self, receipt_date=to_date(receipt_date) or timezone.localdate())
        for line, quantity in owed:
            GoodsReceiptLine.objects.create(receipt=receipt, order_line=line,
                                            warehouse=line.warehouse or warehouse,
                                            quantity_received=quantity)
        return receipt

    @serialised("status")
    def create_bill(self, payable_account, bill_date=None, reference=""):
        """
        Draft a bill for whatever this order still owes the vendor,
        carrying prices, discounts and taxes across.

        Calling it twice bills the remainder, not the whole order again —
        the same drawdown Sales needed after an order was billed three
        times for one delivery. Twice at once, the second waits for the
        first and bills what is left after it.
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
            bill_date=bill_date or timezone.localdate(),
            reference=reference,
            purchase_order=self,
            payable_account=payable_account,
            currency=self.currency,
            # As the order agreed them, not as the vendor's stand today.
            payment_terms=self.payment_terms,
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

        # What is left to drop-ship is read below; two at once must not
        # both order the same remainder.
        lock_rows(sales_order)
        if sales_order.status != SalesOrderStatus.CONFIRMED:
            raise ValidationError("Only a confirmed sales order can be drop-shipped.")
        _require_vendor_role(vendor)

        # What is not already coming from a vendor: drop-shipping the same
        # remainder twice ordered it twice, and the second lorry was the
        # customer's to refuse and this company's to pay for.
        selected = [
            line for line in (lines if lines is not None else sales_order.lines.all())
            if not line.is_charge() and line.quantity_to_ship() > 0
        ]
        if not selected:
            raise ValidationError(
                "There is nothing left on this order to drop-ship: what is still owed "
                "is already on a drop-ship order.")

        order_date = to_date(order_date) or timezone.localdate()
        order = cls.objects.create(
            vendor=vendor, order_date=order_date,
            reference=sales_order.number, drop_ship_for=sales_order,
            shipping_note=f"Deliver direct to {sales_order.customer}",
        )
        for line in selected:
            remaining = line.quantity_to_ship()
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
    def issue_components(self, from_warehouse, occurred_at=None, quantities=None):
        """
        Send the components out to the subcontractor.

        They move warehouse; they do not leave the company. Stock at a
        subcontractor is still stock you own and still stock you can lose,
        and writing it off on despatch would hide both facts. A transfer
        keeps the value on the books where it belongs.

        By default, what the order still needs that has not gone yet: all
        of it the first time, the difference after the order grows, and
        nothing on a second click. `quantities` ({item: quantity}) sends
        exactly that instead, on top of what went: the top-up when the
        subcontractor scrapped some, which the receipt otherwise refuses as
        short and a plain re-issue would have called already sent.
        """
        lock_rows(self)
        if self.status != OrderStatus.CONFIRMED:
            raise ValidationError("Only a confirmed order can issue components.")
        if self.subcontract_warehouse_id is None:
            raise ValidationError(
                "Set a subcontract warehouse before issuing components; the stock has to "
                "sit somewhere it can still be counted."
            )
        occurred_at = occurred_at or timezone.now()

        required = {}
        for line in self.lines.filter(charge__isnull=True):
            for component in line.components.select_related("item"):
                if component.item.track_inventory:
                    required[component.item] = required.get(component.item, Decimal("0")) + round_money(
                        component.quantity_per * line.quantity)
        sent = dict(StockMovement.objects.filter(
            reference=self.number, movement_type=MovementType.TRANSFER_IN,
            warehouse=self.subcontract_warehouse_id,
        ).values_list("item").annotate(total=models.Sum("quantity")).values_list("item", "total"))
        if quantities is None:
            # What is still to go. Sent already counts whether or not it
            # was used: a second call sends nothing rather than all of it again.
            plan = {item: need - (sent.get(item.pk) or Decimal("0")) for item, need in required.items()}
        else:
            plan = {}
            for item, quantity in quantities.items():
                if item not in required:
                    raise ValidationError(f"{item} is not a component of {self}.")
                if quantity <= 0:
                    raise ValidationError(f"Send a quantity of {item} above nothing.")
                plan[item] = quantity
        plan = {item: quantity for item, quantity in plan.items() if quantity > 0}
        if not required:
            raise ValidationError("This order has no components to issue.")
        if not plan:
            raise ValidationError(f"The components for {self} have already been issued.")

        lock_positions(
            pair for item in plan for pair in ((item, from_warehouse), (item, self.subcontract_warehouse))
        )
        moved = []
        for item, quantity in plan.items():
            cost = item.removal_unit_cost(from_warehouse, quantity)
            for warehouse, movement_type, signed in (
                (from_warehouse, MovementType.TRANSFER_OUT, -quantity),
                (self.subcontract_warehouse, MovementType.TRANSFER_IN, quantity),
            ):
                StockMovement.objects.create(
                    item=item, warehouse=warehouse,
                    movement_type=movement_type, uom=item.uom,
                    quantity=signed,
                    unit_cost=cost, reference=self.number,
                    occurred_at=occurred_at,
                    notes=f"Components to subcontractor for {self.number}",
                )
            moved.append((item, quantity))
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
        """Paid up front and not since debited back."""
        return sum(
            (bill.total() - bill.amount_debited() for bill in self.prepayments()),
            Decimal("0"),
        )

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
        lock_rows(self)  # what is already prepaid is read below, and another prepayment could be adding to it
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
            bill_date=bill_date or timezone.localdate(),
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
    bom = models.ForeignKey(
        "manufacturing.BillOfMaterials", null=True, blank=True,
        on_delete=models.PROTECT, related_name="subcontract_lines",
        help_text="What goes into the item, when this line is job work. The "
                  "components are computed from it rather than typed, so a "
                  "specification that moves does not leave a stale copy of "
                  "itself on an open order. Rebuilt only while the order is a "
                  "draft: the job worker was sent what the order said at the "
                  "time.",
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
    closed_short_at = models.DateTimeField(null=True, blank=True, editable=False)
    closed_short_reason = models.CharField(max_length=255, blank=True, editable=False)
    work_order_operation = models.ForeignKey(
        "manufacturing.WorkOrderOperation", null=True, blank=True,
        on_delete=models.PROTECT, related_name="purchase_lines",
        help_text="The outside step of a run this line pays a vendor for — "
                  "lamination sent out mid-routing, say. Its receipt puts the "
                  "vendor's charge into the run's work in progress through "
                  "the goods-received accrual, rather than posting nothing "
                  "and expensing the bill as an ordinary service would.",
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
    warehouse = models.ForeignKey(
        Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Where it is to be received. Planning counts it as coming there; "
                  "blank, it is counted as coming to whichever warehouse is planned, "
                  "which is right only on one site. A receipt line that names no "
                  "warehouse takes this one.",
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

    def _check_outside_step(self):
        """
        A line paying for a run's outside step is exactly that and
        nothing else.

        Each refusal is a line whose receipt could not honestly post
        through the run: a stocked item would want to go on a shelf
        rather than into work in progress; whole-item job work already
        has its own receipt path and would be booked twice; a drop-ship
        never touches this plant; and a step that is ours would get a
        vendor's charge beside our own machine time. The unit has to be
        the run's own because what comes back is counted against what
        the run was planned to make.
        """
        if self.work_order_operation_id is None:
            return
        operation = self.work_order_operation
        order = operation.work_order
        if not operation.is_outside:
            raise ValidationError(
                f"{operation} is done on our own machines; a vendor has "
                "nothing to charge it for."
            )
        if self.item_id is None or self.item.track_inventory:
            raise ValidationError(
                "A line paying for an outside step is for the vendor's "
                "service, not a stocked item — nothing comes back to a shelf."
            )
        if self.bom_id is not None:
            raise ValidationError(
                "This line is whole-item job work already. Paying for a run's "
                "outside step as well would book the vendor's work twice."
            )
        if self.order_id and self.order.is_drop_ship():
            raise ValidationError(
                "A drop-ship never passes through this plant, so it cannot "
                "pay for a step of one of its runs."
            )
        if self.uom_id != order.uom_id:
            raise ValidationError(
                f"{order} is counted in {order.uom} and this line in "
                f"{self.uom}. What comes back from the vendor is counted "
                "against what the run was planned to make, so the two have "
                "to count the same thing."
            )

    @transaction.atomic
    def save(self, *args, **kwargs):
        self._check_outside_step()
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
                    # quantity_released() already counts this line as stored,
                    # so compare against the agreement net of it.
                    others = self.blanket_line.quantity_released() - previous.volume_called_off()
                    if others + self.volume_called_off() > self.blanket_line.quantity:
                        raise ValidationError(
                            f"The agreement commits {self.blanket_line.quantity} of "
                            f"{self.item}; releasing {others + self.volume_called_off()} would "
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
        self._check_bom()
        self._check_drop_ship_quantity()
        super().save(*args, **kwargs)
        if self.sales_order_line_id is not None:
            self.sales_order_line.reclaim_stock()
        if self.bom_id is not None:
            if self.order.status == OrderStatus.DRAFT:
                self.rebuild_components()
        else:
            # A line that no longer names a bill of materials must not
            # keep the rows it computed from one: they refuse to be
            # edited or deleted, so they would sit there for ever being
            # sent to a job worker with nothing behind them.
            for row in list(self.components.filter(is_computed=True)):
                row._rebuilding = True
                row.delete()

    def _check_bom(self):
        """
        Everything that could be wrong about the bill of materials on a
        job-work line, asked while the line is still a draft rather than
        when the fabric is already on the job worker's floor.
        """
        if self.bom_id is not None and self.order.status != OrderStatus.DRAFT:
            previous = (
                PurchaseOrderLine.objects.filter(pk=self.pk).first()
                if self.pk else None
            )
            if previous is None or previous.bom_id != self.bom_id:
                raise ValidationError(
                    f"{self.order} is "
                    f"{self.order.get_status_display().lower()}; a bill of "
                    "materials has to be named while the order is still a "
                    "draft. The job worker was sent what the order said at the "
                    "time, and the components here are what the receipt will "
                    "consume."
                )
        if self.bom_id is None:
            return
        if self.item_id is None:
            raise ValidationError(
                "A line that names a bill of materials must name the item it "
                "makes."
            )
        if self.bom.item_id != self.item_id:
            raise ValidationError(
                f"{self.bom} makes {self.bom.item}, and this line is for "
                f"{self.item}."
            )
        if not self.bom.is_active:
            raise ValidationError(f"{self.bom} is not active.")
        if not self.bom.components.exists():
            raise ValidationError(
                f"{self.bom} has no components, so nothing would be sent to the "
                "job worker and the finished item would be worth only what they "
                "charged."
            )

    @serialised()
    def rebuild_components(self):
        """
        Make the component rows say what the bill of materials says.

        Per ONE stocking unit of the finished item, which is the unit
        `quantity_per` has always been in — so a bill written per
        thousand sacks is divided down once here rather than multiplied
        out at every receipt.

        Replaced rather than diffed: a component the product stopped
        using has to disappear, and a diff that forgets to delete is how
        a job worker ends up being sent a masterbatch nobody has used
        for two years.
        """
        batch = self.item.to_stock_quantity(
            self.bom.quantity_produced, self.bom.uom
        )
        if not batch:
            raise ValidationError(f"{self.bom} says it makes nothing.")
        for row in list(self.components.all()):
            row._rebuilding = True
            row.delete()
        for component in self.bom.components.select_related("item", "uom"):
            gross = component.item.to_stock_quantity(
                component.gross_quantity(), component.uom
            )
            per_unit = (gross / batch).quantize(Decimal("0.000001"))
            if not per_unit:
                raise ValidationError(
                    f"{component.item} works out at less than a millionth of "
                    f"{self.item.uom} per {self.item}, which rounds to nothing. "
                    "Write the bill of materials for a larger batch, or take "
                    "the component off it."
                )
            row = SubcontractComponent(
                order_line=self, item=component.item,
                quantity_per=per_unit, is_computed=True,
            )
            row._rebuilding = True
            row.save()

    def delete(self, *args, **kwargs):
        if self.order_id and self.order.approved_at:
            self.order.withdraw_approval()
        if self.quantity_received() or self.quantity_billed():
            raise ValidationError(
                "This line has been received or billed and can no longer be removed."
            )
        sales_line = self.sales_order_line
        result = super().delete(*args, **kwargs)
        if sales_line is not None:
            sales_line.reclaim_stock()
        return result

    def _check_drop_ship_quantity(self, reopening=False):
        """
        A drop-ship line delivers its customer's line, and may still bring no more than the customer is
        still owed, less what other drop-ship lines are bringing. Checked
        on every save, so an edited quantity orders no more twice than a
        second create_for_drop_ship() may.
        """
        if self.sales_order_line_id is None:
            return
        sales_line = self.sales_order_line
        # Its receipt posts a delivery of the customer's line, so it must
        # be for that line, on that order's drop-ship, in its unit: one
        # pointed anywhere else shipped the customer goods nobody sent.
        if self.order.drop_ship_for_id != sales_line.order_id:
            raise ValidationError({"sales_order_line": [
                f"{sales_line.label()} is on {sales_line.order}; only a drop-ship order raised "
                "for it may deliver it."]})
        unit = self.uom_id or (self.item.uom_id if self.item_id else None)
        if self.is_charge() or self.item_id != sales_line.item_id or \
                unit != (sales_line.uom_id or sales_line.item.uom_id):
            raise ValidationError({"sales_order_line": [
                f"A drop-ship line delivers {sales_line.label()} itself: the same item, counted "
                f"in {sales_line.uom or sales_line.item.uom}."]})
        if self.order.status == OrderStatus.CANCELLED or (self.is_closed_short() and not reopening):
            return
        # What the other drop-ships bring is read next; two edits at once
        # must not both fit the same room. The sales order, as
        # create_for_drop_ship() takes it, so the two queue in one order.
        lock_rows(sales_line.order, refresh=False)
        others = drop_ship_awaited([self.sales_order_line_id], excluding=self.pk)
        room = self.sales_order_line.quantity_open() - others[self.sales_order_line_id]
        coming = self.quantity - (self.quantity_received() if self.pk else Decimal("0"))
        if coming > room:
            def shown(value):
                return format(value.normalize(), "f")
            raise ValidationError({"quantity": [
                f"{self.sales_order_line.label()} is owed {shown(max(room, Decimal('0')))} more "
                f"than other drop-ships bring; this line would bring {shown(coming)}."]})

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
        if prefetched(self, "receipt_lines"):
            return sum(
                (row.quantity_received if row.receipt.reverses_id is None
                 else -row.quantity_received
                 for row in self.receipt_lines.all() if row.receipt.posted),
                Decimal("0"),
            )
        received = self.receipt_lines.filter(
            receipt__posted=True, receipt__reverses__isnull=True
        ).aggregate(total=models.Sum("quantity_received"))["total"] or Decimal("0")
        returned = self.receipt_lines.filter(
            receipt__posted=True, receipt__reverses__isnull=False
        ).aggregate(total=models.Sum("quantity_received"))["total"] or Decimal("0")
        return received - returned

    def is_fully_received(self):
        """Everything came, or the rest never will (closed short)."""
        return self.is_closed_short() or self.quantity_received() >= self.quantity

    def is_closed_short(self):
        return self.closed_short_at is not None

    def volume_called_off(self):
        """What this line takes from its blanket agreement: all of it, or once closed short what came."""
        return self.quantity_received() if self.is_closed_short() else self.quantity

    def quantity_open(self):
        """What is still to come from the vendor: nothing for a charge or once closed short."""
        if self.charge_id is not None or self.is_closed_short():
            return Decimal("0")
        return max(self.quantity - self.quantity_received(), Decimal("0"))

    @serialised("closed_short_at")
    def close_short(self, reason):
        """
        The vendor will send no more: nothing further is expected,
        planned as supply, or awaited by a customer on a drop-ship. The
        mirror of a sales line's close_short(); without it a short
        delivery was supply for ever, and a drop-ship sent back to the
        vendor left its customer's line owed by nobody.
        """
        if self.is_charge():
            raise ValidationError("A charge is not received; there is nothing to close.")
        if self.is_closed_short():
            raise ValidationError(f"{self.label()} is already closed short.")
        if self.quantity_open() <= 0:
            raise ValidationError(f"{self.label()} is already received in full; there is nothing to close.")
        received, billed = self.quantity_received(), self.quantity_billed()
        if billed > received:
            raise ValidationError(
                f"{self.label()} is billed for {billed} and {received} received. Raise a debit "
                "note for the difference first: closed short, it may bill only what came.")
        reason = " ".join((reason or "").split())
        if not reason:
            raise ValidationError("Say why the rest will not come.")
        self.closed_short_at, self.closed_short_reason = timezone.now(), reason[:255]
        super().save(update_fields=["closed_short_at", "closed_short_reason", "updated_at"])
        if self.sales_order_line_id is not None:
            # No longer coming from the vendor: the shelf's to send, and hold.
            self.sales_order_line.reclaim_stock()

    @serialised("closed_short_at")
    def reopen(self):
        """Expected again after all."""
        if not self.is_closed_short():
            raise ValidationError(f"{self.label()} is not closed short.")
        if self.order.status != OrderStatus.CONFIRMED:
            raise ValidationError(f"{self.order} is {self.order.status}; nothing on it can be expected.")
        if self.sales_order_line_id is not None:
            # Reopened, it is awaited again: refused where the customer's
            # line is already being met another way.
            self._check_drop_ship_quantity(reopening=True)
        if self.blanket_line_id is not None:
            # Reopened, the rest is called off its agreement again: refused where the agreement has
            # since called it off on another order. Under the agreement's lock, as a release is.
            from apps.core.api import plain

            blanket = self.blanket_line.blanket
            lock_rows(blanket)
            back, left = self.quantity - self.quantity_received(), self.blanket_line.quantity_remaining()
            if back > left:
                raise ValidationError(
                    f"{blanket.number} has {plain(left)} of {self.item} left to call off; reopened, this "
                    f"line would call off {plain(back)} more.")
        self.closed_short_at, self.closed_short_reason = None, ""
        super().save(update_fields=["closed_short_at", "closed_short_reason", "updated_at"])
        if self.sales_order_line_id is not None:
            self.sales_order_line.reclaim_stock()

    def quantity_billed(self):
        """Net quantity billed: posted bills minus posted debit notes."""
        if prefetched(self, "bill_lines"):
            return sum(
                (row.quantity if row.bill.debits_id is None else -row.quantity
                 for row in self.bill_lines.all() if row.bill.posted),
                Decimal("0"),
            )
        billed = self.bill_lines.filter(
            bill__posted=True, bill__debits__isnull=True
        ).aggregate(total=models.Sum("quantity"))["total"] or Decimal("0")
        debited = self.bill_lines.filter(
            bill__posted=True, bill__debits__isnull=False
        ).aggregate(total=models.Sum("quantity"))["total"] or Decimal("0")
        return billed - debited

    def quantity_unbilled(self):
        return self.quantity - self.quantity_billed()

    def accrual_layers(self):
        """
        What each posted receipt of this line put into the accrual and
        still has there, oldest first: [(quantity, price, base cost)].

        Net of returns against each receipt line, and at the figures the
        receipt froze — the price agreed then, at the rate on the day it
        arrived. A receipt from before those figures were frozen booked
        the order's price as base currency, so it reads back that way.
        """
        rows = []
        lines = self.receipt_lines.filter(
            receipt__posted=True, receipt__reverses__isnull=True,
        ).select_related("receipt").order_by(
            "receipt__receipt_date", "receipt_id", "id"
        )
        for line in lines:
            returned = line.return_lines.filter(
                receipt__posted=True
            ).aggregate(total=models.Sum("quantity_received"))["total"]
            net = line.quantity_received - (returned or Decimal("0"))
            if net <= 0:
                continue
            price = (
                line.accrued_unit_price if line.accrued_unit_price is not None
                else self.unit_price
            )
            cost = (
                line.accrued_unit_cost if line.accrued_unit_cost is not None
                else self.unit_price * (line.receipt.exchange_rate or Decimal("1"))
            )
            rows.append((net, price, cost))
        return rows

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


class Bill(Extensible, PostedTaxDocumentMixin, TaxedDocumentMixin, AuditModel):
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
    posted_total = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True, editable=False,
        help_text="The total when it posted, in its own currency. A fact of the "
                  "posting, which cannot change; reports use it to pass over what "
                  "is already settled without working every total out again.",
    )
    is_prepayment = models.BooleanField(
        default=False, editable=False,
        help_text="Money paid to the vendor up front, held as an asset until the "
                  "goods arrive.",
    )
    corrects_old_supply = models.BooleanField(
        default=False, editable=False,
        help_text="A debit note with its own GST on a bill the old system booked: "
                  "it takes back input tax this month.",
    )
    old_bill_value = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True, editable=False,
        help_text="On such a note, what the old bill was for in all: no more is "
                  "debited against it.",
    )
    is_opening_balance = models.BooleanField(
        default=False, editable=False,
        help_text="Brought in at go-live (import_csv open_bills): what the old "
                  "system's bill still had owing. Its credit was claimed there; no "
                  "return here counts it.",
    )
    settlement_discount_amount = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True, editable=False,
        help_text="Early-settlement discount taken against this bill.",
    )
    settlement_discount_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )
    settlement_discount_withdrawn = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0"), editable=False,
        help_text="Of the discount for paying early, what was taken back when the payment that earned it "
                  "was returned.",
    )
    settlement_discount_withdrawal_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False,
    )
    # On a debit note: the settlement discount on its bill it undid, rather than claim it back from
    # the vendor as cash we never paid. Facts of the note's posting.
    reversed_discount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"), editable=False)
    settlement_reversal_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False,
    )

    class Meta:
        ordering = ["-bill_date", "-id"]
        indexes = [models.Index(fields=["bill_date", "id"], name="bill_by_date")]
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

    def tax_party(self):
        return self.vendor

    def corrected_document(self):
        return self.debits

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
        self._check_kind()

    def _check_kind(self):
        if _names_another(self, "vendor"):
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
        # Filtered here rather than in the query, so a caller's prefetch of
        # debit_notes is used instead of bypassed.
        return sum(
            (note.total() for note in self.debit_notes.all() if note.posted), Decimal("0")
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
        """
        What is still held. Less what was debited back, or a prepayment
        the vendor returned was drawn down again on the bill and the
        vendor was short-paid by it — the mirror of sales' deposits,
        where the same hole was found first.
        """
        if not self.is_prepayment:
            return Decimal("0")
        return self.total() - self.prepayment_applied() - self.amount_debited()

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
        on_date = to_date(on_date) or timezone.localdate()
        # What is left on the prepayment and due on the bill are each read
        # here, and another drawdown could spend either.
        lock_rows(self, prepayment)
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

        left = prepayment.prepayment_unapplied()
        available = min(left, self.amount_due())
        amount = round_money(Decimal(amount)) if amount is not None else available
        if amount <= 0:
            raise ValidationError("There is nothing left to draw down.")
        if amount > available:
            raise ValidationError(
                f"Only {available} can be drawn down here "
                f"({left} left on the prepayment, "
                f"{self.amount_due()} due on the bill)."
            )

        account = Company.get().vendor_prepayment_account
        if account is None:
            raise ValidationError("The company has no vendor prepayment account configured.")

        entry, fx_entry = post_drawdown(
            party=self.vendor, held_account=account,
            control_account=self.payable_account, amount=amount,
            held_rate=prepayment.exchange_rate, document_rate=self.exchange_rate,
            date=on_date, reference=self.number,
            memo=f"Prepayment {prepayment.number} applied to {self.number}",
            is_receivable=False,
        )
        return PrepaymentApplication.objects.create(
            bill=self, prepayment=prepayment, amount=amount, date=on_date,
            journal_entry=entry, fx_entry=fx_entry,
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
        return (to_date(as_of) or timezone.localdate()) <= deadline

    def settlement_discount_standing(self):
        """The discount for paying early, less what notes undid of it and what was withdrawn."""
        undone = sum((note.reversed_discount for note in self.debit_notes.filter(posted=True)), Decimal("0"))
        return (self.settlement_discount_amount or Decimal("0")) - undone - self.settlement_discount_withdrawn

    @serialised("settlement_discount_withdrawn", "settlement_discount_withdrawal_entry")
    def withdraw_unearned_discount(self, on_date=None):
        """
        Take back the discount for paying early once the payment that earned it is returned and the
        bill owes again; None where nothing stands or other payments still settle it. Asked under the
        lock: two returns at once take it back once.
        """
        standing = self.settlement_discount_standing()
        if standing <= 0 or self.amount_due() <= 0:
            return None
        entry = withdraw_discount(self, self.payable_account, self.vendor, standing, to_date(on_date) or timezone.localdate(),
                                  f"Settlement discount on {self.number} withdrawn: its payment was returned")
        self.settlement_discount_withdrawn += standing
        self.settlement_discount_withdrawal_entry = entry
        super(Bill, self).save(update_fields=[
            "settlement_discount_withdrawn", "settlement_discount_withdrawal_entry", "updated_at"])
        return entry

    @serialised("settlement_discount_amount", "settlement_discount_entry")
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
        on_date = to_date(on_date) or timezone.localdate()
        amount = settlement_discount_to_take(self, on_date, force)

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
        # Against what the bill still had after everything that settled
        # it, as sales absorbs credit notes: counting payments alone let a
        # note absorb the tax deducted, and the vendor's refund vanished.
        capacity = max(bill.total() - bill._settled_otherwise(), Decimal("0"))
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
        return self.total() - self.amount_absorbed() - self.reversed_discount - refunded

    def amount_due(self):
        if self.is_debit_note():
            return self.refund_due()
        # Debit notes can take a bill to zero but never below it. Beyond
        # that the money has already gone out, so what is left is cash owed
        # back, which lives on the note — a bill reading minus fifty says
        # the company owes a negative amount, which is not a thing.
        paid = self._settled_otherwise()
        offset = min(self.amount_debited(), max(self.total() - paid, Decimal("0")))
        return self.total() - paid - offset

    def _undo_settlements_without_money(self):
        """
        Past what it clears of its bill, a debit note undoes the settlement
        discount still standing on it before the vendor owes anything back:
        the discount was never paid, so it is not claimed back as cash.
        The mirror of the credit note's. Dr where the discount was booked /
        Cr payables, at the bill's rate.
        """
        bill = self.debits
        lock_rows(bill)
        earlier = list(bill.debit_notes.filter(posted=True).exclude(pk=self.pk))
        taken = bill.settlement_discount_amount or Decimal("0")
        _, discount = undone_by_note(
            self.total(), self.amount_absorbed(), write_off=Decimal("0"),
            discount=taken - bill.settlement_discount_withdrawn
            - sum((note.reversed_discount for note in earlier), Decimal("0")),
            discount_taken=taken, document_total=bill.total(),
            whole=sum((note.total() for note in earlier), self.total()) >= bill.total())
        if not discount:
            return
        base = round_money(discount * (bill.exchange_rate or Decimal("1")))
        memo = f"{self.number} undoes the discount taken on {bill.number}"
        entry = JournalEntry.objects.create(date=self.bill_date, reference=self.number, memo=memo)
        JournalLine.objects.create(entry=entry, account=booked_beside(bill.settlement_discount_entry, bill.payable_account),
                                   party=self.vendor, debit=base, description=memo[:255])
        JournalLine.objects.create(entry=entry, account=bill.payable_account, party=self.vendor, credit=base,
                                   description=memo[:255])
        entry.post()
        self.reversed_discount = discount
        self.settlement_reversal_entry = entry
        super(Bill, self).save(update_fields=["reversed_discount", "settlement_reversal_entry", "updated_at"])

    def _settled_otherwise(self):
        """Paid, discounted, met from a prepayment or deducted as tax: not debited."""
        return (
            self.amount_paid()
            + (self.settlement_discount_amount or Decimal("0")) - self.settlement_discount_withdrawn
            + self.amount_prepaid()
            + self.amount_tds()
        )

    def amount_tds(self):
        """Tax deducted from this bill and not taken back."""
        return sum((row.amount for row in self.tds_deductions.all() if row.reversed_entry_id is None),
                   Decimal("0"))

    def deduct_tds(self, section=None, on_date=None):
        from .tds import deduct

        return deduct(self, section=section, on_date=on_date)

    def settlement_status(self):
        if not self.posted:
            return SettlementStatus.DRAFT
        if self.amount_due() <= 0:
            return SettlementStatus.PAID
        if self.amount_paid() or self.amount_debited() or self.amount_tds():
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
        return amount_overdue(self.installments(), to_date(as_of) or timezone.localdate())

    def is_overdue(self, as_of=None):
        if not self.posted or self.amount_due() <= 0:
            return False
        return self.amount_overdue(as_of) > 0

    def days_overdue(self, as_of=None):
        """Days since the *earliest* installment that is still unpaid."""
        as_of = to_date(as_of) or timezone.localdate()
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
        self._check_kind()
        if self._state.adding and self.vendor_id:
            self.currency = self.currency or self.vendor.default_currency
            self.payment_terms = self.payment_terms or self.vendor.payment_terms
        if self._state.adding and not self.payable_account_id:
            self.payable_account = default_account("payable", "payable_account")
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
        for charge_line, item, _warehouse, base in self.landed_in_base(rate):
            landed[(charge_line.pk, item.pk)] += base

        debits = []
        variance_total = Decimal("0")
        exchange_total = Decimal("0")
        consumed = {}
        for line in lines:
            label = line.description or str(line.item)
            net = line.net_amount()
            account = line.posting_account()
            if line.posted_account_id != account.pk:
                line.posted_account = account
                super(BillLine, line).save(update_fields=["posted_account", "updated_at"])
            if line.clears_grni():
                # Clear the accrual at exactly what the receipts booked —
                # at their agreed price and at THEIR rate, not this
                # bill's. Clearing it at the billed price instead leaves
                # GRNI holding the difference forever, which is how a
                # supposedly self-clearing account silently accumulates a
                # balance nobody can explain; clearing it at the bill's
                # rate did exactly that for every foreign purchase.
                # What the receipts accrued, discount and all: the
                # receipt accrued the net price, so applying this line's
                # discount again would clear less than went in — which is
                # exactly how a discount used to be left in the accrual.
                # A price the vendor bills differently is variance.
                doc, base = line.accrual(consumed, rate)
                if line.accrued_doc is None:
                    line.accrued_doc = doc.quantize(Decimal("0.000001"))
                    line.accrued_base = base.quantize(Decimal("0.000001"))
                    super(BillLine, line).save(update_fields=[
                        "accrued_doc", "accrued_base", "updated_at",
                    ])
                debits.append((account, round_money(base), label, None))
                variance_total += net - round_money(doc)
                # What the accrual is worth at this bill's rate, against
                # what it was booked at: the rate moved between the goods
                # arriving and the bill, and that is an exchange
                # difference, not a price.
                exchange_total += round_money(doc * rate) - round_money(base)
            elif line.is_charge() and line.charge.capitalise_into_inventory:
                shares = {
                    item_pk: amount for (charge_pk, item_pk), amount in landed.items()
                    if charge_pk == line.pk
                }
                if not shares:
                    # Nothing on this bill to absorb it — a freight-only
                    # bill, say. Expense it rather than refuse.
                    debits.append((account, round_money(net * rate), label, line.cost_centre))
                else:
                    for item_pk, base in shares.items():
                        account = inventory_account_for(Item.objects.get(pk=item_pk))
                        debits.append((account, base, f"{label} (landed)", None))
            else:
                debits.append((account, round_money(net * rate), label, line.cost_centre))

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
            debits.append((account, round_money(variance_total * rate), "Price variance", None))

        if exchange_total:
            company = Company.get()
            account = (
                company.fx_loss_account if exchange_total > 0
                else company.fx_gain_account
            )
            if account is None:
                raise ValidationError(
                    f"The rate moved between these goods arriving and this "
                    f"bill, by {exchange_total} in base currency, and the "
                    "company has no exchange "
                    f"{'loss' if exchange_total > 0 else 'gain'} account "
                    "configured to put it in."
                )
            debits.append((account, exchange_total, "Exchange difference", None))

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
                if tax.reverse_charge:
                    # Claimed as credit like any input tax, and owed by the
                    # company rather than the vendor: the two net to
                    # nothing on what the vendor is paid.
                    tax_totals[tax.reverse_charge_account] -= amount
        for account, amount in tax_totals.items():
            debits.append((account, round_money(amount * rate), "Tax", None))

        payable_total = sum(amount for _, amount, _, _ in debits)
        JournalLine.objects.create(
            entry=entry,
            account=self.payable_account,
            party=self.vendor,
            debit=payable_total if reverse else Decimal("0"),
            credit=Decimal("0") if reverse else payable_total,
            description=f"{'Debit note' if reverse else 'Bill'} {self.number}",
        )
        for account, amount, description, centre in debits:
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
                description=description, cost_centre=centre,
            )
        return entry

    @serialised("posted")
    def post(self, memo=None, apply_prepayments=True):
        if self.posted:
            raise ValidationError("This bill is already posted.")
        # What the order has been billed, and paid up front, is decided on
        # below; two bills for one order must not both see the same room.
        lock_rows(self.purchase_order)
        if self.is_prepayment:
            self._check_prepayment_shape()
            self._check_prepayment_room()

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
            # Asked again as the note posts, under the lines' locks: a note drafted before the
            # charge was landed or capitalised was refused nowhere, being saved before either.
            given_back = list(self.lines.filter(debits_line__isnull=False).select_related("debits_line"))
            lock_rows(*(line.debits_line for line in given_back))
            for line in given_back:
                line.refuse_giving_back_what_moved()
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

        self.record_taxes()
        self.journal_entry = entry
        self.posted = True
        self.posted_at = timezone.now()
        self.posted_total = self.total()
        super(Bill, self).save(update_fields=[
            "number", "bill_date", "due_date", "exchange_rate", "journal_entry",
            "posted", "posted_at", "taxes_recorded", "party_gstin", "party_registration",
            "place_of_supply", "posted_total", "updated_at",
        ])

        self._record_landed_cost()
        if self.is_debit_note():
            self._undo_settlements_without_money()

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
        # A debit note takes off the goods what it gives back: skipped for
        # notes, the ledger lost the freight and the shelf kept it.
        sign = Decimal("-1") if self.is_debit_note() else Decimal("1")
        for charge_line, item, warehouse, base in self.landed_in_base(self.exchange_rate):
            StockMovement.objects.create(
                item=item,
                warehouse=warehouse,
                movement_type=MovementType.ADJUSTMENT,
                uom=item.uom,
                quantity=Decimal("0"),
                value_adjustment=sign * base,
                reference=self.number,
                occurred_at=timezone.now(),
                notes=f"Landed cost from {self.number}: {charge_line.label()}",
            )

    def _landed_cost_given_back(self, charges):
        """
        A debit note follows what its original lines did: a charge the
        bill landed on its goods comes back off those goods, in the shares
        it went on, as much of it as the note gives back. Spread afresh
        over the note's own lines, a freight refund with no goods on the
        note fell back to the freight account, which the landing had left
        empty, and the stock kept the freight.
        """
        original = [row for row in self.debits.landed_cost_allocations()] if charges else []
        rows = []
        for line in charges:
            mine = [row for row in original if row[0].pk == line.debits_line_id]
            whole, given = line.debits_line.net_amount() if line.debits_line_id else Decimal("0"), line.net_amount()
            remaining = given
            for index, (_charge, item, warehouse, amount) in enumerate(mine):
                share = remaining if index == len(mine) - 1 else round_money(given * amount / whole)
                remaining -= share
                if share:
                    rows.append((line, item, warehouse, share))
        return rows

    def landed_in_base(self, rate):
        """
        The landed shares at the rate the bill is booked at, each rounded
        once, for the ledger and the shelf alike. The shelf took the bill's
        own figures: a euro bill's landed freight put 88.00 in the
        inventory account and 80.00 on the stock.
        """
        rate = rate or Decimal("1")
        return [(line, item, warehouse, round_money(amount * rate))
                for line, item, warehouse, amount in self.landed_cost_allocations()]

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
        if self.is_debit_note():
            return self._landed_cost_given_back(charges)
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
        if original.is_prepayment:
            # Debited by amount, one line of one at what is left: in full
            # only when nothing had been drawn down.
            return self.total() == original.total()
        original_lines = list(original.lines.all())
        if len(debited) != len(original_lines):
            return False
        return all(debited.get(line.pk) == line.quantity for line in original_lines)

    @serialised("posted")
    def create_debit_note(self, memo="", quantities=None, accruals=None, amount=None):
        """
        Debit this bill. By default the whole thing; pass `quantities` as
        {bill_line: quantity} to give back part of it, which is what a
        partial goods return needs.

        Sales has had partial credit notes since its first pass. The
        purchase side could only ever reverse a bill in full, so a vendor
        who short-shipped one line of ten had to have the entire bill
        cancelled and re-entered.

        A prepayment is debited by `amount`: part of what is left of it,
        or by default all.
        """
        if not self.posted:
            raise ValidationError("Only a posted bill can be corrected with a debit note.")
        if self.debits_id:
            raise ValidationError("Cannot issue a debit note against a debit note.")
        if self.is_prepayment:
            return self._debit_prepayment(memo, quantities, amount)
        if amount is not None:
            raise ValidationError("A bill is debited by the quantities of its lines, not by an amount.")

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
            bill_date=timezone.localdate(),
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
            if accruals and line in accruals:
                # What the returned receipt lines accrued, net of the
                # order's discount as they booked it.
                doc, base = accruals[line]
                note_line.accrued_doc = doc
                note_line.accrued_base = base
                super(BillLine, note_line).save(update_fields=[
                    "accrued_doc", "accrued_base", "updated_at",
                ])
            note_line.taxes.set(line.taxes.all())
        debit_note.post(memo=memo)
        return debit_note

    def debit_old_supply(self, lines, memo="", on_date=None, old_value=None):
        """
        A debit note with GST on a bill the old system booked: the mirror
        of Invoice.credit_old_supply (apps/accounting/old_supply.py).
        """
        from apps.accounting.old_supply import check_room, checked_lines

        if not self.is_opening_balance or self.is_debit_note():
            raise ValidationError(
                f"{self} is not a bill from the old system; debit it the ordinary way.")
        if not self.posted:
            raise ValidationError("Only a posted bill can be corrected with a debit note.")
        checked = checked_lines(lines, "expense_account", "debit")
        with transaction.atomic():
            lock_rows(self)
            earlier = list(self.debit_notes.filter(posted=True, corrects_old_supply=True))
            if old_value is None:
                old_value = next((note.old_bill_value for note in earlier
                                  if note.old_bill_value is not None), None)
            note = Bill.objects.create(
                vendor=self.vendor, bill_date=to_date(on_date) or timezone.localdate(),
                reference=self.reference, currency=self.currency,
                payment_terms=self.payment_terms, payable_account=self.payable_account,
                debits=self, corrects_old_supply=True, old_bill_value=old_value,
            )
            for fields, taxes in checked:
                line = BillLine.objects.create(bill=note, **fields)
                line.taxes.set(taxes)
            check_room(old_value, sum((n.total() for n in earlier), Decimal("0")),
                       note.total(), "bill", "debited")
            note.post(memo=memo or None)
        return note

    def _check_prepayment_shape(self):
        """
        A prepayment is one line of money held on the vendor prepayment
        account, and nothing else. Asked at posting, as sales asks of a
        down payment, for the same reasons.
        """
        lines = list(self.lines.all())
        if len(lines) != 1:
            raise ValidationError(
                f"A prepayment is a single line of money held; this one has {len(lines)}. "
                "Bill anything else on its own bill."
            )
        (line,) = lines
        if line.item_id or line.charge_id or line.order_line_id:
            raise ValidationError("A prepayment line is money held, not goods or a charge.")
        if line.quantity != 1 or line.discount_percent:
            raise ValidationError("A prepayment line is one of its amount, with no discount.")
        if line.taxes.exists():
            raise ValidationError("Tax on a prepayment is not supported; post it without tax.")
        account = Company.get().vendor_prepayment_account
        if account is None or line.expense_account_id != account.pk:
            raise ValidationError(
                "A prepayment is debited to the vendor prepayment account, which is what "
                "the bill draws it down from."
            )

    def _check_prepayment_room(self):
        """Prepayments cannot exceed the order, asked at posting as sales asks of deposits."""
        order = self.purchase_order
        if order is None:
            return
        taken = order.prepayment_total()
        order_total = order.total()
        if taken + self.total() > order_total:
            raise ValidationError(
                f"Prepayments of {taken} are already on {order}; paying {self.total()} more "
                f"would exceed the order total of {order_total}."
            )

    def _debit_prepayment(self, memo, quantities, amount=None):
        """
        Take back what is left of a prepayment, or `amount` of it. The mirror of
        sales' `_credit_deposit`, and for the same reason: part of it may
        already have been drawn down against a bill.
        """
        if quantities is not None:
            raise ValidationError(
                "A prepayment is debited by amount: whatever is left of it once the "
                "bills have drawn it down."
            )
        left = self.prepayment_unapplied()
        if left <= 0:
            drawn = ", ".join(a.bill.number for a in self.applications.all())
            if not drawn:
                raise ValidationError(f"{self.number} has already been debited back in full.")
            raise ValidationError(
                f"{self.number} has already been drawn down in full against {drawn}; "
                "debit those bills instead."
            )
        if amount is not None:
            if amount <= 0 or amount != round_money(amount):
                raise ValidationError("Take back an amount above nothing, to the paisa.")
            if amount > left:
                raise ValidationError(
                    f"Only {left} of {self.number} is left to take back; cannot take back {amount}.")
            left = amount
        line = self.lines.get()  # One, by _check_prepayment_shape().
        debit_note = Bill.objects.create(
            vendor=self.vendor,
            bill_date=timezone.localdate(),
            reference=self.reference,
            currency=self.currency,
            payment_terms=self.payment_terms,
            payable_account=self.payable_account,
            debits=self,
        )
        BillLine.objects.create(
            bill=debit_note,
            debits_line=line,
            description=f"Prepayment {self.number} returned",
            quantity=Decimal("1"),
            unit_price=left,
            expense_account=line.expense_account,
        )
        debit_note.post(memo=memo)
        return debit_note


# Everything a bill's own figures read — total, tax, paid, debited,
# prepaid, voided payments, due dates — so a report walking a year of
# bills asks once per kind, not once per bill. The payment run took 68
# seconds and 44,872 queries without it.
BILL_FIGURES = (
    "lines__taxes",
    "lines__recorded_taxes__tax",
    "payment_allocations__payment__journal_entry__reversed_by",
    "debit_notes__lines__taxes",
    "debit_notes__lines__recorded_taxes__tax",
    "prepayment_applications",
    "tds_deductions",
    "payment_terms__lines",
)


class BillLine(PostedLineMixin, TaxedLineMixin, AuditModel):
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
    cost_centre = models.ForeignKey(
        "accounting.CostCentre", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="The centre this expense is for; carried onto the ledger line when the bill posts.",
    )
    posted_account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        editable=False,
        help_text="Where this line actually landed when the bill posted, frozen so a "
                  "debit note gives it back to the same place.",
    )
    accrued_doc = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, editable=False,
        help_text="What the receipts this line clears accrued, in the bill's "
                  "currency, frozen when it posted.",
    )
    accrued_base = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, editable=False,
        help_text="The same in base currency, at the receipts' own rates — "
                  "exactly what this line took out of goods received not "
                  "invoiced. A debit note puts back the same, and the gap "
                  "between this and the bill's own rate is an exchange "
                  "difference.",
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
        """What is left of the charge to land on other goods: see amount_in_its_account()."""
        return self.amount_in_its_account()

    def landed_by_its_bill(self):
        """
        Whether posting its bill landed this charge on the goods the bill
        brought in. Asked of what the posting recorded: once a bill has
        posted, which of its lines cleared receipts is read from their
        posted_account, so the bill spreads its charges now as it did then.
        """
        if not (self.bill.posted and not self.bill.is_debit_note() and self.is_charge()
                and self.charge.capitalise_into_inventory):
            return False
        return any(charge_line.pk == self.pk for charge_line, *_ in self.bill.landed_cost_allocations())

    def booked_amount(self):
        """What posting this line put in its account, in base: at the rate its bill was booked at."""
        return round_money(self.net_amount() * (self.bill.exchange_rate or Decimal("1")))

    def amount_given_back(self):
        """What posted debit notes have given back of this line, in base, each at its own booked rate."""
        return sum((line.booked_amount() for line in self.debit_lines.filter(bill__posted=True).select_related("bill")),
                   Decimal("0"))

    def amount_in_its_account(self):
        """
        What of this line's cost still sits in the account it posted to, in
        base currency: all that landing it on goods, or capitalising it, may
        take out. Nothing if its own bill landed it on the goods; otherwise
        what the bill put there, less what debit notes have given back, what
        stands landed on other goods, and what stands capitalised.

        Each of those used to measure the line for itself: a refunded charge
        still landed on stock, a charge billed with its goods landed twice,
        and a euro charge landed its euro figure as if it were base.
        """
        from apps.assets.models import AssetStatus

        if self.landed_by_its_bill():
            return Decimal("0")
        capitalised = sum((asset.cost for asset in self.assets.exclude(status=AssetStatus.CANCELLED)), Decimal("0"))
        return self.booked_amount() - self.amount_given_back() - self.landed_cost_allocated() - capitalised

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
        # One allocation at a time per charge, each reading what the last left: two at once both
        # read the whole charge as unallocated and put it into stock twice.
        lock_rows(self)
        on_date = to_date(on_date) or timezone.localdate()
        if not self.bill.posted:
            raise ValidationError("Only a posted bill can be allocated.")
        if not (self.is_charge() and self.charge.capitalise_into_inventory):
            raise ValidationError(
                f"'{self.label()}' is not a charge that capitalises into stock."
            )
        from apps.assets.models import AssetStatus

        # The same cost cannot sit on a machine and on the goods: both credit the expense the
        # bill put it in, and an 80.00 freight bill landed 80.00 on stock and 80.00 on an asset.
        if self.assets.exclude(status=AssetStatus.CANCELLED).exists():
            raise ValidationError(
                f"'{self.label()}' is capitalised as a fixed asset; un-capitalise it before "
                "landing its cost on stock."
            )
        if self.landed_by_its_bill():
            raise ValidationError(
                f"'{self.label()}' was landed on the goods on {self.bill.number} when it posted; "
                "it is in their cost already."
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

        # In base, and net of what debit notes gave back: what the freight account still holds.
        total = self.landed_cost_unallocated()
        if total <= 0:
            raise ValidationError("This charge has already been allocated in full, or given back.")

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

        # One at a time per line, reading what the last did: two at once both found no asset.
        lock_rows(self)
        if not self.bill.posted:
            raise ValidationError("Only a posted bill can be capitalised.")
        if self.assets.exists():
            raise ValidationError("This line has already been capitalised.")
        if any(not application.is_released() for application in self.landed_cost_applications.all()):
            raise ValidationError(
                f"'{self.label()}' has been landed on stock; release that before capitalising "
                "it, or its cost is counted twice."
            )
        if self.landed_by_its_bill():
            raise ValidationError(
                f"'{self.label()}' was landed on the goods on {self.bill.number} when it posted; "
                "its cost is in their stock value, not on a machine."
            )
        # What debit notes gave back is not capitalised: a refunded line made an asset of nothing.
        units = self.quantity_debitable()
        if units <= 0:
            raise ValidationError("This line has been given back in full; there is nothing to capitalise.")
        if units != units.to_integral_value():
            raise ValidationError(
                "Capitalise whole units; a fraction of an asset cannot be disposed of."
            )

        # In base currency, at the rate the bill posted at — which is
        # what the bill put in the account this takes it out of. At the
        # bill's own figures a foreign machine went onto the asset
        # account at its euro price and left the difference behind.
        total = self.booked_amount() - self.amount_given_back()
        unit_cost = round_money(total / units)
        memo = f"Capitalised from {self.bill.number}"
        source = self.posted_account or self.expense_account

        created = []
        remaining = total
        count = int(units)
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
            asset.capitalisation_entry = entry
            asset.save(update_fields=["capitalisation_entry", "updated_at"])
            created.append(asset)
        return created

    def quantity_debited(self):
        """How much of this line posted debit notes have already given back."""
        return self.debit_lines.filter(bill__posted=True).aggregate(
            total=models.Sum("quantity")
        )["total"] or Decimal("0")

    def quantity_debitable(self):
        return self.quantity - self.quantity_debited()

    def document(self):
        return self.bill

    def corrected_line(self):
        return self.debits_line

    def corrections(self):
        return self.debit_lines.filter(bill__posted=True)

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
        # A line paying for a run's outside step accrued when the work
        # came back, though its item is a service — that is the whole
        # difference between it and an ordinary service line, which
        # accrues nothing and expenses on the bill.
        outside = bool(
            self.order_line_id and self.order_line.work_order_operation_id
        )
        if not outside and not (self.item_id and self.item.track_inventory):
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

    def accrual(self, consumed, rate):
        """
        (doc, base): what the receipts this line clears accrued, for its
        quantity.

        Oldest receipt first, after whatever earlier bills and earlier
        lines of this bill have already cleared — `consumed` carries the
        latter, keyed by order line, because this bill is not yet posted
        and so does not count itself as billed. Taken in that order,
        every receipt is cleared exactly once over the life of the line,
        so the accrual ends at nothing whatever the rates did.

        A debit note does not walk the receipts: it puts back what the
        line it debits took, or — raised by a return — what the returned
        receipt line booked, which the return has already frozen onto it.

        A bill line that names no order line is cleared at its own price
        and at this bill's rate, as it always was. Without the link there
        is no agreed price and no particular receipt to clear, and an
        earlier version that walked every order for the item cleared a
        receipt an earlier unlinked bill had already paid for — nothing
        counts an unlinked bill against any order line. Exact clearing in
        a foreign currency needs the link.
        """
        if self.accrued_base is not None and self.accrued_doc is not None:
            return self.accrued_doc, self.accrued_base
        # What this line's own discount leaves, for the two cases below
        # that clear at the line's own figures rather than a receipt's.
        keep = Decimal("1") - self.discount_percent / Decimal("100")
        if self.debits_line_id:
            original = self.debits_line
            if original.accrued_base is not None and original.quantity:
                share = self.quantity / original.quantity
                return original.accrued_doc * share, original.accrued_base * share
            doc = self.quantity * self.accrued_unit_cost() * keep
            return doc, doc * rate
        if not self.order_line_id:
            doc = self.quantity * self.unit_price * keep
            return doc, doc * rate
        candidates = [self.order_line]
        remaining = self.quantity
        doc = base = Decimal("0")
        for order_line in candidates:
            skip = order_line.quantity_billed() + consumed.get(
                order_line.pk, Decimal("0")
            )
            for quantity, price, cost in order_line.accrual_layers():
                if remaining <= 0:
                    break
                if skip >= quantity:
                    skip -= quantity
                    continue
                available = quantity - skip
                skip = Decimal("0")
                taken = min(available, remaining)
                doc += taken * price
                base += taken * cost
                remaining -= taken
                consumed[order_line.pk] = (
                    consumed.get(order_line.pk, Decimal("0")) + taken
                )
        if remaining > 0:
            # More billed than there is accrual left to clear — the rest
            # was received before anything was frozen, or billed ahead
            # of its receipt. At the order's price and the bill's own
            # figures, which is what this line did before.
            order_line = self.order_line
            unit = order_line.unit_price * (
                Decimal("1") - order_line.discount_percent / Decimal("100")
            )
            doc += remaining * unit
            base += remaining * unit * rate
        return doc, base

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
        if self.debits_line_id:
            self.refuse_giving_back_what_moved()
        super().save(*args, **kwargs)

    def refuse_giving_back_what_moved(self):
        """
        A debit note line takes its cost back out of the account its
        original put it in, so that cost must still be there.
        """
        original = self.debits_line
        if original.assets.exclude(status="cancelled").exists():
            # The line's cost has moved onto the asset account, so a
            # debit note crediting the line's own account would take it
            # out of an account that no longer holds it — below nothing —
            # while the asset stayed on the books at full cost.
            raise ValidationError(
                f"{original.label()} was capitalised as fixed assets. "
                "Un-capitalise them first, or, once in service, dispose of "
                "them — then the bill can be debited."
            )
        if any(not application.is_released() for application in original.landed_cost_applications.all()):
            # The same, landed on other goods: the freight account it would credit is empty.
            raise ValidationError(
                f"{original.label()} has been landed on stock. Release that first, then the bill "
                "can be debited."
            )

    def delete(self, *args, **kwargs):
        if self.bill.posted:
            raise ValidationError(
                "Cannot delete a line on a posted bill. Issue a debit note instead."
            )
        super().delete(*args, **kwargs)


class BillLineTax(RecordedLineTax):
    line = models.ForeignKey(BillLine, on_delete=models.CASCADE, related_name="recorded_taxes")


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
    fx_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
        help_text="Realised exchange difference posted when the prepayment, made at one "
                  "rate, was drawn down against a bill at another.",
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
    fx_released_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False,
        help_text="Its payment returned: the exchange difference it realised, reversed on that day.",
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
        return payment.amount - allocated_on(payment)

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

        refuse_other_control_account(self.payment, self.bill.payable_account, self.bill)
        # Applied anywhere, not only to bills: see allocated_on().
        available = self.payment.amount - allocated_on(self.payment, excluding=self)
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
        # What is left on the payment and due on the bill are read in
        # clean(); two allocations at once must not both spend them.
        lock_rows(self.payment if self.payment_id else None,
                  self.bill if self.bill_id else None)
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
        if self.fx_entry_id and not self.fx_released_entry_id:
            self.fx_entry.create_reversal(
                memo=f"Releasing exchange difference on {self}"
            )
        super().delete(*args, **kwargs)

    def release_exchange_difference(self, on_date):
        """
        Its payment returned, the difference it realised never was: reversed on that day, once.
        Left standing, the payable kept it after the void, at a
        rate the money never came at, and the gain or loss stayed in the profit and loss.
        """
        if self.fx_entry_id and not self.fx_released_entry_id:
            self.fx_released_entry = self.fx_entry.create_reversal(
                entry_date=on_date,
                memo=f"Exchange difference on {self.bill.number} released: {self.payment.number} returned")
            super(BillPayment, self).save(update_fields=["fx_released_entry", "updated_at"])


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
    on_date = to_date(on_date) or timezone.localdate()
    vendor = from_warehouse.consignment_vendor
    if vendor is None:
        raise ValidationError(f"{from_warehouse} does not hold consignment stock.")
    if to_warehouse.holds_others_goods():
        raise ValidationError(
            f"{to_warehouse} holds {to_warehouse.owner()}'s stock; drawing into it owns nothing."
        )

    quantity = Decimal(quantity)
    lock_positions(((item, from_warehouse), (item, to_warehouse)))
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
    on_date = to_date(on_date) or timezone.localdate()
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
    on_date = to_date(on_date) or timezone.localdate()
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
            suggested_vendor=row["vendor"], warehouse=row["rule"].warehouse,
            notes=f"Projected {row['projected']} against a minimum of {row['rule'].minimum}",
        )
    return requisition


def not_paid_in_full(bills):
    """
    `bills` less those payments that still stand have already covered.

    Asked in the database so that a report over a year passes over the
    settled majority without building them: what is due is the total less
    payments and every other reduction, all of them non-negative, so a
    bill whose standing payments reach its posted total cannot be due.
    Bills posted before totals were recorded are kept and worked out.
    """
    from django.db.models import DecimalField, Exists, OuterRef, Subquery, Value
    from django.db.models.functions import Coalesce

    standing = BillPayment.objects.filter(
        bill=OuterRef("pk"), payment__voided_entry__isnull=True
    ).exclude(Exists(JournalEntry.objects.filter(reverses=OuterRef("payment__journal_entry"))))
    paid = standing.values("bill").annotate(total=models.Sum("amount")).values("total")
    return bills.annotate(
        standing_paid=Coalesce(Subquery(paid), Value(Decimal("0")),
                               output_field=DecimalField(max_digits=18, decimal_places=2))
    ).exclude(posted_total__isnull=False, standing_paid__gte=models.F("posted_total"))


def bills_still_owed(bills):
    """
    The `bills` that still owe the vendor money: posted bills, not
    debit notes, whose amount_due() is above nothing. The mirror of sales'
    still_owed(), decided by the same owed_beyond(); tests_screens_api
    holds it to amount_due().
    """
    owed = owed_beyond(
        not_paid_in_full(bills.filter(posted=True, debits__isnull=True)),
        notes=(Bill.objects.filter(posted=True), "debits"),
        drawdowns=[(PrepaymentApplication.objects.all(), "bill"),
                   (TdsDeduction.objects.filter(reversed_entry__isnull=True), "bill")],
        reductions=("settlement_discount_amount", "-settlement_discount_withdrawn"),
    )
    unrecorded = bills.filter(posted=True, debits__isnull=True, posted_total__isnull=True)
    asked = [bill.pk for bill in unrecorded.prefetch_related(*BILL_FIGURES) if bill.amount_due() > 0]
    # A subquery, not a list of pks, as still_owed() says.
    return bills.filter(models.Q(pk__in=owed.values("pk")) | models.Q(pk__in=asked))


def not_received_in_full(lines):
    """
    Order lines that may still owe goods: not charges, and less received
    (posted receipts, less posted returns) than ordered. Narrows what the
    database hands back; quantity_open() decides each.
    """
    from django.db.models import DecimalField, OuterRef, Subquery, Value
    from django.db.models.functions import Coalesce

    def received(returns):
        moved = GoodsReceiptLine.objects.filter(
            order_line=OuterRef("pk"), receipt__posted=True,
            receipt__reverses__isnull=not returns,
        ).values("order_line").annotate(total=models.Sum("quantity_received")).values("total")
        return Coalesce(Subquery(moved), Value(Decimal("0")),
                        output_field=DecimalField(max_digits=18, decimal_places=4))

    return lines.filter(charge__isnull=True, closed_short_at__isnull=True).annotate(
        net_received=received(False) - received(True)
    ).exclude(net_received__gte=models.F("quantity"))


def orders_to_receive(orders):
    """The pks of confirmed `orders` with goods still to come (quantity_open)."""
    lines = not_received_in_full(PurchaseOrderLine.objects.filter(
        order__in=orders.filter(status=OrderStatus.CONFIRMED)))
    return sorted({line.order_id for line in lines.prefetch_related(
        models.Prefetch("receipt_lines", queryset=GoodsReceiptLine.objects.select_related("receipt")))
        if line.quantity_open() > 0})


def drop_ship_awaited(sales_line_ids, excluding=None):
    """
    {sales order line pk: what open drop-ship lines are still to deliver
    for it}, in the sales line's unit, which a drop-ship line is raised
    in. Registered with sales (apps.py) as what its shelves need not send.
    Cancelled orders bring nothing; drafts are counted, as a second
    drop-ship raised beside a draft is the same goods ordered twice.
    """
    lines = not_received_in_full(PurchaseOrderLine.objects.filter(
        sales_order_line_id__in=sales_line_ids, order__drop_ship_for__isnull=False,
    ).exclude(order__status=OrderStatus.CANCELLED))
    if excluding is not None:
        lines = lines.exclude(pk=excluding)
    awaited = defaultdict(Decimal)
    for sales_line_id, quantity, received in lines.values_list(
            "sales_order_line_id", "quantity", "net_received"):
        awaited[sales_line_id] += max(quantity - received, Decimal("0"))
    return awaited


def _billed_beyond_received(lines, vendor=None):
    """
    Only the lines billed for more than they now hold.

    Net billed and net received are each summed in one grouped pass, the
    way quantity_billed() and quantity_received() sum them, and compared
    here. A correlated sum per line took 1.2 seconds over a year's lines
    with every index in use: the cost was asking 6,000 times, not how.
    With a `vendor`, only that vendor's lines are summed.
    """
    def net(model, document, quantity, reversal):
        signed = models.Case(
            models.When(**{f"{document}__{reversal}__isnull": True}, then=models.F(quantity)),
            default=-models.F(quantity),
        )
        rows = model.objects.filter(**{f"{document}__posted": True})
        if vendor is not None:
            rows = rows.filter(order_line__order__vendor=vendor)
        rows = rows.values(
            "order_line").annotate(total=models.Sum(signed))
        return {row["order_line"]: row["total"] for row in rows}

    billed = net(BillLine, "bill", "quantity", "debits")
    received = net(GoodsReceiptLine, "receipt", "quantity_received", "reverses")
    over = [line for line, quantity in billed.items()
            if line is not None and quantity > received.get(line, Decimal("0"))]
    return lines.filter(pk__in=over)


def with_line_figures(lines):
    """
    Order lines with the receipts and bills their figures read, so a
    report over a year of lines asks once rather than four times a line:
    billed-not-held took 44 seconds and 24,001 queries without it.
    """
    from django.db.models import Prefetch

    return lines.prefetch_related(
        Prefetch("receipt_lines", queryset=GoodsReceiptLine.objects.select_related("receipt")),
        Prefetch("bill_lines", queryset=BillLine.objects.select_related("bill")),
    )


def _receipt_dates(order_line):
    """[(receipt_date, quantity)] for real receipts, returns excluded."""
    if prefetched(order_line, "receipt_lines"):
        return [(to_date(line.receipt.receipt_date), line.quantity_received)
                for line in order_line.receipt_lines.all()
                if line.receipt.posted and line.receipt.reverses_id is None]
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
    lines = PurchaseOrderLine.objects.exclude(order__status=OrderStatus.CANCELLED).filter(
        charge__isnull=True)
    if vendor is not None:
        lines = lines.filter(order__vendor=vendor)
    if start:
        lines = lines.filter(order__order_date__gte=start)
    if end:
        lines = lines.filter(order__order_date__lte=end)

    # Plain rows, not model instances: over a year of lines, building the
    # objects was 3.3 of the report's 3.4 seconds. Each sum below is the
    # one quantity_received() and _receipt_dates() take.
    receipts = defaultdict(list)
    for row in GoodsReceiptLine.objects.filter(
            order_line__in=lines, receipt__posted=True).values_list(
            "order_line", "receipt__receipt_date", "receipt__reverses", "quantity_received"):
        receipts[row[0]].append(row[1:])
    bills = defaultdict(list)
    for row in BillLine.objects.filter(
            order_line__in=lines, bill__posted=True, bill__debits__isnull=True).values_list(
            "order_line", "unit_price", "quantity"):
        bills[row[0]].append(row[1:])
    vendors = Party.objects.in_bulk(set(lines.values_list("order__vendor", flat=True)))

    rows = {}
    for pk, vendor_id, quantity, unit_price, expected_date, order_date in lines.order_by("pk").values_list(
            "pk", "order__vendor", "quantity", "unit_price", "expected_date", "order__order_date"):
        row = rows.setdefault(vendor_id, {
            "vendor": vendors[vendor_id],
            "order_lines": 0,
            "quantity_ordered": Decimal("0"),
            "quantity_received": Decimal("0"),
            "quantity_returned": Decimal("0"),
            "dated_quantity": Decimal("0"),
            "on_time_quantity": Decimal("0"),
            "late_days_weighted": Decimal("0"),
            "lead_quantity": Decimal("0"),
            "lead_days_weighted": Decimal("0"),
            "price_variance": Decimal("0"),
            "open_lines": 0,
        })
        row["order_lines"] += 1
        row["quantity_ordered"] += quantity
        received = sum((-moved if returned else moved
                        for _, returned, moved in receipts[pk]), Decimal("0"))
        row["quantity_received"] += received
        if received < quantity:
            row["open_lines"] += 1

        # What came back counts against the vendor whatever the reason;
        # days to deliver run from the order to each delivery, weighted.
        for receipt_date, returned, moved in receipts[pk]:
            if returned:
                row["quantity_returned"] += moved
            else:
                row["lead_quantity"] += moved
                row["lead_days_weighted"] += (
                    Decimal((to_date(receipt_date) - to_date(order_date)).days) * moved)

        if expected_date:
            for receipt_date, returned, moved in receipts[pk]:
                if returned:
                    continue
                row["dated_quantity"] += moved
                late = (to_date(receipt_date) - to_date(expected_date)).days
                if late <= 0:
                    row["on_time_quantity"] += moved
                else:
                    row["late_days_weighted"] += Decimal(late) * moved

        for billed_price, billed_quantity in bills[pk]:
            row["price_variance"] += round_money(
                (billed_price - (unit_price or Decimal("0"))) * billed_quantity)

    results = []
    for row in rows.values():
        dated = row.pop("dated_quantity")
        on_time = row.pop("on_time_quantity")
        late_weighted = row.pop("late_days_weighted")
        lead_quantity = row.pop("lead_quantity")
        lead_weighted = row.pop("lead_days_weighted")
        ordered = row["quantity_ordered"]
        delivered = row["quantity_received"] + row["quantity_returned"]
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
            "return_rate": (
                round_money(row["quantity_returned"] / delivered * Decimal("100"))
                if delivered else None
            ),
            "average_lead_days": (
                round_money(lead_weighted / lead_quantity) if lead_quantity else None
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
    lines = with_line_figures(_billed_beyond_received(lines, vendor))

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
    bills = not_paid_in_full(Bill.objects.filter(
        vendor=vendor, posted=True, debits__isnull=True
    )).prefetch_related(*BILL_FIGURES)
    owed = sum((bill.amount_due() for bill in bills), Decimal("0"))

    notes = Bill.objects.filter(
        vendor=vendor, posted=True, debits__isnull=False
    ).prefetch_related(*BILL_FIGURES, *(f"debits__{f}" for f in BILL_FIGURES))
    refundable = sum((note.refund_due() for note in notes), Decimal("0"))
    # Paid ahead of the bill, or refunded to us before a debit note took it: in payables all the
    # same, and the mirror of what a customer's balance counts.
    received, paid_out = standing_on_account(
        Payment.objects.filter(party=vendor, counterpart_account__in=payable_accounts()))
    return owed - refundable - paid_out + received


def undo_what_it_settled(payment, on_date):
    """
    Our payment returned (Payment.void asks), on the void's day: the exchange difference each
    allocation realised is released, and a bill it settled with a discount for paying early, owing
    again, loses the discount. The mirror of sales'.
    """
    for allocation in payment.bill_allocations.select_related("bill", "fx_entry"):
        allocation.release_exchange_difference(on_date)
        if allocation.bill.settlement_discount_entry_id:
            allocation.bill.withdraw_unearned_discount(on_date=on_date)


def payable_accounts():
    """Where what is owed to vendors is: every account a posted bill is payable on, and the company's default."""
    accounts = Account.objects.filter(models.Exists(
        Bill.objects.filter(posted=True, payable_account=models.OuterRef("pk"))))
    default = Company.get().default_payable_account_id
    return accounts | Account.objects.filter(pk=default) if default else accounts


AGING_BUCKETS = ((1, 30), (31, 60), (61, 90))


def ap_aging(as_of=None):
    """
    Outstanding vendor bills bucketed by how overdue they are — the mirror
    of ar_aging(), and the thing a company looks at before deciding what
    it can afford to pay this week.
    """
    as_of = to_date(as_of) or timezone.localdate()
    buckets = {"current": [], "1-30": [], "31-60": [], "61-90": [], "90+": []}

    bills = not_paid_in_full(Bill.objects.filter(posted=True, debits__isnull=True)).select_related(
        "vendor", "currency").prefetch_related(*BILL_FIGURES)
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
    due_by = to_date(due_by) or timezone.localdate()
    bills = not_paid_in_full(Bill.objects.filter(posted=True, debits__isnull=True)).select_related(
        "vendor", "currency"
    ).prefetch_related(*BILL_FIGURES)
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
    with create_return(), which sends back all of it or part (same lines,
    opposite stock effect), never by editing a posted receipt.
    """

    number = models.CharField(max_length=32, blank=True, editable=False)
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="goods_receipts")
    receipt_date = models.DateField()
    exchange_rate = models.DecimalField(
        max_digits=18, decimal_places=8, null=True, blank=True, editable=False,
        help_text="What one unit of the order's currency was worth in base "
                  "currency when this receipt posted, frozen. The stock and "
                  "the accrual are booked at it, and the bill clears the "
                  "accrual at exactly what this booked — any movement in the "
                  "rate before the bill is an exchange difference, not a "
                  "change in what the goods cost. Blank on receipts posted "
                  "before this was recorded, which booked the order's figures "
                  "as base currency: read as one, because that is what they "
                  "did.",
    )
    reference = models.CharField(max_length=64, blank=True)
    reverses = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by"
    )
    posted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True)
    # Kept, as every document keeps what it posted: corrected by a return, never by reversing its
    # entries from the journal (JournalEntry.recorded_by).
    journal_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False,
        help_text="What receiving it posted: stock in, or out on a return; cost of sales on a drop-ship.",
    )
    price_difference_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False,
        help_text="On a return at a price other than the shelf's: the difference, to price variance.",
    )

    class Meta:
        ordering = ["-receipt_date", "-id"]
        indexes = [models.Index(fields=["receipt_date", "id"], name="receipt_by_date")]
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

    @serialised("posted")
    def post(self):
        if self.posted:
            raise ValidationError("This goods receipt is already posted.")
        # What each line has received is read and decided on; two receipts
        # against one order must not both see the same room.
        lock_rows(self.purchase_order)
        lines = list(self.lines.all())
        if not lines:
            raise ValidationError("Cannot post a goods receipt with no lines.")

        is_return = bool(self.reverses_id)
        if is_return and not self.reverses.posted:
            raise ValidationError("Cannot return an unposted goods receipt.")

        self.receipt_date = to_date(self.receipt_date)
        # Frozen before anything is valued. A return goes back at the
        # rate its receipt came in at: it undoes what that receipt
        # booked, and at today's rate it would leave the difference
        # sitting in the accrual.
        if is_return:
            self.exchange_rate = self.reverses.exchange_rate or Decimal("1")
        else:
            currency = self.purchase_order.currency
            self.exchange_rate = (
                Decimal("1") if currency is None or currency.is_base
                else currency.rate_on(self.receipt_date)
            )
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
                if line.order_line.is_closed_short():
                    raise ValidationError(
                        f"{line.order_line.label()} was closed short: "
                        f"{line.order_line.closed_short_reason}. Reopen it to receive more.")
                if already_received + line.quantity_received > line.order_line.quantity:
                    raise ValidationError(
                        f"Receiving {line.quantity_received} of {line.order_line.item} would "
                        f"exceed the ordered quantity ({line.order_line.quantity}; "
                        f"{already_received} already received)."
                    )

        if self.purchase_order.is_drop_ship():
            return self._post_drop_ship(lines, is_return=is_return)

        # Every shelf this receipt touches. Subcontract lines read the
        # components before consuming them, and a receipt into a routed
        # warehouse lands somewhere other than the line names.
        lock_positions(
            pair
            for line in lines
            for pair in (
                (line.order_line.item, line.warehouse),
                (line.order_line.item, line.warehouse.first_receipt_step()),
                *(
                    (component.item, self.purchase_order.subcontract_warehouse)
                    for component in line.order_line.components.all()
                ),
            )
        )

        valued = []
        received = {}
        price_differences = {}
        for line in lines:
            if line.order_line.work_order_operation_id:
                self._post_outside_step(line, is_return)
                continue
            # Services and non-stocked items must never touch stock levels.
            if not line.order_line.item.track_inventory:
                continue
            # Consignment stock is on the premises and not on the books.
            # It moves, so the quantity is recorded; it is not owned, so
            # no value and no liability are. Booking it would put the
            # vendor's inventory on the company's balance sheet and accrue
            # a bill nobody owes yet.
            if line.warehouse.held_for_id:
                raise ValidationError(
                    f"{line.warehouse} holds {line.warehouse.held_for}'s material. What "
                    "the company buys goes into its own stock."
                )
            if line.warehouse.consignment_vendor_id:
                self._move_consignment(line, is_return)
                continue
            movement_type = MovementType.ISSUE if is_return else MovementType.RECEIPT
            quantity = -line.quantity_received if is_return else line.quantity_received
            self._freeze_accrual(line)
            # In base currency, at the receipt's rate. Booked at the
            # order's figure as though it were base currency, a euro
            # purchase went on the shelf at the euro number, and the bill
            # — which does convert — cleared a different amount from the
            # accrual than the receipt had put in it, for ever.
            unit_cost = line.accrued_unit_cost
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
            item = line.order_line.item
            if is_return and item.track_inventory and item.costing_method != "standard":
                # What leaving takes off the shelf, asked before the
                # movement is written, as every outbound path asks. The
                # ledger is credited at what the goods cost; when the shelf
                # averages something else the difference is a gain or a
                # loss on the price, and booking it keeps the two equal.
                removing = item.cost_of_removing(
                    landed, item.to_stock_quantity(line.quantity_received, line.order_line.uom),
                    lot=line.lot)
                price_differences[item] = (price_differences.get(item, Decimal("0"))
                                           + line.quantity_received * unit_cost - removing)
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
                    "stock_movement", "bin", "landed_warehouse",
                    "accrued_unit_price", "accrued_unit_cost", "updated_at",
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

        self.journal_entry = post_inventory_entry(
            valued,
            date=self.receipt_date,
            reference=self.reference or self.number,
            memo=f"{'Return to vendor for' if is_return else 'Goods received for'} {self.purchase_order}",
            direction="in",
            reverse=is_return,
            quantities=received,
        )
        if price_differences:
            self.price_difference_entry = self._post_return_price_difference(price_differences)

        self.posted = True
        self.posted_at = timezone.now()
        super(GoodsReceipt, self).save(
            update_fields=[
                "number", "receipt_date", "exchange_rate", "posted",
                "posted_at", "journal_entry", "price_difference_entry", "updated_at",
            ]
        )

    def _freeze_accrual(self, line):
        """
        What this line puts into the accrual per unit, in the order's
        currency and in base, written onto the line.

        A return takes back what its receipt line booked, not today's
        price at today's rate: the order may have been re-priced since,
        and either difference would be left in the accrual.
        """
        original = line.reverses_line
        if original is not None and original.accrued_unit_cost is not None:
            line.accrued_unit_price = original.accrued_unit_price
            line.accrued_unit_cost = original.accrued_unit_cost
            return
        rate = self.exchange_rate or Decimal("1")
        # Net of the order line's discount, which is what the goods
        # cost. Accrued at the gross price, a ten per cent discount left
        # its ten per cent in the accrual when the bill cleared the net
        # figure, and the stock stayed overstated by it.
        order_line = line.order_line
        price = order_line.unit_price * (
            Decimal("1") - order_line.discount_percent / Decimal("100")
        )
        line.accrued_unit_price = price.quantize(Decimal("0.000001"))
        line.accrued_unit_cost = (price * rate).quantize(Decimal("0.000001"))

    def _post_outside_step(self, line, is_return):
        """
        A vendor's work on a run, coming back or going back.

        Through the run rather than onto a shelf: there is no item to
        receive, only work done on one that never left work in
        progress. Valued at the agreed price exactly as a stocked
        receipt is, so the bill clears the accrual the same way and any
        difference lands in purchase price variance, not on the run.
        The run's own module does the posting — it is the one place that
        writes work in progress — and this passes it the accrual to
        credit.
        """
        from apps.manufacturing.outside import OutsideMovement

        self._freeze_accrual(line)
        movement = OutsideMovement.objects.create(
            operation=line.order_line.work_order_operation,
            movement_date=self.receipt_date,
            is_return=is_return,
            quantity=line.quantity_received,
            value=round_money(line.quantity_received * line.accrued_unit_cost),
            credit_account=grni_account(),
            reference=self.reference or self.number,
        )
        movement.post()
        line.outside_movement = movement
        super(GoodsReceiptLine, line).save(update_fields=[
            "outside_movement", "accrued_unit_price", "accrued_unit_cost",
            "updated_at",
        ])

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
            self._freeze_accrual(line)
            super(GoodsReceiptLine, line).save(update_fields=[
                "accrued_unit_price", "accrued_unit_cost", "updated_at",
            ])
            value = round_money(line.quantity_received * line.accrued_unit_cost)
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
            self.journal_entry = entry

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
            update_fields=[
                "number", "receipt_date", "exchange_rate", "posted",
                "posted_at", "journal_entry", "updated_at",
            ]
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

    @serialised("posted")
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
        lock_positions(
            pair
            for line, _quantity in selected
            for pair in (
                (line.order_line.item, line.warehouse),
                (line.order_line.item, warehouse),
            )
        )
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

    @serialised("posted")
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
                inspected_on=timezone.localdate(), note=note,
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
        """
        Put the components back where they were when a return reverses
        a subcontract receipt, and return their cost per finished unit.

        At what they were consumed at, which the receipt recorded: putting
        them back at whatever they average today restored a different
        value from the one that went into the assembly (CLAUDE.md,
        mistake 4).
        """
        order = self.purchase_order
        warehouse = order.subcontract_warehouse
        original = line.reverses_line
        share = (line.quantity_received / original.quantity_received
                 if original is not None and original.quantity_received else Decimal("1"))
        restored = Decimal("0")
        for component in line.order_line.components.select_related("item"):
            if not component.item.track_inventory:
                continue
            consumed = StockMovement.objects.filter(
                item=component.item, warehouse=warehouse,
                movement_type=MovementType.ISSUE,
                reference=self.reverses.number,
                notes__startswith="Consumed by subcontractor",
            ).first()
            if consumed is not None:
                quantity = round_money(-consumed.quantity * share)
                cost = consumed.unit_cost or Decimal("0")
            else:
                # A receipt from before consumption was recorded this way.
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
            restored += quantity * cost
        return restored / line.quantity_received

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
        consumed = Decimal("0")
        for component in line.order_line.components.select_related("item"):
            if not component.item.track_inventory:
                continue
            used = round_money(component.quantity_per * line.quantity_received)
            # What the replay will take off, exactly: the rule every
            # outbound path follows (inventory/costing.py).
            taking = component.item.cost_of_removing(warehouse, used) if used else Decimal("0")
            cost = (taking / used).quantize(Decimal("0.0001")) if used else Decimal("0")
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
            consumed += taking
        # Per finished unit at full precision. Each component's share was
        # rounded to the paisa and multiplied back up: 1,000 sacks came in
        # 2.90 short of what went into them, and the shelf never agreed
        # with the ledger again.
        return consumed / line.quantity_received

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
        # What each return line took out of the accrual, per bill line
        # it lands on, so the debit note puts back exactly that — at the
        # returned receipt's price and rate, not at the average the bill
        # line cleared across several receipts.
        returned = defaultdict(dict)
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
                if line.accrued_unit_cost is not None:
                    doc, base = returned[bill_line.bill].get(
                        bill_line, (Decimal("0"), Decimal("0"))
                    )
                    returned[bill_line.bill][bill_line] = (
                        doc + taken * line.accrued_unit_price,
                        base + taken * line.accrued_unit_cost,
                    )
                remaining -= taken

        return [
            bill.create_debit_note(
                memo=f"Goods returned on {self.number}", quantities=quantities,
                accruals=returned.get(bill, {}),
            )
            for bill, quantities in allocations.items()
        ]

    @transaction.atomic
    def _post_return_price_difference(self, differences):
        """
        Inventory and the price variance account, for what the vendor is
        owed back less what the shelf gave up. Written here rather than
        through post_inventory_entry, which knows a receipt's GRNI and
        inventory and has no argument meaning "the shelf and the price
        disagree on a return" (CLAUDE.md, mistake 6).
        """
        rows = {}
        for item, difference in differences.items():
            amount = round_money(difference)
            if amount:
                account = inventory_account_for(item)
                rows[account] = rows.get(account, Decimal("0")) + amount
        rows = {account: amount for account, amount in rows.items() if amount}
        if not rows:
            return None
        variance = Company.get().purchase_price_variance_account
        if variance is None:
            raise ValidationError(
                "These goods go back at a price other than what the shelf holds them at, "
                "and the company has no purchase price variance account to take the "
                "difference. Set one, then post the return.")
        memo = f"Price difference returning goods to vendor for {self.purchase_order}"
        entry = JournalEntry.objects.create(date=self.receipt_date,
                                            reference=self.reference or self.number, memo=memo)
        for account, amount in rows.items():
            # Credited at cost, the shelf gave up less: inventory is put
            # back and the vendor's refund is a gain. The other way round,
            # a loss.
            gain, loss = (amount, Decimal("0")) if amount > 0 else (Decimal("0"), -amount)
            JournalLine.objects.create(entry=entry, account=account, debit=gain, credit=loss,
                                       description=memo[:255])
            JournalLine.objects.create(entry=entry, account=variance, debit=loss, credit=gain,
                                       description=memo[:255])
        entry.post()
        return entry

    @serialised("posted")
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
            receipt_date=timezone.localdate(),
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
    accrued_unit_price = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, editable=False,
        help_text="The agreed price this receipt accrued at, in the order's "
                  "currency, frozen. The order's price can still be revised "
                  "before a bill arrives; what this receipt booked cannot.",
    )
    accrued_unit_cost = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, editable=False,
        help_text="The same in base currency, at the receipt's rate — what "
                  "went into goods received not invoiced per unit.",
    )
    outside_movement = models.ForeignKey(
        "manufacturing.OutsideMovement", null=True, blank=True,
        on_delete=models.PROTECT, related_name="receipt_lines", editable=False,
        help_text="Where this line put a vendor's work into a run, when it "
                  "pays for an outside step.",
    )
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
        lock_positions(
            (self.order_line.item, step)
            for step in self.route_steps() if step is not None
        )
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
            transfer_date=to_date(occurred_at) or timezone.localdate(),
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
        self._check_line()

    def _check_line(self):
        # Its quantity is the database's to refuse (received_quantity_positive),
        # which answers beside the field rather than above the form.
        if self.order_line_id and self.receipt_id and self.order_line.order_id != self.receipt.purchase_order_id:
            raise ValidationError("This line's order_line must belong to the receipt's purchase_order.")

    def save(self, *args, **kwargs):
        if self.receipt_id and GoodsReceipt.objects.filter(pk=self.receipt_id, posted=True).exists():
            raise ValidationError(
                "Cannot modify a line on a posted goods receipt. Create a return instead."
            )
        # In save() rather than only clean(): receipts are built in code,
        # where nothing calls full_clean() for us.
        self._check_line()
        if self.order_line_id and self.order_line.is_charge():
            raise ValidationError(
                f"'{self.order_line.charge}' is a charge, not goods; nothing arrives for it."
            )
        if self._state.adding and self.warehouse_id is None and self.order_line_id:
            # Where the order said it was going, unless the receipt says
            # where it actually went.
            self.warehouse_id = self.order_line.warehouse_id
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
    @serialised("released_entry")
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
            entry_date=to_date(on_date) or timezone.localdate(),
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


from .tds import TdsChallan, TdsDeduction  # noqa: E402,F401
from .freight import FreightDelivery  # noqa: E402,F401
