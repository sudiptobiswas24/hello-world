"""
Give every position a starting point, without waiting for a write.

Folding happens on the write path, so a ledger that already exists when
this code is deployed carries no folds at all, and an item that has
stopped moving never gets one — it pays the full replay on every report
forever. This walks the positions once and folds them.
"""

from django.core.management.base import BaseCommand
from django.db.models import Count

from apps.inventory.models import Item, StockMovement, Warehouse
from apps.inventory.snapshots import fold_position


class Command(BaseCommand):
    help = "Fold a valuation starting point for every item and warehouse."

    def add_arguments(self, parser):
        parser.add_argument(
            "--at-least", type=int, default=0,
            help="Skip positions with fewer movements than this, which are "
                 "cheap enough to replay from the beginning anyway.",
        )

    def handle(self, *args, **options):
        floor = options["at_least"]
        positions = (
            StockMovement.objects.values("item_id", "warehouse_id")
            .annotate(moves=Count("id"))
            .order_by("item_id", "warehouse_id")
        )
        items = {}
        warehouses = {}
        folded = 0
        for position in positions:
            if position["moves"] < floor:
                continue
            item = items.setdefault(
                position["item_id"], Item.objects.get(pk=position["item_id"])
            )
            warehouse = warehouses.setdefault(
                position["warehouse_id"],
                Warehouse.objects.get(pk=position["warehouse_id"]),
            )
            fold_position(item, warehouse)
            folded += 1
        self.stdout.write(f"Folded {folded} position(s).")
