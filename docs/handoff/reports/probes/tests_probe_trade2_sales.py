"""
Review probes, second trading review (63d2b34..88a8dd9). Each states one claim
and fails with the observed figures. SQLite probes first, one PostgreSQL race last.
"""

import datetime
import unittest
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import connection

from apps.core.models import Party, PartyRole, PartyRoleAssignment

from .models import (
    ApprovalPolicy, Delivery, DeliveryLine, Invoice, InvoiceLine, InvoicePolicy, SalesOrder, SalesOrderLine,
)
from .tests_base import SalesTestCase

DAY = datetime.date(2026, 3, 1)


def refused(call):
    try:
        call()
    except ValidationError as exc:
        return "; ".join(exc.messages)[:160]
    return ""


class CreditByValueProbes(SalesTestCase):
    def test_a_note_against_a_credit_note_is_refused(self):
        order = self.make_order("10", "100")
        invoice = self.bill(order)
        note = invoice.create_credit_note(quantities={invoice.lines.get(): D("5")})
        second = Invoice(customer=self.customer, invoice_date=DAY, currency=self.usd, receivable_account=self.ar,
                         credits=note)
        said = refused(second.save)
        if said:
            return
        line = InvoiceLine.objects.create(invoice=second, credits_line=note.lines.get(), item=self.item,
                                          quantity=D("5"), unit_price=D("100"), revenue_account=self.revenue)
        said = refused(lambda: Invoice.objects.get(pk=second.pk).post())
        self.assertTrue(said, f"a note against a credit note posted: AR {self.balance(self.ar)} (invoice 1000, "
                              f"credited 500 once, 500 again = 0 expected only if it is a real note)")

    def test_a_claim_then_the_whole_invoice_credited_is_over_credited(self):
        order = self.make_order("10", "100")
        invoice = self.bill(order)
        invoice.credit_claim(D("100"), "rate", on_date=DAY)
        said = refused(lambda: Invoice.objects.get(pk=invoice.pk).create_credit_note())
        self.assertTrue(said, f"claim 100 then a whole credit of the invoice both posted: AR "
                              f"{self.balance(self.ar)} on an invoice of 1000 (expected 100 left to credit)")

    def test_a_typed_note_at_ten_times_the_price_is_refused(self):
        order = self.make_order("10", "100")
        invoice = self.bill(order)
        note = Invoice.objects.create(customer=self.customer, invoice_date=DAY, currency=self.usd,
                                      receivable_account=self.ar, credits=invoice)
        InvoiceLine.objects.create(invoice=note, credits_line=invoice.lines.get(), item=self.item,
                                   quantity=D("10"), unit_price=D("1000"), revenue_account=self.revenue)
        said = refused(lambda: Invoice.objects.get(pk=note.pk).post())
        self.assertTrue(said, f"a note of 10,000 on an invoice of 1,000 posted: AR {self.balance(self.ar)}")


class ConfirmedOrderCorrections(SalesTestCase):
    def test_cancel_and_remake_with_a_deposit_held(self):
        order = self.make_order("10", "100")
        deposit = order.create_down_payment_invoice(self.ar, amount=D("800"), invoice_date=DAY)
        deposit.post()
        self.assertTrue(refused(lambda: SalesOrder.objects.get(pk=order.pk).cancel()), "cancel should wait for the deposit")
        Invoice.objects.get(pk=deposit.pk).create_credit_note()
        SalesOrder.objects.get(pk=order.pk).cancel()
        self.assertEqual(SalesOrder.objects.get(pk=order.pk).status, "cancelled")

    def test_cancel_with_a_paid_deposit(self):
        order = self.make_order("10", "100")
        deposit = order.create_down_payment_invoice(self.ar, amount=D("800"), invoice_date=DAY)
        deposit.post()
        self.allocate(self.receipt("800"), Invoice.objects.get(pk=deposit.pk), "800")
        Invoice.objects.get(pk=deposit.pk).create_credit_note()
        SalesOrder.objects.get(pk=order.pk).cancel()

    def test_a_cancelled_orders_line_still_moves_nowhere(self):
        order = self.make_order("10", "100")
        SalesOrder.objects.get(pk=order.pk).cancel()
        line = SalesOrderLine.objects.get(order=order)
        line.quantity = D("12")
        line.save()  # a typo on a cancelled line still saves

    def test_a_draft_orders_customer_changes_and_confirms(self):
        other = Party.objects.create(code="C-2", name="Other")
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.CUSTOMER)
        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY, currency=self.usd)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("1"), unit_price=D("1"),
                                      revenue_account=self.revenue)
        order.customer = other
        order.save()
        order.confirm()


class ApprovedFiguresProbes(SalesTestCase):
    def approved(self, drop_figures=False):
        ApprovalPolicy.objects.create(code="STD", name="Standard", max_discount_percent=D("15"))
        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY, currency=self.usd)
        line = SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("10"),
                                             unit_price=D("100"), discount_percent=D("20"),
                                             revenue_account=self.revenue)
        order.approve(by=User.objects.create_user("approver"))
        order.confirm()
        if drop_figures:  # an order approved before 0063
            SalesOrder.objects.filter(pk=order.pk).update(approved_figures=None)
        return SalesOrder.objects.get(pk=order.pk), line

    def cut_then_restore(self, drop_figures):
        order, line = self.approved(drop_figures)
        line = SalesOrderLine.objects.get(pk=line.pk)
        line.discount_percent = D("10")
        line.save()
        line = SalesOrderLine.objects.get(pk=line.pk)
        line.discount_percent = D("20")
        return refused(line.save)

    def test_cut_and_restore_within_its_approval(self):
        self.assertEqual(self.cut_then_restore(False), "")

    def test_cut_and_restore_on_an_order_approved_before_0063(self):
        said = self.cut_then_restore(True)
        self.assertEqual(said, "", f"approved at 20%, cut to 10% and put back: {said}")

    def test_changed_within_and_changed_again(self):
        order, line = self.approved()
        line = SalesOrderLine.objects.get(pk=line.pk)
        line.quantity = D("12")
        line.save()
        line = SalesOrderLine.objects.get(pk=line.pk)
        line.discount_percent = D("19")
        line.save()
        line = SalesOrderLine.objects.get(pk=line.pk)
        line.discount_percent = D("21")
        self.assertTrue(refused(line.save), "21% over an approval of 20% was not refused")

    def test_a_deleted_and_retyped_line_keeps_nothing_of_the_approval(self):
        order, line = self.approved()
        SalesOrderLine.objects.get(pk=line.pk).delete()
        retyped = SalesOrderLine(order=order, item=self.item, uom=self.uom, quantity=D("10"), unit_price=D("100"),
                                 discount_percent=D("20"), revenue_account=self.revenue)
        said = refused(retyped.save)
        self.assertEqual(said, "", f"the approved 20% line deleted and made again: {said}")


@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class TwoReturnsOnAConsolidatedInvoice(__import__("apps.e2e.tests_races", fromlist=["RaceCase"]).RaceCase):
    from apps.sales import tests_base as _fixture

    setUp = _fixture.SalesTestCase.setUp
    make_order, ship, balance = (_fixture.SalesTestCase.make_order, _fixture.SalesTestCase.ship,
                                 _fixture.SalesTestCase.balance)

    def consolidated(self):
        first, second = self.make_order("5", "100"), self.make_order("5", "100")
        deliveries = [self.ship(first, "5"), self.ship(second, "5")]
        invoice = Invoice.objects.create(customer=self.customer, invoice_date=DAY, currency=self.usd,
                                         receivable_account=self.ar)
        for order in (first, second):
            InvoiceLine.objects.create(invoice=invoice, order_line=order.lines.get(), quantity=D("5"),
                                       unit_price=D("100"), revenue_account=self.revenue)
        invoice.post()
        return deliveries, invoice

    def test_two_returns_on_two_orders_of_one_invoice_do_not_deadlock(self):
        from apps.e2e.tests_races import race
        from apps.inventory.models import StockMovement

        deliveries, invoice = self.consolidated()

        def give_back(pk):
            def call():
                delivery = Delivery.objects.get(pk=pk)
                delivery.create_return(quantities={delivery.lines.get(): D("2")})
            return call

        outcomes = race((StockMovement, Invoice), *[give_back(d.pk) for d in deliveries])
        self.assertEqual(outcomes, ["done", "done"], f"AR {self.balance(self.ar)}")


class TypedNoteWithoutItsOrderLine(SalesTestCase):
    def test_a_note_line_that_names_no_order_line_frees_the_order_line(self):
        order = self.make_order("10", "100")
        invoice = self.bill(order)
        note = Invoice.objects.create(customer=self.customer, invoice_date=DAY, currency=self.usd,
                                      receivable_account=self.ar, credits=invoice)
        InvoiceLine.objects.create(invoice=note, credits_line=invoice.lines.get(), item=self.item,
                                   quantity=D("10"), unit_price=D("100"), revenue_account=self.revenue)
        said = refused(lambda: Invoice.objects.get(pk=note.pk).post())
        line = SalesOrderLine.objects.get(order=order)
        self.assertTrue(said or line.quantity_invoiced() == 0,
                        f"credited in full, yet the order line still reads invoiced {line.quantity_invoiced()} "
                        f"(AR {self.balance(self.ar)})")
