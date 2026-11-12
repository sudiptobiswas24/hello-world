"""
O419: a fresh install had no pay components and the Indian chart no PF, ESI,
PT, LWF or wages accounts. The payroll desk built each by hand, marked with its
levy, before the first pay run, and setup_statutory could not load professional
tax or the welfare fund until it had: it said so, and loaded them on a second
run. setup_payroll seeds them, for the accountant to review, never overwriting.
"""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.accounting.models import Account, AccountType, SeededDefault

from .payroll import ComponentBasis, ComponentKind, PayComponent, PayComponentSlab, Statutory
from .statutory import FigureKind


def said(command, *args):
    out = StringIO()
    call_command(command, *args, stdout=out)
    return out.getvalue()


class SetupPayrollTests(TestCase):
    def test_a_fresh_install_has_its_components_and_statutory_loads_pt_and_lwf_on_its_first_run(self):
        said("setup_indian_books")
        seeded = said("setup_payroll")
        accounts = {account.code: (account.name, account.account_type)
                    for account in Account.objects.filter(code__in=("2410", "2420", "2430", "2440", "6000", "6100"))}
        self.assertEqual(accounts, {
            "2410": ("PF payable", AccountType.LIABILITY), "2420": ("ESI payable", AccountType.LIABILITY),
            "2430": ("PT payable", AccountType.LIABILITY), "2440": ("LWF payable", AccountType.LIABILITY),
            "6000": ("Wages", AccountType.EXPENSE), "6100": ("Employer contributions", AccountType.EXPENSE)})
        marked = dict(PayComponent.objects.values_list("code", "statutory"))
        self.assertEqual(marked, {
            "BASIC": "", "DA": "", "HRA": "", "PF": Statutory.PF, "PF-ER": Statutory.PF_EMPLOYER,
            "ESI": Statutory.ESI, "ESI-ER": Statutory.ESI_EMPLOYER, "PT": Statutory.PT, "LWF": Statutory.LWF,
            "LWF-ER": Statutory.LWF_EMPLOYER, "BONUS": "", "BONUS-PAID": ""})
        pf = PayComponent.objects.get(code="PF-ER")
        self.assertEqual((pf.kind, pf.basis, pf.ceiling_figure, pf.expense_account.code, pf.liability_account.code),
                         (ComponentKind.EMPLOYER_COST, ComponentBasis.PERCENT_OF_GROSS, FigureKind.EPF_WAGE_CEILING,
                          "6100", "2410"))
        self.assertEqual(sorted(pf.base_components.values_list("code", flat=True)), ["BASIC", "DA"])
        self.assertEqual(sorted(PayComponent.objects.get(code="ESI").coverage_components.values_list("code", flat=True)),
                         ["BASIC", "DA", "HRA"])
        # Each for the accountant to review.
        self.assertEqual(SeededDefault.objects.filter(model="hr.paycomponent").count(), 12)
        self.assertIn("seeded, to review: pay component PT Professional tax", seeded)

        loaded = said("setup_statutory")
        self.assertNotIn("not loaded: Professional tax", loaded)
        self.assertNotIn("not loaded: Labour welfare fund", loaded)
        for code in ("PT", "LWF", "LWF-ER"):
            self.assertTrue(PayComponentSlab.objects.filter(component__code=code).exists(), code)

    def test_run_twice_it_adds_nothing(self):
        said("setup_indian_books")
        said("setup_payroll")
        again = said("setup_payroll")
        # Eight accounts and twelve components, the bonus's two of each among them (known limit 19).
        self.assertIn("0 seeded for the accountant to review, 20 kept.", again)
        self.assertEqual(PayComponent.objects.count(), 12)
        self.assertEqual(SeededDefault.objects.filter(model="hr.paycomponent").count(), 12)

    def test_what_is_there_is_never_overwritten(self):
        rent = Account.objects.create(code="2410", name="Rent payable", account_type=AccountType.LIABILITY)
        wages = Account.objects.create(code="6000", name="Salaries and wages", account_type=AccountType.EXPENSE)
        basic = PayComponent.objects.create(code="BASIC", name="Basic pay", kind=ComponentKind.EARNING,
                                            expense_account=wages, sequence=5)
        own_pt = PayComponent.objects.create(code="PTWB", name="Professional tax, Bengal", kind=ComponentKind.DEDUCTION,
                                             basis=ComponentBasis.SLAB, liability_account=rent, sequence=95,
                                             statutory=Statutory.PT)
        out = said("setup_payroll")
        # 2410 is someone's rent: kept as it is, nothing wired into it, PF payable seeded beside it.
        rent.refresh_from_db()
        self.assertEqual(rent.name, "Rent payable")
        self.assertIn("collision, for the accountant: account 2410 is 'Rent payable'", out)
        self.assertEqual(PayComponent.objects.get(code="PF").liability_account.code, "2410-IN")
        # 6000 under another name for the same thing is used as it is.
        self.assertEqual(PayComponent.objects.get(code="DA").expense_account, wages)
        basic.refresh_from_db()
        self.assertEqual((basic.name, basic.sequence), ("Basic pay", 5))
        # The plant's own professional tax stands; no second one is marked (setup_statutory needs exactly one).
        self.assertFalse(PayComponent.objects.filter(code="PT").exists())
        self.assertEqual(list(PayComponent.objects.filter(statutory=Statutory.PT)), [own_pt])
        self.assertIn("kept as it was: Professional tax: pay component PTWB is marked as it", out)
