from django.apps import AppConfig
from django.db.models.signals import post_migrate


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


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"

    def ready(self):
        from django.conf import settings

        post_migrate.connect(make_the_numbering_row, sender=self, dispatch_uid="apps.core.numbering_row")

        if getattr(settings, "LOCK_ORDER_SENTINEL", False):
            from apps.core import lock_order

            lock_order.install()
