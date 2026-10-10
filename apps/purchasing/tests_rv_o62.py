import datetime
from decimal import Decimal as D
from django.core.exceptions import ValidationError
from apps.purchasing.tests_tds import ChallanOnTheStatementTests as Base
from apps.accounting.models import BankStatement, BankStatementLine


def show(*a):
    print("\nRV", *a)


def rec(st):
    r = st.reconciliation()
    return dict(ledger=r["ledger_balance"], stmt=r["statement_balance"], unp=r["unpresented_total"],
                n_ent=len(r["unpresented_entries"]), unresolved=len(r["unresolved_lines"]), diff=r["difference"])


class O62(Base):
    def test_rv_void_challan_after_matched_and_closed(self):
        self.line.match_entry(self.challan.journal_entry)
        show("matched", rec(self.july))
        self.july.close()
        try:
            self.challan.void(on_date=datetime.date(2026, 7, 9))
            show("challan voided after statement closed; recon:", rec(self.july))
        except ValidationError as e:
            show("void refused", e)
        try:
            self.line.refresh_from_db(); self.line.unmatch()
            show("unmatch on closed statement allowed", rec(self.july))
        except ValidationError as e:
            show("unmatch refused", e)

    def test_rv_unmatch_then_recon(self):
        self.line.match_entry(self.challan.journal_entry)
        a = rec(self.july)
        self.line.unmatch()
        b = rec(self.july)
        show("before unmatch", a, "after", b)

    def test_rv_void_challan_unmatched_then_post_line(self):
        # challan voided same day as paid (never left the bank) with no line for it
        self.challan.void(on_date=datetime.date(2026, 7, 9))
        show("voided, unmatched line; recon:", rec(self.july))
        try:
            self.line.match_entry(self.challan.journal_entry)
            show("original matched; recon:", rec(self.july))
        except ValidationError as e:
            show("match refused", e)

    def test_rv_line_matched_to_voided_entry(self):
        self.challan.void(on_date=datetime.date(2026, 7, 9))
        ch = type(self.challan).objects.get(pk=self.challan.pk)
        try:
            self.line.match_entry(ch.voided_entry)
            show("-700 line matched to the REVERSAL entry (+700)?? accepted")
        except ValidationError as e:
            show("refused sign", str(e)[:100])

    def test_rv_entry_from_an_earlier_statement_period(self):
        # statement for August with the July entry unmatched: appears unpresented on August too
        aug = BankStatement.objects.create(bank_account=self.bank, start_date=datetime.date(2026, 8, 1),
                                           end_date=datetime.date(2026, 8, 31), opening_balance=D("-700"), closing_balance=D("-700"))
        show("Aug recon with challan never matched:", rec(aug))
