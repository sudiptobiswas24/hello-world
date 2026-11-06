"""
Review probes for rules A and B (4b3cf73), sales side. Each states one
claim and fails with observed and expected figures.
"""

import datetime
import unittest
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db import connection

from apps.core.models import Party, PartyRole, PartyRoleAssignment

from .models import (
    ApprovalPolicy,
    CustomerProfile,
    Invoice,
    SalesOrder,
    SalesOrderLine,
    committed_balance,
)
from .tests_probe_audit import ApiCase, ProbeCase

DAY = datetime.date(2026, 3, 1)


class MoveALineBetweenOrdersProbe(ApiCase):
    """Rule A freezes a line's order only once something moved; until then a move asks nothing of either order."""

    def other_customer(self, limit=None):
        other = Party.objects.create(code="C-2", name="Other")
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.CUSTOMER)
        CustomerProfile.objects.create(party=other, credit_limit=limit)
        return other

    def confirmed(self, customer, quantity, price):
        order = SalesOrder.objects.create(customer=customer, order_date=DAY, currency=self.usd)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D(quantity),
                                      unit_price=D(price), revenue_account=self.revenue)
        order.confirm()
        return order

    def test_a_line_moved_onto_a_confirmed_order_asks_its_customers_limit(self):
        other = self.other_customer(limit=D("500"))
        theirs = self.confirmed(other, "1", "100")  # 100 of 500
        ours = self.make_order("10", "100")  # Acme, no limit
        response = self.as_("AR Manager").patch(f"/api/sales/sales-order-lines/{ours.lines.get().pk}/",
                                                {"order": theirs.pk}, format="json")
        self.assertGreaterEqual(
            response.status_code, 400,
            f"PATCH {response.status_code}: Other's confirmed order took Acme's 1,000 line; Other's exposure "
            f"{committed_balance(other)} against a limit of 500 (expected 1100.00 refused)")

    def test_a_line_moved_off_an_order_leaves_it_worth_its_deposits(self):
        ours = self.make_order("10", "100")
        ours.create_down_payment_invoice(self.ar, amount=D("800"), invoice_date=DAY).post()
        second = self.make_order("1", "100")
        response = self.as_("AR Manager").patch(f"/api/sales/sales-order-lines/{ours.lines.get().pk}/",
                                                {"order": second.pk}, format="json")
        ours = SalesOrder.objects.get(pk=ours.pk)
        self.assertGreaterEqual(
            response.status_code, 400,
            f"PATCH {response.status_code}: {ours.number} now worth {ours.total()} holds a deposit of "
            f"{ours.deposit_total()} (cutting the line to nothing is refused)")

    def test_a_confirmed_order_moved_to_another_customer_asks_their_limit(self):
        other = self.other_customer(limit=D("500"))
        ours = self.make_order("10", "100")  # confirmed for Acme, nothing shipped
        response = self.as_("AR Manager").patch(f"/api/sales/sales-orders/{ours.pk}/", {"customer": other.pk},
                                                format="json")
        self.assertGreaterEqual(
            response.status_code, 400,
            f"PATCH {response.status_code}: Other's exposure {committed_balance(other)} against a limit of 500")


class ApprovedOrderGrowthProbe(ProbeCase):
    """O72's decision: a confirmed order changed past the policy is refused. An approved one can then take nothing."""

    def test_an_approved_order_takes_one_more_of_its_compliant_line(self):
        from django.contrib.auth.models import User

        ApprovalPolicy.objects.create(code="STD", name="Standard", max_discount_percent=D("15"))
        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY, currency=self.usd)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("10"),
                                      unit_price=D("100"), discount_percent=D("20"), revenue_account=self.revenue)
        plain = SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("5"),
                                              unit_price=D("100"), revenue_account=self.revenue)
        order.approve(by=User.objects.create_user("approver"))
        order.confirm()
        plain = SalesOrderLine.objects.get(pk=plain.pk)
        plain.quantity = D("6")
        try:
            plain.save()
        except ValidationError as refused:
            self.fail(f"one more of an undiscounted line refused on an approved order: {refused.messages[0]}")


@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class LockOrderRaceProbes(__import__("apps.e2e.tests_races", fromlist=["RaceCase"]).RaceCase):
    """A customer return credits its invoice under the order's lock; a credit note now posts under the order's too."""

    from apps.sales import tests_base as _fixture

    setUp = _fixture.SalesTestCase.setUp
    make_order, ship, balance = (_fixture.SalesTestCase.make_order, _fixture.SalesTestCase.ship,
                                 _fixture.SalesTestCase.balance)

    def test_a_return_and_a_credit_note_on_one_invoice_do_not_deadlock(self):
        from apps.e2e.tests_races import race
        from apps.inventory.models import StockMovement

        order = self.make_order("10", "100")
        delivery = self.ship(order, "10")
        invoice = order.create_invoice(self.ar, invoice_date=DAY)
        invoice.post()
        delivery_line, invoice_line = delivery.lines.get().pk, invoice.lines.get().pk

        def give_back():
            from .models import Delivery, DeliveryLine

            Delivery.objects.get(pk=delivery.pk).create_return(
                quantities={DeliveryLine.objects.get(pk=delivery_line): D("3")})

        def credit():
            from .models import InvoiceLine

            Invoice.objects.get(pk=invoice.pk).create_credit_note(
                quantities={InvoiceLine.objects.get(pk=invoice_line): D("2")})

        outcomes = race((StockMovement, Invoice), give_back, credit)
        self.assertEqual(outcomes, ["done", "done"],
                         f"a return of 3 and a credit of 2 on an invoice of 10 both stand; AR {self.balance(self.ar)}")


class CreditsLineProbe(ApiCase):
    """Rule B checks the order line an invoice line names; the invoice line it credits is another pointer, unasked."""

    def test_an_invoice_line_credits_only_its_own_invoices_lines(self):
        other = Party.objects.create(code="C-2", name="Other")
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.CUSTOMER)
        acme = self.bill(self.make_order("10", "100"))  # Acme's invoice, 10 creditable
        client = self.as_("AR Manager")
        made = client.post("/api/sales/invoices/", {"customer": other.pk, "invoice_date": "2026-03-01",
                                                    "currency": self.usd.pk, "receivable_account": self.ar.pk},
                           format="json")
        line = client.post("/api/sales/invoice-lines/", {
            "invoice": made.data["id"], "credits_line": acme.lines.get().pk, "item": self.item.pk,
            "quantity": "10", "unit_price": "1.00", "revenue_account": self.revenue.pk}, format="json")
        posted = client.post(f"/api/sales/invoices/{made.data['id']}/post_invoice/", {}, format="json")
        if line.status_code >= 400 or posted.status_code >= 400:
            return
        try:
            Invoice.objects.get(pk=acme.pk).create_credit_note()
            refused = ""
        except ValidationError as exc:
            refused = exc.messages[0]
        self.fail(f"line {line.status_code}, post {posted.status_code}: Other's 10.00 sale counts as crediting "
                  f"Acme's line; Acme's line has {acme.lines.get().quantity_creditable()} left to credit "
                  f"(expected 10), and crediting Acme says: {refused!r}")
