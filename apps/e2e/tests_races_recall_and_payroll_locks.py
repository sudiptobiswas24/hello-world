"""
rereview (gate g21), PostgreSQL only. Locks of the review fixes, asked by real threads.
Run: DJANGO_TIME_ZONE=Asia/Kolkata DATABASE_URL=postgres://erp:erp@localhost:5432/rereview \
  PYTHONPATH=scripts/gate .venv/bin/python manage.py test apps.e2e.tests_races_recall_and_payroll_locks --settings=fastsettings --keepdb
"""

import datetime
import unittest
from decimal import Decimal

from django.db import connection
from django.test import TransactionTestCase

from apps.e2e.tests_races import fixture, race


def deadlocked(outcomes):
    return [o for o in outcomes if "deadlock" in str(o).lower()]


@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class TheRecallLocks(TransactionTestCase):
    def joined(self):
        from apps.manufacturing import tests_recall
        from apps.manufacturing.rebatch import rebatch
        from apps.quality.models import Inspection, Reading

        made = fixture(self, tests_recall.RecallTestCase)
        joined = made.new("RB-J")
        rebatch(made.bag, made.plant, [(made.b2, "100"), (made.b3, "100")], [(joined, "200")], "Short ends",
                on_date=tests_recall.TODAY)
        made.inspect(joined, tests_recall.IN)
        first = made.inspect(made.b2, tests_recall.OUT)  # B2 rejected: RB-J recalled

        def draft(lot, number):
            plan = made.bag_spec.inspection_plan
            line = plan.lines.get()
            row = Inspection.objects.create(lot=lot, plan=plan, inspected_on=tests_recall.TODAY, number=number)
            for n, value in enumerate(tests_recall.OUT[:line.sample_size], start=1):
                Reading.objects.create(inspection=row, plan_line=line, value=Decimal(value),
                                       sample_reference=f"S{n}")
            return row

        return made, joined, first, draft

    def states(self, joined):
        from django.contrib.contenttypes.models import ContentType

        from apps.core.chatter import FollowUp
        from apps.quality.models import Inspection, ReleaseStatus
        from apps.quality.release import release_status

        recalls = list(Inspection.objects.filter(lot=joined, recalled_by__isnull=False, voided_at__isnull=True))
        todo_text = " ".join(t.note + t.summary for t in FollowUp.objects.filter(
            content_type=ContentType.objects.get_for_model(Inspection), done_on__isnull=True))
        return release_status(joined) == ReleaseStatus.HELD, recalls, todo_text

    def _lift_against_a_verdict_on_another_source(self):
        """Item 4: B2's rejection voided (lift pending) while B3, the other source of RB-J, is rejected."""
        from django.utils import timezone

        from apps.quality.models import Inspection
        from apps.quality.recall import lift

        made, joined, first, draft = self.joined()
        Inspection.objects.filter(pk=first.pk).update(voided_at=timezone.now(), voided_reason="typo")
        second = draft(made.b3, "QC-RACE-S2")
        from apps.core import lock_order

        original_init = lock_order.LockOrderViolation.__init__

        def init(this, *a, **k):
            import traceback

            print("\nVIOLATION STACK\n" + "".join(traceback.format_stack(limit=16)))
            original_init(this, *a, **k)

        lock_order.LockOrderViolation.__init__ = init
        self.addCleanup(setattr, lock_order.LockOrderViolation, "__init__", original_init)

        def traced():
            import traceback

            try:
                lift(made.b2, f"{first.number} was voided: typo")
            except Exception:
                print("\nLIFT TRACE\n" + "".join(traceback.format_exc().splitlines(True)[-14:]))
                raise

        outcomes = race(Inspection, traced, lambda: Inspection.objects.get(pk=second.pk).post())
        held, recalls, todo = self.states(joined)
        print(f"\nRACE lift(B2) / reject B3: {outcomes}; RB-J held {held}; open recalls {len(recalls)}; "
              f"to-do names QC-RACE-S2 / {second.number}: {'S2' in todo or (second.number or '') in todo}")
        self.assertEqual(deadlocked(outcomes), [], outcomes)
        self.assertEqual(outcomes, ["done", "done"], outcomes)
        self.assertTrue(held)
        self.assertEqual(len(recalls), 1, "held by B3, and one recall waits for the Quality Manager")

    def test_a_lift_and_a_verdict_on_another_source_of_the_lot_1(self):
        self._lift_against_a_verdict_on_another_source()

    def test_a_lift_and_a_verdict_on_another_source_of_the_lot_2(self):
        self._lift_against_a_verdict_on_another_source()

    def test_a_lift_and_a_verdict_on_another_source_of_the_lot_3(self):
        self._lift_against_a_verdict_on_another_source()

    def test_a_lift_and_the_quality_managers_decision_on_the_same_recall(self):
        from django.contrib.auth.models import User
        from django.utils import timezone

        from apps.quality.models import Disposition, Inspection
        from apps.quality.recall import lift
        from apps.manufacturing import tests_recall

        made = fixture(self, tests_recall.RecallTestCase)
        rejection = made.reject_b1()
        recall = made.recall_of(made.a)
        Inspection.objects.filter(pk=rejection.pk).update(voided_at=timezone.now(), voided_reason="typo")
        asha = User.objects.get(username="asha")
        outcomes = race(Inspection, lambda: lift(made.b1, "typo"),
                        lambda: Inspection.objects.get(pk=recall.pk).decide(asha, Disposition.CONCESSION, "As is"))
        print(f"\nRACE lift / decide on the same recall: {outcomes}")
        self.assertEqual(deadlocked(outcomes), [], outcomes)

    def test_the_quality_manager_voiding_a_recall_as_a_second_source_is_rejected(self):
        """void(R) files the recall again (refile) with no lot lock; a verdict on the other source tells R's to-do."""
        from django.contrib.auth.models import User

        from apps.quality.models import Inspection

        made, joined, first, draft = self.joined()
        old = Inspection.objects.get(lot=joined, recalled_by__isnull=False, voided_at__isnull=True)
        second = draft(made.b3, "QC-RACE-S3")
        asha = User.objects.get(username="asha")
        outcomes = race(Inspection, lambda: Inspection.objects.get(pk=old.pk).void("filed in error", by=asha),
                        lambda: Inspection.objects.get(pk=second.pk).post())
        held, recalls, todo = self.states(joined)
        print(f"\nRACE void(R) by QM / reject B3: {outcomes}; held {held}; open recalls "
              f"{[r.number for r in recalls]}; open to-dos mention B3's verdict {second.number}: {second.number in todo}")
        self.assertEqual(deadlocked(outcomes), [], outcomes)
        self.assertEqual(len(recalls), 1, "one recall a lot")
        self.assertIn(second.number, todo + " ".join(r.notes for r in recalls),
                      "the Quality Manager's open recall names B3's rejection")


@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class ThePayrollLocks(TransactionTestCase):
    def paid_file(self):
        from apps.hr import tests_pay_outputs
        from apps.hr.bank_file import SalaryTransferFile

        made = fixture(self, tests_pay_outputs.BankFileTests)
        for person in made.people:
            made.banked(person)
        written = SalaryTransferFile.prepare(made.paid_run)
        paid = made.money(made.people[0].party, "23825.50")
        written.mark_paid(paid)
        return made, written, paid

    def test_the_file_payment_voided_while_the_file_is_marked_paid_again_with_it(self):
        from apps.accounting.models import Payment
        from apps.hr.bank_file import SalaryTransferFile

        made, written, paid = self.paid_file()
        day = datetime.date(2026, 7, 1)
        outcomes = race(None, lambda: Payment.objects.get(pk=paid.pk).void(on_date=day),
                        lambda: SalaryTransferFile.objects.get(pk=written.pk).mark_paid(Payment.objects.get(pk=paid.pk)))
        print(f"\nRACE void(file payment) / mark_paid(file, same payment): {outcomes}")
        self.assertEqual(deadlocked(outcomes), [], outcomes)

    def test_the_file_payment_voided_while_a_slip_on_it_is_offered_that_payment(self):
        from apps.accounting.models import Payment
        from apps.hr.payroll import Payslip

        made, written, paid = self.paid_file()
        slip = made.paid_run.payslips.get(employee=made.people[1])
        day = datetime.date(2026, 7, 1)
        outcomes = race(None, lambda: Payment.objects.get(pk=paid.pk).void(on_date=day),
                        lambda: Payslip.objects.get(pk=slip.pk).pay(Payment.objects.get(pk=paid.pk)))
        print(f"\nRACE void(file payment) / Payslip.pay(slip, same payment): {outcomes}")
        self.assertEqual(deadlocked(outcomes), [], outcomes)

    def test_a_returns_receipt_voided_while_the_same_receipt_is_offered_to_return_it_again(self):
        from apps.accounting.models import Payment, PaymentDirection
        from apps.hr.bank_file import SalaryTransfer

        made, written, paid = self.paid_file()
        row = written.rows.get(payslip__employee=made.people[1])
        receipt = made.money(made.people[1].party, "8450.50", direction=PaymentDirection.RECEIPT)
        row.return_transfer(receipt)
        day = datetime.date(2026, 7, 1)
        outcomes = race(None, lambda: Payment.objects.get(pk=receipt.pk).void(on_date=day),
                        lambda: SalaryTransfer.objects.get(pk=row.pk).return_transfer(Payment.objects.get(pk=receipt.pk)))
        print(f"\nRACE void(receipt) / return_transfer(row, same receipt): {outcomes}")
        self.assertEqual(deadlocked(outcomes), [], outcomes)

    def test_a_receipt_voided_while_the_slip_is_paid_by_a_payment_of_its_own(self):
        """The receipt's void follow-up locks the slip; the slip's own pay locks slip then its payment."""
        from apps.accounting.models import Payment, PaymentDirection
        from apps.hr.payroll import Payslip

        made, written, paid = self.paid_file()
        row = written.rows.get(payslip__employee=made.people[1])
        receipt = made.money(made.people[1].party, "8450.50", direction=PaymentDirection.RECEIPT)
        row.return_transfer(receipt)
        own = made.money(made.people[1].party, "8450.50")
        slip = made.paid_run.payslips.get(employee=made.people[1])
        day = datetime.date(2026, 7, 1)
        outcomes = race(None, lambda: Payment.objects.get(pk=receipt.pk).void(on_date=day),
                        lambda: Payslip.objects.get(pk=slip.pk).pay(Payment.objects.get(pk=own.pk)))
        from apps.hr.payroll import PayRun

        print(f"\nRACE void(receipt) / pay(slip, own): {outcomes}; unpaid {made.paid_run.unpaid_net()} "
              f"net pay payable {made.balance(made.net_pay)}")
        self.assertEqual(deadlocked(outcomes), [], outcomes)
        # Whatever order, the books and the slips agree (E2 is not paid twice). The second payment was posted
        # against net pay payable before the race; refused as the slip's (the void went first, so the slip is
        # paid again), it stands there unapplied, 8,450.50 the slips do not owe. Before O257's numbering row was
        # taken first the pay always won this race, and the assertion read only that order (fix_g23b4).
        unapplied = Decimal("8450.50") if outcomes[1] != "done" else Decimal("0")
        self.assertEqual(abs(made.balance(made.net_pay)), made.paid_run.unpaid_net() + unapplied, outcomes)


@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class TheMonthsFiguresAtOnce(TransactionTestCase):
    def test_two_fortnights_of_one_month_posted_at_once_take_the_welfare_fund_once(self):
        from apps.hr import tests_dated_figures
        from apps.hr.payroll import PayRun, PayslipLine
        from apps.hr.statutory_files import lwf_register, pt_register

        made = fixture(self, tests_dated_figures.TheMonthsFiguresAreOwedOnceTests)
        first, second = made.fortnight(1, post=False), made.fortnight(16, post=False)
        outcomes = race(PayRun, lambda: PayRun.objects.get(pk=first.pk).post(),
                        lambda: PayRun.objects.get(pk=second.pk).post())
        june = datetime.date(2026, 6, 1)
        lwf = sum((r["employee_share"] for r in lwf_register(june)), Decimal("0"))
        lwf_er = sum((r["employer_share"] for r in lwf_register(june)), Decimal("0"))
        pt = sum((r["tax"] for r in pt_register(june)), Decimal("0"))
        print(f"\nRACE two June runs posted at once: {outcomes}; LWF {lwf} / {lwf_er}; PT {pt}")
        self.assertEqual(deadlocked(outcomes), [], outcomes)
        self.assertEqual((lwf, lwf_er), (Decimal("3.00"), Decimal("15.00")), outcomes)
        self.assertLessEqual(pt, Decimal("110.00"), outcomes)
