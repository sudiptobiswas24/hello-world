"""
Tax a customer deducted: an invoice of 1,18,000 (1,00,000 and 18% GST)
paid 1,17,900 because the buyer deducted 194Q at 0.1% of 1,00,000 = 100.
The 100 is not owed; it is a claim on Form 26AS. The receivable clears,
TDS receivable holds 100, and the statement foots to nothing owed.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.models import Account, AccountType, TdsSection

from .models import Invoice, customer_statement, outstanding_balance
from .tests_base import SalesTestCase

DAY = datetime.date(2026, 3, 20)


class CustomerTdsTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.tds_receivable = Account.objects.create(code="1450", name="TDS receivable",
                                                     account_type=AccountType.ASSET)
        self.goods = TdsSection.objects.create(
            code="194Q", name="Purchase of goods", rate_percent=Decimal("0.1"), no_pan_rate_percent=Decimal("5"),
            mode="excess", annual_threshold=Decimal("5000000"), receivable_account=self.tds_receivable)
        self.invoice = self.bill(self.make_order("1", "118000"))

    def test_the_shortfall_is_a_claim_not_a_debt(self):
        self.invoice.record_tds(self.goods, "100", on_date=DAY, certificate="16A-77")
        invoice = Invoice.objects.get(pk=self.invoice.pk)
        self.assertEqual((invoice.amount_due(), invoice.settlement_status()), (Decimal("117900.00"), "partial"))
        self.allocate(self.receipt("117900"), invoice, "117900")
        invoice = Invoice.objects.get(pk=self.invoice.pk)
        self.assertEqual((invoice.amount_due(), invoice.settlement_status()), (Decimal("0.00"), "paid"))
        self.assertEqual((self.balance(self.ar), self.balance(self.tds_receivable)), (Decimal("0"), Decimal("100.00")))
        self.assertEqual(outstanding_balance(self.customer), Decimal("0"))
        statement = customer_statement(self.customer, as_of=datetime.date(2026, 3, 31))
        self.assertEqual(statement["closing_balance"], Decimal("0"))
        self.assertIn("TDS deducted", [entry.kind for entry in statement["entries"]])

    def test_reversed_when_the_customer_never_files_it(self):
        tds = self.invoice.record_tds(self.goods, "100", on_date=DAY)
        self.allocate(self.receipt("117900"), self.invoice, "117900")
        tds.reverse(on_date=datetime.date(2026, 6, 1))
        invoice = Invoice.objects.get(pk=self.invoice.pk)
        self.assertEqual(invoice.amount_due(), Decimal("100.00"))
        self.assertEqual((self.balance(self.ar), self.balance(self.tds_receivable)), (Decimal("100.00"), Decimal("0")))
        statement = customer_statement(self.customer, as_of=datetime.date(2026, 6, 30))
        self.assertEqual(statement["closing_balance"], Decimal("100.00"))
        with self.assertRaisesMessage(ValidationError, "already been reversed"):
            tds.reverse()

    def test_once_in_form_26as_it_stays(self):
        tds = self.invoice.record_tds(self.goods, "100", on_date=DAY)
        tds.confirm(on_date=datetime.date(2026, 7, 15), certificate="16A-77")
        with self.assertRaisesMessage(ValidationError, "Form 26AS shows it"):
            tds.reverse()
        tds.unconfirm()
        tds.reverse()

    def test_refusals(self):
        with self.assertRaisesMessage(ValidationError, "Only 118000.00 is left"):
            self.invoice.record_tds(self.goods, "118000.01")
        draft = self.make_order("1", "100").create_invoice(self.ar, invoice_date=DAY)
        with self.assertRaisesMessage(ValidationError, "posted invoice"):
            draft.record_tds(self.goods, "1")
        note = self.invoice.create_credit_note()
        with self.assertRaisesMessage(ValidationError, "credit note is not paid"):
            note.record_tds(self.goods, "1")
        self.goods.receivable_account = None
        self.goods.save()
        other = self.bill(self.make_order("1", "100"))
        with self.assertRaisesMessage(ValidationError, "no receivable account"):
            other.record_tds(self.goods, "1")


class CustomerTdsApiTests(CustomerTdsTests):
    def setUp(self):
        super().setUp()
        from django.core.management import call_command

        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        from django.contrib.auth.models import Group, User
        from rest_framework.test import APIClient

        from .tests_base import carries_every_customer

        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        if role == "Sales Rep":
            carries_every_customer(user)
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_recorded_by_ar_and_confirmed_by_the_controller(self):
        url = "/api/sales/customer-tds/record/"
        given = {"invoice": self.invoice.pk, "section": self.goods.pk}
        rep = self.as_("Sales Rep")
        self.assertEqual(rep.post(url, {**given, "amount": "100"}, format="json").status_code, 403)
        ar = self.as_("AR Manager")
        missing = ar.post(url, given, format="json")
        self.assertEqual(missing.status_code, 400)
        done = ar.post(url, {**given, "amount": "100", "date": "2026-03-20"}, format="json")
        self.assertEqual(done.status_code, 201, done.content)
        self.assertEqual(ar.get(f"/api/sales/invoices/{self.invoice.pk}/").json()["amount_due"], "117900.00")
        [row] = ar.get("/api/sales/customer-tds/", {"invoice": self.invoice.pk}).json()
        self.assertEqual(ar.post(f"/api/sales/customer-tds/{row['id']}/confirm/", {}, format="json").status_code, 403)
        confirmed = self.as_("Controller").post(f"/api/sales/customer-tds/{row['id']}/confirm/",
                                                {"date": "2026-07-15", "certificate": "16A-77"}, format="json")
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual((confirmed.json()["confirmed_on"], confirmed.json()["certificate"]), ("2026-07-15", "16A-77"))
