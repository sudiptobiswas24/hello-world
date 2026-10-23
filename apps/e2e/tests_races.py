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
from apps.core.tests_second_factor import staff_session


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

    def test_an_irn_is_not_cancelled_as_a_payment_settles_its_invoice(self):
        """
        O220: the cancellation decides on what settles the invoice, and an allocation adds to it. Both
        hold the invoice: one wins, and the other is refused. Unheld, the invoice was paid and reversed
        by a full credit note at once, which no order of the two gives.
        """
        from django.utils import timezone

        from apps.gst.einvoice import EInvoice

        invoice = self.bill(self.make_order("10", "100"))
        payment = self.receipt("1000")
        record = EInvoice.objects.create(
            invoice=invoice, payload={}, irn="b" * 64, ack_number="1", signed_qr="x",
            ack_date=timezone.now() - datetime.timedelta(minutes=5),
            cancel_reason="2", cancel_remarks="Wrong rate", cancel_payload={"Irn": "b" * 64, "CnlRsn": "2"})
        outcomes = race((InvoicePayment, Invoice),
                        lambda: InvoicePayment.objects.create(invoice_id=invoice.pk, payment_id=payment.pk,
                                                              amount=Decimal("1000")),
                        lambda: EInvoice.objects.get(pk=record.pk).record_cancellation(timezone.now()))
        self.once(outcomes)
        paid = InvoicePayment.objects.filter(invoice=invoice).exists()
        cancelled = EInvoice.objects.get(pk=record.pk).cancelled_at is not None
        self.assertNotEqual(paid, cancelled, outcomes)

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

    def test_a_return_and_a_credit_note_on_one_invoice_do_not_deadlock(self):
        """
        O137: 10 shipped and invoiced; 3 taken back while 2 are credited. The
        return held the order and reached for the invoice to credit it; the
        credit note held the invoice and reached for the order: "deadlock
        detected". Both take the order first now, and both stand.
        """
        from apps.inventory.models import StockMovement
        from apps.sales.models import Delivery, DeliveryLine, InvoiceLine

        order = self.make_order("10", "100")
        delivery = self.ship(order, "10")
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 1))
        invoice.post()
        delivery_line, invoice_line = delivery.lines.get().pk, invoice.lines.get().pk

        def give_back():
            Delivery.objects.get(pk=delivery.pk).create_return(
                quantities={DeliveryLine.objects.get(pk=delivery_line): Decimal("3")})

        def credit():
            Invoice.objects.get(pk=invoice.pk).create_credit_note(
                quantities={InvoiceLine.objects.get(pk=invoice_line): Decimal("2")})

        outcomes = race((StockMovement, Invoice), give_back, credit)
        self.assertEqual(outcomes, ["done", "done"], outcomes)
        self.assertEqual((order.lines.get().quantity_invoiced(), self.balance(self.ar)),
                         (Decimal("5"), Decimal("500.00")))

    def test_one_shipment_is_invoiced_once_at_once(self):
        """
        O67: bill on delivery, 5 of 10 shipped, two drafts of 5 posting at once.
        Each holds the order its lines bill and asks what shipped and is not
        yet invoiced: one posts, AR 500.00.
        """
        from apps.sales.models import InvoicePolicy

        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        self.ship(order, "5")
        drafts = [order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 1)).pk for _ in range(2)]
        self.once(race(JournalEntry, *[lambda pk=pk: Invoice.objects.get(pk=pk).post() for pk in drafts]))
        self.assertEqual(self.balance(self.ar), Decimal("500.00"))

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
            code="194C", name="Contractors",
            mode="whole", single_threshold=Decimal("30000"), annual_threshold=Decimal("100000"),
            payable_account=payable)
        tds_fixture.confirmed_rate(section, "2", "20")
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


    def test_a_return_and_a_debit_note_on_one_bill_do_not_deadlock(self):
        """
        O137, the mirror: 10 received and billed @ 5; 3 sent back while 2 are
        debited. Both stand: payable -25.00, and GRNI -10.00 for the 2 still
        here and no longer billed.
        """
        from apps.inventory.models import StockMovement
        from apps.purchasing.models import BillLine, GoodsReceipt, GoodsReceiptLine

        order = self.make_order("10", "5")
        receipt = self.receive(order, "10")
        bill = order.create_bill(self.payable, bill_date=datetime.date(2026, 1, 10))
        bill.post()
        receipt_line, bill_line = receipt.lines.get().pk, bill.lines.get().pk

        def send_back():
            GoodsReceipt.objects.get(pk=receipt.pk).create_return(
                quantities={GoodsReceiptLine.objects.get(pk=receipt_line): Decimal("3")})

        def debit():
            Bill.objects.get(pk=bill.pk).create_debit_note(
                quantities={BillLine.objects.get(pk=bill_line): Decimal("2")})

        outcomes = race((StockMovement, Bill), send_back, debit)
        self.assertEqual(outcomes, ["done", "done"], outcomes)
        self.assertEqual((self.balance(self.payable), self.balance(self.grni)), (Decimal("-25.00"), Decimal("-10.00")))

    def typed_bill(self, order_line, quantity, order=None):
        from apps.purchasing.models import BillLine

        bill = Bill.objects.create(vendor=self.vendor, bill_date=datetime.date(2026, 1, 10), purchase_order=order,
                                   payable_account=self.payable)
        BillLine.objects.create(bill=bill, order_line=order_line, item=self.item, quantity=Decimal(quantity),
                                unit_price=Decimal("5"), expense_account=self.expense)
        return bill

    def test_two_typed_bills_naming_no_order_do_not_both_bill_the_last_of_a_line(self):
        """
        O107: 10 @ 5 received; two typed bills naming no order, each for the 10.
        post() held only the bill's own order, which is none: payable -100 for 50
        received. It holds every order its lines bill now.
        """
        order = self.make_order("10", "5")
        self.receive(order, "10")
        drafts = [self.typed_bill(order.lines.get(), "10").pk for _ in range(2)]
        self.once(race(JournalEntry, *[lambda pk=pk: Bill.objects.get(pk=pk).post() for pk in drafts]))
        self.assertEqual((self.balance(self.payable), self.balance(self.grni)), (Decimal("-50.00"), Decimal("0")))

    def test_a_line_added_to_a_bill_as_it_posts_is_on_what_posted(self):
        """
        O106: 20 received @ 5; a draft bill of 10 posts while a second line of 10 is
        added. The line read "not posted" without the bill's lock: the bill showed
        100.00 and payables held 50.00. Either order is right, so long as what the
        bill shows is what it posted.
        """
        from apps.purchasing.models import BillLine

        order = self.make_order("20", "5")
        self.receive(order, "20")
        draft = self.typed_bill(order.lines.get(), "10", order=order)

        def add_line():
            BillLine.objects.create(bill=Bill.objects.get(pk=draft.pk), order_line=order.lines.get(), item=self.item,
                                    quantity=Decimal("10"), unit_price=Decimal("5"), expense_account=self.expense)

        outcomes = race((JournalEntry, BillLine), lambda: Bill.objects.get(pk=draft.pk).post(), add_line)
        bill = Bill.objects.get(pk=draft.pk)
        self.assertTrue(bill.posted, outcomes)
        self.assertEqual((bill.total(), -self.balance(self.payable)),
                         (Decimal("50.00") * bill.lines.count(), bill.total()), outcomes)

    def test_a_line_added_to_a_receipt_as_it_posts_is_what_moved(self):
        """
        O106, the receipt: 20 ordered; a draft receipt of 10 posts while a second
        line of 10 is added. The order line read 20 received while 10 moved.
        """
        from apps.inventory.models import StockMovement
        from apps.purchasing.models import GoodsReceipt, GoodsReceiptLine

        order = self.make_order("20", "5")
        draft = GoodsReceipt.objects.create(purchase_order=order, receipt_date=datetime.date(2026, 1, 5))
        GoodsReceiptLine.objects.create(receipt=draft, order_line=order.lines.get(), warehouse=self.warehouse,
                                        quantity_received=Decimal("10"))

        def add_line():
            GoodsReceiptLine.objects.create(receipt=GoodsReceipt.objects.get(pk=draft.pk),
                                            order_line=order.lines.get(), warehouse=self.warehouse,
                                            quantity_received=Decimal("10"))

        outcomes = race((StockMovement, GoodsReceiptLine), lambda: GoodsReceipt.objects.get(pk=draft.pk).post(),
                        add_line)
        receipt = GoodsReceipt.objects.get(pk=draft.pk)
        moved = self.item.on_hand_at(self.warehouse)
        self.assertTrue(receipt.posted, outcomes)
        self.assertEqual((order.lines.get().quantity_received(), moved),
                         (Decimal("10") * receipt.lines.count(), Decimal("10") * receipt.lines.count()), outcomes)

    def test_an_award_and_an_order_do_not_both_order_one_request(self):
        """
        O108: 10 asked for on one approved request, out for quotes and quoted
        at 4. The RFQ is awarded while the request is ordered direct from a
        second vendor. Each held its own document, the RFQ and the request,
        and read what was on order unlocked: both went through, 20 ordered
        for 10. Both now hold the request line: one order, 10.
        """
        from django.contrib.auth import get_user_model

        from apps.core.models import Party, PartyRole, PartyRoleAssignment
        from apps.purchasing.models import PurchaseRequisition, PurchaseRequisitionLine, RequestForQuotation

        employee = Party.objects.create(code="E-1", name="Dana")
        PartyRoleAssignment.objects.create(party=employee, role=PartyRole.EMPLOYEE)
        manager = get_user_model().objects.create_user(username="budget", password="x")
        requisition = PurchaseRequisition.objects.create(
            requested_by=employee, request_date=datetime.date(2026, 1, 1), needed_by=datetime.date(2026, 2, 1))
        line = PurchaseRequisitionLine.objects.create(requisition=requisition, item=self.item, uom=self.uom,
                                                      quantity=Decimal("10"), estimated_price=Decimal("5"),
                                                      suggested_vendor=self.vendor)
        requisition.submit()
        requisition.approve(by=manager)
        rfq = requisition.create_rfq()
        rfq.issue()
        invitation = rfq.invited.get()
        invitation.quote(rfq.lines.get(), Decimal("4"))
        second = Party.objects.create(code="V-2", name="Another supplier", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=second, role=PartyRole.VENDOR)
        outcomes = race(PurchaseOrder,
                        lambda: PurchaseRequisition.objects.get(pk=requisition.pk).create_order(
                            second, lines=[PurchaseRequisitionLine.objects.get(pk=line.pk)]),
                        lambda: RequestForQuotation.objects.get(pk=rfq.pk).award(invitation))
        self.once(outcomes)
        self.assertEqual(line.quantity_ordered(), Decimal("10"), outcomes)


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

    def test_two_fortnights_posted_at_once_take_the_months_pf_once(self):
        """
        O301: both fortnights of June at 8,000 calculated (each 960 alone) and posted at once. Each post works
        its slips out again with the employee held, so whichever posts second finds the month's PF changed under
        it and is refused (posted_as_calculated); worked out again it takes 840: 960 + 840 = 1,800, never 1,920.
        """
        import datetime

        person = self.person("F", basic="8000")
        runs = [self.run_for(datetime.date(2026, 6, 1), datetime.date(2026, 6, 15), post=False),
                self.run_for(datetime.date(2026, 6, 16), datetime.date(2026, 6, 30), post=False)]
        outcomes = race(JournalEntry, *[lambda run=run: PayRun.objects.get(pk=run.pk).post() for run in runs])
        self.assertEqual(sorted(outcome.split(":")[0] for outcome in outcomes), ["done", "refused"], outcomes)
        self.assertIn("pay has changed since this run was calculated", "".join(outcomes))
        late = PayRun.objects.filter(pk__in=[run.pk for run in runs]).exclude(status="posted").get()
        late.calculate()
        late.post()
        taken = sum((line.amount for run in runs for line in PayRun.objects.get(pk=run.pk).payslips.get(
            employee=person).lines.all() if line.component.code == "PF"), Decimal("0"))
        self.assertEqual(taken, Decimal("1800.00"), outcomes)

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

    def test_a_count_is_not_posted_over_a_movement_that_landed_after_its_check(self):
        # O252: 100 on the shelf, a sheet counting 96, and an adjustment of -2 posted at once. The count asked
        # whether the books still read 100 before it held the shelf: the -2 landed between, and the count
        # posted its -4 over it. Held first, either the count goes first or it is refused as stale.
        from apps.inventory.models import StockCount, StockMovement

        stock_fixture.AdjustmentTestCase.setUp(self)
        stock_fixture.AdjustmentTestCase.stock(self)
        count = StockCount.objects.create(count_date=datetime.date(2026, 3, 1), warehouse=self.warehouse,
                                          reason=self.recount)
        count.add(self.item, "96")
        other = stock_fixture.AdjustmentTestCase.adjust(self, "-2", post=False)
        outcomes = race(StockAdjustment, lambda: StockCount.objects.get(pk=count.pk).post(),
                        lambda: StockAdjustment.objects.get(pk=other.pk).post())
        other.refresh_from_db()
        counted = StockAdjustment.objects.filter(count=count).first()
        if outcomes[0] == "done":
            first = StockMovement.objects.filter(reference=counted.number).order_by("pk").first()
            second = StockMovement.objects.filter(reference=other.number).order_by("pk").first()
            self.assertTrue(second is None or first.pk < second.pk, (outcomes, first.pk, second and second.pk))
        else:
            self.assertIn("Stock moved after this sheet was written", outcomes[0])
        self.assertEqual(outcomes[1], "done", outcomes)

    def test_two_dispatch_commits_of_the_same_runs_in_opposite_orders_both_go_through(self):
        # O226 (dispatch commit's operations) and O254: each commit wrote its operations in the order it
        # dispatched them, and each operation's save now holds its run: two commits of runs A and B, one
        # A then B and one B then A, each held one and waited for the other. Both held in key order first.
        from apps.manufacturing import tests_dispatch as dispatch_fixture
        from apps.manufacturing.dispatch import commit
        from apps.manufacturing.orders import WorkOrderOperation

        # Its setUp asks super(): built on an instance of its own, committed here for both threads to read.
        plant = dispatch_fixture.DispatchTestCase(methodName="setUp")
        plant.setUp()
        a = plant.run_of("600", dispatch_fixture.D2).operations.get()
        b = plant.run_of("600", dispatch_fixture.D3).operations.get()
        from django.utils import timezone

        start = timezone.now()

        def rows(*operations):
            return [{"held": None, "operation": WorkOrderOperation.objects.get(pk=operation.pk), "start": start,
                     "finish": start, "machine": None} for operation in operations]

        outcomes = race(WorkOrderOperation, lambda: commit(rows(a, b)), lambda: commit(rows(b, a)))
        self.assertEqual(outcomes, ["done", "done"])

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

    def test_a_line_on_a_draft_order_is_not_cut_below_a_call_off_written_at_once(self):
        """
        O36: the same on an order still a draft. A line on a confirmed order is
        held by every save (_policy_before holds the order, then the line), so
        the price never mattered; a draft's line read its call-offs without the
        line's lock: cut to 80 with 90 called off. Every cut now holds it.
        """
        from apps.sales import tests_call_offs
        from apps.sales.call_offs import CallOff, called_off

        made = fixture(self, tests_call_offs.CallOffTestCase)
        draft = SalesOrder.objects.create(customer=made.customer, order_date=tests_call_offs.DAY, currency=made.usd)
        line = SalesOrderLine.objects.create(
            order=draft, item=made.item, uom=made.uom, quantity=Decimal("100"), unit_price=Decimal("10"),
            revenue_account=made.revenue, warehouse=made.warehouse, delivery_date=tests_call_offs.day(90))
        for n, quantity in ((10, "25"), (20, "25"), (30, "20")):
            CallOff.objects.create(line=line, due_on=tests_call_offs.day(n), quantity=Decimal(quantity))

        def cut():
            row = SalesOrderLine.objects.get(pk=line.pk)
            row.quantity = Decimal("80")
            row.save()

        self.once(race(None, cut, lambda: CallOff.objects.create(
            line_id=line.pk, due_on=tests_call_offs.day(40), quantity=Decimal("20"))))
        self.assertIn((SalesOrderLine.objects.get(pk=line.pk).quantity, called_off(line)),
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
class QualityDecisionRaceTests(RaceCase):
    """RISKS.md, decision 22: the first decision on a failure that waits stands."""

    def test_a_draft_posted_as_it_is_voided_is_not_a_deadlock(self):
        """
        O242: the post held the draft, then its batch; the void held the batch, then the
        inspection. Each is held here just after its first lock, so both have it: posting
        held the batch second, and PostgreSQL ended one of the two. Both take the batch
        first now: one waits for the other, the post goes through and the void finds a
        posted verdict or a draft it may not void.
        """
        from unittest import mock

        from apps.core import models as core_models
        from apps.quality import tests_decisions as decisions_fixture
        from apps.quality.models import Inspection

        made = fixture(self, decisions_fixture.AwaitingTestCase)
        draft = made.failed()
        barrier, local = threading.Barrier(2, timeout=3), threading.local()
        lock = core_models.lock_rows

        def first_then_wait(*rows, **kwargs):
            held = lock(*rows, **kwargs)
            if not getattr(local, "held", False):
                local.held = True
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    pass  # The other waits on a lock this one has.
            return held

        with mock.patch.object(core_models, "lock_rows", first_then_wait):
            outcomes = race(None, lambda: Inspection.objects.get(pk=draft.pk).post(),
                            lambda: Inspection.objects.get(pk=draft.pk).void("Wrong batch"))
        self.assertFalse([outcome for outcome in outcomes if "deadlock" in outcome], outcomes)
        self.assertEqual(outcomes[0], "done", outcomes)
        self.assertTrue(Inspection.objects.get(pk=draft.pk).posted)

    def test_two_quality_managers_deciding_at_once_one_wins(self):
        from django.contrib.auth.models import Group, User

        from apps.quality import tests_decisions as decisions_fixture
        from apps.quality.models import Disposition, Inspection

        made = fixture(self, decisions_fixture.AwaitingTestCase)
        inspection = made.awaiting()
        second = User.objects.create_user("kiran")
        second.groups.add(Group.objects.get(name="Quality Manager"))
        first = made.users["asha"]
        outcomes = race(Inspection,
                        lambda: Inspection.objects.get(pk=inspection.pk).decide(
                            first, Disposition.REJECT, "Too heavy for the order."),
                        lambda: Inspection.objects.get(pk=inspection.pk).decide(
                            User.objects.get(pk=second.pk), Disposition.REWORK, "Re-wind it."))
        self.once(outcomes)
        self.assertIn("was decided already", next(o for o in outcomes if o != "done"))
        inspection.refresh_from_db()
        self.assertIn(inspection.disposition, (Disposition.REJECT, Disposition.REWORK))


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

    def test_no_batch_is_named_on_a_complaint_as_it_is_rejected(self):
        """
        O150: reject() held the complaint; a batch added at the same moment
        read it unheld, found it open, and the rejected complaint named it.
        Sequentially the batch is refused ("reopen it to change it"). The
        add starts once the reject has the complaint: added first, a batch
        on a rejected complaint is what anyone would have seen.
        """
        from django.db.models.signals import pre_save

        from apps.manufacturing.complaints import Complaint, ComplaintLot, ComplaintStatus
        from apps.manufacturing.tests_complaints import ComplaintTestCase

        made = fixture(self, ComplaintTestCase)
        complaint = made.complaint()
        deciding = threading.Event()
        pre_save.connect(lambda **kwargs: deciding.set(), sender=Complaint, weak=False,
                         dispatch_uid="race-deciding")
        self.addCleanup(pre_save.disconnect, sender=Complaint, dispatch_uid="race-deciding")
        outcomes = race(
            (Complaint, ComplaintLot),
            lambda: Complaint.objects.get(pk=complaint.pk).reject("Not our sacks", by=made.qa),
            lambda: deciding.wait(5) and Complaint.objects.get(pk=complaint.pk).add_lot(made.tape_lot, "600"),
        )
        self.once(outcomes)
        complaint.refresh_from_db()
        named = complaint.complained_lots.count()
        self.assertFalse(complaint.status == ComplaintStatus.REJECTED and named, outcomes)

    def test_the_only_batch_is_not_taken_off_a_complaint_as_it_closes(self):
        """O150: close() counts the batches under the complaint's lock; the removal now waits for it."""
        from django.db.models.signals import pre_delete

        from apps.manufacturing.complaints import Complaint, ComplaintLot, ComplaintStatus
        from apps.manufacturing.tests_complaints import ComplaintTestCase

        made = fixture(self, ComplaintTestCase)
        complaint = made.ready()
        row = complaint.complained_lots.get()
        # The removal writes no row, so it is held where it deletes and the
        # close where it saves, at one barrier: race() holds saves only.
        from django.db.models.signals import pre_save

        barrier, local = threading.Barrier(2, timeout=3), threading.local()

        closing = threading.Event()

        def pause(**kwargs):
            closing.set()  # the close holds the complaint by now; the removal starts
            if getattr(local, "armed", False):
                local.armed = False
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    pass  # The other is waiting on our lock: carry on and commit.

        pre_save.connect(pause, sender=Complaint, weak=False, dispatch_uid="race-close")
        pre_delete.connect(pause, sender=ComplaintLot, weak=False, dispatch_uid="race-delete")
        self.addCleanup(pre_save.disconnect, sender=Complaint, dispatch_uid="race-close")
        self.addCleanup(pre_delete.disconnect, sender=ComplaintLot, dispatch_uid="race-delete")
        calls = (
            lambda: Complaint.objects.get(pk=complaint.pk).close(
                "Metal in regrind", by=made.qa, on_date=run_fixture.TODAY + datetime.timedelta(days=1)),
            lambda: closing.wait(5) and ComplaintLot.objects.get(pk=row.pk).delete(),
        )
        outcomes = [None, None]

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
        self.once(outcomes)
        complaint.refresh_from_db()
        self.assertFalse(complaint.status == ComplaintStatus.CLOSED and not complaint.complained_lots.exists(),
                         outcomes)

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


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class OpportunityRaceTests(RaceCase):
    def test_one_order_wins_one_opportunity_at_once(self):
        """Two opportunities won with one order at the same moment: the order is held, one is refused."""
        from apps.sales import tests_crm
        from apps.sales.crm import Opportunity, Stage

        made = fixture(self, tests_crm.CrmTestCase)
        order = made.make_order("10", "100")
        first = Opportunity.objects.create(customer=made.customer, title="Cement sacks", value=Decimal("250000"))
        second = Opportunity.objects.create(customer=made.customer, title="Cement sacks again",
                                            value=Decimal("100000"))
        self.once(race(Opportunity, *[
            lambda pk=pk: Opportunity.objects.get(pk=pk).win(SalesOrder.objects.get(pk=order.pk))
            for pk in (first.pk, second.pk)]))
        self.assertEqual(Opportunity.objects.filter(sales_order=order, stage=Stage.WON).count(), 1)
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class TwoReturnsOnAConsolidatedInvoiceTests(RaceCase):
    """
    O169: one invoice bills orders O1 and O2; their deliveries are returned at
    the same moment. Each return held only its own order and the stock, then
    its credit note reached for both orders: deadlock on PostgreSQL. A return
    now holds every order its notes will take, first.
    """
    from apps.sales import tests_base as _fixture

    setUp = _fixture.SalesTestCase.setUp
    make_order, ship, balance = (_fixture.SalesTestCase.make_order, _fixture.SalesTestCase.ship,
                                 _fixture.SalesTestCase.balance)

    def test_two_returns_on_two_orders_of_one_invoice_do_not_deadlock(self):
        from decimal import Decimal as D

        from apps.inventory.models import StockMovement
        from apps.sales.models import Delivery, Invoice, InvoiceLine

        first, second = self.make_order("5", "100"), self.make_order("5", "100")
        deliveries = [self.ship(first, "5"), self.ship(second, "5")]
        invoice = Invoice.objects.create(customer=self.customer, invoice_date=datetime.date(2026, 3, 1),
                                         currency=self.usd, receivable_account=self.ar)
        for order in (first, second):
            InvoiceLine.objects.create(invoice=invoice, order_line=order.lines.get(), quantity=D("5"),
                                       unit_price=D("100"), revenue_account=self.revenue)
        invoice.post()

        def give_back(pk):
            def call():
                delivery = Delivery.objects.get(pk=pk)
                delivery.create_return(quantities={delivery.lines.get(): D("2")})
            return call

        outcomes = race((StockMovement, Invoice), *[give_back(d.pk) for d in deliveries])
        self.assertEqual(outcomes, ["done", "done"], f"AR {self.balance(self.ar)}")
        self.assertEqual(Invoice.objects.filter(credits=invoice, posted=True).count(), 2)


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class StatementAndChallanRaceTests(RaceCase):
    def test_a_statement_does_not_close_over_a_line_unmatched_at_that_moment(self):
        """
        O63: September's statement, 0 to 1,000, its one line matched to the
        1,000 received on the 2nd. It is closed while the line is unmatched.
        close() held the statement; the line's save() read `closed` unlocked:
        closed, with its only line unexplained. The line now asks its
        statement under the statement's lock.
        """
        from apps.accounting.models import Account, AccountType, BankStatement, BankStatementLine
        from apps.core.models import Company, Currency, Party, PartyRole, PartyRoleAssignment

        inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        Company.objects.create(name="Co", base_currency=inr)
        bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        ar = Account.objects.create(code="1100", name="Receivables", account_type=AccountType.ASSET)
        party = Party.objects.create(code="P", name="P", default_currency=inr)
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.CUSTOMER)
        receipt = Payment.objects.create(
            party=party, direction=PaymentDirection.RECEIPT, payment_date=datetime.date(2026, 9, 2),
            amount=Decimal("1000"), currency=inr, bank_account=bank, counterpart_account=ar)
        receipt.post()
        statement = BankStatement.objects.create(
            bank_account=bank, start_date=datetime.date(2026, 9, 1), end_date=datetime.date(2026, 9, 30),
            opening_balance=Decimal("0"), closing_balance=Decimal("1000"))
        line = BankStatementLine.objects.create(statement=statement, date=datetime.date(2026, 9, 2),
                                                amount=Decimal("1000"))
        line.match(receipt)
        outcomes = race((BankStatement, BankStatementLine),
                        lambda: BankStatement.objects.get(pk=statement.pk).close(),
                        lambda: BankStatementLine.objects.get(pk=line.pk).unmatch())
        statement.refresh_from_db()
        line.refresh_from_db()
        self.once(outcomes)
        self.assertFalse(statement.closed and not line.is_resolved(),
                         f"{outcomes}: the statement closed with its only line unexplained")

    def test_a_deduction_is_not_reversed_while_a_challan_pays_it_over(self):
        """
        O64: 700 deducted under 194C on a bill of 35,000 in June. It is
        reversed while June's challan pays it over. The challan read what was
        owed unlocked: reversed and paid, TDS payable +700, paid for nothing.
        The challan now holds the deductions it pays before reading them.
        """
        from apps.purchasing.tds import TdsChallan, TdsDeduction

        books = fixture(self, tds_fixture.TdsTestCase)
        deduction = books.bill("35000", vendor=books.contractor(pan="AAACL1234F")).deduct_tds()
        self.assertEqual(deduction.amount, Decimal("700.00"))
        outcomes = race(JournalEntry,
                        lambda: TdsDeduction.objects.get(pk=deduction.pk).reverse(),
                        lambda: TdsChallan.pay(books.contract, datetime.date(2026, 6, 1), datetime.date(2026, 7, 7),
                                               books.bank, "00001", "0510002"))
        deduction.refresh_from_db()
        self.once(outcomes)
        self.assertEqual(books.balance(books.tds_payable), Decimal("0"),
                         f"{outcomes}: reversed {bool(deduction.reversed_entry_id)}, challan {deduction.challan_id}")


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class LedgerRaceTests(RaceCase):
    def books(self):
        from apps.accounting.models import Account, AccountType
        from apps.core.models import Company, Currency, Party, PartyRole, PartyRoleAssignment

        self.inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        Company.objects.create(name="Co", base_currency=self.inr)
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET,
                                           holds_money=True)
        self.ar = Account.objects.create(code="1100", name="Receivables", account_type=AccountType.ASSET)
        self.party = Party.objects.create(code="P", name="P", default_currency=self.inr)
        PartyRoleAssignment.objects.create(party=self.party, role=PartyRole.CUSTOMER)

    def test_a_line_is_not_added_to_an_entry_as_it_posts(self):
        """
        O125: a draft entry, Dr receivables 100 / Cr bank 100, posts while a
        line of Dr 50 is added to it. The line asked "posted?" without the
        entry's lock, which post() holds while it totals the lines: posted,
        and then 150 against 100.
        """
        self.books()
        entry = JournalEntry.objects.create(date=datetime.date(2026, 9, 2), memo="By hand")
        JournalLine.objects.create(entry=entry, account=self.ar, debit=Decimal("100"))
        JournalLine.objects.create(entry=entry, account=self.bank, credit=Decimal("100"))
        outcomes = race((JournalEntry, JournalLine),
                        lambda: JournalEntry.objects.get(pk=entry.pk).post(),
                        lambda: JournalLine.objects.create(entry=JournalEntry.objects.get(pk=entry.pk),
                                                           account=self.ar, debit=Decimal("50")))
        entry.refresh_from_db()
        self.once(outcomes)
        self.assertFalse(entry.posted and not entry.is_balanced(),
                         f"{outcomes}: posted at {entry.total_debit()} against {entry.total_credit()}")

    def test_an_account_is_not_unmarked_as_money_while_a_payment_posts_through_it(self):
        """
        O16: a draft receipt of 1,000 into the bank account posts while the
        account is unmarked as holding money. Unmarking asked whether
        anything was posted to it, and the payment whether it held money,
        each unlocked: both went through, 1,000 of money on an account that
        is not money.
        """
        from apps.accounting.models import Account

        self.books()
        receipt = Payment.objects.create(
            party=self.party, direction=PaymentDirection.RECEIPT, payment_date=datetime.date(2026, 9, 2),
            amount=Decimal("1000"), currency=self.inr, bank_account=self.bank, counterpart_account=self.ar)

        def unmark():
            account = Account.objects.get(pk=self.bank.pk)
            account.holds_money = False
            account.save()

        outcomes = race((Account, JournalEntry), unmark, lambda: Payment.objects.get(pk=receipt.pk).post())
        self.bank.refresh_from_db()
        posted = JournalLine.objects.filter(account=self.bank, entry__posted=True).exists()
        self.once(outcomes)
        self.assertFalse(posted and not self.bank.holds_money, f"{outcomes}: posted through an account not money")


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class TwoLandedCostsOnTheSameTwoReceiptLinesTests(RaceCase):
    """
    O182: 10 widgets at 5 and 10 gadgets at 15 received at one warehouse;
    carrier bills of 80 and 60 are allocated onto both receipt lines at
    once, one listing them [widgets, gadgets] and the other [gadgets,
    widgets]. Each took one line's stock position, then the other's, in
    the order listed: deadlock on inventory_stockposition. Every shelf is
    now taken at once, in key order, before the first is landed on.
    """

    def setUp(self):
        from apps.purchasing.tests_returns_and_landed import ThirdPartyLandedCostTests

        # The landed-cost tests' fixture, built by an instance that is never run as a test.
        self.fx = ThirdPartyLandedCostTests("test_a_separate_freight_bill_expenses_until_allocated")
        self.fx.setUp()

    def test_two_landed_costs_listing_two_lines_the_other_way_round_do_not_deadlock(self):
        from apps.inventory.models import Item, StockMovement
        from apps.purchasing.models import BillLine, GoodsReceipt, GoodsReceiptLine

        fx = self.fx
        _order, first = fx.goods_received("10", "5")
        other = Item.objects.create(sku="W2", name="Gadget", uom=fx.uom)
        second_order = PurchaseOrder.objects.create(vendor=fx.vendor, order_date=datetime.date(2026, 1, 1))
        second_line = PurchaseOrderLine.objects.create(order=second_order, item=other, uom=fx.uom,
                                                       quantity=Decimal("10"), unit_price=Decimal("15"))
        second_order.confirm()
        second = GoodsReceipt.objects.create(purchase_order=second_order, receipt_date=datetime.date(2026, 1, 5))
        GoodsReceiptLine.objects.create(receipt=second, order_line=second_line, warehouse=fx.warehouse,
                                        quantity_received=Decimal("10"))
        second.post()
        _bill1, charge1 = fx.carrier_bill("80")
        _bill2, charge2 = fx.carrier_bill("60")
        x, y = first.lines.get().pk, second.lines.get().pk

        def allocate(charge_pk, line_pks):
            def call():
                BillLine.objects.get(pk=charge_pk).allocate_landed_cost(
                    [GoodsReceiptLine.objects.get(pk=pk) for pk in line_pks])
            return call

        outcomes = race(StockMovement, allocate(charge1.pk, [x, y]), allocate(charge2.pk, [y, x]))
        self.assertEqual(outcomes, ["done", "done"])


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class WhatAChangeHoldsFirstRaceTests(RaceCase):
    """
    O35: what a row's save() holds before it (locked_before_it()) was read
    from the row as it stood before the lock. 300 of receipt A (1,000)
    applied to an invoice of 1,000 is re-pointed to receipt B at the moment
    another person edits its amount: the edit held A, the invoice and the
    allocation, then read it naming B, and took B last of all, against the
    lock order. Asked again once the row is held, the edit is refused and
    done again from what is there.
    """
    setUp = sales_fixture.SalesTestCase.setUp

    def test_an_edit_does_not_hold_the_payment_an_allocation_named_before_it_was_re_pointed(self):
        from apps.core.models import lock_for_change

        invoice = Invoice.objects.create(customer=self.customer, invoice_date=datetime.date(2026, 3, 1),
                                         currency=self.usd, receivable_account=self.ar)
        InvoiceLine.objects.create(invoice=invoice, item=self.item, quantity=Decimal("1"),
                                   unit_price=Decimal("1000"), revenue_account=self.revenue)
        invoice.post()
        a, b = (Payment.objects.create(party=self.customer, direction=PaymentDirection.RECEIPT,
                                       payment_date=datetime.date(2026, 3, 2), amount=Decimal("1000"),
                                       currency=self.usd, bank_account=self.bank, counterpart_account=self.ar)
                for _ in range(2))
        a.post()
        b.post()
        allocation = InvoicePayment.objects.create(invoice=invoice, payment=a, amount=Decimal("300"))

        held = threading.Event()

        def re_point():
            row = InvoicePayment.objects.get(pk=allocation.pk)
            lock_for_change(row, {"payment": b.pk})
            held.set()  # the edit reads its row before this commits, and asks for the locks after these
            row = InvoicePayment.objects.get(pk=allocation.pk)
            row.payment = b
            row.save()

        def edit_amount():
            stale = InvoicePayment.objects.get(pk=allocation.pk)
            held.wait(5)
            lock_for_change(stale, {"amount": "400"})
            row = InvoicePayment.objects.get(pk=allocation.pk)  # read again once held, as the viewsets do
            row.amount = Decimal("400")
            row.save()

        outcomes = race(InvoicePayment, re_point, edit_amount)
        self.once(outcomes)
        self.assertIn("while this was being done", " ".join(outcomes), outcomes)
        allocation.refresh_from_db()
        self.assertEqual((allocation.payment_id, allocation.amount), (b.pk, Decimal("300.00")), outcomes)


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class AdminEditRaceTests(RaceCase):
    """
    O22: the admin read its row without a lock and saved every field it had
    read. A draft receipt of 1,000 has its reference changed in the admin as
    it is posted: the admin wrote "not posted" back over the posting, and
    the receipt read draft while its entry stood on the ledger. The admin
    now holds the row from its read (RowHeldAdminMixin).
    """

    books = LedgerRaceTests.books

    def admin_post(self, obj, **changes):
        from django.contrib import admin as site_admin
        from django.contrib.auth.models import User
        from django.test import Client, RequestFactory

        model_admin = site_admin.site._registry[type(obj)]
        user = User.objects.filter(username="boss").first() or User.objects.create_superuser("boss", "b@x.in", "pw")
        request = RequestFactory().get("/")
        request.user = user
        form = model_admin.get_form(request, obj)(instance=obj)
        data = {name: ("" if form[name].value() is None else form[name].value()) for name in form.fields}
        data.update(changes, _save="Save")
        client = Client()
        staff_session(client, user)
        url = f"/admin/{obj._meta.app_label}/{obj._meta.model_name}/{obj.pk}/change/"

        def call():
            response = client.post(url, data)
            if response.status_code != 302:
                raise AssertionError(f"admin answered {response.status_code}")
        return call

    def test_an_admin_edit_does_not_write_a_payment_back_to_draft_as_it_posts(self):
        self.books()
        receipt = Payment.objects.create(
            party=self.party, direction=PaymentDirection.RECEIPT, payment_date=datetime.date(2026, 9, 2),
            amount=Decimal("1000"), currency=self.inr, bank_account=self.bank, counterpart_account=self.ar)
        edit = self.admin_post(Payment.objects.get(pk=receipt.pk), reference="CHQ 0042")
        outcomes = race(Payment, edit, lambda: Payment.objects.get(pk=receipt.pk).post(), atomic=False)
        receipt.refresh_from_db()
        entry_posted = JournalEntry.objects.filter(pk=receipt.journal_entry_id, posted=True).exists() \
            if receipt.journal_entry_id else JournalLine.objects.filter(account=self.bank, entry__posted=True).exists()
        self.assertFalse(entry_posted and not receipt.posted,
                         f"{outcomes}: the entry is posted and the receipt reads draft")


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class PunchFileRaceTests(RaceCase):
    def test_a_day_entered_by_hand_is_not_written_over_by_the_punch_file(self):
        """
        O18: 7 October imported present. It is entered by hand as absent while
        the punch file imports it again. The import read the day, imported,
        before the employee's lock, then wrote it back present over the hand
        entry. The file's employees are now held before any day is read: the
        hand entry stands, absent.
        """
        from apps.hr import tests_attendance
        from apps.hr.attendance import AttendanceDay, AttendanceSource, AttendanceStatus, import_punches

        made = fixture(self, tests_attendance.AttendanceTestCase)
        made.mark(7, source=AttendanceSource.IMPORT)

        def by_hand():
            day = AttendanceDay.objects.get(employee=made.worker, on=datetime.date(2026, 10, 7))
            day.status, day.source = AttendanceStatus.ABSENT, AttendanceSource.MANUAL
            day.time_in = day.time_out = None
            day.save()

        outcomes = race(AttendanceDay, by_hand, lambda: import_punches(
            "employee_number,date,shift,in,out\nW1,2026-10-07,,06:00,14:00\n", commit=True), atomic=False)
        day = AttendanceDay.objects.get(employee=made.worker, on=datetime.date(2026, 10, 7))
        self.assertEqual((day.source, day.status), (AttendanceSource.MANUAL, AttendanceStatus.ABSENT), outcomes)


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class RunPostedAsTheRegisterChangesRaceTests(RaceCase):
    """
    O20: October's run is calculated for a worker with a full month, then
    posted while 7 October is marked absent, or unpaid leave for it is
    approved. The run holds its people before working each slip out again;
    the register and leave hold the person before asking whether a posted
    run paid on the day. Either the run posts first and the change is
    refused, or the change lands first and the run, worked out again, no
    longer matches and is refused: never a run posted at a figure the
    register no longer gives.
    """

    def run_against(self, prepare):
        from apps.hr import tests_attendance
        from apps.hr.attendance import AttendanceDay

        made = fixture(self, tests_attendance.AttendanceTestCase)
        run = made.october()
        run.calculate()
        slip = run.payslips.get()
        change = prepare(made)
        outcomes = race((AttendanceDay, LeaveRequest, JournalEntry),
                        lambda: PayRun.objects.get(pk=run.pk).post(), change)
        run.refresh_from_db()
        self.once(outcomes)
        if run.status == "posted":
            Payslip.objects.get(pk=slip.pk).check_current()  # raises if posted at a stale figure
        return outcomes

    def test_a_run_does_not_post_over_an_absence_marked_as_it_posts(self):
        from apps.hr.attendance import AttendanceStatus

        self.run_against(lambda made: lambda: made.mark(7, AttendanceStatus.ABSENT))

    def test_a_run_does_not_post_over_unpaid_leave_approved_as_it_posts(self):
        from apps.hr.models import LeavePolicy

        def prepare(made):
            policy = LeavePolicy.objects.create(code="LWP-R", name="Without pay", leave_type=LeaveType.UNPAID,
                                                annual_days=Decimal("30"), is_paid=False)
            request = LeaveRequest.objects.create(
                employee=made.worker, policy=policy, leave_type=LeaveType.UNPAID,
                start_date=datetime.date(2026, 10, 7), end_date=datetime.date(2026, 10, 7))
            return lambda: LeaveRequest.objects.get(pk=request.pk).approve(by=made.boss)

        self.run_against(prepare)


@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class OneRecallALotRaceTests(RaceCase):
    """
    O207: B2 and B3 joined into RB-J, which passed its own inspection; both
    are rejected at once. RB-J is recalled once, with one to-do, not two:
    each rejection holds RB-J's row before asking whether it is recalled.
    """

    def test_two_rejections_at_once_on_two_sources_tell_once(self):
        from apps.core.chatter import FollowUp
        from apps.manufacturing import tests_recall
        from apps.manufacturing.rebatch import rebatch
        from apps.quality.models import Inspection, Reading

        made = fixture(self, tests_recall.RecallTestCase)
        joined = made.new("RB-J")
        rebatch(made.bag, made.plant, [(made.b2, "100"), (made.b3, "100")], [(joined, "200")],
                "Short ends", on_date=tests_recall.TODAY)
        made.inspect(joined, tests_recall.IN)
        plan = made.bag_spec.inspection_plan
        line = plan.lines.get()
        drafts = []
        # Numbered as drafts, so that posting takes no lock on the numbering: that lock would
        # serialise the two posts by accident and prove nothing about the lot's own.
        for number, lot in enumerate((made.b2, made.b3), start=1):
            draft = Inspection.objects.create(lot=lot, plan=plan, inspected_on=tests_recall.TODAY,
                                              number=f"QC-RACE-{number}")
            for number, value in enumerate(tests_recall.OUT[:line.sample_size], start=1):
                Reading.objects.create(inspection=draft, plan_line=line, value=Decimal(value),
                                       sample_reference=f"S{number}")
            drafts.append(draft.pk)
        outcomes = race(Inspection, *[lambda pk=pk: Inspection.objects.get(pk=pk).post() for pk in drafts])
        self.assertEqual(outcomes, ["done", "done"], outcomes)
        recalls = Inspection.objects.filter(lot=joined, recalled_by__isnull=False)
        self.assertEqual(recalls.count(), 1)
        self.assertEqual(FollowUp.objects.filter(object_id=recalls.get().pk).count(), 1)


@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class NoBatchClearedPastItsRejectionRaceTests(RaceCase):
    """
    O123: DR-7's rejection posting while the bay's drums are accepted. Either
    the rejection comes first and the accept is refused, or the accept comes
    first and is recorded before the rejection posts. Accepted after the
    rejection was decided, on a read taken before it, is the race.
    """

    def test_a_rejection_and_an_accept_at_once(self):
        from apps.inventory.models import StockMovement
        from apps.purchasing import tests_inspection
        from apps.purchasing.models import GoodsReceipt, ReceiptInspection
        from apps.quality.models import Inspection

        made = fixture(self, tests_inspection.ARejectedBatchIsNotClearedTests)
        draft = made.rejection(number="QC-RACE-7")  # numbered: no lock on the numbering row
        outcomes = race((Inspection, StockMovement),
                        lambda: Inspection.objects.get(pk=draft.pk).post(),
                        lambda: GoodsReceipt.objects.get(pk=made.receipt.pk).accept(made.warehouse))
        rejection = Inspection.objects.get(pk=draft.pk)
        self.assertEqual(outcomes[0], "done", outcomes)
        accepted = ReceiptInspection.objects.filter(accepted=True).first()
        if accepted is not None:
            self.assertLess(accepted.created_at, rejection.posted_at,
                            "the drums were cleared after their rejection was posted")


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class PayrollOutputRaceTests(RaceCase):
    """g21: the run's bank file is paid once, and an advance does not go as a run recovers it."""

    def bank_file(self):
        from apps.hr import tests_pay_outputs
        from apps.hr.bank_file import SalaryTransferFile

        made = fixture(self, tests_pay_outputs.BankFileTests)
        for person in made.people:
            made.banked(person)
        return made, SalaryTransferFile.prepare(made.paid_run)

    def test_a_bank_file_is_paid_once(self):
        from apps.hr.bank_file import SalaryTransferFile

        made, written = self.bank_file()
        payments = [made.money(made.people[0].party, "23825.50") for _ in range(2)]
        self.once(race(Payslip, *[
            lambda payment=payment: SalaryTransferFile.objects.get(pk=written.pk).mark_paid(
                Payment.objects.get(pk=payment.pk)) for payment in payments]))

    def test_a_slip_is_not_paid_by_the_file_and_by_a_payment_of_its_own(self):
        from apps.hr.bank_file import SalaryTransferFile

        made, written = self.bank_file()
        slip = made.paid_run.payslips.get(employee=made.people[1])
        bulk, own = made.money(made.people[0].party, "23825.50"), made.money(made.people[1].party, "8450.50")
        self.once(race(Payslip,
                       lambda: SalaryTransferFile.objects.get(pk=written.pk).mark_paid(Payment.objects.get(pk=bulk.pk)),
                       lambda: Payslip.objects.get(pk=slip.pk).pay(Payment.objects.get(pk=own.pk))))

    def test_an_advance_is_not_voided_as_a_run_recovers_it(self):
        import datetime

        from apps.hr import tests_pay_outputs
        from apps.hr.advances import SalaryAdvance

        made = fixture(self, tests_pay_outputs.AdvancesTests)
        advance = made.give()
        june = made.month(6, post=False)
        self.once(race(None, lambda: PayRun.objects.get(pk=june.pk).post(),
                       lambda: SalaryAdvance.objects.get(pk=advance.pk).void(on_date=datetime.date(2026, 7, 1))))

    def test_one_payment_offered_to_the_file_and_to_a_slip_on_it_is_refused_in_words(self):
        """
        Each path held where the other would reach for what it holds: the file's past the payment, the
        slip's before it. Taking the payment before the slips, the file waited on the slip and the slip
        on the payment, and PostgreSQL ended one as a deadlock. Now both take the slip first.
        """
        import threading
        from unittest.mock import patch

        from django.db import transaction

        from apps.hr import bank_file, payroll

        made, written = self.bank_file()
        slip = made.paid_run.payslips.get(employee=made.people[1])
        # The file's total, so the file passes its checks and reaches for the slips.
        shared = made.money(made.people[1].party, "23825.50")
        barrier = threading.Barrier(2, timeout=3)
        real_file, real_pay = bank_file.lock_rows, payroll.lock_rows

        def meet():
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass

        def file_locks(*rows, **kwargs):
            real_file(*rows, **kwargs)
            if any(isinstance(row, Payment) for row in rows):
                meet()

        def slip_locks(*rows, **kwargs):
            if any(isinstance(row, Payment) for row in rows):
                meet()
            real_pay(*rows, **kwargs)

        outcomes = [None, None]

        def attempt(index, call):
            try:
                with transaction.atomic():
                    call()
                outcomes[index] = "done"
            except Exception as exc:
                outcomes[index] = f"refused: {exc}"
            finally:
                connection.close()

        calls = [lambda: bank_file.SalaryTransferFile.objects.get(pk=written.pk).mark_paid(
                     Payment.objects.get(pk=shared.pk)),
                 lambda: Payslip.objects.get(pk=slip.pk).pay(Payment.objects.get(pk=shared.pk))]
        with patch.object(bank_file, "lock_rows", file_locks), patch.object(payroll, "lock_rows", slip_locks):
            threads = [threading.Thread(target=attempt, args=pair) for pair in enumerate(calls)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
        self.assertFalse(any("deadlock" in str(outcome) for outcome in outcomes), outcomes)
        self.assertEqual(outcomes[0], "done", outcomes)
        # Refused in words, as one serial order or the other words it. Since rereview 5 every path takes the
        # payment first, so the slip now meets a file already paid; before, it met the amount.
        self.assertTrue(outcomes[1].startswith("refused:") and any(
            words in outcomes[1] for words in ("E2 has already been paid for this run", "net pay is 8450.50")),
            outcomes)


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class AnAdvancesPaymentAndItsRecoveryRaceTests(RaceCase):
    """Review RP1: the run is held after re-working its slips, before its entry; the advance's payment is voided."""

    def test_an_advances_payment_is_not_voided_while_a_run_recovers_from_it(self):
        import datetime
        import threading
        from unittest.mock import patch

        from django.db import transaction

        from apps.hr import tests_pay_outputs
        from apps.hr.payroll import PayRunStatus, PayslipLine

        made = fixture(self, tests_pay_outputs.AdvancesTests)
        advance = made.give()
        june = made.month(6, post=False)
        reached, go, outcomes = threading.Event(), threading.Event(), {}
        real = PayRun._check_not_already_paid

        def paused(run):
            result = real(run)
            reached.set()
            go.wait(5)
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
                reached.wait(10)
                with transaction.atomic():
                    Payment.objects.get(pk=advance.payment_id).void(on_date=datetime.date(2026, 6, 2))
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
            thread.join(30)
        voided = Payment.objects.get(pk=advance.payment_id).is_voided()
        recovered = PayslipLine.objects.filter(advance=advance, payslip__run__status=PayRunStatus.POSTED).exists()
        self.assertEqual((voided, recovered), (False, True), outcomes)
        self.assertIn("has recovered 2000.00", outcomes["void"])
        self.assertEqual(made.balance(made.advances), advance.balance())


@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class BlanketCallOffRaceTests(RaceCase):
    """
    O227, refuted. A release line raised takes its order, the line, then the
    agreement; release() takes the agreement, then only the order it makes,
    which nobody else can hold. No cycle, so no deadlock; and the agreement's
    lock makes the second wait and re-read. 100 agreed, 40 released on PO-1:
    raising it to 70 and releasing 40 more at once, one is refused.
    """

    def setUp(self):
        purchase_fixture.PurchasingLifecycleTestCase.setUp(self)

    def test_a_raise_and_a_release_at_once_call_off_no_more_than_agreed(self):
        from apps.purchasing.models import BlanketOrder, BlanketOrderLine, PurchaseOrderLine

        blanket = BlanketOrder.objects.create(vendor=self.vendor, start_date=datetime.date(2026, 1, 1),
                                              end_date=datetime.date(2026, 12, 31), currency=self.usd)
        agreed = BlanketOrderLine.objects.create(blanket=blanket, item=self.item, uom=self.uom,
                                                 quantity=Decimal("100"), unit_price=Decimal("5"))
        blanket.confirm()
        first = blanket.release({agreed: Decimal("40")}, order_date=datetime.date(2026, 2, 1))
        line_pk = first.lines.get().pk

        def raise_it():
            line = PurchaseOrderLine.objects.get(pk=line_pk)
            line.quantity = Decimal("70")
            line.save()

        def release_more():
            BlanketOrder.objects.get(pk=blanket.pk).release(
                {BlanketOrderLine.objects.get(pk=agreed.pk): Decimal("40")}, order_date=datetime.date(2026, 2, 2))

        outcomes = race((PurchaseOrderLine, PurchaseOrder), raise_it, release_more)
        self.assertEqual(sum(outcome == "done" for outcome in outcomes), 1, outcomes)
        self.assertLessEqual(BlanketOrderLine.objects.get(pk=agreed.pk).quantity_released(), Decimal("100"))


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class OpeningImportRaceTests(RaceCase):
    """Review of O280, O313: the same opening file brought in twice at once comes in once."""

    BALANCES = "account,debit,credit\n1010,250000,\n3900,,250000\n"
    STOCK = "sku,warehouse,quantity,unit_cost\nWDG-1,WH1,1000,3.50\n"

    def setUp(self):
        from apps.imports.tests import ImportTestCase

        made = fixture(self, ImportTestCase)
        self.opening, self.balance = made.opening, made.balance

    @staticmethod
    def bring(kind, text, **options):
        from apps.imports.importer import run
        from apps.imports.tests import GO_LIVE

        def call():
            report = run(kind, text, commit=True, date=GO_LIVE, **options)
            if not report.committed:
                raise ValueError(report.errors)
        return call

    def test_two_opening_balance_files_at_once_post_the_bank_once(self):
        from apps.accounting.models import Account, BooksOpening, JournalEntry
        from apps.imports.tests import GO_LIVE

        BooksOpening.objects.create(opened_on=GO_LIVE, opening_account=self.opening)
        self.once(race(JournalEntry, *[self.bring("opening_balances", self.BALANCES)] * 2))
        self.assertEqual(self.balance(Account.objects.get(code="1010")), Decimal("250000"))

    def test_two_opening_stock_files_at_once_bring_the_stock_in_once(self):
        from django.db.models import Sum

        from apps.inventory.adjustments import AdjustmentReason, StockAdjustment
        from apps.inventory.models import StockMovement

        AdjustmentReason.objects.create(code="OPENING", name="Opening stock", account=self.opening)
        self.once(race(StockAdjustment, *[self.bring("opening_stock", self.STOCK, reason="OPENING")] * 2))
        on_hand = StockMovement.objects.filter(item__sku="WDG-1").aggregate(t=Sum("quantity"))["t"]
        self.assertEqual(on_hand, Decimal("1500"))


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class AMoveTakesItsOwnPositionsRaceTests(RaceCase):
    """Review item 7: move_stock said its caller held the positions and nothing checked. A caller that did not
    let a creel load of D-1 (100 kg) and a move of the same 100 kg to the store both through."""

    def test_a_load_and_an_unlocked_move_of_one_doff(self):
        from django.utils import timezone

        from apps.inventory.models import StockMovement, Warehouse
        from apps.inventory.transfers import move_stock
        from apps.manufacturing.orders import WorkOrder
        from apps.manufacturing.tape_loads import CreelSide, TapeLoad, load_tape
        from apps.manufacturing.tests_orders import TODAY
        from apps.manufacturing.tests_station import at
        from apps.manufacturing.tests_tape_loads import TapeLoadTestCase

        made = fixture(self, TapeLoadTestCase)
        WorkOrder.objects.filter(pk=made.run.pk).update(backflush=True)
        store = Warehouse.objects.create(code="ST", name="Store")
        d1 = made.doffs["D-1"]
        outcomes = race(
            (TapeLoad, StockMovement),
            lambda: load_tape(made.station, made.operator, made.l17, "D-1", "100", CreelSide.WARP,
                              at=at(TODAY, 9)),
            lambda: move_stock(made.tape, made.plant, store, Decimal("100"), "T-1", timezone.now(), "", lot=d1))
        self.once(outcomes)


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class ACostingMethodCannotChangeUnderAFirstReceiptRace(RaceCase):
    """
    The review of O330: a switch of an item's costing method and its first receipt at once both went through,
    leaving a FIFO item with a movement booked under average. The freeze now holds the item's row before it asks.
    """

    def test_it(self):
        from apps.core.models import UnitOfMeasure as Unit
        from apps.inventory.models import Item as Stocked, MovementType as Kind, StockMovement as Movement
        from apps.inventory.models import Warehouse as Shelf
        from django.utils import timezone

        kg = Unit.objects.create(code="kgr", name="kg")
        item = Stocked.objects.create(sku="RACE", name="r", uom=kg)
        shelf = Shelf.objects.create(code="RW", name="W")

        def switch():
            fresh = Stocked.objects.get(pk=item.pk)
            fresh.costing_method = "fifo"
            fresh.save()

        def receive():
            Movement.objects.create(item=Stocked.objects.get(pk=item.pk), warehouse=shelf,
                                    movement_type=Kind.RECEIPT, uom=kg, quantity=Decimal("5"),
                                    value=Decimal("10.00"), occurred_at=timezone.now())

        outcomes = race((Stocked, Movement), switch, receive)
        item.refresh_from_db()
        # One of the two goes through: never a FIFO item with a movement worked out under average.
        self.assertEqual(outcomes.count("done"), 1, outcomes)
        self.assertEqual((item.costing_method, item.movements.count()) in {("average", 1), ("fifo", 0)}, True,
                         (item.costing_method, item.movements.count(), outcomes))


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class BankImportRaceTests(RaceCase):
    """O290: the same bank file imported twice at once adds its rows once."""

    FILE = "Date,Narration,Ref No,Withdrawal,Deposit\n05-09-2026,NEFT in,UTR1,,500.00\n06-09-2026,Charges,C1,10.00,\n"

    def test_the_same_file_imported_twice_at_once_adds_its_rows_once(self):
        from apps.accounting.bank_import import import_lines
        from apps.accounting.models import Account, AccountType, BankStatement, BankStatementLine
        from apps.core.models import Company, Currency

        inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        Company.objects.create(name="Deccan Polysacks", base_currency=inr)
        bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        statement = BankStatement.objects.create(
            bank_account=bank, start_date=datetime.date(2026, 9, 1), end_date=datetime.date(2026, 9, 30),
            opening_balance=Decimal("0"), closing_balance=Decimal("490.00"))
        mine = lambda: import_lines(BankStatement.objects.get(pk=statement.pk), self.FILE, commit=True)  # noqa: E731
        outcomes = race(BankStatementLine, mine, mine)
        self.assertEqual(BankStatementLine.objects.filter(statement=statement).count(), 2, outcomes)
        self.assertEqual(outcomes, ["done", "done"])


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class TwoDunningRunsAtOnceRaceTests(RaceCase):
    """O296 (probe R1): the scheduled run and a click at once send the level once, and neither run fails."""

    def test_two_dunning_runs_at_once_send_each_level_once_and_neither_fails(self):
        from apps.sales.models import DunningLevel, DunningNotice, run_dunning

        sales = fixture(self, sales_fixture.SalesTestCase)
        DunningLevel.objects.create(name="First", days_overdue=7)
        order = sales.make_order("10", "100")
        sales.ship(order, 10)
        invoice = sales.bill(order, on=datetime.date(2026, 3, 1))
        day = invoice.due_date + datetime.timedelta(days=10)
        outcomes = race(DunningNotice, lambda: run_dunning(as_of=day, send=False),
                        lambda: run_dunning(as_of=day, send=False))
        self.assertEqual(outcomes, ["done", "done"])
        self.assertEqual(DunningNotice.objects.filter(invoice=invoice).count(), 1)
