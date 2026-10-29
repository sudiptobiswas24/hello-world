"""
Review probes for fix_core's O136 (g22 review_core): migration 0077 reports every clash by name, merges nothing,
lets NULLs stand, leaves the database as it was when it refuses, and reverses.
"""
import datetime

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag

from apps.core.models import Party, PartyRole, PartyRoleAssignment


@tag("migration")
class OneOpportunityAnOrderMigrationProbe(TransactionTestCase):
    before = [("sales", "0076_debit_note")]
    after = [("sales", "0077_one_opportunity_an_order")]

    def test_every_clash_is_named_nothing_is_merged_nulls_stand_and_it_reverses(self):
        from .crm import Opportunity
        from .models import SalesOrder

        MigrationExecutor(connection).migrate(self.before)
        try:
            customer = Party.objects.create(code="C-OPP", name="Bengal Cement")
            PartyRoleAssignment.objects.create(party=customer, role=PartyRole.CUSTOMER)
            day = datetime.date(2026, 9, 1)
            first, second, third, fourth = (SalesOrder.objects.create(customer=customer, order_date=day)
                                            for _ in range(4))
            make = lambda title, order=None: Opportunity.objects.create(  # noqa: E731
                customer=customer, title=title, sales_order=order) if order is None else Opportunity.objects.create(
                customer=customer, title=title)
            rows = {name: make(name) for name in ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot",
                                                  "golf", "hotel")}
            link = lambda order, *names: Opportunity.objects.filter(  # noqa: E731
                pk__in=[rows[n].pk for n in names]).update(sales_order=order)
            link(first, "alpha", "bravo", "charlie")     # three on one order
            link(second, "delta", "echo")                # two on another
            link(third, "foxtrot")                       # one on one: fine
            # golf and hotel stay NULL: many opportunities with no order are fine.
            with self.assertRaises(RuntimeError) as caught:
                MigrationExecutor(connection).migrate(self.after)
            said = str(caught.exception)
            for name in ("alpha", "bravo", "charlie", "delta", "echo"):
                self.assertIn(name, said)
            for name in ("foxtrot", "golf", "hotel"):
                self.assertNotIn(name, said)
            self.assertIn(str(first.number or first.pk), said)
            self.assertIn(str(second.number or second.pk), said)
            self.assertNotIn(f"sales order {third.number or third.pk}:", said)
            # Not merged, not changed.
            self.assertEqual(sorted(Opportunity.objects.filter(sales_order=first).values_list("title", flat=True)),
                             ["alpha", "bravo", "charlie"])
            self.assertEqual(Opportunity.objects.filter(sales_order__isnull=True).count(), 2)
            # Not half-applied: the recorded state is still 0076.
            applied = MigrationExecutor(connection).loader.applied_migrations
            self.assertNotIn(self.after[0], applied)
            # A person unlinks all but one of each; then it goes through.
            link(None, "bravo", "charlie", "echo")
            MigrationExecutor(connection).migrate(self.after)
            with self.assertRaises(IntegrityError), transaction.atomic():
                link(first, "bravo")
            # Reversed: the constraint is gone and the clash may stand again.
            MigrationExecutor(connection).migrate(self.before)
            link(first, "bravo")
            self.assertEqual(Opportunity.objects.filter(sales_order=first).count(), 2)
        finally:
            # Unlinked before going forward again, or the migration refuses the clash this test left standing.
            Opportunity.objects.filter(sales_order__isnull=False).update(sales_order=None)
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
