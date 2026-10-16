"""g22 review_purch: the data migrations of fix_purch on old rows, with MigrationExecutor."""

import contextlib
import datetime
import io
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag

D = datetime.date
Z = Decimal


@tag("migration")
class FixPurchMigrationProbes(TransactionTestCase):
    def migrate(self, *targets):
        executor = MigrationExecutor(connection)
        executor.migrate(list(targets))
        return MigrationExecutor(connection).loader.project_state(list(targets)).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_01_every_old_tds_rate_becomes_one_unconfirmed_row_from_2000_with_its_figures(self):
        old = self.migrate(("accounting", "0029_tcs_section_by_date"))
        Section = old.get_model("accounting", "TdsSection")
        Section.objects.create(code="194C", name="C", mode="whole", rate_percent=Z("1.5"), no_pan_rate_percent=Z("20"))
        Section.objects.create(code="194Q", name="Q", mode="excess", annual_threshold=Z("5000000"),
                               rate_percent=Z("0.1"), no_pan_rate_percent=Z("5"))
        new = self.migrate(("accounting", "0030_tds_rates_by_date"))
        Rate = new.get_model("accounting", "TdsRate")
        rows = [(r.section.code, r.applies_from, r.rate_percent, r.no_pan_rate_percent, r.confirmed_on)
                for r in Rate.objects.select_related("section").order_by("section__code")]
        self.assertEqual(rows, [("194C", D(2000, 4, 1), Z("1.5"), Z("20"), None),
                                ("194Q", D(2000, 4, 1), Z("0.1"), Z("5"), None)])

    def test_02_old_vendor_invoices_are_keyed_earliest_first_and_the_report_names_the_rest(self):
        old = self.migrate(("purchasing", "0075_order_approved_figures"))
        Party, Account = old.get_model("core", "Party"), old.get_model("accounting", "Account")
        Bill = old.get_model("purchasing", "Bill")
        vendor = Party.objects.create(code="V-1", name="Supplier")
        other = Party.objects.create(code="V-2", name="Other")
        payable = Account.objects.create(code="2000", name="AP", account_type="liability")

        def bill(party, reference, day):
            return Bill.objects.create(vendor=party, reference=reference, bill_date=day, payable_account=payable)

        first = bill(vendor, "INV-77", D(2026, 6, 10))
        second = bill(vendor, "inv 77", D(2026, 6, 12))
        next_year = bill(vendor, "Inv-77", D(2027, 4, 1))
        earlier_year = bill(vendor, "INV.77", D(2026, 3, 31))
        elsewhere = bill(other, "INV-77", D(2026, 6, 10))
        blank = bill(vendor, "", D(2026, 6, 10))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            new = self.migrate(("purchasing", "0076_a_vendor_invoice_is_one_in_its_year"))
        Bill = new.get_model("purchasing", "Bill")
        keys = {b.pk: b.reference_key for b in Bill.objects.all()}
        self.assertEqual(keys[first.pk], "2026:INV77")
        self.assertEqual(keys[second.pk], "", "the later duplicate is left unkeyed")
        self.assertEqual((keys[next_year.pk], keys[earlier_year.pk], keys[elsewhere.pk], keys[blank.pk]),
                         ("2027:INV77", "2025:INV77", "2026:INV77", ""))
        self.assertIn("inv 77", out.getvalue())
        self.assertIn("left unkeyed", out.getvalue())
