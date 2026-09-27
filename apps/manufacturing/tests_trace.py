"""
A batch traced forward: which runs ate it, what they made, and who was
shipped any of it. The recall, which is the complaint's mirror.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from rest_framework.test import APIClient

from apps.core.models import PartyRole, PartyRoleAssignment
from apps.inventory.models import Lot, TrackingMode
from apps.sales.models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine

from .orders import IssueDirection, MaterialIssue, MaterialIssueLine
from .tests_demand import DemandTestCase
from .tests_orders import TODAY
from .trace import descendants, held_by_customers, recall, runs_that_used


class TraceTestCase(DemandTestCase):
    def setUp(self):
        super().setUp()
        for item in (self.tape, self.virgin, self.regrind):
            item.tracking = TrackingMode.LOT
            item.save()
        self.polymer_lot = Lot.objects.create(item=self.virgin, code="PP-2609")
        self.tape_lot = Lot.objects.create(item=self.tape, code="TAPE-A")
        self.grind = Lot.objects.create(item=self.regrind, code="RG-1")
        self.stock(self.virgin, "3000", "100", lot=self.polymer_lot)
        self.stock(self.regrind, "1000", "60", lot=self.grind)
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)

    def batch_for(self, component):
        return {self.virgin.pk: self.polymer_lot,
                self.regrind.pk: self.grind}.get(component.item_id)

    def a_run(self):
        """Eats polymer and regrind; makes tape and regrind."""
        run = self.order("1000")
        run.release(TODAY)
        rows = [
            (component.item, component.quantity_required, self.batch_for(component))
            for component in run.components.all()
        ]
        self.issue_with_lots(run, rows).post()
        entry = self.produce(run, "1000", lot=self.tape_lot,
                             byproducts=[(self.regrind, "24.7423")])
        entry.byproducts.update(lot=self.grind)
        entry.post()
        return run

    def ship(self, quantity, day=15):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 9, day),
            currency=self.usd,
        )
        line = SalesOrderLine.objects.create(
            order=order, item=self.tape, uom=self.kg, warehouse=self.plant,
            quantity=Decimal(quantity), unit_price=Decimal("120"),
        )
        order.confirm()
        delivery = Delivery.objects.create(
            sales_order=order, delivery_date=datetime.date(2026, 9, day)
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=line, warehouse=self.plant,
            quantity_shipped=Decimal(quantity), lot=self.tape_lot,
        )
        delivery.post()
        return delivery


class WhichRunsAteItTests(TraceTestCase):
    def test_a_run_that_drew_the_batch(self):
        run = self.a_run()
        ((found, quantity),) = runs_that_used(self.polymer_lot)
        self.assertEqual(found, run)
        self.assertGreater(quantity, Decimal("700"))

    def test_what_went_back_to_stock_was_not_used(self):
        run = self.a_run()
        ((_, drawn),) = runs_that_used(self.polymer_lot)
        drawn_line = MaterialIssueLine.objects.get(
            issue__work_order=run, lot=self.polymer_lot
        )
        back = MaterialIssue.objects.create(
            work_order=run, direction=IssueDirection.RETURN, issue_date=TODAY,
            warehouse=self.plant,
        )
        MaterialIssueLine.objects.create(
            issue=back, item=self.virgin, quantity=Decimal("100"), uom=self.kg,
            lot=self.polymer_lot, line_number=1, returns_line=drawn_line,
        )
        back.post()
        ((_, kept),) = runs_that_used(self.polymer_lot)
        self.assertEqual(kept, drawn - Decimal("100"))

    def test_a_run_that_put_it_all_back_did_not_use_it(self):
        first = self.a_run()
        second = self.order("1000")
        second.release(TODAY)
        drawn = self.issue_with_lots(second, [(self.virgin, "50", self.polymer_lot)])
        drawn.post()
        back = MaterialIssue.objects.create(
            work_order=second, direction=IssueDirection.RETURN, issue_date=TODAY,
            warehouse=self.plant,
        )
        MaterialIssueLine.objects.create(
            issue=back, item=self.virgin, quantity=Decimal("50"), uom=self.kg,
            lot=self.polymer_lot, line_number=1, returns_line=drawn.lines.get(),
        )
        back.post()
        self.assertEqual([run for run, _ in runs_that_used(self.polymer_lot)], [first])

    def test_a_voided_issue_used_nothing(self):
        run = self.a_run()
        for issue in run.posted_issues():
            issue.void()
        self.assertEqual(runs_that_used(self.polymer_lot), [])


class WhatWasMadeFromItTests(TraceTestCase):
    def test_the_batches_a_run_made_from_it(self):
        run = self.a_run()
        (row,) = [r for r in descendants(self.polymer_lot) if r["level"] == 1]
        self.assertEqual((row["lot"], row["used_by"]), (self.polymer_lot, run))
        self.assertEqual({lot.code for lot in row["made"]}, {"TAPE-A", "RG-1"})

    def test_regrind_is_followed_and_the_loop_ends(self):
        """RG-1 came off the run and went back into it: followed once."""
        self.a_run()
        rows = descendants(self.polymer_lot, depth=6)
        self.assertEqual([(r["level"], r["lot"].code) for r in rows],
                         [(1, "PP-2609"), (2, "RG-1")])

    def test_a_walk_cut_short_says_where(self):
        self.a_run()
        report = recall(self.polymer_lot, depth=1)
        self.assertEqual([lot.code for lot in report["not_followed"]], ["RG-1"])
        self.assertEqual(recall(self.polymer_lot, depth=4)["not_followed"], [])


class WhoHoldsItTests(TraceTestCase):
    def test_a_customer_shipped_a_batch_made_from_it(self):
        self.a_run()
        delivery = self.ship("600")

        (row,) = recall(self.polymer_lot)["customers"]
        self.assertEqual((row["customer"], row["lot"]), (self.customer, self.tape_lot))
        self.assertEqual(row["quantity"], Decimal("600"))
        self.assertEqual(row["deliveries"], [delivery.number])

    def test_a_customer_who_sent_it_all_back_holds_nothing(self):
        self.a_run()
        self.ship("600").create_return(credit_invoices=False)
        self.assertEqual(held_by_customers([self.tape_lot]), [])

    def test_what_is_left_after_one_of_two_shipments_comes_back(self):
        self.a_run()
        self.ship("400")
        self.ship("200", day=16).create_return(credit_invoices=False)
        (row,) = held_by_customers([self.tape_lot])
        self.assertEqual(row["quantity"], Decimal("400"))

    def test_the_batch_itself_counts_when_sold_as_it_was(self):
        self.a_run()
        self.ship("250")
        (row,) = recall(self.tape_lot)["customers"]
        self.assertEqual(row["quantity"], Decimal("250"))


class LotTraceApiTests(TraceTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user("qa"))

    def test_both_directions(self):
        run = self.a_run()
        self.ship("600")

        forward = self.client.get(f"/api/manufacturing/lot-trace/{self.polymer_lot.pk}/recall/")
        self.assertEqual(forward.status_code, 200, forward.content)
        body = forward.json()
        self.assertEqual(body["descendants"][0]["used_by"], run.number)
        self.assertEqual(body["customers"][0]["customer"], "CEM")
        self.assertEqual(Decimal(body["customers"][0]["quantity"]), Decimal("600"))

        back = self.client.get(f"/api/manufacturing/lot-trace/{self.tape_lot.pk}/made-from/")
        self.assertEqual(back.status_code, 200, back.content)
        self.assertIn("PP-2609", {row["from_lot"]["code"] for row in back.json()})

    def test_a_bad_depth_or_lot_is_a_sentence(self):
        base = f"/api/manufacturing/lot-trace/{self.polymer_lot.pk}/recall/"
        self.assertEqual(self.client.get(base + "?depth=0").status_code, 400)
        self.assertEqual(self.client.get(base + "?depth=x").status_code, 400)
        self.assertEqual(
            self.client.get("/api/manufacturing/lot-trace/99999/recall/").status_code, 400
        )
