"""
Tax deducted at source from vendors, against the figures worked by hand
in the scenario table (rupees rounded half up, base without GST):

  194Q 0.1%, on what the year passes 50 lakh by:
    30L -> 0;  30L more -> 10L over -> 1,000;  5L more -> 500
    no PAN at 5% -> 50,000;  a new financial year starts again at nothing
    10,00,550 over -> 1,000.55 -> 1,001;  10,00,449 over -> 1,000
  194C 2%, 30,000 one bill (that bill alone) or 1 lakh the year (then the whole year):
    25k, 25k, 25k -> 0;  30k -> the year is 1,05,000 -> 2,100;  then 20k -> 400
    20k -> 0;  35k -> 700 on 35k alone;  50k -> the year is 1,05,000 -> 70k -> 1,400
    one bill of 35k -> 700;  at a vendor's own 1% -> 350
  35,000 + 18% GST = 41,300; less 700 tax -> 40,600 to pay; payable and TDS clear
  a challan of 1,000 + 2,100 -> TDS payable 0, bank -3,100
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import Sum
from django.test import TransactionTestCase, tag

from apps.accounting.models import (
    Account, AccountType, JournalLine, PartyTaxProfile, Tax, TaxGroup, TaxScope, TdsSection,
)
from apps.core.models import Currency, Party, PartyRole, PartyRoleAssignment

from .models import Bill, BillLine, TdsChallan
from .tests_lifecycle import PurchasingLifecycleTestCase

DAY = datetime.date(2026, 6, 10)


def confirmed_rate(section, rate, no_pan, day=datetime.date(2000, 4, 1)):
    """A section's rate from `day`, confirmed by the accountant (O233: nothing is deducted at one unconfirmed)."""
    from apps.accounting.models import TdsRate

    return TdsRate.objects.create(section=section, applies_from=day, rate_percent=Decimal(rate),
                                  no_pan_rate_percent=Decimal(no_pan), confirmed_on=day)


class TdsTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.tds_payable = Account.objects.create(code="2250", name="TDS payable", account_type=AccountType.LIABILITY)
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        self.goods = TdsSection.objects.create(
            code="194Q", name="Purchase of goods",
            mode="excess", annual_threshold=Decimal("5000000"), payable_account=self.tds_payable)
        self.contract = TdsSection.objects.create(
            code="194C", name="Contractors",
            mode="whole", single_threshold=Decimal("30000"), annual_threshold=Decimal("100000"),
            payable_account=self.tds_payable)
        self.goods_rate = confirmed_rate(self.goods, "0.1", "5")
        self.contract_rate = confirmed_rate(self.contract, "2", "20")
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

    def test_a_bill_past_the_single_limit_is_taxed_alone(self):
        """
        s.194C(5): 20,000 (under 30,000, the year under a lakh: not taxed), then
        35,000, past the single limit: base 35,000, tax 700, the 20,000 spared
        (calc_stat/o102_194c.py). Then 50,000 takes the year to 1,05,000: the
        spared 20,000 is caught up, base 70,000, tax 1,400.
        """
        party = self.contractor(pan="AAAPL1234C")
        with self.assertRaisesMessage(ValidationError, "Nothing to deduct"):
            self.bill("20000", vendor=party).deduct_tds()
        alone = self.bill("35000", vendor=party).deduct_tds()
        self.assertEqual((alone.base, alone.amount, alone.covered_bills.count()),
                         (Decimal("35000.00"), Decimal("700.00"), 1))
        crossing = self.bill("50000", vendor=party).deduct_tds()
        self.assertEqual((crossing.base, crossing.amount, crossing.covered_bills.count()),
                         (Decimal("70000.00"), Decimal("1400.00"), 2))

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


class QuarterlyReturnTests(TdsTestCase):
    """
    One bill of 35,000 to a contractor on 10 June: 700 deducted under 194C,
    owed in the April-June quarter. Reversed on 5 July. Each quarter's return
    lists what its books say: April-June 700, July-September the reversal,
    -700 (calc_stat/o58_tds_return.py).
    """

    Q1 = (datetime.date(2026, 4, 1), datetime.date(2026, 6, 30))
    Q2 = (datetime.date(2026, 7, 1), datetime.date(2026, 9, 30))

    def booked(self, start, end):
        rows = JournalLine.objects.filter(account=self.tds_payable, entry__posted=True,
                                          entry__date__range=(start, end)).aggregate(d=Sum("debit"), c=Sum("credit"))
        return (rows["c"] or Decimal("0")) - (rows["d"] or Decimal("0"))

    def listed(self, start, end):
        from .tds import tds_return

        return sum((row["amount"] for row in tds_return(start, end)), Decimal("0"))

    def test_a_quarters_return_still_lists_what_its_books_hold_after_a_later_reversal(self):
        deduction = self.bill("35000", vendor=self.contractor(pan="AAACL1234F")).deduct_tds()
        self.assertEqual(deduction.amount, Decimal("700"))
        deduction.reverse(on_date=datetime.date(2026, 7, 5))
        self.assertEqual((self.listed(*self.Q1), self.booked(*self.Q1)), (Decimal("700"), Decimal("700")))
        self.assertEqual((self.listed(*self.Q2), self.booked(*self.Q2)), (Decimal("-700"), Decimal("-700")))
        from .tds import tds_return

        (row,) = tds_return(*self.Q2)
        self.assertEqual((row["date"], row["reverses"], row["base"], row["challan"]),
                         (datetime.date(2026, 7, 5), datetime.date(2026, 6, 10), Decimal("-35000.00"), ""))

    def test_over_the_api_as_the_controller(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        self.bill("35000", vendor=self.contractor(pan="AAACL1234F")).deduct_tds().reverse(
            on_date=datetime.date(2026, 7, 5))
        user = User.objects.create_user("controller")
        user.groups.add(Group.objects.get(name="Controller"))
        client = APIClient()
        client.force_authenticate(user)
        url = "/api/purchasing/tds-deductions/quarter/"
        [q1] = client.get(url, {"from": "2026-04-01", "to": "2026-06-30"}).json()
        [q2] = client.get(url, {"from": "2026-07-01", "to": "2026-09-30"}).json()
        self.assertEqual((q1["amount"], q2["amount"], q2["reverses"]), ("700.00", "-700.00", "2026-06-10"))

    def test_reversed_within_its_quarter_it_is_in_neither(self):
        deduction = self.bill("35000", vendor=self.contractor(pan="AAACL1234F")).deduct_tds()
        deduction.reverse(on_date=datetime.date(2026, 6, 20))
        self.assertEqual((self.listed(*self.Q1), self.listed(*self.Q2)), (Decimal("0"), Decimal("0")))


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


class UndoingTakesTheDecisionRightTests(TdsTestCase):
    """
    O128: a TDS deduction's reverse and a challan's void took the right to
    record one (add_), so anyone who may record one may undo one. They take
    the bill poster's right now; goods failed back to a vendor take the
    inspection's decision right, not the right to record one.
    """

    ROUTES = (
        ("/api/purchasing/tds-deductions/999999/reverse/", "tdsdeduction"),
        ("/api/purchasing/tds-challans/999999/void/", "tdschallan"),
        ("/api/purchasing/goods-receipts/999999/reject/", "goodsreceipt"),
        ("/api/purchasing/goods-receipts/999999/accept/", "goodsreceipt"),
    )

    def test_the_right_to_record_one_does_not_undo_it(self):
        from django.contrib.auth.models import Permission, User
        from rest_framework.test import APIClient

        through = []
        for index, (url, model) in enumerate(self.ROUTES):
            user = User.objects.create_user(f"recorder-{index}")
            user.user_permissions.add(*Permission.objects.filter(
                content_type__app_label="purchasing",
                codename__in=[f"add_{model}", f"view_{model}", "add_receiptinspection", "view_receiptinspection"]))
            client = APIClient()
            client.force_authenticate(User.objects.get(pk=user.pk))
            status = client.post(url, {}, format="json").status_code
            if status != 403:
                through.append(f"{url} -> {status}")
        self.assertEqual(through, [])
class ChallanOnTheStatementTests(TdsTestCase):
    """
    700 deducted on 10 June and paid over by challan on 7 July from the bank.
    July's statement: opening 0, one line of -700, closing -700; the books say
    -700 too. The line is the challan: matched to it, not posted again, which
    took the bank to -1,400 (calc_stat/o62_statement.py).
    """

    def setUp(self):
        super().setUp()
        from apps.accounting.models import BankStatement, BankStatementLine

        self.bill("35000", vendor=self.contractor(pan="AAACL1234F")).deduct_tds()
        self.challan = TdsChallan.pay(self.contract, datetime.date(2026, 6, 1), datetime.date(2026, 7, 7), self.bank,
                                      "00001", "0510002")
        self.july = BankStatement.objects.create(bank_account=self.bank, start_date=datetime.date(2026, 7, 1),
                                                 end_date=datetime.date(2026, 7, 31), opening_balance=Decimal("0"),
                                                 closing_balance=Decimal("-700"))
        self.line = BankStatementLine.objects.create(statement=self.july, date=datetime.date(2026, 7, 7),
                                                     amount=Decimal("-700"))

    def test_a_challan_on_the_bank_statement_can_be_explained_and_closed(self):
        # Unmatched, the challan reads as not yet presented and the line as unexplained, as a payment does.
        before = self.july.reconciliation()
        self.assertEqual((before["unpresented_total"], len(before["unresolved_lines"])), (Decimal("-700.00"), 1))
        self.line.match_entry(self.challan.journal_entry)
        report = self.july.reconciliation()
        self.assertEqual((report["difference"], report["unresolved_lines"], self.balance(self.tds_payable),
                          self.balance(self.bank)), (Decimal("0"), [], Decimal("0"), Decimal("-700")))
        self.july.close()

    def test_the_obvious_one_is_matched_by_itself(self):
        self.assertEqual(self.july.auto_match(), [self.line])
        self.line.refresh_from_db()
        self.assertEqual(self.line.booked_entry_id, self.challan.journal_entry_id)

    def test_a_challan_returned_unpaid_is_two_lines_matched(self):
        from apps.accounting.models import BankStatementLine

        self.challan.void(on_date=datetime.date(2026, 7, 9))
        back = BankStatementLine.objects.create(statement=self.july, date=datetime.date(2026, 7, 9),
                                                amount=Decimal("700"))
        self.july.closing_balance = Decimal("0")
        self.july.save()
        self.line.match_entry(self.challan.journal_entry)
        back.match_entry(TdsChallan.objects.get(pk=self.challan.pk).voided_entry)
        self.assertEqual((self.july.reconciliation()["difference"], self.balance(self.tds_payable)),
                         (Decimal("0"), Decimal("-700")))

    def test_what_is_refused(self):
        from apps.accounting.models import JournalEntry, JournalLine, Payment

        other = Account.objects.create(code="1020", name="Other bank", account_type=AccountType.ASSET,
                                       holds_money=True)
        by_hand = JournalEntry.objects.create(date=datetime.date(2026, 7, 7), memo="Transfer")
        JournalLine.objects.create(entry=by_hand, account=self.bank, credit=Decimal("700"))
        JournalLine.objects.create(entry=by_hand, account=other, debit=Decimal("700"))
        by_hand.post()
        paid = Payment.objects.create(party=self.vendor, direction="disbursement", payment_date=datetime.date(2026, 7, 7),
                                      amount=Decimal("700"), bank_account=self.bank, counterpart_account=self.payable)
        paid.post()
        for entry, said in ((by_hand, "is not money a document moved"), (paid.journal_entry, "match a payment")):
            with self.subTest(entry=entry), self.assertRaisesMessage(ValidationError, said):
                self.line.match_entry(entry)
        self.line.amount = Decimal("-701")
        self.line.save()
        with self.assertRaisesMessage(ValidationError, "moved -700.00"):
            self.line.match_entry(self.challan.journal_entry)
        self.line.amount = Decimal("-700")
        self.line.save()
        self.line.match_entry(self.challan.journal_entry)
        with self.assertRaisesMessage(ValidationError, "already explained"):
            self.line.match_entry(self.challan.journal_entry)
        self.line.unmatch()
        self.assertIsNone(self.line.booked_entry_id)


class TheChallanPaysFromTheAccountTheDeductionWentToTests(TdsTestCase):
    """Review F3, the TDS mirror: the section moved to another payable account after the deduction."""

    def test_the_section_moved_after_the_deduction(self):
        from apps.purchasing.tds import deduct

        deduct(self.bill("35000", vendor=self.contractor(pan="AAAPL1234C")))
        before = self.contract.payable_account
        self.contract.payable_account = Account.objects.create(code="2251", name="TDS payable (new)",
                                                               account_type=AccountType.LIABILITY)
        self.contract.save()
        TdsChallan.pay(self.contract, DAY, DAY, self.bank, "00042", "0510308")

        def balance(account):
            rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
                debit=Sum("debit"), credit=Sum("credit"))
            return (rows["credit"] or Decimal("0")) - (rows["debit"] or Decimal("0"))

        self.assertEqual((balance(before), balance(self.contract.payable_account)), (Decimal("0"), Decimal("0")))


def _migrated_to_the_leaves():
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


# Slow: it unwinds purchasing's latest migration and replays it.
@tag("migration")
class DeductionsAlreadyPostedMigrationTests(TransactionTestCase):
    """
    purchasing 0074: a deduction posted before it kept posted_account keeps the account its own entry
    credited, never the one its section names at the upgrade. Every deduction has an entry (the column
    is not nullable) and an amount over nothing, so there is no unposted or entry-less row to build.
    From calc_fix_mig/scenarios.py:

      D1  1,000.00 on B-1, Dr 2100 / Cr 2250, while 194C read 2250          -> 2250
      D2    500.00 on B-2, Dr 2100 / Cr 2250, reversed Dr 2250 / Cr 2100    -> 2250, not the reversal's 2100
      --  194C moved to 2251: D1 and D2 are the rows whose section moved after they posted
      D3    700.00 on B-3, Dr 2100 / Cr 2251                                -> 2251
      D4    300.00 on B-4 under 194J, wired to the bill's own 2100: Dr 2100 / Cr 2100 -> 2100
          (the old read took a credit to anything but 2100, found none, and the constraint refused
          the upgrade)
      D5    250.00 on B-5, an entry no deduct() made: Cr 2250 200.00 and Cr 2251 50.00
          -> the upgrade stops and names it (the old read took 2250, a guess); taken off, it runs.
      Back: the column goes, and the four rows and their amounts stay.
    """

    before = [("purchasing", "0073_landing_keeps_where_it_went")]
    after = [("purchasing", "0074_posted_account")]

    def test_each_keeps_the_account_its_own_entry_credited(self):
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        self.addCleanup(_migrated_to_the_leaves)
        apps = executor.loader.project_state(self.before).apps
        # Only purchasing is taken back; the accounts, sections and entries are made as they stand now.
        now = executor.loader.project_state(executor.loader.graph.leaf_nodes()).apps
        account = now.get_model("accounting", "Account")
        payable, owed, moved = (account.objects.create(code=code, name=code, account_type="liability")
                                for code in ("2100", "2250", "2251"))
        vendor = now.get_model("core", "Party").objects.create(code="V-C", name="Loom Fitters")
        section = now.get_model("accounting", "TdsSection")
        contract = section.objects.create(code="194C", name="Contractors", payable_account_id=owed.pk)
        fees = section.objects.create(code="194J", name="Fees", payable_account_id=payable.pk)
        entry_model, line = now.get_model("accounting", "JournalEntry"), now.get_model("accounting", "JournalLine")
        bill_model, deduction = apps.get_model("purchasing", "Bill"), apps.get_model("purchasing", "TdsDeduction")

        def entry(memo, *lines):
            made = entry_model.objects.create(date=DAY, memo=memo, posted=True)
            for onto, debit, credit in lines:
                line.objects.create(entry=made, account=onto, debit=Decimal(debit), credit=Decimal(credit))
            return made

        def deducted(number, under, amount, *lines):
            bill = bill_model.objects.create(vendor_id=vendor.pk, bill_date=DAY, payable_account_id=payable.pk,
                                             number=number, posted=True)
            return deduction.objects.create(
                bill=bill, section_id=under.pk, base=Decimal(amount) * 100, rate_percent=Decimal("1"),
                amount=Decimal(amount), date=DAY, journal_entry_id=entry(f"TDS on {number}", *lines).pk)

        deducted("B-1", contract, "1000", (payable, "1000", "0"), (owed, "0", "1000"))
        d2 = deducted("B-2", contract, "500", (payable, "500", "0"), (owed, "0", "500"))
        d2.reversed_entry_id = entry("Reversed", (owed, "500", "0"), (payable, "0", "500")).pk
        d2.save()
        section.objects.filter(pk=contract.pk).update(payable_account_id=moved.pk)
        deducted("B-3", contract, "700", (payable, "700", "0"), (moved, "0", "700"))
        deducted("B-4", fees, "300", (payable, "300", "0"), (payable, "0", "300"))
        d5 = deducted("B-5", contract, "250", (payable, "250", "0"), (owed, "0", "200"), (moved, "0", "50"))

        with self.assertRaisesMessage(RuntimeError, f"TDS deduction {d5.pk} of 250.00 on bill {d5.bill_id} "
                                                    f"(2026-06-10): its entry {d5.journal_entry_id} credits 2 lines"):
            MigrationExecutor(connection).migrate(self.after)
        deduction.objects.filter(pk=d5.pk).delete()
        MigrationExecutor(connection).migrate(self.after)
        kept = executor.loader.project_state(self.after).apps.get_model("purchasing", "TdsDeduction")
        self.assertEqual(sorted(kept.objects.values_list("amount", "posted_account_id")),
                         [(Decimal("300.00"), payable.pk), (Decimal("500.00"), owed.pk),
                          (Decimal("700.00"), moved.pk), (Decimal("1000.00"), owed.pk)])

        MigrationExecutor(connection).migrate(self.before)
        back = MigrationExecutor(connection).loader.project_state(self.before).apps.get_model(
            "purchasing", "TdsDeduction")
        self.assertNotIn("posted_account", {field.name for field in back._meta.get_fields()})
        self.assertEqual(sorted(back.objects.values_list("amount", flat=True)),
                         [Decimal("300.00"), Decimal("500.00"), Decimal("700.00"), Decimal("1000.00")])


class DatedRateTests(TdsTestCase):
    """
    O233. 194C at 2% from 1 April 2000; the law makes it 1.5% from 1 February
    2026. Bills of 40,000 (past the 30,000 single limit, taxed alone): dated
    15 January, 800 at 2%; dated 10 February, 600 at 1.5% (calc_fix_purch/o233.py).
    Each deduction keeps the row it was taken at; a rate taken at is a fact.
    """

    def setUp(self):
        super().setUp()
        from apps.accounting.models import TdsRate

        self.party = self.contractor(pan="AAAPL1234C")
        self.february = TdsRate.objects.create(section=self.contract, applies_from=datetime.date(2026, 2, 1),
                                               rate_percent=Decimal("1.5"), no_pan_rate_percent=Decimal("20"),
                                               confirmed_on=datetime.date(2026, 1, 20))

    def test_each_deduction_takes_the_rate_standing_on_its_day_and_keeps_it(self):
        january = self.bill("40000", day=datetime.date(2026, 1, 15), vendor=self.party).deduct_tds()
        february = self.bill("40000", day=datetime.date(2026, 2, 10), vendor=self.party).deduct_tds()
        self.assertEqual([(row.amount, row.rate_percent, row.rate) for row in (january, february)],
                         [(Decimal("800.00"), Decimal("2.0000"), self.contract_rate),
                          (Decimal("600.00"), Decimal("1.5000"), self.february)])
        self.february.rate_percent = Decimal("1")
        with self.assertRaisesMessage(ValidationError, "Bills were deducted at this rate; a new rate is a new row"):
            self.february.save()
        with self.assertRaisesMessage(ValidationError, "Bills were deducted at this rate; it stays."):
            self.contract_rate.delete()

    def test_a_rate_not_confirmed_deducts_nothing(self):
        from apps.accounting.models import TdsRate

        TdsRate.objects.create(section=self.contract, applies_from=datetime.date(2026, 3, 1),
                               rate_percent=Decimal("1"), no_pan_rate_percent=Decimal("20"))
        # Refused as the bill posts (the integrator's decision, g22), and so never deducted at nothing.
        with self.assertRaisesMessage(ValidationError, "no confirmed 194C rate stands on 10 Mar 2026"):
            self.bill("40000", day=datetime.date(2026, 3, 10), vendor=self.party)

    def test_the_controller_confirms_a_rate_and_the_ap_manager_may_not(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        from apps.accounting.models import TdsRate

        call_command("setup_roles", verbosity=0)
        clients = {}
        for role in ("Controller", "AP Manager"):
            user = User.objects.create_user(role.replace(" ", "").lower())
            user.groups.add(Group.objects.get(name=role))
            clients[role] = APIClient()
            clients[role].force_authenticate(user)
        made = clients["Controller"].post("/api/accounting/tds-rates/", {
            "section": self.contract.pk, "applies_from": "2026-04-01", "rate_percent": "1",
            "no_pan_rate_percent": "20", "authority": "Finance Act 2026"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        url = f"/api/accounting/tds-rates/{made.json()['id']}/confirm/"
        self.assertEqual(clients["AP Manager"].post(url, {}, format="json").status_code, 403)
        self.assertEqual(clients["AP Manager"].get("/api/accounting/tds-rates/").status_code, 200)
        confirmed = clients["Controller"].post(url, {}, format="json")
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertIsNotNone(TdsRate.objects.get(pk=made.json()["id"]).confirmed_on)



@tag("migration")
class TdsRatesByDateMigrationTests(TransactionTestCase):
    """accounting 0030 makes each section's rate a dated row, from 1 April 2000, not confirmed (O233)."""

    BEFORE = [("accounting", "0029_tcs_section_by_date")]
    AFTER = [("accounting", "0030_tds_rates_by_date")]

    def migrate(self, target):
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor

    def test_each_sections_rate_is_kept_as_a_row_to_confirm(self):
        self.addCleanup(lambda: self.migrate(self.migrate([]).loader.graph.leaf_nodes()))
        executor = self.migrate(self.BEFORE)
        old = executor.loader.project_state(self.BEFORE).apps.get_model("accounting", "TdsSection")
        contract = old.objects.create(code="194C", name="Contractors", rate_percent=Decimal("2"),
                                      no_pan_rate_percent=Decimal("20"))
        goods = old.objects.create(code="194Q", name="Goods", rate_percent=Decimal("0.1"),
                                   no_pan_rate_percent=Decimal("5"), mode="excess", annual_threshold=Decimal("5000000"))
        executor = self.migrate(self.AFTER)
        Rate = executor.loader.project_state(self.AFTER).apps.get_model("accounting", "TdsRate")
        self.assertEqual(
            sorted((row.section_id, str(row.applies_from), row.rate_percent, row.no_pan_rate_percent, row.confirmed_on)
                   for row in Rate.objects.all()),
            [(contract.pk, "2000-04-01", Decimal("2.0000"), Decimal("20.0000"), None),
             (goods.pk, "2000-04-01", Decimal("0.1000"), Decimal("5.0000"), None)])


class TcsTheSupplierCollectedTests(TdsTestCase):
    """
    O234. A delivery van bought for 12,00,000; the dealer collects TCS of 1%
    under 206C(1F): 12,000, and its invoice totals 12,12,000. The plant owes
    the dealer 12,12,000 and claims the 12,000 in its own return. A quarter
    given back takes back 3,000; the rest takes back the 9,000 left
    (calc_fix_purch/o234.py).
    """

    def setUp(self):
        super().setUp()
        self.tcs_receivable = Account.objects.create(code="1460", name="TCS receivable", account_type=AccountType.ASSET)

    def van(self, **tcs):
        bill = Bill.objects.create(vendor=self.vendor, bill_date=DAY, payable_account=self.payable, reference="INV-V1",
                                   **tcs)
        BillLine.objects.create(bill=bill, description="Delivery van", quantity=Decimal("1"),
                                unit_price=Decimal("1200000"), expense_account=self.expense)
        return bill

    def test_the_bill_owes_what_the_supplier_invoiced_and_the_plant_claims_the_tcs(self):
        bill = self.van(tcs_amount=Decimal("12000"), tcs_section="206C(1F)", tcs_account=self.tcs_receivable)
        bill.post()
        bill = Bill.objects.get(pk=bill.pk)
        self.assertEqual((bill.total(), bill.amount_due()), (Decimal("1212000.00"), Decimal("1212000.00")))
        self.assertEqual((self.balance(self.tcs_receivable), self.balance(self.payable)),
                         (Decimal("12000.00"), Decimal("-1212000.00")))
        quarter = bill.create_debit_note(quantities={bill.lines.get(): Decimal("0.25")})
        self.assertEqual((quarter.tcs_amount, quarter.total(), self.balance(self.tcs_receivable)),
                         (Decimal("3000.00"), Decimal("303000.00"), Decimal("9000.00")))
        rest = bill.create_debit_note(quantities={bill.lines.get(): Decimal("0.75")})
        self.assertEqual((rest.tcs_amount, self.balance(self.tcs_receivable), self.balance(self.payable)),
                         (Decimal("9000.00"), Decimal("0.00"), Decimal("0.00")))
        self.assertEqual(Bill.objects.get(pk=bill.pk).amount_due(), Decimal("0.00"))

    def test_the_refusals(self):
        for given, field, said in (
                ({"tcs_amount": Decimal("12000")}, "tcs_account", "Name the TCS receivable account"),
                ({"tcs_amount": Decimal("-1"), "tcs_account": self.tcs_receivable}, "tcs_amount", "is not negative"),
                ({"tcs_amount": Decimal("12000"), "tcs_account": self.expense}, "tcs_account", "is not an asset account")):
            with self.subTest(field=field), self.assertRaisesMessage(ValidationError, said):
                self.van(**given)
        bill = self.van(tcs_amount=Decimal("12000"), tcs_account=self.tcs_receivable)
        bill.post()
        note = bill.create_debit_note(quantities={bill.lines.get(): Decimal("0.25")})
        with self.assertRaisesMessage(ValidationError, "a debit note does not take back"):
            Bill.objects.create(vendor=self.vendor, bill_date=DAY, payable_account=self.payable, debits=bill,
                                tcs_amount=Decimal("9000.01"))
        self.assertEqual(note.tcs_account, self.tcs_receivable)

    def test_the_ap_manager_books_it_through_the_api(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        ap = User.objects.create_user("ap")
        ap.groups.add(Group.objects.get(name="AP Manager"))
        client = APIClient()
        client.force_authenticate(ap)
        made = client.post("/api/purchasing/bills/", {
            "vendor": self.vendor.pk, "bill_date": str(DAY), "reference": "INV-V2", "payable_account": self.payable.pk,
            "tcs_amount": "12000", "tcs_section": "206C(1F)", "tcs_account": self.tcs_receivable.pk}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        line = client.post("/api/purchasing/bill-lines/", {
            "bill": made.json()["id"], "description": "Delivery van", "quantity": "1", "unit_price": "1200000",
            "expense_account": self.expense.pk}, format="json")
        self.assertEqual(line.status_code, 201, line.content)
        posted = client.post(f"/api/purchasing/bills/{made.json()['id']}/post_bill/", {}, format="json")
        self.assertEqual(posted.status_code, 200, posted.content)
        self.assertEqual((posted.json()["tcs_amount"], posted.json()["total"]), ("12000.00", "1212000.00"))
        self.assertEqual(self.balance(self.tcs_receivable), Decimal("12000.00"))


class NoConfirmedRateRefusesThePostingTests(TdsTestCase):
    """
    Integrator's decision (g22): a bill its vendor's section taxes, with no
    confirmed rate on its day, is refused at posting, not deducted at nothing
    (section 40(a)(ia)). 194C: one bill over 30,000 is taxed; 20,000 is not.
    """

    def setUp(self):
        super().setUp()
        from apps.accounting.models import TdsRate

        self.party = self.contractor(pan="AAAPL1234C")
        TdsRate.objects.create(section=self.contract, applies_from=datetime.date(2026, 3, 1),
                               rate_percent=Decimal("1"), no_pan_rate_percent=Decimal("20"))

    def test_a_taxed_bill_waits_for_the_accountant_and_an_untaxed_one_posts(self):
        with self.assertRaisesMessage(ValidationError, "no confirmed 194C rate stands on 10 Mar 2026: nothing would "
                                                       "be deducted. The accountant confirms it under TDS rates"):
            self.bill("40000", day=datetime.date(2026, 3, 10), vendor=self.party)
        self.assertEqual(self.bill("20000", day=datetime.date(2026, 3, 10), vendor=self.party).posted, True)

    def test_a_deduction_is_not_dated_before_its_bill(self):
        bill = self.bill("40000", day=datetime.date(2026, 2, 10), vendor=self.party)
        with self.assertRaisesMessage(ValidationError, "is not deducted from"):
            bill.deduct_tds(on_date=datetime.date(2026, 2, 9))
        self.assertEqual(bill.deduct_tds(on_date=datetime.date(2026, 2, 12)).date, datetime.date(2026, 2, 12))
