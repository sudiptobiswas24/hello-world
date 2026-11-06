"""
Review probes for fix_core's O213 and O215 (g22 review_core). Each test states a fact that SHOULD be true,
with figures worked in plain Python first (scratchpad/calc_review_core/o213.py, o215.py), and fails if it is not.

A weaver paid 400.00 for 1,000 m on 3 September at 0.40, the roll voided after payday (the base of
ATakeBackOutsideAFinalRunTests). PF is 12% of the piece work, ESI 0.75% up to the rupee, deductions together at most
half the earning.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.models import Account, AccountType, JournalLine
from apps.hr.payroll import ComponentBasis, ComponentKind, EmployeeCompensation, PayComponent, PayRun, Payslip

from . import tests_piecework as piecework

SEP = piecework.SEP
OCT = lambda day: datetime.date(2026, 10, day)  # noqa: E731
D = Decimal


class Chain(piecework.ATakeBackOutsideAFinalRunTests):
    """The weaver's chain of runs. Inherits the base's own two tests: they pass or fail on their own account."""

    def balance(self, account, party=None):
        rows = JournalLine.objects.filter(account=account, entry__posted=True)
        if party is not None:
            rows = rows.filter(party=party)
        return sum((line.debit - line.credit for line in rows), D("0"))

    def piece_lines(self, run):
        (slip,) = run.payslips.all()
        return sorted((line.kind, line.amount) for line in slip.lines.filter(component=self.per_metre))

    def facts(self, run):
        (slip,) = run.payslips.all()
        def of(component):
            line = slip.lines.filter(component=component).first()
            return D("0.00") if line is None else line.amount

        return (slip.gross(), of(self.pf), of(self.esi), slip.net(), slip.recoverable)

    def posted_net_and_recoverable(self):
        net = D("0")
        owed = D("0")
        for slip in Payslip.objects.filter(employee=self.weaver, run__status="posted"):
            for line in slip.lines.filter(component=self.per_metre):
                net += line.amount if line.kind == ComponentKind.EARNING else -line.amount
            owed += slip.recoverable
        return net, owed

    # --- the chain: 1,500 m in September, 2,000 m on 5 Oct, 1,000 m on 20 Oct --------------------------------

    def test_the_chain_takes_250_50_then_149_50_and_pays_the_rest_whole(self):
        self.woven("1500", SEP(21))
        second = self.pay(SEP(16), SEP(30))
        self.assertEqual(self.piece_lines(second), [(ComponentKind.DEDUCTION, D("250.50")),
                                                    (ComponentKind.EARNING, D("675.00"))])
        self.assertEqual(self.facts(second), (D("675.00"), D("81.00"), D("6"), D("337.50"), D("0")))
        self.woven("2000", OCT(5))
        third = self.pay(OCT(1), OCT(15))
        self.assertEqual(self.piece_lines(third), [(ComponentKind.DEDUCTION, D("149.50")),
                                                   (ComponentKind.EARNING, D("900.00"))])
        self.assertEqual(self.facts(third), (D("900.00"), D("108.00"), D("7"), D("635.50"), D("0")))
        self.woven("1000", OCT(20))
        fourth = self.pay(OCT(16), OCT(31))
        self.assertEqual(self.piece_lines(fourth), [(ComponentKind.EARNING, D("450.00"))])
        self.assertEqual(self.facts(fourth), (D("450.00"), D("54.00"), D("4"), D("392.00"), D("0")))
        # Everything woven and kept, 675 + 900 + 450, is wages: the 400.00 was taken back whole.
        self.assertEqual(self.wages(), D("2025.00"))

    def test_a_run_that_took_part_voided_is_calculated_again_to_the_same_figures(self):
        self.woven("1500", SEP(21))
        second = self.pay(SEP(16), SEP(30))
        second.void()
        self.assertEqual(self.wages(), D("400.00"))
        again = self.pay(SEP(16), SEP(30))
        self.assertEqual(self.piece_lines(again), [(ComponentKind.DEDUCTION, D("250.50")),
                                                   (ComponentKind.EARNING, D("675.00"))])
        self.assertEqual(self.facts(again), (D("675.00"), D("81.00"), D("6"), D("337.50"), D("0")))

    def test_a_run_that_took_all_voided_leaves_it_to_take_again(self):
        self.woven("1500", SEP(21))
        self.pay(SEP(16), SEP(30))
        self.woven("2000", OCT(5))
        third = self.pay(OCT(1), OCT(15))
        third.void()
        self.assertEqual(self.wages(), D("824.50"))
        again = self.pay(OCT(1), OCT(15))
        self.assertEqual(self.piece_lines(again), [(ComponentKind.DEDUCTION, D("149.50")),
                                                   (ComponentKind.EARNING, D("900.00"))])

    def test_the_earlier_run_voided_under_the_later_one_still_foots_when_the_next_run_pays(self):
        # Run 2 (took 250.50) voided with run 3 (took 149.50) standing. By difference, the next run pays what is
        # owed: earned 675 + 900 + 450 = 2,025 less 400 + 750.50 paid = 874.50, and every rupee then agrees.
        self.woven("1500", SEP(21))
        second = self.pay(SEP(16), SEP(30))
        self.woven("2000", OCT(5))
        self.pay(OCT(1), OCT(15))
        second.void()
        self.woven("1000", OCT(20))
        fourth = self.pay(OCT(16), OCT(31))
        self.assertEqual(self.piece_lines(fourth), [(ComponentKind.EARNING, D("874.50"))])
        self.assertEqual(self.wages(), D("2025.00"))

    # --- a leaver --------------------------------------------------------------------------------------------

    def leaver_case(self):
        self.receivable = Account.objects.create(code="1390", name="Owed by former employees",
                                                 account_type=AccountType.ASSET)
        self.per_metre.recoverable_account = self.receivable
        self.per_metre.save()

    def test_a_leaver_after_a_partial_take_back_owes_the_rest_and_the_books_foot(self):
        # After run 2: 250.50 taken, 149.50 waiting. Left on 10 Oct having woven 100 m (45.00): earning 45.00,
        # PF 5.40, ESI 1, half of 45.00 is 22.50, so 16.10 is taken and 133.40 owed; net 22.50.
        self.leaver_case()
        self.woven("1500", SEP(21))
        self.pay(SEP(16), SEP(30))
        self.woven("100", OCT(5))
        self.weaver.terminate(on_date=OCT(10))
        final = self.pay(OCT(1), OCT(15))
        self.assertEqual(self.piece_lines(final), [(ComponentKind.DEDUCTION, D("16.10")),
                                                   (ComponentKind.EARNING, D("45.00"))])
        self.assertEqual(self.facts(final), (D("45.00"), D("5.40"), D("1"), D("22.50"), D("133.40")))
        # Audit shape 4: what was paid, less what is owed, is what was earned (675 + 45); the receivable is the debt.
        net, owed = self.posted_net_and_recoverable()
        self.assertEqual(net - owed, D("720.00"))
        self.assertEqual(self.balance(self.receivable, self.weaver.party), D("133.40"))
        self.assertEqual(self.balance(self.receivable), D("133.40"))

    def test_a_leaver_who_wove_nothing_owes_all_of_it_and_the_files_read_nothing(self):
        self.leaver_case()
        self.weaver.terminate(on_date=SEP(20))
        final = self.pay(SEP(16), SEP(30))
        self.assertEqual(self.facts(final), (D("0.00"), D("0.00"), D("0.00"), D("0.00"), D("400.00")))
        self.assertEqual(self.balance(self.receivable, self.weaver.party), D("400.00"))

    def test_a_final_run_voided_after_it_took_part_is_calculated_again_to_the_same_shortfall(self):
        self.leaver_case()
        self.woven("1500", SEP(21))
        self.pay(SEP(16), SEP(30))
        self.woven("100", OCT(5))
        self.weaver.terminate(on_date=OCT(10))
        final = self.pay(OCT(1), OCT(15))
        final.void()
        self.assertEqual(self.balance(self.receivable, self.weaver.party), D("0.00"))
        again = self.pay(OCT(1), OCT(15))
        self.assertEqual(self.facts(again), (D("45.00"), D("5.40"), D("1"), D("22.50"), D("133.40")))

    # --- the ones who are not paid by the metre: nothing about them may move -----------------------------------

    def other(self, number, hours=None):
        person = self.employee(number, number)
        person.uan, person.esi_number = "100200300%03d" % int(number[-3:]), "3100200%03d" % int(number[-3:])
        person.save()
        from apps.hr.pay_details import EmployeePayDetails

        EmployeePayDetails.objects.create(employee=person, date_of_birth=datetime.date(1990, 1, 1))
        return person

    def test_a_salaried_employee_is_unmoved_on_an_ordinary_run_and_a_final_one(self):
        basic = PayComponent.objects.create(
            code="BASIC", name="Basic", kind=ComponentKind.EARNING, basis=ComponentBasis.FIXED,
            expense_account=self.per_metre.expense_account, sequence=1)
        self.pf.base_components.add(basic)
        person = self.other("EMP-0301")
        EmployeeCompensation.objects.create(employee=person, component=basic, amount=D("10000"),
                                            effective_from=SEP(1))
        for component, amount in ((self.pf, "12"), (self.esi, "1")):  # ESI at the dated rate: 1 is "insured"
            EmployeeCompensation.objects.create(employee=person, component=component, amount=D(amount),
                                                effective_from=SEP(1))
        run = PayRun.objects.create(period_start=SEP(1), period_end=SEP(30), pay_date=SEP(30))
        run.calculate(employees=[person])
        (slip,) = run.payslips.all()
        self.assertEqual((slip.gross(), self.amount(slip, self.pf), self.amount(slip, self.esi), slip.net(),
                          slip.recoverable), (D("10000.00"), D("1200.00"), D("75"), D("8725.00"), D("0")))
        person.terminate(on_date=SEP(30))
        run.calculate(employees=[person])
        (slip,) = run.payslips.all()
        self.assertEqual((slip.gross(), slip.net(), slip.recoverable), (D("10000.00"), D("8725.00"), D("0")))

    def test_an_hourly_shift_worker_is_unmoved(self):
        hourly = PayComponent.objects.create(
            code="HOUR", name="Shift hours", kind=ComponentKind.EARNING, basis=ComponentBasis.PER_HOUR,
            expense_account=self.per_metre.expense_account, sequence=1)
        self.pf.base_components.add(hourly)
        person = self.other("EMP-0302")
        EmployeeCompensation.objects.create(employee=person, component=hourly, amount=D("100"),
                                            effective_from=SEP(1))
        for component, amount in ((self.pf, "12"), (self.esi, "1")):  # ESI at the dated rate: 1 is "insured"
            EmployeeCompensation.objects.create(employee=person, component=component, amount=D(amount),
                                                effective_from=SEP(1))
        run = PayRun.objects.create(period_start=SEP(1), period_end=SEP(30), pay_date=SEP(30))
        run.calculate(employees=[person], hours={person: D("120")})
        (slip,) = run.payslips.all()
        self.assertEqual((slip.gross(), self.amount(slip, self.pf), self.amount(slip, self.esi), slip.net(),
                          slip.recoverable), (D("12000.00"), D("1440.00"), D("90"), D("10470.00"), D("0")))


class Settled(piecework.AFormerEmployeesShortfallIsSettledTests):
    """O215. A leaver owes 400.00 (final slip). Inherits the base's three tests."""

    def by_party(self):
        return self.balance_party(self.receivable)

    def balance_party(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True, party=self.weaver.party)
        return sum((line.debit - line.credit for line in rows), D("0"))

    def settle(self, client, **body):
        return client.post("/api/hr/shortfall-settlements/", {"payslip": self.slip.pk, **body}, format="json")

    def feet(self, owed, loss, bank):
        self.assertEqual((self.owed(), self.balance(self.receivable), self.by_party(), self.balance(self.loss),
                          self.balance(self.bank)), (D(owed), D(owed), D(owed), D(loss), D(bank)))

    def test_part_receipt_part_write_off_clears_the_receivable_and_it_foots_by_party(self):
        controller = self.as_("Controller")
        payment = self.paid_back("150.00")
        self.assertEqual(self.settle(controller, kind="receipt", amount="150.00", payment=payment.pk).status_code, 201)
        self.feet("250.00", "0.00", "150.00")
        answer = self.settle(controller, kind="write_off", amount="250.00", reason="Left the state")
        self.assertEqual(answer.status_code, 201, answer.content)
        self.feet("0.00", "250.00", "150.00")

    def test_a_write_off_alone_clears_it(self):
        answer = self.settle(self.as_("Controller"), kind="write_off", amount="400.00", reason="Cannot be traced")
        self.assertEqual(answer.status_code, 201, answer.content)
        self.feet("0.00", "400.00", "0.00")

    def test_voids_in_either_order_put_all_of_it_back_and_the_run_may_then_go(self):
        controller = self.as_("Controller")
        payment = self.paid_back("150.00")
        self.settle(controller, kind="receipt", amount="150.00", payment=payment.pk)
        off = self.settle(controller, kind="write_off", amount="250.00", reason="Left the state").json()["id"]
        receipt = self.slip.settlements.get(kind="receipt")
        # The receipt first, with the write-off standing: owed 150.00 more than the write-off left.
        self.assertEqual(controller.post(f"/api/hr/shortfall-settlements/{receipt.pk}/void/", {},
                                         format="json").status_code, 200)
        self.feet("150.00", "250.00", "0.00")
        self.assertEqual(controller.post(f"/api/hr/shortfall-settlements/{off}/void/", {},
                                         format="json").status_code, 200)
        self.feet("400.00", "0.00", "0.00")
        # Nothing stands: the run that made the shortfall may be voided, and the receivable goes with it.
        self.final.void()
        self.assertEqual((self.balance(self.receivable), self.by_party(), self.owed()),
                         (D("0.00"), D("0.00"), D("0.00")))

    def test_more_than_is_owed_is_refused_each_way(self):
        controller = self.as_("Controller")
        self.assertEqual(self.settle(controller, kind="write_off", amount="400.01", reason="x").status_code, 400)
        payment = self.paid_back("150.00")
        self.assertEqual(self.settle(controller, kind="receipt", amount="150.00", payment=payment.pk).status_code, 201)
        self.assertEqual(self.settle(controller, kind="write_off", amount="250.01", reason="x").status_code, 400)
        self.assertEqual(self.settle(controller, kind="write_off", amount="250.00", reason="x").status_code, 201)
        self.assertEqual(self.settle(controller, kind="write_off", amount="0.01", reason="x").status_code, 400)

    def test_a_write_off_without_a_reason_is_refused_whatever_the_blank(self):
        controller = self.as_("Controller")
        for body in ({}, {"reason": ""}, {"reason": "   "}, {"reason": None}):
            answer = self.settle(controller, kind="write_off", amount="100.00", **body)
            self.assertEqual(answer.status_code, 400, (body, answer.content))
        self.feet("400.00", "0.00", "0.00")

    def test_only_the_roles_that_decide_it_hold_the_write_off_and_the_void(self):
        from django.contrib.auth.models import Group

        holders = lambda codename: sorted(  # noqa: E731
            Group.objects.filter(permissions__codename=codename).values_list("name", flat=True))
        self.assertEqual(holders("write_off_shortfall"), ["Controller"])
        self.assertEqual(holders("void_shortfallsettlement"), ["Controller"])
        # Reading is not deciding: the Auditor reads every record, read-only (g24, the edit log).
        self.assertEqual(holders("view_shortfallsettlement"), ["Auditor", "Controller"])

    def test_a_write_off_is_dated_by_the_rule(self):
        controller = self.as_("Controller")
        before = self.settle(controller, kind="write_off", amount="100.00", reason="x", date="2026-09-29")
        self.assertEqual(before.status_code, 400, before.content)
        from django.utils import timezone

        ahead = (timezone.localdate() + datetime.timedelta(days=1)).isoformat()
        future = self.settle(controller, kind="write_off", amount="100.00", reason="x", date=ahead)
        self.assertEqual(future.status_code, 400, future.content)
        same = self.settle(controller, kind="write_off", amount="100.00", reason="x", date="2026-09-30")
        self.assertEqual(same.status_code, 201, same.content)
        self.assertEqual(same.json()["date"], "2026-09-30")

    def test_a_write_off_into_a_closed_period_is_refused(self):
        from apps.accounting.models import AccountingPeriod

        AccountingPeriod.objects.create(name="Sep 2026", start_date=SEP(1), end_date=SEP(30), closed=True)
        answer = self.settle(self.as_("Controller"), kind="write_off", amount="100.00", reason="x", date="2026-09-30")
        self.assertEqual(answer.status_code, 400, answer.content)
        self.feet("400.00", "0.00", "0.00")

    def test_a_write_off_is_voided_by_the_rule_too(self):
        controller = self.as_("Controller")
        off = self.settle(controller, kind="write_off", amount="100.00", reason="x", date="2026-09-30").json()["id"]
        from django.utils import timezone

        ahead = (timezone.localdate() + datetime.timedelta(days=1)).isoformat()
        for day in ("2026-09-29", ahead):
            answer = controller.post(f"/api/hr/shortfall-settlements/{off}/void/", {"on_date": day}, format="json")
            self.assertEqual(answer.status_code, 400, (day, answer.content))
        self.assertEqual(controller.post(f"/api/hr/shortfall-settlements/{off}/void/", {"on_date": "2026-09-30"},
                                         format="json").status_code, 200)
        self.feet("400.00", "0.00", "0.00")

    def test_a_receipt_voided_on_a_day_before_the_payment_is_refused_and_leaves_the_receipt_standing(self):
        controller = self.as_("Controller")
        payment = self.paid_back("150.00")
        receipt = self.settle(controller, kind="receipt", amount="150.00", payment=payment.pk).json()["id"]
        answer = controller.post(f"/api/hr/shortfall-settlements/{receipt}/void/", {"on_date": "2026-09-29"},
                                 format="json")
        self.assertEqual(answer.status_code, 400, answer.content)
        self.feet("250.00", "0.00", "150.00")

    def test_a_payment_used_twice_is_a_refusal_not_a_crash(self):
        controller = self.as_("Controller")
        payment = self.paid_back("100.00")
        self.assertEqual(self.settle(controller, kind="receipt", amount="100.00", payment=payment.pk).status_code, 201)
        answer = self.settle(controller, kind="receipt", amount="100.00", payment=payment.pk)
        self.assertEqual(answer.status_code, 400, answer.content)

    def test_the_payment_voided_on_its_own_then_a_second_receipt_then_the_run_stands(self):
        from apps.hr.shortfalls import SettlementKind, ShortfallSettlement

        first = self.paid_back("150.00")
        ShortfallSettlement.objects.create(payslip=self.slip, kind=SettlementKind.RECEIPT, amount=D("150.00"),
                                           payment=first)
        first.void()
        self.feet("400.00", "0.00", "0.00")
        second = self.paid_back("400.00")
        ShortfallSettlement.objects.create(payslip=self.slip, kind=SettlementKind.RECEIPT, amount=D("400.00"),
                                           payment=second)
        self.feet("0.00", "0.00", "400.00")
