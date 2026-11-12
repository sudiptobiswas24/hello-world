"""
python manage.py setup_payroll

Seed what a first pay run needs and a fresh install did not have (O419): the
payroll accounts, and the standard pay components, each statutory one marked
with its levy so the monthly files find it and setup_statutory can load
professional tax and the welfare fund into it.

    2410 PF payable          2420 ESI payable       2430 PT payable
    2440 LWF payable         6000 Wages             6100 Employer contributions

    BASIC, DA, HRA                 earnings, charged to Wages, cut for unpaid leave
    PF, PF-ER                      provident fund, a per cent of BASIC and DA up to the dated EPF ceiling
    ESI, ESI-ER                    ESI at the dated rates, covered up to the dated ESI ceiling
    PT                             professional tax, from its slab of the gross (setup_statutory loads it)
    LWF, LWF-ER                    the labour welfare fund, from its slabs (setup_statutory loads them)
    BONUS                          the statutory bonus accrued each month into 2450 Bonus payable: a per cent of
                                   BASIC and DA up to the bonus ceiling (the higher of the dated 7,000 and the
                                   minimum wage), for those within the dated eligibility ceiling (21,000)
    BONUS-PAID                     the bonus paid out: an earning that debits Bonus payable, put on a month's pay
                                   by the year's bonus declaration

BONUS is then given to everyone in service who is paid BASIC or DA and does not have it, at the dated least per
cent (8.33): run again, or setup_statutory run after it, gives it to whoever joined since. go_live_check names
anyone left without it.

Everything is a default for the accountant to review, listed under Settings >
Seeded defaults until it is marked reviewed. It never overwrites: an account
whose code is taken is kept when it is what the code is for, and otherwise
left as it is and the payroll account seeded under a free code beside it (the
chart loader's rule, setup_indian_books); a component whose code is taken is
kept as it is, and so is a levy some active component is already marked as.
Run again, it adds only what is missing.

Run it after setup_indian_books and before setup_statutory. The per cent each
person pays PF at (12) is typed on the person's pay, as every rate is; ESI's
rates and every ceiling are the dated figures setup_statutory loads.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounting.management.commands.setup_indian_books import Command as Books
from apps.accounting.models import AccountType
from apps.hr.payroll import ComponentBasis, ComponentKind, PayComponent, Rounding, Statutory
from apps.hr.statutory import BONUS_CEILING, FigureKind

L, X = AccountType.LIABILITY, AccountType.EXPENSE
# (code, name, type, words an account already under the code must be named for to be used)
ACCOUNTS = [
    ("2410", "PF payable", L, ("pf", "provident")),
    ("2420", "ESI payable", L, ("esi", "state insurance")),
    ("2430", "PT payable", L, ("pt", "professional")),
    ("2440", "LWF payable", L, ("lwf", "welfare")),
    ("6000", "Wages", X, ("wage", "salar")),
    ("6100", "Employer contributions", X, ("employer", "contribution")),
    ("2450", "Bonus payable", L, ("bonus",)),
    ("6110", "Bonus", X, ("bonus",)),
]
EARNED = dict(kind=ComponentKind.EARNING, basis=ComponentBasis.FIXED, reduces_for_unpaid_leave=True)
PF_TERMS = dict(basis=ComponentBasis.PERCENT_OF_GROSS, ceiling_figure=FigureKind.EPF_WAGE_CEILING,
                rounding=Rounding.RUPEE, remit_by_day=15)
ESI_TERMS = dict(basis=ComponentBasis.PERCENT_OF_GROSS, coverage_period_months=6, rounding=Rounding.RUPEE_UP,
                 remit_by_day=15)
# (code, name, terms, expense account, liability account, base components, coverage components)
COMPONENTS = [
    ("BASIC", "Basic salary", dict(EARNED, sequence=10, is_ordinary_wages=True), "6000", None, (), ()),
    ("DA", "Dearness allowance", dict(EARNED, sequence=20, is_ordinary_wages=True), "6000", None, (), ()),
    ("HRA", "House rent allowance", dict(EARNED, sequence=30), "6000", None, (), ()),
    ("PF", "Provident fund, the employee's share",
     dict(PF_TERMS, kind=ComponentKind.DEDUCTION, sequence=50, statutory=Statutory.PF),
     None, "2410", ("BASIC", "DA"), ()),
    ("PF-ER", "Provident fund, the employer's share",
     dict(PF_TERMS, kind=ComponentKind.EMPLOYER_COST, sequence=60, statutory=Statutory.PF_EMPLOYER),
     "6100", "2410", ("BASIC", "DA"), ()),
    ("ESI", "ESI, the employee's share",
     dict(ESI_TERMS, kind=ComponentKind.DEDUCTION, sequence=70, statutory=Statutory.ESI),
     None, "2420", ("BASIC", "DA", "HRA"), ("BASIC", "DA", "HRA")),
    ("ESI-ER", "ESI, the employer's share",
     dict(ESI_TERMS, kind=ComponentKind.EMPLOYER_COST, sequence=80, statutory=Statutory.ESI_EMPLOYER),
     "6100", "2420", ("BASIC", "DA", "HRA"), ("BASIC", "DA", "HRA")),
    ("PT", "Professional tax",
     dict(kind=ComponentKind.DEDUCTION, basis=ComponentBasis.SLAB, sequence=90, statutory=Statutory.PT),
     None, "2430", (), ()),
    ("LWF", "Labour welfare fund, the employee's share",
     dict(kind=ComponentKind.DEDUCTION, basis=ComponentBasis.SLAB, sequence=95, statutory=Statutory.LWF),
     None, "2440", (), ()),
    ("LWF-ER", "Labour welfare fund, the employer's share",
     dict(kind=ComponentKind.EMPLOYER_COST, basis=ComponentBasis.SLAB, sequence=96,
          statutory=Statutory.LWF_EMPLOYER),
     "6100", "2440", (), ()),
    # The statutory bonus (Payment of Bonus Act 1965; Code on Wages 2019 s.26): accrued on basic and dearness
    # allowance, the Act's "salary or wage", which also decides who is within the eligibility ceiling. The rate
    # is on each person's pay, as every rate is, within the dated least and most.
    ("BONUS", "Statutory bonus, accrued",
     dict(kind=ComponentKind.EMPLOYER_COST, basis=ComponentBasis.PERCENT_OF_GROSS, ceiling_figure=BONUS_CEILING,
          rounding=Rounding.PAISA, sequence=110),
     "6110", "2450", ("BASIC", "DA"), ("BASIC", "DA")),
    # Paid out of what was accrued: it debits Bonus payable, and counts towards no levy's base (a yearly bonus
    # is not the wages PF and ESI are taken of).
    ("BONUS-PAID", "Statutory bonus, paid",
     dict(kind=ComponentKind.EARNING, basis=ComponentBasis.FIXED, is_taxable=False, sequence=40),
     "2450", None, (), ()),
]


def seed_payroll(books):
    """The accounts and components, through the chart loader `books` (its made, kept and collisions lists)."""
    accounts = {code: books.account(code, name, kind, False, words=words) for code, name, kind, words in ACCOUNTS}
    components = {}
    for code, name, terms, expense, liability, base, coverage in COMPONENTS:
        found = PayComponent.objects.filter(code=code).first()
        if found is not None:
            books.kept.append(f"pay component {code}")
            components[code] = found
            continue
        levy = terms.get("statutory")
        marked = PayComponent.objects.filter(statutory=levy, is_active=True).order_by("pk").first() if levy else None
        if marked is not None:
            books.kept.append(f"{Statutory(levy).label}: pay component {marked.code} is marked as it")
            continue
        component = PayComponent.objects.create(
            code=code, name=name, **terms,
            expense_account=accounts[expense] if expense else None,
            liability_account=accounts[liability] if liability else None)
        component.base_components.set([components[part] for part in base if part in components])
        component.coverage_components.set([components[part] for part in coverage if part in components])
        books.seeded(component, f"pay component {code} {name}")
        components[code] = component
    return components


class Command(BaseCommand):
    help = "Seed the payroll accounts and the standard pay components, each a default for the accountant to review."

    def handle(self, *args, **options):
        books = Books(stdout=self.stdout, stderr=self.stderr)
        books.made, books.kept, books.collisions = [], [], []
        from apps.hr.bonus import give_statutory_accruals

        with transaction.atomic():
            seed_payroll(books)
            given = give_statutory_accruals()
        for line in books.made:
            self.stdout.write(f"seeded, to review: {line}")
        for line in books.kept:
            self.stdout.write(f"kept as it was: {line}")
        for line in books.collisions:
            self.stdout.write(f"collision, for the accountant: {line}")
        for line in given:
            self.stdout.write(f"given: {line}")
        self.stdout.write(f"{len(books.made)} seeded for the accountant to review, {len(books.kept)} kept.")
        self.stdout.write("Then: python manage.py setup_statutory, which loads professional tax and the welfare "
                          "fund into the components marked as them.")
