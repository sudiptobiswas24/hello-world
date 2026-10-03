"""
What a run will need, against what is actually in the yard.

Three questions a planner asks before releasing a work order, and the
third is the one no conventional bill of materials answers: a blend
calling for fifteen per cent reprocessed material against processes
that recover six per cent of throughput is a plant that has to buy
scrap from somebody, and the requirement and the recovery sit in
different documents and never meet.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from apps.inventory.models import Item, Warehouse
from apps.manufacturing.bom import (
    default_bom_for,
    material_balance,
    net_requirements,
)


class Command(BaseCommand):
    help = "Explode a bill of materials against stock before releasing a run."

    def add_arguments(self, parser):
        parser.add_argument("sku", help="What is being made.")
        parser.add_argument("quantity", type=Decimal)
        parser.add_argument(
            "--warehouse", help="Which yard to measure against. Default: all of them."
        )

    def handle(self, *args, **options):
        item = Item.objects.filter(sku=options["sku"]).first()
        if item is None:
            raise CommandError(f"No item with SKU {options['sku']}.")
        bom = default_bom_for(item)
        if bom is None:
            raise CommandError(
                f"{item} has no default bill of materials, so nothing here knows "
                "how it is made."
            )
        warehouse = None
        if options["warehouse"]:
            warehouse = Warehouse.objects.filter(code=options["warehouse"]).first()
            if warehouse is None:
                raise CommandError(f"No warehouse with code {options['warehouse']}.")

        quantity = options["quantity"]
        self.stdout.write(f"{quantity} {bom.uom} of {item} to {bom}\n")

        self.stdout.write("\nTo find on a shelf:")
        for material, needed, uom in net_requirements(bom, quantity, bom.uom):
            on_hand = material.on_hand_at(warehouse)
            short = material.to_stock_quantity(needed, uom) - on_hand
            note = f"  SHORT {short:.3f}" if short > 0 else ""
            self.stdout.write(
                f"  {material.sku:<16} {needed:>14.4f} {uom}   "
                f"on hand {on_hand:>12.4f}{note}"
            )

        self.stdout.write("\nAgainst what the run gives back:")
        for material, needed, made, net in material_balance(bom, quantity, bom.uom):
            if not made:
                continue
            self.stdout.write(
                f"  {material.sku:<16} needs {needed:>12.4f}   "
                f"makes {made:>12.4f}   net {net:>12.4f}"
            )
