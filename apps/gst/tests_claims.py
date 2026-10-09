"""
Money given back on a customer's claim, as a price adjustment, against
the figures worked by hand:

  one line 10,000 + 18% (9 and 9) = 11,800; torn bags, 500 before tax:
    note 500 + 45 + 45 = 590; owed 11,210; GSTR-1 lists it as a credit note
  two lines 6,000 and 4,000; claim 1,000 -> 600 and 400
  three lines 3,333.33, 3,333.33, 3,333.34; claim 100 -> 33.33, 33.33, 33.34
  after a 500 claim, no more than 9,500 more; the 1 sack is still returnable
  the quarter: torn 500 + short weight 300 = 800; rate 1,000
"""

from decimal import Decimal as D

from django.core.exceptions import ValidationError

from apps.core.models import PartyRole
from apps.sales.models import Invoice, InvoiceLine, claims

from .returns import gstr1
from .tests import DAY, GstReturnTestCase, gstin

SEPTEMBER = (DAY.replace(day=1), DAY.replace(day=30))


class ClaimTests(GstReturnTestCase):
    def setUp(self):
        super().setUp()
        self.buyer = self.party("CEMENT", gstin=gstin("27AABCC5555E1Z"))

    def lines(self, *prices):
        invoice = Invoice.objects.create(customer=self.buyer, invoice_date=DAY, receivable_account=self.ar,
                                         currency=self.inr)
        for price in prices:
            line = InvoiceLine.objects.create(invoice=invoice, item=self.sack, quantity=D("1"), unit_price=D(price),
                                              revenue_account=self.revenue)
            line.taxes.set(self.pair)
        invoice.post()
        return Invoice.objects.get(pk=invoice.pk)

    def test_a_price_adjustment_with_its_tax(self):
        invoice = self.sell(self.buyer, "10000")
        note = invoice.credit_claim(D("500"), "torn", on_date=DAY)
        self.assertEqual((note.subtotal(), note.tax_total(), note.total()), (D("500.00"), D("90.00"), D("590.00")))
        self.assertEqual(Invoice.objects.get(pk=invoice.pk).amount_due(), D("11210.00"))
        self.assertEqual(invoice.lines.get().quantity_creditable(), D("1"))
        self.assertIn(note.number, [row["number"] for row in gstr1(*SEPTEMBER)["cdnr"]])

    def test_spread_by_value_the_last_taking_the_rounding(self):
        two = self.lines("6000", "4000").credit_claim(D("1000"), "rate", on_date=DAY)
        self.assertEqual(sorted(line.net_amount() for line in two.lines.all()), [D("400.00"), D("600.00")])
        three = self.lines("3333.33", "3333.33", "3333.34").credit_claim(D("100"), "quality", on_date=DAY)
        self.assertEqual([line.net_amount() for line in three.lines.order_by("pk")],
                         [D("33.33"), D("33.33"), D("33.34")])

    def test_no_more_than_is_left_and_the_refusals(self):
        invoice = self.sell(self.buyer, "10000")
        invoice.credit_claim(D("500"), "torn", on_date=DAY)
        with self.assertRaisesMessage(ValidationError, "Only 9500.00"):
            invoice.credit_claim(D("9500.01"), "torn")
        with self.assertRaisesMessage(ValidationError, "Say what the claim was for"):
            invoice.credit_claim(D("1"), "")
        with self.assertRaisesMessage(ValidationError, "more than nothing"):
            invoice.credit_claim(D("0"), "torn")
        note = invoice.credit_notes.get()
        with self.assertRaisesMessage(ValidationError, "not a credit note"):
            note.credit_claim(D("1"), "torn")

    def test_a_claim_is_not_credited_before_its_invoice(self):
        """The audit's probe: an invoice of 10 September, a claim of 100 dated 20 August."""
        import datetime

        from django.utils import timezone

        from apps.accounting.models import JournalLine

        invoice = self.sell(self.buyer, "1000")
        with self.assertRaisesMessage(ValidationError, "is not credited on 2026-08-20: it was issued on 2026-09-10"):
            invoice.credit_claim(D("100"), "torn", on_date=datetime.date(2026, 8, 20))
        with self.assertRaisesMessage(ValidationError, "that day has not come"):
            invoice.credit_claim(D("100"), "torn", on_date=timezone.localdate() + datetime.timedelta(days=1))
        self.assertFalse(invoice.credit_notes.exists())
        self.assertFalse(JournalLine.objects.filter(account=self.revenue, debit__gt=0).exists())

    def test_what_quality_cost_the_quarter(self):
        invoice = self.sell(self.buyer, "10000")
        invoice.credit_claim(D("500"), "torn", on_date=DAY)
        invoice.credit_claim(D("300"), "short_weight", on_date=DAY)
        self.lines("6000", "4000").credit_claim(D("1000"), "rate", on_date=DAY)
        by_reason = {}
        for row in claims(*SEPTEMBER):
            by_reason[row["reason"]] = by_reason.get(row["reason"], D("0")) + row["net"]
        self.assertEqual(by_reason, {"torn": D("500.00"), "short_weight": D("300.00"), "rate": D("1000.00")})


class ClaimApiTests(GstReturnTestCase):
    def setUp(self):
        super().setUp()
        from django.core.management import call_command

        call_command("setup_roles", verbosity=0)
        self.buyer = self.party("CEMENT", gstin=gstin("27AABCC5555E1Z"))
        self.invoice = self.sell(self.buyer, "10000")

    def as_(self, role):
        from django.contrib.auth.models import Group, User
        from rest_framework.test import APIClient

        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_credited_by_ar_and_reported_for_the_quarter(self):
        ar = self.as_("AR Manager")
        made = ar.post("/api/sales/invoices/claim/", {"invoice": self.invoice.pk, "net": "500", "reason": "torn",
                                                      "date": str(DAY)}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual(made.json()["total"], "590.00")
        bad = ar.post("/api/sales/invoices/claim/", {"invoice": self.invoice.pk, "net": "1"}, format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("reason", bad.json())
        [row] = ar.get("/api/sales/invoices/claims/", {"from": "2026-09-01", "to": "2026-09-30"}).json()
        self.assertEqual((row["reason"], row["net"], row["total"]), ("torn", "500.00", "590.00"))

    def test_not_dated_before_its_invoice_over_the_api(self):
        response = self.as_("AR Manager").post(
            "/api/sales/invoices/claim/", {"invoice": self.invoice.pk, "net": "100", "reason": "torn",
                                           "date": "2026-08-20"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("it was issued on 2026-09-10", response.content.decode())

    def test_a_complaint_settled_says_what_it_cost(self):
        from apps.manufacturing.complaints import Complaint

        complaint = Complaint.objects.create(customer=self.buyer, received_on=DAY, category="seam",
                                             description="Seams opened in the silo")
        refused = self.as_("Quality Manager").post(f"/api/manufacturing/complaints/{complaint.pk}/settle/", {
            "invoice": self.invoice.pk, "net": "500", "reason": "quality"}, format="json")
        self.assertEqual(refused.status_code, 403)
        settled = self.as_("AR Manager").post(f"/api/manufacturing/complaints/{complaint.pk}/settle/", {
            "invoice": self.invoice.pk, "net": "500", "reason": "quality"}, format="json")
        self.assertEqual(settled.status_code, 200, settled.content)
        self.assertEqual(settled.json()["cost"], "500.00")
        # Settled today: the note is dated the day it is given.
        [row] = claims(DAY, DAY.replace(month=12, day=31))
        self.assertEqual(row["complaint"], complaint.number)
