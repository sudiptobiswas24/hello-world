"""
Audit probes, sales (9 October). Each test states one claim about money or
state and fails with the observed and the expected figures. Expected
figures were worked in a separate plain-Python script first.
"""

import datetime
import unittest
from decimal import Decimal as D

from django.core import mail
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import override_settings

from apps.accounting.models import Payment, PaymentDirection
from apps.core.models import (
    Currency,
    ExchangeRate,
    Party,
    PartyRole,
    PartyRoleAssignment,
    PaymentTerms,
    PaymentTermsLine,
    UnitOfMeasure,
)

from .models import (
    ApprovalPolicy,
    CommissionBasis,
    CommissionPlan,
    CustomerProfile,
    Delivery,
    DeliveryLine,
    DunningLevel,
    Invoice,
    InvoiceLine,
    InvoicePolicy,
    SalesOrder,
    SalesOrderLine,
    SalesRep,
    commission_report,
    outstanding_balance,
    run_dunning,
)
from .tests_base import SalesTestCase

DAY = datetime.date(2026, 3, 1)


class ProbeCase(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.1"), valid_from=datetime.date(2026, 1, 1))

    def receipt_in(self, amount, currency, on=datetime.date(2026, 3, 10)):
        payment = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.RECEIPT, payment_date=on,
            amount=D(amount), currency=currency, bank_account=self.bank, counterpart_account=self.ar)
        payment.post()
        return payment


# -- credit notes ----------------------------------------------------------

class ClaimThenCreditProbe(ProbeCase):
    """A claim gives money back on a line; a quantity credit after it gives back the full price again."""

    def test_a_claim_and_a_full_credit_give_back_no_more_than_was_invoiced(self):
        invoice = self.bill(self.make_order("10", "100"))  # 1000.00
        invoice.credit_claim(D("300"), "rate", on_date=DAY)
        try:
            invoice.create_credit_note()
        except ValidationError:
            return  # refused: what the claim already gave back is weighed
        invoice.refresh_from_db()
        credited = invoice.amount_credited()
        self.assertLessEqual(
            credited, D("1000.00"),
            f"credited {credited} on an invoice of 1000.00 (claim 300 + quantity credit 1000); "
            f"customer balance {outstanding_balance(self.customer)}, expected 0.00")

    def test_goods_returned_after_a_claim_are_credited_at_what_was_charged(self):
        order = self.make_order("10", "100")
        delivery = self.ship(order, "10")
        invoice = self.bill(order)  # 1000.00, unpaid
        invoice.credit_claim(D("300"), "torn", on_date=DAY)
        delivery.create_return()  # all 10 back, credited
        invoice.refresh_from_db()
        self.assertEqual(
            (invoice.amount_credited(), outstanding_balance(self.customer), self.balance(self.ar)),
            (D("1000.00"), D("0.00"), D("0.00")),
            "observed (credited, customer balance, AR ledger); expected credited 1000.00 "
            "(300 claim + 700 for the goods) and both balances 0.00")


# -- invoice lines pointing elsewhere ----------------------------------------

class InvoiceLineOrderLineProbe(ProbeCase):
    """A delivery line must be for its own order; an invoice line is not asked."""

    def test_an_invoice_line_cannot_bill_another_customers_order_line(self):
        other = Party.objects.create(code="C-2", name="Other")
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.CUSTOMER)
        order = self.make_order("10", "100")  # Acme's
        invoice = Invoice.objects.create(customer=other, invoice_date=DAY, receivable_account=self.ar,
                                         currency=self.usd)
        try:
            InvoiceLine.objects.create(invoice=invoice, order_line=order.lines.get(), item=self.item,
                                       quantity=D("10"), unit_price=D("1"), revenue_account=self.revenue)
            invoice.post()
        except ValidationError:
            return
        line = order.lines.get()
        self.assertEqual(
            (line.quantity_invoiced(), order.invoice_status()), (D("0"), "none"),
            f"Other's invoice of 10.00 marked Acme's order line invoiced {line.quantity_invoiced()} "
            f"({order.invoice_status()}); Acme was billed nothing")


# -- delivered policy, invoiced twice ----------------------------------------

class DeliveredPolicyProbe(ProbeCase):
    """A 'delivered' order bills only what shipped: two drafts each for the same shipment."""

    def test_one_shipment_is_not_invoiced_twice(self):
        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        self.ship(order, "5")
        first = order.create_invoice(self.ar, invoice_date=DAY)
        second = order.create_invoice(self.ar, invoice_date=DAY)  # the first is still a draft
        first.post()
        try:
            second.post()
        except ValidationError:
            return
        line = order.lines.get()
        self.assertLessEqual(
            line.quantity_invoiced(), line.quantity_shipped(),
            f"invoiced {line.quantity_invoiced()} against {line.quantity_shipped()} shipped on a "
            f"bill-on-delivery order; AR {self.balance(self.ar)}, expected 500.00")


# -- approval on a confirmed order -------------------------------------------

class ConfirmedOrderPolicyProbe(ProbeCase):
    """The credit limit is asked again when a confirmed order changes; the discount policy is not."""

    def setUp(self):
        super().setUp()
        ApprovalPolicy.objects.create(code="STD", name="Standard", max_discount_percent=D("15"))

    def test_a_confirmed_line_is_not_discounted_past_the_policy(self):
        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY, currency=self.usd)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("10"),
                                      unit_price=D("100"), discount_percent=D("10"),
                                      revenue_account=self.revenue)
        order.confirm()  # 10% is within 15%: no approval needed
        line = order.lines.get()
        line.discount_percent = D("60")
        try:
            line.save()
        except ValidationError:
            return
        self.ship(order, "10")
        invoice = self.bill(order)
        self.fail(f"discount raised to 60% on a confirmed order, shipped and invoiced at "
                  f"{invoice.subtotal()} with no approval; the policy allows 15% (900.00 at 10%)")

    def test_a_line_added_to_a_confirmed_order_asks_the_policy(self):
        order = self.make_order("1", "100")
        try:
            SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("10"),
                                          unit_price=D("100"), discount_percent=D("60"),
                                          revenue_account=self.revenue)
        except ValidationError:
            return
        order.refresh_from_db()
        self.fail(f"a 60% line added to confirmed {order.number} stands; approval reasons now "
                  f"{order.approval_reasons()}, status {order.status}, nothing refused")


# -- commission ----------------------------------------------------------

class CommissionProbe(ProbeCase):
    def plan(self, basis):
        plan = CommissionPlan.objects.create(code="P", name="Ten", percent=D("10"), basis=basis)
        SalesRep.objects.create(party=self.rep, plan=plan)

    def commission(self):
        rows = commission_report()
        return (rows[0]["basis_amount"], rows[0]["commission"]) if rows else (D("0.00"), D("0.00"))

    def test_a_bounced_cheque_earns_no_commission_on_collection(self):
        self.plan(CommissionBasis.PAID)
        invoice = self.bill(self.make_order("10", "100", rep=self.rep))
        payment = self.receipt("1000")
        self.allocate(payment, invoice, "1000")
        payment.void()
        self.assertEqual(self.commission(), (D("0.00"), D("0.00")),
                         "observed (basis, commission) after the receipt was voided")

    def test_money_taken_up_front_counts_as_collected(self):
        self.plan(CommissionBasis.PAID)
        order = self.make_order("10", "100", rep=self.rep)
        deposit = order.create_down_payment_invoice(self.ar, percent=30, invoice_date=DAY)
        deposit.post()
        self.allocate(self.receipt("300"), deposit, "300")
        invoice = self.bill(order)  # draws the 300 down
        self.assertEqual(invoice.amount_deposited(), D("300.00"))
        self.allocate(self.receipt("700"), invoice, "700")
        self.assertEqual(self.commission(), (D("1000.00"), D("100.00")),
                         "observed (basis, commission): 1000 collected, 300 of it up front")

    def test_commission_is_in_the_base_currency(self):
        self.plan(CommissionBasis.INVOICED)
        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY, currency=self.eur,
                                          sales_rep=self.rep)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("10"),
                                      unit_price=D("100"), revenue_account=self.revenue)
        order.confirm()
        invoice = self.bill(order)
        self.assertEqual(invoice.exchange_rate, D("1.1"))
        self.assertEqual(self.commission(), (D("1100.00"), D("110.00")),
                         "observed (basis, commission) for 1000 EUR at 1.1")


# -- dunning ----------------------------------------------------------------

@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class DunningProbe(ProbeCase):
    def test_the_reminder_asks_for_what_is_late(self):
        split = PaymentTerms.objects.create(code="5050", name="50/50", net_days=30)
        PaymentTermsLine.objects.create(terms=split, sequence=1, percent=D("50"), days=0)
        PaymentTermsLine.objects.create(terms=split, sequence=2, percent=D("50"), days=30)
        self.customer.payment_terms = split
        self.customer.save()
        DunningLevel.objects.create(name="First", days_overdue=7)
        invoice = self.bill(self.make_order("10", "100"), on=DAY)

        notices = run_dunning(as_of=datetime.date(2026, 3, 15), send=True)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].amount_due, D("500.00"))
        body = mail.outbox[0].body
        self.assertTrue("500.00" in body and "01 Mar 2026" in body,
                        f"notice records 500.00 overdue since 01 Mar 2026; the mail says: {body!r}")


# -- pricing ------------------------------------------------------------------

class PricingProbe(ProbeCase):
    def test_an_order_in_another_currency_is_not_priced_at_the_items_base_price(self):
        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY, currency=self.eur)
        try:
            line = SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom,
                                                 quantity=D("1"), revenue_account=self.revenue)
        except ValidationError:
            return
        self.assertNotEqual(line.unit_price, D("10"),
                            "a EUR line priced 10 from the item's 10 USD list price; 10 USD is 9.09 EUR")

    def test_a_line_in_boxes_is_priced_per_box(self):
        box = UnitOfMeasure.objects.create(code="box", name="Box of 12", base_unit=self.uom,
                                           conversion_factor=D("12"))
        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY, currency=self.usd)
        line = SalesOrderLine.objects.create(order=order, item=self.item, uom=box, quantity=D("2"),
                                             revenue_account=self.revenue)
        self.assertEqual((line.unit_price, line.net_amount()), (D("120"), D("240.00")),
                         "observed (unit price per box, line net) at 10 an each, 12 to a box")


# -- credit limit ---------------------------------------------------------------

class CreditLimitProbe(ProbeCase):
    def test_exposure_in_two_currencies_is_weighed_in_one(self):
        CustomerProfile.objects.create(party=self.customer, credit_limit=D("1000"))
        self.make_order("6", "100")  # 600 USD, confirmed
        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY, currency=self.eur)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("1"),
                                      unit_price=D("380"), revenue_account=self.revenue)
        self.assertTrue(order.approval_reasons(),
                        f"600 USD + 380 EUR (418 USD) is 1018 USD against a limit of 1000: "
                        f"breach read as {order.credit_limit_breach()}")


# -- the cycle in part ----------------------------------------------------------

class PartialCycleProbe(ProbeCase):
    """Ship half, invoice it, credit three, return one: the remainder and the ledger agree."""

    def test_the_remainder_and_the_receivable(self):
        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        delivery = self.ship(order, "5")
        invoice = self.bill(order)
        self.allocate(self.receipt("500"), invoice, "500")
        invoice.create_credit_note(quantities={invoice.lines.get(): D("3")})
        delivery.create_return(quantities={delivery.lines.get(): D("1")})
        line = SalesOrderLine.objects.get(pk=order.lines.get().pk)
        self.assertEqual(
            (line.quantity_shipped(), line.quantity_invoiced(), line.quantity_invoiceable(),
             line.quantity_open()),
            (D("4"), D("1"), D("3"), D("6")))
        self.assertEqual((outstanding_balance(self.customer), self.balance(self.ar)),
                         (D("-400.00"), D("-400.00")))
        self.assertEqual(self.balance(self.cogs), D("16.00"))


# -- two at once (PostgreSQL) -----------------------------------------------------

@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class SalesRaceProbes(__import__("apps.e2e.tests_races", fromlist=["RaceCase"]).RaceCase):
    from apps.sales import tests_base as _fixture

    setUp = _fixture.SalesTestCase.setUp
    make_order, ship, balance = (_fixture.SalesTestCase.make_order, _fixture.SalesTestCase.ship,
                                 _fixture.SalesTestCase.balance)

    def test_two_deliveries_do_not_both_ship_the_last_of_a_line(self):
        from apps.e2e.tests_races import race
        from apps.inventory.models import StockMovement

        order = self.make_order("10", "100")
        self.ship(order, "6")
        drafts = []
        for _ in range(2):
            delivery = Delivery.objects.create(sales_order=order, delivery_date=DAY)
            DeliveryLine.objects.create(delivery=delivery, order_line=order.lines.get(),
                                        warehouse=self.warehouse, quantity_shipped=D("4"))
            drafts.append(delivery.pk)
        outcomes = race(StockMovement, *[lambda pk=pk: Delivery.objects.get(pk=pk).post() for pk in drafts])
        self.assertEqual(sorted(o == "done" for o in outcomes), [False, True], outcomes)
        self.assertEqual(order.lines.get().quantity_shipped(), D("10"))

    def test_two_invoices_do_not_both_bill_one_delivery(self):
        from apps.e2e.tests_races import race
        from apps.accounting.models import JournalEntry

        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        self.ship(order, "10")
        drafts = [order.create_invoice(self.ar, invoice_date=DAY).pk for _ in range(2)]
        outcomes = race(JournalEntry, *[lambda pk=pk: Invoice.objects.get(pk=pk).post() for pk in drafts])
        self.assertEqual(sorted(o == "done" for o in outcomes), [False, True], outcomes)
        self.assertEqual(self.balance(self.ar), D("1000.00"))


# -- a deposit in another currency, drawn, credited back in part, refunded -------

class ForeignDepositCycleProbe(ProbeCase):
    def test_deposits_and_receivables_clear_and_the_gap_is_exchange(self):
        from apps.accounting.models import Account, AccountType
        from apps.core.models import Company
        from .models import InvoicePayment

        loss = Account.objects.create(code="7100", name="FX loss", account_type=AccountType.EXPENSE)
        gain = Account.objects.create(code="7000", name="FX gain", account_type=AccountType.INCOME)
        company = Company.get()
        company.fx_loss_account, company.fx_gain_account = loss, gain
        company.save()
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.2"), valid_from=datetime.date(2026, 3, 5))
        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY, currency=self.eur,
                                          invoice_policy=InvoicePolicy.DELIVERED)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("10"),
                                      unit_price=D("100"), revenue_account=self.revenue)
        order.confirm()
        deposit = order.create_down_payment_invoice(self.ar, amount=D("800"), invoice_date=DAY)
        deposit.post()
        self.allocate(self.receipt_in("800", self.eur, on=datetime.date(2026, 3, 2)), deposit, "800")
        self.ship(order, "6")
        invoice = self.bill(order, on=datetime.date(2026, 3, 6))  # 600 EUR at 1.2, draws 600 of the deposit
        note = Invoice.objects.get(pk=deposit.pk).create_credit_note(amount=D("200"))
        refund = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.DISBURSEMENT, payment_date=datetime.date(2026, 3, 10),
            amount=D("200"), currency=self.eur, bank_account=self.bank, counterpart_account=self.ar)
        refund.post()
        InvoicePayment.objects.create(invoice=note, payment=refund, amount=D("200"))
        deposit = Invoice.objects.get(pk=deposit.pk)
        self.assertEqual(
            (self.balance(self.deposits), self.balance(self.ar), deposit.deposit_unapplied(),
             Invoice.objects.get(pk=invoice.pk).amount_due(), Invoice.objects.get(pk=note.pk).amount_due(),
             outstanding_balance(self.customer), self.balance(loss) + self.balance(gain)),
            (D("0.00"), D("0.00"), D("0.00"), D("0.00"), D("0.00"), D("0.00"), D("80.00")),
            "observed (deposits, AR, left on deposit, due on invoice, due on note, customer balance, net FX loss)")


# -- through the API, as the people who do it ---------------------------------------

class ApiCase(ProbeCase):
    def setUp(self):
        super().setUp()
        from django.core.management import call_command

        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        from django.contrib.auth.models import Group, User
        from rest_framework.test import APIClient

        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client


class ApiProbe(ApiCase):
    def test_a_posted_delivery_keeps_its_lines(self):
        from apps.accounting.models import JournalLine

        order = self.make_order("10", "100")
        delivery = self.ship(order, "5")
        line = delivery.lines.get()
        response = self.as_("Warehouse Staff").delete(f"/api/sales/delivery-lines/{line.pk}/")
        order_line = order.lines.get()
        self.assertEqual(
            (response.status_code >= 400, order_line.quantity_shipped(), self.item.on_hand_at(self.warehouse)),
            (True, D("5"), D("495")),
            f"DELETE answered {response.status_code}; the posted delivery now has "
            f"{Delivery.objects.get(pk=delivery.pk).lines.count()} lines, its COGS entry still stands "
            f"({self.balance(self.cogs)}), and the order line reads {order_line.quantity_shipped()} shipped "
            f"with {self.item.on_hand_at(self.warehouse)} on the shelf")

    def test_an_invoice_line_bills_only_its_own_invoices_order(self):
        other = Party.objects.create(code="C-2", name="Other")
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.CUSTOMER)
        order = self.make_order("10", "100")
        client = self.as_("AR Manager")
        made = client.post("/api/sales/invoices/", {"customer": other.pk, "invoice_date": "2026-03-01",
                                                    "currency": self.usd.pk, "receivable_account": self.ar.pk}, format="json")
        self.assertEqual(made.status_code, 201, made.data)
        line = client.post("/api/sales/invoice-lines/", {
            "invoice": made.data["id"], "order_line": order.lines.get().pk, "item": self.item.pk,
            "quantity": "10", "unit_price": "1.00", "revenue_account": self.revenue.pk}, format="json")
        posted = client.post(f"/api/sales/invoices/{made.data['id']}/post_invoice/", {}, format="json")
        refused = line.status_code >= 400 or posted.status_code >= 400
        self.assertTrue(refused, f"line {line.status_code}, post {posted.status_code}: Acme's order line now "
                                 f"reads {order.lines.get().quantity_invoiced()} invoiced on Other's 10.00 invoice")


class EditAfterShippingProbe(ApiCase):
    """Confirming and shipping asked about the customer, the currency and the item; an edit asks nothing."""

    def test_a_shipped_lines_item_does_not_change(self):
        from apps.inventory.models import Item

        gadget = Item.objects.create(sku="GDG-1", name="Gadget", uom=self.uom, sale_price=D("10"))
        order = self.make_order("10", "100")
        delivery = self.ship(order, "5")  # five widgets out at 4.00
        line = order.lines.get()
        response = self.as_("AR Manager").patch(f"/api/sales/sales-order-lines/{line.pk}/",
                                                {"item": gadget.pk}, format="json")
        if response.status_code >= 400:
            return
        Delivery.objects.get(pk=delivery.pk).create_return(credit_invoices=False)
        self.fail(f"PATCH answered {response.status_code}; returning the five widgets put "
                  f"{gadget.on_hand_at(self.warehouse)} gadgets on the shelf (none ever existed) and "
                  f"{self.item.on_hand_at(self.warehouse)} widgets (expected 0 gadgets, 500 widgets)")

    def test_a_shipped_orders_customer_and_currency_do_not_change(self):
        other = Party.objects.create(code="C-2", name="Other")
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.CUSTOMER)
        CustomerProfile.objects.create(party=other, credit_hold=True, credit_hold_reason="Unpaid since May")
        order = self.make_order("10", "100")
        self.ship(order, "5")
        response = self.as_("AR Manager").patch(f"/api/sales/sales-orders/{order.pk}/",
                                                {"customer": other.pk, "currency": self.eur.pk}, format="json")
        if response.status_code >= 400:
            return
        invoice = SalesOrder.objects.get(pk=order.pk).create_invoice(self.ar, invoice_date=DAY)
        invoice.post()
        self.fail(f"PATCH answered {response.status_code}: Acme's shipped order now bills {invoice.customer} "
                  f"(on credit hold) {invoice.total()} {invoice.currency} for goods priced in USD")


class WriteOffRecoveryProbe(ProbeCase):
    """Written off 600, a credit note of 300 undoes half: the other 300, paid after all, must be recoverable."""

    def test_what_a_credit_note_left_written_off_can_be_recovered(self):
        from .models import ar_aging

        invoice = self.bill(self.make_order("10", "100"))  # 1000.00
        self.allocate(self.receipt("400"), invoice, "400")
        invoice.write_off(reason="Customer in liquidation", on_date=datetime.date(2026, 4, 1))
        invoice = Invoice.objects.get(pk=invoice.pk)
        invoice.create_credit_note(quantities={invoice.lines.get(): D("3")})  # 300 back
        self.assertEqual((self.balance(self.bad_debt), self.balance(self.ar)), (D("300.00"), D("0.00")))
        invoice = Invoice.objects.get(pk=invoice.pk)
        try:
            invoice.recover_write_off(invoice.write_offs.get(), on_date=datetime.date(2026, 5, 1))
        except ValidationError as refused:
            self.fail(f"the 300 still on bad debt cannot be recovered: {refused.messages[0]}")
        invoice = Invoice.objects.get(pk=invoice.pk)
        self.allocate(self.receipt("300", on=datetime.date(2026, 5, 2)), invoice, "300")
        aging = ar_aging(as_of=datetime.date(2026, 5, 31))
        self.assertEqual(
            (self.balance(self.bad_debt), self.balance(self.ar), sum(b["total"] for b in aging.values())),
            (D("0.00"), D("0.00"), D("0.00")), "observed (bad debt, AR, aging total)")


class ReopenProbe(ProbeCase):
    """Raising a confirmed line asks the credit limit; reopening one closed short owes as much again and does not."""

    def test_reopening_a_line_asks_the_credit_limit(self):
        from .models import committed_balance

        CustomerProfile.objects.create(party=self.customer, credit_limit=D("1000"))
        first = self.make_order("10", "100")  # 1000, at the limit
        self.ship(first, "2")
        line = first.lines.get()
        line.close_short("Customer wants no more")
        self.make_order("8", "100")  # 800: 200 + 800 = 1000, within
        line = SalesOrderLine.objects.get(pk=line.pk)
        try:
            line.reopen()
        except ValidationError:
            return
        self.fail(f"reopened with nothing asked: exposure {committed_balance(self.customer)} against a "
                  f"limit of 1000 (expected 1800.00 refused)")
