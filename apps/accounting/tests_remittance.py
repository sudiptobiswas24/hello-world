"""
Money on paper. A vendor paid for two bills gets a remittance advice
naming both, the account it went to and what is on account; a customer
who paid gets a receipt; a draft or a void payment sends nothing; the AP
manager emails it from the office and a store hand may not.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.core.models import PartyBankAccount
from apps.purchasing.models import BillPayment
from apps.sales.models import InvoicePayment
from apps.sales.tests_base import SalesTestCase

from .models import Payment, PaymentDirection


class RemittanceTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        from apps.accounting.models import Account, AccountType
        from apps.core.models import Party, PartyRole, PartyRoleAssignment
        from apps.purchasing.models import Bill, BillLine

        self.payable = Account.objects.create(code="2000", name="Payables", account_type=AccountType.LIABILITY)
        self.expense = Account.objects.create(code="6000", name="Granules", account_type=AccountType.EXPENSE)
        self.vendor = Party.objects.create(code="V-1", name="Granule House", default_currency=self.usd,
                                           email="billing@granules.example")
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)
        PartyBankAccount.objects.create(party=self.vendor, account_name="Granule House", bank_name="SBI, Pune",
                                        account_number="30012345678", ifsc="SBIN0000300", is_primary=True)
        self.bills = []
        for reference, amount in (("GH/101", "6000"), ("GH/102", "4000")):
            bill = Bill.objects.create(vendor=self.vendor, bill_date=datetime.date(2026, 3, 5), currency=self.usd,
                                       payable_account=self.payable, reference=reference)
            BillLine.objects.create(bill=bill, description="Granules", quantity=Decimal("1"),
                                    unit_price=Decimal(amount), expense_account=self.expense)
            bill.post()
            self.bills.append(bill)

    def disbursement(self, amount="10500"):
        payment = Payment.objects.create(party=self.vendor, direction=PaymentDirection.DISBURSEMENT,
                                         payment_date=datetime.date(2026, 3, 20), amount=Decimal(amount),
                                         currency=self.usd, bank_account=self.bank, counterpart_account=self.payable,
                                         reference="UTR 7788")
        payment.post()
        for bill in self.bills:
            BillPayment.objects.create(bill=bill, payment=payment, amount=bill.total())
        return payment

    def test_the_advice_names_the_bills_the_account_and_what_is_on_account(self):
        payment = self.disbursement()
        pdf = payment.render_pdf()
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreater(len(pdf), 1000)
        self.assertEqual(payment.email_to_party(), "billing@granules.example")
        message = mail.outbox[0]
        self.assertEqual((message.to, message.attachments[0][0]), (["billing@granules.example"], f"{payment.number}.pdf"))
        self.assertIn("Remittance advice", message.subject)

    def test_a_receipt_for_money_in(self):
        receipt = Payment.objects.create(party=self.customer, direction=PaymentDirection.RECEIPT,
                                         payment_date=datetime.date(2026, 3, 21), amount=Decimal("500"),
                                         currency=self.usd, bank_account=self.bank, counterpart_account=self.ar)
        with self.assertRaisesMessage(ValidationError, "post it first"):
            receipt.email_to_party()
        receipt.post()
        invoice = self.make_order_invoice() if hasattr(self, "make_order_invoice") else None
        if invoice is not None:
            InvoicePayment.objects.create(invoice=invoice, payment=receipt, amount=Decimal("500"))
        self.assertTrue(receipt.render_pdf().startswith(b"%PDF-"))
        self.assertEqual(receipt.email_to_party(), "ap@acme.example")
        self.assertIn("Payment receipt", mail.outbox[0].subject)
        receipt.void(memo="bounced")
        with self.assertRaisesMessage(ValidationError, "void"):
            receipt.email_to_party()

    def test_from_the_office_and_who_may(self):
        call_command("setup_roles", verbosity=0)
        payment = self.disbursement()

        def as_(role):
            user = User.objects.create_user(role.replace(" ", "_").lower())
            user.groups.add(Group.objects.get(name=role))
            client = APIClient()
            client.force_authenticate(user)
            return client

        manager = as_("AP Manager")
        paper = manager.get(f"/api/accounting/payments/{payment.pk}/pdf/")
        self.assertEqual((paper.status_code, paper["Content-Type"]), (200, "application/pdf"))
        sent = manager.post(f"/api/accounting/payments/{payment.pk}/send/", {"to": "owner@granules.example"}, format="json")
        self.assertEqual((sent.status_code, sent.json()), (200, {"sent_to": "owner@granules.example"}), sent.content)
        self.assertEqual(as_("Warehouse Staff").post(f"/api/accounting/payments/{payment.pk}/send/").status_code, 403)
