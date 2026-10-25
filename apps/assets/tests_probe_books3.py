"""review_books3 probes: O353 capitalising with a draft debit note waiting."""
import datetime
from decimal import Decimal as D

from apps.core.models import Currency, ExchangeRate
from apps.assets.models import FixedAsset
from .tests import CapitalisationFixture


class O353(CapitalisationFixture):
    def check(self, name, got, want):
        print(("PASS " if got == want else "FAIL ") + name, "" if got == want else f"got {got} expected {want}")
        return got == want

    def test_draft_deleted_after_capitalising(self):
        line = self.bill_line("3", "1000.01")
        note = line.bill.create_debit_note(quantities={line: D("1")}, post=False)
        line.capitalise_as_asset(self.category)
        note.delete()
        line.refresh_from_db()
        ok = [self.check("purchases keeps the draft's share after delete (stranded)", self.balance(self.expense), D("1000.01"))]
        from django.core.exceptions import ValidationError
        try:
            line.capitalise_as_asset(self.category); third = "capitalised"
        except ValidationError as e:
            third = "refused"
        ok.append(self.check("third unit can be capitalised after the draft is deleted", third, "capitalised"))
        ok.append(self.check("plant == sum of asset costs", self.balance(self.plant),
                             sum((a.cost for a in FixedAsset.objects.filter(bill_line=line)), D("0"))))
        self.assertTrue(all(ok))

    def test_draft_posts_then_is_voided_or_second_draft(self):
        line = self.bill_line("3", "1000.01")
        note = line.bill.create_debit_note(quantities={line: D("1")}, post=False)
        line.capitalise_as_asset(self.category)
        ok = []
        note.post()
        ok.append(self.check("after the counted draft posts: purchases 0", self.balance(self.expense), D("0")))
        ok.append(self.check("AP net = 3000.03 - 1000.01", -self.balance(self.payable), D("2000.02")))
        ok.append(self.check("plant == AP net", self.balance(self.plant), -self.balance(self.payable)))
        self.assertTrue(all(ok))

    def test_edit_a_waiting_draft_after_capitalising(self):
        line = self.bill_line("3", "1000.01")
        note = line.bill.create_debit_note(quantities={line: D("1")}, post=False)
        line.capitalise_as_asset(self.category)
        dl = note.lines.get()
        dl.quantity = D("2")
        from django.core.exceptions import ValidationError
        try:
            dl.save(); note.refresh_from_db(); note.post(); res = "posted 2 units"
        except ValidationError as e:
            res = "refused"
        # the books may not take back more than the two assets left in the account
        self.check("editing the counted draft to 2 units and posting", res, "refused")
        self.assertGreaterEqual(self.balance(self.plant), D("0"))
        self.assertEqual(self.balance(self.plant), sum((a.cost for a in FixedAsset.objects.filter(bill_line=line).exclude(status="cancelled")), D("0")))
        self.assertGreaterEqual(self.balance(self.expense), D("0"), "purchases must not go credit")


class O353Foreign(CapitalisationFixture):
    def test_foreign_rates_leave_nothing_in_purchases(self):
        from apps.purchasing.models import Bill, BillLine
        from django.db import transaction
        eur = Currency.objects.create(code="EUR", name="Euro")
        bad = []
        n = 0
        for rate in ("1.2", "1.37777", "0.91234", "83.4567", "1.00005", "1.11111"):
            for price, qty, dq in (("1000.01", "3", "1"), ("333.33", "7", "2"), ("12.345", "5", "1"), ("0.37", "9", "4")):
                ExchangeRate.objects.filter(currency=eur).delete()
                ExchangeRate.objects.create(currency=eur, rate=D(rate), valid_from=datetime.date(2025, 12, 1))
                sid = transaction.savepoint()
                bill = Bill.objects.create(vendor=self.vendor, bill_date=datetime.date(2026, 1, 1), payable_account=self.payable, currency=eur)
                line = BillLine.objects.create(bill=bill, description="M", quantity=D(qty), unit_price=D(price), expense_account=self.expense)
                bill.post()
                note = bill.create_debit_note(quantities={line: D(dq)}, post=False)
                before_plant, before_exp = self.balance(self.plant), self.balance(self.expense)
                assets = line.capitalise_as_asset(self.category)
                note.post()
                n += 1
                exp = self.balance(self.expense)
                plant = self.balance(self.plant)
                ap = -(self.balance(self.payable))
                if exp != 0 or plant != ap:
                    bad.append((rate, price, qty, dq, "expense", exp, "plant", plant, "AP", ap))
                transaction.savepoint_rollback(sid)
        print(("PASS" if not bad else "FAIL"), "foreign capitalise-with-draft leaves purchases 0 and plant == AP net:", n, "cases,", len(bad), "bad")
        for b in bad[:8]: print("   ", b)
        self.assertFalse(bad)
