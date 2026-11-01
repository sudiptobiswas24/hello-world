"""
O403: a fresh plant had no countries and no stock adjustment reasons, so no
export customer could be given a country and no adjustment or count's
difference could be booked. setup_indian_books adds the countries and the two
stock accounts; setup_stores the reasons against them.
"""

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.accounting.models import Account, AccountType, SeededDefault
from apps.core.countries import COUNTRIES
from apps.core.models import Country
from apps.inventory.adjustments import AdjustmentReason


def run(command):
    out = StringIO()
    call_command(command, stdout=out)
    return out.getvalue()


class SetupStoresTests(TestCase):
    def test_the_books_then_the_stores_give_a_new_plant_its_countries_and_reasons(self):
        said = run("setup_indian_books")
        self.assertEqual(Country.objects.count(), len(COUNTRIES))
        self.assertEqual(Country.objects.get(code="IN").name, "India")
        self.assertIn("setup_stores", said)
        run("setup_stores")
        reasons = {reason.code: (reason.account.code, reason.direction) for reason in AdjustmentReason.objects.all()}
        self.assertEqual(reasons, {"DAMAGE": ("5300", "decrease"), "SHORT": ("5300", "decrease"),
                                   "FOUND": ("4200", "increase")})
        self.assertEqual(SeededDefault.objects.filter(model="inventory.adjustmentreason").count(), 3)

    def test_run_again_nothing_is_made_twice_nor_changed(self):
        run("setup_indian_books")
        run("setup_stores")
        Country.objects.filter(code="AE").update(name="UAE")
        AdjustmentReason.objects.filter(code="SHORT").update(name="Shortage")
        run("setup_indian_books")
        said = run("setup_stores")
        self.assertEqual(Country.objects.count(), len(COUNTRIES))
        self.assertEqual(Country.objects.get(code="AE").name, "UAE")
        self.assertEqual(AdjustmentReason.objects.count(), 3)
        self.assertEqual(AdjustmentReason.objects.get(code="SHORT").name, "Shortage")
        self.assertIn("0 seeded", said)

    def test_a_plant_s_own_5300_is_not_where_stock_is_written_off(self):
        """The demo's 5300 is power and fuel: the books seed the stock account beside it, and the reasons find that."""
        Account.objects.create(code="5300", name="Power and fuel", account_type=AccountType.EXPENSE)
        run("setup_indian_books")
        run("setup_stores")
        self.assertEqual(AdjustmentReason.objects.get(code="SHORT").account.code, "5300-IN")
        self.assertEqual(AdjustmentReason.objects.get(code="SHORT").account.name, "Stock written off")

    def test_without_the_books_it_refuses_and_says_which_command_comes_first(self):
        with self.assertRaisesMessage(CommandError, "run setup_indian_books first"):
            run("setup_stores")
        self.assertFalse(AdjustmentReason.objects.exists())
