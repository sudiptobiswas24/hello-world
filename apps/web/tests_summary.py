"""
Any list, grouped: the list's own permission, scoping, narrowing and
figures, added up by group.
"""

from decimal import Decimal

from django.utils import timezone

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.sales.tests_screens_api import ScreensTestCase

SUMMARY = "/api/web/summary/"


class GroupedListTests(ScreensTestCase):
    def invoice(self, order_quantity="10", post=True):
        order = self.make_order(quantity=order_quantity)
        invoice = order.create_invoice(self.ar)
        if post:
            invoice.post()
        return invoice

    def test_invoices_by_customer_count_and_add_up_as_the_list_shows_them(self):
        self.invoice("10")
        self.invoice("3")
        manager = self.as_("AR Manager")
        shape = manager.get(SUMMARY, {"endpoint": "/api/sales/invoices/"}).json()
        self.assertIn({"key": "customer", "label": "Customer"}, shape["by"])
        self.assertIn({"key": "invoice_date:month", "label": "Invoice date, by month"}, shape["by"])
        grouped = manager.get(SUMMARY, {"endpoint": "/api/sales/invoices/", "by": "customer", "posted": "true"}).json()
        [row] = grouped["rows"]
        self.assertEqual((row["label"], row["count"], row["sums"]["total"], row["sums"]["amount_due"]),
                         ("Acme", 2, "1300.00", "1300.00"))
        self.assertIn({"key": "total", "label": "Total", "kind": "money"}, grouped["sums"])
        self.assertNotIn("id", row["sums"])

    def test_the_lists_narrowing_and_the_month_apply(self):
        self.invoice("10")
        self.invoice("1", post=False)
        manager = self.as_("AR Manager")
        drafts = manager.get(SUMMARY, {"endpoint": "/api/sales/invoices/", "by": "posted"}).json()["rows"]
        self.assertEqual([(row["label"], row["count"]) for row in drafts], [("No", 1), ("Yes", 1)])
        only_posted = manager.get(SUMMARY, {"endpoint": "/api/sales/invoices/", "by": "posted", "posted": "true"}).json()["rows"]
        self.assertEqual([(row["label"], row["count"]) for row in only_posted], [("Yes", 1)])
        months = manager.get(SUMMARY, {"endpoint": "/api/sales/invoices/", "by": "invoice_date:month"}).json()["rows"]
        self.assertEqual(len(months), 1)
        self.assertEqual(months[0]["label"], timezone.localdate().strftime("%Y-%m"))

    def test_a_rep_adds_up_only_the_customers_they_carry(self):
        self.invoice("10")
        rep = self.as_("Sales Rep")  # carries every customer there is now
        other = Party.objects.create(code="C-2", name="Other Mills", default_currency=self.usd,
                                     payment_terms=self.terms)
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.CUSTOMER)
        self.customer = other
        self.invoice("5")
        self.assertEqual(len(self.as_("AR Manager").get(SUMMARY, {"endpoint": "/api/sales/invoices/", "by": "customer"}).json()["rows"]), 2)
        [mine] = rep.get(SUMMARY, {"endpoint": "/api/sales/invoices/", "by": "customer"}).json()["rows"]
        self.assertEqual((mine["label"], mine["sums"]["total"]), ("Acme", "1000.00"))

    def test_refusals(self):
        self.invoice("10")
        manager = self.as_("AR Manager")
        self.assertEqual(manager.get(SUMMARY, {"endpoint": "/api/sales/invoices/", "by": "number"}).status_code, 400)
        self.assertEqual(manager.get(SUMMARY, {"endpoint": "/api/sales/invoices/", "by": "customer", "meter": "1"}).status_code, 400)
        self.assertEqual(manager.get(SUMMARY, {"endpoint": "/api/sales/invoices/1/", "by": "customer"}).status_code, 400)
        self.assertEqual(manager.get(SUMMARY, {"endpoint": "/app/sales/invoices/", "by": "customer"}).status_code, 400)
        self.assertEqual(manager.get(SUMMARY, {"endpoint": "/api/sales/nowhere/", "by": "customer"}).status_code, 400)
        self.assertEqual(self.as_("Payroll Officer").get(SUMMARY, {"endpoint": "/api/sales/invoices/", "by": "customer"}).status_code, 403)
        self.assertEqual(self.as_("Payroll Officer").get(SUMMARY, {"endpoint": "/api/sales/invoices/"}).status_code, 403)
        self.assertIsInstance(Decimal("1"), Decimal)
