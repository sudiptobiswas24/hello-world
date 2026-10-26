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


def numbering_before_a_write(sender, instance, **kwargs):
    """
    A save of a row that stood, or a delete, locks that row: it comes after the company's numbering row as
    every row lock does (O257, row_lock_query). A password reset saved the user, then locked a row, and held the
    user while it waited for the numbering row. Not a row the transaction made, nor a migration's.
    """
    from .models import DocumentNumbering, DocumentSequence, DocumentSequenceYear, numbering_first

    if sender in (DocumentNumbering, DocumentSequence, DocumentSequenceYear) or sender.__module__ == "__fake__":
        return
    if kwargs.get("raw") or (instance._state.adding and "signal" in kwargs and kwargs["signal"] is pre_save):
        return
    numbering_first()


def numbering_before_a_link(sender, action, **kwargs):
    """A role given or taken (a many-to-many row added or removed) locks its rows, after the numbering row (O257)."""
    if action in ("pre_add", "pre_remove", "pre_clear"):
        from .models import numbering_first

        numbering_first()


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"

    def ready(self):
        from django.conf import settings

        post_migrate.connect(make_the_numbering_row, sender=self, dispatch_uid="apps.core.numbering_row")
        pre_save.connect(numbering_before_a_write, weak=False, dispatch_uid="apps.core.numbering_before_save")
        pre_delete.connect(numbering_before_a_write, weak=False, dispatch_uid="apps.core.numbering_before_delete")
        m2m_changed.connect(numbering_before_a_link, weak=False, dispatch_uid="apps.core.numbering_before_link")

        if getattr(settings, "LOCK_ORDER_SENTINEL", False):
            from apps.core import lock_order

            lock_order.install()
