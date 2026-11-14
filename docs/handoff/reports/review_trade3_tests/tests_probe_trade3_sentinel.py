"""Review probes (trade3): what the lock-order sentinel does and does not see. Each prints its finding."""
from django.db import connection, transaction
from django.test import TestCase

from .lock_order import LockOrderViolation, _state
from .models import Party, lock_rows, row_lock_query


def verdict(call):
    try:
        call()
    except LockOrderViolation as exc:
        return f"RAISED ({exc})"
    return "not seen"


class SentinelReach(TestCase):
    def setUp(self):
        self.a, self.b = (Party.objects.create(code=f"S{n}", name=f"S{n}") for n in (1, 2))

    def test_S1_no_key_lock_of_the_mixin_is_seen(self):
        def go():
            with transaction.atomic():
                list(row_lock_query(Party, self.b.pk, no_key=True))
                list(row_lock_query(Party, self.a.pk, no_key=True))
        print("\nS1 lock_for_change-style FOR NO KEY UPDATE, key order reversed:", verdict(go))

    def test_S2_iterator_form_is_seen(self):
        def go():
            with transaction.atomic():
                list(Party.objects.select_for_update().filter(pk=self.b.pk).iterator())
                list(Party.objects.select_for_update().filter(pk=self.a.pk).iterator())
        print("\nS2 select_for_update().iterator():", verdict(go))

    def test_S3_values_list_without_pk_loses_the_key_order(self):
        def go():
            with transaction.atomic():
                list(Party.objects.select_for_update().filter(pk=self.b.pk).values_list("code", flat=True))
                list(Party.objects.select_for_update().filter(pk=self.a.pk).values_list("code", flat=True))
        print("\nS3 same two parties locked in reverse key order through values_list('code'):", verdict(go))

    def test_S4_update_and_save_take_a_lock_the_sentinel_never_records(self):
        def go():
            with transaction.atomic():
                lock_rows(self.b)
                Party.objects.filter(pk=self.a.pk).update(name="x")  # row lock of a, after b
                self.a.name = "y"
                self.a.save()
                held = _state(connection)["held"]
                print("   held after update()/save() of a:", sorted(held))
        print("\nS4 update()/save() of a lower-keyed party after locking a higher one:", verdict(go))

    def test_S5_select_related_locks_the_joined_table_unrecorded(self):
        from apps.sales.models import SalesOrder
        def go():
            with transaction.atomic():
                lock_rows(self.b)  # core.party, rank 4
                # base model SalesOrderLine is rank 3 so it WOULD be seen; take a base model that ranks above
                # and join to a lower-ranked one: Invoice (8) joined to SalesOrder (1)
                from apps.sales.models import Invoice
                qs = Invoice.objects.select_related("sales_order").select_for_update()
                print("   sql has join:", "sales_salesorder" in str(qs.query), "| for_update:", qs.query.select_for_update)
                list(qs)  # no rows: nothing locked here; the point is the compiler sees one model
        print("\nS5 select_related + select_for_update (joined order not ranked):", verdict(go))

    def test_S6_rolled_back_savepoint_keeps_its_rows_held(self):
        def go():
            with transaction.atomic():
                try:
                    with transaction.atomic():
                        lock_rows(self.b)
                        raise ValueError("savepoint rolled back; PostgreSQL released that row")
                except ValueError:
                    pass
                lock_rows(self.a)
        print("\nS6 lock b in a savepoint that rolls back, then a (legal on PostgreSQL):", verdict(go))
