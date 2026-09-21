"""
The admin is the whole interface this ERP has, so it is worth more than
a list of field names.

Two things it does beyond registering models. A specification's derived
figures — GSM, grammes a bag, metres to the kilo — are shown on the
page that sets the mesh and the denier, because a planner setting a
10 x 10 mesh needs to see 87.5 GSM come back before saving, not after
a loom has run. And a computed bill of materials is read-only here
rather than merely refused on save: the model would raise, and an
admin page that lets you type for a minute and then refuses is a worse
answer than one that does not let you type.
"""

from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from apps.core.audit import AuditableAdminMixin

from .bom import BillOfMaterials, BomByproduct, BomComponent
from .orders import (
    ManufacturingSettings,
    MaterialIssue,
    MaterialIssueLine,
    ProductionByproduct,
    ProductionEntry,
    WorkCentre,
    WorkOrder,
    WorkOrderComponent,
    WorkOrderStatus,
)
from .woven import BagSpecification, FabricSpecification, TapeSpecification


def _run(request, action, *args, **kwargs):
    """Let a model's refusal reach the page as the sentence it wrote."""
    try:
        action(*args, **kwargs)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return False
    return True


def _frozen(modeladmin, obj, declared):
    """
    Every editable field, for a document that refuses to be edited.

    The models already refuse; this is the other half of the same rule.
    An admin page that lets somebody retype a closed run's quantity for
    a minute and then refuses the save is a worse answer than one that
    does not offer the box.
    """
    return list(declared) + [
        field.name for field in modeladmin.model._meta.fields if field.editable
    ]


def _act(label, method, description):
    """An admin action that calls one model method on each row."""

    def run(modeladmin, request, queryset):
        done = 0
        for row in queryset:
            if _run(request, getattr(row, method)):
                done += 1
        if done:
            messages.success(request, f"{label}: {done}.")

    run.short_description = description
    run.__name__ = f"action_{method}"
    return run


# -- specifications -----------------------------------------------------

@admin.register(TapeSpecification)
class TapeSpecificationAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "tape_item", "denier", "shown_virgin", "regrind_percent",
                    "filler_percent", "extrusion_waste_percent", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name", "tape_item__sku")
    readonly_fields = ("bom", "shown_virgin", "shown_metres_per_kg")

    @admin.display(description="Virgin polymer (derived)")
    def shown_virgin(self, obj):
        return f"{obj.virgin_percent()}%"

    @admin.display(description="Metres per kg")
    def shown_metres_per_kg(self, obj):
        return f"{obj.metres_per_kg():,.0f} m"


@admin.register(FabricSpecification)
class FabricSpecificationAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "fabric_item", "shown_mesh", "shown_gsm", "target_gsm",
                    "shown_deviation", "lay_flat_width_cm", "weave", "is_active")
    list_filter = ("weave", "is_active")
    search_fields = ("code", "name", "fabric_item__sku")
    readonly_fields = ("bom", "shown_gsm", "shown_deviation", "shown_grams_per_metre",
                       "shown_metres_per_kg")

    @admin.display(description="Mesh")
    def shown_mesh(self, obj):
        return f"{obj.ends_per_inch:g} x {obj.picks_per_inch:g}"

    @admin.display(description="GSM (derived)")
    def shown_gsm(self, obj):
        return f"{obj.gsm():.2f}"

    @admin.display(description="Off target")
    def shown_deviation(self, obj):
        return f"{obj.gsm_deviation_percent():+.2f}%"

    @admin.display(description="Grammes per running metre")
    def shown_grams_per_metre(self, obj):
        return f"{obj.grams_per_metre():.2f} g"

    @admin.display(description="Metres per kg")
    def shown_metres_per_kg(self, obj):
        return f"{obj.metres_per_kg():.3f} m"


@admin.register(BagSpecification)
class BagSpecificationAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "bag_item", "shown_size", "fabric", "is_laminated",
                    "print_colours", "shown_bag_grams", "is_active")
    list_filter = ("is_laminated", "is_active")
    search_fields = ("code", "name", "bag_item__sku")
    readonly_fields = ("bom", "shown_cut_length", "shown_area", "shown_fabric_grams",
                       "shown_bag_grams", "shown_fabric_metres")

    @admin.display(description="Size")
    def shown_size(self, obj):
        return f"{obj.bag_width_cm:g} x {obj.bag_length_cm:g} cm"

    @admin.display(description="Cut length")
    def shown_cut_length(self, obj):
        return f"{obj.cut_length_cm():g} cm (hems included)"

    @admin.display(description="Fabric area per bag")
    def shown_area(self, obj):
        return f"{obj.fabric_area_sqm():.4f} m2"

    @admin.display(description="Fabric per bag")
    def shown_fabric_grams(self, obj):
        return f"{obj.fabric_grams():.3f} g"

    @admin.display(description="Finished bag weight")
    def shown_bag_grams(self, obj):
        return f"{obj.bag_grams():.3f} g"

    @admin.display(description="Fabric per bag (length)")
    def shown_fabric_metres(self, obj):
        return f"{obj.fabric_metres_per_bag():.3f} m"


# -- bills of materials -------------------------------------------------

class ComputedInlineMixin:
    """
    An inline on a computed bill of materials offers nothing.

    Freezing the parent's own fields and leaving its lines editable is
    the same mistake one level down, and it is the one the screenshot
    caught: the header was read-only and the component quantities were
    still boxes with an "add another" under them.
    """

    def _frozen_parent(self, obj):
        return obj is not None and obj.is_computed

    def has_add_permission(self, request, obj=None):
        return not self._frozen_parent(obj)

    def has_change_permission(self, request, obj=None):
        return not self._frozen_parent(obj)

    def has_delete_permission(self, request, obj=None):
        return not self._frozen_parent(obj)


class BomComponentInline(ComputedInlineMixin, admin.TabularInline):
    model = BomComponent
    extra = 1
    autocomplete_fields = ("item",)
    fields = ("line_number", "item", "quantity", "uom", "waste_percent",
              "shown_gross", "notes")
    readonly_fields = ("shown_gross",)

    @admin.display(description="Gross (waste included)")
    def shown_gross(self, obj):
        if obj.pk is None:
            return "-"
        return f"{obj.gross_quantity():.6f}"


class BomByproductInline(ComputedInlineMixin, admin.TabularInline):
    model = BomByproduct
    extra = 0
    autocomplete_fields = ("item",)


@admin.register(BillOfMaterials)
class BillOfMaterialsAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("__str__", "item", "version", "is_computed", "is_default",
                    "is_active")
    list_filter = ("is_computed", "is_default", "is_active")
    search_fields = ("name", "item__sku")
    inlines = [BomComponentInline, BomByproductInline]

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.is_computed:
            return _frozen(self, obj, ())
        return ()

    def has_delete_permission(self, request, obj=None):
        return obj is None or not obj.is_computed


# -- runs ---------------------------------------------------------------

@admin.register(WorkCentre)
class WorkCentreAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name", "capacity_per_hour", "capacity_uom", "is_active")
    search_fields = ("code", "name")


class WorkOrderComponentInline(admin.TabularInline):
    model = WorkOrderComponent
    extra = 0
    readonly_fields = ("item", "quantity_required", "uom", "waste_percent",
                       "shown_issued", "line_number")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="Issued so far")
    def shown_issued(self, obj):
        if obj.pk is None:
            return "-"
        return f"{obj.quantity_issued():.4f}"


@admin.register(WorkOrder)
class WorkOrderAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("number", "item", "quantity_ordered", "uom", "work_centre",
                    "status", "shown_produced", "planned_unit_cost", "shown_wip",
                    "shown_unaccounted")
    list_filter = ("status", "work_centre", "warehouse")
    search_fields = ("number", "item__sku")
    inlines = [WorkOrderComponentInline]
    readonly_fields = ("number", "status", "planned_unit_cost",
                       "planned_material_cost", "released_at", "closed_at",
                       "close_entry", "reopened_entry", "shown_wip", "shown_unaccounted",
                       "shown_variance")

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.status in (
            WorkOrderStatus.CLOSED, WorkOrderStatus.CANCELLED
        ):
            return _frozen(self, obj, self.readonly_fields)
        return self.readonly_fields
    actions = [
        _act("Released", "release", "Release: freeze the requirements and the cost"),
        _act("Closed", "close", "Close: send what is left in WIP to variance"),
        _act("Reopened", "reopen", "Reopen: reverse the close"),
        _act("Cancelled", "cancel", "Cancel (only if nothing has been drawn)"),
    ]

    @admin.display(description="Produced")
    def shown_produced(self, obj):
        return f"{obj.quantity_produced():.4f}"

    @admin.display(description="In WIP")
    def shown_wip(self, obj):
        if obj.pk is None:
            return "-"
        return f"{obj.wip_balance():,.2f}"

    @admin.display(description="Consumed over plan")
    def shown_unaccounted(self, obj):
        if obj.pk is None:
            return "-"
        return f"{obj.unaccounted():,.2f}"

    @admin.display(description="Material variance, in stocking units")
    def shown_variance(self, obj):
        if obj.pk is None:
            return "-"
        rows = [
            f"{item.sku}: wanted {want:.4f}, took {got:.4f} ({diff:+.4f})"
            for item, want, got, diff in obj.material_variance()
        ]
        return "; ".join(rows) or "-"


class PostedInlineMixin:
    """A posted document's lines refuse to be edited, so do not offer it."""

    def _posted(self, obj):
        return obj is not None and obj.posted

    def has_add_permission(self, request, obj=None):
        return not self._posted(obj)

    def has_change_permission(self, request, obj=None):
        return not self._posted(obj)

    def has_delete_permission(self, request, obj=None):
        return not self._posted(obj)


class MaterialIssueLineInline(PostedInlineMixin, admin.TabularInline):
    model = MaterialIssueLine
    fk_name = "issue"
    extra = 1
    autocomplete_fields = ("item", "lot")
    readonly_fields = ("unit_cost", "stock_movement")


@admin.register(MaterialIssue)
class MaterialIssueAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("number", "work_order", "direction", "issue_date", "warehouse",
                    "posted", "posted_value", "voided_at")
    list_filter = ("direction", "posted", "warehouse")
    search_fields = ("number", "work_order__number")
    inlines = [MaterialIssueLineInline]
    readonly_fields = ("number", "posted", "posted_at", "posted_value",
                       "journal_entry", "voided_entry", "voided_at")

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.posted:
            return _frozen(self, obj, self.readonly_fields)
        return self.readonly_fields

    actions = [
        _act("Posted", "post", "Post: draw the material and charge it to WIP"),
        _act("Voided", "void", "Void: put the material back"),
    ]


class ProductionByproductInline(PostedInlineMixin, admin.TabularInline):
    model = ProductionByproduct
    extra = 0
    autocomplete_fields = ("item", "lot")
    readonly_fields = ("unit_value", "stock_movement")


@admin.register(ProductionEntry)
class ProductionEntryAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("number", "work_order", "entry_date", "quantity_produced",
                    "quantity_scrapped", "uom", "work_centre", "posted",
                    "unit_cost", "posted_value")
    list_filter = ("posted", "warehouse", "work_centre")
    search_fields = ("number", "work_order__number")
    inlines = [ProductionByproductInline]
    readonly_fields = ("number", "posted", "posted_at", "posted_value", "unit_cost",
                       "stock_movement", "journal_entry", "voided_entry", "voided_at")

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.posted:
            return _frozen(self, obj, self.readonly_fields)
        return self.readonly_fields

    actions = [
        _act("Posted", "post", "Post: book the output at the planned cost"),
        _act("Voided", "void", "Void: take the output back off the shelf"),
    ]


@admin.register(ManufacturingSettings)
class ManufacturingSettingsAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("__str__", "wip_account", "variance_account", "scrap_account")

    def has_add_permission(self, request):
        # One company, one set of accounts. A second row would be read by
        # nothing and quietly disagree with the first.
        return not ManufacturingSettings.objects.exists()
