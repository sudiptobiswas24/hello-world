"""
python manage.py setup_statutory

Load the figures the law sets from the dated files in statutory/ at the
repository's root, one file per kind, each row with the day it applies from,
the state where it applies, its value and its source:

    tds_rates.csv            TDS sections, rates and thresholds (1961 and 2025 Act sections)
    tcs_rates.csv            the TCS section and rate on scrap
    provident_fund.csv       the EPF and EPS wage ceilings
    esi.csv                  the ESI rates and coverage ceiling
    professional_tax.csv     professional tax schedules (West Bengal from 1 October 2026)
    labour_welfare_fund.csv  the labour welfare fund (June and December)
    bonus.csv                the bonus rates, the calculation floor and the eligibility ceiling
    minimum_wages.csv        minimum wages by state, employment, zone and skill, with VDA revisions

Every row is loaded unconfirmed unless its confirmed_by column names a user
who may confirm it, and nothing that posts reads an unconfirmed figure: the
pay run, the bill and the invoice are refused in words naming it until the
Controller confirms it. A row whose key is taken is kept as it is; run again,
it adds only what is missing. Professional tax and the welfare fund load into
the active pay component marked as that levy (Statutory), which must exist.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounting.statutory_load import Loader, load_tcs, load_tds, number, rows
from apps.hr.payroll import PayComponent, PayComponentSlab, Statutory
from apps.hr.statutory import MinimumWage, StatutoryFigure

FIGURE_FILES = ("provident_fund", "esi", "bonus")
LWF_SHARES = {"employee": Statutory.LWF, "employer": Statutory.LWF_EMPLOYER}


def load_figures(loader, name):
    for row in rows(name):
        key = dict(kind=row["kind"], state=row["state"], effective_from=row["effective_from"])
        if StatutoryFigure.objects.filter(**key).exists():
            loader.kept.append(f"{row['kind']} {row['state'] or 'every state'} from {row['effective_from']}")
            continue
        figure = StatutoryFigure.objects.create(**key, value=number(row["value"]), source=row["source"][:255])
        loader.seeded(figure, f"{figure}, unconfirmed")
        loader.settle(figure, row, "hr.confirm_statutoryfigure", str(figure))


def component_for(loader, statutory, what):
    found = list(PayComponent.objects.filter(statutory=statutory, is_active=True).order_by("pk"))
    if len(found) != 1:
        loader.skipped.append(
            f"{what}: {'no' if not found else len(found)} active pay component(s) marked "
            f"'{Statutory(statutory).label}'. Make one (or leave one active), then run this again.")
        return None
    return found[0]


def load_slab(loader, component, row, month, above, up_to, amount):
    key = dict(component=component, state=row["state"], effective_from=row["effective_from"], month=month,
               above=above)
    if PayComponentSlab.objects.filter(**key).exists():
        loader.kept.append(f"{component.code} {row['state']} from {row['effective_from']} over {above}")
        return
    slab = PayComponentSlab.objects.create(**key, up_to=up_to, amount=amount)
    loader.seeded(slab, f"{slab}, unconfirmed")
    loader.settle(slab, row, "hr.confirm_statutoryfigure", str(slab))


def load_professional_tax(loader):
    component = component_for(loader, Statutory.PT, "Professional tax")
    if component is None:
        return
    for row in rows("professional_tax"):
        month = int(row["month"]) if row["month"] else None
        load_slab(loader, component, row, month, number(row["above"]), number(row["up_to"]), number(row["amount"]))


def load_welfare_fund(loader):
    components = {share: component_for(loader, statutory, f"Labour welfare fund, the {share}'s share")
                  for share, statutory in LWF_SHARES.items()}
    for row in rows("labour_welfare_fund"):
        component = components[row["share"]]
        if component is not None:
            load_slab(loader, component, row, int(row["month"]), number("0"), None, number(row["amount"]))


def load_minimum_wages(loader):
    for row in rows("minimum_wages"):
        key = {name: row[name] for name in ("state", "employment", "zone", "skill", "effective_from")}
        if MinimumWage.objects.filter(**key).exists():
            loader.kept.append(f"Minimum wage {row['employment']} {row['skill']} {row['state']} {row['zone']} "
                               f"from {row['effective_from']}")
            continue
        wage = MinimumWage.objects.create(**key, basic=number(row["basic"]), vda=number(row["vda"]),
                                          source=row["source"][:255])
        loader.seeded(wage, f"{wage}, unconfirmed")
        loader.settle(wage, row, "hr.confirm_minimumwage", str(wage))


def load_all(loader):
    from apps.accounting.models import Account

    with transaction.atomic():
        load_tds(loader)
        load_tcs(loader, payable_account=Account.objects.filter(code="2260").first())
        for name in FIGURE_FILES:
            load_figures(loader, name)
        load_professional_tax(loader)
        load_welfare_fund(loader)
        load_minimum_wages(loader)
    return loader


class Command(BaseCommand):
    help = "Load the dated statutory figures from statutory/, each unconfirmed until the Controller confirms it."

    def handle(self, *args, **options):
        load_all(Loader()).report(self.stdout.write)
