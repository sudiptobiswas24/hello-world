"""
A season's order of 100, the contract ending on day 90. Called off: 25
on day 10, 25 on day 20, 20 on day 30 — 70 called, 30 not yet.

Shipped 30: the day-10 call-off is met and 5 of day 20's, so 20 is owed
on day 20, 20 on day 30 and the uncalled 30 on day 90. Shipped 75:
every call-off met and 5 of the uncalled, so 25 on day 90.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.planning.mrp import sales_demand

from .call_offs import CallOff, called_off, open_schedule
from .models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine
from .tests_base import SalesTestCase

DAY = datetime.date(2026, 3, 1)


def day(n):
    return DAY + datetime.timedelta(days=n)


class CallOffTestCase(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.sale = SalesOrder.objects.create(customer=self.customer, order_date=DAY,
                                              currency=self.usd)
        self.line = SalesOrderLine.objects.create(
            order=self.sale, item=self.item, uom=self.uom, quantity=Decimal("100"),
            unit_price=Decimal("10"), revenue_account=self.revenue, warehouse=self.warehouse,
            delivery_date=day(90))
        self.sale.confirm()
        for n, quantity in ((10, "25"), (20, "25"), (30, "20")):
            CallOff.objects.create(line=self.line, due_on=day(n), quantity=Decimal(quantity))

    def ship(self, quantity):
        delivery = Delivery.objects.create(sales_order=self.sale, delivery_date=DAY)
        DeliveryLine.objects.create(delivery=delivery, order_line=self.line,
                                    warehouse=self.warehouse,
                                    quantity_shipped=Decimal(quantity))
        delivery.post()
        return delivery


class WhatIsOwedAndWhenTests(CallOffTestCase):
    def test_shipments_meet_the_oldest_call_off_first(self):
        self.ship("30")
        self.assertEqual(open_schedule(self.line),
                         [(day(20), Decimal("20")), (day(30), Decimal("20")),
                          (day(90), Decimal("30"))])

    def test_past_every_call_off_into_the_uncalled_rest(self):
        self.ship("75")
        self.assertEqual(open_schedule(self.line), [(day(90), Decimal("25"))])

    def test_nothing_shipped(self):
        self.assertEqual(open_schedule(self.line),
                         [(day(10), Decimal("25")), (day(20), Decimal("25")),
                          (day(30), Decimal("20")), (day(90), Decimal("30"))])
        self.assertEqual(called_off(self.line), Decimal("70"))

    def test_a_line_nobody_called_off_is_owed_on_its_date(self):
        self.line.call_offs.all().delete()
        self.assertEqual(open_schedule(self.line), [(day(90), Decimal("100"))])

    def test_fully_called_off_leaves_no_rest(self):
        CallOff.objects.create(line=self.line, due_on=day(40), quantity=Decimal("30"))
        self.assertEqual(open_schedule(self.line)[-1], (day(40), Decimal("30")))

    def test_closed_short_owes_nothing(self):
        self.ship("30")
        self.line.close_short("Season over")
        self.assertEqual(open_schedule(self.line), [])

    def test_a_return_puts_the_call_off_back(self):
        delivery = self.ship("30")
        delivery.create_return(credit_invoices=False)
        self.assertEqual(open_schedule(self.line)[0], (day(10), Decimal("25")))


class NeverMoreThanTheLineTests(CallOffTestCase):
    def test_a_call_off_past_the_line(self):
        with self.assertRaisesMessage(ValidationError, "so 31 more is 1 over"):
            CallOff.objects.create(line=self.line, due_on=day(40), quantity=Decimal("31"))

    def test_raising_one_past_the_line(self):
        call = self.line.call_offs.get(due_on=day(30))
        call.quantity = Decimal("51")
        with self.assertRaisesMessage(ValidationError, "is 1 over"):
            call.save()
        call.quantity = Decimal("50")
        call.save()

    def test_a_charge_has_nothing_to_call_off(self):
        from apps.accounting.models import ChargeType

        freight = SalesOrderLine.objects.create(
            order=self.sale, charge=ChargeType.objects.create(
                code="FRT", name="Freight", revenue_account=self.revenue),
            quantity=Decimal("1"), unit_price=Decimal("5"))
        with self.assertRaisesMessage(ValidationError, "is a charge"):
            CallOff.objects.create(line=freight, due_on=day(10), quantity=Decimal("1"))

    def test_the_line_cut_below_its_call_offs(self):
        self.line.quantity = Decimal("69")
        with self.assertRaisesMessage(ValidationError, "has 70 called off"):
            self.line.save()
        self.line.quantity = Decimal("70")
        self.line.save()


class PlannedOnTheCallOffsTests(CallOffTestCase):
    def test_demand_on_each_day_owed(self):
        self.ship("30")
        rows = [(row.date, row.quantity) for row in sales_demand(self.item, self.warehouse, DAY)]
        self.assertEqual(rows, [(day(20), Decimal("20")), (day(30), Decimal("20")),
                                (day(90), Decimal("30"))])


class CallOffApiTests(CallOffTestCase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_superuser("scheduler"))

    def test_called_off_and_scheduled(self):
        response = self.client.post("/api/sales/call-offs/", {
            "line": self.line.pk, "due_on": str(day(40)), "quantity": "10",
            "reference": "CO-7"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        body = self.client.get(f"/api/sales/sales-order-lines/{self.line.pk}/schedule/").json()
        self.assertEqual(body["called_off"], "80.0000")
        self.assertEqual([row["quantity"] for row in body["open"]],
                         ["25.0000", "25.0000", "20.0000", "10.0000", "20.0000"])
        response = self.client.post("/api/sales/call-offs/", {
            "line": self.line.pk, "due_on": str(day(50)), "quantity": "21"}, format="json")
        self.assertEqual(response.status_code, 400)
        rows = self.client.get(f"/api/sales/call-offs/?line={self.line.pk}").json()
        self.assertEqual(len(rows), 4)
