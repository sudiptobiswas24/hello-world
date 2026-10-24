"""
review_people race probes (PostgreSQL only): payroll's go-live outputs at fbfc49d.

Each one holds two requests at the decision (race()) and then asks the state they leave: money that was
paid twice, a receivable that does not foot, a file that disagrees with its details.
"""

import datetime
import unittest
from decimal import Decimal

from django.db import connection
from django.test import tag

from apps.accounting.models import JournalEntry, Payment, PaymentDirection
from apps.hr import tests_pay_outputs
from apps.hr.advances import SalaryAdvance
from apps.hr.bank_file import SalaryTransferFile
from apps.hr.pay_details import EmployeePayDetails
from apps.hr.payroll import PayRun, PayRunStatus, PayslipLine, Payslip

from .tests_races import RaceCase, fixture
from .tests_races import race as _race


def race(*args, **kwargs):
    outcomes = _race(*args, **kwargs)
    print("OUTCOMES", outcomes, flush=True)
    return outcomes

D = datetime.date


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class PayrollOutputsUnderRace(RaceCase):
    def bank_file_fixture(self):
        made = fixture(self, tests_pay_outputs.BankFileTests)
        for person in made.people:
            made.banked(person)
        return made

    def owed(self, made):
        return -made.balance(made.net_pay)

    def test_R1_an_advance_given_as_a_run_posts(self):
        made = fixture(self, tests_pay_outputs.AdvancesTests)
        june = made.month(6, post=False)
        payment = made.money(made.person.party, "5000", counterpart=made.advances, day=D(2026, 5, 20))

        def give():
            SalaryAdvance.objects.create(
                employee=made.person, payment=Payment.objects.get(pk=payment.pk), component=made.recovery,
                amount=Decimal("5000"), instalment=Decimal("2000"), recover_from=D(2026, 6, 1))

        outcomes = race((SalaryAdvance, JournalEntry), give, lambda: PayRun.objects.get(pk=june.pk).post())
        advance = SalaryAdvance.objects.get()
        self.assertEqual(made.balance(made.advances), advance.balance(), outcomes)

    def test_R2_the_payment_of_an_advance_voided_as_a_run_recovers_from_it(self):
        made = fixture(self, tests_pay_outputs.AdvancesTests)
        advance = made.give()
        june = made.month(6, post=False)
        outcomes = race((Payment, JournalEntry),
                        lambda: Payment.objects.get(pk=advance.payment_id).void(on_date=D(2026, 6, 2)),
                        lambda: PayRun.objects.get(pk=june.pk).post())
        voided = Payment.objects.get(pk=advance.payment_id).is_voided()
        recovered = PayslipLine.objects.filter(advance=advance, payslip__run__status=PayRunStatus.POSTED).exists()
        self.assertFalse(voided and recovered,
                         f"{outcomes}: the money was never given and a run took 2000 of it back; "
                         f"receivable {made.balance(made.advances)}")

    def test_R3_a_bank_file_is_written_as_a_slip_is_paid(self):
        made = self.bank_file_fixture()
        own = made.money(made.people[1].party, "8450.50")
        outcomes = race((SalaryTransferFile, Payslip),
                        lambda: SalaryTransferFile.prepare(made.paid_run),
                        lambda: made.paid_run.payslips.get(employee=made.people[1]).pay(Payment.objects.get(pk=own.pk)))
        written = SalaryTransferFile.objects.filter(run=made.paid_run).first()
        if written is not None:
            stray = made.money(made.people[0].party, str(written.total))
            try:
                SalaryTransferFile.objects.get(pk=written.pk).mark_paid(Payment.objects.get(pk=stray.pk))
            except Exception:
                Payment.objects.get(pk=stray.pk).void(on_date=D(2026, 7, 1))
        run = PayRun.objects.get(pk=made.paid_run.pk)
        self.assertGreaterEqual(made.balance(made.net_pay), Decimal("-100000"))
        self.assertEqual((self.owed(made), run.unpaid_net()), (self.owed(made),) * 2)
        self.assertEqual(self.owed(made), run.unpaid_net(), f"{outcomes}: the ledger and the slips disagree")

    def test_R4_two_bank_files_for_one_run_written_at_once(self):
        made = self.bank_file_fixture()
        outcomes = race(SalaryTransferFile, *[lambda: SalaryTransferFile.prepare(made.paid_run)] * 2)
        self.once(outcomes)

    def test_R5_pay_details_edited_as_a_file_is_written(self):
        made = self.bank_file_fixture()
        details = EmployeePayDetails.objects.get(employee=made.people[1])

        def edit():
            row = EmployeePayDetails.objects.get(pk=details.pk)
            row.account_number, row.ifsc = "777700001111", "HDFC0000001"
            row.save()

        outcomes = race((SalaryTransferFile, EmployeePayDetails), lambda: SalaryTransferFile.prepare(made.paid_run), edit)
        written = SalaryTransferFile.objects.get()
        row = written.rows.get(payslip__employee=made.people[1])
        self.assertTrue(all(o == "done" for o in outcomes), outcomes)
        self.assertIn(row.account_number, (f"3001{made.people[1].pk:08d}", "777700001111"))
        self.assertIn(row.account_number, written.text)
        self.assertEqual(((row.account_number, row.ifsc) in ((f"3001{made.people[1].pk:08d}", "SBIN0001234"),
                                                             ("777700001111", "HDFC0000001"))), True)

    def test_R6_a_file_is_paid_as_its_run_is_voided(self):
        made = self.bank_file_fixture()
        written = SalaryTransferFile.prepare(made.paid_run)
        payment = made.money(made.people[0].party, "23825.50")
        outcomes = race((Payslip, JournalEntry),
                        lambda: SalaryTransferFile.objects.get(pk=written.pk).mark_paid(Payment.objects.get(pk=payment.pk)),
                        lambda: PayRun.objects.get(pk=made.paid_run.pk).void(on_date=D(2026, 7, 1)))
        run = PayRun.objects.get(pk=made.paid_run.pk)
        paid = [slip.is_paid() for slip in run.payslips.all()]
        self.assertFalse(run.is_voided() and any(paid), f"{outcomes}: a voided run with paid slips")


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class PayrollOutputsInTheBadOrder(RaceCase):
    def test_R2b_the_payment_of_an_advance_voided_after_the_run_checked_it_and_before_the_run_posts(self):
        """
        The run has re-worked every slip (the advance stands, its payment is good) and is about to write its
        entry; the advance's payment is voided and committed in that window; the run then posts. Payment.void
        holds the payment, not the employee, and the run is not yet posted for its follow-up to see.
        """
        import threading

        from django.db import transaction
        from unittest.mock import patch

        made = fixture(self, tests_pay_outputs.AdvancesTests)
        advance = made.give()
        june = made.month(6, post=False)
        reached, go, outcomes = threading.Event(), threading.Event(), {}
        real = PayRun._check_not_already_paid

        def paused(run):
            result = real(run)
            reached.set()
            go.wait(20)
            return result

        def posting():
            try:
                with patch.object(PayRun, "_check_not_already_paid", paused), transaction.atomic():
                    PayRun.objects.get(pk=june.pk).post()
                outcomes["post"] = "done"
            except Exception as exc:
                outcomes["post"] = f"refused: {exc}"
            finally:
                connection.close()

        def voiding():
            try:
                reached.wait(20)
                with transaction.atomic():
                    Payment.objects.get(pk=advance.payment_id).void(on_date=D(2026, 6, 2))
                outcomes["void"] = "done"
            except Exception as exc:
                outcomes["void"] = f"refused: {exc}"
            finally:
                go.set()
                connection.close()

        threads = [threading.Thread(target=posting), threading.Thread(target=voiding)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        print("OUTCOMES", outcomes, flush=True)
        voided = Payment.objects.get(pk=advance.payment_id).is_voided()
        recovered = PayslipLine.objects.filter(advance=advance, payslip__run__status=PayRunStatus.POSTED).exists()
        self.assertFalse(voided and recovered,
                         f"{outcomes}: the payment is voided, the run took 2000 of the advance back; the receivable "
                         f"reads {made.balance(made.advances)} and the advance's balance {advance.balance()}")
