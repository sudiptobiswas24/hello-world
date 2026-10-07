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
from apps.manufacturing.orders import MaterialIssue, WorkOrder, WorkOrderStatus
from apps.manufacturing import tests_orders as run_fixture
from apps.purchasing.models import Bill, BillPayment
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
)
from apps.sales import tests_base as sales_fixture


def race(sender, *calls, atomic=True):
    """
    Run `calls` at once, each held at `sender`'s pre_save until all arrive.

    Each in a transaction of its own, as a request's write is; `atomic=False`
    for a step whose endpoint runs it outside one, so what it commits before
    refusing stays committed.
    """
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
    pre_save.disconnect(sender=sender, dispatch_uid="race")
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

    def test_a_schedule_puts_one_job_on_the_board(self):
        from apps.manufacturing.maintenance import MaintenanceJob, MaintenanceSchedule
        from apps.manufacturing import tests_maintenance

        schedule = fixture(self, tests_maintenance.MaintenanceTestCase).schedule(days=90)
        self.once(race(MaintenanceJob, *[lambda: MaintenanceSchedule.objects.get(pk=schedule.pk).raise_job(
            as_of=run_fixture.TODAY)] * 2))
        self.assertEqual(MaintenanceJob.objects.count(), 1)

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
