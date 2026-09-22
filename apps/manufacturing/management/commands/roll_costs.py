"""
Roll a cost version up and say what a sack is made of.

Publishing is deliberately not here. A standard cost change revalues
every standard-costed shelf in the company, and that is not something
to do with a command-line flag by accident.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.inventory.models import Item
from apps.manufacturing.costing import CostVersion, against_actual, explain


class Command(BaseCommand):
    help = "Roll up a cost version and print what things are made of."

    def add_arguments(self, parser):
        parser.add_argument("version", help="The cost version's code.")
        parser.add_argument(
            "--explain", dest="sku",
            help="Break one item's cost into what it is made of.",
        )
        parser.add_argument(
            "--drift", action="store_true",
            help="Show where the standard has moved away from the shelf.",
        )

    def handle(self, *args, **options):
        version = CostVersion.objects.filter(code=options["version"]).first()
        if version is None:
            raise CommandError(f"No cost version with code {options['version']}.")

        if not version.is_published():
            done = version.roll_up()
            self.stdout.write(f"{version}: rolled {len(done)} made items.")
        else:
            self.stdout.write(f"{version}: published {version.published_on}.")

        for row in version.costs.select_related("item").order_by("-total")[:20]:
            self.stdout.write(
                f"  {row.item.sku:16} {row.total:>14}"
                f"  material {row.material:>12}"
                f"  conversion {row.conversion:>10}"
                f"  credit {row.byproduct_credit:>10}"
            )

        if options["sku"]:
            item = Item.objects.filter(sku=options["sku"]).first()
            if item is None:
                raise CommandError(f"No item with sku {options['sku']}.")
            report = explain(version, item)
            self.stdout.write(f"\n{item.sku} costs {report['cost']} because:")
            for line in report["lines"]:
                share = line["share_percent"]
                self.stdout.write(
                    f"  {line['item'].sku:16} {line['quantity_per_unit']:>12}"
                    f" at {line['rate']:>10}"
                    f"  = {line['cost_per_unit']:>12}"
                    + (f"  ({share}%)" if share is not None else "")
                )

        if options["drift"]:
            self.stdout.write("\nStandard against the shelf, worst first:")
            for row in against_actual(version)[:15]:
                if row["difference_percent"] is None:
                    continue
                self.stdout.write(
                    f"  {row['item'].sku:16} standard {row['standard']:>12}"
                    f"  actual {row['actual']:>12}"
                    f"  {row['difference_percent']:>8}%"
                )
