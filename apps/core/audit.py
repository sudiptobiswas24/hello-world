"""
Shared mixins that stamp created_by/updated_by on AuditModel subclasses.
Every module's admin/viewsets should use these instead of setting the
fields ad hoc, so "who changed this" is handled consistently everywhere.
"""

from django.db import transaction


class AuditableAdminMixin:
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


class AuditableViewSetMixin:
    # Each write in a savepoint of its own: when the database refuses it
    # (a check constraint no serializer runs), only that write is undone
    # and the connection is still good for the answer that says why.
    def perform_create(self, serializer):
        with transaction.atomic():
            serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer):
        with transaction.atomic():
            serializer.save(updated_by=self.request.user)

    def perform_destroy(self, instance):
        with transaction.atomic():
            instance.delete()
