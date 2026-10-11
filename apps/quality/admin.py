"""
The quality pages.

Two things beyond registering models. A computed plan is read-only
here, not merely refused on save — the same rule the bills of materials
follow. And an inspection shows what it measured beside what it was
measured against, because a reader looking at a released batch is
asking "how close was it", which a pass or a fail does not say.
"""

from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from apps.core.audit import AuditableAdminMixin

from .models import (
    Characteristic,
    Inspection,
    InspectionPlan,
    PlanLine,
    QualitySettings,
    Reading,
)
from .release import release_status


def _act(label, method, description, needs_reason=False):
    def run(modeladmin, request, queryset):
        done = 0
        for row in queryset:
            try:
                getattr(row, method)(
                    *(["Voided from the admin."] if needs_reason else [])
                )
                done += 1
            except ValidationError as error:
                messages.error(request, " ".join(error.messages))
        if done:
            messages.success(request, f"{label}: {done}.")

    run.short_description = description
    run.__name__ = f"action_{method}"
    return run


@admin.register(Characteristic)
class CharacteristicAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name", "kind", "uom", "decimal_places", "is_active")
    list_filter = ("kind", "is_active")
    search_fields = ("code", "name")


class PlanLineInline(admin.TabularInline):
    model = PlanLine
    extra = 1
    autocomplete_fields = ("characteristic",)
    fields = ("line_number", "characteristic", "target", "lower_limit",
              "upper_limit", "sample_size", "evaluation", "derived_from")
    readonly_fields = ("derived_from",)

    def _computed(self, obj):
        return obj is not None and obj.is_computed

    def has_add_permission(self, request, obj=None):
        return not self._computed(obj)

    def has_change_permission(self, request, obj=None):
        return not self._computed(obj)

    def has_delete_permission(self, request, obj=None):
        return not self._computed(obj)


@admin.register(InspectionPlan)
class InspectionPlanAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("__str__", "item", "is_mandatory", "is_computed",
                    "shown_lines", "is_active")
    list_filter = ("is_mandatory", "is_computed", "is_active")
    search_fields = ("item__sku", "name")
    autocomplete_fields = ("item",)
    inlines = [PlanLineInline]

    @admin.display(description="Checks")
    def shown_lines(self, obj):
        return ", ".join(
            f"{row.characteristic.code} {row.lower_limit or '—'}…"
            f"{row.upper_limit or '—'}"
            for row in obj.lines.select_related("characteristic")
        ) or "—"

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.is_computed:
            return [f.name for f in self.model._meta.fields if f.editable]
        return ()


class ReadingInline(admin.TabularInline):
    model = Reading
    extra = 1
    fields = ("plan_line", "value", "present", "sample_reference",
              "lower_limit", "upper_limit", "passed")
    readonly_fields = ("lower_limit", "upper_limit", "passed")

    def _posted(self, obj):
        return obj is not None and obj.posted

    def has_add_permission(self, request, obj=None):
        return not self._posted(obj)

    def has_change_permission(self, request, obj=None):
        return not self._posted(obj)

    def has_delete_permission(self, request, obj=None):
        return not self._posted(obj)


@admin.register(Inspection)
class InspectionAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("number", "lot", "inspected_on", "inspected_by", "result",
                    "disposition", "shown_self_approved", "posted", "voided_at")
    list_filter = ("result", "disposition", "posted", "inspected_on")
    search_fields = ("number", "lot__code", "lot__item__sku")
    raw_id_fields = ("lot",)
    inlines = [ReadingInline]
    readonly_fields = ("number", "result", "posted", "posted_at", "voided_at",
                       "voided_reason", "shown_readings", "shown_status")
    actions = [
        _act("Posted", "post", "Post: judge the readings and decide"),
        _act("Voided", "void", "Void: hand the batch back to the inspection "
                               "before this one", needs_reason=True),
    ]

    @admin.display(description="Signed off by the inspector", boolean=True)
    def shown_self_approved(self, obj):
        return obj.self_approved()

    @admin.display(description="What was measured")
    def shown_readings(self, obj):
        if obj.pk is None:
            return "-"
        rows = []
        for row in obj.readings_by_line():
            values = ", ".join(str(v) for v in row["values"] if v is not None)
            verdict = {True: "pass", False: "FAIL", None: "—"}[row["passed"]]
            rows.append(
                f"{row['characteristic'].code}: {values} "
                f"(mean {row['mean']}, target {row['plan_line'].target}) {verdict}"
            )
        return " · ".join(rows) or "—"

    @admin.display(description="The batch now reads")
    def shown_status(self, obj):
        if obj.pk is None:
            return "-"
        return release_status(obj.lot)

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.posted:
            return list(self.readonly_fields) + [
                f.name for f in self.model._meta.fields if f.editable
            ]
        return self.readonly_fields


@admin.register(QualitySettings)
class QualitySettingsAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("__str__", "concessions_need_a_second_person")

    def has_add_permission(self, request):
        return not QualitySettings.objects.exists()
