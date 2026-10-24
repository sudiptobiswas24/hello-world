"""
Answer the telephone: can we promise this, and when.

A command rather than only an API because the question is asked while
somebody is on the line, and a terminal is faster than a screen
nobody has built yet.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from apps.core.models import to_date
from apps.inventory.models import Item, Warehouse

from apps.planning.promise import (
    available_to_promise,
    capable_to_promise,
    when_can_we_promise,
)


class Command(BaseCommand):
    help = "Say when a quantity of an item can be promised."

    def add_arguments(self, parser):
        parser.add_argument("sku")
        parser.add_argument("quantity")
        parser.add_argument("--warehouse", required=True)
        parser.add_argument("--on", dest="on_date")
        parser.add_argument(
            "--ladder", action="store_true",
            help="Show the uncommitted position period by period.",
        )

    def handle(self, *args, **options):
        item = Item.objects.filter(sku=options["sku"]).first()
        if item is None:
            raise CommandError(f"No item with sku {options['sku']}.")
        warehouse = Warehouse.objects.filter(code=options["warehouse"]).first()
        if warehouse is None:
            raise CommandError(f"No warehouse with code {options['warehouse']}.")
        quantity = Decimal(options["quantity"])
        on_date = to_date(options.get("on_date"))

        if options["ladder"]:
            self.stdout.write(f"Uncommitted {item.sku} at {warehouse}:")
            for row in available_to_promise(item, warehouse, planned_on=on_date):
                self.stdout.write(
                    f"  {row['date']}  in {row['arriving']:>12}"
                    f"  owed {row['owed']:>12}"
                    f"  promisable {row['promisable']:>12}"
                )
            self.stdout.write("")

        from_stock = when_can_we_promise(
            item, warehouse, quantity, planned_on=on_date
        )
        if from_stock is not None:
            self.stdout.write(
                f"{quantity} {item.uom} {item.sku}: yes, from {from_stock}, "
                "out of stock and what is already on order."
            )
            return

        answer = capable_to_promise(item, warehouse, quantity, planned_on=on_date)
        if answer["date"] is None:
            self.stdout.write(f"{quantity} {item.uom} {item.sku}: no — {answer['note']}.")
            return
        self.stdout.write(
            f"{quantity} {item.uom} {item.sku}: not from stock, but "
            f"{answer['date']} — {answer['note']}."
        )
        self.stdout.write(
            "Nothing here is reserved. Confirm the order to hold it."
        )
