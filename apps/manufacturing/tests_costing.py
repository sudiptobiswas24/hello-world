"""
What a sack costs, and what happens to the books when that changes.

The fixture's tape BOM makes 100 kg from 75 kg of virgin at 3% input
waste (77.319587 kg gross), 15 of regrind (15.463917), 8 of filler
(8.247422) and 2 of masterbatch (2.061855), and throws off 2.474227 kg
of regrind.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.accounting.models import Account, AccountType, JournalLine
from apps.inventory.costing import CostingMethod

from .costing import CostVersion, StandardCost, against_actual, explain
from .orders import ManufacturingSettings
from .tests_orders import TODAY, RunTestCase


class CostingTestCase(RunTestCase):
    def setUp(self):
        super().setUp()
        self.revaluation = Account.objects.create(
            code="5300", name="Stock revaluation", account_type=AccountType.EXPENSE
        )
        settings = ManufacturingSettings.get()
        settings.revaluation_account = self.revaluation
        settings.save()

    def version(self, code="V1", prices=None):
        version = CostVersion.objects.create(
            code=code, name=f"Version {code}", effective_from=TODAY,
        )
        # Merged over the defaults rather than replacing them: a
        # version missing a price for one filler refuses to roll
        # anything made from it, which is right and is not what these
        # tests are about.
        entered = {
            self.virgin: "100", self.regrind: "60",
            self.filler: "30", self.colour: "200",
        }
        entered.update(prices or {})
        for item, price in entered.items():
            StandardCost.objects.create(
                version=version, item=item, material=Decimal(price),
            )
        return version

    def balance(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True)
        totals = rows.aggregate(debit=Sum("debit"), credit=Sum("credit"))
        return (totals["debit"] or Decimal("0")) - (totals["credit"] or Decimal("0"))


class RollingItUpTests(CostingTestCase):
    def test_a_bought_item_costs_what_the_version_says(self):
        version = self.version()
        self.assertEqual(version.cost_of(self.virgin), Decimal("100"))

    def test_a_made_item_is_the_sum_of_what_goes_in(self):
        """
        77.319587 x 100 + 15.463917 x 60 + 8.247422 x 30 + 2.061855 x
        200 = 7,731.9587 + 927.8351 + 247.4227 + 412.3711 = 9,319.5876
        for a hundred kilos, less the regrind credit.
        """
        version = self.version()
        version.roll_up([self.tape])
        row = version.costs.get(item=self.tape)
        self.assertEqual(row.material, Decimal("93.195876"))

    def test_the_byproduct_carries_value_out_with_it(self):
        version = self.version()
        version.roll_up([self.tape])
        row = version.costs.get(item=self.tape)
        # 2.474227 kg of regrind at its 60 standard is 148.4536 a
        # batch, so 1.484536 a kilo.
        self.assertEqual(row.byproduct_credit, Decimal("1.484536"))
        self.assertEqual(
            row.total, row.material + row.conversion - row.byproduct_credit
        )

    def test_the_three_elements_are_kept_apart(self):
        """
        A sack whose cost doubled is a different problem depending on
        which of them moved.
        """
        version = self.version()
        version.roll_up([self.tape])
        row = version.costs.get(item=self.tape)
        self.assertGreater(row.material, Decimal("0"))
        self.assertEqual(row.conversion, Decimal("0"))
        self.assertTrue(row.is_rolled)
        self.assertEqual(row.bom, self.bom)

    def test_machine_time_is_costed_at_the_work_centres_rates(self):
        from .routing import Routing, RoutingOperation

        self.loom.machine_rate_per_hour = Decimal("600")
        self.loom.labour_rate_per_hour = Decimal("300")
        self.loom.save()
        routing = Routing.objects.create(code="R-EXT", name="Extrude")
        RoutingOperation.objects.create(
            routing=routing, sequence=10, name="Extrude",
            work_centre=self.loom, setup_minutes=Decimal("0"),
            units_per_hour=Decimal("100"), rate_uom=self.kg,
        )
        self.bom.routing = routing
        self.bom.save()
        version = self.version()
        version.roll_up([self.tape])
        row = version.costs.get(item=self.tape)
        # 100 kg at 100 an hour is one hour at 900 an hour: 9 a kilo.
        self.assertEqual(row.conversion, Decimal("9.000000"))

    def test_a_bought_item_with_no_price_anywhere_is_refused(self):
        """
        Everything made from it would be priced as though it were
        free.
        """
        version = CostVersion.objects.create(
            code="V0", name="Empty", effective_from=TODAY
        )
        self.virgin.standard_cost = None
        self.virgin.save()
        with self.assertRaisesMessage(ValidationError, "as though it were free"):
            version.roll_up([self.tape])

    def loop(self):
        """Regrind reprocessed from tape, which is made from regrind."""
        from .bom import BillOfMaterials, BomComponent

        self.regrind.boms.update(is_default=False)
        reprocess = BillOfMaterials.objects.create(
            item=self.regrind, name="Reprocess", version=2,
            quantity_produced=Decimal("100"), uom=self.kg, is_default=True,
        )
        BomComponent.objects.create(
            bom=reprocess, item=self.tape, quantity=Decimal("100"),
            uom=self.kg, line_number=1,
        )
        return reprocess

    def test_a_true_loop_with_no_stated_price_is_refused(self):
        """
        A made from B made from A has no standard cost to compute: it
        is a simultaneous equation, and a system that pretends
        otherwise is quietly using a stale number for one side.
        """
        self.loop()
        version = self.version()
        with self.assertRaisesMessage(ValidationError, "simultaneous equation"):
            version.roll_up([self.tape])

    def test_a_stated_price_breaks_the_loop(self):
        """
        Which is what a plant does when it decides reground waste is
        worth sixty whatever it came out of.
        """
        self.loop()
        version = self.version()
        StandardCost.objects.create(
            version=version, item=self.tape, material=Decimal("120"),
        )
        version.roll_up([self.regrind])
        self.assertIsNotNone(version.cost_of(self.regrind))


class PublishingIsARevaluationTests(CostingTestCase):
    def standard_item(self):
        self.virgin.costing_method = CostingMethod.STANDARD
        self.virgin.standard_cost = Decimal("100")
        self.virgin.save()
        return self.virgin

    def test_a_standard_change_posts_the_difference(self):
        """
        The hole this module was written to close. Stock at standard
        is worth quantity times the standard, so changing the standard
        changed every shelf in the company — silently, with no journal
        entry, and nothing in the system disagreed.
        """
        item = self.standard_item()
        # 2,000 kg on the shelf at 100 becomes 2,000 at 110.
        version = self.version(prices={item: "110"})
        entry = version.publish(on_date=TODAY)
        self.assertIsNotNone(entry)
        self.assertEqual(self.balance(self.inventory), Decimal("20000.00"))
        self.assertEqual(self.balance(self.revaluation), Decimal("-20000.00"))

    def test_the_item_carries_the_new_standard_afterwards(self):
        item = self.standard_item()
        self.version(prices={item: "110"}).publish(on_date=TODAY)
        item.refresh_from_db()
        self.assertEqual(item.standard_cost, Decimal("110"))
        self.assertEqual(item.stock_value_at(self.plant), Decimal("220000.00"))

    def test_a_fall_in_the_standard_posts_the_other_way(self):
        item = self.standard_item()
        self.version(prices={item: "90"}).publish(on_date=TODAY)
        self.assertEqual(self.balance(self.inventory), Decimal("-20000.00"))
        self.assertEqual(self.balance(self.revaluation), Decimal("20000.00"))

    def test_an_item_on_weighted_average_is_not_revalued(self):
        """
        It is valued from its own movements, so a standard change
        means nothing to what it is worth.
        """
        self.assertEqual(self.virgin.costing_method, CostingMethod.AVERAGE)
        version = self.version(prices={self.virgin: "110"})
        entry = version.publish(on_date=TODAY)
        self.assertIsNone(entry)
        self.virgin.refresh_from_db()
        self.assertEqual(self.virgin.standard_cost, Decimal("110"))

    def test_an_unchanged_standard_posts_nothing(self):
        item = self.standard_item()
        self.assertIsNone(self.version(prices={item: "100"}).publish(on_date=TODAY))

    def test_an_empty_shelf_posts_nothing(self):
        item = self.standard_item()
        self.stock(item, "-2000", "100")
        self.assertIsNone(self.version(prices={item: "110"}).publish(on_date=TODAY))

    def test_publishing_with_nowhere_to_put_the_difference_is_refused(self):
        item = self.standard_item()
        settings = ManufacturingSettings.get()
        settings.revaluation_account = None
        settings.save()
        with self.assertRaisesMessage(ValidationError, "has to land somewhere"):
            self.version(prices={item: "110"}).publish(on_date=TODAY)

    def test_publishing_twice_is_refused(self):
        item = self.standard_item()
        version = self.version(prices={item: "110"})
        version.publish(on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "already published"):
            version.publish(on_date=TODAY)

    def test_an_empty_version_is_refused(self):
        version = CostVersion.objects.create(
            code="V0", name="Empty", effective_from=TODAY
        )
        with self.assertRaisesMessage(ValidationError, "every standard to nothing"):
            version.publish(on_date=TODAY)

    def test_a_published_version_cannot_be_rolled_again(self):
        item = self.standard_item()
        version = self.version(prices={item: "110"})
        version.publish(on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "roll up a new version"):
            version.roll_up([self.tape])

    def test_the_revaluation_is_one_entry_for_the_whole_publication(self):
        """
        A revaluation is a single act with a single date. A hundred
        entries for a hundred items is a hundred things to reconcile
        and one thing to understand.
        """
        # Both at standard, and both with a standard to move from —
        # the fixture leaves polymer with none, and from nothing to a
        # hundred and ten is the whole shelf, not the difference.
        self.standard_item()
        self.regrind.costing_method = CostingMethod.STANDARD
        self.regrind.save()
        version = self.version(prices={self.virgin: "110", self.regrind: "70"})
        entry = version.publish(on_date=TODAY)
        self.assertTrue(entry.posted)
        # Two lines, not three: both items sit in the same inventory
        # account, and the helper aggregates by account before it
        # writes. 2,000 kg of polymer up ten and 500 of regrind up ten
        # is 25,000 against one account, and the balancing side.
        self.assertEqual(entry.lines.count(), 2)
        self.assertEqual(self.balance(self.inventory), Decimal("25000.00"))
        self.assertEqual(self.balance(self.revaluation), Decimal("-25000.00"))


class WhyDoesItCostThatTests(CostingTestCase):
    def test_it_breaks_the_cost_into_what_it_is_made_of(self):
        version = self.version()
        version.roll_up([self.tape])
        report = explain(version, self.tape)
        self.assertFalse(report["bought"])
        skus = [line["item"].sku for line in report["lines"]]
        # Dearest first: polymer is three quarters of a kilo of tape.
        self.assertEqual(skus[0], "PP-RAFFIA")
        self.assertGreater(report["lines"][0]["share_percent"], Decimal("70"))

    def test_a_bought_item_has_nothing_to_break_down(self):
        version = self.version()
        report = explain(version, self.virgin)
        self.assertTrue(report["bought"])
        self.assertEqual(report["lines"], [])

    def test_the_shares_of_one_level_come_to_the_whole(self):
        version = self.version()
        version.roll_up([self.tape])
        report = explain(version, self.tape)
        total = sum(line["cost_per_unit"] for line in report["lines"])
        # Each line is stated at six places and so is the total, so
        # they can differ by a millionth of a rupee. Anything more
        # than that is a line missing.
        self.assertLess(abs(total - report["material"]), Decimal("0.00001"))


class HasTheStandardGoneStaleTests(CostingTestCase):
    def test_it_compares_the_standard_with_the_shelf(self):
        """
        A plant whose polymer standard is ninety-four and whose
        average is a hundred and nine has been reporting a favourable
        variance on every run for months and calling it good buying.
        """
        version = self.version(prices={self.virgin: "80"})
        row = against_actual(version)[0]
        self.assertEqual(row["item"], self.virgin)
        self.assertEqual(row["standard"], Decimal("80"))
        self.assertEqual(row["actual"], Decimal("100.0000"))
        self.assertEqual(row["difference"], Decimal("20.0000"))
        self.assertEqual(row["difference_percent"], Decimal("25.00"))

    def test_the_worst_drift_is_listed_first(self):
        version = self.version(prices={
            self.virgin: "99", self.filler: "10",
        })
        rows = against_actual(version)
        self.assertEqual(rows[0]["item"], self.filler)


class ThroughTheDoorTests(CostingTestCase):
    def run_it(self, *args):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("roll_costs", *args, stdout=out)
        return out.getvalue()

    def test_the_command_rolls_and_prints(self):
        self.version()
        report = self.run_it("V1")
        self.assertIn("rolled", report)
        self.assertIn("TAPE-1000", report)

    def test_it_can_explain_one_item(self):
        self.version()
        report = self.run_it("V1", "--explain", "TAPE-1000")
        self.assertIn("TAPE-1000 costs", report)
        self.assertIn("PP-RAFFIA", report)

    def test_it_can_show_the_drift(self):
        self.version(prices={self.virgin: "80"})
        report = self.run_it("V1", "--drift")
        self.assertIn("worst first", report)
        self.assertIn("PP-RAFFIA", report)

    def test_an_unknown_version_is_refused(self):
        from django.core.management import CommandError

        with self.assertRaises(CommandError):
            self.run_it("NOPE")

    def test_the_api_rolls_publishes_and_explains(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        user = get_user_model().objects.create_superuser(
            username="cost", email="c@example.com", password="x"
        )
        client = APIClient()
        client.force_authenticate(user)
        version = self.version()
        rolled = client.post(
            f"/api/manufacturing/cost-versions/{version.pk}/roll_up/"
        )
        self.assertEqual(rolled.status_code, 200)
        self.assertGreater(rolled.data["rolled"], 0)
        why = client.get(
            f"/api/manufacturing/cost-versions/{version.pk}/explain/"
            f"?item={self.tape.pk}"
        )
        self.assertEqual(why.data["sku"], "TAPE-1000")
        self.assertTrue(why.data["lines"])
        published = client.post(
            f"/api/manufacturing/cost-versions/{version.pk}/publish/",
            {"on_date": str(TODAY)},
        )
        self.assertEqual(published.status_code, 200)
        self.assertTrue(published.data["is_published"])

    def test_the_api_refuses_a_second_publish_as_an_answer(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        user = get_user_model().objects.create_superuser(
            username="cost2", email="c2@example.com", password="x"
        )
        client = APIClient()
        client.force_authenticate(user)
        version = self.version()
        url = f"/api/manufacturing/cost-versions/{version.pk}/publish/"
        client.post(url, {"on_date": str(TODAY)})
        again = client.post(url, {"on_date": str(TODAY)})
        self.assertEqual(again.status_code, 400)
        self.assertIn("already published", str(again.data))
