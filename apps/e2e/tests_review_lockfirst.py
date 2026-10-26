"""
review_lockfirst: probes of c5b6fae (the numbering row is the first lock of every transaction that locks).
PostgreSQL only. Each test asserts what SHOULD be true; a FAIL on the tip is a finding.
Run: DATABASE_URL=postgres://erp:erp@localhost:5432/review_lockfirst PYTHONPATH=scripts/gate \
  nice -n 19 .venv/bin/python manage.py test apps.e2e.tests_review_lockfirst --settings=fastsettings --parallel 1 --keepdb
"""

import datetime
import threading
import time
import unittest
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import DatabaseError, connection, transaction
from django.db.models.signals import pre_save
from django.test import Client, TransactionTestCase

from apps.core.models import DocumentNumbering, lock_rows
from apps.e2e.tests_races import fixture
from apps.sales import tests_base as sales_fixture

HOLD = 4  # seconds a long posting holds the numbering row


def numbering_is_free():
    """From a connection of its own: can the numbering row be taken at once?"""
    answer = []

    def ask():
        try:
            with transaction.atomic():
                DocumentNumbering.objects.select_for_update(nowait=True).get(pk=1)
            answer.append(True)
        except DatabaseError:
            answer.append(False)
        finally:
            connection.close()

    thread = threading.Thread(target=ask)
    thread.start()
    thread.join(10)
    return answer[0]


class Holder:
    """A long posting: a transaction that takes the numbering row and keeps it for `seconds`."""

    def __init__(self, seconds=HOLD):
        self.seconds, self.started = seconds, threading.Event()
        self.thread = threading.Thread(target=self.run_it)

    def run_it(self):
        try:
            with transaction.atomic():
                DocumentNumbering.hold()
                self.started.set()
                time.sleep(self.seconds)
        finally:
            connection.close()

    def __enter__(self):
        self.thread.start()
        assert self.started.wait(10)
        return self

    def __exit__(self, *exc):
        self.thread.join(30)


def timed(call):
    began = time.monotonic()
    result = call()
    return time.monotonic() - began, result


@unittest.skipUnless(connection.vendor == "postgresql", "needs PostgreSQL")
class SignInAndReadWaitOnNothing(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user("pat", password="pw-12345-xyz", is_superuser=True, is_staff=True)

    def test_p2a_an_ordinary_get_does_not_wait(self):
        from apps.core.tests_second_factor import staff_session

        c = staff_session(Client(), self.user)
        with Holder():
            took, response = timed(lambda: c.get("/api/inventory/items/"))
        print(f"\nP2a GET behind a {HOLD}s hold: {took:.2f}s status {response.status_code}")
        self.assertEqual(response.status_code, 200)
        self.assertLess(took, 1.5)

    def test_p2b_a_sign_in_with_a_session_already_open_does_not_wait(self):
        c = Client()
        session = c.session
        session["seen"] = 1
        session.save()
        c.cookies["sessionid"] = session.session_key
        with Holder():
            took, response = timed(lambda: c.post("/accounts/login/", {"username": "pat", "password": "pw-12345-xyz"}))
        print(f"\nP2b sign-in (open session) behind a {HOLD}s hold: {took:.2f}s status {response.status_code}")
        self.assertLess(took, 1.5)

    def test_p2c_a_fresh_sign_in_does_not_wait(self):
        c = Client()
        with Holder():
            took, response = timed(lambda: c.post("/accounts/login/", {"username": "pat", "password": "pw-12345-xyz"}))
        print(f"\nP2c sign-in (no session) behind a {HOLD}s hold: {took:.2f}s status {response.status_code}")
        self.assertLess(took, 1.5)

    def test_p2d_a_sign_out_does_not_wait(self):
        c = Client()
        c.force_login(self.user)
        with Holder():
            took, response = timed(lambda: c.post("/accounts/logout/"))
        print(f"\nP2d sign-out behind a {HOLD}s hold: {took:.2f}s status {response.status_code}")
        self.assertLess(took, 1.5)

    def test_p2e_a_session_delete_by_the_password_change_does_not_wait(self):
        from apps.core.second_factor import end_sessions

        Client().force_login(self.user)
        with Holder():
            took, _ = timed(lambda: end_sessions(self.user))
        print(f"\nP2e end_sessions behind a {HOLD}s hold: {took:.2f}s")
        self.assertLess(took, 1.5)


@unittest.skipUnless(connection.vendor == "postgresql", "needs PostgreSQL")
class TheHoldAcrossSavepoints(TransactionTestCase):
    setUp = sales_fixture.SalesTestCase.setUp

    def test_p3a_taken_again_after_a_savepoint_that_took_it_rolls_back(self):
        class Boom(Exception):
            pass

        with transaction.atomic():
            try:
                with transaction.atomic():
                    DocumentNumbering.hold()
                    raise Boom
            except Boom:
                pass
            free_after_rollback = numbering_is_free()  # nothing is held: the savepoint took it
            lock_rows(self.customer)  # a row lock: the numbering row first, again
            free_after_a_row = numbering_is_free()
        print(f"\nP3a free after rollback {free_after_rollback}; free after a row lock {free_after_a_row}")
        self.assertFalse(free_after_a_row)

    def test_p3b_kept_when_taken_outside_the_savepoint(self):
        class Boom(Exception):
            pass

        with transaction.atomic():
            DocumentNumbering.hold()
            try:
                with transaction.atomic():
                    lock_rows(self.customer)
                    raise Boom
            except Boom:
                pass
            held = not numbering_is_free()
        self.assertTrue(held)

    def test_p3c_a_save_inside_a_rolled_back_savepoint_then_a_save_outside(self):
        """The viewset mixin's per-write savepoint: a refused write, then the next one in the same request."""
        class Boom(Exception):
            pass

        with transaction.atomic():
            try:
                with transaction.atomic():
                    self.customer.name = "A"
                    self.customer.save()
                    raise Boom
            except Boom:
                pass
            self.item.name = "B"
            self.item.save()
            held = not numbering_is_free()
        self.assertTrue(held)


@unittest.skipUnless(connection.vendor == "postgresql", "needs PostgreSQL")
class RowsLockedWithoutTheNumberingRow(TransactionTestCase):
    setUp = sales_fixture.SalesTestCase.setUp

    def test_p1a_an_insert_naming_a_row_then_a_delete_of_that_row_by_a_second_transaction(self):
        """
        An INSERT takes FOR KEY SHARE on the row it names, a row lock the hold does not see. A: inserts a lot
        of an item (the importer's opening stock does), then posts (numbering). B: deletes the item, taking the
        numbering row first, then waits for A's key share. A waits for the numbering row: a deadlock the first
        lock rule was meant to rule out. Before c5b6fae B held nothing while it waited.
        """
        from apps.inventory.models import Item
        from apps.inventory.tracking import Lot

        spare = Item.objects.create(sku="SPARE-1", name="Spare", uom=self.uom, sale_price=Decimal("1"))
        inserted, outcomes = threading.Event(), {}

        def a():
            try:
                with transaction.atomic():
                    Lot.objects.create(item=spare, code="L-1")
                    inserted.set()
                    time.sleep(1.5)  # B reaches the numbering row and its delete
                    DocumentNumbering.hold()  # what any posting does next
                outcomes["a"] = "done"
            except Exception as exc:
                outcomes["a"] = f"refused: {exc}"
            finally:
                connection.close()

        def b():
            try:
                assert inserted.wait(10)
                with transaction.atomic():
                    Item.objects.get(pk=spare.pk).delete()
                outcomes["b"] = "done"
            except Exception as exc:
                outcomes["b"] = f"refused: {exc}"
            finally:
                connection.close()

        threads = [threading.Thread(target=a), threading.Thread(target=b)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        print(f"\nP1a {outcomes}")
        self.assertFalse(any("deadlock" in v for v in outcomes.values()), outcomes)

    def test_p1b_a_model_built_with_its_key_and_saved_updates_a_row_with_no_hold(self):
        from apps.inventory.models import Warehouse

        with transaction.atomic():
            again = Warehouse.objects.get(pk=self.warehouse.pk)
            again._state.adding = True  # as a model built with its key is
            again.name = "Renamed"
            again.save()  # an UPDATE all the same
            free = numbering_is_free()
        print(f"\nP1b numbering free after a pk-built save: {free}")
        self.assertFalse(free)


@unittest.skipUnless(connection.vendor == "postgresql", "needs PostgreSQL")
class ALongPostingAndASecondUsersInvoice(TransactionTestCase):
    def test_p5_a_pay_run_post_and_an_invoice_post_at_once(self):
        from apps.accounting.models import Account, AccountType, JournalEntry
        from apps.core.models import Party
        from apps.hr import tests_pay_outputs as hr
        from apps.sales.models import Invoice, InvoiceLine

        made = fixture(self, hr.OutputsTestCase)
        people = [made.employee(code) for code in ("E1", "E2", "E3")]
        for person, amount in zip(people, ("12000", "8450.50", "3375")):
            made.pay(person, made.salary, amount)
        run = made.pay_run(*hr.JUNE, pay_date=hr.JUNE[1])
        run.calculate(employees=people)
        customer = Party.objects.create(code="C-LF", name="Late", default_currency=made.usd)
        from apps.core.models import PartyRole, PartyRoleAssignment

        PartyRoleAssignment.objects.create(party=customer, role=PartyRole.CUSTOMER)
        ar = Account.objects.create(code="1190", name="AR", account_type=AccountType.ASSET)
        revenue = Account.objects.create(code="4190", name="Rev", account_type=AccountType.INCOME)
        invoice = Invoice.objects.create(customer=customer, invoice_date=hr.JUNE[1], receivable_account=ar,
                                         currency=made.usd)
        InvoiceLine.objects.create(invoice=invoice, description="S", quantity=Decimal("1"),
                                   unit_price=Decimal("100"), revenue_account=revenue)
        long_one, out, mine = threading.Event(), {}, threading.local()

        def slow(**kwargs):
            if getattr(mine, "slow", False):
                mine.slow = False
                long_one.set()
                time.sleep(HOLD)

        pre_save.connect(slow, sender=JournalEntry, weak=False, dispatch_uid="slow-lf")

        def pay_run():
            mine.slow = True
            try:
                began = time.monotonic()
                type(run).objects.get(pk=run.pk).post()
                out["run"] = ("done", time.monotonic() - began)
            except Exception as exc:
                out["run"] = (f"refused: {exc}", 0)
            finally:
                connection.close()

        def sales():
            try:
                assert long_one.wait(30)
                began = time.monotonic()
                Invoice.objects.get(pk=invoice.pk).post()
                out["invoice"] = ("done", time.monotonic() - began)
            except Exception as exc:
                out["invoice"] = (f"refused: {exc}", 0)
            finally:
                connection.close()

        threads = [threading.Thread(target=pay_run), threading.Thread(target=sales)]
        try:
            for t in threads:
                t.start()
            for t in threads:
                t.join(90)
        finally:
            pre_save.disconnect(sender=JournalEntry, dispatch_uid="slow-lf")
        print(f"\nP5 {out}")
        self.assertEqual(out["run"][0], "done", out)
        self.assertEqual(out["invoice"][0], "done", out)
        invoice.refresh_from_db()
        self.assertTrue(invoice.posted)
        self.assertTrue(invoice.number)


@unittest.skipUnless(connection.vendor == "postgresql", "needs PostgreSQL")
class TheChangedPayrollRace(TransactionTestCase):
    """
    Item 4 by hand. The run: E1 12,000.00 + E2 8,450.50 + E3 3,375.00 = 23,825.50 net pay payable (credit). The file
    payment (Dr 23,825.50) pays all three. E2's transfer comes back: a receipt 8,450.50 (Cr), the slip owes again
    (8,450.50). An own payment 8,450.50 (Dr) is posted against net pay payable.
      void first:  receipt voided (Dr 8,450.50), the slip is paid by the file again; the own payment cannot pay
                   it: credit 23,825.50 - Dr 23,825.50 - Dr 8,450.50 = Dr 8,450.50. E2 holds 8,450.50 twice: true cash.
      pay first:   the own payment pays the slip (unpaid 0); credit 23,825.50 - Dr 23,825.50 + Cr 8,450.50 - Dr 8,450.50 = 0;
                   then the void of the receipt must be refused, else E2 is paid by the file and by the own payment.
    """

    def build(self):
        from apps.accounting.models import PaymentDirection
        from apps.hr import tests_pay_outputs
        from apps.hr.bank_file import SalaryTransferFile

        made = fixture(self, tests_pay_outputs.BankFileTests)
        for person in made.people:
            made.banked(person)
        written = SalaryTransferFile.prepare(made.paid_run)
        paid = made.money(made.people[0].party, "23825.50")
        written.mark_paid(paid)
        row = written.rows.get(payslip__employee=made.people[1])
        receipt = made.money(made.people[1].party, "8450.50", direction=PaymentDirection.RECEIPT)
        row.return_transfer(receipt)
        own = made.money(made.people[1].party, "8450.50")
        return made, receipt, own, made.paid_run.payslips.get(employee=made.people[1])

    def test_p4a_void_first(self):
        from apps.accounting.models import Payment
        from apps.hr.payroll import Payslip

        made, receipt, own, slip = self.build()
        Payment.objects.get(pk=receipt.pk).void(on_date=datetime.date(2026, 7, 1))
        with self.assertRaises(Exception) as caught:
            Payslip.objects.get(pk=slip.pk).pay(Payment.objects.get(pk=own.pk))
        print(f"\nP4a void first: pay refused {caught.exception}; unpaid {made.paid_run.unpaid_net()} npp {made.balance(made.net_pay)}")
        self.assertEqual(made.paid_run.unpaid_net(), Decimal("0"))
        self.assertEqual(abs(made.balance(made.net_pay)), Decimal("8450.50"))

    def test_p4b_pay_first(self):
        from apps.accounting.models import Payment
        from apps.hr.payroll import Payslip

        made, receipt, own, slip = self.build()
        Payslip.objects.get(pk=slip.pk).pay(Payment.objects.get(pk=own.pk))
        try:
            Payment.objects.get(pk=receipt.pk).void(on_date=datetime.date(2026, 7, 1))
            voided = "void done"
        except Exception as exc:
            voided = f"void refused: {exc}"
        print(f"\nP4b pay first: {voided}; unpaid {made.paid_run.unpaid_net()} npp {made.balance(made.net_pay)}")
        # Never both: E2 paid by the file again and by the own payment.
        self.assertTrue(voided.startswith("void refused") or abs(made.balance(made.net_pay)) == Decimal("0"), voided)
