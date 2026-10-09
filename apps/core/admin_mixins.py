"""
Admin mixins for documents that become immutable once posted.

The models already refuse to save a posted record, but a raw admin form
would still render editable fields and a SAVE button, turning that
refusal into an uncaught ValidationError (HTTP 500) instead of a clear
read-only view. These mixins make the admin agree with the model.

And the changelist's "delete selected", every model's, deletes each row
through its own delete() (`delete_selected`, registered site-wide in
core/admin.py).
"""

from django.contrib import messages
from django.contrib.admin import action
from django.contrib.admin.actions import delete_selected as djangos_delete_selected
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import ProtectedError, RestrictedError
from django.utils.translation import gettext_lazy


class PostedImmutableAdminMixin:
    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.posted:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.posted:
            return False
        return super().has_delete_permission(request, obj)


class PostedImmutableInlineMixin:
    """`obj` here is the parent document, not the inline row."""

    def has_add_permission(self, request, obj=None):
        if obj is not None and obj.posted:
            return False
        return super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.posted:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.posted:
            return False
        return super().has_delete_permission(request, obj)


@action(permissions=["delete"], description=gettext_lazy("Delete selected %(verbose_name_plural)s"))
def delete_selected(modeladmin, request, queryset):
    """
    Django's "delete selected", each row through its own delete().

    Django's runs one QuerySet.delete(), which never calls a model's
    delete(): a staff HR Admin deleted approved leave in bulk that the API
    refuses, and the same held for every model whose delete() says no
    (fixed assets, requisitions, blanket orders, quotes, bank statements,
    maintenance jobs...). The confirmation page and the permission checks
    are still Django's; the deleting is one transaction, so one row
    refused leaves every row as it was.
    """
    _deletable, _counts, lacking, protected = modeladmin.get_deleted_objects(queryset, request)
    if not request.POST.get("post") or lacking or protected:
        return djangos_delete_selected(modeladmin, request, queryset)
    rows = list(queryset)
    row = None
    try:
        with transaction.atomic():
            modeladmin.log_deletions(request, rows)
            for row in rows:
                row.delete()
    except (ValidationError, ProtectedError, RestrictedError) as refused:
        said = " ".join(getattr(refused, "messages", None) or [str(refused)])
        modeladmin.message_user(request, f"Nothing was deleted. {row}: {said}", messages.ERROR)
        return None
    modeladmin.message_user(request, f"Deleted {len(rows)}.", messages.SUCCESS)
    return None
