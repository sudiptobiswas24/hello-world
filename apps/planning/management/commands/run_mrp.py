"""
Run the plan and print it as a planner would want to read it.

Late first, because a suggestion that needed starting last Tuesday is
the only thing on the list that cannot wait until after lunch.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.inventory.models import Warehouse
from apps.manufacturing.orders import WorkCentre

from apps.planning.capacity import LoadBook, overloaded_weeks

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
        if not orders and not run.actions.exists():
            self.stdout.write("Nothing to raise and nothing to move.")
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
            if order.stand_in_note:
                self.stdout.write(f"      but {order.stand_in_note}")
            if order.is_late():
                self.stdout.write(f"      late because {order.why_late()}")

        for label, rows in (
            ("Pull in", run.expedites()),
            ("Push out", run.defers()),
            ("Covering nothing", run.cancels()),
        ):
            rows = list(rows)
            if not rows:
                continue
            self.stdout.write(f"\n{label}:")
            for action in rows:
                self.stdout.write(f"  {action.document()}: {action.sentence()}")

        weeks = overloaded_weeks(
            LoadBook(run.warehouse, run.planned_on, run.horizon_end),
            WorkCentre.objects.filter(is_active=True),
            run.planned_on, run.horizon_end,
        )
        if weeks:
            self.stdout.write("\nWeeks a machine is over its hours:")
            for row in weeks[:10]:
                over = -row["spare_minutes"] / 60
                self.stdout.write(
                    f"  {row['work_centre'].code} week of "
                    f"{row['week_beginning']}: {over:,.1f} hours over"
                )

        overloaded = list(run.overloaded())
        if overloaded:
            self.stdout.write("\nMachines with no room:")
            for order in overloaded:
                self.stdout.write(
                    f"  {order.bottleneck or 'a machine'}: "
                    f"{order.quantity} {order.item.uom} {order.item.sku} "
                    f"wanted {order.needed_by}"
                )

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
