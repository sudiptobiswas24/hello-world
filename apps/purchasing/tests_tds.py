"""
Tax deducted at source from vendors, against the figures worked by hand
in the scenario table (rupees rounded half up, base without GST):

  194Q 0.1%, on what the year passes 50 lakh by:
    30L -> 0;  30L more -> 10L over -> 1,000;  5L more -> 500
    no PAN at 5% -> 50,000;  a new financial year starts again at nothing
    10,00,550 over -> 1,000.55 -> 1,001;  10,00,449 over -> 1,000
  194C 2%, 30,000 one bill or 1 lakh the year, then on the whole year:
    25k, 25k, 25k -> 0;  30k -> the year is 1,05,000 -> 2,100;  then 20k -> 400
    one bill of 35k -> 700;  at a vendor's own 1% -> 350
  35,000 + 18% GST = 41,300; less 700 tax -> 40,600 to pay; payable and TDS clear
  a challan of 1,000 + 2,100 -> TDS payable 0, bank -3,100
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.accounting.models import (
    Account, AccountType, JournalLine, PartyTaxProfile, Tax, TaxGroup, TaxScope, TdsSection,
)
from apps.core.models import Currency, Party, PartyRole, PartyRoleAssignment

from .models import Bill, BillLine, TdsChallan
from .tests_lifecycle import PurchasingLifecycleTestCase

DAY = datetime.date(2026, 6, 10)


class TdsTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.tds_payable = Account.objects.create(code="2250", name="TDS payable", account_type=AccountType.LIABILITY)
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        self.goods = TdsSection.objects.create(
            code="194Q", name="Purchase of goods", rate_percent=Decimal("0.1"), no_pan_rate_percent=Decimal("5"),
            mode="excess", annual_threshold=Decimal("5000000"), payable_account=self.tds_payable)
        self.contract = TdsSection.objects.create(
            code="194C", name="Contractors", rate_percent=Decimal("2"), no_pan_rate_percent=Decimal("20"),
            mode="whole", single_threshold=Decimal("30000"), annual_threshold=Decimal("100000"),
            payable_account=self.tds_payable)
        PartyTaxProfile.objects.create(party=self.vendor, pan="AAACG1234F", tds_section=self.goods)

    def bill(self, amount, day=DAY, vendor=None, gst=None):
        bill = Bill.objects.create(vendor=vendor or self.vendor, bill_date=day, payable_account=self.payable)
        line = BillLine.objects.create(bill=bill, description="Granules", quantity=Decimal("1"),
                                       unit_price=Decimal(amount), expense_account=self.expense)
        if gst:
            line.taxes.set([gst])
        bill.post()
        return Bill.objects.get(pk=bill.pk)

    def balance(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(d=Sum("debit"), c=Sum("credit"))
        return (rows["d"] or Decimal("0")) - (rows["c"] or Decimal("0"))

    def contractor(self, **profile):
        party = Party.objects.create(code="V-C", name="Loom Fitters")
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.VENDOR)
        PartyTaxProfile.objects.create(party=party, tds_section=self.contract, **profile)
        return party


class RefusalTests(TdsTestCase):
    def test_a_draft_bill(self):
        draft = Bill.objects.create(vendor=self.vendor, bill_date=DAY, payable_account=self.payable)
        with self.assertRaisesMessage(ValidationError, "posted bill"):
            draft.deduct_tds()

    def test_below_the_threshold_nothing_is_recorded(self):
        with self.assertRaisesMessage(ValidationError, "Nothing to deduct"):
            self.bill("3000000").deduct_tds()

    def test_a_second_deduction_on_one_bill(self):
        self.bill("3000000")
        bill = self.bill("3000000")
        bill.deduct_tds()
        with self.assertRaisesMessage(ValidationError, "already been deducted"):
            bill.deduct_tds()

    def test_a_foreign_bill(self):
        eur = Currency.objects.create(code="EUR", name="Euro")
        bill = Bill.objects.create(vendor=self.vendor, bill_date=DAY, payable_account=self.payable, currency=eur)
        Bill.objects.filter(pk=bill.pk).update(posted=True)
        with self.assertRaisesMessage(ValidationError, "195"):
            Bill.objects.get(pk=bill.pk).deduct_tds()

    def test_no_section_on_the_vendor(self):
        party = Party.objects.create(code="V-N", name="No section")
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.VENDOR)
        with self.assertRaisesMessage(ValidationError, "say which section"):
            self.bill("50000", vendor=party).deduct_tds()

    def test_more_than_is_left_to_pay(self):
        party = self.contractor(pan="AAAPL1234C")
        bill = self.bill("35000", vendor=party)
        Bill.objects.filter(pk=bill.pk).update(settlement_discount_amount=Decimal("34500"))
        with self.assertRaisesMessage(ValidationError, "only 500.00 is left"):
            Bill.objects.get(pk=bill.pk).deduct_tds()

    def test_a_rate_of_its_own_needs_a_reason_and_a_pan(self):
        party = Party.objects.create(code="V-R", name="Rated")
        with self.assertRaisesMessage(ValidationError, "what the rate rests on"):
            PartyTaxProfile.objects.create(party=party, pan="AAAPR1234C", tds_section=self.contract,
                                           tds_rate_percent=Decimal("1"))
        with self.assertRaisesMessage(ValidationError, "no-PAN rate"):
            PartyTaxProfile.objects.create(party=party, tds_section=self.contract,
                                           tds_rate_percent=Decimal("1"), tds_rate_reference="Individual")


class GoodsTests(TdsTestCase):
    def test_on_what_the_year_passes_fifty_lakh_by(self):
        first = self.bill("3000000")
        second = self.bill("3000000")
        deduction = second.deduct_tds()
        self.assertEqual((deduction.base, deduction.amount), (Decimal("1000000.00"), Decimal("1000.00")))
        third = self.bill("500000")
        self.assertEqual(third.deduct_tds().amount, Decimal("500.00"))
        self.assertEqual(first.tds_deductions.count(), 0)

    def test_without_a_pan_at_the_no_pan_rate(self):
        self.vendor.tax_profile.pan = ""
        self.vendor.tax_profile.save()
        self.bill("3000000")
        deduction = self.bill("3000000").deduct_tds()
        self.assertEqual((deduction.amount, deduction.pan), (Decimal("50000.00"), ""))

    def test_the_pan_inside_the_gstin_is_on_file(self):
        profile = self.vendor.tax_profile
        profile.pan, profile.gstin = "", "07AAGFF2194N1Z1"
        profile.save()
        self.assertEqual(profile.pan_on_file(), "AAGFF2194N")
        with self.assertRaisesMessage(ValidationError, "carries the PAN"):
            profile.pan = "AAACX9999F"
            profile.save()

    def test_a_new_financial_year_starts_again(self):
        self.assertEqual(self.bill("6000000", day=datetime.date(2026, 3, 31)).deduct_tds().amount, Decimal("1000.00"))
        with self.assertRaisesMessage(ValidationError, "Nothing to deduct"):
            self.bill("3000000", day=datetime.date(2026, 4, 1)).deduct_tds()

    def test_whole_rupees_rounded_half_up(self):
        self.bill("4000000")
        self.assertEqual(self.bill("2000550").deduct_tds().amount, Decimal("1001.00"))
        self.bill("4000000", day=datetime.date(2027, 5, 1))
        self.assertEqual(self.bill("2000449", day=datetime.date(2027, 5, 2)).deduct_tds().amount, Decimal("1000.00"))


class ContractTests(TdsTestCase):
    def test_the_whole_year_once_the_annual_limit_is_passed(self):
        party = self.contractor(pan="AAAPL1234C")
        for _ in range(3):
            with self.assertRaisesMessage(ValidationError, "Nothing to deduct"):
                self.bill("25000", vendor=party).deduct_tds()
        crossing = self.bill("30000", vendor=party).deduct_tds()
        self.assertEqual((crossing.base, crossing.amount), (Decimal("105000.00"), Decimal("2100.00")))
        self.assertEqual(crossing.covered_bills.count(), 4)
        later = self.bill("20000", vendor=party).deduct_tds()
        self.assertEqual((later.base, later.amount), (Decimal("20000.00"), Decimal("400.00")))
        with self.assertRaisesMessage(ValidationError, "already been deducted"):
            party.bills.order_by("pk").first().deduct_tds()

    def test_one_bill_past_the_single_limit(self):
        party = self.contractor(pan="AAAPL1234C")
        self.assertEqual(self.bill("35000", vendor=party).deduct_tds().amount, Decimal("700.00"))

    def test_at_the_vendors_own_rate(self):
        party = self.contractor(pan="AAAPL1234C", tds_rate_percent=Decimal("1"), tds_rate_reference="Individual")
        deduction = self.bill("35000", vendor=party).deduct_tds()
        self.assertEqual((deduction.amount, deduction.rate_percent), (Decimal("350.00"), Decimal("1.0000")))


class LedgerTests(TdsTestCase):
    def gst(self):
        group = TaxGroup.objects.create(code="GST", name="GST")
        itc = Account.objects.create(code="1300", name="GST input", account_type=AccountType.ASSET)
        return Tax.objects.create(code="GST18", name="GST 18%", rate=Decimal("18"), group=group,
                                  scope=TaxScope.BOTH, collected_account=itc, paid_account=itc)

    def test_the_bill_is_paid_net_and_both_accounts_clear(self):
        party = self.contractor(pan="AAAPL1234C")
        bill = self.bill("35000", vendor=party, gst=self.gst())
        self.assertEqual(bill.total(), Decimal("41300.00"))
        bill.deduct_tds()
        bill = Bill.objects.get(pk=bill.pk)
        self.assertEqual((bill.amount_due(), bill.settlement_status()), (Decimal("40600.00"), "partial"))
        self.assertEqual(self.balance(self.payable), Decimal("-40600.00"))
        self.assertEqual(self.balance(self.tds_payable), Decimal("-700.00"))

    def test_paid_net_of_the_tax_it_is_off_every_open_list(self):
        from apps.accounting.models import Payment, PaymentDirection

        from .models import BillPayment, bills_still_owed

        party = self.contractor(pan="AAAPL1234C")
        bill = self.bill("35000", vendor=party)
        deduction = bill.deduct_tds()
        paying = Payment.objects.create(party=party, direction=PaymentDirection.DISBURSEMENT, payment_date=DAY,
                                        amount=Decimal("34300"), bank_account=self.bank,
                                        counterpart_account=self.payable)
        paying.post()
        BillPayment.objects.create(bill=bill, payment=paying, amount=Decimal("34300"))
        self.assertEqual(Bill.objects.get(pk=bill.pk).amount_due(), Decimal("0.00"))
        self.assertFalse(bills_still_owed(Bill.objects.filter(pk=bill.pk)).exists())
        deduction.reverse()
        self.assertTrue(bills_still_owed(Bill.objects.filter(pk=bill.pk)).exists())

    def test_reversed_until_a_challan_pays_it(self):
        party = self.contractor(pan="AAAPL1234C")
        deduction = self.bill("35000", vendor=party).deduct_tds()
        deduction.reverse()
        self.assertEqual(self.balance(self.tds_payable), Decimal("0"))
        self.assertEqual(Bill.objects.get(pk=deduction.bill_id).amount_due(), Decimal("35000.00"))
        again = Bill.objects.get(pk=deduction.bill_id).deduct_tds()
        challan = TdsChallan.pay(self.contract, DAY, DAY, self.bank, "00042", "0510308")
        self.assertEqual(challan.amount, Decimal("700.00"))
        with self.assertRaisesMessage(ValidationError, "void the challan first"):
            again.reverse()

    def test_not_paid_over_from_where_it_is_owed(self):
        self.bill("35000", vendor=self.contractor(pan="AAAPL1234C")).deduct_tds()
        with self.assertRaisesMessage(ValidationError, "2250 - TDS payable is not a bank, cash or card account"):
            TdsChallan.pay(self.contract, DAY, DAY, self.tds_payable, "00042", "0510308")
        self.assertFalse(TdsChallan.objects.exists())
        self.assertEqual(self.balance(self.tds_payable), Decimal("-700.00"))

    def test_not_paid_over_from_what_customers_owe(self):
        self.bill("35000", vendor=self.contractor(pan="AAAPL1234C")).deduct_tds()
        receivable = Account.objects.create(code="1105", name="Customers", account_type=AccountType.ASSET)
        with self.assertRaisesMessage(ValidationError, "1105 - Customers is not a bank, cash or card account"):
            TdsChallan.pay(self.contract, DAY, DAY, receivable, "00042", "0510308")
        self.assertFalse(TdsChallan.objects.exists())

    def test_a_challan_clears_the_month_and_voiding_it_owes_again(self):
        self.bill("3000000")
        self.bill("3000000").deduct_tds()
        party = self.contractor(pan="AAAPL1234C")
        for amount in ("25000", "25000", "25000"):
            self.bill(amount, vendor=party)
        self.bill("30000", vendor=party).deduct_tds()
        challan = TdsChallan.pay(self.goods, DAY, DAY, self.bank, "00042", "0510308")
        self.assertEqual(challan.amount, Decimal("1000.00"))
        challan_c = TdsChallan.pay(self.contract, DAY, DAY, self.bank, "00043", "0510308")
        self.assertEqual(challan_c.amount, Decimal("2100.00"))
        self.assertEqual((self.balance(self.tds_payable), self.balance(self.bank)),
                         (Decimal("0"), Decimal("-3100.00")))
        with self.assertRaisesMessage(ValidationError, "Nothing deducted"):
            TdsChallan.pay(self.goods, DAY, DAY, self.bank, "00044", "0510308")
        challan.void()
        self.assertEqual(self.balance(self.tds_payable), Decimal("-1000.00"))
        self.assertEqual(TdsChallan.pay(self.goods, DAY, DAY, self.bank, "00045", "0510308").amount,
                         Decimal("1000.00"))

    def test_a_full_debit_note_after_the_tax_leaves_the_vendor_owing_it(self):
        party = self.contractor(pan="AAAPL1234C")
        bill = self.bill("35000", vendor=party)
        bill.deduct_tds()
        note = Bill.objects.create(vendor=party, bill_date=DAY, payable_account=self.payable, debits=bill)
        BillLine.objects.create(bill=note, description="Granules", quantity=Decimal("1"),
                                unit_price=Decimal("35000"), expense_account=self.expense)
        note.post()
        note = Bill.objects.get(pk=note.pk)
        self.assertEqual((note.amount_absorbed(), note.refund_due()), (Decimal("34300.00"), Decimal("700.00")))
        self.assertEqual(self.balance(self.payable), Decimal("700.00"))


class ApiTests(TdsTestCase):
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

    def test_deducted_paid_over_and_listed_for_the_quarter(self):
        self.bill("3000000")
        bill = self.bill("3000000")
        manager = self.as_("AP Manager")
        clerk = self.as_("Purchasing Clerk")
        self.assertEqual(clerk.post(f"/api/purchasing/bills/{bill.pk}/deduct_tds/", {}, format="json").status_code, 403)
        done = manager.post(f"/api/purchasing/bills/{bill.pk}/deduct_tds/", {}, format="json")
        self.assertEqual(done.status_code, 200, done.content)
        self.assertEqual(done.json()["amount_due"], "2999000.00")
        again = manager.post(f"/api/purchasing/bills/{bill.pk}/deduct_tds/", {}, format="json")
        self.assertEqual(again.status_code, 400)
        self.assertIn("already been deducted", again.content.decode())

        paid = manager.post("/api/purchasing/tds-challans/pay/", {
            "section": self.goods.pk, "month": "2026-06-01", "date": "2026-07-07", "bank_account": self.bank.pk,
            "challan_number": "00042", "bsr_code": "0510308"}, format="json")
        self.assertEqual(paid.status_code, 201, paid.content)
        self.assertEqual(paid.json()["amount"], "1000.00")

        controller = self.as_("Controller")
        [row] = controller.get("/api/purchasing/tds-deductions/quarter/",
                               {"from": "2026-04-01", "to": "2026-06-30"}).json()
        self.assertEqual((row["section"], row["pan"], row["base"], row["amount"], row["challan"]),
                         ("194Q", "AAACG1234F", "1000000.00", "1000.00", "0510308/00042"))
        [listed] = controller.get("/api/purchasing/tds-deductions/", {"bill": bill.pk}).json()
        reversed_ = manager.post(f"/api/purchasing/tds-deductions/{listed['id']}/reverse/", {}, format="json")
        self.assertEqual(reversed_.status_code, 400)
        self.assertIn("void the challan first", reversed_.content.decode())

    def test_the_vendors_terms_are_kept_on_its_tax_profile(self):
        profile = self.vendor.tax_profile
        changed = self.as_("Controller").patch(f"/api/accounting/party-tax-profiles/{profile.pk}/", {
            "tds_section": self.contract.pk, "tds_rate_percent": "1", "tds_rate_reference": "197/2026/14"},
            format="json")
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(changed.json()["tds_rate_percent"], "1.0000")
