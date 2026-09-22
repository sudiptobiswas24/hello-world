"""
Run the plan and print it as a planner would want to read it.

Late first, because a suggestion that needed starting last Tuesday is
the only thing on the list that cannot wait until after lunch.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.inventory.models import Warehouse

from apps.planning.models import PlannedOrderKind
from apps.planning.mrp import plan


class Command(BaseCommand):
    help = "Net demand against supply and suggest what to make and buy."

    def add_arguments(self, parser):
        parser.add_argument("warehouse", help="Warehouse code to plan.")
        parser.add_argument("--on", dest="on_date", help="Treat this as today.")
        parser.add_argument("--horizon", type=int, help="Days ahead to plan.")
        parser.add_argument(
            "--firm", action="store_true",
            help="Turn every suggestion into a work order or requisition.",
        )

    def handle(self, *args, **options):
        warehouse = Warehouse.objects.filter(code=options["warehouse"]).first()
        if warehouse is None:
            raise CommandError(f"No warehouse with code {options['warehouse']}.")
        run = plan(
            warehouse, planned_on=options.get("on_date"),
            horizon_days=options.get("horizon"),
        )
        self.stdout.write(f"{run}  (to {run.horizon_end})")

        orders = list(run.orders.select_related("item").prefetch_related("demands"))
        if not orders:
            self.stdout.write("Nothing to raise.")
        for order in sorted(
            orders, key=lambda o: (-o.days_late(), o.release_on, o.item.sku)
        ):
            verb = "Make" if order.kind == PlannedOrderKind.MAKE else "Buy"
            late = f"  LATE by {order.days_late()} days" if order.is_late() else ""
            self.stdout.write(
                f"  {verb} {order.quantity} {order.item.uom} {order.item.sku}"
                f" — start {order.release_on}, wanted {order.needed_by}{late}"
            )
            self.stdout.write(f"      because {order.explanation()}")

        if run.cut_links:
            self.stdout.write("\nLinks not planned through:")
            for line in run.cut_links.splitlines():
                self.stdout.write(f"  {line}")
        if run.deferred_demand:
            self.stdout.write("\nDemand this run could not net:")
            for line in run.deferred_demand.splitlines():
                self.stdout.write(f"  {line}")

        if options["firm"]:
            firmed = 0
            for order in run.suggestions():
                order.firm()
                firmed += 1
            self.stdout.write(f"\nFirmed {firmed}.")
