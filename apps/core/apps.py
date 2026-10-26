from django.apps import AppConfig
from django.db.models.signals import m2m_changed, post_migrate, pre_delete, pre_save


def make_the_numbering_row(sender, using="default", **kwargs):
    """
    The company's numbering row stands before any number is asked for (O257). Made by a first number
    instead, two first numbers at once each insert it, each waits on the other's insert, and one ends
    as a deadlock: a database flushed and migrated again (a test's) would meet that on its first race.
    """
    from django.db import DatabaseError, transaction

    from .models import DocumentNumbering

    try:
        with transaction.atomic(using=using):
            DocumentNumbering.objects.using(using).get_or_create(pk=DocumentNumbering.ROW)
    except DatabaseError:
        pass  # migrating to before the table existed: its migration makes the row


def numbering_exempt(model):
    """
    Whether `model` is not a business record and takes no numbering row (O257): it says so with a
    `numbering_exempt` reason. Sign-in, sign-out and a session's refresh write such rows; held behind the
    numbering row, signing out waited the whole of a pay run (review_lockfirst F1). A model that does not say so
    is held.
    """
    return bool(getattr(model, "numbering_exempt", None))


def numbering_before_a_write(sender, instance, **kwargs):
    """
    A save of a row that stood, or a delete, locks that row: it comes after the company's numbering row as
    every row lock does (O257, row_lock_query). A password reset saved the user, then locked a row, and held the
    user while it waited for the numbering row. Not a fresh insert (no key yet: a first sign-in does not wait),
    nor a migration's row, nor a model that is not a business record (numbering_exempt). A model built with its
    key and saved runs an UPDATE, so a key set is held, adding or not (review_lockfirst F2).
    """
    from .models import DocumentNumbering, DocumentSequence, DocumentSequenceYear, numbering_first

    if sender in (DocumentNumbering, DocumentSequence, DocumentSequenceYear) or sender.__module__ == "__fake__":
        return
    if kwargs.get("raw") or numbering_exempt(sender) or instance.pk is None:
        return
    numbering_first()


def numbering_before_a_link(sender, action, **kwargs):
    """A many-to-many row added or removed locks its rows, after the numbering row (O257), unless exempt."""
    if action in ("pre_add", "pre_remove", "pre_clear") and not numbering_exempt(sender):
        from .models import numbering_first

        numbering_first()


# Django's own infrastructure, which a sign-in, a sign-out and a session's refresh write: marked here because
# their classes are not ours to edit (O257, review_lockfirst F1). Each of our models says it on its class.
DJANGO_INFRASTRUCTURE = {
    "sessions.Session": "a session, saved and deleted by every sign-in, sign-out and refresh",
    "admin.LogEntry": "the admin's own log of what it did",
    "contenttypes.ContentType": "Django's table of models",
    "auth.Permission": "Django's table of rights",
    "auth.Group": "a role, given and read at sign-in",
    "auth.User": "a login, stamped at sign-in (last_login) and by a password change",
}


def mark_django_infrastructure():
    from django.apps import apps

    for label, reason in DJANGO_INFRASTRUCTURE.items():
        try:
            model = apps.get_model(label)
        except LookupError:
            continue
        model.numbering_exempt = reason
        for field in model._meta.many_to_many:
            field.remote_field.through.numbering_exempt = f"{label}'s {field.name}: {reason}"


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"

    def ready(self):
        from django.conf import settings

        post_migrate.connect(make_the_numbering_row, sender=self, dispatch_uid="apps.core.numbering_row")
        mark_django_infrastructure()
        pre_save.connect(numbering_before_a_write, weak=False, dispatch_uid="apps.core.numbering_before_save")
        pre_delete.connect(numbering_before_a_write, weak=False, dispatch_uid="apps.core.numbering_before_delete")
        m2m_changed.connect(numbering_before_a_link, weak=False, dispatch_uid="apps.core.numbering_before_link")

        if getattr(settings, "LOCK_ORDER_SENTINEL", False):
            from apps.core import lock_order

            lock_order.install()

        # The edit log (apps/core/history.py): every save, delete, link and bulk write of a record, at the model
        # layer, so no path through the ORM goes around it; after the sentinel, whose
        # wrappers it wraps. Not a setting: it cannot be switched off.
        from apps.core import history, ledger_log

        history.install()
        ledger_log.install()
