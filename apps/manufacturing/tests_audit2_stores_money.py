"""
Audit probe (audit2_stores): a run's issues, returns, voids, output, byproducts, scrap and close, at
costs that do not come out even, the shelf moving with the inventory account after every step.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.inventory.costing import CostingMethod, replay
from apps.inventory.models import Item, StockMovement

from .orders import IssueDirection
from .tests_orders import RunTestCase, TODAY

D = Decimal


class RunShelfAgainstLedger(RunTestCase):
    costs = (("2000", "100.00333"), ("500", "60.0049"), ("300", "30.3333"), ("100", "200.00501"))
    method = CostingMethod.AVERAGE

    def setUp(self):
        super().setUp()
        for item in (self.virgin, self.regrind, self.filler, self.colour, self.tape):
            item.costing_method = self.method
            item.save()
        for item, (q, c) in zip((self.virgin, self.regrind, self.filler, self.colour), self.costs):
            # second arrival at another price, so the average and the layers do not come out even
            self.stock(item, "37.7777", "97.123456")
        self.shelf0 = self.shelf()
        self.books0 = self.balance(self.inventory)

    def shelf(self):
        return sum((i.valuation_at(self.plant)[1] for i in Item.objects.all()), D("0"))

    def agree(self, label):
        self.assertEqual(self.shelf() - self.shelf0, self.balance(self.inventory) - self.books0, label)
        for i in Item.objects.all():
            self.assertEqual(replay(i, self.plant, before_id=10 ** 9), i.valuation_at(self.plant), label + " fold")

    def test_the_run(self):
        run = self.order("1000")
        run.release(TODAY)
        first = self.issue(run, [(self.virgin, "700.3333"), (self.regrind, "151.1111"), (self.filler, "80.0707"),
                                 (self.colour, "20.2003")])
        first.post()
        self.agree("issued")
        second = self.issue(run, [(self.virgin, "55.5555"), (self.colour, "1.1111")])
        second.post()
        self.agree("issued again")
        back = self.issue(run, [(self.virgin, "13.3333", first.lines.get(item=self.virgin))],
                          direction=IssueDirection.RETURN)
        back.post()
        self.agree("returned")
        second.void(TODAY)
        self.agree("second voided")
        e1 = self.produce(run, "333.3333", scrapped="3.3333", byproducts=[(self.regrind, "8.2474")])
        e1.post()
        self.agree("output 1")
        e2 = self.produce(run, "666.6667", byproducts=[(self.regrind, "16.4948")])
        e2.post()
        self.agree("output 2")
        e1.void(TODAY)
        self.agree("output 1 voided")
        run.close(TODAY)
        self.agree("closed")
        run.reopen(TODAY)
        self.agree("reopened")


class RunShelfAgainstLedgerFifo(RunShelfAgainstLedger):
    method = CostingMethod.FIFO
