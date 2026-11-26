from django.apps import AppConfig
from django.db.models.signals import m2m_changed, post_migrate, pre_delete, pre_save
from django.dispatch import Signal

# Sent, with the model as sender, as a bulk update takes the company's numbering row (numbering_before_an_update):
# Django sends no signal of its own for one. What audit_invariants watches a sign-in for, beside pre_save,
# pre_delete and m2m_changed.
bulk_update = Signal()
# And as a select_for_update() takes it (numbering_before_a_lock, O382).
row_lock = Signal()


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


def locked_behind_the_numbering_row(model):
    """
    Whether a lock of `model`'s rows comes after the company's numbering row (O257): every model's but the
    row's own, a migration's model, and one that is not a business record (numbering_exempt). The sequences
    too, though a number takes them behind it already (DocumentSequence.next_value): a sequence edited over the
    API or in the admin is held from read to write (lock_for_change), and that hold took the numbering row first
    in row_lock_query() before the compiler did.
    """
    from .models import DocumentNumbering

    return model is not None and model is not DocumentNumbering and model.__module__ != "__fake__" and \
        not numbering_exempt(model)


def written_behind_the_numbering_row(model):
    """
    Whether a save, a delete or a bulk update of `model`'s rows takes the company's numbering row first: what is
    locked behind it (locked_behind_the_numbering_row), but the sequences, written only behind it (a number
    holds it before its sequence, and an edit's hold before its save).
    """
    from .models import DocumentSequence, DocumentSequenceYear

    return locked_behind_the_numbering_row(model) and model not in (DocumentSequence, DocumentSequenceYear)


def numbering_before_a_write(sender, instance, **kwargs):
    """
    A save of a row that stood, or a delete, locks that row: it comes after the company's numbering row as
    every row lock does (O257, row_lock_query). A password reset saved the user, then locked a row, and held the
    user while it waited for the numbering row. Not a fresh insert (no key yet: a first sign-in does not wait),
    nor a migration's row, nor a model that is not a business record (numbering_exempt). A model built with its
    key and saved runs an UPDATE, so a key set is held, adding or not (review_lockfirst F2).
    """
    from .models import numbering_first

    if kwargs.get("raw") or instance.pk is None or not written_behind_the_numbering_row(sender):
        return
    numbering_first()


def numbering_before_an_update(update):
    """
    QuerySet.update(), wrapped (O384). A bulk update locks the rows it matches as a save of a row that stood does,
    and Django sends no signal for it, so it took no numbering row: a transaction that updated a row so and then
    took a number held that row while it waited for the numbering row, which a posting behind it held while it
    waited for the row, the shape O257 deadlocked on. It comes after the company's numbering row now, as every
    row lock does. A bulk delete sends pre_delete for each row it collects, which numbering_before_a_write
    answers. Not the numbering rows themselves, a migration's model, nor a model that is not a business record
    (numbering_exempt).
    """
    def update_behind_the_numbering_row(self, **kwargs):
        from .models import numbering_first

        if written_behind_the_numbering_row(self.model):
            bulk_update.send(sender=self.model)
            numbering_first()
        return update(self, **kwargs)

    update_behind_the_numbering_row.__wrapped__ = update
    update_behind_the_numbering_row.numbering_first = True
    return update_behind_the_numbering_row


def numbering_before_a_lock(execute_sql):
    """
    SQLCompiler.execute_sql(), wrapped (O382). A select_for_update() of a business record comes after the
    company's numbering row, whoever asks for it and whether or not a row matches. Each lock took it by hand
    before: row_lock_query() for lock_rows() and every helper that locks through it, and a numbering_first()
    written before each select_for_update() of its own. One written without it was seen by the sentinel only
    where a test gave it a row to lock: the job-work challan's period was, once a test had an open period. In
    the plant, where the row stands, such a lock held its row while it waited for the numbering row, which a
    posting behind it held while it waited for the row: the shape O257 deadlocked on. Raw SQL is not compiled
    here; the one raw lock (accounting.money.hold_money_account) holds the numbering row itself.
    """
    def execute_behind_the_numbering_row(self, *args, **kwargs):
        if self.query.select_for_update and locked_behind_the_numbering_row(self.query.model):
            from .models import numbering_first

            row_lock.send(sender=self.query.model)
            numbering_first()
        return execute_sql(self, *args, **kwargs)

    execute_behind_the_numbering_row.__wrapped__ = execute_sql
    execute_behind_the_numbering_row.numbering_first = True
    return execute_behind_the_numbering_row


def holds_the_numbering_row_first(function):
    """
    Whether this QuerySet.update() or SQLCompiler.execute_sql(), or one it wraps (the edit log wraps the first,
    apps.core.history), is ours.
    """
    while function is not None:
        if getattr(function, "numbering_first", False):
            return True
        function = getattr(function, "__wrapped__", None)
    return False


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
        from django.db.models.query import QuerySet
        from django.db.models.sql.compiler import SQLCompiler

        if not holds_the_numbering_row_first(QuerySet.update):
            QuerySet.update = numbering_before_an_update(QuerySet.update)
        # Before the sentinel, which wraps it in turn and so sees the numbering row taken before the rows.
        if not holds_the_numbering_row_first(SQLCompiler.execute_sql):
            SQLCompiler.execute_sql = numbering_before_a_lock(SQLCompiler.execute_sql)

        if getattr(settings, "LOCK_ORDER_SENTINEL", False):
            from apps.core import lock_order

            lock_order.install()

        # The edit log (apps/core/history.py): every save, delete, link and bulk write of a record, at the model
        # layer, so no path through the ORM goes around it; after the sentinel, whose
        # wrappers it wraps. Not a setting: it cannot be switched off.
        from apps.core import history, ledger_log

        history.install()
        ledger_log.install()
        # The application's role given rows to read and write and nothing more, at every migrate (as the owner).
        post_migrate.connect(ledger_log.grant_after_migrate, sender=self, dispatch_uid="apps.core.grant_app_role")
