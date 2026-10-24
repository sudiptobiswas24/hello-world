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


class RowHeldAdminMixin:
    """
    An admin change or delete holds its row from the read to the write, as
    the viewsets do (AuditableViewSetMixin.get_object). The admin reads the
    row, shows or checks the form against it, and saves every field it
    read, all inside one transaction (changeform_view, delete_view), but
    read the row without a lock: two people saving one record at once each
    wrote the other's field back as they had found it (O22). Held through
    lock_for_change, so what its save() holds first is held before it.
    """

    def get_object(self, request, object_id, from_field=None):
        found = super().get_object(request, object_id, from_field)
        if found is not None and request.method == "POST" and transaction.get_connection().in_atomic_block:
            lock_for_change(found, request.POST)
            found = super().get_object(request, object_id, from_field)
        return found


class AuditableAdminMixin(RowHeldAdminMixin):
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


# The actions whose own code writes the history line, with what matters
# (a mail says who it went to), so the generic line would be a second.
WRITES_ITS_OWN_HISTORY = {"send"}


class _WhereTheViewSetHasIt:
    """
    The mixin's update() or destroy(), present only on a viewset whose own
    bases have one to wrap. Defined outright, they gave a read-only viewset
    a PUT and a DELETE route, advertised in its Allow header and OPTIONS,
    that answered 405 (review F6): the router maps whatever the class has.
    """

    def __init__(self, method):
        self.method = method

    def __set_name__(self, owner, name):
        self.owner, self.name = owner, name

    def __get__(self, instance, cls=None):
        cls = cls if cls is not None else type(instance)
        below = cls.__mro__[cls.__mro__.index(self.owner) + 1:]
        if not any(self.name in base.__dict__ for base in below):
            raise AttributeError(self.name)
        return self.method if instance is None else self.method.__get__(instance, cls)


class RowHeldViewSetMixin:
    """
    A PUT, PATCH or DELETE holds its row from the read to the write. The
    part of AuditableViewSetMixin a viewset of a model with no stamps takes
    (a country, a party tag, one's saved filter), and one that only deletes
    (an e-invoice, an e-way bill, a month's GSTR-2B): without it, their
    edits took no lock (O17).
    """

    # A PUT, PATCH or DELETE in one transaction, from the read to the
    # write: get_object() reads its row under the lock. Only where the
    # viewset's bases update or destroy (_WhereTheViewSetHasIt): a read-only
    # viewset has no PUT or DELETE route at all.
    @_WhereTheViewSetHasIt
    def update(self, request, *args, **kwargs):
        with transaction.atomic():
            self._changing = True
            return super().update(request, *args, **kwargs)

    @_WhereTheViewSetHasIt
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
            lock_for_change(found, getattr(self.request, "data", None))
            found = super().get_object()
        return found



class AuditableViewSetMixin(RowHeldViewSetMixin):
    # A method a route does not map answers 405. DRF finds a request's
    # handler by its method's name, so on a route that maps no POST, an
    # action named `post` answered it: a POST to a GET-only report ran
    # the document's post action with no document (a 500), and a POST to
    # a document's own address posted it, asked only the add_ permission,
    # not its action's (review F6). audit_invariants reports a viewset
    # with such an action that does not take this.
    @classmethod
    def reads_only_some(cls, user):
        """Whether this screen reads `user` only some of its kind: so a deleted one's history is not theirs (endpoints.narrows_for)."""
        from .endpoints import narrows_for

        return narrows_for(cls, user)

    def dispatch(self, request, *args, **kwargs):
        mapped = getattr(self, "action_map", None)
        if mapped is not None:
            for verb in self.http_method_names:
                if verb not in mapped and hasattr(getattr(type(self), verb, None), "mapping"):
                    setattr(self, verb, self.http_method_not_allowed)
        return super().dispatch(request, *args, **kwargs)

    # Each write in a savepoint of its own: when the database refuses it
    # (a check constraint no serializer runs), only that write is undone
    # and the connection is still good for the answer that says why. A
    # viewset that sets a field of its own (the owner, the employee) passes
    # it as `extra` and calls these: replaced, they took the transaction
    # and the stamps with them (audit_invariants: writes_around_the_mixin).
    def perform_create(self, serializer, **extra):
        with transaction.atomic():
            serializer.save(created_by=self.request.user, updated_by=self.request.user, **extra)

    def perform_update(self, serializer, **extra):
        with transaction.atomic():
            serializer.save(updated_by=self.request.user, **extra)

    def perform_destroy(self, instance):
        with transaction.atomic():
            self._deleting = str(instance)
            instance.delete()

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
