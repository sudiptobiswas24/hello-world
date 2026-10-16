"""
A customer's material rules, checked on the recipe when the order is
confirmed and on the batches when it ships.

The trace fixture's tape is 75% virgin, 15% regrind, 8% filler and 2%
masterbatch, and its run throws regrind off: regrind is recovered waste
wherever it turns up again.
"""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.sales.models import (
    CustomerProfile, Delivery, DeliveryLine, SalesOrder, SalesOrderLine,
)

from apps.inventory.models import Lot

from .bom import BillOfMaterials, BomComponent
from .orders import IssueDirection, MaterialIssue, MaterialIssueLine
from .compliance import material_problems
from .tests_orders import TODAY
from .tests_trace import TraceTestCase
from .tests_woven import WovenTestCase


class OrderedTape(TraceTestCase):
    def rules(self, **values):
        CustomerProfile.objects.update_or_create(party=self.customer, defaults=values)

    def sales_order(self, quantity="600", day=15):
        order = SalesOrder.objects.create(customer=self.customer, currency=self.usd,
                                          order_date=datetime.date(2026, 9, day))
        SalesOrderLine.objects.create(order=order, item=self.tape, uom=self.kg,
                                      warehouse=self.plant, quantity=Decimal(quantity),
                                      unit_price=Decimal("120"))
        return order

    def deliver(self, order):
        delivery = Delivery.objects.create(sales_order=order, delivery_date=order.order_date)
        DeliveryLine.objects.create(delivery=delivery, order_line=order.lines.get(),
                                    warehouse=self.plant, lot=self.tape_lot,
                                    quantity_shipped=order.lines.get().quantity)
        delivery.post()
        return delivery

    def refused(self, message, call, *args):
        with self.assertRaises(ValidationError) as caught:
            call(*args)
        self.assertIn(message, str(caught.exception))
        return str(caught.exception)


class VirginOnlyTests(OrderedTape):
    def test_a_recipe_with_regrind_is_not_confirmed(self):
        self.rules(virgin_only=True)
        order = self.sales_order()
        self.refused("uses REGRIND, which is recovered waste", order.confirm)
        order.refresh_from_db()
        self.assertEqual(order.number, "")

    def test_a_clean_recipe_still_ships_nothing_made_with_regrind(self):
        self.a_run()  # this run drew regrind batch RG-1 into TAPE-A
        BomComponent.objects.filter(item=self.regrind).delete()
        self.rules(virgin_only=True)
        order = self.sales_order()
        order.confirm()
        message = self.refused("was made by", self.deliver, order)
        self.assertIn("REGRIND batch RG-1, which is recovered waste", message)
        self.assertFalse(Delivery.objects.filter(posted=True).exists())

    def test_a_rule_added_after_the_order_stops_the_shipment(self):
        self.a_run()
        order = self.sales_order()
        order.confirm()
        self.rules(virgin_only=True)
        self.refused("recovered waste", self.deliver, order)

    def test_a_customer_without_rules_is_not_asked(self):
        self.a_run()
        order = self.sales_order()
        order.confirm()
        self.assertTrue(self.deliver(order).posted)

    def test_regrind_two_batches_back_is_found(self):
        self.a_run()  # TAPE-A drew regrind batch RG-1
        BomComponent.objects.filter(item=self.regrind).delete()
        second = self.order("100")
        second.release(TODAY)
        self.issue_with_lots(second, [(self.tape, "50", self.tape_lot)]).post()
        later = Lot.objects.create(item=self.tape, code="TAPE-B")
        self.produce(second, "50", lot=later).post()
        problems = material_problems(CustomerProfile(virgin_only=True), self.tape, [later],
                                     TODAY)
        self.assertTrue(any("REGRIND batch RG-1" in problem for problem in problems), problems)

    def test_regrind_drawn_and_put_back_was_not_used(self):
        BomComponent.objects.filter(item=self.regrind).delete()
        run = self.order("100")
        run.release(TODAY)
        drawn = self.issue_with_lots(run, [(self.regrind, "20", self.grind)])
        drawn.post()
        back = MaterialIssue.objects.create(work_order=run, direction=IssueDirection.RETURN,
                                            issue_date=TODAY, warehouse=self.plant)
        MaterialIssueLine.objects.create(issue=back, item=self.regrind, quantity=Decimal("20"),
                                         uom=self.kg, lot=self.grind, line_number=1,
                                         returns_line=drawn.lines.get())
        back.post()
        made = Lot.objects.create(item=self.tape, code="TAPE-C")
        self.produce(run, "10", lot=made).post()
        self.assertEqual(material_problems(CustomerProfile(virgin_only=True), self.tape,
                                           [made], TODAY), [])

    def test_regrind_itself_is_not_sold_as_virgin(self):
        self.rules(virgin_only=True)
        order = SalesOrder.objects.create(customer=self.customer, currency=self.usd,
                                          order_date=datetime.date(2026, 9, 15))
        SalesOrderLine.objects.create(order=order, item=self.regrind, uom=self.kg,
                                      warehouse=self.plant, quantity=Decimal("10"),
                                      unit_price=Decimal("60"))
        self.refused("is itself recovered waste", order.confirm)

    def test_a_profile_without_material_rules_asks_nothing(self):
        self.rules(credit_limit=Decimal("1000000"))
        with patch("apps.sales.models.MATERIAL_CHECKERS", []):
            self.sales_order().confirm()

    def test_something_made_here_drawn_without_a_batch_cannot_be_shown(self):
        BomComponent.objects.filter(item=self.regrind).delete()
        compound = BillOfMaterials.objects.create(item=self.filler, quantity_produced=Decimal("1"),
                                                  uom=self.kg)
        BomComponent.objects.create(bom=compound, item=self.virgin, quantity=Decimal("1"),
                                    uom=self.kg)
        run = self.order("100")
        run.release(TODAY)
        self.stock(self.filler, "100", "30")
        self.issue_with_lots(run, [(self.filler, "8", None)]).post()
        made = Lot.objects.create(item=self.tape, code="TAPE-E")
        self.produce(run, "10", lot=made).post()
        problems = material_problems(CustomerProfile(virgin_only=True), self.tape, [made], TODAY)
        self.assertTrue(any("drawn without a batch" in problem for problem in problems), problems)

    def test_rules_nothing_can_check_are_refused_not_passed(self):
        self.rules(virgin_only=True)
        with patch("apps.sales.models.MATERIAL_CHECKERS", []):
            self.refused("nothing here can check", self.sales_order().confirm)


class FillerAndUvWithoutATapeTests(OrderedTape):
    def test_a_recipe_that_states_no_filler_cannot_show_it(self):
        self.rules(max_filler_percent=Decimal("5"))
        self.refused("states its tape's filler or UV", self.sales_order().confirm)


class FillerAndUvTests(WovenTestCase):
    def profile(self, **values):
        return CustomerProfile(**values)

    def test_filler_over_the_limit_and_uv_under_it(self):
        self.bag()
        problems = material_problems(
            self.profile(max_filler_percent=Decimal("5"), min_uv_percent=Decimal("0.5")),
            self.bag_item, [], datetime.date.today())
        self.assertEqual(len(problems), 2)
        self.assertIn("filler; the most allowed is 5%", problems[0])
        self.assertIn("UV stabiliser; at least 0.5% is required", problems[1])

    def test_within_both(self):
        self.bag()
        problems = material_problems(
            self.profile(max_filler_percent=Decimal("8"), min_uv_percent=Decimal("0")),
            self.bag_item, [], datetime.date.today())
        self.assertEqual(problems, [])

    def test_the_bag_recipe_reaches_its_tape(self):
        self.bag()
        problems = material_problems(self.profile(virgin_only=True), self.bag_item, [],
                                     datetime.date.today())
        self.assertTrue(any("recipe uses REGRIND" in problem for problem in problems))

    def test_rules_are_percentages(self):
        from apps.core.models import Party

        party = Party.objects.create(code="P", name="P")
        with self.assertRaises(IntegrityError), transaction.atomic():
            CustomerProfile.objects.create(party=party, max_filler_percent=Decimal("101"))
