"""
Audit probe (audit2_stores): inventory 0040 on old rows of every costing method, awkward figures,
shortfalls, value-only rows, lots, a standard changed, a consignment shelf: it must not crash, every
row gets a whole-paisa value, and each shelf's sum is the old replay's within the rounding it says.
"""

import datetime
from decimal import Decimal

from django.db import connection
from django.test import tag
from django.utils import timezone

from .tests_migrations import MigrationCase

D = Decimal


@tag("migration")
class Mixed(MigrationCase):
    before = [("inventory", "0039_moved_from")]
    after = [("inventory", "0040_movement_money")]

    def goods_as(self, sku, method, standard=None, tracking="none"):
        item = self.goods(sku)
        item.costing_method = method
        if standard is not None:
            item.standard_cost = D(standard)
        item.tracking = tracking
        item.save()
        return item

    def row(self, item, wh, q, cost=None, adj=None, minute=0, kind="adjustment", lot=None, adjusts=None):
        uom = self.apps.get_model("core", "UnitOfMeasure").objects.get(code="kg")
        return self.apps.get_model("inventory", "StockMovement").objects.create(
            item_id=item.pk, warehouse_id=wh.pk, movement_type=kind, uom_id=uom.pk, quantity=D(q),
            unit_cost=D(cost) if cost is not None else None,
            value_adjustment=D(adj) if adj is not None else None, lot_id=lot.pk if lot else None,
            adjusts_id=adjusts.pk if adjusts else None,
            occurred_at=timezone.now() + datetime.timedelta(minutes=minute))

    def test_every_method(self):
        store, other = self.shelf("STORE"), self.shelf("OTHER")
        consign = self.shelf("CONS")
        Wh = self.apps.get_model("inventory", "Warehouse")
        # average
        a = self.goods_as("A", "average")
        rows = [self.row(a, store, "3", "6.6667", minute=1), self.row(a, store, "-1", minute=2),
                self.row(a, store, "2.5", "7.3333", minute=3), self.row(a, store, "0", adj="1.337", minute=4),
                self.row(a, store, "-4.5", minute=5), self.row(a, store, "-2", minute=6),   # past empty
                self.row(a, store, "5", "1.005", minute=7), self.row(a, store, "-5", minute=8)]
        # fifo
        f = self.goods_as("F", "fifo", standard="9")
        first = self.row(f, store, "2", "10.005", minute=1)
        self.row(f, store, "3", "10.3333", minute=2)
        self.row(f, store, "-4", minute=3)
        self.row(f, store, "0", adj="5.55", minute=4, adjusts=first)       # its layer shipped
        self.row(f, store, "-3", minute=5)                                    # short
        self.row(f, store, "4", "11.1111", minute=6)
        self.row(f, store, "0", adj="3.3333", minute=7)
        self.row(f, store, "-2", minute=8)
        self.row(f, other, "-1", minute=1)                                    # short from nothing
        # specific
        s = self.goods_as("S", "specific", tracking="lot")
        Lot = self.apps.get_model("inventory", "Lot")
        l1, l2 = Lot.objects.create(item_id=s.pk, code="L1"), Lot.objects.create(item_id=s.pk, code="L2")
        self.row(s, store, "2", "10.005", lot=l1, minute=1)
        self.row(s, store, "2", "3.3333", lot=l2, minute=2)
        self.row(s, store, "-1", lot=l1, minute=3)
        self.row(s, store, "-3", lot=l2, minute=4)                            # past empty
        self.row(s, store, "0", adj="2.5", lot=l1, minute=5)
        # standard, revalued once, plus a consignment shelf
        t = self.goods_as("T", "standard", standard="12.005")
        self.row(t, store, "5", "11", minute=1)
        self.row(t, store, "0", adj="5.025", minute=2)   # +1.005 each on 5
        self.row(t, store, "-2", minute=3)
        self.row(t, consign, "3", "0", minute=1)
        Wh.objects.filter(pk=consign.pk).update(consignment_vendor_id=None)
        self.snapshot(a, store, "average")

        apps = self.migrated()
        Row = apps.get_model("inventory", "StockMovement")
        for item in (a, f, s, t):
            for wh in (store, other, consign):
                total = sum((r.value for r in Row.objects.filter(item_id=item.pk, warehouse_id=wh.pk)), D("0"))
                self.assertEqual(total, total.quantize(D("0.01")))
        # the average's shelf: old replay value, unrounded
        def old_average(rows):
            q = v = D("0")
            for r in rows:
                if r.quantity > 0:
                    v += r.quantity * (r.unit_cost or 0); q += r.quantity
                elif r.quantity < 0:
                    v -= -r.quantity * ((v / q) if q > 0 else 0); q += r.quantity
                if r.value_adjustment:
                    v += r.value_adjustment
            return v
        old = old_average(rows)
        new = sum((Row.objects.get(pk=r.pk).value for r in rows), D("0"))
        self.assertLessEqual(abs(new - old), D("0.005") * len(rows), (old, new))
        # standard shelf: what the books would show: 5*11 + 5.025 - 2*12.005 = 36.015
        std = sum((r.value for r in Row.objects.filter(item_id=t.pk, warehouse_id=store.pk)), D("0"))
        self.assertLessEqual(abs(std - D("36.015")), D("0.02"), std)
