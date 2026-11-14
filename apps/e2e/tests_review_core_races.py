"""
Review probes for fix_core (g22 review_core), on PostgreSQL with race(): a shortfall receipt and a write-off at
once, a write-off as the run is voided, and a leave cancelled as the run posts.
"""
import datetime
import unittest
from decimal import Decimal

from django.db import connection
from django.test import tag

from apps.accounting.models import JournalLine
from apps.hr.models import LeaveRequest, LeavePolicy, LeaveStatus, LeaveType
from apps.hr.payroll import PayRun
from apps.hr.shortfalls import SettlementKind, ShortfallSettlement, owed_back
from apps.hr import tests_payroll as payroll_fixture
from apps.manufacturing import tests_piecework as piece_fixture

from .tests_races import RaceCase, fixture, race

D = Decimal


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class ShortfallRaceTests(RaceCase):
    def setUp(self):
        self.shop = fixture(self, piece_fixture.AFormerEmployeesShortfallIsSettledTests)
        self.slip = self.shop.slip

    def receipt_of(self, amount):
        payment = self.shop.paid_back(amount)
        return lambda: ShortfallSettlement.objects.create(
            payslip=self.slip, kind=SettlementKind.RECEIPT, amount=D(amount), payment=payment)

    def write_off_of(self, amount):
        return lambda: ShortfallSettlement.objects.create(
            payslip=self.slip, kind=SettlementKind.WRITE_OFF, amount=D(amount), reason="Cannot be traced")

    def standing(self):
        return sorted((s.kind, s.amount) for s in ShortfallSettlement.objects.filter(payslip=self.slip)
                      if s.stands())

    def test_a_receipt_and_a_write_off_that_together_pass_what_is_owed_do_not_both_stand(self):
        outcomes = race(ShortfallSettlement, self.receipt_of("300.00"), self.write_off_of("300.00"))
        self.once(outcomes)
        left = owed_back(self.slip)
        self.assertEqual(left, D("100.00"), (outcomes, self.standing()))
        ledger = sum((line.debit - line.credit for line in JournalLine.objects.filter(
            account=self.shop.receivable, party=self.shop.weaver.party, entry__posted=True)), D("0"))
        self.assertEqual(ledger, D("400.00") - D("300.00") if self.standing()[0][0] == "write_off"
                         else D("100.00"), (outcomes, self.standing()))

    def test_two_write_offs_that_together_pass_what_is_owed_do_not_both_stand(self):
        self.once(race(ShortfallSettlement, self.write_off_of("250.00"), self.write_off_of("250.00")))
        self.assertEqual(owed_back(self.slip), D("150.00"))

    def test_two_receipts_that_together_pass_what_is_owed_do_not_both_stand(self):
        self.once(race(ShortfallSettlement, self.receipt_of("250.00"), self.receipt_of("250.00")))
        self.assertEqual(owed_back(self.slip), D("150.00"))

    def test_a_write_off_and_the_voiding_of_the_run_that_made_the_shortfall_do_not_both_go_through(self):
        final = self.shop.final
        outcomes = race((ShortfallSettlement, PayRun), self.write_off_of("100.00"),
                        lambda: PayRun.objects.get(pk=final.pk).void())
        self.once(outcomes)
        run = PayRun.objects.get(pk=final.pk)
        stands = any(s.stands() for s in ShortfallSettlement.objects.filter(payslip=self.slip))
        self.assertFalse(run.is_voided() and stands, outcomes)


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class LeaveAgainstTheRunRaceTests(RaceCase):
    def setUp(self):
        self.shop = fixture(self, payroll_fixture.PayrollTestCase)

    def test_a_leave_cancelled_as_the_run_posts_leaves_the_run_and_the_leave_agreeing(self):
        shop = self.shop
        unpaid = LeavePolicy.objects.create(code="UNP", name="Unpaid", leave_type="unpaid",
                                            annual_days=D("0"), allows_negative=True, is_paid=False)
        boss = shop.employee("R2")
        person = shop.employee("R1", manager=boss)
        shop.pay(person, shop.salary, "4400")
        leave = LeaveRequest.objects.create(
            employee=person, policy=unpaid, leave_type=LeaveType.UNPAID,
            start_date=datetime.date(2026, 6, 8), end_date=datetime.date(2026, 6, 12))
        leave.approve(by=boss)
        run = shop.pay_run()
        run.calculate(employees=[person])
        self.assertEqual(run.payslips.get().gross(), D("3400.00"))
        outcomes = race((PayRun, LeaveRequest),
                        lambda: PayRun.objects.get(pk=run.pk).post(),
                        lambda: LeaveRequest.objects.get(pk=leave.pk).cancel(
                            on_date=datetime.date(2026, 6, 8), as_hr=True))
        run.refresh_from_db()
        leave.refresh_from_db()
        # Either the run posted at 3,400 and the leave stands, or the leave went back and the run was not posted
        # at 3,400: never a posted run that docked days the leave no longer holds.
        posted = run.status == "posted"
        cancelled = leave.status == LeaveStatus.CANCELLED
        self.assertFalse(posted and cancelled, outcomes)
