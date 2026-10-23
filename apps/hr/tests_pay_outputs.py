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
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType, Payment, PaymentDirection

from .advances import SalaryAdvance
from .bank_file import SalaryTransferFile
from .pay_details import EmployeePayDetails
from .models import Employee, EmploymentStatus
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

    def test_a_file_whose_payment_was_voided_reads_unpaid(self):
        """Review RP6: the file still names its payment; the screen reads whether it pays anybody."""
        for person in self.people:
            self.banked(person)
        made = SalaryTransferFile.prepare(self.paid_run)
        paid = self.money(self.people[0].party, "23825.50")
        made.mark_paid(paid)
        controller = self.as_role("Controller")
        read = lambda: controller.get(f"/api/hr/salary-files/{made.pk}/").json()
        self.assertEqual((read()["paid"], read()["payment_voided"]), (True, False))
        paid.void(on_date=D(2026, 7, 1))
        self.assertEqual((read()["paid"], read()["payment_voided"]), (False, True))

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

    def test_an_instalment_is_a_months_however_many_runs_pay_the_month(self):
        """Review A7, decided 15 October: June in two runs takes 2,000 once; July 2,000 again."""
        advance = self.give()
        runs = []
        for first, last in ((D(2026, 6, 1), D(2026, 6, 15)), (D(2026, 6, 16), D(2026, 6, 30)),
                            (D(2026, 7, 1), D(2026, 7, 31))):
            run = self.pay_run(first, last, pay_date=last)
            run.calculate()
            run.post()
            runs.append(run)
        self.assertEqual([self.recovered_on(run) for run in runs], [[Decimal("2000.00")], [], [Decimal("2000.00")]])
        self.assertEqual(advance.balance(), Decimal("1000.00"))

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


class ALeaverNoLongerSignsInOrDecides(OutputsTestCase):
    """O300: a leaver's login is off from the day after the leaving date, and nobody who has left decides."""

    def leaver_with_a_login(self, code="E1", password="a-long-pass-phrase-41"):
        person = self.employee(code)
        self.as_role("Stores Manager", person)
        person.user.set_password(password)
        person.user.save()
        return person, password

    def test_a_leavers_login_stops_signing_in_on_the_day_they_go(self):
        # E1, a Stores Manager's login, left on 30 September 2026; on 20 October the login is off.
        person, password = self.leaver_with_a_login()
        person.terminate(datetime.date(2026, 9, 30))
        user = User.objects.get(pk=person.user_id)
        self.assertFalse(user.is_active)
        self.assertFalse(APIClient().login(username=user.username, password=password))

    def test_a_leaving_date_withdrawn_switches_the_login_on_again(self):
        person, password = self.leaver_with_a_login()
        person.terminate(datetime.date(2026, 9, 30))
        person.termination_date, person.employment_status = None, EmploymentStatus.ACTIVE
        person.save()
        self.assertTrue(User.objects.get(pk=person.user_id).is_active)
        self.assertTrue(APIClient().login(username=person.user.username, password=password))

    def test_a_login_another_reason_switched_off_stays_off_when_the_date_is_withdrawn(self):
        person, _ = self.leaver_with_a_login()
        person.user.is_active = False
        person.user.save()
        person.terminate(timezone.localdate() + datetime.timedelta(days=10))
        person.termination_date, person.employment_status = None, EmploymentStatus.ACTIVE
        person.save()
        self.assertFalse(User.objects.get(pk=person.user_id).is_active)

    def test_a_login_an_administrator_switched_off_stays_off_when_a_past_leaving_date_is_withdrawn(self):
        # Review of O300: admin switches the login off; HR sets 30 September (nothing to switch), then withdraws it.
        person, _ = self.leaver_with_a_login()
        person.user.is_active = False
        person.user.save()
        person.terminate(datetime.date(2026, 9, 30))
        person.termination_date, person.employment_status = None, EmploymentStatus.ACTIVE
        person.save()
        self.assertFalse(User.objects.get(pk=person.user_id).is_active)

    def test_the_switch_off_and_on_are_kept_with_who_and_when(self):
        from .models import LeaverLoginSwitch

        person, _ = self.leaver_with_a_login()
        hr = User.objects.create_user("hr-officer")
        person.updated_by = hr
        person.terminate(datetime.date(2026, 9, 30))
        switch = LeaverLoginSwitch.objects.get(user=person.user)
        self.assertEqual((switch.switched_off_by, switch.switched_on_at), (hr, None))
        person.termination_date, person.employment_status = None, EmploymentStatus.ACTIVE
        person.save()
        switch.refresh_from_db()
        self.assertEqual(switch.switched_on_by, hr)
        self.assertIsNotNone(switch.switched_on_at)

    def test_a_leaving_date_to_come_keeps_the_login_until_it_and_ends_it_the_day_after(self):
        from apps.core.models import login_ended

        today = timezone.localdate()
        person, password = self.leaver_with_a_login()
        person.terminate(today)
        self.assertTrue(User.objects.get(pk=person.user_id).is_active)
        self.assertIsNone(login_ended(person.user, today))
        self.assertIn("has left", login_ended(person.user, today + datetime.timedelta(days=1)))
        # The day has come with nobody switching the login off: a session open then ends, and it signs in no more.
        client = APIClient()
        self.assertTrue(client.login(username=person.user.username, password=password))
        self.assertEqual(client.get("/api/inventory/items/").status_code, 200)
        Employee.objects.filter(pk=person.pk).update(termination_date=today - datetime.timedelta(days=1))
        self.assertIn(client.get("/api/inventory/items/").status_code, (401, 403))
        self.assertFalse(APIClient().login(username=person.user.username, password=password))

    def test_a_manager_on_leave_still_decides_their_reports_leave(self):
        # O332: employed that day is enough; On Leave is still on the roll.
        from .models import LeaveRequest, LeaveStatus

        boss, report = self.employee("E2"), self.employee("E1")
        report.manager = boss
        report.save()
        boss.employment_status = EmploymentStatus.ON_LEAVE
        boss.save()
        leave = LeaveRequest.objects.create(employee=report, leave_type="vacation",
                                            start_date=datetime.date(2026, 11, 2), end_date=datetime.date(2026, 11, 3))
        leave.approve(by=boss)
        self.assertEqual(LeaveRequest.objects.get(pk=leave.pk).status, LeaveStatus.APPROVED)

    def test_a_manager_who_has_left_decides_no_leave_hours_or_claims(self):
        from .expenses import ExpenseClaim
        from .models import LeaveRequest
        from .timesheets import Timesheet

        boss, report = self.employee("E2"), self.employee("E1")
        report.manager = boss
        report.save()
        leave = LeaveRequest.objects.create(employee=report, leave_type="vacation",
                                            start_date=datetime.date(2026, 11, 2), end_date=datetime.date(2026, 11, 3))
        boss.terminate(datetime.date(2026, 6, 1))
        with self.assertRaisesMessage(ValidationError, "has left (leaving date 01-06-2026)"):
            leave.approve(by=boss)
        with self.assertRaisesMessage(ValidationError, "has left (leaving date 01-06-2026)"):
            ExpenseClaim(employee=report, purpose="Taxi")._check_decider(boss, as_hr=False)
        with self.assertRaisesMessage(ValidationError, "has left (leaving date 01-06-2026)"):
            Timesheet(employee=report).check_approver(boss)


class WhoARunLeftOffTests(OutputsTestCase):
    """O305: a run names who was employed in its period and has no slip on it."""

    def test_the_forgotten_joiner_is_named_and_the_paid_and_the_gone_are_not(self):
        paid, forgotten = self.employee("E1"), self.employee("ZED9", hire=datetime.date(2026, 6, 16))
        gone = self.employee("OLD1")
        gone.terminate(datetime.date(2026, 5, 31))
        self.employee("NEW1", hire=datetime.date(2026, 7, 1))
        self.pay(paid, self.salary, "5000")
        run = self.pay_run()
        said = self.as_role("Payroll Officer").post(f"/api/hr/pay-runs/{run.pk}/calculate/", {}, format="json")
        self.assertEqual(said.status_code, 200, said.content)
        self.assertEqual([row["employee_number"] for row in said.json()["skipped"]], ["ZED9"])
        self.assertEqual(run.skipped(), [forgotten])


class APayDateNearItsPeriodTests(OutputsTestCase):
    """O308: a run is posted for a pay date from its period's start to ten days after its end (or today, if late)."""

    def posted(self, pay_date):
        person = self.employee("E1")
        self.pay(person, self.salary, "5000")
        run = self.pay_run(pay_date=pay_date)
        run.calculate()
        return run

    def test_a_typed_year_is_refused(self):
        run = self.posted(datetime.date(2062, 6, 30))
        with self.assertRaisesMessage(ValidationError, "Is the year right?"):
            run.post()

    def test_a_day_before_the_period_is_refused(self):
        run = self.posted(datetime.date(2026, 5, 31))
        with self.assertRaisesMessage(ValidationError, "before the period starts"):
            run.post()

    def test_posted_on_the_last_day_the_tenth_day_after_is_taken_and_the_eleventh_is_not(self):
        from unittest import mock

        with mock.patch("apps.hr.payroll.timezone.localdate", return_value=datetime.date(2026, 6, 30)):
            run = self.posted(datetime.date(2026, 7, 11))
            with self.assertRaisesMessage(ValidationError, "past 2026-07-10"):
                run.post()
            run.pay_date = datetime.date(2026, 7, 10)
            run.save()
            run.post()
        self.assertEqual(run.status, "posted")

    def test_paid_late_the_window_ends_today(self):
        """Review 3: June posted on 23 October: 23 October is taken, 2 November (ten days on) is not."""
        from unittest import mock

        with mock.patch("apps.hr.payroll.timezone.localdate", return_value=datetime.date(2026, 10, 23)):
            run = self.posted(datetime.date(2026, 11, 2))
            with self.assertRaisesMessage(ValidationError, "past 2026-10-23"):
                run.post()
            run.pay_date = datetime.date(2026, 10, 23)
            run.save()
            run.post()
        self.assertEqual(run.status, "posted")

    def test_paid_late_today_is_taken(self):
        run = self.posted(timezone.localdate())
        run.post()
        self.assertEqual(run.status, "posted")


class APeriodClosesWithItsPayRunsPostedTests(OutputsTestCase):
    """O310: hr tells the period's close what it still owes it."""

    def test_june_does_not_close_with_its_run_calculated_and_closes_once_it_is_posted(self):
        from apps.accounting.models import AccountingPeriod

        person = self.employee("E1")
        self.pay(person, self.salary, "5000")
        run = self.pay_run()
        run.calculate()
        june = AccountingPeriod.objects.create(name="June 2026", start_date=datetime.date(2026, 6, 1),
                                               end_date=datetime.date(2026, 6, 30))
        with self.assertRaisesMessage(ValidationError, "1 pay run(s) are still to post or delete"):
            june.close()
        run.post()
        june.close()
        self.assertTrue(AccountingPeriod.objects.get(pk=june.pk).closed)

    def test_a_run_paid_in_july_does_not_hold_june(self):
        from apps.accounting.models import AccountingPeriod

        self.pay_run(pay_date=datetime.date(2026, 7, 7))
        june = AccountingPeriod.objects.create(name="June 2026", start_date=datetime.date(2026, 6, 1),
                                               end_date=datetime.date(2026, 6, 30))
        june.close()
        self.assertTrue(AccountingPeriod.objects.get(pk=june.pk).closed)


class WhoReadsStatutoryNumbersTests(OutputsTestCase):
    """O311: UAN and ESI numbers are served to HR and payroll, not to the floor or a line manager."""

    def test_the_floor_reads_names_and_hr_and_payroll_read_the_numbers(self):
        person = self.employee("E1")
        person.uan, person.esi_number = "100000000001", "2200012345"
        person.save()
        for role in ("Quality Inspector", "Maintenance", "Production Supervisor", "Process Engineer",
                     "Quality Manager", "Line Manager"):
            said = self.as_role(role).get("/api/hr/employees/")
            self.assertEqual(said.status_code, 200, role)
            self.assertNotIn("100000000001", said.content.decode(), role)
            self.assertNotIn("2200012345", said.content.decode(), role)
        for role in ("HR Admin", "Payroll Officer"):
            [row] = self.as_role(role).get("/api/hr/employees/").json()
            self.assertEqual((row["uan"], row["esi_number"]), ("100000000001", "2200012345"), role)
