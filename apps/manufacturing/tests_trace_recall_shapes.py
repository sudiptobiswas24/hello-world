"""
rereview, item 5: trace.recall on a plain tree, a chain past its depth, a diamond and a loop. Printed as
JSON lines (TRACE_*) so that the same file run at the commit before a550ec6 and at the tip can be diffed.
Run: PYTHONPATH=scripts/gate .venv/bin/python manage.py test apps.manufacturing.tests_trace_recall_shapes --settings=fastsettings
"""

import json

from .tests_orders import TODAY, RunTestCase
from .tests_recall import RecallTestCase


def shape(found):
    rows = [(r["level"], r["lot"].code, str(r["quantity"]), sorted(m.code for m in r["made"]))
            for r in found["descendants"]]
    return {"rows_sorted": sorted(rows), "order": [row[:2] for row in rows],
            "customers": sorted((c["lot"].code, str(c["quantity"])) for c in found["customers"]),
            "not_followed": sorted(x.code for x in found["not_followed"])}


class TraceShape(RecallTestCase):
    def test_trace_recall_shapes(self):
        from .trace import recall

        print("\nTRACE_PLAIN " + json.dumps(shape(recall(self.b1)), sort_keys=True))
        # A diamond: RB-A and RB-B joined into RB-J (reached by two paths), and a split of RB-A.
        from .rebatch import rebatch

        joined = self.new("RB-J")
        rebatch(self.bag, self.plant, [(self.a, "10"), (self.b, "10")], [(joined, "15"), (self.new("RB-K"), "5")],
                "join", on_date=TODAY)
        print("TRACE_DIAMOND " + json.dumps(shape(recall(self.b1)), sort_keys=True))
        previous, taken = self.b2, "150"
        for code in ("C1", "C2", "C3", "C4", "C5"):
            keep = str(int(taken) - 20)
            _, (lot, _x) = self.split(taken=taken, rows=((code, keep), (code + "x", "20")), source=previous)
            previous, taken = lot, keep
        print("TRACE_DEEP " + json.dumps(shape(recall(self.b2)), sort_keys=True))
        print("TRACE_DEEP10 " + json.dumps(shape(recall(self.b2, depth=10)), sort_keys=True))
        print("TRACE_DEEP2 " + json.dumps(shape(recall(self.b2, depth=2)), sort_keys=True))


class TraceOnALoop(RunTestCase):
    def run_of(self, eats, makes):
        order = self.order()
        order.release(TODAY)
        self.issue_with_lots(order, [(self.regrind, "15", eats)]).post()
        entry = self.produce(order, "100", byproducts=[(self.regrind, "2")])
        entry.byproducts.update(lot=makes)
        entry.post()

    def test_trace_recall_ends_on_a_loop(self):
        from apps.inventory.models import Item, Lot

        from .trace import recall

        Item.objects.filter(pk=self.regrind.pk).update(tracking="lot")
        self.regrind.refresh_from_db()
        lots = [Lot.objects.create(item=self.regrind, code=f"RG-{n}") for n in range(1, 7)]
        for lot in lots:
            self.stock(self.regrind, "50", "60", lot=lot)
        # A ring of six runs: RG-1 into a run that made RG-2, ... RG-6 into one that made RG-1.
        for eats, makes in zip(lots, lots[1:] + lots[:1]):
            self.run_of(eats=eats, makes=makes)
        print("\nTRACE_RING6_DEFAULT " + json.dumps(shape(recall(lots[0])), sort_keys=True))
        print("TRACE_RING6_DEPTH10 " + json.dumps(shape(recall(lots[0], depth=10)), sort_keys=True))
