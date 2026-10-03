"""
Admin mixins for documents that become immutable once posted.

The models already refuse to save a posted record, but a raw admin form
would still render editable fields and a SAVE button, turning that
refusal into an uncaught ValidationError (HTTP 500) instead of a clear
read-only view. These mixins make the admin agree with the model.
"""


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
