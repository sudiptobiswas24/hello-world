"""review_sechr probes (O262, O360, O270)."""
from decimal import Decimal

from django.core.exceptions import ValidationError

from .tests_pay_outputs import *  # noqa
from .tests_pay_outputs import ALeaversAdvanceLeftIsSettledTests as Base, D


class P_O262(Base):
    def test_zz_print(self):
        from .shortfalls import owed_back, owed_back_line
        print("recoverable", self.slip.recoverable, "owed_back", owed_back(self.slip), "line", owed_back_line(self.slip),
              "advance balance", self.advance.balance(), "ledger", self.owed_by(self.leaver))

    def test_a_slip_settlement_and_an_advance_settlement_cannot_both_clear_one_debt(self):
        """Fact: the receivable (ledger) can never be settled twice over by records: sum of what records say is owed
        must equal what the books say is owed."""
        from .shortfalls import owed_back
        self.settle("write_off", "3860.00")           # against the advance
        books = self.owed_by(self.leaver)
        records = self.advance.balance() + owed_back(self.slip)
        print("books", books, "advance", self.advance.balance(), "slip", owed_back(self.slip))
        self.assertEqual(records, books)

    def test_the_slip_alone_cannot_settle_what_the_advance_did(self):
        from .shortfalls import ShortfallSettlement
        self.settle("write_off", "3860.00")
        try:
            ShortfallSettlement.objects.create(payslip=self.slip, kind="receipt", amount=Decimal("1.00"),
                payment=self.paid_back("1.00"), date=D(2026, 7, 10))
            created = True
        except ValidationError:
            created = False
        self.assertFalse(created)

    def test_receipt_larger_than_a_payment_part_each(self):
        """Part receipt, part write-off, where the write-off's date precedes the receipt: allowed? and void order."""
        self.settle("receipt", "1000.00", payment=self.paid_back("1000.00"))
        w = self.settle("write_off", "2860.00")
        from .shortfalls import ShortfallSettlement
        # void the receipt first while the write-off stands: books must still foot
        r = ShortfallSettlement.objects.get(kind="receipt")
        r.void(on_date=D(2026, 7, 10))
        self.assertEqual(self.advance.balance(), self.owed_by(self.leaver) - 0 if False else self.advance.balance())
        self.assertEqual(self.owed_by(self.leaver), Decimal("1000.00"))
        self.assertEqual(self.advance.balance(), Decimal("1000.00"))


from .tests_people import AClaimIsChargedToExpenseAccountsOnlyTests as ClaimBase
from apps.accounting.models import Account, AccountType


class P_O360(ClaimBase):
    def test_patch_a_draft_line_to_an_asset_is_refused_over_the_api(self):
        riley = self.as_(self.riley.user)
        claim_id = riley.post("/api/hr/expense-claims/", {"purpose": "Drill"}, format="json").json()["id"]
        line = riley.post("/api/hr/expense-lines/", {"claim": claim_id, "spent_on": "2026-06-01",
                          "expense_account": self.tools.pk, "description": "Drill", "amount": "2000"}, format="json")
        self.assertEqual(line.status_code, 201, line.content)
        for how in ("patch", "put"):
            body = {"expense_account": self.equipment.pk}
            if how == "put":
                body = {"claim": claim_id, "spent_on": "2026-06-01", "expense_account": self.equipment.pk,
                        "description": "Drill", "amount": "2000"}
            r = getattr(riley, how)(f"/api/hr/expense-lines/{line.json()['id']}/", body, format="json")
            self.assertEqual(r.status_code, 400, (how, r.content))

    def test_nested_lines_on_a_claim_create_cannot_carry_an_asset_account(self):
        riley = self.as_(self.riley.user)
        r = riley.post("/api/hr/expense-claims/", {"purpose": "Drill", "lines": [
            {"spent_on": "2026-06-01", "expense_account": self.equipment.pk, "description": "Drill", "amount": "2000"}]},
            format="json")
        from .expenses import ExpenseLine
        self.assertFalse(ExpenseLine.objects.filter(expense_account=self.equipment).exists(), r.content)

    def test_an_account_that_became_inactive_stops_payment(self):
        claim = self.claim(lines=(("Drill", "tools", "2000"),))
        claim.submit(); claim.approve(self.manager)
        Account.objects.filter(pk=self.tools.pk).update(is_active=False)
        r = self.as_(self.books).post(f"/api/hr/expense-claims/{claim.pk}/pay/", {
            "paid_from": self.cash.pk, "on_date": "2026-06-01"}, format="json")
        self.assertEqual(r.status_code, 400, r.content)
