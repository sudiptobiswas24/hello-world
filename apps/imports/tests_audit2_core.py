"""audit2_core probes for the imports app: the go-live files run twice."""
from decimal import Decimal

from django.db.models import Sum

from apps.inventory.adjustments import AdjustmentReason
from apps.inventory.models import Item

from .importer import run
from .tests import GO_LIVE, ImportTestCase


class RunTwiceTests(ImportTestCase):
    def test_opening_stock_file_run_twice_does_not_double_the_stock(self):
        # Every other opening kind refuses a repeat (O280 is the same hole in opening balances).
        # Plant figure: 1,000 kg of granules at 3.50 = 3,500.00 in the books, once.
        from apps.inventory.models import StockMovement
        AdjustmentReason.objects.create(code="OPENING", name="Opening stock", account=self.opening)
        text = "sku,warehouse,quantity,unit_cost\nWDG-1,WH1,1000,3.50\n"
        first = run("opening_stock", text, commit=True, date=GO_LIVE, reason="OPENING")
        self.assertEqual(first.errors, [])
        kept = StockMovement.objects.filter(item__sku="WDG-1").aggregate(t=Sum("quantity"))["t"]
        second = run("opening_stock", text, commit=True, date=GO_LIVE, reason="OPENING")
        on_hand = StockMovement.objects.filter(item__sku="WDG-1").aggregate(t=Sum("quantity"))["t"]
        self.assertEqual((second.committed, on_hand), (False, kept))
