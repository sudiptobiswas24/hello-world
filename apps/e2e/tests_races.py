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

import contextlib
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
from apps.manufacturing.orders import (
    MaterialIssue,
    ProductionEntry,
    TimeBooking,
    WorkOrder,
    WorkOrderStatus,
)
from apps.manufacturing.outside import OutsideMovement
from apps.manufacturing import tests_orders as run_fixture
from apps.purchasing.models import Bill, BillPayment, BillPolicy, PurchaseOrder, PurchaseOrderLine
from apps.purchasing import tests_prepayments as purchase_fixture
from apps.purchasing import tests_tds as tds_fixture
# Modules, not classes: a TestCase named here would be collected and run
# again in this module.
from apps.sales.models import (
    DepositApplication,
    Invoice,
    InvoiceLine,
    InvoicePayment,
    InvoicePolicy,
    InvoiceWriteOff,
    SalesOrder,
    SalesOrderLine,
)
from apps.sales import tests_base as sales_fixture


def race(sender, *calls, atomic=True):
    """
    Run `calls` at once, each held at `sender`'s pre_save until all arrive.

    Each in a transaction of its own, as a request's write is; `atomic=False`
    for a step whose endpoint runs it outside one, so what it commits before
    refusing stays committed. `sender` may be a tuple of models, for two
    steps whose first write after their checks is to different models: each
    is held at the first of them it saves.
    """
    senders = sender if isinstance(sender, tuple) else (sender,)
    barrier = threading.Barrier(len(calls), timeout=3)
    local = threading.local()

    def pause(**kwargs):
        if getattr(local, "armed", False):
            local.armed = False
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass  # The other is waiting on our lock: carry on and commit.

    for each in senders:
        pre_save.connect(pause, sender=each, weak=False, dispatch_uid=("race", each))
    outcomes = [None] * len(calls)

    def run(index, call):
        local.armed = True
        try:
            with transaction.atomic() if atomic else contextlib.nullcontext():
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
    for each in senders:
        pre_save.disconnect(sender=each, dispatch_uid=("race", each))
    return outcomes


def fixture(test, cls):
    """
    `cls`'s own setUp, run as a `cls`: its helpers and what it builds, for
    a fixture whose setUp calls super() and so cannot be borrowed.
    """
    made = cls()
    made.setUp()
    test.addCleanup(made.doCleanups)
    return made


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

    def test_a_write_off_is_recovered_once(self):
        invoice = self.bill(self.make_order("10", "100"))
        invoice.write_off(reason="Liquidated")
        write_off = invoice.write_offs.get()
        self.once(race(JournalEntry, *[
            lambda: Invoice.objects.get(pk=invoice.pk).recover_write_off(
                InvoiceWriteOff.objects.get(pk=write_off.pk))] * 2))
        self.assertEqual((self.balance(self.bad_debt), self.balance(self.ar)),
                         (Decimal("0.00"), Decimal("1000.00")))

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

    def test_an_order_is_not_cancelled_as_its_draft_invoice_posts(self):
        """
        Each held at its first write after deciding (any model's): the cancel at the order's
        status, the post at its number. One wins; the other, waiting on the order, then sees it.
        """
        order = self.make_order("10", "100")
        draft = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 1))
        self.once(race(None, lambda: SalesOrder.objects.get(pk=order.pk).cancel(),
                       lambda: Invoice.objects.get(pk=draft.pk).post()))
        order.refresh_from_db()
        posted = Invoice.objects.get(pk=draft.pk).posted
        self.assertIn((order.status, posted, self.balance(self.ar)),
                      [("cancelled", False, Decimal("0")), ("confirmed", True, Decimal("1000.00"))])

    def test_an_order_is_not_cut_under_a_deposit_posting_at_once(self):
        """
        10 x 100 cut to 5 while a deposit of 800 posts. The cut holds the line and the order
        before writing, the post holds the order before weighing its room: whichever goes second
        sees the other, and either the cut or the deposit stands, never both.
        """
        order = self.make_order("10", "100")
        draft = order.create_down_payment_invoice(self.ar, amount=Decimal("800"))
        line_pk = order.lines.get().pk

        def cut():
            line = SalesOrderLine.objects.get(pk=line_pk)
            line.quantity = Decimal("5")
            line.save()

        self.once(race(None, cut, lambda: Invoice.objects.get(pk=draft.pk).post()))
        self.assertIn((SalesOrderLine.objects.get(pk=line_pk).quantity, Invoice.objects.get(pk=draft.pk).posted),
                      [(Decimal("5"), False), (Decimal("10"), True)])

    def test_two_orders_do_not_both_ship_the_last_of_the_shelf(self):
        # Five hundred on the shelf, nothing reserved, three hundred on each
        # of two orders. The shelf was read before the positions were held,
        # so both found five hundred and the second left minus a hundred.
        from apps.inventory.models import StockMovement
        from apps.sales.models import Delivery, DeliveryLine

        drafts = []
        for order in (self.make_order("300", "10"), self.make_order("300", "10")):
            delivery = Delivery.objects.create(sales_order=order, delivery_date=datetime.date(2026, 3, 3))
            DeliveryLine.objects.create(delivery=delivery, order_line=order.lines.get(),
                                        warehouse=self.warehouse, quantity_shipped=Decimal("300"))
            drafts.append(delivery.pk)
        self.once(race(StockMovement, *[lambda pk=pk: Delivery.objects.get(pk=pk).post() for pk in drafts]))
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("200"))


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
                                           account_type=AccountType.ASSET, holds_money=True)

    def test_two_bills_do_not_both_catch_up_the_year(self):
        """
        Three untaxed bills of 25,000 under 194C, then two of 30,000 deducted
        at once. Whichever goes first catches the year up (1,05,000 or
        1,35,000 of base); the other takes only itself, or finds itself
        already covered. Either way the year is taxed once: 1,35,000 and 2,700.
        """
        from apps.accounting.models import Account, AccountType, PartyTaxProfile, TdsSection
        from apps.core.models import Party, PartyRole, PartyRoleAssignment
        from apps.purchasing.models import TdsDeduction

        payable = Account.objects.create(code="2250", name="TDS payable", account_type=AccountType.LIABILITY)
        section = TdsSection.objects.create(
            code="194C", name="Contractors", rate_percent=Decimal("2"), no_pan_rate_percent=Decimal("20"),
            mode="whole", single_threshold=Decimal("30000"), annual_threshold=Decimal("100000"),
            payable_account=payable)
        party = Party.objects.create(code="V-C", name="Loom Fitters")
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.VENDOR)
        PartyTaxProfile.objects.create(party=party, pan="AAAPL1234C", tds_section=section)
        for _ in range(3):
            tds_fixture.TdsTestCase.bill(self, "25000", vendor=party)
        last = [tds_fixture.TdsTestCase.bill(self, "30000", vendor=party) for _ in range(2)]
        race(JournalEntry, *[lambda pk=bill.pk: Bill.objects.get(pk=pk).deduct_tds() for bill in last])
        standing = TdsDeduction.objects.filter(reversed_entry__isnull=True)
        self.assertEqual((sum(row.base for row in standing), sum(row.amount for row in standing)),
                         (Decimal("135000.00"), Decimal("2700.00")))

    def billed(self):
        order = self.make_order("10", "5")
        self.receive(order, "10")
        return order.create_bill(self.payable, bill_date=datetime.date(2026, 1, 10))

    def test_a_bill_posts_once(self):
        draft = self.billed()
        self.once(race(JournalEntry, *[lambda: Bill.objects.get(pk=draft.pk).post()] * 2))
        self.assertEqual(self.balance(self.payable), Decimal("-50.00"))

    def test_an_order_is_not_cancelled_as_its_draft_bill_posts(self):
        """Sales' race, mirrored: billed as ordered, nothing received, so only the order decides."""
        from apps.core.models import Company

        company = Company.get()
        company.default_purchase_expense_account = self.expense
        company.save()
        order = self.make_order("10", "5")
        order.bill_policy = BillPolicy.ORDERED
        order.save()
        draft = order.create_bill(self.payable, bill_date=datetime.date(2026, 1, 10))
        self.once(race(None, lambda: PurchaseOrder.objects.get(pk=order.pk).cancel(),
                       lambda: Bill.objects.get(pk=draft.pk).post()))
        order.refresh_from_db()
        posted = Bill.objects.get(pk=draft.pk).posted
        self.assertIn((order.status, posted, self.balance(self.payable)),
                      [("cancelled", False, Decimal("0")), ("confirmed", True, Decimal("-50.00"))])

    def test_an_order_is_not_cut_under_a_prepayment_posting_at_once(self):
        """Sales' race, mirrored: 10 x 5 cut to 5 while 40 is paid up front."""
        from apps.accounting.models import Account, AccountType
        from apps.core.models import Company

        company = Company.get()
        company.vendor_prepayment_account = Account.objects.create(
            code="1400", name="Vendor Prepayments", account_type=AccountType.ASSET)
        company.save()
        order = self.make_order("10", "5")
        draft = order.create_prepayment_bill(self.payable, amount=Decimal("40"))
        line_pk = order.lines.get().pk

        def cut():
            line = PurchaseOrderLine.objects.get(pk=line_pk)
            line.quantity = Decimal("5")
            line.save()

        self.once(race(None, cut, lambda: Bill.objects.get(pk=draft.pk).post()))
        self.assertIn((PurchaseOrderLine.objects.get(pk=line_pk).quantity, Bill.objects.get(pk=draft.pk).posted),
                      [(Decimal("5"), False), (Decimal("10"), True)])

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

    def test_a_run_is_not_voided_as_its_dues_are_paid_over(self):
        """
        Each held at its first write after deciding: the void at its reversal, the remittance at
        itself. Both hold the ESI account, so whichever goes second sees the other: the run
        stands with the dues paid over, or goes with nothing paid over against it.
        """
        from apps.core.models import Party

        run = self.run_for(*payroll_fixture.JUNE)
        payment = self.payment(self.esi_payable, "893", Party.objects.create(code="ESIC", name="ESIC"))
        self.once(race(None, lambda: PayRun.objects.get(pk=run.pk).void(on_date=payroll_fixture.JULY_15),
                       lambda: StatutoryRemittance.objects.create(
                           payment_id=payment.pk, liability_account=self.esi_payable,
                           period=payroll_fixture.JUNE[0], amount=Decimal("893"))))
        self.assertIn((PayRun.objects.get(pk=run.pk).is_voided(), StatutoryRemittance.objects.count()),
                      [(True, 0), (False, 1)])


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


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class NothingIsTakenBackOutOfARunAsItClosesTests(RaceCase):
    """
    A void, or a vendor's movement, read the run as released without its
    lock while the close summed the run including that document and
    committed; the void then committed its reversal into work in progress
    after the close had cleared it. Either the void waits and is refused,
    or the close waits and counts it: work in progress ends at nothing.
    """

    def closing(self, made, order, take_back):
        race(JournalEntry, take_back,
             lambda: WorkOrder.objects.get(pk=order.pk).close(on_date=run_fixture.TODAY))
        order.refresh_from_db()
        self.assertEqual(order.status, WorkOrderStatus.CLOSED)
        self.assertEqual(made.balance(made.wip), Decimal("0.00"))

    def test_an_issue_voided_as_the_run_closes(self):
        made = fixture(self, run_fixture.RunTestCase)
        order = made.order("10")
        order.release(run_fixture.TODAY)
        issue = made.full_issue(order)
        issue.post()
        self.closing(made, order, lambda: MaterialIssue.objects.get(pk=issue.pk).void(
            on_date=run_fixture.TODAY))

    def test_output_voided_as_the_run_closes(self):
        made = fixture(self, run_fixture.RunTestCase)
        order = made.order("10")
        order.release(run_fixture.TODAY)
        made.full_issue(order).post()
        entry = made.produce(order, "10")
        entry.post()
        self.closing(made, order, lambda: ProductionEntry.objects.get(pk=entry.pk).void(
            on_date=run_fixture.TODAY))

    def test_a_booking_voided_as_the_run_closes(self):
        from apps.manufacturing import tests_conversion

        made = fixture(self, tests_conversion.ConversionTestCase)
        order = made.order("1000")
        order.release(run_fixture.TODAY)
        booking = made.book(order, "60")
        booking.post()
        self.closing(made, order, lambda: TimeBooking.objects.get(pk=booking.pk).void(
            on_date=run_fixture.TODAY))

    def test_vendor_work_voided_as_the_run_closes(self):
        from apps.manufacturing import tests_outside

        made = fixture(self, tests_outside.OutsideTestCase)
        order = made.released()
        movement = made.back(order, "1000", "2000")
        self.closing(made, order, lambda: OutsideMovement.objects.get(pk=movement.pk).void(
            on_date=run_fixture.TODAY))

    def test_vendor_work_booked_as_the_run_closes(self):
        from apps.manufacturing import tests_outside

        made = fixture(self, tests_outside.OutsideTestCase)
        order = made.released()
        draft = OutsideMovement.objects.create(
            operation=order.operations.get(is_outside=True), movement_date=run_fixture.TODAY,
            quantity=Decimal("1000"), value=Decimal("2000"), credit_account=made.grni,
        )
        self.closing(made, order, lambda: OutsideMovement.objects.get(pk=draft.pk).post())


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class OneDecisionAtATimeRaceTests(RaceCase):
    """
    The steps the audit's lock check found once it asked every public step
    rather than a list of verbs: each read its state, decided and wrote,
    with nothing to stop a second press doing the same in between.
    """

    def test_a_quote_is_accepted_into_one_order(self):
        from apps.sales.models import Quotation, SalesOrder
        from apps.sales import tests_audit_fixes

        quotation = fixture(self, tests_audit_fixes.AcceptedQuotationTests).make_quotation()
        self.once(race(SalesOrder, *[lambda: Quotation.objects.get(pk=quotation.pk).accept(
            order_date=datetime.date(2026, 3, 2))] * 2))
        self.assertEqual(SalesOrder.objects.count(), 1)

    def test_a_settlement_discount_is_written_off_once(self):
        from apps.sales import tests_audit_round2

        made = fixture(self, tests_audit_round2.SettlementDiscountTests)
        invoice = made.bill(made.make_order("10", "100"))
        self.once(race(JournalEntry, *[lambda: Invoice.objects.get(pk=invoice.pk).apply_settlement_discount(
            on_date=datetime.date(2026, 3, 8))] * 2))
        self.assertEqual(Invoice.objects.get(pk=invoice.pk).amount_due(), Decimal("980.00"))

    def test_a_vendor_settlement_discount_is_taken_once(self):
        from apps.purchasing import tests_audit

        made = fixture(self, tests_audit.VendorSettlementDiscountTests)
        bill = made.discounted_bill()
        self.once(race(JournalEntry, *[lambda: Bill.objects.get(pk=bill.pk).take_settlement_discount(
            on_date=datetime.date(2026, 1, 15))] * 2))
        self.assertEqual(made.balance(made.discount_received), Decimal("-1.00"))

    def landed(self):
        from apps.purchasing import tests_returns_and_landed

        made = fixture(self, tests_returns_and_landed.OneChargeLandsOnceTests)
        order, receipt = made.goods_received("10", "5")
        bill, charge = made.carrier_bill("80")
        return made, receipt.lines.get(), charge

    def test_a_charge_is_capitalised_once(self):
        from apps.purchasing.models import BillLine

        made, _, charge = self.landed()
        self.once(race(FixedAsset, *[
            lambda: BillLine.objects.get(pk=charge.pk).capitalise_as_asset(made.category)] * 2))
        self.assertEqual(made.balance(made.category.asset_account), Decimal("80.00"))

    def test_a_charge_lands_on_the_goods_once(self):
        from apps.purchasing.models import BillLine, GoodsReceiptLine, LandedCostApplication

        made, line, charge = self.landed()
        self.once(race(LandedCostApplication, *[lambda: BillLine.objects.get(pk=charge.pk).allocate_landed_cost(
            [GoodsReceiptLine.objects.get(pk=line.pk)])] * 2))
        self.assertEqual(made.balance(made.freight_expense), Decimal("0.00"))
        self.assertEqual(made.item.stock_value_at(made.warehouse), Decimal("130.00"))

    def test_a_charge_goes_on_the_goods_or_a_machine_not_both(self):
        """Either wins; together they put 80.00 of freight in two places."""
        from apps.purchasing.models import BillLine, GoodsReceiptLine

        made, line, charge = self.landed()
        self.once(race(JournalEntry,
                       lambda: BillLine.objects.get(pk=charge.pk).allocate_landed_cost(
                           [GoodsReceiptLine.objects.get(pk=line.pk)]),
                       lambda: BillLine.objects.get(pk=charge.pk).capitalise_as_asset(made.category)))
        self.assertEqual(made.balance(made.freight_expense), Decimal("0.00"))
        self.assertEqual(made.balance(made.category.asset_account) + made.item.stock_value_at(made.warehouse),
                         Decimal("130.00"))

    def test_a_transfer_leaves_the_shelf_once(self):
        from apps.inventory.models import StockMovement, StockTransfer
        from apps.inventory import tests_transfers

        made = fixture(self, tests_transfers.TransferTestCase)
        made.stock("100", "5")
        move = made.transfer("30", transit=True)
        self.once(race(StockMovement, *[lambda: StockTransfer.objects.get(pk=move.pk).dispatch()] * 2))
        self.assertEqual(made.item.on_hand_at(made.north), Decimal("70"))

    def test_a_delivery_leaves_one_backorder(self):
        from apps.sales.models import Delivery
        from apps.sales import tests_lifecycle

        made = fixture(self, tests_lifecycle.BackorderTests)
        made.stock_up()
        delivery = made.ship(made.make_order("10"), "4")
        self.once(race(Delivery, *[lambda: Delivery.objects.get(pk=delivery.pk).create_backorder()] * 2))
        self.assertEqual(Delivery.objects.filter(backorder_of=delivery).count(), 1)

    def test_an_order_is_confirmed_once_on_one_number(self):
        from apps.purchasing.models import PurchaseOrder
        from apps.purchasing import tests_lifecycle

        made = fixture(self, tests_lifecycle.PurchasingLifecycleTestCase)
        order = made.make_order(confirm=False)
        self.once(race(PurchaseOrder, *[lambda: PurchaseOrder.objects.get(pk=order.pk).confirm()] * 2))
        later = made.make_order(confirm=False)
        later.confirm()
        self.assertEqual((PurchaseOrder.objects.get(pk=order.pk).number, later.number),
                         ("PO-2026-00001", "PO-2026-00002"))

    def test_a_week_is_committed_into_one_run(self):
        from apps.planning.mps import MasterScheduleEntry
        from apps.planning import tests_mps

        entry = fixture(self, tests_mps.ScheduleTestCase).entry()
        self.once(race(WorkOrder, *[
            lambda: MasterScheduleEntry.objects.get(pk=entry.pk).commit(on_date=tests_mps.TODAY)] * 2))
        self.assertEqual(WorkOrder.objects.count(), 1)

    def test_two_fortnights_posted_at_once_do_not_both_pay_the_piece_work(self):
        """
        Each run's piece work is what was made less what posted runs paid. Calculated before
        either posted and posted together, each found the other not there, and September's
        2,000 m were paid 1,250.00 for 850.00. The weaver is held as either posts.
        """
        from apps.manufacturing import tests_piecework

        made = fixture(self, tests_piecework.PieceworkTestCase)
        made.woven("1000", tests_piecework.SEP(3))
        made.woven("1000", tests_piecework.SEP(21))
        runs = [made.pay(tests_piecework.SEP(1), tests_piecework.SEP(15), post=False),
                made.pay(tests_piecework.SEP(16), tests_piecework.SEP(30), post=False)]
        self.once(race(JournalEntry, *[lambda pk=run.pk: PayRun.objects.get(pk=pk).post() for run in runs]))
        self.assertLessEqual(sum((run.gross() for run in PayRun.objects.filter(status="posted")), Decimal("0")),
                             Decimal("850.00"))

    def test_a_day_is_not_taken_off_as_it_is_marked_worked(self):
        """Each finds the other not there yet, and the day is paid twice."""
        from apps.hr import tests_attendance
        from apps.hr.attendance import AttendanceDay
        from apps.hr.models import LeavePolicy

        made = fixture(self, tests_attendance.AttendanceTestCase)
        policy = LeavePolicy.objects.create(code="HOL-R", name="Holiday", leave_type="vacation",
                                            annual_days=Decimal("12"))
        request = LeaveRequest.objects.create(
            employee=made.worker, policy=policy, leave_type=LeaveType.VACATION,
            start_date=datetime.date(2026, 10, 7), end_date=datetime.date(2026, 10, 7))
        self.once(race(None, lambda: LeaveRequest.objects.get(pk=request.pk).approve(by=made.boss),
                       lambda: made.mark(7)))
        request.refresh_from_db()
        marked = AttendanceDay.objects.filter(employee=made.worker, on=datetime.date(2026, 10, 7)).exists()
        self.assertNotEqual(request.status == LeaveStatus.APPROVED, marked)

    def test_a_calibration_is_not_withdrawn_as_readings_come_to_rely_on_it(self):
        """
        The inspection reads the calibration as standing, the void finds
        nothing relying on it yet, and the readings are frozen on a
        calibration withdrawn under them. No save falls between the read
        and the freeze, so the inspection is held in the read itself while
        the void decides.
        """
        from unittest import mock

        from apps.quality import tests_calibration
        from apps.quality.calibration import Calibration, Instrument
        from apps.quality.models import Inspection, Reading

        made = fixture(self, tests_calibration.CalibrationTestCase)
        calibration = made.calibrate(datetime.date(2026, 1, 10))
        # One bag weighed: with more readings on the balance, the next one's
        # read finds the void, and the first one's freeze holds the row.
        made.gsm_plan.lines.update(sample_size=1)
        inspection = made.measured(datetime.date(2026, 3, 1), values=("87",))
        read, decided = threading.Event(), threading.Event()
        status, outcomes = Instrument.status, {}

        def held(instrument, on_date):
            found = status(instrument, on_date)
            read.set()
            decided.wait(5)
            return found

        def step(name, call, before=None, after=None):
            if before is not None:
                before.wait(5)
            try:
                with transaction.atomic():
                    call()
                outcomes[name] = "done"
            except Exception as exc:
                outcomes[name] = f"refused: {exc}"
            finally:
                if after is not None:
                    after.set()
                connection.close()

        with mock.patch.object(Instrument, "status", held):
            threads = [
                threading.Thread(target=step, args=("post", lambda: Inspection.objects.get(pk=inspection.pk).post())),
                threading.Thread(target=step, args=(
                    "void", lambda: Calibration.objects.get(pk=calibration.pk).void("Wrong certificate"),
                    read, decided)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
        self.once([outcomes.get("post"), outcomes.get("void")])
        calibration.refresh_from_db()
        self.assertFalse(calibration.voided_at is not None
                         and Reading.objects.filter(calibration=calibration).exists())

    def test_two_call_offs_at_once_do_not_call_off_more_than_the_line(self):
        """100 ordered and 70 called off: two more of 20 at once each found 30 left."""
        from apps.sales import tests_call_offs
        from apps.sales.call_offs import CallOff, called_off

        made = fixture(self, tests_call_offs.CallOffTestCase)
        self.once(race(CallOff, *[lambda: CallOff.objects.create(
            line_id=made.line.pk, due_on=tests_call_offs.day(40), quantity=Decimal("20"))] * 2))
        self.assertEqual(called_off(made.line), Decimal("90"))

    def test_a_line_is_not_cut_below_a_call_off_written_at_once(self):
        """Cut to 80 as 20 more is called off: either stands, not 90 called off a line of 80."""
        from apps.sales import tests_call_offs
        from apps.sales.call_offs import CallOff, called_off

        made = fixture(self, tests_call_offs.CallOffTestCase)
        line_pk = made.line.pk

        def cut():
            line = SalesOrderLine.objects.get(pk=line_pk)
            line.quantity = Decimal("80")
            line.save()

        self.once(race(None, cut, lambda: CallOff.objects.create(
            line_id=line_pk, due_on=tests_call_offs.day(40), quantity=Decimal("20"))))
        self.assertIn((SalesOrderLine.objects.get(pk=line_pk).quantity, called_off(made.line)),
                      [(Decimal("80"), Decimal("70")), (Decimal("100"), Decimal("90"))])

    def test_two_orders_confirmed_at_once_do_not_both_fit_under_the_credit_limit(self):
        from apps.sales import tests_approvals
        from apps.sales.models import CustomerProfile, SalesOrder, committed_balance

        made = fixture(self, tests_approvals.ApprovalTestCase)
        CustomerProfile.objects.create(party=made.customer, credit_limit=Decimal("1000"))
        first, second = made.draft("6", "100"), made.draft("6", "100")
        self.once(race(SalesOrder, lambda: SalesOrder.objects.get(pk=first.pk).confirm(),
                       lambda: SalesOrder.objects.get(pk=second.pk).confirm()))
        self.assertEqual(committed_balance(made.customer), Decimal("600.00"))

    def test_two_orders_confirmed_at_once_do_not_both_fit_in_the_budget(self):
        from apps.purchasing import tests_budgets
        from apps.purchasing.models import PurchaseApprovalPolicy, PurchaseOrder

        made = fixture(self, tests_budgets.BudgetTestCase)
        PurchaseApprovalPolicy.objects.create(code="STD", name="Standard")
        first, second = (made.order_of("300", "20", confirm=False) for _ in range(2))
        self.once(race(PurchaseOrder, lambda: PurchaseOrder.objects.get(pk=first.pk).confirm(),
                       lambda: PurchaseOrder.objects.get(pk=second.pk).confirm()))
        self.assertEqual(made.budget.available(), Decimal("4000.00"))

    def test_a_schedule_puts_one_job_on_the_board(self):
        from apps.manufacturing.maintenance import MaintenanceJob, MaintenanceSchedule
        from apps.manufacturing import tests_maintenance

        schedule = fixture(self, tests_maintenance.MaintenanceTestCase).schedule(days=90)
        self.once(race(MaintenanceJob, *[lambda: MaintenanceSchedule.objects.get(pk=schedule.pk).raise_job(
            as_of=run_fixture.TODAY)] * 2))
        self.assertEqual(MaintenanceJob.objects.count(), 1)

    def test_a_service_is_not_reopened_as_its_schedule_raises_the_next(self):
        """Both at once would leave the schedule with two jobs on the board."""
        from apps.manufacturing.maintenance import MaintenanceJob, MaintenanceSchedule
        from apps.manufacturing import tests_maintenance

        schedule = fixture(self, tests_maintenance.MaintenanceTestCase).schedule(days=90)
        job = schedule.raise_job(as_of=run_fixture.TODAY)
        job.complete(on_date=run_fixture.TODAY)
        self.once(race(MaintenanceJob,
                       lambda: MaintenanceJob.objects.get(pk=job.pk).reopen("Wrong loom"),
                       lambda: MaintenanceSchedule.objects.get(pk=schedule.pk).raise_job(
                           as_of=run_fixture.TODAY)))
        self.assertEqual(MaintenanceJob.objects.open().filter(schedule=schedule).count(), 1)

    def test_a_repair_closes_on_its_stoppage_as_it_stands(self):
        """
        The stoppage corrected as the repair is closed: either the repair
        takes the corrected minutes, or the correction finds it done and
        is refused. Not a repair that says 90 on a stoppage of 120.
        """
        from apps.manufacturing.maintenance import MaintenanceJob
        from apps.manufacturing.shifts import Downtime
        from apps.manufacturing import tests_breakdowns

        job = fixture(self, tests_breakdowns.BreakdownTestCase).broken("90")

        def correct():
            stoppage = Downtime.objects.get(pk=job.downtime_id)
            stoppage.minutes = Decimal("120")
            stoppage.save()

        race(None, lambda: MaintenanceJob.objects.select_related("downtime").get(pk=job.pk).complete(
            on_date=run_fixture.TODAY, action="Screen changed"), correct)
        job.refresh_from_db()
        self.assertIsNotNone(job.done_on)
        self.assertEqual(job.actual_minutes, Downtime.objects.get(pk=job.downtime_id).minutes)

    def test_two_runs_at_once_take_one_month_not_two(self):
        from apps.accounting import tests_recurring
        from apps.accounting.recurring import generate_due_journals

        schedule = fixture(self, tests_recurring.RecurringTestCase).schedule()
        outcomes = race(JournalEntry, *[lambda: generate_due_journals(as_of=datetime.date(2026, 1, 31))] * 2)
        self.assertEqual(outcomes, ["done", "done"])
        self.assertEqual(JournalEntry.objects.filter(recurring_journal=schedule).count(), 1)

    def test_two_runs_at_once_issue_one_months_invoice_not_two(self):
        from apps.sales import tests_lifecycle
        from apps.sales.models import generate_due_invoices

        fixture(self, tests_lifecycle.RecurringInvoiceTests).make_schedule()
        outcomes = race(Invoice, *[lambda: generate_due_invoices(as_of=datetime.date(2026, 1, 15))] * 2)
        self.assertEqual(outcomes, ["done", "done"])
        self.assertEqual(Invoice.objects.filter(reference="SUB-1").count(), 1)

    def test_a_station_counts_each_guess_before_weighing_the_next(self):
        """
        Four wrong PINs, then two more at once. Weighed together, both
        read four and both are tried: a sixth guess the lock should have
        stopped, and with more at once, as many as are sent.
        """
        from django.core.exceptions import ValidationError
        from django.utils import timezone

        from apps.manufacturing.station import LoomStation, StationAttempt
        from apps.manufacturing import tests_station

        made = fixture(self, tests_station.StationTestCase)
        pin = made.operator.issue_pin()
        wrong, now = ("000000" if pin != "000000" else "111111"), timezone.now()
        for _ in range(4):
            with self.assertRaises(ValidationError):
                made.station.identify(wrong, now=now)
        outcomes = race(StationAttempt, *[
            lambda: LoomStation.objects.get(pk=made.station.pk).identify(wrong, now=now)] * 2, atomic=False)
        self.assertEqual(StationAttempt.objects.filter(station=made.station).count(), 5, outcomes)
        self.assertTrue(any("locked" in outcome for outcome in outcomes), outcomes)


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class EditRaceTests(RaceCase):
    """
    Two people editing one record through the API at once, each a field of
    their own. DRF reads the row, checks the request against it and saves
    every field it holds, so whichever saved second wrote back the field
    the other had just changed, as it had read it, without a word.
    """

    def setUp(self):
        from django.core.management import call_command

        call_command("setup_roles", verbosity=0)

    def person(self, role):
        from django.contrib.auth.models import Group, User

        user = User.objects.create_user(role.lower().replace(" ", "-"))
        user.groups.add(Group.objects.get(name=role))
        return user

    def patch(self, user, url, data):
        """A PATCH from a client of its own, as each person's browser is; anything but 200 raises."""
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user)
        response = client.patch(url, data, format="json")
        if response.status_code != 200:
            raise AssertionError(f"{response.status_code}: {response.content.decode()}")

    def test_two_people_editing_one_party_keep_both_changes(self):
        from apps.core.models import Party

        party = Party.objects.create(code="C-ACME", name="Acme Sacks", email="orders@acme.example")
        clerk, url = self.person("AR Manager"), f"/api/core/parties/{party.pk}/"
        outcomes = race(Party, lambda: self.patch(clerk, url, {"name": "Acme Sacks Ltd"}),
                        lambda: self.patch(clerk, url, {"email": "accounts@acme.example"}), atomic=False)
        self.assertEqual(outcomes, ["done", "done"])
        party.refresh_from_db()
        self.assertEqual((party.name, party.email), ("Acme Sacks Ltd", "accounts@acme.example"))

    def test_a_stoppage_corrected_as_its_repair_completes_is_not_a_deadlock(self):
        """
        Completing the repair holds the job, then the stoppage; the stoppage's
        save holds the job too. The correction holding the stoppage from its
        read, each waited for the other until PostgreSQL ended one of them.
        Each is held here just before its second lock, so both have their
        first. Taking the job first, as save() does, one waits for the other:
        the repair closes on the corrected minutes, or the correction finds
        it done.
        """
        from unittest import mock

        from apps.manufacturing import maintenance, shifts, tests_breakdowns
        from apps.manufacturing.maintenance import MaintenanceJob
        from apps.manufacturing.shifts import Downtime

        job = fixture(self, tests_breakdowns.BreakdownTestCase).broken("90")
        supervisor, url = self.person("Production Supervisor"), f"/api/manufacturing/downtime/{job.downtime_id}/"
        barrier, local = threading.Barrier(2, timeout=3), threading.local()

        def held(lock):
            def second(*rows, **kwargs):
                if not getattr(local, "held", False):
                    local.held = True
                    try:
                        barrier.wait()
                    except threading.BrokenBarrierError:
                        pass  # The other waits on a lock this one has.
                return lock(*rows, **kwargs)
            return second

        with mock.patch.object(shifts, "lock_rows", held(shifts.lock_rows)), \
                mock.patch.object(maintenance, "lock_rows", held(maintenance.lock_rows)):
            outcomes = race(None, lambda: self.patch(supervisor, url, {"minutes": "120"}),
                            lambda: MaintenanceJob.objects.select_related("downtime").get(pk=job.pk).complete(
                                on_date=run_fixture.TODAY, action="Screen changed"), atomic=False)
        self.assertFalse([outcome for outcome in outcomes if "deadlock" in outcome], outcomes)
        job.refresh_from_db()
        self.assertIsNotNone(job.done_on)
        self.assertEqual(job.actual_minutes, Downtime.objects.get(pk=job.downtime_id).minutes)


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class FloorRaceTests(RaceCase):
    """
    Found in review of the floor's fixes: each read what it decided on
    before holding what another step changes it through.
    """

    @staticmethod
    def each_waits_after(read):
        """
        `read`, after which each of two threads waits for the other once:
        both have read before either writes, which no model's save marks
        where the reads come before a lock.
        """
        barrier, local = threading.Barrier(2, timeout=3), threading.local()

        def held(*args, **kwargs):
            answer = read(*args, **kwargs)
            if not getattr(local, "held", False):
                local.held = True
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    pass  # The other waits on a lock this one has.
            return answer
        return held

    def test_a_count_is_not_voided_as_its_bundle_is_baled(self):
        """
        A bundle pressed into a bale as its count is voided. Packing held
        the shelf and read what was baled; the void read it before holding
        the shelf, found nothing, then waited for the packing and took the
        500 bags out from under the sealed bale. Reading after the hold,
        as packing does, one of the two finds the other.
        """
        from unittest import mock

        from apps.manufacturing import bales, tests_bales
        from apps.manufacturing.bales import Bale, pack
        from apps.manufacturing.conversion import BagCount, void_bags

        made = fixture(self, tests_bales.BaleTestCase)
        count = BagCount.objects.get(inspection__lot=made.b1)
        with mock.patch.object(bales, "baled", self.each_waits_after(bales.baled)):
            outcomes = race(
                (),
                lambda: pack(made.plant, made.operator, [(made.b1, 500)], on_date=run_fixture.TODAY),
                lambda: void_bags(BagCount.objects.get(pk=count.pk), made.supervisor, "Miscounted"))
        self.once(outcomes)
        sealed = Bale.objects.filter(lines__lot=made.b1, broken_at__isnull=True).exists()
        self.assertEqual(made.b1.on_hand_at(made.plant), Decimal("500") if sealed else Decimal("0"))

    def test_two_looms_do_not_both_load_the_whole_doff(self):
        """
        One 100 kg doff loaded on two looms of two backflushed runs at once.
        Each held only its own loom and run, read the whole doff as free,
        and the creels held 200 kg of it. Holding the doff's place on the
        shelf first, the second finds the first's load.
        """
        from apps.manufacturing.orders import WorkOrderOperation
        from apps.manufacturing.tape_loads import CreelSide, TapeLoad, load_tape
        from apps.manufacturing.tests_station import at
        from apps.manufacturing import tests_tape_loads as loads

        made = fixture(self, loads.TapeLoadTestCase)
        other = made.released_run()
        WorkOrder.objects.filter(pk__in=[made.run.pk, other.pk]).update(backflush=True)
        WorkOrderOperation.objects.filter(work_order=made.run).update(machine=made.l17)
        WorkOrderOperation.objects.filter(work_order=other).update(machine=made.l20)
        when = at(run_fixture.TODAY, 9)
        outcomes = race(TapeLoad, *[
            lambda machine=machine: load_tape(made.station, made.operator, machine, "D-1", "100",
                                              CreelSide.WARP, at=when)
            for machine in (made.l17, made.l20)])
        self.once(outcomes)
        self.assertEqual(TapeLoad.objects.filter(lot=made.doffs["D-1"]).count(), 1)

    def test_a_load_is_not_withdrawn_as_the_output_draws_on_it(self):
        """
        A load withdrawn as output on its backflushed run is booked. The
        withdrawal read nothing drawn before holding the run, the output
        read the load still on the creel, and both went through: the load
        withdrawn, its tape drawn. Holding the run first, one of them
        finds the other.
        """
        from apps.inventory.models import Lot
        from apps.manufacturing.tape_loads import CreelSide, TapeLoad, drawn_by_output, load_tape, void_load
        from apps.manufacturing.tests_station import at
        from apps.manufacturing import tests_tape_loads as loads

        made = fixture(self, loads.TapeLoadTestCase)
        WorkOrder.objects.filter(pk=made.run.pk).update(backflush=True)
        made.run.refresh_from_db()
        load = load_tape(made.station, made.operator, made.l17, "D-1", "100", CreelSide.WARP,
                         at=at(run_fixture.TODAY, 9))
        entry = made.produce(made.run, "49", lot=Lot.objects.create(item=made.fabric, code="F-RACE"))
        outcomes = race(
            (TapeLoad, MaterialIssue),
            lambda: ProductionEntry.objects.get(pk=entry.pk).post(),
            lambda: void_load(TapeLoad.objects.get(pk=load.pk), made.station, made.supervisor,
                              made.operator, "Wrong doff"))
        self.once(outcomes)
        load.refresh_from_db()
        self.assertEqual(load.voided_at is None, drawn_by_output(made.run, made.doffs["D-1"]) > 0)

    def test_two_challans_do_not_both_send_past_the_run(self):
        """
        Two challans of 600 issued at once for a run that holds 1,100. Each
        read the other unissued without holding the run, and 1,200 went
        out. Holding the run first, the second finds the first.
        """
        from apps.manufacturing import tests_jobwork
        from apps.manufacturing.jobwork import JobWorkChallan

        made = fixture(self, tests_jobwork.JobWorkTestCase)
        drafts = [made.challan("600", post=False) for _ in range(2)]
        outcomes = race(JobWorkChallan, *[
            lambda pk=draft.pk: JobWorkChallan.objects.get(pk=pk).post() for draft in drafts])
        self.once(outcomes)
        self.assertEqual(JobWorkChallan.objects.filter(posted=True).count(), 1)

    def test_a_challan_is_not_withdrawn_as_the_vendors_work_comes_back(self):
        """
        600 and 400 out; the 400 withdrawn as 700 comes back. The
        withdrawal read nothing back without holding the run while the
        receipt counted the 400 as sent: 700 back against 600 out. Holding
        the run first, one of the two finds the other.
        """
        from apps.manufacturing import tests_jobwork
        from apps.manufacturing.jobwork import JobWorkChallan

        made = fixture(self, tests_jobwork.JobWorkTestCase)
        made.challan("600")
        second = made.challan("400")
        receipt = OutsideMovement.objects.create(
            operation=made.coat, movement_date=run_fixture.TODAY,
            quantity=Decimal("700"), value=Decimal("1400"), credit_account=made.grni,
        )
        outcomes = race(
            (JobWorkChallan, OutsideMovement),
            lambda: JobWorkChallan.objects.get(pk=second.pk).void(),
            lambda: OutsideMovement.objects.get(pk=receipt.pk).post())
        self.once(outcomes)
        withdrawn = JobWorkChallan.objects.get(pk=second.pk).voided_at is not None
        self.assertEqual(withdrawn, not OutsideMovement.objects.get(pk=receipt.pk).posted)

    def test_a_clock_does_not_start_on_a_run_as_it_closes(self):
        """
        C-1's clock started as its run closed. The close held the run and
        found no clock running; the start read the run as released without
        holding it, and the run closed under a running clock that could
        then never be stopped. Holding the run first, one finds the other.
        """
        from apps.manufacturing import tests_bag_counts
        from apps.manufacturing.station_clock import MachineClock, start_clock
        from apps.manufacturing.tests_station import at

        made = fixture(self, tests_bag_counts.ConversionTestCase)
        outcomes = race(
            (MachineClock, WorkOrder),
            lambda: start_clock(made.cv, made.operator, made.c1, at=at(run_fixture.TODAY, 16)),
            lambda: WorkOrder.objects.get(pk=made.bag_run.pk).close(run_fixture.TODAY))
        self.once(outcomes)
        closed = WorkOrder.objects.get(pk=made.bag_run.pk).status == WorkOrderStatus.CLOSED
        running = MachineClock.objects.filter(operation__work_order=made.bag_run,
                                              stopped_at__isnull=True).exists()
        self.assertNotEqual(closed, running)


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class ComplaintRaceTests(RaceCase):
    """A complaint decided while something is added to it or paid on it."""

    def test_no_action_lands_on_a_complaint_as_it_closes(self):
        """
        Audit, 9 October: close() held the complaint and counted its actions;
        an action added at the same moment read the complaint without holding
        it, found it open, and the complaint closed with an action not done.
        """
        from apps.manufacturing.complaints import ActionKind, Complaint, ComplaintStatus, CorrectiveAction
        from apps.manufacturing.tests_complaints import ComplaintTestCase

        made = fixture(self, ComplaintTestCase)
        complaint = made.ready()
        outcomes = race(
            (Complaint, CorrectiveAction),
            lambda: Complaint.objects.get(pk=complaint.pk).close(
                "Metal in regrind", by=made.qa, on_date=run_fixture.TODAY + datetime.timedelta(days=1)),
            lambda: CorrectiveAction.objects.create(
                complaint_id=complaint.pk, kind=ActionKind.PREVENTIVE,
                description="Audit the regrind supplier", owner=made.plant_head,
                due_on=run_fixture.TODAY + datetime.timedelta(days=30)),
        )
        self.once(outcomes)
        complaint.refresh_from_db()
        undone = CorrectiveAction.objects.filter(complaint=complaint, done_on__isnull=True).count()
        self.assertFalse(complaint.status == ComplaintStatus.CLOSED and undone, outcomes)

    def test_a_complaint_is_not_paid_for_and_rejected_at_once(self):
        """settle() holds the complaint as reject() does, so one sees the other."""
        from apps.gst import tests_claims
        from apps.manufacturing.complaints import Complaint, ComplaintStatus
        from apps.manufacturing.tests_complaints import person

        made = fixture(self, tests_claims.ClaimTests)
        invoice = made.sell(made.buyer, "10000")
        head = person("EMP-0601", "Quality head")
        complaint = Complaint.objects.create(customer=made.buyer, received_on=tests_claims.DAY,
                                             category="seam", description="Seams opened in the silo")
        outcomes = race(
            (Invoice, Complaint),
            lambda: Complaint.objects.get(pk=complaint.pk).settle(invoice, Decimal("500"), "quality"),
            lambda: Complaint.objects.get(pk=complaint.pk).reject("Not our sacks after all", by=head),
        )
        self.once(outcomes)
        complaint.refresh_from_db()
        self.assertFalse(complaint.status == ComplaintStatus.REJECTED and complaint.settlements.exists(),
                         outcomes)
