"""
What the trail costs: a 200-employee pay run's post and a 500-line invoice's post, with the trail on and with
it off, three times each (tag "timing": run by hand, not in the lanes). `trail_off()` is for measuring only:
the product has no switch.
"""

import contextlib

from django.db import connection


@contextlib.contextmanager
def trail_off():
    """The application layer's receivers and wrappers, the actor stamp and (PostgreSQL) every table's trigger, off."""
    from django.db.models import QuerySet
    from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete, pre_save

    from apps.core import history, ledger_log

    tables = ledger_log.product_tables(connection) if connection.vendor == "postgresql" else []
    with connection.cursor() as cursor:
        if tables:
            # A test's transaction holds deferred key checks; ALTER TABLE waits for none, so they are run first.
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        for table in tables:
            cursor.execute(f"ALTER TABLE {table} DISABLE TRIGGER {ledger_log.TRIGGER}")
    pairs = [(pre_save, history.before_save), (post_save, history.after_save), (pre_delete, history.before_delete),
             (post_delete, history.after_delete), (m2m_changed, history.link_changed)]
    wrapped = {name: getattr(QuerySet, name) for name in ("update", "bulk_create", "bulk_update")}
    for signal, receiver in pairs:
        signal.disconnect(receiver, dispatch_uid=f"apps.core.history.{receiver.__name__}")
    for name, wrapper in wrapped.items():
        setattr(QuerySet, name, wrapper.__wrapped__)
    stamps = [w for w in connection.execute_wrappers if w is ledger_log.stamp_actor]
    connection.execute_wrappers[:] = [w for w in connection.execute_wrappers if w is not ledger_log.stamp_actor]
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            if tables:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            for table in tables:
                cursor.execute(f"ALTER TABLE {table} ENABLE TRIGGER {ledger_log.TRIGGER}")
        for name, wrapper in wrapped.items():
            setattr(QuerySet, name, wrapper)
        for signal, receiver in pairs:
            signal.connect(receiver, weak=False, dispatch_uid=f"apps.core.history.{receiver.__name__}")
        connection.execute_wrappers[:0] = stamps
