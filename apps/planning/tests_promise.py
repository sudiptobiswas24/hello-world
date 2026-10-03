"""
Answering the telephone.

"Fifty thousand sacks by the twentieth: yes or no." Every piece of
this existed and nothing put them together.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.manufacturing.orders import WorkOrder
from apps.purchasing.models import PurchaseOrder, PurchaseOrderLine

from .promise import (
    available_to_promise,
    capable_to_promise,
    when_can_we_promise,
)
from .tests_base import TODAY
from .tests_mrp import PlanningTestCase


class PromiseTestCase(PlanningTestCase):
    def purchase(self, item, quantity, expected):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=TODAY, currency=self.inr,
        )
        PurchaseOrderLine.objects.create(
            order=order, item=item, uom=self.kg, quantity=Decimal(quantity),
            unit_price=Decimal("90"), expected_date=expected,
        )
        order.confirm()
        return order

    def atp(self, item=None, **kwargs):
        kwargs.setdefault("planned_on", TODAY)
        return available_to_promise(item or self.fabric, self.plant, **kwargs)

    def ask(self, quantity, item=None, **kwargs):
        kwargs.setdefault("planned_on", TODAY)
        return when_can_we_promise(
            item or self.fabric, self.plant, Decimal(quantity), **kwargs
        )


class WhatIsUncommittedTests(PromiseTestCase):
    def test_an_empty_shelf_promises_nothing(self):
        # One period, for today, holding nothing. A ladder that said
        # nothing at all would not distinguish "no stock" from "not
        # asked".
        self.assertEqual(
            [(row["date"], row["promisable"]) for row in self.atp()],
            [(TODAY, Decimal("0"))],
        )
        self.assertIsNone(self.ask("1"))

    def test_stock_with_nothing_owed_is_all_promisable(self):
        self.stock(self.fabric, "1000")
        ladder = self.atp()
        self.assertEqual(ladder[0]["date"], TODAY)
        self.assertEqual(ladder[0]["promisable"], Decimal("1000"))
        self.assertEqual(self.ask("1000"), TODAY)
        self.assertIsNone(self.ask("1001"))

    def test_stock_already_promised_is_not_promisable_again(self):
        """
        The whole point. A rep who reads on-hand and not the order
        book sells the same fabric twice.
        """
        self.stock(self.fabric, "1000")
        self.sell(self.fabric, "800", self.day(10))
        self.assertEqual(self.ask("200"), TODAY)
        self.assertIsNone(self.ask("201"))

    def test_a_receipt_makes_more_promisable_from_its_own_date(self):
        self.stock(self.fabric, "100")
        self.purchase(self.fabric, "900", self.day(20))
        self.assertEqual(self.ask("100"), TODAY)
        self.assertEqual(self.ask("1000"), self.day(20))

    def test_yesterdays_spare_cannot_cover_todays_shortage(self):
        """
        A running total that dipped below nought and came back would
        promise the same sack twice. It is floored.
        """
        self.stock(self.fabric, "100")
        self.sell(self.fabric, "500", self.day(5))
        self.purchase(self.fabric, "300", self.day(10))
        ladder = {row["date"]: row for row in self.atp()}
        # The 500 owed on day 5 falls in today's window, because
        # nothing arrives between now and then: today is 400 short.
        self.assertEqual(ladder[TODAY]["owed"], Decimal("500"))
        self.assertEqual(ladder[TODAY]["uncommitted"], Decimal("-400"))
        self.assertEqual(ladder[TODAY]["promisable"], Decimal("0"))
        # The 300 landing on day 10 does not cure a shortage that
        # happened on day 5, so there is still nothing to give away.
        self.assertEqual(ladder[self.day(10)]["promisable"], Decimal("0"))

    def test_a_later_shortage_eats_into_what_is_promisable_now(self):
        """
        Stock travels forwards in time, so a period that comes out
        short borrows from the ones before it.

        A thousand on the shelf, a hundred landing on day ten and five
        hundred owed on day twelve: the day-ten period is four hundred
        short, which has to come off what today may give away. Told
        otherwise a rep promises the whole thousand and the day-twelve
        customer goes without.
        """
        self.stock(self.fabric, "1000")
        self.purchase(self.fabric, "100", self.day(10))
        self.sell(self.fabric, "500", self.day(12))
        ladder = {row["date"]: row for row in self.atp()}
        self.assertEqual(ladder[TODAY]["uncommitted"], Decimal("600"))
        self.assertEqual(ladder[TODAY]["promisable"], Decimal("600"))
        self.assertEqual(self.ask("600"), TODAY)
        self.assertIsNone(self.ask("601"))

    def test_an_open_run_is_promisable_when_it_finishes(self):
        self.stock(self.virgin, "5000")
        self.stock(self.regrind, "5000")
        WorkOrder.objects.create(
            item=self.tape, bom=self.tape_bom, quantity_ordered=Decimal("1000"),
            uom=self.kg, warehouse=self.plant, scheduled_start=TODAY,
            scheduled_end=self.day(5),
        )
        self.assertEqual(self.ask("1000", item=self.tape), self.day(5))

    def test_nothing_past_the_horizon_is_promised(self):
        self.purchase(self.fabric, "1000", self.day(200))
        self.assertIsNone(self.ask("1000", horizon_days=90))


class WhenCouldWeMakeItTests(PromiseTestCase):
    def promise(self, quantity, item=None, **kwargs):
        kwargs.setdefault("planned_on", TODAY)
        return capable_to_promise(
            item or self.fabric, self.plant, Decimal(quantity), **kwargs
        )

    def test_stock_answers_first_and_says_so(self):
        self.stock(self.fabric, "1000")
        answer = self.promise("1000")
        self.assertEqual(answer["date"], TODAY)
        self.assertEqual(answer["source"], "stock")

    def test_a_bought_item_answers_with_its_lead_time(self):
        answer = self.promise("500", item=self.virgin)
        self.assertEqual(answer["source"], "purchase")
        self.assertEqual(answer["lead_days"], 7)
        self.assertEqual(answer["date"], self.day(7))

    def test_a_made_item_answers_with_a_run(self):
        """
        Machines and materials both: the polymer takes a week to buy
        and the loom then needs a day, so nothing lands sooner.
        """
        answer = self.promise("1000")
        self.assertEqual(answer["source"], "run")
        self.assertEqual(answer["bottleneck"], self.loom)
        self.assertIsNotNone(answer["date"])
        self.assertGreaterEqual(answer["date"], answer["material_ready"])
        self.assertGreaterEqual(answer["date"], answer["starts_on"])

    def test_material_on_the_shelf_brings_the_date_forward(self):
        with_nothing = self.promise("1000")["date"]
        self.stock(self.tape, "5000")
        with_tape = self.promise("1000")["date"]
        self.assertLess(with_tape, with_nothing)

    def test_a_busy_loom_pushes_the_date_out(self):
        self.stock(self.tape, "50000")
        free = self.promise("1000")["date"]
        self.loom.available_hours_per_day = Decimal("1")
        self.loom.save()
        busy = self.promise("1000")["date"]
        self.assertGreater(busy, free)

    def test_a_quantity_the_plant_cannot_reach_answers_nothing(self):
        self.stock(self.tape, "5000000")
        self.loom.available_hours_per_day = Decimal("1")
        self.loom.save()
        answer = self.promise("5000000", horizon_days=30)
        self.assertIsNone(answer["date"])
        self.assertIn("cannot fit it", answer["note"])

    def test_a_bom_with_no_routing_falls_back_to_the_default(self):
        self.fabric_bom.routing = None
        self.fabric_bom.save()
        self.stock(self.tape, "5000")
        answer = self.promise("1000")
        self.assertEqual(answer["source"], "run")
        self.assertIsNone(answer["bottleneck"])

    def test_no_routing_and_no_default_refuses_rather_than_guessing(self):
        self.fabric_bom.routing = None
        self.fabric_bom.save()
        self.settings.default_make_lead_days = None
        self.settings.save()
        self.stock(self.tape, "5000")
        with self.assertRaisesMessage(ValidationError, "could be ready"):
            self.promise("1000")


class AQuotationReservesNothingTests(PromiseTestCase):
    def test_two_enquiries_the_same_morning_get_the_same_answer(self):
        """
        Stated rather than discovered. The cure is to confirm the
        order, which does reserve stock, not to have the enquiry
        pretend it did.
        """
        self.stock(self.tape, "50000")
        first = capable_to_promise(
            self.fabric, self.plant, Decimal("1000"), planned_on=TODAY
        )
        second = capable_to_promise(
            self.fabric, self.plant, Decimal("1000"), planned_on=TODAY
        )
        self.assertEqual(first["date"], second["date"])

    def test_confirming_the_first_order_moves_the_second_answer(self):
        self.stock(self.fabric, "1000")
        self.assertEqual(self.ask("1000"), TODAY)
        self.sell(self.fabric, "1000", self.day(5))
        self.assertIsNone(self.ask("1000"))


class ThroughTheDoorTests(PromiseTestCase):
    def run_it(self, *args):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command(*args, stdout=out)
        return out.getvalue()

    def test_the_command_answers_yes_from_stock(self):
        self.stock(self.fabric, "1000")
        report = self.run_it(
            "can_we_promise", "FAB-10X10", "1000", "--warehouse", "P",
            "--on", str(TODAY),
        )
        self.assertIn("yes, from 2026-06-01", report)

    def test_the_command_answers_with_a_run_and_says_nothing_is_held(self):
        self.stock(self.tape, "5000")
        report = self.run_it(
            "can_we_promise", "FAB-10X10", "1000", "--warehouse", "P",
            "--on", str(TODAY),
        )
        self.assertIn("not from stock", report)
        self.assertIn("Nothing here is reserved", report)

    def test_the_ladder_can_be_shown(self):
        self.stock(self.fabric, "1000")
        self.sell(self.fabric, "800", self.day(10))
        report = self.run_it(
            "can_we_promise", "FAB-10X10", "100", "--warehouse", "P",
            "--on", str(TODAY), "--ladder",
        )
        self.assertIn("Uncommitted FAB-10X10", report)
        self.assertIn("promisable", report)

    def test_an_unknown_item_is_refused(self):
        from django.core.management import CommandError

        with self.assertRaises(CommandError):
            self.run_it(
                "can_we_promise", "NOPE", "1", "--warehouse", "P"
            )

    def test_the_api_answers_the_ladder_and_the_question(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        user = get_user_model().objects.create_superuser(
            username="rep", email="r@example.com", password="x"
        )
        client = APIClient()
        client.force_authenticate(user)
        self.stock(self.fabric, "1000")
        self.sell(self.fabric, "800", self.day(10))
        base = f"?item={self.fabric.pk}&warehouse={self.plant.pk}&on={TODAY}"
        ladder = client.get(f"/api/planning/promise/{base}")
        self.assertEqual(ladder.status_code, 200)
        self.assertEqual(Decimal(str(ladder.data[0]["promisable"])), Decimal("200"))
        answer = client.get(f"/api/planning/promise/when/{base}&quantity=200")
        self.assertEqual(answer.data["source"], "stock")
        self.assertEqual(str(answer.data["date"]), str(TODAY))

    def test_the_api_refuses_a_question_with_no_quantity(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        user = get_user_model().objects.create_superuser(
            username="rep2", email="r2@example.com", password="x"
        )
        client = APIClient()
        client.force_authenticate(user)
        response = client.get(
            f"/api/planning/promise/when/?item={self.fabric.pk}"
            f"&warehouse={self.plant.pk}"
        )
        self.assertEqual(response.status_code, 400)
