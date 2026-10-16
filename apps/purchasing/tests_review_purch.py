"""
g22 review_purch: the review of fix_purch (e73428e..674ac8a), kept as regression tests: each states a
fact about money or a document that is true, named by what it protects.
"""

import datetime
import importlib
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.accounting.models import (
    Account, AccountType, JournalLine, PartyTaxProfile, Tax, TaxGroup, TaxScope, TdsRate, TdsSection,
)
from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.purchasing import tests_msme, tests_vendor_prices
from apps.purchasing.models import (
    Bill, BillLine, PurchaseApprovalPolicy, PurchaseOrder, PurchaseOrderLine, payment_run, vendor_invoice_key,
)
from apps.purchasing.tds import TdsChallan, tds_return
from apps.purchasing.tests_returns_and_landed import ProbeCase
from apps.purchasing.tests_tds import TdsTestCase, confirmed_rate

D = datetime.date
Z = Decimal


# ---------------------------------------------------------------- O233: dated TDS rates
class TdsRateOfTheDayItFallsDueTests(TdsTestCase):
    def setUp(self):
        super().setUp()
        self.party = self.contractor(pan="AAAPL1234C")

    def vendor_with(self, code, pan=""):
        party = Party.objects.create(code=code, name=f"Vendor {code}")
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.VENDOR)
        PartyTaxProfile.objects.create(party=party, tds_section=self.contract, pan=pan)
        return party

    def test_01_a_change_on_the_1st_and_one_mid_month_each_bill_takes_the_rate_of_its_own_day(self):
        """194C, 50,000 bills each taxed alone: 30 Jun 2% 1,000; 1 Jul 3% 1,500; 14 Jul 1,500; 15 Jul 4% 2,000; 31 Jul 2,000."""
        confirmed_rate(self.contract, "3", "25", day=D(2026, 7, 1))
        confirmed_rate(self.contract, "4", "30", day=D(2026, 7, 15))
        got = []
        for day in (D(2026, 6, 30), D(2026, 7, 1), D(2026, 7, 14), D(2026, 7, 15), D(2026, 7, 31)):
            made = self.bill("50000", day=day, vendor=self.party).deduct_tds()
            got.append((day, made.amount, made.rate_percent, made.rate.applies_from))
        self.assertEqual(got, [
            (D(2026, 6, 30), Z("1000"), Z("2"), D(2000, 4, 1)), (D(2026, 7, 1), Z("1500"), Z("3"), D(2026, 7, 1)),
            (D(2026, 7, 14), Z("1500"), Z("3"), D(2026, 7, 1)), (D(2026, 7, 15), Z("2000"), Z("4"), D(2026, 7, 15)),
            (D(2026, 7, 31), Z("2000"), Z("4"), D(2026, 7, 15))])

    def test_02_a_bill_dated_before_the_change_and_deducted_after_it(self):
        """Dated 30 Jun, deducted 2 Jul (the day the rate is 3%): the deduction is at 3% and dated 2 Jul."""
        confirmed_rate(self.contract, "3", "25", day=D(2026, 7, 1))
        made = self.bill("50000", day=D(2026, 6, 30), vendor=self.party).deduct_tds(on_date=D(2026, 7, 2))
        self.assertEqual((made.amount, made.date, made.rate_percent), (Z("1500"), D(2026, 7, 2), Z("3")))

    def test_02b_a_deduction_cannot_be_dated_before_the_bill_it_deducts_from_to_pick_the_old_rate(self):
        """Bill 5 Jul (3% from 1 Jul); date 30 Jun is the old 2%: 1,000 deducted where 1,500 is owed, entry before the bill."""
        confirmed_rate(self.contract, "3", "25", day=D(2026, 7, 1))
        bill = self.bill("50000", day=D(2026, 7, 5), vendor=self.party)
        try:
            made = bill.deduct_tds(on_date=D(2026, 6, 30))
        except ValidationError:
            return
        self.fail(f"deducted {made.amount} at {made.rate_percent}% dated {made.date}, five days before the bill "
                  f"dated {bill.bill_date}; at the bill's own day it is 1,500")

    def test_03_a_rate_used_by_a_deduction_cannot_be_edited_moved_or_deleted(self):
        self.bill("50000", day=D(2026, 6, 30), vendor=self.party).deduct_tds()
        rate = TdsRate.objects.get(pk=self.contract_rate.pk)
        refused = []
        for field, value in (("rate_percent", Z("5")), ("no_pan_rate_percent", Z("1")),
                             ("applies_from", D(2001, 1, 1))):
            row = TdsRate.objects.get(pk=rate.pk)
            setattr(row, field, value)
            try:
                row.save()
            except ValidationError:
                refused.append(field)
        try:
            TdsRate.objects.get(pk=rate.pk).delete()
        except ValidationError:
            refused.append("delete")
        self.assertEqual(refused, ["rate_percent", "no_pan_rate_percent", "applies_from", "delete"])
        # a reversed deduction still holds its row
        deduction = self.party.bills.get().tds_deductions.get()
        deduction.reverse()
        row = TdsRate.objects.get(pk=rate.pk)
        row.rate_percent = Z("5")
        with self.assertRaises(ValidationError):
            row.save()

    def test_04_the_return_and_the_challan_stand_at_the_rate_taken_after_a_new_rate_arrives(self):
        confirmed_rate(self.contract, "3", "25", day=D(2026, 7, 1))
        self.bill("50000", day=D(2026, 6, 30), vendor=self.party).deduct_tds()
        self.bill("50000", day=D(2026, 7, 1), vendor=self.party).deduct_tds()
        rows = tds_return(D(2026, 4, 1), D(2026, 9, 30))
        self.assertEqual([(r["date"], r["rate_percent"], r["amount"]) for r in rows],
                         [(D(2026, 6, 30), Z("2"), Z("1000")), (D(2026, 7, 1), Z("3"), Z("1500"))])
        june = TdsChallan.pay(self.contract, D(2026, 6, 1), D(2026, 7, 7), self.bank, "00001", "0510308")
        july = TdsChallan.pay(self.contract, D(2026, 7, 1), D(2026, 8, 7), self.bank, "00002", "0510308")
        self.assertEqual((june.amount, july.amount), (Z("1000"), Z("1500")))
        self.assertEqual(self.balance(self.tds_payable), Z("0"))

    def test_05_the_no_pan_rate_is_kept_and_a_pan_given_later_moves_nothing_before_it(self):
        nopan = self.vendor_with("V-N")
        first = self.bill("50000", day=D(2026, 6, 30), vendor=nopan).deduct_tds()
        self.assertEqual((first.amount, first.rate_percent, first.pan), (Z("10000"), Z("20"), ""))
        row = tds_return(D(2026, 4, 1), D(2026, 6, 30))[0]
        self.assertEqual(row["pan"], "PANNOTAVBL")
        PartyTaxProfile.objects.filter(party=nopan).update(pan="AAAPL1234C")
        second = self.bill("50000", day=D(2026, 7, 1), vendor=nopan).deduct_tds()
        self.assertEqual((second.amount, second.rate_percent), (Z("1000"), Z("2")))
        self.assertEqual(type(first).objects.get(pk=first.pk).rate_percent, Z("20"))

    def test_06_194c_the_year_crossing_a_lakh_across_a_rate_change_takes_the_year_at_the_rate_of_the_day(self):
        """25k + 25k + 25k in June pass nothing; 30k on 1 Jul takes the year (105,000) at that day's 3%: 3,150."""
        confirmed_rate(self.contract, "3", "25", day=D(2026, 7, 1))
        for day in (D(2026, 6, 1), D(2026, 6, 15), D(2026, 6, 30)):
            with self.assertRaisesMessage(ValidationError, "Nothing to deduct"):
                self.bill("25000", day=day, vendor=self.party).deduct_tds()
        made = self.bill("30000", day=D(2026, 7, 1), vendor=self.party).deduct_tds()
        self.assertEqual((made.base, made.amount, made.covered_bills.count()), (Z("105000"), Z("3150"), 4))

    def test_07_194q_the_excess_over_50_lakh_across_a_rate_change_is_taxed_at_the_new_rate(self):
        confirmed_rate(self.goods, "0.25", "5", day=D(2026, 7, 1))
        june = self.bill("3000000", day=D(2026, 6, 20))
        with self.assertRaisesMessage(ValidationError, "Nothing to deduct"):
            june.deduct_tds()
        made = self.bill("3000000", day=D(2026, 7, 5)).deduct_tds()
        self.assertEqual((made.base, made.amount, made.rate_percent), (Z("1000000"), Z("2500"), Z("0.25")))

    def test_08_a_rate_edited_while_untaken_needs_the_accountants_word_again(self):
        row = TdsRate.objects.get(pk=self.contract_rate.pk)
        row.rate_percent = Z("2.5")
        row.save()
        # Refused as the bill posts, before anything could be deducted at the unconfirmed figure (g22 decision).
        with self.assertRaisesMessage(ValidationError, "no confirmed 194C rate stands on 30 Jun 2026"):
            self.bill("50000", day=D(2026, 6, 30), vendor=self.party)

    def test_09_confirming_vouches_for_the_figures_the_accountant_looked_at(self):
        """A screen loaded at 2%; somebody edits the row to 20%; the confirm click must not confirm 20%."""
        pending = TdsRate.objects.create(section=self.contract, applies_from=D(2026, 9, 1), rate_percent=Z("2"),
                                         no_pan_rate_percent=Z("20"))
        screen = TdsRate.objects.get(pk=pending.pk)
        other = TdsRate.objects.get(pk=pending.pk)
        other.rate_percent = Z("20")
        other.save()
        from django.contrib.auth.models import User
        with self.assertRaisesMessage(ValidationError, "is not what was read: its rate percent differ"):
            screen.confirm(User.objects.create_user("accountant"))
        stored = TdsRate.objects.get(pk=pending.pk)
        self.assertFalse(stored.confirmed_on is not None and stored.rate_percent == Z("20"),
                         "the rate the accountant never saw (20%) is now confirmed and will be deducted")

    def test_10_tcs_collected_by_the_supplier_is_not_part_of_the_tds_base(self):
        tcs = Account.objects.create(code="1461", name="TCS receivable", account_type=AccountType.ASSET)
        bill = Bill.objects.create(vendor=self.party, bill_date=D(2026, 6, 30), payable_account=self.payable,
                                   tcs_amount=Z("500"), tcs_account=tcs, tcs_section="206C(1F)")
        BillLine.objects.create(bill=bill, description="Job", quantity=Z("1"), unit_price=Z("50000"),
                                expense_account=self.expense)
        bill.post()
        made = Bill.objects.get(pk=bill.pk).deduct_tds()
        self.assertEqual((made.base, made.amount), (Z("50000"), Z("1000")))
        self.assertEqual(Bill.objects.get(pk=bill.pk).amount_due(), Z("49500"))


# ---------------------------------------------------------------- O234: TCS collected by a supplier
class TcsTheSupplierCollectedFootsTests(ProbeCase):
    def setUp(self):
        super().setUp()
        self.tcs = Account.objects.create(code="1460", name="TCS receivable", account_type=AccountType.ASSET)

    def tcs_bill(self, price="14285.71", qty="7", tcs="1000", day=D(2026, 1, 10), reference=""):
        bill = Bill.objects.create(vendor=self.vendor, bill_date=day, payable_account=self.payable,
                                   tcs_amount=Z(tcs), tcs_account=self.tcs, tcs_section="206C(1F)",
                                   reference=reference)
        BillLine.objects.create(bill=bill, description="Truck", quantity=Z(qty), unit_price=Z(price),
                                expense_account=self.expense)
        bill.post()
        return Bill.objects.get(pk=bill.pk)

    def test_01_the_bill_total_is_the_suppliers_to_the_paisa_and_the_ledger_agrees(self):
        bill = self.tcs_bill()
        self.assertEqual(bill.total(), Z("100999.97"))  # 7 x 14,285.71 = 99,999.97, plus 1,000.00
        self.assertEqual((-self.balance(self.payable), self.balance(self.tcs), bill.amount_due()),
                         (Z("100999.97"), Z("1000.00"), Z("100999.97")))

    def test_02_three_notes_take_back_exactly_the_tcs_and_the_receivable_foots_to_the_bills(self):
        bill = self.tcs_bill()
        line = bill.lines.get()
        taken = []
        for qty in ("3", "2", "2"):
            note = bill.create_debit_note(quantities={line: Z(qty)})
            taken.append(note.tcs_amount)
            self.assertEqual(note.total(), note.subtotal() + note.tcs_amount)
            billed = Bill.objects.get(pk=bill.pk)
            self.assertEqual(self.balance(self.tcs), billed.tcs_amount - billed.tcs_taken_back())
        self.assertEqual(sum(taken), Z("1000.00"))
        self.assertEqual((self.balance(self.tcs), self.balance(self.payable)), (Z("0"), Z("0")))

    def test_03_a_note_for_a_third_of_the_bill_takes_a_third_of_the_tcs_not_all_of_it(self):
        bill = self.tcs_bill(price="100", qty="3", tcs="10")
        note = bill.create_debit_note(quantities={bill.lines.get(): Z("1")})
        self.assertEqual((note.tcs_amount, note.total()), (Z("3.33"), Z("103.33")))

    def test_04_a_return_to_vendor_takes_its_share_of_the_tcs_back(self):
        order = self.order("10", "100")
        receipt = self.receive_on(order, "10", D(2026, 1, 5))
        bill = self.hand_bill(order.lines.get(), "10", "100", order=order)
        bill.tcs_amount, bill.tcs_account = Z("10"), self.tcs
        bill.save()
        bill.post()
        receipt.create_return(quantities={receipt.lines.get(): Z("4")}, debit_bills=True)
        (note,) = Bill.objects.filter(debits__isnull=False)
        self.assertEqual((note.tcs_amount, note.total(), self.balance(self.tcs), -self.balance(self.payable)),
                         (Z("4.00"), Z("404.00"), Z("6.00"), Z("606.00")))

    def test_09_grni_clears_when_a_tcs_bill_is_part_returned(self):
        order = self.order("10", "100")
        receipt = self.receive_on(order, "10", D(2026, 1, 5))
        bill = self.hand_bill(order.lines.get(), "10", "100", order=order)
        bill.tcs_amount, bill.tcs_account = Z("10"), self.tcs
        bill.save()
        bill.post()
        receipt.create_return(quantities={receipt.lines.get(): Z("4")}, debit_bills=True)
        self.assertEqual(self.balance(self.grni), Z("0"))

    def test_05_the_payment_run_foots_to_the_payable_ledger_with_tcs_and_a_part_note(self):
        bill = self.tcs_bill()
        bill.create_debit_note(quantities={bill.lines.get(): Z("3")})
        run = sum((row["total"] for row in payment_run(due_by=D(2027, 1, 1))), Z("0"))
        self.assertEqual(run, -self.balance(self.payable))

    def test_06_tcs_cannot_be_booked_into_a_bank_or_cash_account(self):
        """A TCS receivable is a claim on the tax department; a Dr to the bank with no money moving breaks its reconciliation."""
        bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        with self.assertRaises(ValidationError):
            bill = Bill.objects.create(vendor=self.vendor, bill_date=D(2026, 1, 10), payable_account=self.payable,
                                       tcs_amount=Z("1000"), tcs_account=bank)
            BillLine.objects.create(bill=bill, description="Truck", quantity=Z("1"), unit_price=Z("1000"),
                                    expense_account=self.expense)
            bill.post()

    def test_07_a_posted_bills_tcs_cannot_be_changed_by_a_save(self):
        bill = self.tcs_bill()
        bill.tcs_amount = Z("5000")
        with self.assertRaises(ValidationError):
            bill.save()
        self.assertEqual(Bill.objects.get(pk=bill.pk).tcs_amount, Z("1000.00"))

    def test_08_a_debit_note_made_by_hand_cannot_take_back_more_tcs_than_is_left(self):
        bill = self.tcs_bill()
        bill.create_debit_note(quantities={bill.lines.get(): Z("3")})  # takes back 428.57; 571.43 is left
        with self.assertRaises(ValidationError):
            Bill.objects.create(vendor=self.vendor, bill_date=D(2026, 1, 12), payable_account=self.payable,
                                debits=bill, tcs_amount=Z("900"), tcs_account=self.tcs)


# ---------------------------------------------------------------- O95: vendor invoice numbers
class OneVendorInvoiceNumberTests(ProbeCase):
    def draft(self, ref, day=D(2026, 6, 10), vendor=None):
        return Bill.objects.create(vendor=vendor or self.vendor, bill_date=day, payable_account=self.payable,
                                   reference=ref)

    def accepted(self, variants, day=D(2026, 6, 10)):
        out = []
        for variant in variants:
            try:
                made = self.draft(variant, day)
            except ValidationError:
                continue
            out.append(variant)
            made.delete()
        return out

    def test_01_case_and_whitespace_variants_are_one_invoice_and_other_separators_are_others(self):
        """The g22 decision: case and whitespace fold, separators do not."""
        self.draft("INV-77")
        self.assertEqual(self.accepted(["inv-77", " INV-77 ", "Inv-77", "INV-77\t"]), [])
        self.draft("INV 77")
        self.assertEqual(self.accepted(["INV\t77", "INV\u00a077", "INV\u202f77", "INV\u300077", "INV  77"]), [])
        others = ["INV/77", "I.N.V.77", "INV77", "Inv - 77", "INV--77", "INV/-.77"]
        self.assertEqual(self.accepted(others), others)

    def test_02_unicode_look_alikes_of_one_invoice_number_are_one_invoice(self):
        """Pasted from a PDF or a spreadsheet: format characters, non-ASCII dashes, full-width forms (review F5)."""
        self.draft("INV-77")
        variants = {"zero-width space": "INV-\u200b77", "BOM": "\ufeffINV-77", "soft hyphen": "INV-\u00ad77",
                    "hyphen U+2010": "INV\u201077", "non-breaking hyphen": "INV\u201177", "en dash": "INV\u201377",
                    "em dash": "INV\u201477", "minus sign": "INV\u221277", "full-width hyphen": "INV\uff0d77",
                    "full-width letters and digits": "\uff29\uff2e\uff36-\uff17\uff17"}
        got = self.accepted(variants.values())
        self.assertEqual([name for name, text in variants.items() if text in got], [])
        # A slash is a slash in any form, and another invoice than a dash's.
        self.draft("INV/77")
        self.assertEqual(self.accepted(["INV\uff0f77"]), [])

    def test_03_the_financial_year_boundary(self):
        self.draft("INV-77", D(2026, 3, 31))
        self.assertEqual(self.accepted(["inv-77"], D(2026, 3, 31)), [])
        self.assertEqual(self.accepted(["INV-77"], D(2026, 4, 1)), ["INV-77"])  # a new supplier year
        self.draft("INV-77", D(2026, 4, 1))
        self.assertEqual(self.accepted(["inv-77"], D(2026, 4, 2)), [])
        self.assertEqual(self.accepted(["inv-77"], D(2027, 3, 31)), [])
        self.assertEqual(self.accepted(["inv-77"], D(2027, 4, 1)), ["inv-77"])

    def test_04_redating_a_draft_across_the_boundary_into_a_collision_is_refused(self):
        self.draft("INV-77", D(2026, 3, 31))
        later = self.draft("INV-77", D(2026, 4, 1))
        later.bill_date = D(2026, 3, 31)
        with self.assertRaises(ValidationError):
            later.save()

    def test_05_another_vendor_may_use_the_number(self):
        self.draft("INV-77")
        self.assertEqual(self.accepted(["INV-77"], D(2026, 6, 10)), [])
        other = self.other_vendor()
        self.draft("INV-77", vendor=other)

    def test_06_a_bill_fully_debited_still_holds_its_number(self):
        """
        Q2, for the accountant: a cancelled invoice still holds its number (carried over, not new). A GST
        invoice number is unique per supplier per year, so the corrected invoice is expected under a new
        number; this records the rule as it stands until the accountant says otherwise.
        """
        bill = self.draft("INV-77")
        BillLine.objects.create(bill=bill, description="x", quantity=Z("1"), unit_price=Z("100"),
                                expense_account=self.expense)
        bill.post()
        Bill.objects.get(pk=bill.pk).create_debit_note()
        self.assertEqual(self.accepted(["INV-77"]), [])

    def test_07_a_reference_with_no_letter_or_digit_is_refused(self):
        """'-' and '/' and '.' as a placeholder: refused in words, so no two stand side by side (review F7)."""
        for placeholder in ("-", "/", ".", " - "):
            with self.assertRaisesMessage(ValidationError, "has no letter or digit"):
                self.draft(placeholder)

    def test_08_the_migrations_frozen_key_is_the_models(self):
        migration = importlib.import_module("apps.purchasing.migrations.0079_document_numbers_keyed_by_one_rule")
        samples = ["INV-77", "inv 77", "X/1/12", "", None, " a.b-c/d ", "ß-1", "INV 77", "ﬃ"]
        days = [D(2026, 3, 31), D(2026, 4, 1), D(2027, 1, 1), None]
        differing = [(s, d) for s in samples for d in days
                     if migration.vendor_invoice_key(s, d) != vendor_invoice_key(s, d)]
        self.assertEqual(differing, [])

    def test_10_a_long_reference_of_ligatures_is_refused_in_words_not_by_the_column(self):
        """On PostgreSQL the 95-character key overflows varchar(80): a DataError, a 500, not a ValidationError."""
        from django.db import DataError

        try:
            self.draft("\ufb03" * 30)
        except ValidationError:
            return
        except DataError as exc:
            self.fail(f"the column refused it, not the form: {exc}")

    def test_09_a_reference_that_folds_past_the_column_is_refused_in_words(self):
        """reference is 64 characters; folding can lengthen it (ﬃ -> FFI). Past 64 compared, refused, never a 500."""
        with self.assertRaisesMessage(ValidationError, "at most 64"):
            self.draft("\ufb03" * 64)
        self.assertLessEqual(len(vendor_invoice_key("x" * 64, D(2026, 6, 1))), 80)


# ---------------------------------------------------------------- O154, O190: the supplier's credit-note number
class SupplierCreditNoteNumberTests(ProbeCase):
    def note(self):
        bill = self.hand_bill(None, "10", "100", link=False)
        bill.post()
        return Bill.objects.get(pk=bill.pk).create_debit_note(quantities={bill.lines.get(): Z("4")})

    def test_01_set_once_on_a_posted_note_and_never_changed(self):
        note = self.note()
        self.assertTrue(note.posted)
        note.record_supplier_note("CN-9", D(2026, 1, 20))
        for number in ("CN-9", "CN-10", "cn 9"):
            fresh = Bill.objects.get(pk=note.pk)
            with self.assertRaises(ValidationError):
                fresh.record_supplier_note(number, D(2026, 1, 21))
        kept = Bill.objects.get(pk=note.pk)
        self.assertEqual((kept.supplier_note_number, kept.supplier_note_date), ("CN-9", D(2026, 1, 20)))

    def test_02_a_full_save_cannot_slip_a_new_number_past_a_posted_note(self):
        note = self.note()
        note.record_supplier_note("CN-9", D(2026, 1, 20))
        fresh = Bill.objects.get(pk=note.pk)
        fresh.supplier_note_number = "CN-10"
        try:
            fresh.save()
        except ValidationError:
            pass
        self.assertEqual(Bill.objects.get(pk=note.pk).supplier_note_number, "CN-9")

    def test_03_the_key_is_kept_with_the_number_so_gstr2b_can_pair_it(self):
        note = self.note()
        note.record_supplier_note("CN-9", D(2026, 1, 20))
        kept = Bill.objects.get(pk=note.pk)
        self.assertTrue(kept.supplier_note_key.endswith("CN-9") or "CN9" in kept.supplier_note_key.upper())

    def test_05_a_number_given_without_its_date_is_completed_with_a_reason_by_whoever_posts_bills(self):
        """
        Q1, decided: corrected by the role that posts bills, with a reason the history keeps; never cleared.
        In setup_roles that is the AP Manager (purchasing.post_bill); the Controller does not post bills.
        """
        from django.contrib.auth.models import Group, User
        from django.core.exceptions import PermissionDenied
        from django.core.management import call_command

        from apps.core.history import RecordEvent

        call_command("setup_roles", verbosity=0)
        controller, clerk = User.objects.create_user("ap"), User.objects.create_user("clerk")
        controller.groups.add(Group.objects.get(name="AP Manager"))
        clerk.groups.add(Group.objects.get(name="Purchasing Clerk"))
        note = self.note()
        note.record_supplier_note("CN-9")
        with self.assertRaisesMessage(ValidationError, "corrected only with a reason"):
            Bill.objects.get(pk=note.pk).record_supplier_note("CN-9", D(2026, 1, 20), by=controller)
        with self.assertRaises(PermissionDenied):
            Bill.objects.get(pk=note.pk).record_supplier_note("CN-9", D(2026, 1, 20), by=clerk, reason="date")
        with self.assertRaisesMessage(ValidationError, "Give the number"):
            Bill.objects.get(pk=note.pk).record_supplier_note("", by=controller, reason="clear it")
        Bill.objects.get(pk=note.pk).record_supplier_note("CN-9", D(2026, 1, 20), by=controller,
                                                          reason="Date left off the first time")
        kept = Bill.objects.get(pk=note.pk)
        self.assertEqual((kept.supplier_note_number, kept.supplier_note_date), ("CN-9", D(2026, 1, 20)))
        self.assertTrue(RecordEvent.objects.filter(
            object_id=note.pk, action="correct supplier note",
            summary="Supplier credit note CN-9 corrected to CN-9 dated 2026-01-20: Date left off the first time",
            who=controller).exists())

    def test_04_two_notes_cannot_answer_one_credit_note(self):
        first, second = self.note(), None
        bill = self.hand_bill(None, "10", "100", link=False, reference="B-2")
        bill.post()
        second = Bill.objects.get(pk=bill.pk).create_debit_note(quantities={bill.lines.get(): Z("4")})
        first.record_supplier_note("CN-9", D(2026, 1, 20))
        with self.assertRaisesMessage(ValidationError, "one credit note is answered by one debit note"):
            Bill.objects.get(pk=second.pk).record_supplier_note("CN-9", D(2026, 1, 21))


# ---------------------------------------------------------------- O188, O189: MSME and due dates
class MsmePaidOnAndDueDeliveryByDeliveryTests(tests_msme.MsmeTestCase):
    def setUp(self):
        super().setUp()
        self.tds_payable = Account.objects.create(code="2250", name="TDS payable", account_type=AccountType.LIABILITY)
        self.section = TdsSection.objects.create(code="194J", name="Professional", mode="whole",
                                                 payable_account=self.tds_payable)
        confirmed_rate(self.section, "10", "20")
        PartyTaxProfile.objects.filter(party=self.vendor).update(pan="AAAPL1234C", tds_section=self.section)

    def test_01_a_part_settled_by_tds_a_note_and_a_payment_is_paid_on_the_last_of_them(self):
        """1,000 billed 1 Jun net 60 (due 16 Jul): TDS 100 on 10 Jun, note 300 on 20 Jun, 600 paid 25 Jul: 9 days late."""
        bill = self.bill("1000", terms=self.net60)
        bill.deduct_tds(on_date=D(2026, 6, 10))
        note = bill.create_debit_note(quantities={bill.lines.get(): Z("0.3")})
        Bill.objects.filter(pk=note.pk).update(bill_date=D(2026, 6, 20))
        self.pay(bill, "600", D(2026, 7, 25))
        row = self.row(bill, D(2026, 12, 31))
        self.assertEqual((row["due"], row["paid_on"], row["days_late"], row["unpaid"]),
                         (D(2026, 7, 16), D(2026, 7, 25), 9, Z("0")))

    def test_02_a_part_settled_only_by_tds_and_a_note_is_paid_on_the_later_of_the_two(self):
        bill = self.bill("1000", terms=self.net60)
        bill.deduct_tds(on_date=D(2026, 6, 10))
        note = bill.create_debit_note(quantities={bill.lines.get(): Z("0.9")})
        Bill.objects.filter(pk=note.pk).update(bill_date=D(2026, 6, 20))
        row = self.row(bill, D(2026, 12, 31))
        self.assertEqual((row["paid_on"], row["days_late"], row["unpaid"]), (D(2026, 6, 20), 0, Z("0")))

    def test_03_a_reversed_deduction_does_not_count_as_a_settlement_and_the_part_is_unpaid(self):
        bill = self.bill("1000", terms=self.net60)
        made = bill.deduct_tds(on_date=D(2026, 6, 10))
        made.reverse()
        row = self.row(Bill.objects.get(pk=bill.pk), D(2026, 12, 31))
        self.assertEqual((row["paid_on"], row["unpaid"]), (None, Z("1000")))

    def test_04_objected_then_accepted_the_run_the_schedule_and_pay_by_agree(self):
        probe = tests_msme.AcceptedOnDeliveryTests
        order, line = probe.order(self)
        probe.receive(self, order, line, D(2026, 6, 1), "6")
        second, _ = probe.receive(self, order, line, D(2026, 6, 20), "4")
        second.record_objection(D(2026, 6, 25), "Letter PUR/15: GSM short")
        bill = probe.bill_for(self, order, line, D(2026, 6, 25), "10")
        standing = [(r["due_date"], r["amount"]) for r in bill.installments()]
        self.assertEqual(standing, [(D(2026, 7, 16), Z("600.00")), (D(2026, 8, 24), Z("400.00"))])
        second.remove_objection(D(2026, 7, 5))
        bill = Bill.objects.get(pk=bill.pk)
        rows = [(r["due_date"], r["amount"]) for r in bill.installments()]
        self.assertEqual(rows, [(D(2026, 7, 16), Z("600.00")), (D(2026, 8, 19), Z("400.00"))])
        self.assertEqual(bill.pay_by(), D(2026, 8, 19))
        for by in (D(2026, 7, 15), D(2026, 7, 16), D(2026, 8, 18), D(2026, 8, 19)):
            listed = [e for run in payment_run(due_by=by) for e in run["bills"]]
            due = sum((r["outstanding"] for r in bill.installments() if r["due_date"] <= by), Z("0"))
            self.assertEqual([(e["amount_due"], e["due_date"]) for e in listed],
                             [(due, bill.pay_by(by=by))] if due else [], by)

    def test_05_once_the_first_part_is_paid_the_run_shows_the_second_parts_day(self):
        probe = tests_msme.AcceptedOnDeliveryTests
        order, line = probe.order(self)
        probe.receive(self, order, line, D(2026, 6, 1), "6")
        probe.receive(self, order, line, D(2026, 6, 20), "4")
        bill = probe.bill_for(self, order, line, D(2026, 6, 25), "10")
        self.pay(bill, "600", D(2026, 7, 10))
        bill = Bill.objects.get(pk=bill.pk)
        self.assertEqual(bill.pay_by(by=D(2026, 8, 5)), D(2026, 8, 4))
        (entry,) = [e for run in payment_run(due_by=D(2026, 8, 5)) for e in run["bills"]]
        self.assertEqual((entry["amount_due"], entry["due_date"]), (Z("400.00"), D(2026, 8, 4)))


# ---------------------------------------------------------------- O211: returns in posting order
class ReturnsDebitTheBillsHoldingTheirGoodsTests(ProbeCase):
    def grni_and_payable(self):
        return self.balance(self.grni), -self.balance(self.payable)

    def notes(self):
        return [(n.debits.reference, n.total()) for n in Bill.objects.filter(debits__isnull=False).order_by("pk")]

    def test_01_a_return_between_two_bills_leaves_grni_clear_and_payables_at_what_the_goods_held_cost(self):
        order = self.order("20", "5")
        line = order.lines.get()
        self.receive_on(order, "10", D(2026, 1, 2))
        r2 = self.receive_on(order, "10", D(2026, 1, 3))
        self.hand_bill(line, "10", "5", order=order, reference="B-1").post()
        r2.create_return(quantities={r2.lines.get(): Z("4")}, debit_bills=True)
        # R2's 4 went back before any bill held them: B-1 is not debited (review F2), so 6 are left to bill.
        self.hand_bill(line, "6", "4.80", order=order, reference="B-2", day=D(2026, 1, 15)).post()
        self.assertEqual(self.notes(), [])
        grni, payable = self.grni_and_payable()
        billed = sum((l.quantity for l in line.bill_lines.filter(bill__debits__isnull=True)), Z("0"))
        debited = sum((l.quantity for l in line.bill_lines.filter(bill__debits__isnull=False)), Z("0"))
        self.assertEqual(billed - debited, Z("16"))
        self.assertEqual(grni, Z("0.00"), f"notes {self.notes()} payable {payable}")

    def test_02_two_returns_of_two_receipts_each_debit_the_bill_that_paid_for_their_goods(self):
        order = self.order("20", "5")
        line = order.lines.get()
        r1 = self.receive_on(order, "10", D(2026, 1, 2))
        r2 = self.receive_on(order, "10", D(2026, 1, 3))
        self.hand_bill(line, "10", "5", order=order, reference="B-1").post()
        self.hand_bill(line, "10", "4.80", order=order, reference="B-2", day=D(2026, 1, 11)).post()
        r1.create_return(quantities={r1.lines.get(): Z("3")}, debit_bills=True)
        r2.create_return(quantities={r2.lines.get(): Z("4")}, debit_bills=True)
        self.assertEqual(self.notes(), [("B-1", Z("15.00")), ("B-2", Z("19.20"))])
        self.assertEqual(self.grni_and_payable(), (Z("0.00"), Z("63.80")))

    def test_03_returns_before_and_between_bills_find_the_bill_that_paid_for_each_receipt(self):
        order = self.order("15", "5")
        line = order.lines.get()
        first = self.receive_on(order, "10", D(2026, 1, 2))
        first.create_return(quantities={first.lines.get(): Z("6")}, debit_bills=True)
        second = self.receive_on(order, "5", D(2026, 1, 3))
        self.hand_bill(line, "4", "5", order=order, reference="B-1").post()
        self.hand_bill(line, "5", "4.80", order=order, reference="B-2", day=D(2026, 1, 11)).post()
        second.create_return(quantities={second.lines.get(): Z("2")}, debit_bills=True)
        self.assertEqual(self.notes(), [("B-2", Z("9.60"))])
        first.create_return(quantities={first.lines.get(): Z("4")}, debit_bills=True)
        self.assertEqual(self.notes(), [("B-2", Z("9.60")), ("B-1", Z("20.00"))])
        self.assertEqual(self.grni_and_payable(), (Z("0.00"), Z("14.40")))

    def test_04_a_return_that_straddles_two_bills_of_one_receipt_debits_both_and_clears_grni(self):
        order = self.order("10", "5")
        line = order.lines.get()
        r1 = self.receive_on(order, "10", D(2026, 1, 2))
        self.hand_bill(line, "5", "5", order=order, reference="B-1").post()
        self.hand_bill(line, "5", "4.80", order=order, reference="B-2", day=D(2026, 1, 11)).post()
        r1.create_return(quantities={r1.lines.get(): Z("8")}, debit_bills=True)
        # Which bill goes first is a convention; that both are debited and GRNI clears is not.
        self.assertEqual(len(self.notes()), 2)
        self.assertEqual(self.grni_and_payable()[0], Z("0.00"))


# ---------------------------------------------------------------- O91, O99, O167: the match and the approval
class TheMatchWeighsNetPricesTests(ProbeCase):
    def test_01_the_match_compares_net_prices_with_a_tolerance_on_the_net(self):
        from apps.core.models import Company
        company = Company.get()
        company.purchase_price_tolerance_percent = Z("5")
        company.save()
        order = self.order("10", "100", discount="10")  # net 90, allowed up to 94.50
        line = order.lines.get()
        self.receive_on(order, "10", D(2026, 1, 5))
        bad = []
        # 99.45 less 5% is 94.4775 a unit, inside 94.50: the reviewer's expectation was the arithmetic (mistake 7).
        for price, discount in (("100", "10"), ("90", "0"), ("94.50", "0"), ("94.51", "0"), ("100", "0"), ("99.45", "5")):
            try:
                self.hand_bill(line, "1", price, discount=discount, order=order,
                               reference=f"M-{price}-{discount}").post()
                bad.append((price, discount, "accepted"))
            except ValidationError:
                bad.append((price, discount, "refused"))
        self.assertEqual(bad, [("100", "10", "accepted"), ("90", "0", "accepted"), ("94.50", "0", "accepted"),
                               ("94.51", "0", "refused"), ("100", "0", "refused"), ("99.45", "5", "accepted")])


class APolicyAskedOfConfirmedOrderChangesTests(tests_vendor_prices.VendorPriceTestCase):
    def setUp(self):
        super().setUp()
        PurchaseApprovalPolicy.objects.create(code="STD", name="Standard", max_order_value=Z("1000"))

    def gst(self):
        group = TaxGroup.objects.create(code="GST", name="GST")
        itc = Account.objects.create(code="1300", name="GST input", account_type=AccountType.ASSET)
        return Tax.objects.create(code="GST18", name="GST 18%", rate=Z("18"), group=group, scope=TaxScope.BOTH,
                                  collected_account=itc, paid_account=itc)

    def test_01_tax_added_to_a_confirmed_line_is_asked_of_the_policy_like_a_price(self):
        """900 confirmed under a 1,000 limit; GST 18% takes it to 1,062: refused or needs approval."""
        line = self.draft_line("18", price="50")
        line.order.confirm()
        line = PurchaseOrderLine.objects.get(pk=line.pk)
        tax = self.gst()
        try:
            line.taxes.set([tax])
        except ValidationError:
            return
        self.assertLessEqual(PurchaseOrder.objects.get(pk=line.order_id).total(), Z("1000"),
                             "total now 1062.00 on an order confirmed at 900 under a 1,000 limit; nobody asked")

    def test_02_a_line_added_to_a_confirmed_order_past_the_limit_is_refused(self):
        line = self.draft_line("18", price="50")
        line.order.confirm()
        with self.assertRaises(ValidationError):
            PurchaseOrderLine.objects.create(order=line.order, item=self.item, uom=self.item.uom, quantity=Z("5"),
                                             unit_price=Z("50"))

    def test_03_a_vendor_price_dropping_after_the_order_does_not_block_an_unrelated_change(self):
        PurchaseApprovalPolicy.objects.all().update(require_approval_without_vendor_price=True, max_order_value=None)
        self.agree("50")
        line = self.draft_line("5", price="50")
        line.order.confirm()
        self.agree("40", min_quantity="1")
        line = PurchaseOrderLine.objects.get(pk=line.pk)
        line.quantity = Z("4")
        line.save()


class UnbilledGoodsReturnedDebitNoBillTests(ProbeCase):
    def test_01_goods_returned_unbilled_raise_no_debit_note_against_a_bill_that_paid_for_other_goods(self):
        """
        20 ordered at 5; R1 10 and R2 10 in; B1 bills 10 at 5.00 (R1's goods); R2's 4 go back before anything
        bills them. The supplier then bills what is held of R2: 6 at 4.80. Owed: 50.00 + 28.80 = 78.80;
        GRNI clears; the return debited no bill, since no bill had billed those goods.
        """
        order = self.order("20", "5")
        line = order.lines.get()
        self.receive_on(order, "10", D(2026, 1, 2))
        r2 = self.receive_on(order, "10", D(2026, 1, 3))
        self.hand_bill(line, "10", "5", order=order, reference="B-1").post()
        r2.create_return(quantities={r2.lines.get(): Z("4")}, debit_bills=True)
        notes = [(n.debits.reference, n.total()) for n in Bill.objects.filter(debits__isnull=False)]
        self.hand_bill(line, "6", "4.80", order=order, reference="B-2", day=D(2026, 1, 15)).post()
        grni, payable = self.balance(self.grni), -self.balance(self.payable)
        self.assertEqual((notes, grni, payable), ([], Z("0"), Z("78.80")))

    def test_02_conservation_over_random_sequences_of_receipts_bills_and_returns(self):
        """GRNI = (net received - net billed) x order price after any sequence the system lets through."""
        import random

        from django.db import transaction

        failures = []
        for seed in range(25):
            rnd = random.Random(seed)
            with transaction.atomic():
                sid = transaction.savepoint()
                from apps.purchasing.models import GoodsReceipt
                order = self.order("40", "5", vendor=self.vendor)
                line = order.lines.get()
                receipts, day = [], 2
                for step in range(rnd.randint(4, 9)):
                    kind = rnd.choice(["receive", "bill", "return", "return"])
                    day += 1
                    try:
                        with transaction.atomic():
                            if kind == "receive":
                                receipts.append(self.receive_on(order, str(rnd.randint(2, 8)), D(2026, 1, min(day, 28))))
                            elif kind == "bill":
                                price = rnd.choice(["5", "4.80", "4.60"])
                                self.hand_bill(line, str(rnd.randint(1, 8)), price, order=order,
                                               reference=f"S{seed}-{step}", day=D(2026, 1, min(day, 28))).post()
                            elif receipts:
                                receipt = rnd.choice(receipts)
                                rl = receipt.lines.get()
                                receipt.create_return(quantities={rl: Z(str(rnd.randint(1, 4)))}, debit_bills=True)
                    except ValidationError:
                        continue
                billed = sum((l.quantity for l in line.bill_lines.filter(bill__posted=True, bill__debits__isnull=True)), Z("0"))
                debited = sum((l.quantity for l in line.bill_lines.filter(bill__posted=True, bill__debits__isnull=False)), Z("0"))
                received = sum((l.quantity_received for l in line.receipt_lines.filter(receipt__posted=True)
                                .exclude(receipt__reverses__isnull=False)), Z("0")) - sum(
                    (l.quantity_received for l in line.receipt_lines.filter(receipt__posted=True, receipt__reverses__isnull=False)), Z("0"))
                expected = (received - (billed - debited)) * Z("-5")
                got = self.balance(self.grni)
                if got != expected:
                    failures.append((seed, "grni", got, "expected", expected, "received", received, "billed", billed, "debited", debited))
                transaction.savepoint_rollback(sid)
        self.assertEqual(failures, [])
