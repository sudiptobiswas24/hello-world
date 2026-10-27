"""
What happened to a record, by whom, when and through what: made, changed
(each field's old and new value), each action taken on it (posted,
voided, approved, sent), each mail that went out about it, deleted. One
table for every kind of record, read on the record's own page and, across
every record, in the edit log (Companies (Accounts) Rules 2014, rule 3(1)
proviso: an edit log of each change, with the date it was made, that
cannot be disabled).

Written at the model layer, not the API's: a signal on every save, delete
and many-to-many change of an edit-logged model, and QuerySet.update(),
bulk_create() and bulk_update() wrapped to write theirs (a QuerySet's
delete() sends each row's signals already, because a receiver listens to
every model). So an admin change, a management command, the shell and a
script leave the same line the API does; `through` says which. Who acted
is whoever the request signed in (apps.core.middleware.Acting), or
nobody for a command or a script, said as such.

Every model of ours is edit-logged unless its class says why not
(`not_edit_logged`, a log of its own: a wrong password, a key's use); an
AuditModel never may (audit_invariants: writes_without_an_event). Nothing
here is ever pruned. The books' own tables are also logged row by row by
the database itself on PostgreSQL (apps.core.ledger_log), which a raw SQL
change cannot go around.
"""

import contextlib
import contextvars
import datetime
import json
import re
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from django.utils import timezone


class EventKind(models.TextChoices):
    CREATED = "created", "Made"
    UPDATED = "updated", "Changed"
    ACTION = "action", "Done"
    MAIL = "mail", "Sent"
    DELETED = "deleted", "Deleted"


class KeptAsWritten(PermissionDenied):
    """A line of the edit log changed or removed: refused, whatever path asked (rule 3(1): the trail is kept)."""


def _refuse(what):
    raise KeptAsWritten(f"The edit log is kept as written: {what} a line of it is refused (Companies (Accounts) Rules "
                        f"2014, rule 3(1)).")


class KeptQuerySet(models.QuerySet):
    """The edit log's rows: added to, read, never changed or removed in bulk either."""

    def update(self, **kwargs):
        _refuse("changing")

    def bulk_update(self, objs, fields, *args, **kwargs):
        _refuse("changing")

    def delete(self):
        _refuse("deleting")

    def _raw_delete(self, using):
        _refuse("deleting")


class RecordEvent(models.Model):
    # Kept eight years and more (Companies Act s.128(5)), never pruned: a kind of record removed from the code
    # keeps its lines, so its content type cannot be deleted under them (PROTECT, not CASCADE).
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    object_id = models.PositiveBigIntegerField()
    at = models.DateTimeField(default=timezone.now, editable=False)
    # PROTECT: a login that has acted is never deleted (only deactivated), so its lines keep naming it.
    who = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                            related_name="+")
    # The login's name as it was, so a login removed later still names who did it.
    who_name = models.CharField(max_length=150, blank=True, default="")
    through = models.CharField(max_length=64, blank=True, default="",
                               help_text="api, admin, web, shell, command:<name>, or script.")
    kind = models.CharField(max_length=8, choices=EventKind.choices)
    action = models.CharField(max_length=64, blank=True, help_text="The action's name: post, void, approve, send.")
    summary = models.CharField(max_length=255, blank=True,
                               help_text="The fields a change touched, or who a mail went to.")
    # {field: {"from": old, "to": new}}, each value exact text: a decimal as its digits, a date as ISO, a key as
    # {"id", "label"}; a secret only {"changed": true}.
    changes = models.JSONField(default=dict, blank=True)

    not_edit_logged = "the edit log itself"

    objects = KeptQuerySet.as_manager()

    class Meta:
        base_manager_name = "objects"
        indexes = [models.Index(fields=["content_type", "object_id", "-at"], name="record_event_by_record"),
                   models.Index(fields=["-at"], name="record_event_by_time")]
        ordering = ["-at", "-pk"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.content_type.model} {self.object_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            _refuse("changing")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _refuse("deleting")


# -- who is acting, and through what ------------------------------------------------------------------------

_ACTING = contextvars.ContextVar("acting", default=None)
# The API action whose handling made a change (accept, post): kept on the model layer's line as its `action`, so
# a change a document's own action made (an order made by accepting a quote) is told from an edit of it.
DOING = contextvars.ContextVar("doing_an_action", default="")


class _Acting:
    def __init__(self, user, through, request):
        self.user, self.through, self.request = user, through, request


@contextlib.contextmanager
def acting_as(user=None, through=None, request=None):
    """Whoever makes the changes inside, through what: a request (its user read when asked), a command."""
    outer = _ACTING.get()
    token = _ACTING.set(_Acting(user, through or (outer.through if outer else "script"), request))
    try:
        yield
    finally:
        _ACTING.reset(token)


def acting():
    """(user or None, through): the signed-in person of the request being answered, or nobody, and how."""
    now = _ACTING.get()
    if now is None:
        return None, "script"
    user = now.user
    if user is None and now.request is not None:
        user = getattr(now.request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False) or not getattr(user, "pk", None):
        return None, now.through
    return user, now.through


def through_for(path):
    """What a request came through, from its path."""
    if path.startswith("/admin"):
        return "admin"
    if path.startswith("/api/"):
        return "api"
    return "web"


def through_command(command):
    """The shell is the shell; any other management command by its name."""
    name = type(command).__module__.rsplit(".", 1)[-1]
    return "shell" if name in ("shell", "shell_plus", "dbshell") else f"command:{name}"


def _stamp(event_kwargs, user):
    user, through = (user, acting()[1]) if user is not None else acting()
    if not getattr(user, "pk", None):
        user = None
    event_kwargs.update(who=user, who_name=user.get_username() if user else "", through=through)
    return event_kwargs


def record(instance, user, kind, action="", summary="", changes=None):
    """One line in the record's history. `instance` may be gone from the database (a delete) but still has its pk."""
    return RecordEvent.objects.create(**_stamp(dict(
        content_type=ContentType.objects.get_for_model(type(instance)), object_id=instance.pk,
        kind=kind, action=action, summary=summary[:255], changes=changes or {}), user))


def record_by_id(model, pk, user, kind, action="", summary=""):
    """The same, for a view that knows the record's model and id and has not loaded it."""
    return RecordEvent.objects.create(**_stamp(dict(
        content_type=ContentType.objects.get_for_model(model), object_id=pk,
        kind=kind, action=action, summary=summary[:255]), user))


# -- which models, and their values as text -----------------------------------------------------------------

# Django's own models, written by every sign-in and by nothing a person decides, or the log itself: not logged.
# A login and a role are (who may do what is the first thing an auditor asks).
LOGGED_OUTSIDE_OUR_APPS = {"auth.User", "auth.Group"}
# A field whose value is never written down, only that it changed: a password's hash, a key's digest, the
# second factor's sealed secret, a PIN's digest. By name, so a secret added later is covered unasked.
SECRET = re.compile(r"password|digest|secret|sealed")
# Kept, but shown only by its last four wherever the log is read: a bank account's number, a PAN, a UAN. Who may
# read the record's history or the edit log is not by that alone who may read where its money goes.
SHOWN_MASKED = re.compile(r"account_number|^pan$|^uan$|aadhaar")
# Stamped on every save, so it says nothing the line's own time does not.
UNLOGGED_FIELDS = {"updated_at"}

_LOGGED = {}


def edit_logged(model):
    """
    Whether every change of `model` is a line in the edit log: every model of ours unless it says why not. A
    migration's historical model (RunPython, apps.get_model) is logged as the model it stands for.
    """
    try:
        return _LOGGED[model]
    except KeyError:
        pass
    concrete = model._meta.concrete_model or model
    if concrete.__module__ == "__fake__":
        from django.apps import apps

        try:
            answer = edit_logged(apps.get_model(concrete._meta.label))
        except LookupError:  # a model since removed: logged as it was, if it was ours
            answer = concrete._meta.app_label not in ("sessions", "admin", "contenttypes", "migrations")
    else:
        answer = (not concrete._meta.auto_created and not getattr(concrete, "not_edit_logged", None)
                  and (concrete.__module__.startswith("apps.") or concrete._meta.label in LOGGED_OUTSIDE_OUR_APPS))
    _LOGGED[model] = answer
    return answer


def logging_now(model, using):
    """
    Whether to write `model`'s line now: always, but for a migration's historical model on a database whose edit
    log is not yet built to this shape (a migration running before core 0043, or a test migrating back past it).
    """
    if not edit_logged(model):
        return False
    if (model._meta.concrete_model or model).__module__ != "__fake__":
        return True
    from django.db import connections

    connection = connections[using]
    with connection.cursor() as cursor:
        try:
            columns = {column.name for column in connection.introspection.get_table_description(
                cursor, RecordEvent._meta.db_table)}
        except Exception:  # noqa: BLE001 - no table yet: the migration that makes it has not run
            return False
    return {"changes", "through", "who_name"} <= columns


def _fields(model, names=None):
    fields = [field for field in model._meta.concrete_fields if field.name not in UNLOGGED_FIELDS]
    if names is not None:
        names = set(names)
        fields = [field for field in fields if field.name in names or field.attname in names]
    return fields


def _secret(field):
    return bool(SECRET.search(field.name))


def _label(field, value, instance=None):
    if instance is not None and field.is_cached(instance):
        related = field.get_cached_value(instance)
        if related is not None and getattr(related, field.target_field.attname, None) == value:
            return str(related)[:200]
    related = field.related_model._base_manager.filter(**{field.target_field.attname: value}).first()
    return str(related)[:200] if related is not None else None


def as_text(field, value, instance=None):
    """A field's value as the log keeps it: exact text, a key with its label."""
    if value is None or value == "" and not field.is_relation:
        return value
    if field.is_relation:
        return {"id": value, "label": _label(field, value, instance)}
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return str(value)
    if isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return "(binary)"
    if hasattr(value, "name") and isinstance(getattr(value, "name"), (str, type(None))):  # a file
        return value.name or None
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return str(value)


def _current(field, instance):
    value = field.value_from_object(instance) if not field.is_relation else getattr(instance, field.attname)
    if hasattr(value, "resolve_expression"):
        return value  # an F() or a function, read back after the write
    if not field.is_relation and value is not None and not isinstance(value, (dict, list)):
        try:
            value = field.to_python(value)
        except Exception:  # noqa: BLE001 - kept as given; the database's own value is what the save wrote
            pass
    if isinstance(value, Decimal) and isinstance(field, models.DecimalField) and value.is_finite():
        value = value.quantize(Decimal(1).scaleb(-field.decimal_places))  # the places the column keeps
    return value


def _same(old, new):
    try:
        return old == new
    except Exception:  # noqa: BLE001
        return False


def _change(field, old, new, instance=None):
    if _secret(field):
        return {"changed": True}
    return {"from": as_text(field, old), "to": as_text(field, new, instance)}


def _diff(fields, old, new, instance=None):
    """{field name: change} for every field `old` and `new` (by attname) hold differently."""
    found = {}
    for field in fields:
        before, after = old.get(field.attname), new.get(field.attname)
        if not _same(before, after):
            found[field.name] = _change(field, before, after, instance)
    return found


def _event(model, pk, kind, changes, summary=None, using="default"):
    return RecordEvent(**_stamp(dict(
        content_type=ContentType.objects.db_manager(using).get_for_model(model, for_concrete_model=True),
        object_id=pk, kind=kind, changes=changes, action=DOING.get()[:64],
        summary=(", ".join(sorted(changes)) if summary is None else summary)[:255]), None))


def _made_with(field, value):
    """Whether a field's value on a record just made says anything: not its own key, its stamp, or nothing."""
    return not field.primary_key and field.name != "created_at" and value not in (None, "", {}, [])


def _write(events, using):
    if events:
        RecordEvent.objects.using(using).bulk_create(events)


# -- the signals ------------------------------------------------------------------------------------------

def before_save(sender, instance, raw=False, using="default", update_fields=None, **kwargs):
    """The row as it stands, before the save writes over it (none: it is being made)."""
    if not logging_now(sender, using):
        return
    instance.__dict__.pop("_edit_log_before", None)
    if instance.pk is None:
        return
    fields = _fields(sender, update_fields)
    instance.__dict__["_edit_log_before"] = sender._base_manager.using(using).filter(pk=instance.pk).values(
        *[field.attname for field in fields]).first()


def after_save(sender, instance, created, raw=False, using="default", update_fields=None, **kwargs):
    """Made, with every value it was made with, or changed, with each field's old and new value."""
    if not logging_now(sender, using):
        return
    before = instance.__dict__.pop("_edit_log_before", None)
    fields = _fields(sender, update_fields)
    new = {field.attname: _current(field, instance) for field in fields}
    if any(hasattr(value, "resolve_expression") for value in new.values()):
        new = sender._base_manager.using(using).filter(pk=instance.pk).values(
            *[field.attname for field in fields]).first() or {}
    if before is None:
        changes = {field.name: _change(field, None, new.get(field.attname), instance) for field in fields
                   if _made_with(field, new.get(field.attname))}
        _write([_event(sender, instance.pk, EventKind.CREATED, changes, summary="", using=using)], using)
        return
    changes = _diff(fields, before, new, instance)
    if changes:
        _write([_event(sender, instance.pk, EventKind.UPDATED, changes, using=using)], using)


def before_delete(sender, instance, using="default", **kwargs):
    if instance.pk is None or not logging_now(sender, using):
        return
    fields = _fields(sender)
    instance.__dict__["_edit_log_gone"] = (str(instance)[:255], {
        field.name: _change(field, getattr(instance, field.attname), None)
        for field in fields if getattr(instance, field.attname) not in (None, "")})


def after_delete(sender, instance, using="default", **kwargs):
    gone = instance.__dict__.pop("_edit_log_gone", None)
    if gone is None or not edit_logged(sender):
        return
    summary, changes = gone
    _write([_event(sender, instance.pk, EventKind.DELETED, changes, summary=summary or "-", using=using)], using)


def _link_name(instance, sender, reverse):
    """The manager on `instance` that the link table `sender` stands behind."""
    for field in type(instance)._meta.get_fields():
        if not field.many_to_many:
            continue
        if field.concrete and not reverse and field.remote_field.through is sender:
            return field.name
        if not field.concrete and reverse and getattr(field, "through", None) is sender:
            return field.get_accessor_name()
    return None


def link_changed(sender, instance, action, reverse, model, pk_set, using="default", **kwargs):
    """A role given or taken, a tag added: the record's links before and after, by label."""
    if action not in ("pre_add", "pre_remove", "pre_clear", "post_add", "post_remove", "post_clear"):
        return
    if not edit_logged(type(instance)):
        return
    name = _link_name(instance, sender, reverse)
    if name is None:
        return
    linked = sorted(str(row)[:200] for row in getattr(instance, name).all())
    if action.startswith("pre_"):
        instance.__dict__.setdefault("_edit_log_links", {})[name] = linked
        return
    before = instance.__dict__.get("_edit_log_links", {}).pop(name, [])
    if before != linked:
        _write([_event(type(instance), instance.pk, EventKind.UPDATED,
                       {name: {"from": before, "to": linked}}, using=using)], using)


# -- the bulk writes ---------------------------------------------------------------------------------------

def logged_update(original):
    """QuerySet.update(): each row's old and new value of what it set, one line a row."""
    def update(self, **kwargs):
        if not kwargs or not logging_now(self.model, self.db):
            return original(self, **kwargs)
        model = self.model
        fields = _fields(model, kwargs)
        attnames = [field.attname for field in fields]
        with transaction.atomic(using=self.db, savepoint=False):
            before = {row.pop("pk"): row for row in self.values("pk", *attnames)}
            # Only the rows read: a row the filter matches only after the read is not written unlogged.
            count = original(self.filter(pk__in=list(before)), **kwargs) if before else 0
            if before and fields:
                after = {row.pop("pk"): row for row in model._base_manager.using(self.db).filter(
                    pk__in=list(before)).values("pk", *attnames)}
                events = []
                for pk in sorted(before):
                    changes = _diff(fields, before[pk], after.get(pk, {}))
                    if changes:
                        events.append(_event(model, pk, EventKind.UPDATED, changes, using=self.db))
                _write(events, self.db)
        return count
    update.edit_logged = True
    return update


def logged_bulk_create(original):
    def bulk_create(self, objs, *args, **kwargs):
        objs = original(self, objs, *args, **kwargs)
        if logging_now(self.model, self.db):
            fields = _fields(self.model)
            _write([_event(self.model, obj.pk, EventKind.CREATED, {
                field.name: _change(field, None, _current(field, obj), obj) for field in fields
                if _made_with(field, _current(field, obj))}, summary="")
                for obj in objs if obj.pk is not None], self.db)
        return objs
    bulk_create.edit_logged = True
    return bulk_create


def logged_bulk_update(original):
    def bulk_update(self, objs, fields, *args, **kwargs):
        if not logging_now(self.model, self.db):
            return original(self, objs, fields, *args, **kwargs)
        objs = list(objs)
        logged = _fields(self.model, fields)
        attnames = [field.attname for field in logged]
        with transaction.atomic(using=self.db, savepoint=False):
            pks = [obj.pk for obj in objs]
            before = {row.pop("pk"): row for row in self.model._base_manager.using(self.db).filter(
                pk__in=pks).values("pk", *attnames)}
            count = original(self, objs, fields, *args, **kwargs)
            after = {row.pop("pk"): row for row in self.model._base_manager.using(self.db).filter(
                pk__in=pks).values("pk", *attnames)}
            _write([_event(self.model, pk, EventKind.UPDATED, changes, using=self.db)
                    for pk in sorted(before) if (changes := _diff(logged, before[pk], after.get(pk, {})))], self.db)
        return count
    bulk_update.edit_logged = True
    return bulk_update


def install():
    """Every save, delete, link and bulk write of an edit-logged model leaves its line (apps.core.apps.ready)."""
    from django.core.management.base import BaseCommand
    from django.db.models import QuerySet
    from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete, pre_save

    pre_save.connect(before_save, weak=False, dispatch_uid="apps.core.history.before_save")
    post_save.connect(after_save, weak=False, dispatch_uid="apps.core.history.after_save")
    pre_delete.connect(before_delete, weak=False, dispatch_uid="apps.core.history.before_delete")
    post_delete.connect(after_delete, weak=False, dispatch_uid="apps.core.history.after_delete")
    m2m_changed.connect(link_changed, weak=False, dispatch_uid="apps.core.history.link_changed")
    for name, wrap in (("update", logged_update), ("bulk_create", logged_bulk_create),
                       ("bulk_update", logged_bulk_update)):
        if not getattr(getattr(QuerySet, name), "edit_logged", False):
            setattr(QuerySet, name, wrap(getattr(QuerySet, name)))
    if not getattr(BaseCommand.execute, "edit_logged", False):
        execute = BaseCommand.execute

        def execute_as_itself(self, *args, **options):
            with acting_as(through=through_command(self)):
                return execute(self, *args, **options)
        execute_as_itself.edit_logged = True
        BaseCommand.execute = execute_as_itself


def installed():
    """Whether nothing has taken the bulk writes' logging off (audit_invariants)."""
    from django.db.models import QuerySet

    return all(getattr(getattr(QuerySet, name), "edit_logged", False) for name in ("update", "bulk_create", "bulk_update"))


def words_for(action):
    """An action's name as the history reads it: post_delivery -> Posted delivery; send -> Sent."""
    head, _, rest = action.partition("_")
    past = {"post": "Posted", "void": "Voided", "send": "Sent", "approve": "Approved", "confirm": "Confirmed",
            "cancel": "Cancelled", "reverse": "Reversed", "close": "Closed", "reopen": "Reopened",
            "accept": "Accepted", "reject": "Rejected", "release": "Released", "receive": "Received",
            "ship": "Shipped", "pay": "Paid", "allocate": "Allocated", "break": "Broken", "load": "Loaded",
            "unload": "Unloaded", "convert": "Converted", "lose": "Lost", "win": "Won", "take": "Taken",
            "quote": "Quoted", "done": "Done", "renew": "Renewed", "settle": "Settled", "withdraw": "Withdrawn",
            "submit": "Submitted", "issue": "Issued", "return": "Returned", "mark": "Marked", "attach": "Attached",
            "detach": "Removed"}
    verb = past.get(head.rstrip("_"), head.replace("_", " ").capitalize())
    return f"{verb} {rest.replace('_', ' ')}".strip()


def changes_for(event):
    """[{field, label, from, to}] for a line, the field's own name as its label."""
    model = event.content_type.model_class()
    rows = []
    for name, change in sorted((event.changes or {}).items()):
        try:
            label = str(model._meta.get_field(name).verbose_name) if model else name
        except Exception:  # noqa: BLE001 - a field since removed keeps its line under its name
            label = name
        if change.get("changed"):
            rows.append({"field": name, "label": label, "from": None, "to": None, "secret": True})
        else:
            shown = _masked if SHOWN_MASKED.search(name) else _shown
            rows.append({"field": name, "label": label, "from": shown(change.get("from")), "to": shown(change.get("to"))})
    return rows


def _masked(value):
    value = _shown(value)
    if value in (None, ""):
        return value
    value = str(value)
    return "•" * max(len(value) - 4, 0) + value[-4:] if len(value) > 4 else "•" * len(value)


def _shown(value):
    if isinstance(value, dict) and "id" in value:
        return value.get("label") or f"#{value['id']}"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return value


def as_row(event):
    return {
        "id": event.pk, "at": event.at, "who": event.who.get_username() if event.who_id else event.who_name,
        "through": event.through, "kind": event.kind,
        "label": words_for(event.action) if event.kind == EventKind.ACTION else event.get_kind_display(),
        "summary": event.summary, "changes": changes_for(event),
    }


def history(model, pk):
    """[{at, who, through, kind, action, summary, changes}] newest first, for the record's page."""
    rows = RecordEvent.objects.filter(
        content_type=ContentType.objects.get_for_model(model), object_id=pk).select_related("who", "content_type")
    return [as_row(event) for event in rows]
