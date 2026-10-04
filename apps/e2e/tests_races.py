"""
Two people doing the same thing at the same moment.

Every posting path read the document's state, decided, and wrote, with
nothing between the read and the write. On PostgreSQL with two threads
held at the decision, ten of eleven paths let both through: an invoice
posted twice put 2,000 on receivables for 1,000; a payment voided twice
took the bank below where it started; one payment settled two invoices;
a pay run posted twice owed everyone double. No single-threaded test
can see any of it.

Each test runs the same action in two threads, each on its own
connection, and holds both at a barrier on a write made after the
checks. Unlocked, both pass their checks and meet there. Locked, the
second waits for the first to commit; the first gives up waiting at the
barrier, commits, and the second then sees what it did and refuses.

PostgreSQL only: SQLite serialises every writer behind one lock, so the
window never opens there and the tests would prove nothing.
"""

import datetime
import threading
import unittest
from decimal import Decimal

from django.db import connection, transaction
from django.db.models.signals import pre_save
from django.test import TransactionTestCase, tag

from apps.accounting.models import JournalEntry, JournalLine, Payment, PaymentDirection
from apps.assets.models import FixedAsset
from apps.assets import tests as assets_fixture
from apps.hr.models import LeaveRequest, LeaveStatus, LeaveType
from apps.hr.payroll import PayRun, Payslip, StatutoryRemittance
from apps.hr import tests_audit as hr_fixture
from apps.hr import tests_statutory as payroll_fixture
from apps.inventory.models import StockAdjustment
from apps.inventory import tests_adjustments as stock_fixture
from apps.manufacturing.orders import MaterialIssue, WorkOrder, WorkOrderStatus
from apps.manufacturing import tests_orders as run_fixture
from apps.purchasing.models import Bill, BillPayment
from apps.purchasing import tests_prepayments as purchase_fixture
# Modules, not classes: a TestCase named here would be collected and run
# again in this module.
from apps.sales.models import (
    DepositApplication,
    Invoice,
    InvoiceLine,
    InvoicePayment,
    InvoicePolicy,
)
from apps.sales import tests_base as sales_fixture


def race(sender, *calls):
    """Run `calls` at once, each held at `sender`'s pre_save until all arrive."""
    barrier = threading.Barrier(len(calls), timeout=3)
    local = threading.local()

    def pause(**kwargs):
        if getattr(local, "armed", False):
            local.armed = False
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass  # The other is waiting on our lock: carry on and commit.

    pre_save.connect(pause, sender=sender, weak=False, dispatch_uid="race")
    outcomes = [None] * len(calls)

    def run(index, call):
        local.armed = True
        try:
            with transaction.atomic():
                call()
            outcomes[index] = "done"
        except Exception as exc:
            outcomes[index] = f"refused: {exc}"
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=pair) for pair in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    pre_save.disconnect(sender=sender, dispatch_uid="race")
    return outcomes


class RaceCase(TransactionTestCase):
    # Not serialized_rollback: restoring the snapshot collides with content
    # types under the parallel runner, and every fixture here builds what
    # it uses.

    def once(self, outcomes):
        self.assertEqual(sorted(o == "done" for o in outcomes), [False, True], outcomes)


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class SalesRaceTests(RaceCase):
    setUp = sales_fixture.SalesTestCase.setUp
    make_order, ship, bill, receipt, balance = (
        sales_fixture.SalesTestCase.make_order, sales_fixture.SalesTestCase.ship, sales_fixture.SalesTestCase.bill,
        sales_fixture.SalesTestCase.receipt, sales_fixture.SalesTestCase.balance)

    def test_an_invoice_posts_once(self):
        draft = self.make_order("10", "100").create_invoice(
            self.ar, invoice_date=datetime.date(2026, 3, 1))
        self.once(race(JournalEntry, *[lambda: Invoice.objects.get(pk=draft.pk).post()] * 2))
        self.assertEqual(self.balance(self.ar), Decimal("1000.00"))

    def free_invoice(self, amount="1000"):
        """An invoice typed in, against no order: nothing else is locked for it."""
        invoice = Invoice.objects.create(
            customer=self.customer, invoice_date=datetime.date(2026, 3, 1),
            receivable_account=self.ar, currency=self.usd)
        InvoiceLine.objects.create(invoice=invoice, description="Service", quantity=Decimal("1"),
                                   unit_price=Decimal(amount), revenue_account=self.revenue)
        return invoice

    def test_an_invoice_against_no_order_posts_once(self):
        draft = self.free_invoice()
        self.once(race(JournalEntry, *[lambda: Invoice.objects.get(pk=draft.pk).post()] * 2))
        self.assertEqual(self.balance(self.ar), Decimal("1000.00"))

    def test_one_deposit_is_not_drawn_by_two_invoices_at_once(self):
        """Drawn by hand onto invoices of no order, where no order lock helps."""
        deposit = self.make_order("10", "100").create_down_payment_invoice(self.ar, percent=30)
        deposit.post()
        invoices = [self.free_invoice("500") for _ in range(2)]
        for invoice in invoices:
            invoice.post()
        self.once(race(DepositApplication, *[
            lambda pk=invoice.pk: Invoice.objects.get(pk=pk).apply_deposit(
                Invoice.objects.get(pk=deposit.pk), amount=Decimal("300"))
            for invoice in invoices]))
        self.assertEqual(self.balance(self.deposits), Decimal("0.00"))

    def test_a_journal_entry_posts_once(self):
        entry = JournalEntry.objects.create(date=datetime.date(2026, 3, 1), memo="x")
        JournalLine.objects.create(entry=entry, account=self.bank, debit=Decimal("5"))
        JournalLine.objects.create(entry=entry, account=self.revenue, credit=Decimal("5"))
        self.once(race(JournalEntry, *[lambda: JournalEntry.objects.get(pk=entry.pk).post()] * 2))

    def test_a_payment_posts_once(self):
        payment = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.RECEIPT,
            payment_date=datetime.date(2026, 3, 1), amount=Decimal("100"), currency=self.usd,
            bank_account=self.bank, counterpart_account=self.ar)
        self.once(race(JournalEntry, *[lambda: Payment.objects.get(pk=payment.pk).post()] * 2))
        self.assertEqual(self.balance(self.bank), Decimal("100.00"))

    def test_a_payment_is_voided_once(self):
        payment = self.receipt("100")
        self.once(race(JournalEntry, *[lambda: Payment.objects.get(pk=payment.pk).void()] * 2))
        self.assertEqual(self.balance(self.bank), Decimal("0.00"))

    def test_an_invoice_is_not_paid_twice_over(self):
        invoice = self.bill(self.make_order("10", "100"))
        first, second = self.receipt("1000"), self.receipt("1000")
        self.once(race(InvoicePayment, *[
            lambda payment=payment: InvoicePayment.objects.create(
                invoice_id=invoice.pk, payment_id=payment.pk, amount=Decimal("1000"))
            for payment in (first, second)]))

    def test_a_payment_is_not_spent_twice(self):
        invoices = [self.bill(self.make_order("10", "100")) for _ in range(2)]
        payment = self.receipt("1000")
        self.once(race(InvoicePayment, *[
            lambda invoice=invoice: InvoicePayment.objects.create(
                invoice_id=invoice.pk, payment_id=payment.pk, amount=Decimal("1000"))
            for invoice in invoices]))

    def test_an_invoice_is_credited_once(self):
        invoice = self.bill(self.make_order("10", "100"))
        self.once(race(Invoice, *[
            lambda: Invoice.objects.get(pk=invoice.pk).create_credit_note()] * 2))
        self.assertEqual(self.balance(self.ar), Decimal("0.00"))

    def test_a_deposit_is_drawn_down_once(self):
        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        order.create_down_payment_invoice(self.ar, percent=30).post()
        self.ship(order, "5")
        first = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 1))
        self.ship(order, "5")
        second = Invoice.objects.create(
            customer=self.customer, invoice_date=datetime.date(2026, 3, 1),
            receivable_account=self.ar, currency=self.usd, sales_order=order)
        InvoiceLine.objects.create(invoice=second, order_line=order.lines.get(), item=self.item,
                                   quantity=Decimal("5"), unit_price=Decimal("100"),
                                   revenue_account=self.revenue)
        race(DepositApplication, *[lambda pk=pk: Invoice.objects.get(pk=pk).post()
                                   for pk in (first.pk, second.pk)])
        self.assertEqual(
            (sum(a.amount for a in DepositApplication.objects.all()),
             self.balance(self.deposits)),
            (Decimal("300.00"), Decimal("0.00")))


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class PurchasingRaceTests(RaceCase):
    make_order, receive, balance = (purchase_fixture.PrepaymentTestCase.make_order,
                                    purchase_fixture.PrepaymentTestCase.receive,
                                    purchase_fixture.PrepaymentTestCase.balance)

    def setUp(self):
        # PrepaymentTestCase.setUp calls super(), which cannot be borrowed;
        # its base, and the one account it adds that these need.
        from apps.accounting.models import Account, AccountType

        purchase_fixture.PurchasingLifecycleTestCase.setUp(self)
        self.bank = Account.objects.create(code="1010", name="Bank",
                                           account_type=AccountType.ASSET)

    def billed(self):
        order = self.make_order("10", "5")
        self.receive(order, "10")
        return order.create_bill(self.payable, bill_date=datetime.date(2026, 1, 10))

    def test_a_bill_posts_once(self):
        draft = self.billed()
        self.once(race(JournalEntry, *[lambda: Bill.objects.get(pk=draft.pk).post()] * 2))
        self.assertEqual(self.balance(self.payable), Decimal("-50.00"))

    def test_a_payment_is_not_spent_on_two_bills(self):
        bills = []
        for _ in range(2):
            bill = self.billed()
            bill.post()
            bills.append(bill)
        payment = Payment.objects.create(
            party=self.vendor, direction=PaymentDirection.DISBURSEMENT,
            payment_date=datetime.date(2026, 1, 15), amount=Decimal("50"), currency=self.usd,
            bank_account=self.bank, counterpart_account=self.payable)
        payment.post()
        self.once(race(BillPayment, *[
            lambda bill=bill: BillPayment.objects.create(
                bill_id=bill.pk, payment_id=payment.pk, amount=Decimal("50"))
            for bill in bills]))


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class PayrollRaceTests(RaceCase):
    setUp = payroll_fixture.StatutoryTestCase.setUp
    percent, person, paid, run_for, balance = (
        payroll_fixture.StatutoryTestCase.percent, payroll_fixture.StatutoryTestCase.person, payroll_fixture.StatutoryTestCase.paid,
        payroll_fixture.StatutoryTestCase.run_for, payroll_fixture.StatutoryTestCase.balance)

    def payment(self, account, amount, party):
        payment = Payment.objects.create(
            party=party, direction=PaymentDirection.DISBURSEMENT, payment_date=payroll_fixture.JULY_15,
            amount=Decimal(amount), currency=self.inr, bank_account=self.bank,
            counterpart_account=account)
        payment.post()
        return payment

    def test_a_pay_run_posts_once(self):
        run = self.run_for(*payroll_fixture.JUNE, post=False)
        self.once(race(JournalEntry, *[lambda: PayRun.objects.get(pk=run.pk).post()] * 2))

    def test_a_payslip_is_paid_once(self):
        slip = self.run_for(*payroll_fixture.JUNE).payslips.get(employee=self.a)
        payments = [self.payment(self.net_pay, slip.net(), self.a.party) for _ in range(2)]
        self.once(race(Payslip, *[
            lambda payment=payment: Payslip.objects.get(pk=slip.pk).pay(
                Payment.objects.get(pk=payment.pk))
            for payment in payments]))

    def test_a_month_is_remitted_once(self):
        from apps.core.models import Party

        self.run_for(*payroll_fixture.JUNE)
        esic = Party.objects.create(code="ESIC", name="ESIC")
        payments = [self.payment(self.esi_payable, "893", esic) for _ in range(2)]
        self.once(race(StatutoryRemittance, *[
            lambda payment=payment: StatutoryRemittance.objects.create(
                payment_id=payment.pk, liability_account=self.esi_payable,
                period=payroll_fixture.JUNE[0], amount=Decimal("893"))
            for payment in payments]))


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class LeaveRaceTests(RaceCase):
    setUp = hr_fixture.AuditTestCase.setUp
    employee = hr_fixture.AuditTestCase.employee

    def test_a_request_is_approved_once(self):
        """
        Two requests cannot share the last days even one after the other:
        a pending request already holds its days. The race left is the
        same request decided twice.
        """
        request = LeaveRequest.objects.create(
            employee=self.employee("L1"), policy=self.policy, leave_type=LeaveType.VACATION,
            start_date=datetime.date(2026, 6, 1), end_date=datetime.date(2026, 6, 5))
        self.once(race(LeaveRequest, *[
            lambda: LeaveRequest.objects.get(pk=request.pk).approve(by=self.boss)] * 2))
        request.refresh_from_db()
        self.assertEqual(request.status, LeaveStatus.APPROVED)


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class StockAndPlantRaceTests(RaceCase):
    def test_an_adjustment_posts_once(self):
        stock_fixture.AdjustmentTestCase.setUp(self)
        stock_fixture.AdjustmentTestCase.stock(self)
        draft = stock_fixture.AdjustmentTestCase.adjust(self, "-10", post=False)
        self.once(race(JournalEntry, *[
            lambda: StockAdjustment.objects.get(pk=draft.pk).post()] * 2))
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("90"))

    def test_an_asset_is_disposed_of_once(self):
        assets_fixture.AssetTestCase.setUp(self)
        asset = assets_fixture.AssetTestCase.asset(self)
        self.once(race(JournalEntry, *[
            lambda: FixedAsset.objects.get(pk=asset.pk).dispose(
                on_date=datetime.date(2026, 6, 15))] * 2))


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class RunRaceTests(RaceCase):
    setUp = run_fixture.RunTestCase.setUp
    stock, order, issue, produce, balance, full_issue = (
        run_fixture.RunTestCase.stock, run_fixture.RunTestCase.order,
        run_fixture.RunTestCase.issue, run_fixture.RunTestCase.produce,
        run_fixture.RunTestCase.balance, run_fixture.RunTestCase.full_issue)

    def test_nothing_lands_in_a_run_as_it_closes(self):
        """
        Material issued while the order closes: either the issue waits and
        the close counts it, or it is refused. Never in WIP after the close.
        """
        order = self.order("10")
        order.release(run_fixture.TODAY)
        self.full_issue(order).post()
        self.produce(order, "10").post()
        late = self.issue(order, [(order.components.first().item, "1")])
        race(JournalEntry, lambda: MaterialIssue.objects.get(pk=late.pk).post(),
             lambda: WorkOrder.objects.get(pk=order.pk).close(on_date=run_fixture.TODAY))
        order.refresh_from_db()
        self.assertEqual(order.status, WorkOrderStatus.CLOSED)
        self.assertEqual(self.balance(self.wip), Decimal("0.00"))
