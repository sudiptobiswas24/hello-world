"""
Provident fund, ESI and professional tax, and paying them over.

Every percentage was taken of the whole taxable gross, with no ceiling,
no coverage limit, no rounding rule and no slab. A loom operator on
12,000 basic, 3,000 special allowance, 4,800 HRA and 2,500 overtime had
2,676 taken for PF instead of 1,800; a supervisor on 28,000 paid 210 of
ESI he is not covered for. And nothing said which month's PF had been
paid over, or that it was due on the 15th.

The figures (June 2026, worked in a separate script):

    A  basic 12,000, special 3,000, HRA 4,800, overtime 2,500 = 22,300
       PF 12% of min(15,000, 15,000) = 1,800, and 1,800 from the company
       covered for ESI (19,800 without overtime <= 21,000):
       0.75% of 22,300 = 167.25 -> 168 up; 3.25% = 724.75 -> 725
       PT (Maharashtra) 200; 300 in February
    B  basic 20,000, HRA 8,000 = 28,000
       PF on 15,000 = 1,800 and 1,800; no ESI; PT 200

    June: PF payable 7,200, ESI payable 893, PT payable 400.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase, TransactionTestCase, tag

from apps.accounting.models import (
    Account,
    AccountType,
    JournalLine,
    Payment,
    PaymentDirection,
)
from apps.core.models import Company, Currency, Party, PartyRole, PartyRoleAssignment

from .models import Employee
from .payroll import (
    ComponentBasis,
    ComponentKind,
    EmployeeCompensation,
    PayComponent,
    PayComponentSlab,
    PayRun,
    Rounding,
    StatutoryRemittance,
    statutory_liabilities,
)

JUNE = (datetime.date(2026, 6, 1), datetime.date(2026, 6, 30))
JULY_15 = datetime.date(2026, 7, 15)


class StatutoryTestCase(TestCase):
    def setUp(self):
        self.inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        acc = lambda c, n, t, **more: Account.objects.create(code=c, name=n, account_type=t, **more)
        self.wages = acc("6000", "Wages", AccountType.EXPENSE)
        self.employer = acc("6100", "Employer contributions", AccountType.EXPENSE)
        self.net_pay = acc("2300", "Net pay payable", AccountType.LIABILITY)
        self.pf_payable = acc("2310", "PF payable", AccountType.LIABILITY)
        self.esi_payable = acc("2320", "ESI payable", AccountType.LIABILITY)
        self.pt_payable = acc("2330", "PT payable", AccountType.LIABILITY)
        self.bank = acc("1010", "Bank", AccountType.ASSET, holds_money=True)
        Company.objects.create(name="Deccan Polysacks", base_currency=self.inr,
                               net_pay_account=self.net_pay)
        earning = lambda code, seq: PayComponent.objects.create(
            code=code, name=code, kind=ComponentKind.EARNING, expense_account=self.wages,
            sequence=seq)
        self.basic, self.special = earning("BASIC", 10), earning("SPL", 20)
        self.hra, self.ot = earning("HRA", 30), earning("OT", 40)
        self.pf = self.percent("PF", 50, ComponentKind.DEDUCTION, self.pf_payable,
                               base=[self.basic, self.special], ceiling="15000",
                               rounding=Rounding.RUPEE)
        self.pf_er = self.percent("PF-ER", 60, ComponentKind.EMPLOYER_COST, self.pf_payable,
                                  base=[self.basic, self.special], ceiling="15000",
                                  rounding=Rounding.RUPEE)
        everything = [self.basic, self.special, self.hra, self.ot]
        self.esi = self.percent("ESI", 70, ComponentKind.DEDUCTION, self.esi_payable,
                                base=everything, rounding=Rounding.RUPEE_UP,
                                coverage=everything[:3], coverage_ceiling="21000")
        self.esi_er = self.percent("ESI-ER", 80, ComponentKind.EMPLOYER_COST, self.esi_payable,
                                   base=everything, rounding=Rounding.RUPEE_UP,
                                   coverage=everything[:3], coverage_ceiling="21000")
        self.pt = PayComponent.objects.create(
            code="PT", name="Professional tax", kind=ComponentKind.DEDUCTION,
            basis=ComponentBasis.SLAB, liability_account=self.pt_payable, sequence=90)
        for above, up_to, amount, month in [("7500", "10000", "175", None),
                                            ("10000", None, "200", None),
                                            ("10000", None, "300", 2)]:
            PayComponentSlab.objects.create(
                component=self.pt, above=Decimal(above),
                up_to=Decimal(up_to) if up_to else None, amount=Decimal(amount), month=month)
        self.a = self.person("A", basic="12000", special="3000", hra="4800", ot="2500")
        self.b = self.person("B", basic="20000", hra="8000")

    def percent(self, code, sequence, kind, liability, base, rounding, ceiling=None,
                coverage=(), coverage_ceiling=None):
        component = PayComponent.objects.create(
            code=code, name=code, kind=kind, basis=ComponentBasis.PERCENT_OF_GROSS,
            liability_account=liability, sequence=sequence, rounding=rounding,
            base_ceiling=Decimal(ceiling) if ceiling else None,
            coverage_ceiling=Decimal(coverage_ceiling) if coverage_ceiling else None,
            coverage_period_months=6 if coverage_ceiling else 1, remit_by_day=15,
            expense_account=self.employer if kind == ComponentKind.EMPLOYER_COST else None,
        )
        component.base_components.set(base)
        component.coverage_components.set(coverage)
        return component

    def person(self, code, **pay):
        party = Party.objects.create(code=f"P-{code}", name=code)
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
        employee = Employee.objects.create(party=party, employee_number=code,
                                           hire_date=datetime.date(2020, 1, 1))
        rates = {"basic": self.basic, "special": self.special, "hra": self.hra, "ot": self.ot}
        for name, amount in pay.items():
            self.paid(employee, rates[name], amount)
        for component, rate in [(self.pf, "12"), (self.pf_er, "12"), (self.esi, "0.75"),
                                (self.esi_er, "3.25"), (self.pt, "1")]:
            self.paid(employee, component, rate)
        return employee

    def paid(self, employee, component, amount, since=datetime.date(2020, 1, 1)):
        return EmployeeCompensation.objects.create(
            employee=employee, component=component, amount=Decimal(amount),
            effective_from=since)

    def run_for(self, start, end, post=True):
        run = PayRun.objects.create(period_start=start, period_end=end, pay_date=end)
        run.calculate()
        if post:
            run.post()
        return run

    def lines(self, run, employee):
        slip = run.payslips.get(employee=employee)
        return {line.component.code: line.amount for line in slip.lines.all()
                if line.kind != ComponentKind.EARNING}

    def balance(self, account):
        return sum((l.debit - l.credit for l in
                    JournalLine.objects.filter(account=account, entry__posted=True)),
                   Decimal("0"))

    def remit(self, account, amount, period=JUNE[0], **overrides):
        fields = {
            "party": Party.objects.get_or_create(code="EPFO", defaults={"name": "EPFO"})[0],
            "direction": PaymentDirection.DISBURSEMENT, "payment_date": JULY_15,
            "amount": Decimal(amount), "currency": self.inr, "bank_account": self.bank,
            "counterpart_account": account,
        }
        post = overrides.pop("post", True)
        fields.update(overrides)
        payment = Payment.objects.create(**fields)
        if post:
            payment.post()
        return StatutoryRemittance.objects.create(
            payment=payment, liability_account=account, period=period, amount=Decimal(amount))


class WhatIsTakenTests(StatutoryTestCase):
    def test_the_operator(self):
        run = self.run_for(*JUNE, post=False)
        self.assertEqual(self.lines(run, self.a), {
            "PF": Decimal("1800.00"), "PF-ER": Decimal("1800.00"), "ESI": Decimal("168.00"),
            "ESI-ER": Decimal("725.00"), "PT": Decimal("200.00")})

    def test_the_supervisor_pf_capped_and_no_esi(self):
        run = self.run_for(*JUNE, post=False)
        self.assertEqual(self.lines(run, self.b), {
            "PF": Decimal("1800.00"), "PF-ER": Decimal("1800.00"), "PT": Decimal("200.00")})

    def test_february_professional_tax(self):
        run = self.run_for(datetime.date(2027, 2, 1), datetime.date(2027, 2, 28), post=False)
        self.assertEqual(self.lines(run, self.a)["PT"], Decimal("300.00"))

    def test_a_base_component_must_come_before_it(self):
        self.pf.base_components.add(self.pt)
        with self.assertRaisesMessage(ValidationError, "do not come before it"):
            self.run_for(*JUNE, post=False)


class CoverageHoldsForThePeriodTests(StatutoryTestCase):
    """
    A's basic rises to 14,000 in July: 21,800 without overtime, over the
    limit. Covered at the start of April-September, A stays covered to
    September (0.75% of 24,300 = 182.25 -> 183) and drops out in October.
    """

    def setUp(self):
        super().setUp()
        self.run_for(*JUNE)
        old = EmployeeCompensation.objects.get(employee=self.a, component=self.basic)
        old.effective_to = datetime.date(2026, 6, 30)
        old.save()
        self.paid(self.a, self.basic, "14000", since=datetime.date(2026, 7, 1))

    def test_still_covered_in_july(self):
        run = self.run_for(datetime.date(2026, 7, 1), datetime.date(2026, 7, 31), post=False)
        self.assertEqual(self.lines(run, self.a)["ESI"], Decimal("183.00"))

    def test_out_from_october(self):
        run = self.run_for(datetime.date(2026, 10, 1), datetime.date(2026, 10, 31), post=False)
        self.assertNotIn("ESI", self.lines(run, self.a))


class PayingItOverTests(StatutoryTestCase):
    def setUp(self):
        super().setUp()
        self.june = self.run_for(*JUNE)

    def report(self, as_of=datetime.date(2026, 7, 31)):
        return {row["account"].code: (row["deducted"], row["remitted"], row["outstanding"],
                                      row["due_date"], row["overdue"])
                for row in statutory_liabilities(as_of)}

    def test_what_june_owes_and_when(self):
        self.assertEqual(self.report(datetime.date(2026, 7, 1)), {
            "2310": (Decimal("7200.00"), Decimal("0"), Decimal("7200.00"), JULY_15, False),
            "2320": (Decimal("893.00"), Decimal("0"), Decimal("893.00"), JULY_15, False),
            "2330": (Decimal("400.00"), Decimal("0"), Decimal("400.00"), None, False),
        })

    def test_paid_over_in_part_and_late(self):
        self.remit(self.pf_payable, "7200")
        self.remit(self.esi_payable, "800")
        report = self.report()
        self.assertEqual((report["2310"][2:], report["2320"][2:]),
                         ((Decimal("0.00"), JULY_15, False),
                          (Decimal("93.00"), JULY_15, True)))
        self.assertEqual((self.balance(self.pf_payable), self.balance(self.esi_payable)),
                         (Decimal("0.00"), Decimal("-93.00")))

    def test_a_bounced_payment_pays_nothing_over(self):
        remittance = self.remit(self.pf_payable, "7200")
        remittance.payment.void(on_date=datetime.date(2026, 7, 20))
        self.assertEqual(self.report()["2310"][2], Decimal("7200.00"))

    def test_a_voided_run_owes_nothing(self):
        self.june.void(on_date=datetime.date(2026, 7, 1))
        self.assertEqual(self.report(), {})

    def test_a_run_is_not_voided_under_its_dues_paid_over(self):
        # Voided after June's PF went to the EPFO, the run left 7,200 paid over against nothing
        # deducted, PF payable in debit by it, and June gone from the report.
        remittance = self.remit(self.pf_payable, "7200")
        with self.assertRaisesMessage(ValidationError, "for June 2026 has been paid over; voided, this run "
                                                       "would leave 0.00 deducted for it"):
            self.june.void(on_date=datetime.date(2026, 7, 20))
        self.june.refresh_from_db()
        self.assertFalse(self.june.is_voided())

        remittance.payment.void()  # it bounced: nothing was paid over after all
        self.june.void(on_date=datetime.date(2026, 7, 20))
        self.assertEqual((self.report(), self.balance(self.pf_payable)), ({}, Decimal("0.00")))

    def test_as_of_a_day_before_it_was_paid_over_june_is_still_owed(self):
        self.remit(self.pf_payable, "7200")  # paid on 15 July
        self.assertEqual(self.report(datetime.date(2026, 7, 10))["2310"][1:3], (Decimal("0"), Decimal("7200.00")))
        self.assertEqual(self.report(JULY_15)["2310"][1:3], (Decimal("7200.00"), Decimal("0.00")))

    def test_a_payment_that_bounced_had_paid_it_over_until_it_was_returned(self):
        remittance = self.remit(self.pf_payable, "7200")
        remittance.payment.void(on_date=datetime.date(2026, 7, 20))
        self.assertEqual((self.report(datetime.date(2026, 7, 17))["2310"][2],
                          self.report(datetime.date(2026, 7, 20))["2310"][2]),
                         (Decimal("0.00"), Decimal("7200.00")))

    def test_a_remittance_whose_payment_stands_is_not_deleted(self):
        # Deleted, the 7,200 stayed paid over in the ledger, the report said June was owed it
        # again, and the run could be voided under it.
        remittance = self.remit(self.pf_payable, "7200")
        with self.assertRaisesMessage(ValidationError, "it stays while the payment stands"):
            remittance.delete()
        with self.assertRaisesMessage(ValidationError, "has been paid over"):
            self.june.void(on_date=datetime.date(2026, 7, 20))
        self.assertEqual((self.report()["2310"][2], self.balance(self.pf_payable)),
                         (Decimal("0.00"), Decimal("0.00")))

    def test_the_remittance_of_a_payment_that_bounced_is_deleted(self):
        remittance = self.remit(self.pf_payable, "7200")
        remittance.payment.void(on_date=JULY_15)
        remittance.delete()
        self.june.void(on_date=datetime.date(2026, 7, 20))
        self.assertEqual((self.report(), self.balance(self.pf_payable)), ({}, Decimal("0.00")))

    def test_the_account_it_was_owed_into_is_kept(self):
        other = Account.objects.create(code="2311", name="PF payable (new)",
                                       account_type=AccountType.LIABILITY)
        self.pf_er.liability_account = other
        self.pf_er.save()
        self.assertEqual(self.report()["2310"][0], Decimal("7200.00"))
        self.assertNotIn("2311", self.report())


class OnlyAPaymentOverSettlesTests(StatutoryTestCase):
    """The questions a payslip asks of its payment, asked of a remittance."""

    def setUp(self):
        super().setUp()
        self.run_for(*JUNE)

    def test_a_draft_payment(self):
        with self.assertRaisesMessage(ValidationError, "not been posted"):
            self.remit(self.pf_payable, "100", post=False)

    def test_money_received(self):
        with self.assertRaisesMessage(ValidationError, "money paid out"):
            self.remit(self.pf_payable, "100", direction=PaymentDirection.RECEIPT)

    def test_a_payment_against_another_account(self):
        payment = Payment.objects.create(
            party=Party.objects.create(code="ESIC", name="ESIC"),
            direction=PaymentDirection.DISBURSEMENT, payment_date=JULY_15,
            amount=Decimal("100"), currency=self.inr, bank_account=self.bank,
            counterpart_account=self.esi_payable)
        payment.post()
        with self.assertRaisesMessage(ValidationError, "does not clear this liability"):
            StatutoryRemittance.objects.create(payment=payment, liability_account=self.pf_payable,
                                               period=JUNE[0], amount=Decimal("100"))

    def test_more_than_was_deducted(self):
        with self.assertRaisesMessage(ValidationError, "more than was deducted"):
            self.remit(self.esi_payable, "900")

    def test_a_month_with_nothing_owed(self):
        with self.assertRaisesMessage(ValidationError, "more than was deducted"):
            self.remit(self.pf_payable, "100", period=datetime.date(2026, 5, 1))

    def test_one_payment_spent_twice(self):
        remittance = self.remit(self.pf_payable, "3600")
        with self.assertRaisesMessage(ValidationError, "already accounted"):
            StatutoryRemittance.objects.create(
                payment=remittance.payment, liability_account=self.pf_payable,
                period=JUNE[0], amount=Decimal("3600"))

    def test_not_edited(self):
        remittance = self.remit(self.pf_payable, "3600")
        remittance.amount = Decimal("3000")
        with self.assertRaisesMessage(ValidationError, "not edited"):
            remittance.save()


class PayrollOverTheApiTests(StatutoryTestCase):
    """The Payroll Officer works it out; the Controller posts it and pays it over."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        self.clients = {}
        for role in ("Payroll Officer", "Controller", "HR Admin"):
            user = User.objects.create_user(role.replace(" ", "-").lower())
            user.groups.add(Group.objects.get(name=role))
            client = APIClient()
            client.force_authenticate(user)
            self.clients[role] = client

    def ok(self, response, status=200):
        self.assertEqual(response.status_code, status, response.content[:400])
        return response.json()

    def test_worked_out_posted_and_paid_over(self):
        officer, controller = self.clients["Payroll Officer"], self.clients["Controller"]
        run = self.ok(officer.post("/api/hr/pay-runs/", {
            "period_start": "2026-06-01", "period_end": "2026-06-30",
            "pay_date": "2026-06-30"}, format="json"), 201)
        self.ok(officer.post(f"/api/hr/pay-runs/{run['id']}/calculate/", {}, format="json"))
        self.assertEqual(officer.post(f"/api/hr/pay-runs/{run['id']}/post/").status_code, 403)
        self.ok(controller.post(f"/api/hr/pay-runs/{run['id']}/post/"))

        payment = Payment.objects.create(
            party=Party.objects.create(code="ESIC", name="ESIC"),
            direction=PaymentDirection.DISBURSEMENT, payment_date=JULY_15,
            amount=Decimal("893"), currency=self.inr, bank_account=self.bank,
            counterpart_account=self.esi_payable)
        payment.post()
        self.assertEqual(officer.post("/api/hr/statutory-remittances/", {
            "payment": payment.pk, "liability_account": self.esi_payable.pk,
            "period": "2026-06-01", "amount": "893.00"}, format="json").status_code, 403)
        self.ok(controller.post("/api/hr/statutory-remittances/", {
            "payment": payment.pk, "liability_account": self.esi_payable.pk,
            "period": "2026-06-15", "amount": "893.00"}, format="json"), 201)
        rows = {row["account_code"]: row for row in self.ok(
            controller.get("/api/hr/statutory-liabilities/?as_of=2026-07-31"))}
        self.assertEqual(
            {code: (row["period"], row["deducted"], row["outstanding"], row["overdue"])
             for code, row in rows.items()},
            {"2310": ("2026-06-01", "7200.00", "7200.00", True),
             "2320": ("2026-06-01", "893.00", "0.00", False),
             "2330": ("2026-06-01", "400.00", "400.00", False)})

    def test_the_controller_deletes_a_remittance_only_once_its_payment_is_voided(self):
        self.run_for(*JUNE)
        remittance = self.remit(self.pf_payable, "7200")
        controller = self.clients["Controller"]
        refused = controller.delete(f"/api/hr/statutory-remittances/{remittance.pk}/")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("it stays while the payment stands", refused.content.decode())
        remittance.payment.void(on_date=JULY_15)
        self.assertEqual(controller.delete(f"/api/hr/statutory-remittances/{remittance.pk}/").status_code, 204)

    def test_a_refused_remittance_is_a_400(self):
        self.run_for(*JUNE)
        payment = Payment.objects.create(
            party=Party.objects.create(code="ESIC", name="ESIC"),
            direction=PaymentDirection.DISBURSEMENT, payment_date=JULY_15,
            amount=Decimal("1000"), currency=self.inr, bank_account=self.bank,
            counterpart_account=self.esi_payable)
        payment.post()
        refused = self.clients["Controller"].post("/api/hr/statutory-remittances/", {
            "payment": payment.pk, "liability_account": self.esi_payable.pk,
            "period": "2026-06-01", "amount": "1000.00"}, format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("more than was deducted", refused.content.decode())

    def test_components_are_set_up_with_their_rules(self):
        officer = self.clients["Payroll Officer"]
        made = self.ok(officer.post("/api/hr/pay-components/", {
            "code": "LWF", "name": "Labour welfare fund", "kind": "deduction",
            "basis": "slab", "liability_account": self.pt_payable.pk,
            "base_components": [self.basic.pk], "rounding": "rupee", "remit_by_day": 15,
            "sequence": 95}, format="json"), 201)
        self.ok(officer.post("/api/hr/pay-component-slabs/", {
            "component": made["id"], "above": "0", "amount": "12", "month": 6},
            format="json"), 201)
        refused = officer.post("/api/hr/pay-components/", {
            "code": "BAD", "name": "Bad", "kind": "deduction", "basis": "percent",
            "liability_account": self.pt_payable.pk, "coverage_period_months": 5},
            format="json")
        self.assertEqual(refused.status_code, 400)
        refused = officer.post("/api/hr/pay-component-slabs/", {
            "component": made["id"], "above": "100", "up_to": "50", "amount": "1"},
            format="json")
        self.assertEqual(refused.status_code, 400)

    def test_the_hr_admin_does_not_see_pay(self):
        self.assertEqual(
            self.clients["HR Admin"].get("/api/hr/statutory-liabilities/").status_code, 403)
        self.assertEqual(self.clients["HR Admin"].get("/api/hr/payslips/").status_code, 403)


class EdgesTests(StatutoryTestCase):
    def test_pf_to_the_nearest_rupee(self):
        """12% of 12,345 is 1,481.40: deducted as 1,481."""
        c = self.person("C", basic="12345")
        run = self.run_for(*JUNE, post=False)
        self.assertEqual(self.lines(run, c)["PF"], Decimal("1481.00"))

    def test_on_a_slab_edge_the_lower_band(self):
        """7,500 exactly is in the nil band; 'above 7,500' starts after it."""
        d = self.person("D", basic="7500")
        run = self.run_for(*JUNE, post=False)
        self.assertNotIn("PT", self.lines(run, d))

    def test_the_earliest_due_day_on_an_account_wins(self):
        self.pf_er.remit_by_day = 20
        self.pf_er.save()
        self.run_for(*JUNE)
        (row,) = [row for row in statutory_liabilities(datetime.date(2026, 7, 31))
                  if row["account"] == self.pf_payable]
        self.assertEqual(row["due_date"], JULY_15)


# Slow: it unwinds every later migration and replays them.
@tag("migration")
class LinesAlreadyPostedMigrate(TransactionTestCase):
    """
    Lines posted before the liability account was frozen on them: a
    deduction was owed into the account it landed on, an employer
    contribution into its component's liability account. A draft run's
    lines owe nothing yet and are left alone.
    """

    before = [("hr", "0008_paycomponent_measure_payslipline_quantity_and_more")]
    after = [("hr", "0009_statutory_contributions")]

    def test_posted_lines_learn_what_they_were_owed_into(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        apps = executor.loader.project_state(self.before).apps
        # Only payroll is taken back. What it points at is made as it stands now and pointed at by
        # id: a party made by core's old model missed the columns core has gained since.
        now = executor.loader.project_state(executor.loader.graph.leaf_nodes()).apps
        account = now.get_model("accounting", "Account")
        wages = account.objects.create(code="6000", name="Wages", account_type="expense")
        pf = account.objects.create(code="2310", name="PF", account_type="liability")
        component = apps.get_model("hr", "PayComponent")
        deduction = component.objects.create(code="PF", name="PF", kind="deduction",
                                             liability_account_id=pf.pk)
        employer = component.objects.create(code="PF-ER", name="PF ER", kind="employer_cost",
                                            expense_account_id=wages.pk, liability_account_id=pf.pk)
        party = now.get_model("core", "Party").objects.create(code="E", name="E")
        person = apps.get_model("hr", "Employee").objects.create(
            party_id=party.pk, employee_number="E", hire_date=datetime.date(2020, 1, 1))
        run = apps.get_model("hr", "PayRun")
        slip = apps.get_model("hr", "Payslip")
        line = apps.get_model("hr", "PayslipLine")
        for status in ("posted", "draft"):
            made = run.objects.create(period_start=JUNE[0], period_end=JUNE[1],
                                      pay_date=JUNE[1], status=status)
            payslip = slip.objects.create(employee=person, run=made)
            line.objects.create(payslip=payslip, component=deduction, kind="deduction",
                                basis="percent", description="PF", rate=12, amount=1800,
                                posted_account_id=pf.pk if status == "posted" else None)
            line.objects.create(payslip=payslip, component=employer, kind="employer_cost",
                                basis="percent", description="PF ER", rate=12, amount=1800,
                                posted_account_id=wages.pk if status == "posted" else None)

        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        line = executor.loader.project_state(self.after).apps.get_model("hr", "PayslipLine")
        self.assertEqual(
            sorted((row.payslip.run.status, row.kind, row.posted_liability_account_id)
                   for row in line.objects.all()),
            [("draft", "deduction", None), ("draft", "employer_cost", None),
             ("posted", "deduction", pf.pk), ("posted", "employer_cost", pf.pk)])
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


class SlabBandsDoNotOverlapTests(StatutoryTestCase):
    """Overlapping bands were saved and the first one silently won."""

    def test_an_overlapping_band_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "overlaps"):
            PayComponentSlab.objects.create(component=self.pt, above=Decimal("9000"),
                                            up_to=Decimal("12000"), amount=Decimal("150"))
        with self.assertRaisesMessage(ValidationError, "overlaps"):
            PayComponentSlab.objects.create(component=self.pt, above=Decimal("0"), amount=Decimal("0"))

    def test_touching_bands_and_a_months_own_band_are_not_overlaps(self):
        PayComponentSlab.objects.create(component=self.pt, above=Decimal("0"),
                                        up_to=Decimal("7500"), amount=Decimal("0"))
        PayComponentSlab.objects.create(component=self.pt, above=Decimal("7500"),
                                        up_to=Decimal("10000"), amount=Decimal("190"), month=3)
        self.assertEqual(self.pt.slab_for(Decimal("9000"), 3), Decimal("190"))
        self.assertEqual(self.pt.slab_for(Decimal("9000"), 4), Decimal("175"))
        self.assertEqual(self.pt.slab_for(Decimal("5000"), 4), Decimal("0"))
