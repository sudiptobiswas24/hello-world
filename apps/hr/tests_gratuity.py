"""
The gratuity provision and its payout on leaving (known limit 19).

Numbers worked in /home/user/work/calc_g39_grat/gratuity.py. Basic 12,000
and DA 3,000 a month, 15,000 of wages.

  the provision, 4.81% of 15,000 (15/26/12 = 4.8077%, to the hundredth):
    721.50 a month; June 2026's run for L and Q books 1,443.00 to 2470
  L  hired 1 Jan 2020, resigned 30 Jun 2026: 6 years 5 months, a part year
     of five months is not counted (s.4(2)): 6 years, 15/26 x 15,000 x 6 =
     51,923.08
  Q  hired 1 Jan 2023, died 30 Jun 2026: 3 years 5 months, under five but
     paid on death (s.4(1) proviso): 3 years, 25,961.54. Resigned instead,
     nothing is payable.

The provision clears: L's past service booked at 51,201.58 (51,923.08 less
June's 721.50), June's run, then the payout: 0.00 left. Had L's runs accrued
every month instead, 2470 would keep what the Act does not pay: 72 months at
721.50 are 51,948.00, 24.92 over (4.81 rounds 4.8077 up); 78 months, the five
months of the last part year included, 56,277.00, 4,353.92 over.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounting.models import (Account, AccountType, Currency, JournalEntry, JournalLine, Payment,
                                    PaymentDirection)
from apps.core.models import Company, Party, PartyRole, PartyRoleAssignment

from .gratuity import GratuityPayout, LeavingReason
from .models import Employee
from .payroll import ComponentBasis, ComponentKind, EmployeeCompensation, PayComponent, PayRun, Rounding

D = datetime.date
LEFT = D(2026, 6, 30)
OWED_L, OWED_Q = Decimal("51923.08"), Decimal("25961.54")


class GratuityTestCase(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        acc = lambda code, name, kind, **more: Account.objects.create(  # noqa: E731
            code=code, name=name, account_type=kind, **more)
        self.wages = acc("6000", "Wages", AccountType.EXPENSE)
        self.net_pay = acc("2300", "Net pay payable", AccountType.LIABILITY)
        self.bank = acc("1010", "Bank", AccountType.ASSET, holds_money=True)
        self.provision = acc("2470", "Gratuity provision", AccountType.LIABILITY)
        self.expense = acc("6120", "Gratuity", AccountType.EXPENSE)
        Company.objects.create(name="Deccan Polysacks", base_currency=self.inr, net_pay_account=self.net_pay)
        earning = lambda code, sequence: PayComponent.objects.create(  # noqa: E731
            code=code, name=code, kind=ComponentKind.EARNING, expense_account=self.wages, sequence=sequence,
            is_ordinary_wages=True)
        self.basic, self.da = earning("BASIC", 10), earning("DA", 20)
        self.gratuity = PayComponent.objects.create(
            code="GRATUITY", name="Gratuity provision", kind=ComponentKind.EMPLOYER_COST,
            basis=ComponentBasis.PERCENT_OF_GROSS, expense_account=self.expense, liability_account=self.provision,
            sequence=120, rounding=Rounding.PAISA)
        self.gratuity.base_components.set([self.basic, self.da])

    def person(self, code, hired, left=None):
        party = Party.objects.create(code=f"P-{code}", name=code)
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
        employee = Employee.objects.create(party=party, employee_number=code, hire_date=hired)
        for component, amount in ((self.basic, "12000"), (self.da, "3000"), (self.gratuity, "4.81")):
            EmployeeCompensation.objects.create(employee=employee, component=component, amount=Decimal(amount),
                                                effective_from=hired)
        if left:
            employee.terminate(left)
        return employee

    def june(self, *people):
        run = PayRun.objects.create(period_start=D(2026, 6, 1), period_end=LEFT, pay_date=LEFT)
        run.calculate(employees=list(people))
        run.post()
        return run

    def money(self, employee, amount, *, party=None, direction=PaymentDirection.DISBURSEMENT, account=None,
              day=LEFT, post=True):
        payment = Payment.objects.create(
            party=party or employee.party, direction=direction, payment_date=day, amount=Decimal(amount),
            currency=self.inr, bank_account=self.bank, counterpart_account=account or self.provision)
        if post:
            payment.post()
        return payment

    def past_service(self, amount):
        """The provision for service before the first run, booked as it is at go-live."""
        entry = JournalEntry.objects.create(date=D(2026, 5, 31), memo="Gratuity for service before June 2026")
        JournalLine.objects.create(entry=entry, account=self.expense, debit=Decimal(amount))
        JournalLine.objects.create(entry=entry, account=self.provision, credit=Decimal(amount))
        entry.post()

    def balance(self, account):
        sums = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            d=Sum("debit"), c=Sum("credit"))
        return (sums["d"] or Decimal("0")) - (sums["c"] or Decimal("0"))

    def pay(self, employee, payment, reason=LeavingReason.RESIGNATION):
        return GratuityPayout.objects.create(employee=employee, reason=reason, payment=payment)

    def refused(self, says, employee, payment, reason=LeavingReason.RESIGNATION):
        with self.assertRaises(ValidationError) as caught:
            self.pay(employee, payment, reason)
        self.assertIn(says, str(caught.exception))
        self.assertFalse(GratuityPayout.objects.filter(payment=payment).exists())


class GratuityRefusalTests(GratuityTestCase):
    """Each refusal on its own build: its own person or its own payment."""

    def test_someone_still_in_service_is_not_paid(self):
        staying = self.person("S", D(2020, 1, 1))
        self.june(staying)
        self.refused("has not left: gratuity is paid on leaving", staying, self.money(staying, OWED_L))
        going = self.person("G", D(2020, 1, 1))
        self.june(going)
        going.terminate(timezone.localdate() + datetime.timedelta(days=20))
        self.refused("has not left", going, self.money(going, OWED_L))

    def test_under_five_years_only_death_or_disablement_is_paid(self):
        q = self.person("Q", D(2023, 1, 1), left=LEFT)
        self.june(q)
        self.refused("served 3 complete year(s); gratuity is paid after 5 years", q, self.money(q, OWED_Q))
        self.refused("served 3 complete year(s)", q, self.money(q, OWED_Q), LeavingReason.RETIREMENT)
        disabled = self.person("Q2", D(2023, 1, 1), left=LEFT)
        self.june(disabled)
        payout = self.pay(disabled, self.money(disabled, OWED_Q), LeavingReason.DISABLEMENT)
        self.assertEqual((payout.years, payout.amount), (3, OWED_Q))

    def test_the_payment_is_the_money_paid_to_them_from_the_provision_at_what_is_owed(self):
        l, other = self.person("L", D(2020, 1, 1), left=LEFT), self.person("O", D(2020, 1, 1), left=LEFT)
        self.june(l, other)
        cases = [
            ("went to P-O - O, not L - L", dict(party=other.party)),
            ("Gratuity is money paid out, not received", dict(direction=PaymentDirection.RECEIPT)),
            ("no run accrued L - L's gratuity there", dict(account=self.net_pay)),
            ("not posted, or has been voided", dict(post=False)),
            ("is not paid on 2026-06-29: they left on 2026-06-30", dict(day=D(2026, 6, 29))),
        ]
        for says, how in cases:
            with self.subTest(says):
                self.refused(says, l, self.money(l, OWED_L, **how))
        with self.subTest("a paisa short"):
            self.refused("L - L is owed 51923.08: 6 year(s) at 15/26 of 15000.00. The payment is 51923.07.",
                         l, self.money(l, "51923.07"))
        with self.subTest("voided"):
            voided = self.money(l, OWED_L)
            voided.void()
            self.refused("not posted, or has been voided", l, voided)
        # None of those paid L: what is owed is paid once, at the figure.
        self.assertEqual(self.pay(l, self.money(l, OWED_L)).amount, OWED_L)

    def test_a_second_payout_is_refused_while_the_first_stands(self):
        l = self.person("L", D(2020, 1, 1), left=LEFT)
        self.june(l)
        first = self.pay(l, self.money(l, OWED_L))
        self.refused(f"{first} stands. Void it before paying L - L again.", l, self.money(l, OWED_L))

    def test_it_is_not_edited_or_deleted(self):
        l = self.person("L", D(2020, 1, 1), left=LEFT)
        self.june(l)
        payout = self.pay(l, self.money(l, OWED_L))
        payout.reason = LeavingReason.SUPERANNUATION
        with self.assertRaisesMessage(ValidationError, "is the record of what was paid; void it and pay again"):
            payout.save()
        with self.assertRaisesMessage(ValidationError, "is posted: void it instead"):
            GratuityPayout.objects.get(pk=payout.pk).delete()
        self.assertEqual(GratuityPayout.objects.get(pk=payout.pk).reason, LeavingReason.RESIGNATION)

    def test_the_leaving_or_start_date_is_not_moved_under_a_standing_payout(self):
        l = self.person("L", D(2020, 1, 1), left=LEFT)
        self.june(l)
        payout = self.pay(l, self.money(l, OWED_L))
        l = Employee.objects.get(pk=l.pk)
        l.termination_date = D(2026, 7, 31)
        with self.assertRaisesMessage(ValidationError, f"{payout} was worked out on L - L's service to 2026-06-30; "
                                                       "void it to change their leaving date"):
            l.save()
        l = Employee.objects.get(pk=l.pk)
        l.hire_date = D(2019, 1, 1)
        with self.assertRaisesMessage(ValidationError, "void it to change their start date"):
            l.save()
        payout.void()
        l = Employee.objects.get(pk=l.pk)
        l.termination_date = D(2026, 7, 31)
        l.save()
        self.assertEqual(Employee.objects.get(pk=l.pk).termination_date, D(2026, 7, 31))

    def test_the_void_is_not_dated_before_the_payment(self):
        l = self.person("L", D(2020, 1, 1), left=LEFT)
        self.june(l)
        payout = self.pay(l, self.money(l, OWED_L))
        with self.assertRaisesMessage(ValidationError, "A payment is not voided on 2026-06-29: it was made on 2026-06-30"):
            payout.void(on_date=D(2026, 6, 29))
        payout.refresh_from_db()
        self.assertIsNone(payout.voided_at)
        self.assertTrue(payout.stands())
        payout.void(on_date=LEFT)
        with self.assertRaisesMessage(ValidationError, "has already been voided"):
            GratuityPayout.objects.get(pk=payout.pk).void()


class GratuityPayoutTests(GratuityTestCase):
    def test_the_provision_accrues_721_50_a_month_on_15000(self):
        l, q = self.person("L", D(2020, 1, 1)), self.person("Q", D(2023, 1, 1))
        run = self.june(l, q)
        lines = [run.payslips.get(employee=who).lines.get(component=self.gratuity) for who in (l, q)]
        self.assertEqual([line.amount for line in lines], [Decimal("721.50"), Decimal("721.50")])
        self.assertEqual(self.balance(self.provision), Decimal("-1443.00"))
        self.assertEqual(self.balance(self.expense), Decimal("1443.00"))

    def test_a_leaver_of_six_years_is_paid_and_the_provision_clears(self):
        l = self.person("L", D(2020, 1, 1), left=LEFT)
        self.past_service("51201.58")
        self.june(l)
        self.assertEqual(self.balance(self.provision), -OWED_L)
        payment = self.money(l, OWED_L)
        payout = self.pay(l, payment)
        payout.refresh_from_db()
        # Each figure kept as it was worked out on the leaving day.
        self.assertEqual((payout.left_on, payout.years, payout.wages, payout.amount, payout.provision_account),
                         (LEFT, 6, Decimal("15000.00"), OWED_L, self.provision))
        self.assertEqual(self.balance(self.provision), Decimal("0.00"))
        self.assertEqual(self.balance(self.bank), -OWED_L)
        # The reverse: void, the payment voided with it, the provision restored, owed again.
        payout.void(on_date=D(2026, 7, 2))
        payout = GratuityPayout.objects.get(pk=payout.pk)
        self.assertFalse(payout.stands())
        self.assertTrue(Payment.objects.get(pk=payment.pk).is_voided())
        self.assertEqual(self.balance(self.provision), -OWED_L)
        self.assertEqual(self.balance(self.bank), Decimal("0.00"))
        again = self.pay(l, self.money(l, OWED_L, day=D(2026, 7, 2)))
        self.assertEqual(again.amount, OWED_L)
        self.assertEqual(self.balance(self.provision), Decimal("0.00"))

    def test_someone_who_died_at_three_years_five_months_is_paid_three_years(self):
        q = self.person("Q", D(2023, 1, 1), left=LEFT)
        self.june(q)
        payout = self.pay(q, self.money(q, OWED_Q), LeavingReason.DEATH)
        self.assertEqual((payout.years, payout.wages, payout.amount), (3, Decimal("15000.00"), OWED_Q))
        self.assertEqual(self.balance(self.provision), OWED_Q - Decimal("721.50"))

    def test_its_payment_voided_on_its_own_leaves_it_standing_for_nothing(self):
        l = self.person("L", D(2020, 1, 1), left=LEFT)
        self.june(l)
        payment = self.money(l, OWED_L)
        payout = self.pay(l, payment)
        Payment.objects.get(pk=payment.pk).void(on_date=D(2026, 7, 1))
        payout = GratuityPayout.objects.get(pk=payout.pk)
        self.assertFalse(payout.stands())
        self.assertEqual(self.balance(self.provision), Decimal("-721.50"))
        paid_again = self.pay(l, self.money(l, OWED_L, day=D(2026, 7, 1)))
        payout.void()  # its payment is voided already: nothing more is reversed
        self.assertEqual(self.balance(self.provision), Decimal("-721.50") + OWED_L)
        self.assertTrue(GratuityPayout.objects.get(pk=paid_again.pk).stands())


class GratuityPayoutApiTests(GratuityTestCase):
    def as_role(self, role):
        user = User.objects.create_user(f"{role.replace(' ', '-').lower()}-{User.objects.count()}")
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_controller_pays_and_voids_the_payroll_officer_reads_and_others_are_refused(self):
        l = self.person("L", D(2020, 1, 1), left=LEFT)
        self.june(l)
        controller, officer = self.as_role("Controller"), self.as_role("Payroll Officer")
        url = "/api/hr/gratuity-payouts/"
        body = {"employee": l.pk, "reason": "resignation", "payment": self.money(l, OWED_L).pk}
        refused = officer.post(url, body, format="json")
        self.assertEqual(refused.status_code, 403, refused.content)
        short = controller.post(url, {**body, "payment": self.money(l, "51923.07").pk}, format="json")
        self.assertEqual(short.status_code, 400, short.content)
        self.assertIn("L - L is owed 51923.08", str(short.json()))
        made = controller.post(url, {**body, "left_on": "2026-12-31", "amount": "1.00", "years": 40}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        row = made.json()
        self.assertEqual((row["number"], row["left_on"], row["years"], row["wages"], row["amount"], row["stands"]),
                         ("L", "2026-06-30", 6, "15000.00", "51923.08", True))
        read = officer.get(url)
        self.assertEqual(read.status_code, 200, read.content)
        rows = read.json()["results"] if isinstance(read.json(), dict) else read.json()
        self.assertEqual([(r["number"], r["amount"]) for r in rows], [("L", "51923.08")])
        self.assertEqual(officer.post(f"{url}{row['id']}/void/", {}, format="json").status_code, 403)
        for role in ("Line Manager", "Sales Rep"):
            self.assertEqual(self.as_role(role).get(url).status_code, 403, role)
        # No one is given a change or a delete of it (the permission is refused before the method).
        self.assertEqual(controller.patch(f"{url}{row['id']}/", {"reason": "death"}, format="json").status_code, 403)
        self.assertEqual(controller.delete(f"{url}{row['id']}/").status_code, 403)
        self.assertEqual(GratuityPayout.objects.get(pk=row["id"]).reason, LeavingReason.RESIGNATION)
        voided = controller.post(f"{url}{row['id']}/void/", {"on_date": "2026-07-02"}, format="json")
        self.assertEqual(voided.status_code, 200, voided.content)
        self.assertFalse(voided.json()["stands"])
        # June's accrual, and the short payment: refused as a payout, it is still money paid until it is voided.
        self.assertEqual(self.balance(self.provision), Decimal("-721.50") + Decimal("51923.07"))


class SetupGivesTheGratuityProvisionTests(TestCase):
    def said(self, command):
        from io import StringIO

        out = StringIO()
        call_command(command, stdout=out)
        return out.getvalue()

    def test_setup_seeds_the_provision_and_gives_it_to_everyone_in_service_paid_its_wages(self):
        from .statutory_check import statutory_gaps

        self.said("setup_indian_books")
        self.said("setup_payroll")
        gratuity = PayComponent.objects.get(code="GRATUITY")
        self.assertEqual((gratuity.kind, gratuity.basis, gratuity.rounding, gratuity.expense_account.code,
                          gratuity.expense_account.account_type, gratuity.liability_account.code,
                          gratuity.liability_account.account_type,
                          sorted(gratuity.base_components.values_list("code", flat=True))),
                         (ComponentKind.EMPLOYER_COST, ComponentBasis.PERCENT_OF_GROSS, Rounding.PAISA, "6120",
                          AccountType.EXPENSE, "2470", AccountType.LIABILITY, ["BASIC", "DA"]))
        today = timezone.localdate()
        basic = PayComponent.objects.get(code="BASIC")
        for code, left in (("S1", None), ("S2", None), ("GONE", today - datetime.timedelta(days=40))):
            party = Party.objects.create(code=f"P-{code}", name=code)
            PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
            person = Employee.objects.create(party=party, employee_number=code, hire_date=D(2024, 1, 1))
            EmployeeCompensation.objects.create(employee=person, component=basic, amount=Decimal("12000"),
                                                effective_from=D(2024, 1, 1))
            if left:
                person.terminate(left)
        gaps = lambda: dict((engine, missing) for engine, used, missing in statutory_gaps(today))["Gratuity"]  # noqa: E731
        self.assertTrue(gaps())
        self.assertIn("given: GRATUITY at 4.81% given to 2: S1, S2", self.said("setup_payroll"))
        given = sorted(EmployeeCompensation.objects.filter(component=gratuity).values_list(
            "employee__employee_number", "amount", "effective_from", "effective_to"))
        self.assertEqual(given, [("S1", Decimal("4.8100"), today.replace(day=1), None),
                                 ("S2", Decimal("4.8100"), today.replace(day=1), None)])
        self.assertFalse([line for line in gaps() if "not given" in line])
        # Run again, nobody is given it twice.
        self.assertNotIn("given: GRATUITY", self.said("setup_payroll"))
        self.assertEqual(EmployeeCompensation.objects.filter(component=gratuity).count(), 2)
