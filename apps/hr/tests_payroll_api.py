"""
Payroll over the API, and the rule the models stated and did not keep:
pay is dated, not overwritten. P1 on 5,000 a month, tax 20%, pension
5%, the employer 3%: gross 5,000.00, deductions 1,250.00, net 3,750.00,
employer 150.00. Once June is posted on that salary, the salary row
cannot be rewritten; a rise is a new row from July, and the old one is
closed off, not before June ended.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.accounting.models import Payment, PaymentDirection

from .payroll import EmployeeCompensation, PayRunStatus
from .tests_payroll import PayrollTestCase

JUNE_END = datetime.date(2026, 6, 30)


class PayrollApiTestCase(PayrollTestCase):
    def setUp(self):
        super().setUp()
        self.person = self.employee("P1")
        self.salary_row = self.pay(self.person, self.salary, "5000")
        self.pay(self.person, self.tax, "20")
        self.pay(self.person, self.pension, "5")
        self.pay(self.person, self.employer_pension, "3")
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_superuser("payroll"))


class ARunThroughTheApiTests(PayrollApiTestCase):
    def test_opened_calculated_posted_and_paid(self):
        response = self.client.post("/api/hr/pay-runs/", {
            "period_start": "2026-06-01", "period_end": "2026-06-30",
            "pay_date": "2026-06-30", "name": "June"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        base = f"/api/hr/pay-runs/{response.json()['id']}/"
        response = self.client.post(base + "calculate/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        (slip,) = response.json()["payslips"]
        self.assertEqual((slip["gross"], slip["deductions"], slip["net"], slip["employer_cost"]),
                         ("5000.00", "1250.00", "3750.00", "150.00"))
        self.assertEqual([line["component"] for line in slip["lines"]],
                         ["SAL", "TAX", "PEN", "EPEN"])
        response = self.client.post(base + "post/", {}, format="json")
        self.assertEqual((response.status_code, response.json()["status"]),
                         (200, PayRunStatus.POSTED))
        self.assertEqual(self.client.post(base + "calculate/", {}, format="json").status_code,
                         400)
        self.assertEqual(self.client.patch(base, {"name": "x"}, format="json").status_code, 400)
        payment = Payment.objects.create(
            party=self.person.party, direction=PaymentDirection.DISBURSEMENT,
            payment_date=JUNE_END, amount=Decimal("3750.00"), currency=self.usd,
            bank_account=self.bank, counterpart_account=self.net_pay)
        payment.post()
        response = self.client.post(f"/api/hr/payslips/{slip['id']}/pay/",
                                    {"payment": payment.pk}, format="json")
        self.assertEqual((response.status_code, response.json()["paid"]), (200, True))
        self.assertEqual(self.client.get(base).json()["unpaid_net"], "0")
        self.assertEqual(self.client.post(base + "void/", {}, format="json").status_code, 400)
        self.assertEqual(self.balance(self.net_pay), Decimal("0"))

    def test_voided_while_nobody_is_paid(self):
        run = self.pay_run()
        run.calculate()
        run.post()
        response = self.client.post(f"/api/hr/pay-runs/{run.pk}/void/",
                                    {"on_date": "2026-07-01"}, format="json")
        self.assertEqual((response.status_code, response.json()["status"]),
                         (200, PayRunStatus.VOIDED))
        self.assertEqual(self.balance(self.wages), Decimal("0"))

    def test_some_people_and_their_hours(self):
        other = self.employee("P2")
        self.pay(other, self.salary, "4000")
        run = self.pay_run()
        base = f"/api/hr/pay-runs/{run.pk}/calculate/"
        response = self.client.post(base, {"employees": [other.pk]}, format="json")
        self.assertEqual([slip["employee"] for slip in response.json()["payslips"]], ["P2"])
        for bad in ({"employees": [other.pk, 99999]}, {"hours": {str(other.pk): "lots"}},
                    {"hours": {str(other.pk): "-1"}}, {"hours": {str(other.pk): "NaN"}}):
            self.assertEqual(self.client.post(base, bad, format="json").status_code, 400, bad)
        response = self.client.get("/api/hr/payslips/", {"run": run.pk})
        self.assertEqual(len(response.json()), 1)

    def test_one_persons_slips_and_nothing_it_cannot_narrow_by(self):
        other = self.employee("P2")
        self.pay(other, self.salary, "4000")
        run = self.pay_run()
        run.calculate()
        mine = self.client.get("/api/hr/payslips/", {"employee": self.person.pk})
        self.assertEqual([slip["employee"] for slip in mine.json()], ["P1"])
        # Read by hand once, an unknown narrowing listed every slip.
        refused = self.client.get("/api/hr/payslips/", {"meter": "5"})
        self.assertEqual(refused.status_code, 400)

    def test_posting_needs_the_permission(self):
        clerk = User.objects.create_user("clerk")
        clerk.user_permissions.add(*Permission.objects.filter(
            codename__in=["view_payrun", "add_payrun", "change_payrun"]))
        client = APIClient()
        client.force_authenticate(clerk)
        run = self.pay_run()
        run.calculate()
        self.assertEqual(client.post(f"/api/hr/pay-runs/{run.pk}/post/", {},
                                     format="json").status_code, 403)

    def test_set_up_through_the_api(self):
        response = self.client.post("/api/hr/pay-components/", {
            "code": "HRA", "name": "House rent", "kind": "earning", "basis": "fixed",
            "expense_account": self.wages.pk}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        response = self.client.post("/api/hr/pay-components/", {
            "code": "PF", "name": "PF", "kind": "deduction", "basis": "percent"},
            format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/hr/compensation/", {
            "employee": self.person.pk, "component": self.salary.pk, "amount": "5500",
            "effective_from": "2026-01-01"}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.get("/api/hr/compensation/", {"employee": self.person.pk})
        self.assertEqual(len(response.json()["results"] if isinstance(response.json(), dict)
                             else response.json()), 4)


class PayIsDatedTests(PayrollApiTestCase):
    def posted_june(self):
        run = self.pay_run()
        run.calculate()
        run.post()
        return run

    def test_a_rate_paid_on_is_not_rewritten(self):
        self.posted_june()
        self.salary_row.amount = Decimal("5500")
        with self.assertRaisesMessage(ValidationError, "has been paid on"):
            self.salary_row.save()
        self.salary_row.refresh_from_db()
        self.salary_row.effective_from = datetime.date(2026, 7, 1)
        with self.assertRaisesMessage(ValidationError, "has been paid on"):
            self.salary_row.save()
        with self.assertRaisesMessage(ValidationError, "has been paid on"):
            self.salary_row.delete()

    def test_closed_off_not_before_what_was_paid_on_it(self):
        self.posted_june()
        self.salary_row.effective_to = datetime.date(2026, 6, 15)
        with self.assertRaisesMessage(ValidationError, "paid on it to 2026-06-30"):
            self.salary_row.save()
        self.salary_row.effective_to = JUNE_END
        self.salary_row.save()
        rise = self.pay(self.person, self.salary, "5500", since=datetime.date(2026, 7, 1))
        self.assertEqual(rise.amount, Decimal("5500"))

    def test_a_voided_run_paid_nothing(self):
        run = self.posted_june()
        run.void()
        self.salary_row.amount = Decimal("5500")
        self.salary_row.save()
        self.salary_row.delete()
        self.assertFalse(EmployeeCompensation.objects.filter(pk=self.salary_row.pk).exists())

    def test_a_rate_not_yet_paid_on_is_edited_freely(self):
        run = self.pay_run()
        run.calculate()
        self.salary_row.amount = Decimal("5100")
        self.salary_row.save()


class OnlyWhatThePostedRunPaidTests(PayrollApiTestCase):
    """June is posted for P1 alone; every other rate stays free to edit."""

    def setUp(self):
        super().setUp()
        self.other = self.employee("P2")
        self.others_salary = self.pay(self.other, self.salary, "4000")
        run = self.pay_run()
        run.calculate(employees=[self.person])
        run.post()

    def edit(self, row):
        row.note = "checked"
        row.amount = row.amount + 1
        row.save()

    def test_somebody_else_s_rate(self):
        self.edit(self.others_salary)

    def test_a_component_the_run_did_not_pay(self):
        self.edit(self.pay(self.person, self.bonus, "100", since=datetime.date(2026, 1, 1)))

    def test_a_rate_that_had_ended_before_the_run(self):
        EmployeeCompensation.objects.filter(pk=self.salary_row.pk).update(
            effective_from=datetime.date(2026, 7, 1))
        old = EmployeeCompensation.objects.create(
            employee=self.person, component=self.salary, amount=Decimal("4800"),
            effective_from=datetime.date(2025, 1, 1), effective_to=datetime.date(2026, 5, 31))
        self.edit(old)

    def test_a_rate_that_starts_after_the_run(self):
        self.salary_row.effective_to = JUNE_END
        self.salary_row.save()
        self.edit(self.pay(self.person, self.salary, "5500", since=datetime.date(2026, 7, 1)))


class TheLastRunPaidOnTests(PayrollApiTestCase):
    def test_closed_off_not_before_the_latest(self):
        for start, end in ((datetime.date(2026, 5, 1), datetime.date(2026, 5, 31)),
                           (datetime.date(2026, 6, 1), JUNE_END)):
            run = self.pay_run(start, end, end)
            run.calculate()
            run.post()
        self.salary_row.effective_to = datetime.date(2026, 6, 10)
        with self.assertRaisesMessage(ValidationError, "paid on it to 2026-06-30"):
            self.salary_row.save()

    def test_payslips_of_one_run(self):
        for start, end in ((datetime.date(2026, 5, 1), datetime.date(2026, 5, 31)),
                           (datetime.date(2026, 6, 1), JUNE_END)):
            run = self.pay_run(start, end, end)
            run.calculate()
        response = self.client.get("/api/hr/payslips/", {"run": run.pk})
        self.assertEqual([slip["run"] for slip in response.json()], [run.pk])
        self.assertEqual(len(self.client.get("/api/hr/payslips/").json()), 2)


class PayrollScreensTests(PayrollApiTestCase):
    """What the payroll screens ask: every slip, paged; a void on a date."""

    def test_payslips_are_paged_not_cut_at_five_hundred(self):
        for code in ("P2", "P3"):
            self.pay(self.employee(code), self.salary, "4000")
        run = self.pay_run()
        run.calculate()
        response = self.client.get("/api/hr/payslips/", {"run": run.pk, "page_size": "2"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response["X-Total-Count"], len(response.json())), ("3", 2))
        rest = self.client.get("/api/hr/payslips/", {"run": run.pk, "page_size": "2", "page": "2"}).json()
        self.assertEqual(len(rest), 1)

    def test_a_void_on_no_such_day_is_refused_not_dated_today(self):
        run = self.pay_run()
        run.calculate()
        run.post()
        response = self.client.post(f"/api/hr/pay-runs/{run.pk}/void/", {"on_date": "31/06/2026"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        run.refresh_from_db()
        self.assertIsNone(run.voided_at)

    def test_the_run_list_does_not_ask_per_slip(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def count():
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.client.get("/api/hr/pay-runs/").status_code, 200)
            return len(queries)

        self.pay_run().calculate()
        before = count()
        for code in ("P2", "P3", "P4"):
            self.pay(self.employee(code), self.salary, "4000")
        import datetime
        self.pay_run(start=datetime.date(2026, 7, 1), end=datetime.date(2026, 7, 31),
                     pay_date=datetime.date(2026, 7, 31)).calculate()
        self.assertEqual(count(), before)

    def test_a_liabilities_date_that_is_no_day_is_refused(self):
        for typed in ["2026-02-30", "tomorrow"]:
            with self.subTest(typed=typed):
                response = self.client.get("/api/hr/statutory-liabilities/", {"as_of": typed})
                self.assertEqual(response.status_code, 400, response.content)
