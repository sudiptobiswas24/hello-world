"""
Shared mixins that stamp created_by/updated_by on AuditModel subclasses
and write the record's history (apps/core/history.py): made, which
fields changed, each action taken on it, deleted. Every module's
admin/viewsets use these instead of doing it ad hoc, so "who changed
this, and what did they do" is handled consistently everywhere.
"""

from django.db import transaction

from .history import EventKind, record_by_id
from .models import lock_for_change


class AuditableAdminMixin:
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


# The actions whose own code writes the history line, with what matters
# (a mail says who it went to), so the generic line would be a second.
WRITES_ITS_OWN_HISTORY = {"send"}


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
            self._deleting = str(instance)
            instance.delete()

    # A PUT, PATCH or DELETE in one transaction, from the read to the
    # write: get_object() reads its row under the lock.
    def update(self, request, *args, **kwargs):
        with transaction.atomic():
            self._changing = True
            return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        with transaction.atomic():
            self._changing = True
            return super().destroy(request, *args, **kwargs)

    def get_object(self):
        """
        The row a PUT, PATCH or DELETE changes, read again once it is held.

        DRF reads the row, checks the request against it and saves every
        field it read. Two people editing one party at once, one its name
        and the other its email: both read it, and whichever saved second
        wrote the other's field back as it had found it. Held from the
        read, the second waits for the first to commit, then reads what it
        did. Read first to answer 404 and to know what to hold
        (apps.core.models.lock_for_change).
        """
        found = super().get_object()
        if getattr(self, "_changing", False):
            lock_for_change(found)
            found = super().get_object()
        return found

    def finalize_response(self, request, response, *args, **kwargs):
        """
        A write that succeeded is a line in the record's history: made,
        changed (the fields the request named), each action (posted,
        voided, approved), deleted. Written here, after the fact, so a
        viewset that saves its own way (an owner forced, a party made with
        its roles) is in the history like any other.
        """
        response = super().finalize_response(request, response, *args, **kwargs)
        action = getattr(self, "action", None)
        if request.method not in ("POST", "PUT", "PATCH", "DELETE") or not 200 <= response.status_code < 300:
            return response
        model = self.get_queryset().model
        pk = str(self.kwargs.get(self.lookup_url_kwarg or self.lookup_field, ""))
        if action == "create":
            made = getattr(response, "data", None) or {}
            if str(made.get("id", "")).isdigit():
                record_by_id(model, int(made["id"]), request.user, EventKind.CREATED)
        elif action in ("update", "partial_update") and pk.isdigit():
            record_by_id(model, int(pk), request.user, EventKind.UPDATED, summary=", ".join(sorted(request.data)))
        elif action == "destroy" and pk.isdigit():
            record_by_id(model, int(pk), request.user, EventKind.DELETED, summary=getattr(self, "_deleting", ""))
        elif action not in (None, "create", "update", "partial_update", "destroy") and getattr(self, "detail", False) \
                and action not in WRITES_ITS_OWN_HISTORY and pk.isdigit():
            record_by_id(model, int(pk), request.user, EventKind.ACTION, action=action,
                         summary=getattr(self, "_action_summary", ""))
        return response
