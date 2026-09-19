"""
Shared mixins that stamp created_by/updated_by on AuditModel subclasses.
Every module's admin/viewsets should use these instead of setting the
fields ad hoc, so "who changed this" is handled consistently everywhere.
"""


class AuditableAdminMixin:
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


class AuditableViewSetMixin:
    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)
