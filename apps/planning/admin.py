"""
The planning pages.

A planned order is shown with the reason it exists inline, because the
number on its own is unarguable and the reason is the whole point. The
list leads with how late a suggestion already is, since that is the
only column that changes what a planner does this morning.
"""

from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from apps.core.audit import AuditableAdminMixin

from .models import (
    PlannedDemand,
    PlannedOrder,
    PlanningAction,
    PlanningRun,
    PlanningSettings,
)


def _act(label, method, description):
    def run(modeladmin, request, queryset):
        done = 0
        for row in queryset:
            try:
                getattr(row, method)()
                done += 1
            except ValidationError as error:
                messages.error(request, " ".join(error.messages))
        if done:
            messages.success(request, f"{label}: {done}.")

    run.short_description = description
    run.__name__ = f"action_{method}"
    return run


@admin.register(PlanningSettings)
class PlanningSettingsAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ["__str__", "horizon_days", "default_buy_lead_days",
                    "default_make_lead_days", "queue_days"]


class PlannedDemandInline(admin.TabularInline):
    model = PlannedDemand
    fk_name = "planned_order"
    extra = 0
    fields = ["source", "quantity", "needed_by", "sales_order_line",
              "work_order", "parent"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(PlanningRun)
class PlanningRunAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ["__str__", "warehouse", "planned_on", "horizon_end",
                    "suggested", "late_count", "pull_in", "push_out",
                    "lapsed_count", "complete"]
    list_filter = ["warehouse"]
    readonly_fields = ["ran_at", "cut_links", "deferred_demand"]

    @admin.display(description="Suggestions")
    def suggested(self, obj):
        return obj.suggestions().count()

    @admin.display(boolean=True, description="Everything netted")
    def complete(self, obj):
        return obj.is_complete()

    @admin.display(description="Late")
    def late_count(self, obj):
        return len(obj.late()) or ""

    @admin.display(description="Lapsed")
    def lapsed_count(self, obj):
        return len(obj.lapsed()) or ""

    @admin.display(description="Pull in")
    def pull_in(self, obj):
        return obj.expedites().count() or ""

    @admin.display(description="Push out")
    def push_out(self, obj):
        return obj.defers().count() or ""


@admin.register(PlanningAction)
class PlanningActionAdmin(AuditableAdminMixin, admin.ModelAdmin):
    """
    Orders that exist and are dated wrong, worst first.

    Read-only, because an action is a message: pulling a purchase in
    means ringing a vendor who may say no, and the person who owns
    that order decides.
    """

    list_display = ["run", "action", "item", "quantity", "scheduled_on",
                    "wanted_on", "days", "order", "because"]
    list_filter = ["run", "action", "source", "warehouse"]
    search_fields = ["item__sku", "item__name", "because"]

    @admin.display(description="Order")
    def order(self, obj):
        return obj.document()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PlannedOrder)
class PlannedOrderAdmin(AuditableAdminMixin, admin.ModelAdmin):
    """
    A suggestion, the reasons under it, and which plan proposed it.
    """

    # The run leads, and it is not optional. Planning is re-run whenever
    # anything changes and every run keeps its own suggestions, so a
    # list without it shows three plans' worth of identical-looking rows
    # and reads as duplicated data — which is what it looked like the
    # first time this page was opened against a real plant.
    list_display = ["run", "item", "kind", "quantity", "needed_by",
                    "release_on", "late", "level", "status", "warehouse"]
    list_filter = ["run", "kind", "status", "warehouse", "level"]
    search_fields = ["item__sku", "item__name"]
    inlines = [PlannedDemandInline]
    readonly_fields = ["work_order", "requisition_line", "firmed_at",
                       "rounded_up_by", "lead_days", "level"]
    actions = [
        _act("Firmed", "firm", "Firm into a work order or requisition"),
        _act("Cancelled", "cancel", "Cancel this suggestion"),
    ]

    @admin.display(description="Days late")
    def late(self, obj):
        return obj.days_late() or ""

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "run", "run__warehouse", "item", "warehouse"
        )
