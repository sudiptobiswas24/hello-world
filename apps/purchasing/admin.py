from django.contrib import admin

from apps.core.admin_mixins import PostedImmutableAdminMixin, PostedImmutableInlineMixin
from apps.core.audit import AuditableAdminMixin

from .models import (
    Bill,
    BillLine,
    BillPayment,
    PrepaymentApplication,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseApprovalPolicy,
    VendorPrice,
    BlanketOrder,
    BlanketOrderLine,
    PurchaseRequisition,
    PurchaseRequisitionLine,
    RequestForQuotation,
    RfqInvitation,
    RfqLine,
    RfqQuote,
    LandedCostApplication,
    ApprovalTier,
    ReceiptInspection,
    ReorderRule,
    Budget,
    SubcontractComponent,
)


class SubcontractComponentInline(admin.TabularInline):
    model = SubcontractComponent
    extra = 0
    autocomplete_fields = ("item",)
    fields = ("item", "quantity_per", "is_computed")
    readonly_fields = ("is_computed",)

    def _computed(self, obj):
        return obj is not None and obj.components.filter(is_computed=True).exists()

    def has_add_permission(self, request, obj=None):
        return not self._computed(obj)

    def has_change_permission(self, request, obj=None):
        return not self._computed(obj)

    def has_delete_permission(self, request, obj=None):
        return not self._computed(obj)


@admin.register(PurchaseOrderLine)
class PurchaseOrderLineAdmin(AuditableAdminMixin, admin.ModelAdmin):
    """
    Registered for its own page because a job-work line's components do
    not fit on an inline row inside the order, and they are the part
    somebody checks before the fabric goes out of the gate.
    """

    list_display = ("order", "item", "quantity", "uom", "unit_price", "bom",
                    "shown_components")
    list_filter = ("order__status",)
    search_fields = ("order__number", "item__sku")
    autocomplete_fields = ("item",)
    inlines = [SubcontractComponentInline]

    @admin.display(description="Goes out with")
    def shown_components(self, obj):
        rows = list(obj.components.select_related("item"))
        if not rows:
            return "—"
        made = "computed" if rows[0].is_computed else "typed"
        return f"{len(rows)} component(s), {made}"


class PurchaseOrderLineInline(admin.TabularInline):
    model = PurchaseOrderLine
    extra = 1
    filter_horizontal = ("taxes",)
    show_change_link = True


class RfqLineInline(admin.TabularInline):
    model = RfqLine
    extra = 1


class RfqInvitationInline(admin.TabularInline):
    model = RfqInvitation
    extra = 1


@admin.register(RequestForQuotation)
class RequestForQuotationAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("number", "issue_date", "response_due", "status")
    list_filter = ("status",)
    inlines = [RfqLineInline, RfqInvitationInline]


@admin.register(ReceiptInspection)
class ReceiptInspectionAdmin(admin.ModelAdmin):
    list_display = ("receipt_line", "quantity", "accepted", "inspected_on", "warehouse")
    list_filter = ("accepted",)
    readonly_fields = ("receipt_line", "quantity", "accepted", "inspected_on", "warehouse")

    def has_add_permission(self, request):
        # Recorded by GoodsReceipt.accept()/reject(), which also move the
        # stock — a form here would record a decision nothing acted on.
        return False


@admin.register(LandedCostApplication)
class LandedCostApplicationAdmin(admin.ModelAdmin):
    list_display = ("charge_line", "receipt_line", "amount", "date", "is_released")
    readonly_fields = (
        "charge_line", "receipt_line", "amount", "date",
        "journal_entry", "stock_movement", "released_entry",
    )

    def has_add_permission(self, request):
        # Allocating posts to the ledger and moves stock value, so it goes
        # through BillLine.allocate_landed_cost() rather than a form.
        return False

    @admin.display(boolean=True, description="Released")
    def is_released(self, obj):
        return obj.is_released()


@admin.register(RfqQuote)
class RfqQuoteAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("invitation", "line", "unit_price", "lead_time_days")


class PurchaseRequisitionLineInline(admin.TabularInline):
    model = PurchaseRequisitionLine
    extra = 1


@admin.register(PurchaseRequisition)
class PurchaseRequisitionAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = (
        "number", "requested_by", "request_date", "needed_by", "status", "estimated_total",
    )
    list_filter = ("status",)
    readonly_fields = ("decided_by", "decided_at", "decision_note")
    inlines = [PurchaseRequisitionLineInline]


class BlanketOrderLineInline(admin.TabularInline):
    model = BlanketOrderLine
    extra = 1
    filter_horizontal = ("taxes",)


@admin.register(BlanketOrder)
class BlanketOrderAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("number", "vendor", "start_date", "end_date", "status")
    list_filter = ("status",)
    inlines = [BlanketOrderLineInline]


@admin.register(Budget)
class BudgetAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = (
        "code", "name", "account", "start_date", "end_date", "amount",
        "spent", "committed", "requested", "available", "is_active",
    )
    list_filter = ("is_active", "account")


@admin.register(ReorderRule)
class ReorderRuleAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = (
        "item", "warehouse", "minimum", "target", "multiple_of", "vendor", "is_active",
    )
    list_filter = ("is_active", "warehouse")
    search_fields = ("item__sku",)


@admin.register(VendorPrice)
class VendorPriceAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = (
        "item", "vendor", "unit_price", "min_quantity", "currency",
        "lead_time_days", "is_preferred", "is_active",
    )
    list_filter = ("is_active", "is_preferred", "vendor")
    search_fields = ("item__sku", "vendor__name", "vendor_item_code")


class ApprovalTierInline(admin.TabularInline):
    model = ApprovalTier
    extra = 1


@admin.register(PurchaseApprovalPolicy)
class PurchaseApprovalPolicyAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = (
        "code", "name", "max_order_value", "max_line_value",
        "require_approval_without_vendor_price", "is_active",
    )
    list_filter = ("is_active",)
    inlines = [ApprovalTierInline]


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = (
        "number", "vendor", "order_date", "status", "approval_status", "receipt_status",
    )
    list_filter = ("status",)
    inlines = [PurchaseOrderLineInline]


class BillLineInline(PostedImmutableInlineMixin, admin.TabularInline):
    model = BillLine
    extra = 1
    filter_horizontal = ("taxes",)


class BillPaymentInline(admin.TabularInline):
    model = BillPayment
    extra = 0


@admin.register(Bill)
class BillAdmin(PostedImmutableAdminMixin, AuditableAdminMixin, admin.ModelAdmin):
    list_display = (
        "number", "vendor", "bill_date", "due_date", "posted",
        "settlement_status", "debits",
    )
    list_filter = ("posted",)
    readonly_fields = ("number", "due_date", "exchange_rate", "posted", "posted_at", "journal_entry")
    inlines = [BillLineInline, BillPaymentInline]


class GoodsReceiptLineInline(PostedImmutableInlineMixin, admin.TabularInline):
    model = GoodsReceiptLine
    extra = 1


@admin.register(PrepaymentApplication)
class PrepaymentApplicationAdmin(admin.ModelAdmin):
    list_display = ("prepayment", "bill", "amount", "date")
    readonly_fields = ("prepayment", "bill", "amount", "date", "journal_entry")

    def has_add_permission(self, request):
        # Drawing a prepayment down posts to the ledger, so it goes through
        # Bill.apply_prepayment() rather than a form.
        return False


@admin.register(GoodsReceipt)
class GoodsReceiptAdmin(PostedImmutableAdminMixin, AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("number", "purchase_order", "receipt_date", "posted", "reverses")
    list_filter = ("posted",)
    readonly_fields = ("number", "posted", "posted_at")
    inlines = [GoodsReceiptLineInline]
