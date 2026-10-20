"""Review probes for O292 / O293 (review_blockers). Not product tests."""
import datetime
from decimal import Decimal

from apps.core.models import Currency, ExchangeRate
from apps.core.tests_roles import RolesTestCase
from .models import ApprovalPolicy, Invoice, InvoicePolicy, SalesOrder, SalesOrderLine
from .tests_base import SalesTestCase


class Probes(RolesTestCase, SalesTestCase):
    def setUp(self):
        SalesTestCase.setUp(self)
        RolesTestCase.setUp(self)
        self.eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(currency=self.eur, rate=Decimal("90"), valid_from=datetime.date(2026, 1, 1))

    def eur_order(self, q, p):
        o = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=self.eur)
        SalesOrderLine.objects.create(order=o, item=self.item, uom=self.uom, quantity=Decimal(q), unit_price=Decimal(p), revenue_account=self.revenue)
        return o

    def test_api_eur_limit_and_floor(self):
        ApprovalPolicy.objects.create(code="P", name="L", max_order_value=Decimal("1000000"), min_margin_percent=Decimal("20"))
        o = self.eur_order("10", "5000")
        rep = self.as_("AR Manager")  # sees all; approve right checked via model for refusal
        from django.core.exceptions import ValidationError as VE
        try:
            o.confirm(); print("MODEL CONFIRM PASSED?!")
        except VE as e: print("model confirm refused:", e.messages)
        r = rep.post(f"/api/sales/sales-orders/{o.pk}/confirm/", {}, format="json")
        print("rep confirm big EUR:", r.status_code, r.content[:200])
        mgr = rep
        r = mgr.get(f"/api/sales/sales-orders/{o.pk}/approval/")
        print("approval:", r.status_code, r.content[:300])
        r = mgr.post(f"/api/sales/sales-orders/{o.pk}/approve/", {}, format="json")
        print("manager approve:", r.status_code, r.content[:100])
        r = rep.post(f"/api/sales/sales-orders/{o.pk}/confirm/", {}, format="json")
        print("rep confirm after approval:", r.status_code)
        # later: rate on the same date corrected upward, then rep adds a line
        ExchangeRate.objects.filter(currency=self.eur).update(rate=Decimal("100"))
        r = rep.get(f"/api/sales/sales-orders/{o.pk}/approval/")
        print("after rate correction approval status:", r.status_code, r.content[:260])
        # small cut
        line = o.lines.first()
        r = rep.patch(f"/api/sales/sales-order-lines/{line.pk}/", {"quantity": "9"}, format="json")
        print("cut a line after rate correction:", r.status_code, r.content[:200])

    def test_return_hand_then_ar_credit_then_posting(self):
        order = self.make_order(quantity="10", price="100")
        delivery = self.ship(order, 10)
        invoice = self.bill(order)
        hand = self.as_("Warehouse Staff")
        r = hand.post(f"/api/sales/deliveries/{delivery.pk}/customer_return/", {"quantities": {str(delivery.lines.first().pk): "4"}}, format="json")
        print("hand return 4:", r.status_code, r.content[:300])
        ar = self.as_("AR Manager")
        draft = Invoice.objects.filter(credits=invoice, posted=False).first()
        print("draft", draft and (draft.pk, draft.total()))
        # AR posts the draft over the API
        r = ar.post(f"/api/sales/invoices/{draft.pk}/post_invoice/", {}, format="json")
        print("AR posts draft:", r.status_code, r.content[:120])
        print("due", invoice.amount_due())
        # warehouse hand may not post the draft
        d2 = hand.post(f"/api/sales/deliveries/{delivery.pk}/customer_return/", {"quantities": {str(delivery.lines.first().pk): "6"}}, format="json")
        print("hand return 6:", d2.status_code)
        d3 = hand.post(f"/api/sales/deliveries/{delivery.pk}/customer_return/", {}, format="json")
        print("hand return again (all):", d3.status_code, d3.content[:200])
        d = Invoice.objects.filter(credits=invoice, posted=False).first()
        r = hand.post(f"/api/sales/invoices/{d.pk}/post_invoice/", {}, format="json")
        print("hand posts draft:", r.status_code)
        r = ar.delete(f"/api/sales/invoices/{d.pk}/")
        print("AR deletes draft:", r.status_code)
