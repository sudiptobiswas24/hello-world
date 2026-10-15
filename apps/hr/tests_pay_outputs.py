"""
Payroll's go-live outputs (g21 items 1 to 4): the employee's bank and
return details, the run's bank file and its one payment, the payslip on
paper and the employee's own payslips, and advances recovered by the runs.

Bank file, June 2026: E1 12,000.00, E2 8,450.50, E3 3,375.00, a total of
23,825.50; E2's transfer comes back, and 8,450.50 is owed again.

Advance of 5,000 given on 20 May, 2,000 a run from June: June, July and
August take 2,000, 2,000 and 1,000. A leaver earning 3,000 with 360 of
tax (12%) has half, 1,500.00, less 360.00 = 1,140.00 of room under the
deduction cap: 1,140.00 is taken and 3,860.00 stays owed on the advance.
(worked in scratchpad/calc_fix_people/scenarios.py)
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType, Payment, PaymentDirection

from .advances import SalaryAdvance
from .bank_file import SalaryTransferFile
from .pay_details import EmployeePayDetails
from .models import EmploymentStatus
from .payroll import ComponentBasis, ComponentKind, PayComponent, PayslipLine
from .tests_payroll import PayrollTestCase

D = datetime.date
JUNE = (D(2026, 6, 1), D(2026, 6, 30))


class OutputsTestCase(PayrollTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.advances = Account.objects.create(code="1300", name="Employee advances", account_type=AccountType.ASSET)
        self.recovery = PayComponent.objects.create(
            code="ADV", name="Advance recovered", kind=ComponentKind.DEDUCTION, basis=ComponentBasis.FIXED,
            liability_account=self.advances, sequence=80)

    def as_role(self, role, employee=None):
        user = User.objects.create_user(f"{role.replace(' ', '_')}-{User.objects.count()}")
        user.groups.add(Group.objects.get(name=role))
        if employee is not None:
            employee.user = user
            employee.save()
        client = APIClient()
        client.force_authenticate(user)
        return client

    def banked(self, person):
        return EmployeePayDetails.objects.create(employee=person, account_holder=f"{person.employee_number} Das",
                                                 account_number=f"3001{person.pk:08d}", ifsc="SBIN0001234")

    def money(self, party, amount, counterpart=None, direction=PaymentDirection.DISBURSEMENT, day=JUNE[1], post=True):
        payment = Payment.objects.create(party=party, direction=direction, payment_date=day, amount=Decimal(amount),
                                         currency=self.usd, bank_account=self.bank,
                                         counterpart_account=counterpart or self.net_pay)
        if post:
            payment.post()
        return payment

    def june(self, *people):
        run = self.pay_run(*JUNE, pay_date=JUNE[1])
        run.calculate(employees=list(people) or None)
        run.post()
        return run


class PayDetailsTests(OutputsTestCase):
    def test_the_shapes_are_refused(self):
        person = self.employee("E1")
        for fields, said in [({"ifsc": "SBIN012345"}, "is not an IFSC"), ({"ifsc": "SBIN1234567"}, "is not an IFSC"),
                             ({"pan": "ABCDE1234"}, "is not a PAN"), ({"account_number": "12345"}, "nine to eighteen"),
                             ({"account_holder": ""}, "all three")]:
            values = {"account_holder": "E1 Das", "account_number": "300100001111", "ifsc": "SBIN0001234", **fields}
            with self.subTest(fields), self.assertRaisesMessage(ValidationError, said):
                EmployeePayDetails.objects.create(employee=person, **values)
        kept = EmployeePayDetails.objects.create(employee=person, account_holder="E1 Das", account_number="3001 0000 1111",
                                                 ifsc="sbin0001234", pan="abcde1234f")
        self.assertEqual((kept.account_number, kept.ifsc, kept.pan), ("300100001111", "SBIN0001234", "ABCDE1234F"))

    def test_only_those_who_run_payroll_read_them(self):
        person = self.employee("E1")
        self.banked(person)
        for role in ("Payroll Officer", "Controller"):
            self.assertEqual(self.as_role(role).get("/api/hr/pay-details/").status_code, 200, role)
        for role in ("HR Admin", "Line Manager", "Employee Self Service"):
            self.assertEqual(self.as_role(role).get("/api/hr/pay-details/").status_code, 403, role)
        manager = self.as_role("Line Manager")
        self.assertNotIn(b"300100", manager.get(f"/api/hr/employees/{person.pk}/").content)

    def test_entered_by_payroll_and_refused_in_words(self):
        person = self.employee("E1")
        officer = self.as_role("Payroll Officer")
        refused = officer.post("/api/hr/pay-details/", {"employee": person.pk, "account_holder": "E1 Das",
                                                        "account_number": "300100001111", "ifsc": "SBIN1234"},
                               format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("is not an IFSC", refused.content.decode())
        made = officer.post("/api/hr/pay-details/", {"employee": person.pk, "account_holder": "E1 Das",
                                                     "account_number": "300100001111", "ifsc": "SBIN0001234"},
                            format="json")
        self.assertEqual(made.status_code, 201, made.content)


class BankFileTests(OutputsTestCase):
    def setUp(self):
        super().setUp()
        self.people = [self.employee(code) for code in ("E1", "E2", "E3")]
        for person, amount in zip(self.people, ("12000", "8450.50", "3375")):
            self.pay(person, self.salary, amount)
        self.paid_run = self.june()

    def test_refused_while_anybody_lacks_bank_details_naming_them(self):
        self.banked(self.people[0])
        with self.assertRaisesMessage(ValidationError, "No bank account on E2, E3"):
            SalaryTransferFile.prepare(self.paid_run)

    def test_one_row_a_slip_the_total_and_one_payment_that_settles_them(self):
        for person in self.people:
            self.banked(person)
        made = SalaryTransferFile.prepare(self.paid_run)
        lines = made.text.splitlines()
        self.assertEqual(lines[0], "Beneficiary name,Account number,IFSC,Amount,Narration")
        self.assertEqual(lines[2], f"E2 Das,3001{self.people[1].pk:08d},SBIN0001234,8450.50,Salary Jun 2026")
        self.assertEqual(lines[-1], "Total,,,23825.50,3 transfers")
        self.assertEqual(made.total, Decimal("23825.50"))
        bank = self.people[0].party
        for wrong, said in [(self.money(bank, "23825.00"), "totals 23825.50"),
                            (self.money(bank, "23825.50", direction=PaymentDirection.RECEIPT), "not money received"),
                            (self.money(bank, "23825.50", counterpart=self.wages), "not net pay payable")]:
            with self.subTest(said), self.assertRaisesMessage(ValidationError, said):
                made.mark_paid(wrong)
            wrong.void(on_date=D(2026, 7, 1))
        paid = self.money(bank, "23825.50")
        made.mark_paid(paid)
        self.assertEqual(self.paid_run.unpaid_net(), Decimal("0"))
        self.assertEqual(self.balance(self.net_pay), Decimal("0.00"))
        with self.assertRaisesMessage(ValidationError, "already been paid"):
            self.paid_run.void(on_date=D(2026, 7, 1))
        # One transfer comes back: that slip alone is owed again.
        row = made.rows.get(payslip__employee=self.people[1])
        short = self.money(self.people[1].party, "8450.00", direction=PaymentDirection.RECEIPT)
        with self.assertRaisesMessage(ValidationError, "The receipt is 8450.00"):
            row.return_transfer(short)
        short.void(on_date=D(2026, 7, 1))
        receipt = self.money(self.people[1].party, "8450.50", direction=PaymentDirection.RECEIPT)
        row.return_transfer(receipt)
        self.assertEqual([slip.is_paid() for slip in self.paid_run.payslips.order_by("employee__employee_number")],
                         [True, False, True])
        self.assertEqual(self.paid_run.unpaid_net(), Decimal("8450.50"))
        self.assertEqual(self.balance(self.net_pay), Decimal("-8450.50"))
        twice = self.money(self.people[1].party, "8450.50", direction=PaymentDirection.RECEIPT)
        with self.assertRaisesMessage(ValidationError, "already been returned"):
            row.return_transfer(twice)
        twice.void(on_date=D(2026, 7, 1))
        # Written again, the file holds only what is still owed.
        again = SalaryTransferFile.prepare(self.paid_run)
        self.assertEqual([r.payslip.employee.employee_number for r in again.rows.all()], ["E2"])
        # The file's payment is not voided under the return (review RP2).
        with self.assertRaisesMessage(ValidationError, "Void those receipts first"):
            paid.void(on_date=D(2026, 7, 1))
        # The receipt voided: the transfer went through after all.
        again.delete()
        receipt.void(on_date=D(2026, 7, 1))
        self.assertTrue(self.paid_run.payslips.get(employee=self.people[1]).is_paid())
        # The payment voided: every slip it paid is owed again.
        paid.void(on_date=D(2026, 7, 1))
        self.assertEqual(self.paid_run.unpaid_net(), Decimal("23825.50"))

    def test_the_run_list_asks_whether_each_slip_is_paid_in_one_query_for_them_all(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def paid_by_a_file(run):
            for person in run.payslips.values_list("employee", flat=True):
                EmployeePayDetails.objects.get_or_create(employee_id=person, defaults={
                    "account_holder": "Das", "account_number": f"3001{person:08d}", "ifsc": "SBIN0001234"})
            made = SalaryTransferFile.prepare(run)
            made.mark_paid(self.money(self.people[0].party, made.total, day=run.pay_date))

        controller = self.as_role("Controller")
        paid_by_a_file(self.paid_run)

        def reads(run):
            with CaptureQueriesContext(connection) as seen:
                self.assertEqual(controller.get(f"/api/hr/pay-runs/{run.pk}/").status_code, 200)
            return len(seen)

        for code in ("E4", "E5", "E6"):
            self.pay(self.employee(code), self.salary, "1000")
        july = self.pay_run(D(2026, 7, 1), D(2026, 7, 31), pay_date=D(2026, 7, 31))
        july.calculate()
        july.post()
        paid_by_a_file(july)
        reads(july)  # The login's permissions are read once, on its first request.
        # Three slips or six, each paid by a file: the same reads.
        self.assertEqual(reads(july), reads(self.paid_run))

    def owed_and_unpaid(self):
        return -self.balance(self.net_pay), self.paid_run.unpaid_net()

    def test_net_pay_payable_foots_to_the_slips_in_every_order_of_void_return_and_repay(self):
        """Review RP2 and RP3: E2's 8,450.50 comes back; each way of undoing it leaves the books and slips agreed."""
        for person in self.people:
            self.banked(person)
        made = SalaryTransferFile.prepare(self.paid_run)
        paid = self.money(self.people[0].party, "23825.50")
        made.mark_paid(paid)
        row = made.rows.get(payslip__employee=self.people[1])
        receipt = self.money(self.people[1].party, "8450.50", direction=PaymentDirection.RECEIPT)
        row.return_transfer(receipt)
        self.assertEqual(self.owed_and_unpaid(), (Decimal("8450.50"), Decimal("8450.50")))
        # E2 paid by a payment of their own: the receipt is not voided under it.
        own = self.money(self.people[1].party, "8450.50")
        self.paid_run.payslips.get(employee=self.people[1]).pay(own)
        self.assertEqual(self.owed_and_unpaid(), (Decimal("0.00"), Decimal("0")))
        with self.assertRaisesMessage(ValidationError, "paid again since the transfer came back"):
            receipt.void(on_date=D(2026, 7, 1))
        # Their own payment voided, then the receipt: the slip is the file's again, paid once.
        own.void(on_date=D(2026, 7, 1))
        self.assertEqual(self.owed_and_unpaid(), (Decimal("8450.50"), Decimal("8450.50")))
        receipt.void(on_date=D(2026, 7, 1))
        self.assertEqual(self.owed_and_unpaid(), (Decimal("0.00"), Decimal("0")))
        self.assertEqual(self.paid_run.payslips.get(employee=self.people[1]).payment_id, paid.pk)
        # And now the file's payment can go: everyone is owed again, and the books say so.
        paid.void(on_date=D(2026, 7, 1))
        self.assertEqual(self.owed_and_unpaid(), (Decimal("23825.50"), Decimal("23825.50")))

    def test_written_by_payroll_and_paid_by_whoever_posts_payroll(self):
        for person in self.people:
            self.banked(person)
        officer, controller = self.as_role("Payroll Officer"), self.as_role("Controller")
        made = officer.post("/api/hr/salary-files/", {"run": self.paid_run.pk}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        number = made.json()["id"]
        text = officer.get(f"/api/hr/salary-files/{number}/csv/")
        self.assertEqual((text.status_code, text["X-Total-Amount"]), (200, "23825.50"))
        paid = self.money(self.people[0].party, "23825.50")
        self.assertEqual(officer.post(f"/api/hr/salary-files/{number}/mark_paid/", {"payment": paid.pk},
                                      format="json").status_code, 403)
        response = controller.post(f"/api/hr/salary-files/{number}/mark_paid/", {"payment": paid.pk}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.paid_run.unpaid_net(), Decimal("0"))


class PayslipsOnPaperTests(OutputsTestCase):
    def test_the_payslip_prints_and_each_employee_reads_only_their_own(self):
        mine, theirs = self.employee("E1"), self.employee("E2")
        self.pay(mine, self.salary, "12000")
        self.pay(theirs, self.salary, "9000")
        run = self.june()
        later = self.pay_run(D(2026, 7, 1), D(2026, 7, 31), pay_date=D(2026, 7, 31))
        later.calculate()
        slip, other = run.payslips.get(employee=mine), run.payslips.get(employee=theirs)
        printed = self.as_role("Payroll Officer").get(f"/api/hr/payslips/{slip.pk}/pdf/")
        self.assertEqual((printed.status_code, printed["Content-Type"], printed.content[:4]),
                         (200, "application/pdf", b"%PDF"))
        me = self.as_role("Employee Self Service", employee=mine)
        listed = me.get("/api/hr/my-payslips/")
        self.assertEqual([(row["employee"], row["net"]) for row in listed.json()], [("E1", "12000.00")])
        self.assertEqual(me.get(f"/api/hr/my-payslips/{other.pk}/").status_code, 404)
        self.assertEqual(me.get(f"/api/hr/my-payslips/{other.pk}/pdf/").status_code, 404)
        own = me.get(f"/api/hr/my-payslips/{slip.pk}/pdf/")
        self.assertEqual((own.status_code, own.content[:4]), (200, b"%PDF"))
        self.assertEqual(me.get(f"/api/hr/payslips/{other.pk}/").status_code, 403)
        nobody = self.as_role("Employee Self Service").get("/api/hr/my-payslips/")
        self.assertEqual((nobody.status_code, nobody.json()), (200, []))
        self.assertEqual(self.as_role("Line Manager", employee=self.employee("E9")).get(
            "/api/hr/my-payslips/").status_code, 403)


class AdvancesTests(OutputsTestCase):
    def setUp(self):
        super().setUp()
        self.person = self.employee("A1")
        self.pay(self.person, self.salary, "12000")

    def give(self, amount="5000", instalment="2000", person=None, counterpart=None):
        person = person or self.person
        payment = self.money(person.party, amount, counterpart=counterpart or self.advances, day=D(2026, 5, 20))
        return SalaryAdvance.objects.create(employee=person, payment=payment, component=self.recovery,
                                            amount=Decimal(amount), instalment=Decimal(instalment),
                                            recover_from=D(2026, 6, 1))

    def month(self, number, post=True):
        first = D(2026, number, 1)
        last = (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
        run = self.pay_run(first, last, pay_date=last)
        run.calculate()
        if post:
            run.post()
        return run

    def recovered_on(self, run, person=None):
        return [line.amount for line in PayslipLine.objects.filter(payslip__run=run, advance__isnull=False,
                                                                   payslip__employee=person or self.person)]

    def test_what_is_given_must_have_been_paid_out_to_them_against_the_receivable(self):
        other = self.employee("A2")
        for build, said in [
                (lambda: SalaryAdvance.objects.create(
                    employee=self.person, payment=self.money(other.party, "5000", counterpart=self.advances),
                    component=self.recovery, amount=Decimal("5000"), instalment=Decimal("2000"),
                    recover_from=D(2026, 6, 1)), "not A1"),
                (lambda: self.give(counterpart=self.wages), "would never meet"),
                (lambda: SalaryAdvance.objects.create(
                    employee=self.person, payment=self.money(self.person.party, "4000", counterpart=self.advances),
                    component=self.recovery, amount=Decimal("5000"), instalment=Decimal("2000"),
                    recover_from=D(2026, 6, 1)), "The payment is 4000"),
                (lambda: SalaryAdvance.objects.create(
                    employee=self.person, component=self.recovery, amount=Decimal("5000"),
                    payment=self.money(self.person.party, "5000", counterpart=self.advances, post=False),
                    instalment=Decimal("2000"), recover_from=D(2026, 6, 1)), "nothing was given")]:
            with self.subTest(said), self.assertRaisesMessage(ValidationError, said):
                build()

    def test_recovered_in_instalments_the_balance_derived_and_the_ledger_cleared(self):
        advance = self.give()
        runs = [self.month(n) for n in (6, 7, 8, 9)]
        self.assertEqual([self.recovered_on(run) for run in runs],
                         [[Decimal("2000.00")], [Decimal("2000.00")], [Decimal("1000.00")], []])
        self.assertEqual(advance.balance(), Decimal("0.00"))
        self.assertEqual(self.balance(self.advances), Decimal("0.00"))
        self.assertEqual(runs[0].payslips.get().net(), Decimal("10000.00"))

    def test_a_run_voided_after_it_recovered_gives_the_balance_back_and_only_then_the_advance_voids(self):
        advance = self.give()
        june = self.month(6)
        self.assertEqual(advance.balance(), Decimal("3000.00"))
        self.assertEqual(self.balance(self.advances), Decimal("3000.00"))
        with self.assertRaisesMessage(ValidationError, "Void that run first"):
            advance.void(on_date=D(2026, 7, 1))
        with self.assertRaisesMessage(ValidationError, "Void that run first"):
            Payment.objects.get(pk=advance.payment_id).void(on_date=D(2026, 7, 1))
        june.void(on_date=D(2026, 7, 1))
        self.assertEqual(advance.balance(), Decimal("5000.00"))
        advance.void(on_date=D(2026, 7, 1))
        self.assertTrue(Payment.objects.get(pk=advance.payment_id).is_voided())
        self.assertEqual(self.balance(self.advances), Decimal("0.00"))
        self.assertEqual(self.recovered_on(self.month(7, post=False)), [])

    def test_never_past_the_deduction_cap_and_a_leaver_owes_the_rest(self):
        self.pay(self.person, self.tax, "12")
        leaver = self.employee("A3", termination_date=D(2026, 6, 30), employment_status=EmploymentStatus.TERMINATED)
        self.pay(leaver, self.salary, "3000")
        self.pay(leaver, self.tax, "12")
        stayer = self.employee("A4")
        self.pay(stayer, self.salary, "3000")
        self.pay(stayer, self.tax, "12")
        advance, theirs = self.give(person=leaver), self.give(person=stayer)
        june = self.month(6)
        self.assertEqual(self.recovered_on(june, leaver), [Decimal("1140.00")])
        self.assertEqual(advance.balance(), Decimal("3860.00"))
        self.assertEqual(self.recovered_on(june, stayer), [Decimal("1140.00")])
        self.assertEqual(theirs.balance(), Decimal("3860.00"))

    def test_recovery_is_credited_where_the_advance_was_given_whatever_the_component_says_now(self):
        """Review RP4: given against 1300; the component re-pointed to 1301; June recovers 2,000 from 1300."""
        advance = self.give()
        moved = Account.objects.create(code="1301", name="Employee advances (new)", account_type=AccountType.ASSET)
        self.recovery.liability_account = moved
        self.recovery.save()
        self.month(6)
        self.assertEqual((self.balance(self.advances), self.balance(moved), advance.balance()),
                         (Decimal("3000.00"), Decimal("0"), Decimal("3000.00")))
        self.assertEqual(advance.receivable_account, self.advances)

    def test_an_advance_whose_payment_alone_was_voided_is_owed_nothing(self):
        """Review RP8."""
        advance = self.give()
        Payment.objects.get(pk=advance.payment_id).void(on_date=D(2026, 5, 25))
        self.assertEqual((self.balance(self.advances), advance.balance()), (Decimal("0.00"), Decimal("0.00")))

    def test_given_and_voided_by_whoever_pays_it_out(self):
        payment = self.money(self.person.party, "5000", counterpart=self.advances, day=D(2026, 5, 20))
        officer, controller = self.as_role("Payroll Officer"), self.as_role("Controller")
        given = {"employee": self.person.pk, "payment": payment.pk, "component": self.recovery.pk, "amount": "5000",
                 "instalment": "2000", "recover_from": "2026-06-01"}
        self.assertEqual(officer.post("/api/hr/salary-advances/", given, format="json").status_code, 403)
        made = controller.post("/api/hr/salary-advances/", given, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual(made.json()["balance"], "5000.00")
        number = made.json()["id"]
        self.assertEqual(officer.post(f"/api/hr/salary-advances/{number}/void/", {"on_date": "2026-07-01"},
                                      format="json").status_code, 403)
        voided = controller.post(f"/api/hr/salary-advances/{number}/void/", {"on_date": "2026-07-01"}, format="json")
        self.assertEqual((voided.status_code, voided.json()["voided"]), (200, True))
