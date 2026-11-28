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
        # The company's TAN: a challan and a return carry it, and are refused without it (g24 decision 3).
        from apps.core.models import Company

        Company.objects.filter(pk=Company.get().pk).update(tan="CALA12345B")
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
        party = self.contractor(pan="AAAPL1234C", tds_rate_percent=Decimal("1"), tds_rate_reference="Individual",
                                tds_rate_basis="deductee")
        deduction = self.bill("35000", vendor=party).deduct_tds()
        self.assertEqual((deduction.amount, deduction.rate_percent), (Decimal("350.00"), Decimal("1.0000")))
        # A deductee's own class is not a lower deduction: no certificate (O597).
        self.assertEqual(deduction.certificate, "")


class CertificateTests(TdsTestCase):
    """
    O247: a lower-deduction certificate (section 197) stands from its first day to its last. Undated, a lapsed one
    went on deducting at its rate. 194C at 2%, a certificate at 0.5% from 1 June to 30 September 2026, one bill of
    35,000 a day, each deducted in turn (g46_calc/o247.py): 700.00 on 31 May, 175.00 on the first and the last
    day, 700.00 on 1 October, when the year has passed 1,00,000 with nothing left untaxed.
    """

    FROM, UNTIL = datetime.date(2026, 6, 1), datetime.date(2026, 9, 30)

    def holder(self):
        return self.contractor(pan="AAAPL1234C", tds_rate_percent=Decimal("0.5"), tds_rate_reference="197/2026/14",
                               tds_rate_basis="certificate", tds_rate_from=self.FROM, tds_rate_until=self.UNTIL,
                               tds_rate_limit=Decimal("500000"))

    def profile(self, code, **terms):
        party = Party.objects.create(code=code, name=f"Vendor {code}")
        return PartyTaxProfile.objects.create(party=party, pan="AAAPD1234C", tds_section=self.contract,
                                              tds_rate_percent=Decimal("0.5"), tds_rate_reference="197/2026/14",
                                              **terms)

    def test_a_rate_says_what_it_rests_on(self):
        with self.assertRaisesMessage(ValidationError, "the deductee's own class"):
            self.profile("V-B")

    def test_a_certificate_names_both_its_days(self):
        with self.assertRaisesMessage(ValidationError, "give both"):
            self.profile("V-D", tds_rate_basis="certificate", tds_rate_from=self.FROM)

    def test_a_certificate_ending_before_it_starts(self):
        with self.assertRaisesMessage(ValidationError, "cannot end on 31 May 2026, before it starts on 01 Jun 2026"):
            self.profile("V-E", tds_rate_basis="certificate", tds_rate_from=self.FROM,
                         tds_rate_until=datetime.date(2026, 5, 31))

    def test_a_certificate_is_for_one_financial_year(self):
        with self.assertRaisesMessage(ValidationError, "starts in 2025-26, so it ends by 31 Mar 2026"):
            self.profile("V-Y", tds_rate_basis="certificate", tds_rate_from=datetime.date(2026, 3, 1),
                         tds_rate_until=datetime.date(2026, 4, 30))

    def test_a_deductees_class_has_no_dates(self):
        with self.assertRaisesMessage(ValidationError, "has no dates"):
            self.profile("V-C", tds_rate_basis="deductee", tds_rate_until=self.UNTIL)

    def test_a_basis_and_dates_without_a_rate(self):
        party = Party.objects.create(code="V-N", name="No rate")
        with self.assertRaisesMessage(ValidationError, "belong to a rate of its own"):
            PartyTaxProfile.objects.create(party=party, pan="AAAPN1234C", tds_section=self.contract,
                                           tds_rate_basis="certificate", tds_rate_from=self.FROM,
                                           tds_rate_until=self.UNTIL)
        with self.assertRaisesMessage(ValidationError, "an amount belong to a rate of its own"):
            PartyTaxProfile.objects.create(party=party, pan="AAAPN1234C", tds_section=self.contract,
                                           tds_rate_limit=Decimal("100000"))

    def test_deducted_at_the_certificate_only_between_its_days(self):
        party = self.holder()
        made = [self.bill("35000", day=day, vendor=party).deduct_tds()
                for day in (datetime.date(2026, 5, 31), self.FROM, self.UNTIL, datetime.date(2026, 10, 1))]
        self.assertEqual([(row.base, row.rate_percent, row.amount, row.rate_id, row.certificate) for row in made], [
            (Decimal("35000.00"), Decimal("2.0000"), Decimal("700.00"), self.contract_rate.pk, ""),
            (Decimal("35000.00"), Decimal("0.5000"), Decimal("175.00"), None, "197/2026/14"),
            (Decimal("35000.00"), Decimal("0.5000"), Decimal("175.00"), None, "197/2026/14"),
            (Decimal("35000.00"), Decimal("2.0000"), Decimal("700.00"), self.contract_rate.pk, ""),
        ])
        # The return marks the lower deductions with the certificate each was kept with (O597), whatever the
        # profile names by the time it is filed.
        PartyTaxProfile.objects.filter(party=party).update(tds_rate_reference="197/2027/03")
        from .tds import tds_return

        self.assertEqual([(row["date"], row["amount"], row["certificate"])
                          for row in tds_return(datetime.date(2026, 4, 1), datetime.date(2027, 3, 31))], [
            (datetime.date(2026, 5, 31), Decimal("700.00"), ""),
            (self.FROM, Decimal("175.00"), "197/2026/14"),
            (self.UNTIL, Decimal("175.00"), "197/2026/14"),
            (datetime.date(2026, 10, 1), Decimal("700.00"), ""),
        ])

    def test_deducted_on_its_payment_day_at_what_stands_that_day(self):
        # Deducted as it is paid, the rate is the payment day's (O233): a bill inside the certificate paid after it
        # lapsed is deducted at the section's.
        bill = self.bill("35000", day=self.UNTIL, vendor=self.holder())
        deduction = bill.deduct_tds(on_date=datetime.date(2026, 10, 5))
        self.assertEqual((deduction.rate_percent, deduction.amount), (Decimal("2.0000"), Decimal("700.00")))

    def test_a_bill_past_the_certificate_needs_the_sections_rate_to_post(self):
        # The mirror at posting (refuse_posting_without_a_rate): any rate of its own let every bill through, so a
        # bill after the certificate lapsed posted where the section had no confirmed rate, and was then deducted
        # at the lapsed 0.5%.
        fees = TdsSection.objects.create(code="194J", name="Professional fees", mode="whole",
                                         single_threshold=Decimal("30000"), annual_threshold=Decimal("100000"),
                                         payable_account=self.tds_payable)
        party = self.holder()
        PartyTaxProfile.objects.filter(party=party).update(tds_section=fees)
        party = Party.objects.get(pk=party.pk)
        self.assertEqual(self.bill("35000", day=self.UNTIL, vendor=party).deduct_tds().amount, Decimal("175.00"))
        with self.assertRaisesMessage(ValidationError, "no confirmed 194J rate stands on 01 Oct 2026"):
            self.bill("35000", day=datetime.date(2026, 10, 1), vendor=party)

    def test_a_rate_entered_before_the_basis_was_asked(self):
        # Upgraded, it keeps its old meaning (every day) until it says, and Health lists it; its next save asks.
        from apps.web.health import _own_tds_rates_without_a_basis

        party = self.holder()
        PartyTaxProfile.objects.filter(party=party).update(tds_rate_basis="", tds_rate_from=None,
                                                           tds_rate_until=None)
        party = Party.objects.get(pk=party.pk)
        late = self.bill("35000", day=datetime.date(2026, 10, 1), vendor=party).deduct_tds()
        self.assertEqual((late.rate_percent, late.amount), (Decimal("0.5000"), Decimal("175.00")))
        listed = _own_tds_rates_without_a_basis(None)
        self.assertEqual((listed["ok"], listed["count"], [row["label"] for row in listed["rows"]]),
                         (False, 1, ["V-C - Loom Fitters: 0.5% under 194C, resting on 197/2026/14"]))
        with self.assertRaisesMessage(ValidationError, "the deductee's own class"):
            party.tax_profile.save()
        PartyTaxProfile.objects.filter(party=party).update(tds_rate_basis="deductee")
        self.assertEqual(_own_tds_rates_without_a_basis(None)["count"], 0)


class TheCertificatesAmountTests(TdsTestCase):
    """
    O598: a lower-deduction certificate's rate applies up to the amount it states, the section's past it. 194C at 2%
    (single 30,000, year 1,00,000), a certificate at 0.5% from 1 June to 30 September 2026 for 1,00,000; each bill
    deducted as it posts (calc_g56/o598.py):
      15 Jun 40,000 -> 200 at the certificate (40,000 used);  1 Jul 45,000 -> 225 (85,000 used)
      1 Aug 35,000 -> 15,000 at 0.5% = 75 and 20,000 at 2% = 400: two rows, 475;  1 Sep 30,000 -> 600 at 2%
    The two rows of 1 August are one deduction: reversed together they free 15,000, and deducted again split the
    same. A certificate for 35,050: 35,000 -> 175, then 35,000 -> 50 left, 0.25 at 0.5%, nothing: all 700 at 2%.
    """

    FROM, UNTIL, CERT = datetime.date(2026, 6, 1), datetime.date(2026, 9, 30), "197/2026/14"

    def holder(self, limit="100000", code="V-C"):
        party = Party.objects.create(code=code, name=f"Loom Fitters {code}")
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.VENDOR)
        PartyTaxProfile.objects.create(party=party, pan="AAAPL1234C", tds_section=self.contract,
                                       tds_rate_percent=Decimal("0.5"), tds_rate_reference=self.CERT,
                                       tds_rate_basis="certificate", tds_rate_from=self.FROM,
                                       tds_rate_until=self.UNTIL, tds_rate_limit=Decimal(limit))
        return party

    def rows(self, party):
        from .tds import TdsDeduction

        return [(row.date, row.base, row.rate_percent, row.amount, row.rate_id, row.certificate)
                for row in TdsDeduction.objects.filter(bill__vendor=party, reversed_on__isnull=True)
                .order_by("date", "pk")]

    def four_bills(self, party):
        return [self.bill(amount, day=day, vendor=party).deduct_tds()
                for day, amount in [(datetime.date(2026, 6, 15), "40000"), (datetime.date(2026, 7, 1), "45000"),
                                    (datetime.date(2026, 8, 1), "35000"), (datetime.date(2026, 9, 1), "30000")]]

    def test_what_is_refused(self):
        def profile(code, **terms):
            party = Party.objects.create(code=code, name=f"Vendor {code}")
            return PartyTaxProfile.objects.create(party=party, pan="AAAPD1234C", tds_section=self.contract,
                                                  tds_rate_reference=self.CERT, **terms)

        dated = {"tds_rate_basis": "certificate", "tds_rate_from": self.FROM, "tds_rate_until": self.UNTIL}
        with self.assertRaisesMessage(ValidationError, "applies up to the amount it states"):
            profile("V-R1", tds_rate_percent=Decimal("0.5"), **dated)
        with self.assertRaisesMessage(ValidationError, "more than nothing"):
            profile("V-R2", tds_rate_percent=Decimal("0.5"), tds_rate_limit=Decimal("0"), **dated)
        with self.assertRaisesMessage(ValidationError, "A deductee's own class has no amount"):
            profile("V-R3", tds_rate_percent=Decimal("1"), tds_rate_basis="deductee", tds_rate_limit=Decimal("100000"))
        # A nil rate's amount is asked as any rate's: what is paid under it is recorded at nothing and counted (O618).
        with self.assertRaisesMessage(ValidationError, "applies up to the amount it states"):
            profile("V-R4", tds_rate_percent=Decimal("0"), **dated)
        self.assertEqual(profile("V-R5", tds_rate_percent=Decimal("0"), tds_rate_limit=Decimal("100000"), **dated)
                         .tds_rate_limit, Decimal("100000"))

    def test_past_the_amount_the_rest_is_deducted_at_the_sections_rate(self):
        from .tds import certificate_left, tds_return

        party = self.holder()
        made = self.four_bills(party)
        rate = self.contract_rate.pk
        self.assertEqual(self.rows(party), [
            (datetime.date(2026, 6, 15), Decimal("40000.00"), Decimal("0.5000"), Decimal("200.00"), None, self.CERT),
            (datetime.date(2026, 7, 1), Decimal("45000.00"), Decimal("0.5000"), Decimal("225.00"), None, self.CERT),
            (datetime.date(2026, 8, 1), Decimal("15000.00"), Decimal("0.5000"), Decimal("75.00"), None, self.CERT),
            (datetime.date(2026, 8, 1), Decimal("20000.00"), Decimal("2.0000"), Decimal("400.00"), rate, ""),
            (datetime.date(2026, 9, 1), Decimal("30000.00"), Decimal("2.0000"), Decimal("600.00"), rate, ""),
        ])
        # deduct() hands back the part at the certificate; the bill owes the net of both.
        third = made[2]
        self.assertEqual((third.amount, [part.amount for part in third.parts()]),
                         (Decimal("75.00"), [Decimal("75.00"), Decimal("400.00")]))
        self.assertEqual(Bill.objects.get(pk=third.bill_id).amount_due(), Decimal("34525.00"))
        self.assertEqual(self.balance(self.tds_payable), Decimal("-1500.00"))
        self.assertEqual(certificate_left(party, self.contract, datetime.date(2026, 9, 1)), Decimal("0.00"))
        # The return lists each row, the lower deduction under its certificate and the rest at the section's.
        self.assertEqual([(row["date"], row["base"], row["rate_percent"], row["amount"], row["certificate"])
                          for row in tds_return(datetime.date(2026, 7, 1), datetime.date(2026, 9, 30))
                          if row["vendor"] == party.name], [
            (datetime.date(2026, 7, 1), Decimal("45000.00"), Decimal("0.5000"), Decimal("225.00"), self.CERT),
            (datetime.date(2026, 8, 1), Decimal("15000.00"), Decimal("0.5000"), Decimal("75.00"), self.CERT),
            (datetime.date(2026, 8, 1), Decimal("20000.00"), Decimal("2.0000"), Decimal("400.00"), ""),
            (datetime.date(2026, 9, 1), Decimal("30000.00"), Decimal("2.0000"), Decimal("600.00"), ""),
        ])

    def test_the_two_rows_are_reversed_together_and_deducted_again_the_same(self):
        from .tds import TdsDeduction, certificate_left

        party = self.holder()
        made = self.four_bills(party)
        at_certificate, past_it = made[2].parts()
        # Reversed by its second row, the first goes too: one part alone would leave 15,000 counted as used.
        past_it.reverse()
        at_certificate.refresh_from_db()
        self.assertIsNotNone(at_certificate.reversed_entry_id)
        with self.assertRaisesMessage(ValidationError, "already been reversed"):
            TdsDeduction.objects.get(pk=at_certificate.pk).reverse()
        self.assertEqual(certificate_left(party, self.contract, datetime.date(2026, 9, 1)), Decimal("15000.00"))
        again = Bill.objects.get(pk=at_certificate.bill_id).deduct_tds()
        self.assertEqual([(part.base, part.amount, part.certificate) for part in again.parts()],
                         [(Decimal("15000.00"), Decimal("75.00"), self.CERT), (Decimal("20000.00"), Decimal("400.00"), "")])
        self.assertEqual(self.balance(self.tds_payable), Decimal("-1500.00"))

    def test_paid_over_together_and_freed_together(self):
        party = self.holder()
        made = self.four_bills(party)
        challan = TdsChallan.pay(self.contract, datetime.date(2026, 8, 1), datetime.date(2026, 9, 7), self.bank,
                                 "00008", "0510002")
        self.assertEqual(challan.amount, Decimal("475.00"))
        at_certificate, past_it = made[2].parts()
        with self.assertRaisesMessage(ValidationError, "void the challan first"):
            at_certificate.reverse()
        challan.void(on_date=datetime.date(2026, 9, 9))
        # Reversed by its first row this time, the second goes too.
        at_certificate.refresh_from_db()
        at_certificate.reverse(on_date=datetime.date(2026, 9, 9))
        past_it.refresh_from_db()
        self.assertEqual(past_it.reversed_entry.date, datetime.date(2026, 9, 9))
        self.assertEqual(self.rows(party)[2:], [
            (datetime.date(2026, 9, 1), Decimal("30000.00"), Decimal("2.0000"), Decimal("600.00"),
             self.contract_rate.pk, "")])

    def test_a_part_that_comes_to_nothing_at_the_certificate_goes_at_the_sections_rate(self):
        party = self.holder(limit="35050")
        self.bill("35000", day=datetime.date(2026, 6, 15), vendor=party).deduct_tds()
        self.bill("35000", day=datetime.date(2026, 7, 1), vendor=party).deduct_tds()
        self.assertEqual(self.rows(party), [
            (datetime.date(2026, 6, 15), Decimal("35000.00"), Decimal("0.5000"), Decimal("175.00"), None, self.CERT),
            (datetime.date(2026, 7, 1), Decimal("35000.00"), Decimal("2.0000"), Decimal("700.00"),
             self.contract_rate.pk, ""),
        ])

    def test_a_rest_that_comes_to_nothing_at_the_sections_rate_is_no_row(self):
        # A certificate for 34,990: 35,000 is 34,990 at 0.5% = 174.95 -> 175, and 10 at 2% = 0.20 -> nothing. A row
        # of nothing is refused by the database (an amount is more than nothing): it is no row.
        party = self.holder(limit="34990")
        made = self.bill("35000", day=datetime.date(2026, 6, 15), vendor=party).deduct_tds()
        self.assertEqual(self.rows(party), [
            (datetime.date(2026, 6, 15), Decimal("34990.00"), Decimal("0.5000"), Decimal("175.00"), None, self.CERT)])
        self.assertEqual(Bill.objects.get(pk=made.bill_id).amount_due(), Decimal("34825.00"))

    def test_the_amount_changed_and_a_new_certificate(self):
        party = self.holder()
        self.bill("40000", day=datetime.date(2026, 6, 15), vendor=party).deduct_tds()
        # Lowered below what is used: nothing is left, the next bill goes at the section's rate whole.
        profile = PartyTaxProfile.objects.get(party=party)
        profile.tds_rate_limit = Decimal("30000")
        profile.save()
        self.bill("45000", day=datetime.date(2026, 7, 1), vendor=Party.objects.get(pk=party.pk)).deduct_tds()
        # A new certificate counts from nothing: the rows deducted at the old one keep its number.
        profile.tds_rate_reference, profile.tds_rate_limit = "197/2026/15", Decimal("50000")
        profile.save()
        self.bill("35000", day=datetime.date(2026, 8, 1), vendor=Party.objects.get(pk=party.pk)).deduct_tds()
        self.assertEqual(self.rows(party), [
            (datetime.date(2026, 6, 15), Decimal("40000.00"), Decimal("0.5000"), Decimal("200.00"), None, self.CERT),
            (datetime.date(2026, 7, 1), Decimal("45000.00"), Decimal("2.0000"), Decimal("900.00"),
             self.contract_rate.pk, ""),
            (datetime.date(2026, 8, 1), Decimal("35000.00"), Decimal("0.5000"), Decimal("175.00"), None,
             "197/2026/15"),
        ])

    def test_a_bill_past_the_amount_needs_the_sections_rate_to_post(self):
        # The mirror at posting (refuse_posting_without_a_rate): inside its amount a certificate needs no section
        # rate, past it the bill would be deducted at nothing on the rest.
        fees = TdsSection.objects.create(code="194J", name="Professional fees", mode="whole",
                                         single_threshold=Decimal("30000"), annual_threshold=Decimal("100000"),
                                         payable_account=self.tds_payable)
        party = self.holder()
        PartyTaxProfile.objects.filter(party=party).update(tds_section=fees)
        party = Party.objects.get(pk=party.pk)
        self.assertEqual(self.bill("40000", day=datetime.date(2026, 6, 15), vendor=party).deduct_tds().amount,
                         Decimal("200.00"))
        with self.assertRaisesMessage(ValidationError, "no confirmed 194J rate stands on 01 Jul 2026"):
            self.bill("70000", day=datetime.date(2026, 7, 1), vendor=party)
        self.assertEqual(self.bill("60000", day=datetime.date(2026, 7, 2), vendor=party).deduct_tds().amount,
                         Decimal("300.00"))

    def test_health_lists_a_certificate_with_no_amount(self):
        from apps.web.health import _own_tds_rates_without_a_basis

        party = self.holder()
        PartyTaxProfile.objects.filter(party=party).update(tds_rate_limit=None)
        listed = _own_tds_rates_without_a_basis(None)
        self.assertEqual((listed["ok"], [row["label"] for row in listed["rows"]]),
                         (False, ["V-C - Loom Fitters V-C: 0.5% under 194C, resting on 197/2026/14, with no amount"]))
        # Until it is given, its rate goes on to its last day whatever is paid, as before.
        self.assertEqual(self.bill("150000", day=datetime.date(2026, 6, 15), vendor=Party.objects.get(pk=party.pk))
                         .deduct_tds().amount, Decimal("750.00"))
        with self.assertRaisesMessage(ValidationError, "give the amount"):
            PartyTaxProfile.objects.get(party=party).save()
        PartyTaxProfile.objects.filter(party=party).update(tds_rate_limit=Decimal("100000"))
        self.assertTrue(_own_tds_rates_without_a_basis(None)["ok"])


class TheNilCertificateTests(TdsTestCase):
    """
    O618: a certificate at a nil rate (section 197 at 0%) deducts nothing, and each payment it covers is still a row
    of the return, under it, with no tax; what it has used counts against its amount, past which the section's rate
    applies. 194C at 2% (single 30,000, year 1,00,000), a certificate at nil from 1 June to 30 September 2026 for
    1,00,000 (calc_g57/o618.py):
      15 Jun 40,000 -> a row at nil (40,000 used);  1 Jul 45,000 -> a row at nil (85,000 used)
      1 Aug 35,000 -> 15,000 at nil and 20,000 at 2% = 400: two rows, the bill owes 34,600;  1 Sep 30,000 -> 600
    A row at nil posts nothing and no challan pays it; reversed, it takes its day alone and frees its base.
    """

    FROM, UNTIL, CERT = datetime.date(2026, 6, 1), datetime.date(2026, 9, 30), "197/2026/21"

    def holder(self, limit="100000", code="V-N"):
        party = Party.objects.create(code=code, name=f"Bag Stitchers {code}")
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.VENDOR)
        PartyTaxProfile.objects.create(party=party, pan="AAAPN1234C", tds_section=self.contract,
                                       tds_rate_percent=Decimal("0"), tds_rate_reference=self.CERT,
                                       tds_rate_basis="certificate", tds_rate_from=self.FROM,
                                       tds_rate_until=self.UNTIL, tds_rate_limit=Decimal(limit))
        return party

    def rows(self, party):
        from .tds import TdsDeduction

        return [(row.date, row.base, row.rate_percent, row.amount, row.journal_entry_id is None, row.certificate)
                for row in TdsDeduction.objects.filter(bill__vendor=party, reversed_on__isnull=True)
                .order_by("date", "pk")]

    def four_bills(self, party):
        return [self.bill(amount, day=day, vendor=party).deduct_tds()
                for day, amount in [(datetime.date(2026, 6, 15), "40000"), (datetime.date(2026, 7, 1), "45000"),
                                    (datetime.date(2026, 8, 1), "35000"), (datetime.date(2026, 9, 1), "30000")]]

    def test_each_payment_is_a_row_at_nil_until_the_amount_then_the_sections_rate(self):
        from .tds import certificate_left, tds_return

        party = self.holder()
        made = self.four_bills(party)
        self.assertEqual(self.rows(party), [
            (datetime.date(2026, 6, 15), Decimal("40000.00"), Decimal("0.0000"), Decimal("0.00"), True, self.CERT),
            (datetime.date(2026, 7, 1), Decimal("45000.00"), Decimal("0.0000"), Decimal("0.00"), True, self.CERT),
            (datetime.date(2026, 8, 1), Decimal("15000.00"), Decimal("0.0000"), Decimal("0.00"), True, self.CERT),
            (datetime.date(2026, 8, 1), Decimal("20000.00"), Decimal("2.0000"), Decimal("400.00"), False, ""),
            (datetime.date(2026, 9, 1), Decimal("30000.00"), Decimal("2.0000"), Decimal("600.00"), False, ""),
        ])
        first, _, third, fourth = (Bill.objects.get(pk=row.bill_id) for row in made)
        self.assertEqual([bill.amount_due() for bill in (first, third, fourth)],
                         [Decimal("40000.00"), Decimal("34600.00"), Decimal("29400.00")])
        # A row at nil moved nothing: the bill's history names only what posted.
        self.assertEqual([(amount, label) for _, amount, _, label in third.settled_otherwise_on()],
                         [(Decimal("400.00"), "TDS deducted")])
        self.assertEqual(first.settled_otherwise_on(), [])
        self.assertEqual(self.balance(self.tds_payable), Decimal("-1000.00"))
        self.assertEqual(certificate_left(party, self.contract, datetime.date(2026, 9, 1)), Decimal("0.00"))
        # The return lists the payments the certificate covered, under it with no tax, and the rest at 2%.
        self.assertEqual([(row["date"], row["base"], row["rate_percent"], row["amount"], row["certificate"],
                           row["challan"]) for row in tds_return(datetime.date(2026, 7, 1), datetime.date(2026, 9, 30))
                          if row["vendor"] == party.name], [
            (datetime.date(2026, 7, 1), Decimal("45000.00"), Decimal("0.0000"), Decimal("0.00"), self.CERT, ""),
            (datetime.date(2026, 8, 1), Decimal("15000.00"), Decimal("0.0000"), Decimal("0.00"), self.CERT, ""),
            (datetime.date(2026, 8, 1), Decimal("20000.00"), Decimal("2.0000"), Decimal("400.00"), "", ""),
            (datetime.date(2026, 9, 1), Decimal("30000.00"), Decimal("2.0000"), Decimal("600.00"), "", ""),
        ])

    def test_no_challan_pays_a_row_at_nil(self):
        party = self.holder()
        made = self.four_bills(party)
        with self.assertRaisesMessage(ValidationError, "Nothing deducted under 194C in June 2026 is waiting"):
            TdsChallan.pay(self.contract, datetime.date(2026, 6, 1), datetime.date(2026, 7, 7), self.bank,
                           "00006", "0510002")
        challan = TdsChallan.pay(self.contract, datetime.date(2026, 8, 1), datetime.date(2026, 9, 7), self.bank,
                                 "00008", "0510002")
        at_nil, past_it = made[2].parts()
        self.assertEqual((challan.amount, list(challan.deductions.values_list("pk", flat=True))),
                         (Decimal("400.00"), [past_it.pk]))
        # The deduction is one: its row at nil is not reversed while the challan has paid its other row over.
        with self.assertRaisesMessage(ValidationError, "void the challan first"):
            at_nil.reverse()

    def test_reversed_together_and_deducted_again_the_same(self):
        from .tds import TdsDeduction, certificate_left

        party = self.holder()
        made = self.four_bills(party)
        at_nil, past_it = made[2].parts()
        at_nil.reverse(on_date=datetime.date(2026, 9, 9))
        past_it.refresh_from_db()
        at_nil.refresh_from_db()
        self.assertEqual((at_nil.reversed_on, at_nil.reversed_entry_id, past_it.reversed_on,
                          past_it.reversed_entry.date),
                         (datetime.date(2026, 9, 9), None, datetime.date(2026, 9, 9), datetime.date(2026, 9, 9)))
        with self.assertRaisesMessage(ValidationError, "already been reversed"):
            TdsDeduction.objects.get(pk=at_nil.pk).reverse()
        # The day it was taken back is a fact, as its entry is.
        at_nil.reversed_on = datetime.date(2026, 9, 10)
        with self.assertRaisesMessage(ValidationError, "What was deducted is a fact"):
            at_nil.save()
        self.assertEqual(certificate_left(party, self.contract, datetime.date(2026, 9, 9)), Decimal("15000.00"))
        again = Bill.objects.get(pk=at_nil.bill_id).deduct_tds()
        self.assertEqual([(part.base, part.amount, part.certificate) for part in again.parts()],
                         [(Decimal("15000.00"), Decimal("0.00"), self.CERT), (Decimal("20000.00"), Decimal("400.00"), "")])
        # Reversed, a row is a deduction of its own, never part of the one made again in its place.
        self.assertEqual(TdsDeduction.objects.get(pk=at_nil.pk).parts(), [at_nil])
        self.assertEqual(self.balance(self.tds_payable), Decimal("-1000.00"))

    def test_a_row_at_nil_reversed_after_its_quarter(self):
        from .tds import certificate_left, tds_return

        party = self.holder()
        alone = self.bill("40000", day=datetime.date(2026, 6, 15), vendor=party).deduct_tds()
        alone.reverse(on_date=datetime.date(2026, 7, 5))
        self.assertEqual(certificate_left(party, self.contract, datetime.date(2026, 7, 5)), Decimal("100000.00"))
        listed = [(row["date"], row["base"], row["amount"], row["reverses"])
                  for row in tds_return(datetime.date(2026, 4, 1), datetime.date(2026, 6, 30))]
        taken_back = [(row["date"], row["base"], str(row["amount"]), row["reverses"])
                      for row in tds_return(datetime.date(2026, 7, 1), datetime.date(2026, 9, 30))]
        self.assertEqual((listed, taken_back), (
            [(datetime.date(2026, 6, 15), Decimal("40000.00"), Decimal("0.00"), None)],
            [(datetime.date(2026, 7, 5), Decimal("-40000.00"), "0.00", datetime.date(2026, 6, 15))]))

    def test_a_row_at_nil_reversed_within_its_quarter_is_in_neither(self):
        from .tds import tds_return

        party = self.holder()
        self.bill("40000", day=datetime.date(2026, 6, 15), vendor=party).deduct_tds().reverse(
            on_date=datetime.date(2026, 6, 20))
        self.assertEqual((tds_return(datetime.date(2026, 4, 1), datetime.date(2026, 6, 30)),
                          tds_return(datetime.date(2026, 7, 1), datetime.date(2026, 9, 30))), ([], []))

    def test_a_closed_month_takes_no_row_at_nil_nor_its_reversal(self):
        from apps.accounting.models import AccountingPeriod

        party = self.holder()
        june = AccountingPeriod.objects.create(name="June 2026", start_date=datetime.date(2026, 6, 1),
                                               end_date=datetime.date(2026, 6, 30))
        bill = self.bill("40000", day=datetime.date(2026, 6, 15), vendor=party)
        june.close()
        with self.assertRaisesMessage(ValidationError, "a deduction at a nil rate is not recorded on 2026-06-15"):
            bill.deduct_tds()
        june.reopen()
        made = Bill.objects.get(pk=bill.pk).deduct_tds()
        june.refresh_from_db()
        june.close()
        with self.assertRaisesMessage(ValidationError, "a deduction at a nil rate is not reversed on 2026-06-20"):
            made.reverse(on_date=datetime.date(2026, 6, 20))
        made.refresh_from_db()
        self.assertIsNone(made.reversed_on)

    def test_a_bill_past_the_amount_needs_the_sections_rate_to_post(self):
        fees = TdsSection.objects.create(code="194J", name="Professional fees", mode="whole",
                                         single_threshold=Decimal("30000"), annual_threshold=Decimal("100000"),
                                         payable_account=self.tds_payable)
        party = self.holder()
        PartyTaxProfile.objects.filter(party=party).update(tds_section=fees)
        party = Party.objects.get(pk=party.pk)
        self.assertEqual(self.bill("40000", day=datetime.date(2026, 6, 15), vendor=party).deduct_tds().base,
                         Decimal("40000.00"))
        with self.assertRaisesMessage(ValidationError, "no confirmed 194J rate stands on 01 Jul 2026"):
            self.bill("70000", day=datetime.date(2026, 7, 1), vendor=party)
        made = self.bill("60000", day=datetime.date(2026, 7, 2), vendor=party).deduct_tds()
        self.assertEqual((made.base, made.amount, made.journal_entry_id), (Decimal("60000.00"), Decimal("0.00"), None))

    def test_a_bill_at_nil_is_not_deducted_twice(self):
        party = self.holder()
        bill = self.bill("40000", day=datetime.date(2026, 6, 15), vendor=party)
        bill.deduct_tds()
        with self.assertRaisesMessage(ValidationError, "Tax under 194C has already been deducted on"):
            Bill.objects.get(pk=bill.pk).deduct_tds()

    def test_under_the_threshold_there_is_still_nothing_to_deduct(self):
        party = self.holder()
        with self.assertRaisesMessage(ValidationError, "have not passed its threshold"):
            self.bill("20000", day=datetime.date(2026, 6, 15), vendor=party).deduct_tds()

    def test_the_database_holds_a_row_at_nil_to_its_shape(self):
        from django.db import IntegrityError, transaction

        from apps.accounting.models import JournalEntry

        from .tds import TdsDeduction

        party = self.holder()
        made = self.bill("40000", day=datetime.date(2026, 6, 15), vendor=party).deduct_tds()
        entry = JournalEntry.objects.create(date=made.date, memo="Not this deduction's")
        shape = {"bill": made.bill, "section": self.contract, "base": Decimal("1000"), "date": made.date,
                 "posted_account": self.tds_payable}
        for case, wrong in [
                ("nothing at 2%", {"rate_percent": Decimal("2"), "amount": Decimal("0"), "certificate": "Y"}),
                ("nothing at no certificate", {"rate_percent": Decimal("0"), "amount": Decimal("0"), "certificate": ""}),
                ("nothing with an entry", {"rate_percent": Decimal("0"), "amount": Decimal("0"), "certificate": "X",
                                           "journal_entry": entry}),
                ("money with no entry", {"rate_percent": Decimal("2"), "amount": Decimal("20"), "certificate": "Z"})]:
            with self.subTest(case), self.assertRaises(IntegrityError), transaction.atomic():
                TdsDeduction.objects.create(**shape, **wrong)
        # A row at nil reversed by an entry it never posted, or taken back with no day: refused as well.
        for case, wrong in [("by an entry", {"reversed_entry": entry, "reversed_on": made.date}),
                            ("with no day", {"reversed_entry": entry})]:
            with self.subTest(case), self.assertRaises(IntegrityError), transaction.atomic():
                TdsDeduction.objects.filter(pk=made.pk).update(**wrong)

    def test_the_profile_asks_a_nil_rate_its_amount_and_health_lists_one_without(self):
        from apps.web.health import _own_tds_rates_without_a_basis

        party = self.holder()
        PartyTaxProfile.objects.filter(party=party).update(tds_rate_limit=None)
        listed = _own_tds_rates_without_a_basis(None)
        self.assertEqual((listed["ok"], [row["label"] for row in listed["rows"]]),
                         (False, ["V-N - Bag Stitchers V-N: 0% under 194C, resting on 197/2026/21, with no amount"]))
        with self.assertRaisesMessage(ValidationError, "give the amount"):
            PartyTaxProfile.objects.get(party=party).save()
        # Until it is given, every payment is a row at nil to the certificate's last day, as the rate stood before.
        made = self.bill("150000", day=datetime.date(2026, 6, 15), vendor=Party.objects.get(pk=party.pk)).deduct_tds()
        self.assertEqual((made.base, made.amount, made.certificate), (Decimal("150000.00"), Decimal("0.00"), self.CERT))


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
        [q1] = client.get(url, {"from": "2026-04-01", "to": "2026-06-30"}).json()["rows"]
        [q2] = client.get(url, {"from": "2026-07-01", "to": "2026-09-30"}).json()["rows"]
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
                               {"from": "2026-04-01", "to": "2026-06-30"}).json()["rows"]
        self.assertEqual((row["section"], row["pan"], row["base"], row["amount"], row["challan"]),
                         ("194Q", "AAACG1234F", "1000000.00", "1000.00", "0510308/00042"))
        [listed] = controller.get("/api/purchasing/tds-deductions/", {"bill": bill.pk}).json()
        reversed_ = manager.post(f"/api/purchasing/tds-deductions/{listed['id']}/reverse/", {}, format="json")
        self.assertEqual(reversed_.status_code, 400)
        self.assertIn("void the challan first", reversed_.content.decode())

    def test_a_row_at_nil_through_the_api(self):
        # O618, as the people who do it: AP deducts at a nil certificate, the Controller lists the row with nothing
        # deducted and no entry, it is standing until AP reverses it, and the Auditor then sees it reversed.
        party = Party.objects.create(code="V-N", name="Bag Stitchers")
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.VENDOR)
        PartyTaxProfile.objects.create(party=party, pan="AAAPN1234C", tds_section=self.contract,
                                       tds_rate_percent=Decimal("0"), tds_rate_reference="197/2026/21",
                                       tds_rate_basis="certificate", tds_rate_from=datetime.date(2026, 6, 1),
                                       tds_rate_until=datetime.date(2026, 9, 30), tds_rate_limit=Decimal("100000"))
        bill = self.bill("40000", day=datetime.date(2026, 6, 15), vendor=party)
        manager = self.as_("AP Manager")
        done = manager.post(f"/api/purchasing/bills/{bill.pk}/deduct_tds/", {}, format="json")
        self.assertEqual(done.status_code, 200, done.content)
        self.assertEqual(done.json()["amount_due"], "40000.00")
        controller = self.as_("Controller")
        [row] = controller.get("/api/purchasing/tds-deductions/", {"bill": bill.pk, "reversed_on__isnull": "true"}).json()
        self.assertEqual((row["base"], row["rate_percent"], row["amount"], row["certificate"], row["journal_entry"],
                          row["reversed"], row["challan_label"]),
                         ("40000.00", "0.0000", "0.00", "197/2026/21", None, False, ""))
        [listed] = controller.get("/api/purchasing/tds-deductions/quarter/",
                                  {"from": "2026-04-01", "to": "2026-06-30"}).json()["rows"]
        self.assertEqual((listed["base"], listed["amount"], listed["certificate"]), ("40000.00", "0.00", "197/2026/21"))
        self.assertEqual(self.as_("Purchasing Clerk").post(f"/api/purchasing/tds-deductions/{row['id']}/reverse/", {},
                                                          format="json").status_code, 403)
        reversed_ = manager.post(f"/api/purchasing/tds-deductions/{row['id']}/reverse/", {"date": "2026-06-20"},
                                 format="json")
        self.assertEqual(reversed_.status_code, 200, reversed_.content)
        self.assertEqual((reversed_.json()["reversed"], reversed_.json()["reversed_on"]), (True, "2026-06-20"))
        auditor = self.as_("Auditor")
        self.assertEqual(auditor.get("/api/purchasing/tds-deductions/",
                                     {"bill": bill.pk, "reversed_on__isnull": "true"}).json(), [])
        self.assertEqual(auditor.get("/api/purchasing/tds-deductions/",
                                     {"bill": bill.pk, "reversed_on__isnull": "false"}).json()[0]["reversed"], True)

    def test_a_bill_split_at_a_certificates_amount_through_the_api(self):
        # O598, as the people who do it: AP deducts, the two rows are listed, and reversing either takes back both.
        party = Party.objects.create(code="V-C", name="Loom Fitters")
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.VENDOR)
        PartyTaxProfile.objects.create(party=party, pan="AAAPL1234C", tds_section=self.contract,
                                       tds_rate_percent=Decimal("0.5"), tds_rate_reference="197/2026/14",
                                       tds_rate_basis="certificate", tds_rate_from=datetime.date(2026, 6, 1),
                                       tds_rate_until=datetime.date(2026, 9, 30), tds_rate_limit=Decimal("15000"))
        bill = self.bill("35000", vendor=party)
        manager = self.as_("AP Manager")
        done = manager.post(f"/api/purchasing/bills/{bill.pk}/deduct_tds/", {}, format="json")
        self.assertEqual(done.status_code, 200, done.content)
        # 15,000 at 0.5% = 75 and 20,000 at 2% = 400: 35,000 less 475.
        self.assertEqual(done.json()["amount_due"], "34525.00")
        listed = self.as_("Controller").get("/api/purchasing/tds-deductions/", {"bill": bill.pk}).json()
        self.assertEqual(sorted((row["base"], row["rate_percent"], row["amount"], row["certificate"]) for row in listed),
                         [("15000.00", "0.5000", "75.00", "197/2026/14"), ("20000.00", "2.0000", "400.00", "")])
        past_it = next(row for row in listed if row["certificate"] == "")
        reversed_ = manager.post(f"/api/purchasing/tds-deductions/{past_it['id']}/reverse/", {}, format="json")
        self.assertEqual(reversed_.status_code, 200, reversed_.content)
        self.assertEqual(self.as_("Auditor").get("/api/purchasing/tds-deductions/",
                                                 {"bill": bill.pk, "reversed_on__isnull": "true"}).json(), [])

    def test_the_vendors_terms_are_kept_on_its_tax_profile(self):
        profile = self.vendor.tax_profile
        controller = self.as_("Controller")
        terms = {"tds_section": self.contract.pk, "tds_rate_percent": "1", "tds_rate_reference": "197/2026/14",
                 "tds_rate_basis": "certificate"}
        # A certificate with no dates is refused beside the field it lacks (O247).
        undated = controller.patch(f"/api/accounting/party-tax-profiles/{profile.pk}/", terms, format="json")
        self.assertEqual(undated.status_code, 400, undated.content)
        self.assertIn("tds_rate_from", undated.json())
        changed = controller.patch(f"/api/accounting/party-tax-profiles/{profile.pk}/", {
            **terms, "tds_rate_from": "2026-06-01", "tds_rate_until": "2026-09-30"}, format="json")
        # Dated, it states its amount too (O598), refused beside the field.
        self.assertEqual(changed.status_code, 400, changed.content)
        self.assertIn("tds_rate_limit", changed.json())
        changed = controller.patch(f"/api/accounting/party-tax-profiles/{profile.pk}/", {
            **terms, "tds_rate_from": "2026-06-01", "tds_rate_until": "2026-09-30", "tds_rate_limit": "500000"},
            format="json")
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual([changed.json()[key] for key in ("tds_rate_percent", "tds_rate_basis", "tds_rate_from",
                                                          "tds_rate_until", "tds_rate_limit")],
                         ["1.0000", "certificate", "2026-06-01", "2026-09-30", "500000.00"])


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
        with self.assertRaisesMessage(ValidationError, "match a payment"):
            self.line.match_entry(paid.journal_entry)
        # Refused until O614, this test with it: the bank shows a transfer keyed by hand as it shows the challan,
        # and matched to nothing a month with one could never close.
        self.line.match_entry(by_hand)
        self.assertEqual(self.line.booked_entry_id, by_hand.pk)
        self.line.unmatch()
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
        read = {"section": self.contract.pk, "applies_from": "2026-04-01", "rate_percent": "1.0000",
                "no_pan_rate_percent": "20"}
        # Review F9: what the Controller confirms is what it read; a figure that moved is refused.
        stale = clients["Controller"].post(url, {**read, "rate_percent": "2"}, format="json")
        self.assertEqual(stale.status_code, 400, stale.content)
        self.assertIn("its rate percent differ", str(stale.json()))
        confirmed = clients["Controller"].post(url, read, format="json")
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        again = clients["Controller"].post(url, read, format="json")
        self.assertEqual(again.status_code, 400, again.content)
        self.assertIn("is already confirmed", str(again.json()))
        self.assertIsNotNone(TdsRate.objects.get(pk=made.json()["id"]).confirmed_on)


class ADeductionsCoveredBillsSentAreRefusedTests(TdsTestCase):
    """
    O575. A related list read back only (many=True, read_only) was dropped without a word, as a nested one was
    before O565: a deduction's covered bills, sent, kept nothing and said nothing. The shared base refuses it
    beside the field; the deduction's own fields read back only are ignored, as DRF ignores them.
    """

    def test_covered_bills_sent_are_refused_beside_the_field(self):
        from .tds_api import TdsDeductionSerializer

        party = self.contractor(pan="AAAPL1234C")
        deduction = self.bill("40000", vendor=party).deduct_tds()
        other = self.bill("25000", vendor=party)
        sent = TdsDeductionSerializer(deduction, data={"covered_bills": [other.pk], "amount": "1.00"}, partial=True)
        self.assertFalse(sent.is_valid())
        self.assertEqual(sent.errors, {"covered_bills": [
            "`covered_bills` is what the record reads back, and nothing sent in it is kept: leave it out."]})
        self.assertEqual((deduction.amount, list(deduction.covered_bills.all())), (Decimal("800.00"), [deduction.bill]))


class TheRateListAsksWhetherEachWasTakenOnceTests(TdsTestCase):
    """
    O568. The list said why each rate stays (kept_because, O556) by asking one EXISTS a row. 194C at 2% has a
    deduction (a bill of 40,000 to a contractor with a PAN: 800); 194Q at 0.1% has none. The list asks the same
    number of queries at 2 rows as at 5, and still says which one stays: the count and the page, where it asked
    4 and 7.
    """

    QUERIES = 2

    def test_the_list_asks_the_same_at_two_rows_and_at_five(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        from apps.accounting.models import TdsRate

        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("apmanager")
        user.groups.add(Group.objects.get(name="AP Manager"))
        client = APIClient()
        client.force_authenticate(user)
        party = self.contractor(pan="AAAPL1234C")
        self.assertEqual(self.bill("40000", vendor=party).deduct_tds().amount, Decimal("800.00"))
        stays = "Bills were deducted at this rate; it stays."
        client.get("/api/accounting/tds-rates/")  # who is asking and what they may do, read once a session
        with self.assertNumQueries(self.QUERIES):
            listed = client.get("/api/accounting/tds-rates/")
        self.assertEqual(sorted((row["section_code"], row["kept_because"]) for row in listed.json()),
                         [("194C", stays), ("194Q", "")])
        for month in (7, 8, 9):
            TdsRate.objects.create(section=self.goods, applies_from=datetime.date(2026, month, 1),
                                   rate_percent=Decimal("0.1"), no_pan_rate_percent=Decimal("5"))
        with self.assertNumQueries(self.QUERIES):
            listed = client.get("/api/accounting/tds-rates/")
        self.assertEqual(sorted((row["section_code"], row["applies_from"], row["kept_because"]) for row in listed.json()),
                         [("194C", "2000-04-01", stays), ("194Q", "2000-04-01", ""), ("194Q", "2026-07-01", ""),
                          ("194Q", "2026-08-01", ""), ("194Q", "2026-09-01", "")])
        # What a delete is refused in is asked again as it is refused, not read off a list.
        self.assertEqual(client.get(f"/api/accounting/tds-rates/{self.contract_rate.pk}/").json()["kept_because"], stays)
        with self.assertRaisesMessage(ValidationError, stays):
            TdsRate.objects.get(pk=self.contract_rate.pk).delete()



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


# Slow: it unwinds purchasing's latest migration and replays it.
@tag("migration")
class ReversedOnMigrationTests(TransactionTestCase):
    """
    purchasing 0098 (O618): every reversal keeps its day, so a row at a nil rate, which has no entry to reverse, can
    be taken back too. Each deduction reversed before it takes its reversal entry's day:
      D1  700.00 on B-1, 10 June, standing                  -> reversed_on empty
      D2  500.00 on B-2, 10 June, reversed by an entry of 5 July -> reversed_on 5 July
    Back: refused while a row at nil (no entry) exists, naming it; with it gone, the column goes and both rows stay.
    """

    before = [("purchasing", "0097_deduction_split_at_a_certificate")]
    after = [("purchasing", "0098_deduction_at_a_nil_rate")]

    def test_each_reversal_keeps_its_entrys_day_and_a_row_at_nil_holds_the_way_back(self):
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        self.addCleanup(_migrated_to_the_leaves)
        apps = executor.loader.project_state(self.before).apps
        now = executor.loader.project_state(executor.loader.graph.leaf_nodes()).apps
        account = now.get_model("accounting", "Account")
        payable, owed = (account.objects.create(code=code, name=code, account_type="liability")
                         for code in ("2100", "2250"))
        vendor = now.get_model("core", "Party").objects.create(code="V-N", name="Bag Stitchers")
        contract = now.get_model("accounting", "TdsSection").objects.create(code="194C", name="Contractors",
                                                                            payable_account_id=owed.pk)
        entry_model = now.get_model("accounting", "JournalEntry")
        bill_model, deduction = apps.get_model("purchasing", "Bill"), apps.get_model("purchasing", "TdsDeduction")

        def bill(number):
            return bill_model.objects.create(vendor_id=vendor.pk, bill_date=DAY, payable_account_id=payable.pk,
                                             number=number, posted=True)

        def deducted(number, amount):
            return deduction.objects.create(
                bill=bill(number), section_id=contract.pk, base=Decimal(amount) * 50, rate_percent=Decimal("2"),
                amount=Decimal(amount), date=DAY, posted_account_id=owed.pk,
                journal_entry_id=entry_model.objects.create(date=DAY, memo=f"TDS on {number}", posted=True).pk)

        d1 = deducted("B-1", "700")
        d2 = deducted("B-2", "500")
        d2.reversed_entry_id = entry_model.objects.create(date=datetime.date(2026, 7, 5), memo="Reversed",
                                                          posted=True).pk
        d2.save()

        MigrationExecutor(connection).migrate(self.after)
        kept = MigrationExecutor(connection).loader.project_state(self.after).apps.get_model(
            "purchasing", "TdsDeduction")
        self.assertEqual(sorted(kept.objects.values_list("pk", "reversed_on")),
                         [(d1.pk, None), (d2.pk, datetime.date(2026, 7, 5))])
        at_nil = kept.objects.create(
            bill_id=bill("B-3").pk, section_id=contract.pk, base=Decimal("40000"), rate_percent=Decimal("0"),
            amount=Decimal("0"), date=DAY, posted_account_id=owed.pk, certificate="197/2026/21")

        with self.assertRaisesMessage(RuntimeError, f"1 TDS deduction(s) at a certificate's nil rate have no entry, "
                                                    f"which purchasing 0097 requires: {at_nil.pk} on bill "
                                                    f"{at_nil.bill_id} under 197/2026/21"):
            MigrationExecutor(connection).migrate(self.before)
        kept.objects.filter(pk=at_nil.pk).delete()
        MigrationExecutor(connection).migrate(self.before)
        back = MigrationExecutor(connection).loader.project_state(self.before).apps.get_model(
            "purchasing", "TdsDeduction")
        self.assertNotIn("reversed_on", {field.name for field in back._meta.get_fields()})
        self.assertEqual(sorted(back.objects.values_list("amount", "reversed_entry_id")),
                         [(Decimal("500.00"), d2.reversed_entry_id), (Decimal("700.00"), None)])


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


class ABillWithTcsAndTdsSettlesAtNoExchangeTests(TdsTestCase):
    """
    O450, the purchasing mirror: a bill's TCS is on its own entry, so what it booked on the payable is its
    total(); its TDS is settled otherwise, at its slice. The van, 1,200,000 and 12,000 of TCS, after a
    5,000,000 bill took the year's threshold: TDS 0.1% of 1,200,000 is 1,200. 600,000 paid, then what is
    due, 610,800: each clears its own figure, nothing is exchange, the van leaves the payable at 0.00
    (calc_fix_new/o450.py).
    """

    van = TcsTheSupplierCollectedTests.van

    def setUp(self):
        super().setUp()
        self.tcs_receivable = Account.objects.create(code="1460", name="TCS receivable", account_type=AccountType.ASSET)

    def pay(self, bill, amount):
        from apps.accounting.models import Payment, PaymentDirection
        from .models import BillPayment

        payment = Payment.objects.create(party=self.vendor, direction=PaymentDirection.DISBURSEMENT,
                                         payment_date=DAY, amount=Decimal(amount), currency=self.usd,
                                         bank_account=self.bank, counterpart_account=self.payable)
        payment.post()
        row = BillPayment.objects.create(bill=bill, payment=payment, amount=Decimal(amount))
        row.refresh_from_db()
        return row

    def test_part_then_whole(self):
        self.bill("5000000")
        before = self.balance(self.payable)
        van = self.van(tcs_amount=Decimal("12000"), tcs_section="206C(1F)", tcs_account=self.tcs_receivable)
        van.post()
        van = Bill.objects.get(pk=van.pk)
        self.assertEqual(van.deduct_tds().amount, Decimal("1200.00"))
        first = self.pay(Bill.objects.get(pk=van.pk), "600000")
        self.assertEqual((first.document_base, first.payment_base, first.fx_entry_id),
                         (Decimal("600000.00"), Decimal("600000.00"), None))
        van = Bill.objects.get(pk=van.pk)
        self.assertEqual(van.amount_due(), Decimal("610800.00"))
        second = self.pay(van, "610800")
        self.assertEqual((second.document_base, second.payment_base, second.fx_entry_id),
                         (Decimal("610800.00"), Decimal("610800.00"), None))
        self.assertEqual((Bill.objects.get(pk=van.pk).amount_due(), self.balance(self.payable) - before),
                         (Decimal("0.00"), Decimal("0.00")))


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
                                                       "be deducted. The accountant confirms it under Settings > TDS rates"):
            self.bill("40000", day=datetime.date(2026, 3, 10), vendor=self.party)
        self.assertEqual(self.bill("20000", day=datetime.date(2026, 3, 10), vendor=self.party).posted, True)

    def test_a_deduction_is_not_dated_before_its_bill(self):
        bill = self.bill("40000", day=datetime.date(2026, 2, 10), vendor=self.party)
        with self.assertRaisesMessage(ValidationError, "is not deducted from"):
            bill.deduct_tds(on_date=datetime.date(2026, 2, 9))
        self.assertEqual(bill.deduct_tds(on_date=datetime.date(2026, 2, 12)).date, datetime.date(2026, 2, 12))
