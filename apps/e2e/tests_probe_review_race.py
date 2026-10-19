"""
review_race (gate g21): probes of the race work 69cc818..8caae5c.

PostgreSQL only, like tests_races.py, whose race(), fixture() and RaceCase
these use. Each test names the shape it presses on; a failure is a finding.
"""

import datetime
import threading
import time
import unittest
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import TransactionTestCase, tag

from apps.accounting.models import JournalEntry, JournalLine, Payment, PaymentDirection
from apps.e2e import tests_races as R
from apps.e2e.tests_races import RaceCase, fixture, race
from apps.purchasing import tests_tds as tds_fixture
from apps.core.tests_second_factor import staff_session

pg = unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")


@tag("race")
@pg
class AdminBulkActionProbe(RaceCase):
    """O22: the admin's changelist action reads its rows before it locks any."""

    books = R.LedgerRaceTests.books

    def test_admin_delete_selected_of_a_draft_payment_as_it_posts(self):
        from django.contrib.auth.models import User
        from django.test import Client

        self.books()
        receipt = Payment.objects.create(
            party=self.party, direction=PaymentDirection.RECEIPT, payment_date=datetime.date(2026, 9, 2),
            amount=Decimal("1000"), currency=self.inr, bank_account=self.bank, counterpart_account=self.ar)
        user = User.objects.create_superuser("boss", "b@x.in", "pw")
        client = Client()
        staff_session(client, user)
        answers = []

        def bulk_delete():
            response = client.post("/admin/accounting/payment/", {
                "action": "delete_selected", "_selected_action": [receipt.pk], "post": "yes"})
            answers.append(response.status_code)

        outcomes = race(Payment, lambda: Payment.objects.get(pk=receipt.pk).post(), bulk_delete, atomic=False)
        gone = not Payment.objects.filter(pk=receipt.pk).exists()
        on_ledger = JournalLine.objects.filter(account=self.bank, entry__posted=True).exists()
        self.assertFalse(gone and on_ledger,
                         f"{outcomes} {answers}: the payment is deleted and its entry stands posted on the bank")


@tag("race")
@pg
class BankAccountProbe(RaceCase):
    """O16: other payers of a money account than Payment."""

    books = R.LedgerRaceTests.books

    def test_a_challan_is_not_paid_through_a_bank_unmarked_as_it_posts(self):
        from apps.accounting.models import Account
        from apps.purchasing.tds import TdsChallan

        books = fixture(self, tds_fixture.TdsTestCase)
        books.bill("35000", vendor=books.contractor(pan="AAACL1234F")).deduct_tds()

        def unmark():
            account = Account.objects.get(pk=books.bank.pk)
            account.holds_money = False
            account.save()

        outcomes = race((Account, JournalEntry), unmark,
                        lambda: TdsChallan.pay(books.contract, datetime.date(2026, 6, 1), datetime.date(2026, 7, 7),
                                               books.bank, "00001", "0510002"), atomic=False)
        books.bank.refresh_from_db()
        posted = JournalLine.objects.filter(account=books.bank, entry__posted=True).exists()
        self.assertFalse(posted and not books.bank.holds_money,
                         f"{outcomes}: a challan posted through an account no longer money")

    def test_a_line_is_not_matched_to_a_payment_voided_as_it_is_matched(self):
        from apps.accounting.models import BankStatement, BankStatementLine

        self.books()
        receipt = Payment.objects.create(
            party=self.party, direction=PaymentDirection.RECEIPT, payment_date=datetime.date(2026, 9, 2),
            amount=Decimal("1000"), currency=self.inr, bank_account=self.bank, counterpart_account=self.ar)
        receipt.post()
        statement = BankStatement.objects.create(
            bank_account=self.bank, start_date=datetime.date(2026, 9, 1), end_date=datetime.date(2026, 9, 30),
            opening_balance=Decimal("0"), closing_balance=Decimal("1000"))
        line = BankStatementLine.objects.create(statement=statement, date=datetime.date(2026, 9, 2),
                                                amount=Decimal("1000"))
        stale = Payment.objects.get(pk=receipt.pk)  # what a screen read before the void
        # fix_race2: raced, "match, then void" is a serial order that also ends matched to a voided
        # payment (void asks nothing of statement lines), so the race could not tell the defect from
        # it. The defect is the decision on the object passed in: the void commits, then the match is
        # made with what was read before it. Unlocked, it matched; held and read again, it refuses.
        Payment.objects.get(pk=receipt.pk).void(on_date=datetime.date(2026, 9, 3))
        try:
            BankStatementLine.objects.get(pk=line.pk).match(stale)
        except Exception as exc:  # noqa: BLE001 - the refusal is the answer
            outcome = f"refused: {exc}"
        else:
            outcome = "done"
        line.refresh_from_db()
        self.assertFalse(line.payment_id == receipt.pk and not line.returned_payment_id,
                         f"{outcome}: matched to a payment the bank never saw because it was voided")


@tag("race")
@pg
class PayRunInputsProbe(RaceCase):
    """O20: the run holds its people; does every writer of what it reads hold the person?"""

    def test_a_rate_is_not_edited_inside_a_period_as_its_run_posts(self):
        from apps.hr import tests_attendance
        from apps.hr.payroll import EmployeeCompensation, PayRun, Payslip

        made = fixture(self, tests_attendance.AttendanceTestCase)
        run = made.october()
        run.calculate()
        slip = run.payslips.get()
        row = EmployeeCompensation.objects.get(employee=made.worker, component=made.salary)

        def edit():
            current = EmployeeCompensation.objects.get(pk=row.pk)
            current.amount = Decimal("14000")
            current.save()

        outcomes = race((EmployeeCompensation, JournalEntry), lambda: PayRun.objects.get(pk=run.pk).post(), edit)
        run.refresh_from_db()
        row.refresh_from_db()
        self.assertFalse(run.status == "posted" and row.amount != Decimal("13500.00"),
                         f"{outcomes}: the run posted at 13,500 and the rate paid on now reads {row.amount}")
        if run.status == "posted":
            Payslip.objects.get(pk=slip.pk).check_current()

    def test_a_leave_is_not_cancelled_as_its_run_posts(self):
        from apps.hr import tests_attendance
        from apps.hr.models import LeavePolicy, LeaveRequest, LeaveType
        from apps.hr.payroll import PayRun, Payslip

        made = fixture(self, tests_attendance.AttendanceTestCase)
        policy = LeavePolicy.objects.create(code="LWP-P", name="Without pay", leave_type=LeaveType.UNPAID,
                                            annual_days=Decimal("30"), is_paid=False)
        request = LeaveRequest.objects.create(
            employee=made.worker, policy=policy, leave_type=LeaveType.UNPAID,
            start_date=datetime.date(2026, 10, 7), end_date=datetime.date(2026, 10, 7))
        request.approve(by=made.boss)
        run = made.october()
        run.calculate()
        slip = run.payslips.get()
        cancel = getattr(LeaveRequest, "cancel", None)
        if cancel is None:
            self.skipTest("no cancel()")
        outcomes = race((LeaveRequest, JournalEntry), lambda: PayRun.objects.get(pk=run.pk).post(),
                        lambda: LeaveRequest.objects.get(pk=request.pk).cancel(
                            on_date=datetime.date(2026, 10, 6), by=made.boss, as_hr=True))
        run.refresh_from_db()
        request.refresh_from_db()
        print("LEAVE", outcomes, run.status, request.status)
        self.once(outcomes)
        if run.status == "posted":
            Payslip.objects.get(pk=slip.pk).check_current()  # raises if posted at a stale figure


@tag("race")
@pg
class ReservationProbe(RaceCase):
    """O39: claim() alone, with the source not yet holding a claim."""

    setUp = R.sales_fixture.SalesTestCase.setUp

    def test_two_claims_of_one_source_to_two_shelves_leave_one_claim(self):
        from apps.core.models import Party
        from apps.inventory.models import Warehouse
        from apps.inventory.reservations import StockReservation
        from apps.inventory.models import Item

        second = Warehouse.objects.create(code="WH2", name="Second")
        source = self.customer  # any row will do as a source
        with mock.patch.object(Item, "available_at", return_value=Decimal("100")):
            outcomes = race(StockReservation,
                            lambda: StockReservation.objects.claim(source, self.item, self.warehouse, Decimal("5")),
                            lambda: StockReservation.objects.claim(source, self.item, second, Decimal("5")))
        open_claims = StockReservation.objects.for_source(source).open().count()
        print("RESERVATION", outcomes, open_claims)
        # fix_race2: one after the other, the second first claim moves the first; neither is refused.
        self.assertEqual(outcomes, ["done", "done"], outcomes)
        self.assertEqual(open_claims, 1, f"{outcomes}: {open_claims} open claims for one source")


@tag("race")
@pg
class ChatterProbe(RaceCase):
    """O17: a note deleted twice at once."""

    def test_a_note_deleted_by_two_clicks_answers_204_and_404_never_500(self):
        from django.contrib.auth.models import Group, User
        from django.test import Client

        from apps.core import chatter_api
        from apps.core.chatter import Note
        from apps.core.models import Party
        from django.contrib.contenttypes.models import ContentType

        user = User.objects.create_superuser("writer", "w@x.in", "pw")
        party = Party.objects.create(code="P", name="P")
        note = Note.objects.create(content_type=ContentType.objects.get_for_model(Party), object_id=party.pk,
                                   body="x", created_by=user, updated_by=user)
        slow = chatter_api._held
        first_in = threading.Event()

        def held(row):
            out = slow(row)
            if not first_in.is_set():
                first_in.set()
                time.sleep(1.0)  # the first holds the row a while after reading it
            return out

        statuses = []

        def delete(wait):
            def call():
                client = Client(raise_request_exception=False)
                staff_session(client, user)
                if wait:
                    first_in.wait(5)
                    time.sleep(0.2)
                statuses.append(client.delete(f"/api/core/notes/{note.pk}/").status_code)
            return call

        with mock.patch.object(chatter_api, "_held", held):
            race(None, delete(False), delete(True), atomic=False)
        print("NOTE", statuses)
        self.assertNotIn(500, statuses, statuses)


@tag("race")
@pg
class UnseenReasonProbe(RaceCase):
    """lock_order.UNSEEN: is each reason true?"""

    def test_two_addresses_of_one_party_made_primary_at_once(self):
        from apps.core.models import Address, Party

        party = Party.objects.create(code="P", name="P")
        a = Address.objects.create(party=party, address_type="billing", line1="1", city="X")
        b = Address.objects.create(party=party, address_type="billing", line1="2", city="X")

        def make(pk):
            def call():
                row = Address.objects.get(pk=pk)
                row.is_primary = True
                row.save()
            return call

        outcomes = race(Address, make(a.pk), make(b.pk))
        self.assertEqual(outcomes, ["done", "done"], outcomes)


@tag("race")
@pg
class MoreLedgerProbe(RaceCase):
    books = R.LedgerRaceTests.books

    def test_a_line_is_not_deleted_from_an_entry_as_it_posts(self):
        self.books()
        entry = JournalEntry.objects.create(date=datetime.date(2026, 9, 2), memo="By hand")
        keep = JournalLine.objects.create(entry=entry, account=self.ar, debit=Decimal("100"))
        JournalLine.objects.create(entry=entry, account=self.bank, credit=Decimal("100"))
        extra = JournalLine.objects.create(entry=entry, account=self.ar, debit=Decimal("50"))
        JournalLine.objects.create(entry=entry, account=self.bank, credit=Decimal("50"))
        outcomes = race((JournalEntry, JournalLine), lambda: JournalEntry.objects.get(pk=entry.pk).post(),
                        lambda: JournalLine.objects.get(pk=extra.pk).delete())
        entry.refresh_from_db()
        print("LINEDEL", outcomes, entry.posted, entry.is_balanced())
        self.assertFalse(entry.posted and not entry.is_balanced(), f"{outcomes}")

    def test_two_lines_swapped_between_two_draft_entries_do_not_deadlock(self):
        self.books()
        one = JournalEntry.objects.create(date=datetime.date(2026, 9, 2), memo="one")
        two = JournalEntry.objects.create(date=datetime.date(2026, 9, 2), memo="two")
        a = JournalLine.objects.create(entry=one, account=self.ar, debit=Decimal("10"))
        b = JournalLine.objects.create(entry=two, account=self.ar, debit=Decimal("20"))

        def move(line_pk, entry_pk):
            def call():
                row = JournalLine.objects.get(pk=line_pk)
                row.entry_id = entry_pk
                row.save()
            return call

        outcomes = race(JournalLine, move(a.pk, two.pk), move(b.pk, one.pk))
        print("SWAP", outcomes)
        self.assertEqual(outcomes, ["done", "done"], outcomes)


@tag("race")
@pg
class OrderLineProbe(RaceCase):
    """O21: the order and the line, in the same order on close-short and delete."""

    setUp = R.sales_fixture.SalesTestCase.setUp
    make_order = R.sales_fixture.SalesTestCase.make_order

    def test_a_line_closed_short_as_it_is_deleted_does_not_deadlock(self):
        from apps.sales.models import SalesOrderLine

        order = self.make_order("10")
        line = order.lines.get()
        outcomes = race(SalesOrderLine,
                        lambda: SalesOrderLine.objects.get(pk=line.pk).close_short("no more"),
                        lambda: SalesOrderLine.objects.get(pk=line.pk).delete())
        print("CLOSESHORT", outcomes)
        self.assertFalse(any("deadlock" in o for o in outcomes), outcomes)


@tag("race")
@pg
class CutProbe(RaceCase):
    def test_a_draft_line_cut_below_a_call_off_written_at_once_by_the_api(self):
        """O36 through the API: PATCH the line's quantity as a call-off is written."""
        from django.contrib.auth.models import User
        from django.test import Client

        from apps.sales import tests_call_offs
        from apps.sales.call_offs import CallOff, called_off
        from apps.sales.models import SalesOrder, SalesOrderLine

        made = fixture(self, tests_call_offs.CallOffTestCase)
        draft = SalesOrder.objects.create(customer=made.customer, order_date=tests_call_offs.DAY, currency=made.usd)
        line = SalesOrderLine.objects.create(
            order=draft, item=made.item, uom=made.uom, quantity=Decimal("100"), unit_price=Decimal("10"),
            revenue_account=made.revenue, warehouse=made.warehouse, delivery_date=tests_call_offs.day(90))
        for n, quantity in ((10, "25"), (20, "25"), (30, "20")):
            CallOff.objects.create(line=line, due_on=tests_call_offs.day(n), quantity=Decimal(quantity))
        user = User.objects.create_superuser("boss2", "b@x.in", "pw")
        client = Client(raise_request_exception=False)
        staff_session(client, user)
        answers = []

        def patch():
            answers.append(client.patch(f"/api/sales/sales-order-lines/{line.pk}/", {"quantity": "80"},
                                        content_type="application/json").status_code)

        outcomes = race(CallOff, patch, lambda: CallOff.objects.create(
            line_id=line.pk, due_on=tests_call_offs.day(40), quantity=Decimal("20")), atomic=False)
        row = SalesOrderLine.objects.get(pk=line.pk)
        print("APICUT", outcomes, answers, row.quantity, called_off(row))
        self.assertFalse(called_off(row) > row.quantity, f"{outcomes} {answers}: {called_off(row)} called off a line of {row.quantity}")


@tag("race")
@pg
class MoreBankProbe(RaceCase):
    books = R.LedgerRaceTests.books

    def test_NOT_A_FINDING_a_charge_posted_through_a_bank_unmarked_as_it_posts(self):
        # Serial order [unmark, post_to] also posts: post_to never asks refuse_as_money_account. Kept for the record.
        self.skipTest('serial-equivalent: post_to has no money-account rule')
        from apps.accounting.models import Account, AccountType, BankStatement, BankStatementLine

        self.books()
        fees = Account.objects.create(code="6100", name="Bank charges", account_type=AccountType.EXPENSE)
        statement = BankStatement.objects.create(
            bank_account=self.bank, start_date=datetime.date(2026, 9, 1), end_date=datetime.date(2026, 9, 30),
            opening_balance=Decimal("0"), closing_balance=Decimal("-50"))
        line = BankStatementLine.objects.create(statement=statement, date=datetime.date(2026, 9, 2),
                                                amount=Decimal("-50"))

        def unmark():
            account = Account.objects.get(pk=self.bank.pk)
            account.holds_money = False
            account.save()

        outcomes = race((Account, JournalEntry), unmark,
                        lambda: BankStatementLine.objects.get(pk=line.pk).post_to(fees), atomic=False)
        self.bank.refresh_from_db()
        posted = JournalLine.objects.filter(account=self.bank, entry__posted=True).exists()
        print("POSTTO", outcomes)
        self.assertFalse(posted and not self.bank.holds_money, f"{outcomes}: a charge posted through an account no longer money")

    def test_a_payment_is_not_voided_as_it_is_allocated(self):
        from apps.sales.models import Invoice, InvoiceLine, InvoicePayment

        self.books()
        from apps.accounting.models import Account, AccountType
        revenue = Account.objects.create(code="4000", name="Revenue", account_type=AccountType.INCOME)
        invoice = Invoice.objects.create(customer=self.party, invoice_date=datetime.date(2026, 9, 1),
                                         currency=self.inr, receivable_account=self.ar)
        InvoiceLine.objects.create(invoice=invoice, description="x", quantity=Decimal("1"),
                                   unit_price=Decimal("1000"), revenue_account=revenue)
        invoice.post()
        receipt = Payment.objects.create(
            party=self.party, direction=PaymentDirection.RECEIPT, payment_date=datetime.date(2026, 9, 2),
            amount=Decimal("1000"), currency=self.inr, bank_account=self.bank, counterpart_account=self.ar)
        receipt.post()
        outcomes = race((InvoicePayment, JournalEntry, Payment),
                        lambda: Payment.objects.get(pk=receipt.pk).void(on_date=datetime.date(2026, 9, 3)),
                        lambda: InvoicePayment.objects.create(
                            invoice=Invoice.objects.get(pk=invoice.pk), payment=Payment.objects.get(pk=receipt.pk),
                            amount=Decimal("1000")))
        print("VOIDALLOC", outcomes)
        self.assertFalse(any("deadlock" in o for o in outcomes), outcomes)
        receipt.refresh_from_db()
        # Allocate, then void, is a serial order: the void keeps the allocation as history and every balance
        # stops counting it (Payment.void). What must hold is the invoice owing what nothing settles.
        owed = Invoice.objects.get(pk=invoice.pk).amount_due()
        self.assertEqual(owed, Decimal("1000.00") if receipt.is_voided() else Decimal("0.00"),
                         f"{outcomes}: the invoice reads {owed} owed")


@tag("race")
@pg
class RequisitionProbe(RaceCase):
    """O108 mirrors: two direct orders at once on one request line."""

    setUp = R.PurchasingRaceTests.setUp

    def test_two_direct_orders_of_one_request_do_not_both_order_it(self):
        from django.contrib.auth import get_user_model

        from apps.core.models import Party, PartyRole, PartyRoleAssignment
        from apps.purchasing.models import PurchaseRequisition, PurchaseRequisitionLine

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
        second = Party.objects.create(code="V-2", name="Another supplier", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=second, role=PartyRole.VENDOR)
        from apps.purchasing.models import PurchaseOrder

        outcomes = race(PurchaseOrder,
                        lambda: PurchaseRequisition.objects.get(pk=requisition.pk).create_order(
                            self.vendor, lines=[PurchaseRequisitionLine.objects.get(pk=line.pk)]),
                        lambda: PurchaseRequisition.objects.get(pk=requisition.pk).create_order(
                            second, lines=[PurchaseRequisitionLine.objects.get(pk=line.pk)]))
        print("TWOORDERS", outcomes, line.quantity_ordered())
        self.once(outcomes)
        self.assertEqual(line.quantity_ordered(), Decimal("10"), outcomes)


@tag("race")
@pg
class ChallansProbe(RaceCase):
    def test_two_challans_for_one_month_do_not_both_pay_it(self):
        from apps.purchasing.tds import TdsChallan

        books = fixture(self, tds_fixture.TdsTestCase)
        books.bill("35000", vendor=books.contractor(pan="AAACL1234F")).deduct_tds()

        def pay(serial):
            return lambda: TdsChallan.pay(books.contract, datetime.date(2026, 6, 1), datetime.date(2026, 7, 7),
                                          books.bank, serial, "0510002")

        outcomes = race(JournalEntry, pay("00001"), pay("00002"))
        print("TWOCHALLANS", outcomes, books.balance(books.tds_payable))
        self.once(outcomes)
        self.assertEqual(books.balance(books.tds_payable), Decimal("0"))
