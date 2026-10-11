"""
A thousand kilos of tape sold and made on one run. The full blend posts,
each line to the paisa, 77,319.59 + 9,278.35 + 2,474.23 + 4,123.72 =
93,195.89, so a kilo cost 93.19589. 800 shipped and 100,000.00 billed:
800 x 93.19589 = 74,556.712 of cost, a margin of 25,443.288, 25.44%,
at a realised 125.00 a kilo.
"""

import datetime
from decimal import Decimal
from unittest.mock import patch

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.sales.models import SalesOrder, SalesOrderLine

from .profitability import line_profitability
from .tests_orders import TODAY, RunTestCase


class ProfitTestCase(RunTestCase):
    def setUp(self):
        super().setUp()
        self.customer = Party.objects.create(code="CEM", name="Deccan Cement")
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)
        self.sale = SalesOrder.objects.create(customer=self.customer,
                                              order_date=datetime.date(2026, 5, 1),
                                              currency=self.usd)
        self.line = SalesOrderLine.objects.create(order=self.sale, item=self.tape, uom=self.kg,
                                                  quantity=Decimal("1000"),
                                                  unit_price=Decimal("125"))

    def made_for_the_line(self, byproducts=()):
        run = self.order("1000")
        run.sales_order_line = self.line
        run.save()
        run.release(TODAY)
        self.full_issue(run).post()
        self.produce(run, "1000", byproducts=byproducts).post()
        return run


class WhatItCostTests(ProfitTestCase):
    def test_what_the_runs_for_it_consumed_over_what_they_made(self):
        self.made_for_the_line()
        found = line_profitability(self.line)
        self.assertEqual((found["runs"], found["made"]), (1, Decimal("1000.0000")))
        self.assertEqual(found["actual"]["direct"], Decimal("93.19589"))
        self.assertEqual(found["actual"]["conversion"], Decimal("0"))
        self.assertFalse(found["final"])

    def test_less_what_came_back(self):
        run = self.made_for_the_line(byproducts=[(self.regrind, "24.7423")])
        row = run.entries.get().byproducts.get()
        credit = row.unit_value * Decimal("24.7423")
        self.assertGreater(credit, 0)
        found = line_profitability(self.line)
        self.assertAlmostEqual(found["actual"]["direct"],
                               (Decimal("93195.89") - credit) / 1000, places=6)

    def test_a_cancelled_run_is_not_the_lines(self):
        run = self.order("1000")
        run.sales_order_line = self.line
        run.save()
        run.release(TODAY)
        run.cancel()
        found = line_profitability(self.line)
        self.assertEqual((found["runs"], found["actual"], found["margin"]), (0, None, None))

    def test_final_once_every_run_is_closed(self):
        run = self.made_for_the_line()
        run.close(TODAY)
        self.assertTrue(line_profitability(self.line)["final"])


class WhatItMadeTests(ProfitTestCase):
    def test_margin_on_what_shipped_at_what_it_cost(self):
        self.made_for_the_line()
        with patch.object(SalesOrderLine, "quantity_shipped", return_value=Decimal("800")), \
                patch.object(SalesOrderLine, "revenue_in_base",
                             return_value=Decimal("100000.00")):
            found = line_profitability(self.line)
        self.assertEqual(found["cost_of_shipped"], Decimal("74556.712"))
        self.assertEqual(found["margin"], Decimal("25443.288"))
        self.assertEqual(found["margin_percent"].quantize(Decimal("0.01")), Decimal("25.44"))
        self.assertEqual(found["realised_price"], Decimal("125"))

    def test_nothing_shipped_is_no_price(self):
        self.made_for_the_line()
        found = line_profitability(self.line)
        self.assertEqual((found["shipped"], found["realised_price"], found["margin"]),
                         (Decimal("0"), None, Decimal("0.00")))
        self.assertIsNone(found["margin_percent"])

    def test_over_the_api(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        self.made_for_the_line()
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("owner"))
        with patch.object(SalesOrderLine, "quantity_shipped", return_value=Decimal("800")), \
                patch.object(SalesOrderLine, "revenue_in_base",
                             return_value=Decimal("100000.00")):
            (row,) = client.get(f"/api/manufacturing/order-profitability/?order={self.sale.pk}"
                                ).json()
        self.assertEqual((row["actual"]["direct"], row["margin"], row["margin_percent"],
                          row["quoted"]),
                         ("93.1959", "25443.29", "25.44", None))
        self.assertEqual(client.get("/api/manufacturing/order-profitability/").status_code, 400)


class TheEdgesTests(ProfitTestCase):
    """Found by mutation."""

    def test_a_withdrawn_entry_gives_nothing_back(self):
        run = self.order("1000")
        run.sales_order_line = self.line
        run.save()
        run.release(TODAY)
        self.full_issue(run).post()
        first = self.produce(run, "500", byproducts=[(self.regrind, "24.7423")])
        first.post()
        self.produce(run, "500").post()
        first.void()
        found = line_profitability(self.line)
        self.assertEqual((found["made"], found["actual"]["credit"]),
                         (Decimal("500.0000"), Decimal("0")))

    def test_vendors_work_is_conversion(self):
        from .orders import WorkOrder

        self.made_for_the_line()
        with patch.object(WorkOrder, "outside_cost", return_value=Decimal("1000")):
            found = line_profitability(self.line)
        self.assertEqual(found["actual"]["conversion"], Decimal("1"))
        self.assertEqual(found["actual"]["direct"], Decimal("94.19589"))
