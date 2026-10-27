"""
F2 cutover: a reverse-charge tax with no account its credit waits in. A fresh installation, an existing one
migrated, the go-live check, and the bill it would refuse.
"""

import importlib
from decimal import Decimal as D

from django.apps import apps as live_apps
from django.core.exceptions import ValidationError
from django.core.management import call_command

from apps.accounting.models import Account, Tax
from apps.imports.management.commands.go_live_check import checks

from .tests_rcm_cash import ReverseChargeCashTestCase

cutover = importlib.import_module("apps.accounting.migrations.0033_reverse_charge_waiting_account_cutover")


class WaitingAccountCutoverTests(ReverseChargeCashTestCase):
    def without(self, *taxes):
        Tax.objects.filter(pk__in=[tax.pk for tax in taxes]).update(reverse_charge_waiting_account=None)

    def test_a_bill_is_refused_naming_the_tax_and_who_sets_it(self):
        self.without(self.igst5)
        with self.assertRaisesMessage(ValidationError, "Tax IGST5RC names no account its reverse-charge credit "
                                                       "waits in"):
            self.freight_bill()
        with self.assertRaisesMessage(ValidationError, "The Controller sets it in accounting setup"):
            self.freight_bill()

    def test_the_go_live_check_fails_while_one_lacks_it(self):
        self.assertEqual([row for row in checks() if row[1] == "Reverse charge: credit waiting account"], [])
        self.without(self.igst5, self.cgst25)
        [(level, _, detail)] = [row for row in checks() if row[1] == "Reverse charge: credit waiting account"]
        self.assertEqual(level, "FAIL")
        self.assertIn("CGST25RC, IGST5RC", detail)

    def test_the_migration_sets_it_where_the_account_exists_and_names_the_rest(self):
        self.without(self.igst5)
        said = []
        self.assertEqual(cutover.set_waiting_account(live_apps, None, out=said.append), ([], ["IGST5RC"]))
        self.assertIn("IGST5RC", said[0])
        waiting = Account.objects.create(code="1319", name="Reverse-charge credit awaiting payment",
                                         account_type="asset")
        self.assertEqual(cutover.set_waiting_account(live_apps, None, out=said.append), (["IGST5RC"], []))
        self.assertEqual(Tax.objects.get(pk=self.igst5.pk).reverse_charge_waiting_account, waiting)
        self.assertEqual(self.freight_bill().total(), D("10000.00"))

    def test_setup_indian_books_makes_the_account_and_sets_it(self):
        self.without(self.igst5)
        call_command("setup_indian_books", stdout=open("/dev/null", "w"))
        self.assertEqual(Tax.objects.get(pk=self.igst5.pk).reverse_charge_waiting_account.code, "1319")
        self.assertEqual(Tax.objects.get(pk=self.cgst25.pk).reverse_charge_waiting_account, self.waiting)
