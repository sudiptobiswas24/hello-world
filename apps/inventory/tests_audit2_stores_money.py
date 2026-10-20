"""
Audit probe (audit2_stores): the shelf moves by exactly what the inventory account moves,
over random runs of inventory documents at figures that do not come out even, for every
costing method, and every fold reads as a full replay.
"""

import random
from decimal import Decimal

from django.core.exceptions import ValidationError

from .costing import CostingMethod, replay
from .models import Item, Warehouse, StockMovement
from .snapshots import fold_position
from .tests_adjustments import AdjustmentTestCase
from .transfers import StockTransfer, StockTransferLine

D = Decimal


class FuzzShelfAgainstLedger(AdjustmentTestCase):
    def setUp(self):
        super().setUp()
        self.south = Warehouse.objects.create(code="S", name="South")
        self.items = {
            "avg": self.item,
            "fifo": Item.objects.create(sku="F", name="F", uom=self.each, costing_method=CostingMethod.FIFO),
        }
        self.items["std"] = Item.objects.create(sku="S", name="S", uom=self.each,
                                                costing_method=CostingMethod.STANDARD, standard_cost=D("10.005"))
        self.items["spec"] = Item.objects.create(sku="P", name="P", uom=self.each,
                                                 costing_method=CostingMethod.SPECIFIC, tracking="lot")
        from .models import Lot
        self.lots = [Lot.objects.create(item=self.items["spec"], code="L1"),
                     Lot.objects.create(item=self.items["spec"], code="L2")]
        self.whs = [self.warehouse, self.south]

    def shelf(self):
        return sum((i.valuation_at(w)[1] for i in Item.objects.all() for w in self.whs), D("0"))

    def check(self, trail):
        ledger = self.balance(self.inventory)
        self.assertEqual(self.shelf(), ledger, f"shelf vs ledger after {trail[-3:]}")
        for i in Item.objects.all():
            for w in self.whs:
                kept = i.valuation_at(w)
                fold_position(i, w)
                self.assertEqual(i.valuation_at(w), kept, f"fold {i} {w} after {trail[-3:]}")
                self.assertEqual(replay(i, w, before_id=10 ** 9), kept, f"full replay {i} {w} {trail[-3:]}")

    def run_seed(self, seed):
        rnd = random.Random(seed)
        trail, posted = [], []
        costs = ["10.005", "95.835051", "7.3333", "3.14159", "0.995", "12.5", "1.005", "6.665"]
        qtys = ["1", "2", "3", "7", "0.5", "1.25", "0.3333", "4.4", "11"]
        for step in range(30):
            op = rnd.choice(["in", "in", "out", "out", "void", "transfer", "transfer"])
            item = rnd.choice(list(self.items.values()))
            wh = rnd.choice(self.whs)
            try:
                if op == "in":
                    q, c = rnd.choice(qtys), rnd.choice(costs)
                    self.warehouse_for(wh)
                    if item.sku == "S":
                        c = "10.005"
                    a = self.adjust_lot(q, self.found, c, item, rnd)
                    posted.append(a)
                    trail.append(("in", item.sku, q, c, wh.code))
                elif op == "out":
                    have = D(item.on_hand_at(wh)).quantize(D('0.0001'))
                    if have <= 0:
                        continue
                    q = str(min(have, D(rnd.choice(qtys))))
                    self.warehouse_for(wh)
                    a = self.adjust_lot("-" + q, self.shrink, None, item, rnd, wh=wh)
                    posted.append(a)
                    trail.append(("out", item.sku, q, wh.code))
                elif op == "void" and posted:
                    a = rnd.choice(posted)
                    posted.remove(a)
                    a.void()
                    trail.append(("void", a.pk))
                elif op == "transfer":
                    have = D(item.on_hand_at(wh)).quantize(D('0.0001'))
                    if have <= 0:
                        continue
                    q = min(have, D(rnd.choice(qtys)))
                    dest = self.south if wh == self.warehouse else self.warehouse
                    t = StockTransfer.objects.create(transfer_date=__import__("datetime").date(2026, 3, 1),
                                                     from_warehouse=wh, to_warehouse=dest)
                    StockTransferLine.objects.create(transfer=t, item=item, uom=item.uom, quantity=q,
                                                     lot=self.pick_lot(item, wh, q, rnd))
                    t.post()
                    trail.append(("xfer", item.sku, str(q), wh.code))
            except __import__('decimal').InvalidOperation:
                raise AssertionError(f'crash after {trail[-4:]} op={op} q={locals().get("q")} have={locals().get("have")}')
            except ValidationError as refused:
                trail.append(("refused", str(refused)[:60]))
                continue
            self.check(trail)

    def pick_lot(self, item, wh, q, rnd):
        if item.sku != "P":
            return None
        for lot in rnd.sample(self.lots, 2):
            if self.held(lot, wh) >= q:
                return lot
        return self.lots[0]

    def adjust_lot(self, q, reason, cost, item, rnd, wh=None):
        import datetime
        from .models import StockAdjustment, StockAdjustmentLine
        lot = None
        if item.sku == "P":
            lot = rnd.choice(self.lots)
            if D(q) < 0:
                held = {l: self.held(l, self.warehouse) for l in self.lots}
                lot = max(held, key=held.get)
                if held[lot] < -D(q):
                    q = str(-held[lot]) if held[lot] > 0 else None
                    if q is None:
                        raise ValidationError("nothing in any batch")
                    q = "-" + q.lstrip("-")
        a = StockAdjustment.objects.create(adjustment_date=datetime.date(2026, 3, 1),
                                           warehouse=self.warehouse, reason=reason)
        StockAdjustmentLine.objects.create(adjustment=a, item=item, uom=item.uom, quantity=D(q),
                                           unit_cost=D(cost) if cost else None, lot=lot)
        a.post()
        return a

    def held(self, lot, wh):
        from django.db.models import Sum
        total = StockMovement.objects.filter(lot=lot, warehouse=wh).aggregate(t=Sum("quantity"))["t"] or 0
        return D(str(total)).quantize(D("0.0001"))

    def warehouse_for(self, wh):
        self.warehouse_saved = self.warehouse
        self.warehouse = wh  # adjust() uses self.warehouse

    def test_seeds(self):
        for seed in range(12):
            with self.subTest(seed=seed):
                # fresh state per seed is costly: continue on one DB, the check is cumulative
                self.run_seed(seed)
                self.warehouse = self.whs[0]


class ASignIsGuarded(AdjustmentTestCase):
    """A movement's money has the sign of its quantity: a path that books the wrong way round is refused."""

    def test_an_outbound_movement_cannot_add_money(self):
        self.stock("2", "10")
        with self.assertRaises(ValidationError):
            StockMovement.objects.create(item=self.item, warehouse=self.warehouse, movement_type="issue",
                                         uom=self.item.uom, quantity=D("-1"), value=D("10.00"),
                                         occurred_at=__import__("django.utils.timezone", fromlist=["now"]).now())

    def test_an_inbound_movement_cannot_take_money(self):
        with self.assertRaises(ValidationError):
            StockMovement.objects.create(item=self.item, warehouse=self.warehouse, movement_type="receipt",
                                         uom=self.item.uom, quantity=D("1"), value=D("-10.00"),
                                         occurred_at=__import__("django.utils.timezone", fromlist=["now"]).now())


class ABackdatedMovementAfterAFold(AdjustmentTestCase):
    """A fold written, then a movement dated before its boundary: the position reads as a full replay."""

    def test_every_method(self):
        import datetime as dt
        from django.utils import timezone
        for method in (CostingMethod.AVERAGE, CostingMethod.FIFO, CostingMethod.STANDARD):
            item = Item.objects.create(sku=f"B-{method}", name="B", uom=self.each, costing_method=method,
                                       standard_cost=D("10.005") if method == CostingMethod.STANDARD else None)
            now = timezone.now()
            def put(q, c, ago, kind="receipt"):
                return StockMovement.objects.create(
                    item=item, warehouse=self.warehouse, movement_type=kind, uom=self.each, quantity=D(q),
                    unit_cost=D(c) if c else None, occurred_at=now - dt.timedelta(hours=ago))
            put("3", "10.005", 10); put("2", "7.3333", 9); put("-2", None, 8, "issue")
            fold_position(item, self.warehouse)
            put("4", "95.835051", 20)           # backdated before the fold's boundary
            put("-3", None, 1, "issue")
            self.assertEqual(item.valuation_at(self.warehouse), replay(item, self.warehouse, before_id=10 ** 9), method)
            put("0", None, 30, "adjustment")  # a value-only row backdated further


class FifoThreeLayers(AdjustmentTestCase):
    """Worked in calc_audit2_stores/fifo3.py: 19.53 for five, then 119.65 for six, 287.50 left on 3."""

    def test_the_layers(self):
        item = Item.objects.create(sku="F3", name="F3", uom=self.each, costing_method=CostingMethod.FIFO)
        for q, c in (("3", "3.336667"), ("7", "4.761429"), ("4", "95.835051")):
            self.adjust(q, reason=self.found, cost=c, item=item)
        shelf0 = item.valuation_at(self.warehouse)[1]
        self.assertEqual(shelf0, D("10.01") + D("33.33") + D("383.34"))
        first = self.adjust("-5", reason=self.shrink, item=item)
        second = self.adjust("-6", reason=self.shrink, item=item)
        self.assertEqual(item.valuation_at(self.warehouse), (D("3"), shelf0 - D("19.53") - D("119.65")))
        self.assertEqual(-first.lines.get().posted_value, D("19.53"), "worked by hand for layers booked 10.01, 33.33, 383.34")


class AWriteUpAtTheAverageIsATotalNotARateMultipliedBack(AdjustmentTestCase):
    """9,000 worth 30,000.00 average 3.33333333: 3,000 found with no cost given are worth 10,000.00, not 9,999.90."""

    def test_it(self):
        self.stock("9000", "3.333333333333")
        self.assertEqual(self.item.valuation_at(self.warehouse)[1], D("30000.00"))
        up = self.adjust("3000", reason=self.found)
        self.assertEqual(up.lines.get().posted_value, D("10000.00"))
