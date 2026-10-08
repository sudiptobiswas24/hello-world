"""
Micro and small vendors paid within the Act's days, against the figures
worked by hand:

  bill 1 Jun 2026: net 30 -> due 1 Jul; paid 15 Jul -> 14 days late
  net 60 -> capped at 45 -> due 16 Jul;  no terms -> 15 days -> 16 Jun
  paid in two parts, 20 Jun and 10 Jul, net 30 -> paid on 10 Jul, 9 late
  at 31 Mar 2027: 1 Feb net 30 (due 3 Mar) unpaid 50,000 -> at risk, 28 late;
                  1 Mar net 30 (due 31 Mar) unpaid -> not yet late
  a medium enterprise is not listed
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.models import Account, AccountType, PartyTaxProfile, Payment, PaymentDirection
from apps.core.models import Party, PartyRole, PartyRoleAssignment, PaymentTerms

from .models import Bill, BillLine, BillPayment
from .msme import msme_bills
from .tests_lifecycle import PurchasingLifecycleTestCase

JUNE = datetime.date(2026, 6, 1)


class MsmeTests(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        self.net30 = PaymentTerms.objects.create(code="N30M", name="Net 30", net_days=30)
        self.net60 = PaymentTerms.objects.create(code="N60", name="Net 60", net_days=60)
        PartyTaxProfile.objects.create(party=self.vendor, msme_category="micro", udyam_number="UDYAM-MH-26-0012345")

    def bill(self, amount="10000", day=JUNE, terms=None, vendor=None):
        bill = Bill.objects.create(vendor=vendor or self.vendor, bill_date=day, payable_account=self.payable,
                                   payment_terms=terms)
        BillLine.objects.create(bill=bill, description="Cones", quantity=Decimal("1"), unit_price=Decimal(amount),
                                expense_account=self.expense)
        bill.post()
        return Bill.objects.get(pk=bill.pk)

    def pay(self, bill, amount, day):
        payment = Payment.objects.create(
            party=bill.vendor, direction=PaymentDirection.DISBURSEMENT, payment_date=day, amount=Decimal(amount),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.payable)
        payment.post()
        BillPayment.objects.create(bill=bill, payment=payment, amount=Decimal(amount))

    def row(self, bill, as_of):
        [row] = [row for row in msme_bills(JUNE.replace(month=1), datetime.date(2027, 12, 31), as_of=as_of)
                 if row["bill"] == bill.pk]
        return row

    def test_late_by_the_days_past_the_acts_date(self):
        bill = self.bill(terms=self.net30)
        self.pay(bill, "10000", datetime.date(2026, 7, 15))
        row = self.row(bill, datetime.date(2026, 8, 1))
        self.assertEqual((row["due"], row["paid_on"], row["days_late"], row["at_risk"]),
                         (datetime.date(2026, 7, 1), datetime.date(2026, 7, 15), 14, Decimal("0")))

    def test_agreed_days_capped_at_45_and_15_without_agreement(self):
        self.assertEqual(self.row(self.bill(terms=self.net60), JUNE)["due"], datetime.date(2026, 7, 16))
        # The fixture's vendor brings Net 30 to every bill; one with no terms agreed.
        loose = Party.objects.create(code="V-L", name="Loose Terms")
        PartyRoleAssignment.objects.create(party=loose, role=PartyRole.VENDOR)
        PartyTaxProfile.objects.create(party=loose, msme_category="small", udyam_number="UDYAM-MH-26-0054321")
        unagreed = self.bill(vendor=loose)
        self.assertIsNone(unagreed.payment_terms_id)
        self.assertEqual(self.row(unagreed, JUNE)["due"], datetime.date(2026, 6, 16))

    def test_paid_in_parts_is_paid_on_the_last(self):
        bill = self.bill(terms=self.net30)
        self.pay(bill, "4000", datetime.date(2026, 6, 20))
        self.pay(bill, "6000", datetime.date(2026, 7, 10))
        row = self.row(bill, datetime.date(2026, 8, 1))
        self.assertEqual((row["paid_on"], row["days_late"]), (datetime.date(2026, 7, 10), 9))

    def test_what_the_year_end_adds_back(self):
        year_end = datetime.date(2027, 3, 31)
        late = self.bill("50000", day=datetime.date(2027, 2, 1), terms=self.net30)
        current = self.bill("20000", day=datetime.date(2027, 3, 1), terms=self.net30)
        self.pay(late, "50000", datetime.date(2027, 4, 10))  # after the year: not paid at its end
        late_row, current_row = self.row(late, year_end), self.row(current, year_end)
        self.assertEqual((late_row["unpaid"], late_row["at_risk"], late_row["days_late"]),
                         (Decimal("50000.00"), Decimal("50000.00"), 28))
        self.assertEqual((current_row["unpaid"], current_row["at_risk"]), (Decimal("20000.00"), Decimal("0")))

    def test_a_medium_enterprise_is_not_listed_and_a_category_needs_its_udyam(self):
        medium = Party.objects.create(code="V-M", name="Medium Mills")
        PartyRoleAssignment.objects.create(party=medium, role=PartyRole.VENDOR)
        PartyTaxProfile.objects.create(party=medium, msme_category="medium", udyam_number="UDYAM-MH-26-0099999")
        self.bill(vendor=medium)
        self.assertEqual(msme_bills(JUNE, JUNE, as_of=JUNE), [])
        other = Party.objects.create(code="V-X", name="No Udyam")
        with self.assertRaisesMessage(ValidationError, "give the number"):
            PartyTaxProfile.objects.create(party=other, msme_category="small")
        with self.assertRaisesMessage(ValidationError, "is not a Udyam number"):
            PartyTaxProfile.objects.create(party=other, msme_category="small", udyam_number="12345")

    def test_the_controller_reads_it_and_the_stores_do_not(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        bill = self.bill(terms=self.net30)
        self.pay(bill, "10000", datetime.date(2026, 7, 15))

        def as_(role):
            user = User.objects.create_user(role.replace(" ", "_").lower())
            user.groups.add(Group.objects.get(name=role))
            client = APIClient()
            client.force_authenticate(user)
            return client

        url = "/api/purchasing/purchasing-reports/msme/"
        query = {"start": "2026-06-01", "end": "2026-06-30", "as_of": "2026-08-01"}
        [row] = as_("Controller").get(url, query).json()
        self.assertEqual((row["due"], row["paid_on"], row["days_late"], row["total"]),
                         ("2026-07-01", "2026-07-15", 14, "10000.00"))
        self.assertEqual(as_("Warehouse Staff").get(url, query).status_code, 403)
