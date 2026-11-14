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
                                   "FOUND": ("4200", "increase"), "OPENING": ("3900", "increase")})
        self.assertEqual(SeededDefault.objects.filter(model="inventory.adjustmentreason").count(), 4)

    def test_run_again_nothing_is_made_twice_nor_changed(self):
        run("setup_indian_books")
        run("setup_stores")
        Country.objects.filter(code="AE").update(name="UAE")
        AdjustmentReason.objects.filter(code="SHORT").update(name="Shortage")
        run("setup_indian_books")
        said = run("setup_stores")
        self.assertEqual(Country.objects.count(), len(COUNTRIES))
        self.assertEqual(Country.objects.get(code="AE").name, "UAE")
        self.assertEqual(AdjustmentReason.objects.count(), 4)  # DAMAGE, SHORT, FOUND, OPENING: none twice
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


class AFreshPlantHasItsUnitsTests(TestCase):
    """No unit existed on a fresh install, so no item could be made or imported (2 November)."""

    def test_the_units_with_their_gst_codes_and_conversions_and_a_second_run_adds_none(self):
        from decimal import Decimal

        from apps.core.models import UnitOfMeasure
        from apps.gst.models import UnitQuantityCode

        call_command("setup_indian_books", stdout=StringIO())
        UnitOfMeasure.objects.create(code="bag", name="Sack", category="count")  # the plant's own, kept
        out = StringIO()
        call_command("setup_stores", stdout=out)
        self.assertEqual(sorted(UnitOfMeasure.objects.values_list("code", flat=True)),
                         sorted(["kg", "g", "t", "nos", "pcs", "bag", "bale", "roll", "m", "L"]))
        self.assertEqual(UnitOfMeasure.objects.get(code="bag").name, "Sack")
        self.assertIn("kept as it was: unit bag", out.getvalue())
        tonne = UnitOfMeasure.objects.get(code="t")
        self.assertEqual((tonne.base_unit.code, tonne.conversion_factor), ("kg", Decimal("1000")))
        self.assertEqual(dict(UnitQuantityCode.objects.values_list("uom__code", "code")), {
            "kg": "KGS", "g": "GMS", "t": "TON", "nos": "NOS", "pcs": "PCS", "bale": "BAL", "roll": "ROL",
            "m": "MTR", "L": "LTR"})
        # The opening position's stock posts with nothing more made by hand (docs/IMPORT.md).
        opening = AdjustmentReason.objects.get(code="OPENING")
        self.assertEqual((opening.account.code, opening.direction), ("3900", "increase"))
        again = StringIO()
        call_command("setup_stores", stdout=again)
        self.assertIn("0 seeded for the accountant to review", again.getvalue())



class TheOpeningReasonFollowsTheBooksTests(TestCase):
    """
    O542: setup_stores pointed OPENING at 3900 by code while the books' setting held the account the
    plant opened against; a plant opened against another account was refused its opening stock.
    """

    def setUp(self):
        import datetime

        from apps.accounting.models import BooksOpening

        run("setup_indian_books")
        self.capital = Account.objects.create(code="3100", name="Proprietor's capital", account_type=AccountType.EQUITY)
        self.seeded = Account.objects.get(code="3900")
        self.day = datetime.date(2026, 10, 1)
        self.BooksOpening = BooksOpening

    def reason(self):
        return AdjustmentReason.objects.get(code="OPENING")

    def test_the_stores_set_up_after_the_books_take_the_account_they_opened_against(self):
        self.BooksOpening.objects.create(opened_on=self.day, opening_account=self.capital)
        said = run("setup_stores")
        self.assertEqual((self.reason().account.code, self.reason().follows_books_opening), ("3100", True))
        self.assertIn("adjustment reason OPENING Opening stock -> 3100, following the books' opening account", said)
        self.assertEqual({reason.code: reason.follows_books_opening for reason in AdjustmentReason.objects.all()},
                         {"DAMAGE": False, "SHORT": False, "FOUND": False, "OPENING": True})

    def test_the_stores_set_up_first_follow_the_books_when_they_open_and_when_the_account_is_changed(self):
        run("setup_stores")
        self.assertEqual((self.reason().account.code, self.reason().follows_books_opening), ("3900", True))
        books = self.BooksOpening.objects.create(opened_on=self.day, opening_account=self.capital)
        self.assertEqual(self.reason().account.code, "3100")
        books.opening_account = self.seeded  # the edit path: nothing is posted yet, so accounting allows it
        books.save()
        self.assertEqual(self.reason().account.code, "3900")
        # The reasons that do not follow are where they were.
        self.assertEqual(AdjustmentReason.objects.get(code="FOUND").account.code, "4200")
        books.delete()  # the delete path: the reason keeps the account it had
        self.assertEqual((self.reason().account.code, self.reason().follows_books_opening), ("3900", True))

    def test_a_following_reason_takes_no_other_account_and_one_unticked_is_left_alone(self):
        from django.core.exceptions import ValidationError

        run("setup_stores")
        books = self.BooksOpening.objects.create(opened_on=self.day, opening_account=self.capital)
        reason = self.reason()
        reason.account = self.seeded
        with self.assertRaises(ValidationError) as refused:
            reason.save()
        self.assertEqual(refused.exception.message_dict, {"account": [
            "OPENING posts to the books' opening account, which is 3100 Proprietor's capital (Settings, Books "
            "opening), not 3900. Choose that account, or untick \"posts to the books' opening account\" to post "
            "this reason elsewhere."]})
        with self.assertRaises(ValidationError):
            reason.clean()  # the admin's form asks the same question
        reason.follows_books_opening = False  # the reverse: unticked, it is any reason
        reason.save()
        books.opening_account = self.seeded
        books.save()
        books.opening_account = self.capital
        books.save()
        self.assertEqual(self.reason().account.code, "3900")
        reason.follows_books_opening = True  # ticked again with its account as it stood: it takes the books'
        reason.save()
        self.assertEqual(self.reason().account.code, "3100")
