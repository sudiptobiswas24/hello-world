"""
Audit probes, accounting with GST (9 October audit round).

Each test states one claim about money or state and fails with the
observed and expected figures. Expected figures were worked in separate
plain-Python scripts, not typed from memory. Not regression tests yet:
a probe that fails here is a finding.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase

from apps.core.models import ExchangeRate, Party, PartyRole, PartyRoleAssignment

from .models import (
    AccountingPeriod,
    Account,
    AccountType,
    BankStatement,
    BankStatementLine,
    JournalLine,
    Payment,
    PaymentDirection,
)
from .tests_fx_settlement import FxSettlementTestCase

D = Decimal


def balance(account, party=None):
    rows = JournalLine.objects.filter(account=account, entry__posted=True)
    if party is not None:
        rows = rows.filter(party=party)
    rows = rows.aggregate(debit=Sum("debit"), credit=Sum("credit"))
    return (rows["debit"] or D("0")) - (rows["credit"] or D("0"))


class FxResidueProbe(FxSettlementTestCase):
    """
    100.09 EUR booked at 1.12345 (5 Jan) and settled in full at 1.09876
    (15 Feb). Booked base 112.45, paid base 109.97: 2.48 to clear. The
    code clears round(100.09 * (1.12345 - 1.09876)) = 2.47 (calc/fx.py).
    """

    def setUp(self):
        super().setUp()
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.12345"), valid_from=datetime.date(2026, 1, 5))
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.09876"), valid_from=datetime.date(2026, 2, 1))

    def invoice(self, amount):
        from apps.sales.models import SalesOrder, SalesOrderLine

        order = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 1, 5),
                                          currency=self.eur)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("1"),
                                      unit_price=D(amount), revenue_account=self.revenue)
        order.confirm()
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 1, 5))
        invoice.post()
        return invoice

    def bill(self, amount):
        from apps.purchasing.models import Bill, BillLine

        bill = Bill.objects.create(vendor=self.vendor, bill_date=datetime.date(2026, 1, 5),
                                   payable_account=self.ap, currency=self.eur)
        BillLine.objects.create(bill=bill, description="Parts", quantity=D("1"), unit_price=D(amount),
                                expense_account=self.expense)
        bill.post()
        return bill

    def test_a_settled_foreign_invoice_leaves_nothing_on_the_receivable(self):
        from apps.sales.models import InvoicePayment

        invoice = self.invoice("100.09")
        receipt = self.euro_payment(PaymentDirection.RECEIPT, "100.09")
        InvoicePayment.objects.create(invoice=invoice, payment=receipt, amount=D("100.09"))
        self.assertEqual(invoice.amount_due(), D("0"))
        self.assertEqual(balance(self.ar), D("0"),
                         f"AR after full settlement: observed {balance(self.ar)}, expected 0 "
                         f"(booked 112.45, paid 109.97, FX {balance(self.loss)})")

    def test_a_settled_foreign_bill_leaves_nothing_on_the_payable(self):
        from apps.purchasing.models import BillPayment

        bill = self.bill("100.09")
        paying = self.euro_payment(PaymentDirection.DISBURSEMENT, "100.09")
        BillPayment.objects.create(bill=bill, payment=paying, amount=D("100.09"))
        self.assertEqual(bill.amount_due(), D("0"))
        self.assertEqual(balance(self.ap), D("0"),
                         f"AP after full settlement: observed {balance(self.ap)}, expected 0 "
                         f"(booked 112.45, paid 109.97, FX {balance(self.gain)})")

    def test_a_deposit_drawn_in_two_halves_leaves_nothing_held(self):
        """100 EUR taken up front at 1.12345 is 112.35 held. Drawn 50 + 50 onto two invoices
        of the same day, each clears 56.17 (calc/deposit.py): 0.01 stays on deposits for good."""
        from apps.core.models import Company
        from apps.sales.models import Invoice, InvoiceLine, SalesOrder, SalesOrderLine

        deposits = Account.objects.create(code="2300", name="Deposits", account_type=AccountType.LIABILITY)
        Company.objects.update(customer_deposit_account=deposits)
        order = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 1, 5),
                                          currency=self.eur)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("2"),
                                      unit_price=D("100"), revenue_account=self.revenue)
        order.confirm()
        deposit = order.create_down_payment_invoice(self.ar, amount=D("100"), invoice_date=datetime.date(2026, 1, 5))
        deposit.post()
        for _ in range(2):
            invoice = Invoice.objects.create(customer=self.customer, invoice_date=datetime.date(2026, 1, 5),
                                             receivable_account=self.ar, currency=self.eur)
            InvoiceLine.objects.create(invoice=invoice, description="Sacks", quantity=D("1"), unit_price=D("100"),
                                       revenue_account=self.revenue)
            invoice.post()
            invoice.apply_deposit(Invoice.objects.get(pk=deposit.pk), amount=D("50"))
        self.assertEqual(Invoice.objects.get(pk=deposit.pk).deposit_unapplied(), D("0"))
        self.assertEqual(balance(deposits), D("0"),
                         f"Deposits after the whole 100 EUR was drawn: observed {balance(deposits)}, expected 0")

    def test_a_foreign_receipt_from_a_closed_month_can_still_be_allocated(self):
        """Received 15 Feb, February closed, applied to the invoice in March: the
        exchange difference is realised on settlement, in an open month."""
        from apps.sales.models import InvoicePayment

        invoice = self.invoice("100.09")
        receipt = self.euro_payment(PaymentDirection.RECEIPT, "100.09")
        period = AccountingPeriod.objects.create(name="Feb", start_date=datetime.date(2026, 2, 1),
                                                 end_date=datetime.date(2026, 2, 28))
        period.close()
        try:
            InvoicePayment.objects.create(invoice=invoice, payment=receipt, amount=D("100.09"))
        except ValidationError as error:
            self.fail(f"Allocating a closed month's receipt in an open month was refused: {error}")


class StatementProbe(TestCase):
    """
    September: +1,000 received on the 2nd (on the statement, matched); a
    cheque of 400 written on the 25th and presented on 2 October. The
    September statement reads 0 -> 1,000. Books at 30 Sep: 600. The cheque is
    unpresented at 30 Sep whatever happened to it afterwards; with it named,
    the difference is 0 (calc/bank.py).
    """

    def setUp(self):
        from apps.core.models import Company, Currency

        inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        Company.objects.create(name="Co", base_currency=inr)
        acc = lambda c, t, **more: Account.objects.create(code=c, name=c, account_type=t, **more)
        self.bank = acc("1010", AccountType.ASSET, holds_money=True)
        self.ar = acc("1100", AccountType.ASSET)
        self.ap = acc("2000", AccountType.LIABILITY)
        self.party = Party.objects.create(code="P", name="P", default_currency=inr)
        PartyRoleAssignment.objects.create(party=self.party, role=PartyRole.CUSTOMER)
        PartyRoleAssignment.objects.create(party=self.party, role=PartyRole.VENDOR)
        self.inr = inr

    def pay(self, direction, amount, on):
        payment = Payment.objects.create(
            party=self.party, direction=direction, payment_date=on, amount=D(amount), currency=self.inr,
            bank_account=self.bank, counterpart_account=self.ar if direction == "receipt" else self.ap)
        payment.post()
        return payment

    def september(self):
        statement = BankStatement.objects.create(
            bank_account=self.bank, start_date=datetime.date(2026, 9, 1), end_date=datetime.date(2026, 9, 30),
            opening_balance=D("0"), closing_balance=D("1000"))
        line = BankStatementLine.objects.create(statement=statement, date=datetime.date(2026, 9, 2),
                                                amount=D("1000"))
        line.match(self.receipt)
        return statement

    def test_a_cheque_presented_next_month_is_unpresented_at_this_months_end(self):
        self.receipt = self.pay("receipt", "1000", datetime.date(2026, 9, 2))
        cheque = self.pay("disbursement", "400", datetime.date(2026, 9, 25))
        september = self.september()
        october = BankStatement.objects.create(
            bank_account=self.bank, start_date=datetime.date(2026, 10, 1), end_date=datetime.date(2026, 10, 31),
            opening_balance=D("1000"), closing_balance=D("600"))
        BankStatementLine.objects.create(statement=october, date=datetime.date(2026, 10, 2),
                                         amount=D("-400")).match(cheque)
        report = september.reconciliation()
        self.assertEqual((report["unpresented_total"], report["difference"]), (D("-400"), D("0")),
                         "September after October's line matched the cheque: observed unpresented "
                         f"{report['unpresented_total']}, difference {report['difference']}; expected -400 and 0")

    def test_a_cheque_cancelled_next_month_is_unpresented_at_this_months_end(self):
        self.receipt = self.pay("receipt", "1000", datetime.date(2026, 9, 2))
        cheque = self.pay("disbursement", "400", datetime.date(2026, 9, 25))
        cheque.void(memo="Cancelled, never presented", on_date=datetime.date(2026, 10, 3))
        september = self.september()
        report = september.reconciliation()
        self.assertEqual((report["unpresented_total"], report["difference"]), (D("-400"), D("0")),
                         "September after the cheque was cancelled on 3 Oct: observed unpresented "
                         f"{report['unpresented_total']}, difference {report['difference']}; expected -400 and 0")

    def test_matched_unmatched_and_matched_again(self):
        self.receipt = self.pay("receipt", "1000", datetime.date(2026, 9, 2))
        cheque = self.pay("disbursement", "400", datetime.date(2026, 9, 25))
        september = BankStatement.objects.create(
            bank_account=self.bank, start_date=datetime.date(2026, 9, 1), end_date=datetime.date(2026, 9, 30),
            opening_balance=D("0"), closing_balance=D("600"))
        first = BankStatementLine.objects.create(statement=september, date=datetime.date(2026, 9, 2), amount=D("1000"))
        second = BankStatementLine.objects.create(statement=september, date=datetime.date(2026, 9, 26), amount=D("-400"))
        first.match(self.receipt)
        second.match(cheque)
        second.refresh_from_db()
        second.unmatch()
        self.assertFalse(september.is_reconciled())
        second.refresh_from_db()
        second.match(cheque)
        report = september.reconciliation()
        self.assertEqual((report["difference"], report["unpresented_total"], len(report["unresolved_lines"])),
                         (D("0"), D("0"), 0))
        september.close()


class InrBooks(TestCase):
    """Rupee books with a customer, a vendor and the accounts a trading cycle needs."""

    def setUp(self):
        from apps.core.models import Company, Currency, UnitOfMeasure
        from apps.inventory.models import Item

        self.inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        acc = lambda c, t, **more: Account.objects.create(code=c, name=c, account_type=t, **more)
        self.bank = acc("1010", AccountType.ASSET, holds_money=True)
        self.ar = acc("1100", AccountType.ASSET)
        self.ap = acc("2000", AccountType.LIABILITY)
        self.revenue = acc("4000", AccountType.INCOME)
        self.expense = acc("5000", AccountType.EXPENSE)
        self.bad_debt = acc("5100", AccountType.EXPENSE)
        Company.objects.create(name="Co", base_currency=self.inr, bad_debt_account=self.bad_debt)
        self.uom = UnitOfMeasure.objects.create(code="ea", name="Each")
        self.item = Item.objects.create(sku="W", name="Widget", uom=self.uom)
        self.customer = Party.objects.create(code="C", name="C", default_currency=self.inr)
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)
        self.vendor = Party.objects.create(code="V", name="V", default_currency=self.inr)
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)

    def pay(self, party, direction, amount, on, counterpart):
        payment = Payment.objects.create(party=party, direction=direction, payment_date=on, amount=D(amount),
                                         currency=self.inr, bank_account=self.bank, counterpart_account=counterpart)
        payment.post()
        return payment

    def bill(self, quantity, price, on=datetime.date(2026, 9, 10), centre=None):
        from apps.purchasing.models import Bill, BillLine

        bill = Bill.objects.create(vendor=self.vendor, bill_date=on, payable_account=self.ap, currency=self.inr)
        BillLine.objects.create(bill=bill, description="Spares", quantity=D(quantity), unit_price=D(price),
                                expense_account=self.expense, cost_centre=centre)
        bill.post()
        return bill

    def invoice(self, price, on=datetime.date(2026, 9, 10)):
        from apps.sales.models import Invoice, InvoiceLine

        invoice = Invoice.objects.create(customer=self.customer, invoice_date=on, receivable_account=self.ar,
                                         currency=self.inr)
        InvoiceLine.objects.create(invoice=invoice, item=self.item, quantity=D("1"), unit_price=D(price),
                                   revenue_account=self.revenue)
        invoice.post()
        return invoice


class DebitNoteRefundProbe(InrBooks):
    """
    Bill 1,000 paid in full; a debit note of 300; the vendor refunds 300 by
    cheque, applied to the note; the cheque bounces. The vendor owes 300
    again, as the payable says (calc/batch2.py). Sales' credit note counts
    only standing payments (amount_paid); purchasing's debit note counts
    every allocation.
    """

    def test_a_vendor_refund_returned_unpaid_is_owed_again(self):
        from apps.purchasing.models import BillPayment

        bill = self.bill("10", "100")
        paying = self.pay(self.vendor, "disbursement", "1000", datetime.date(2026, 9, 12), self.ap)
        BillPayment.objects.create(bill=bill, payment=paying, amount=D("1000"))
        note = bill.create_debit_note(quantities={bill.lines.get(): D("3")})
        self.assertEqual(note.refund_due(), D("300"))
        refund = self.pay(self.vendor, "receipt", "300", datetime.date(2026, 9, 20), self.ap)
        BillPayment.objects.create(bill=note, payment=refund, amount=D("300"))
        refund.void(memo="Bounced", on_date=datetime.date(2026, 9, 25))
        note.refresh_from_db()
        ledger = balance(self.ap, self.vendor)
        self.assertEqual((note.refund_due(), ledger), (D("300"), D("300")),
                         f"After the refund bounced: note's refund_due {note.refund_due()}, payable {ledger}; "
                         "expected 300 and 300")


class CostCentreProbe(InrBooks):
    """Bill of 10 x 100 to the loom shed; 3 debited back. The shed's cost is 700, and nothing unallocated."""

    def test_a_debit_note_takes_the_cost_off_the_centre_that_bore_it(self):
        from .analytic import CostCentre, costs_by_centre

        loom = CostCentre.objects.create(code="LOOM", name="Loom shed")
        bill = self.bill("10", "100", centre=loom)
        bill.create_debit_note(quantities={bill.lines.get(): D("3")})
        rows = {row["code"] or "unallocated": row["amount"] for row in costs_by_centre()["rows"]}
        self.assertEqual(rows, {"LOOM": D("700.00")},
                         f"Centres after the debit note: observed {rows}; expected LOOM 700 and nothing unallocated")


class DatedAnywhereProbe(InrBooks):
    """An invoice of 10 September: nothing that settles or corrects it is dated before it."""

    def test_a_claim_is_not_credited_before_its_invoice(self):
        invoice = self.invoice("1000")
        try:
            note = invoice.credit_claim(D("100"), "torn", on_date=datetime.date(2026, 8, 20))
        except ValidationError:
            return
        self.fail(f"A claim on {invoice.number} (10 Sep) was credited on {note.invoice_date}: "
                  f"revenue reduced in August by {balance(self.revenue) + D('1000')}")

    def test_a_write_off_is_not_dated_before_its_invoice(self):
        invoice = self.invoice("1000")
        try:
            invoice.write_off(on_date=datetime.date(2026, 8, 20), reason="probe")
        except ValidationError:
            return
        august = JournalLine.objects.filter(account=self.ar, entry__date__lte=datetime.date(2026, 8, 31)).aggregate(
            debit=Sum("debit"), credit=Sum("credit"))
        self.fail(f"An invoice of 10 Sep was written off on 20 Aug: receivables at 31 Aug "
                  f"{(august['debit'] or 0) - (august['credit'] or 0)}, expected the write-off refused")


from apps.gst.tests import GstReturnTestCase, gstin  # noqa: E402
from apps.purchasing.tests_tds import TdsTestCase  # noqa: E402


class TdsReturnProbe(TdsTestCase):
    """
    One bill of 35,000 to a contractor on 10 June: 700 deducted under 194C
    (calc/batch3.py), owed in the April-June quarter. Reversed on 5 July.
    The April-June return lists what June's books say was deducted: 700.
    """

    def test_a_quarters_return_still_lists_what_its_books_hold_after_a_later_reversal(self):
        from apps.purchasing.tds import tds_return

        deduction = self.bill("35000", vendor=self.contractor(pan="AAACL1234F")).deduct_tds()
        self.assertEqual(deduction.amount, D("700"))
        deduction.reverse(on_date=datetime.date(2026, 7, 5))
        listed = sum((row["amount"] for row in tds_return(datetime.date(2026, 4, 1), datetime.date(2026, 6, 30))),
                     D("0"))
        booked = -JournalLine.objects.filter(
            account=self.tds_payable, entry__posted=True, entry__date__range=(datetime.date(2026, 4, 1),
                                                                               datetime.date(2026, 6, 30))
        ).aggregate(net=Sum("debit") - Sum("credit"))["net"]
        self.assertEqual((listed, booked), (D("700"), D("700")),
                         f"April-June: the return lists {listed}, the TDS payable account holds {booked}; "
                         "expected 700 and 700")


class GstFootingProbe(GstReturnTestCase):
    """
    September: 1,000 inside the state (CGST 90 + SGST 90) and 2,000 to
    Karnataka (IGST 360). October: a claim of 100 on the first (CGST 9 +
    SGST 9). Each month's 3B output tax is that month's movement on the
    output tax accounts (calc/batch3.py).
    """

    def test_each_months_3b_foots_to_the_output_tax_accounts(self):
        from apps.gst.returns import gstr3b, month

        local = self.party("MH-C", gstin=gstin("27AABCL1111L1Z"))
        away = self.party("KA-C", gstin=gstin("29AABCK3333K1Z"))
        first = self.sell(local, "1000")
        self.sell(away, "2000")
        first.credit_claim(D("100"), "torn", on_date=datetime.date(2026, 10, 5))
        for period, expected in (("2026-09", {"igst": D("360"), "cgst": D("90"), "sgst": D("90")}),
                                 ("2026-10", {"igst": D("0"), "cgst": D("-9"), "sgst": D("-9")})):
            start, end = month(period)
            result = gstr3b(start, end)
            reported = {head: result["3.1a"][head] + result["3.1b"][head] for head in ("igst", "cgst", "sgst")}
            booked = {}
            for head, code in (("igst", "2203"), ("cgst", "2201"), ("sgst", "2202")):
                rows = JournalLine.objects.filter(account__code=code, entry__posted=True,
                                                  entry__date__range=(start, end)).aggregate(d=Sum("debit"), c=Sum("credit"))
                booked[head] = (rows["c"] or D("0")) - (rows["d"] or D("0"))
            self.assertEqual((reported, booked), (expected, expected), period)


class Gstr2bNoteProbe(GstReturnTestCase):
    """
    KA's bill INV/27/0012 (10,000 + IGST 1,800). A tenth of it goes back:
    the company's debit note is 1,000 + 180 (calc/batch3.py). KA files its
    credit note CN-9 for exactly that on 20 September. The two are the same
    note; the reconciliation pairs them.
    """

    def test_a_debit_note_meets_the_suppliers_credit_note(self):
        from apps.gst.gstr2b import keep, reconcile
        from apps.gst.tests_gstr2b import KA, inv, portal_json  # noqa: F401

        from apps.core.models import PartyRole as Role

        ka = self.party("KA", role=Role.VENDOR, gstin=KA, gst_state="29")
        bill = self.buy(ka, "10000", taxes=[self.igst], reference="INV/27/0012")
        note = bill.create_debit_note(quantities={bill.lines.get(): D("0.1")})
        self.assertEqual(note.total(), D("1180.00"))
        period = f"{note.bill_date:%Y-%m}"
        keep("", portal_json(
            period=f"{note.bill_date:%m%Y}",
            b2b=[{"ctin": KA, "trdnm": "KA", "inv": [inv("INV-27-12", "10-09-2026", "10000", igst="1800")]}],
            cdnr=[{"ctin": KA, "trdnm": "KA", "nt": [{
                "ntnum": "CN-9", "nttyp": "C", "dt": "20-09-2026", "val": "1180", "rev": "N", "itcavl": "Y",
                "rsn": "", "items": [{"num": 1, "rt": 18, "txval": 1000, "igst": 180, "cgst": 0, "sgst": 0,
                                      "cess": 0}]}]}]))
        report = reconcile(period)
        self.assertEqual(
            ([row["line"]["number"] for row in report["matched"]], [row["bill"]["number"] for row in report["not_in_2b"]],
             [row["line"]["number"] for row in report["not_booked"]]),
            (["INV-27-12", "CN-9"], [], []),
            f"Debit note {note.number} (reference {note.reference!r}, dated {note.bill_date}) against KA's CN-9")


import unittest  # noqa: E402

from django.db import connection  # noqa: E402
from django.test import TransactionTestCase, tag  # noqa: E402

from apps.e2e.tests_races import fixture, race  # noqa: E402


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class AccountingRaceProbe(TransactionTestCase):
    def test_a_statement_does_not_close_over_a_line_unmatched_at_that_moment(self):
        """Close reads every line explained; an unmatch lands meanwhile. Closed, nothing may be unexplained."""
        books = fixture(self, StatementProbe)
        books.receipt = books.pay("receipt", "1000", datetime.date(2026, 9, 2))
        statement = books.september()
        line = statement.lines.get()
        outcomes = race((BankStatement, BankStatementLine),
                        lambda: BankStatement.objects.get(pk=statement.pk).close(),
                        lambda: BankStatementLine.objects.get(pk=line.pk).unmatch())
        statement.refresh_from_db()
        line.refresh_from_db()
        self.assertFalse(statement.closed and not line.is_resolved(),
                         f"{outcomes}: the statement closed with its only line unexplained")

    def test_a_deduction_is_not_reversed_while_a_challan_pays_it_over(self):
        """700 deducted on 10 June. Reversed and paid over at once: TDS payable must end at 0, not 700 paid for nothing."""
        from apps.purchasing.tds import TdsChallan, TdsDeduction

        books = fixture(self, TdsTestCase)
        deduction = books.bill("35000", vendor=books.contractor(pan="AAACL1234F")).deduct_tds()
        outcomes = race(JournalEntryModel(),
                        lambda: TdsDeduction.objects.get(pk=deduction.pk).reverse(),
                        lambda: TdsChallan.pay(books.contract, datetime.date(2026, 6, 1), datetime.date(2026, 7, 7),
                                               books.bank, "00001", "0510002"))
        deduction.refresh_from_db()
        payable = balance(books.tds_payable)
        self.assertEqual(payable, D("0"),
                         f"{outcomes}: reversed {bool(deduction.reversed_entry_id)}, challan {deduction.challan_id}; "
                         f"TDS payable {payable}, expected 0")


def JournalEntryModel():
    from .models import JournalEntry

    return JournalEntry


class OverpaymentProbe(InrBooks):
    """Invoice 1,000; the customer sends 1,200; 200 is sent back. Owed: 0 by every reader."""

    def test_an_overpayment_refunded_leaves_nothing_owed(self):
        from apps.sales.models import InvoicePayment, customer_statement, outstanding_balance

        invoice = self.invoice("1000")
        receipt = self.pay(self.customer, "receipt", "1200", datetime.date(2026, 9, 12), self.ar)
        InvoicePayment.objects.create(invoice=invoice, payment=receipt, amount=D("1000"))
        self.pay(self.customer, "disbursement", "200", datetime.date(2026, 9, 15), self.ar)
        statement = customer_statement(self.customer, as_of=datetime.date(2026, 9, 30))
        closing = statement["entries"][-1].balance if statement["entries"] else None
        self.assertEqual((outstanding_balance(self.customer), balance(self.ar, self.customer), closing),
                         (D("0"), D("0"), D("0")))


class PlaceOfSupplyProbe(GstReturnTestCase):
    """
    The plant is in Maharashtra (27). An unregistered buyer whose address on
    record is in Maharashtra has 1,000 of sacks delivered to its own site in
    Karnataka (29). The movement ends in Karnataka: IGST 180, place 29
    (IGST Act s.10(1)(a)), not CGST 90 + SGST 90 at 27.
    """

    def test_goods_delivered_across_the_state_line_bear_integrated_tax(self):
        from apps.core.models import Address
        from apps.sales.models import Invoice, InvoiceLine

        buyer = self.party("B2C", gst_state="27", gst_registration="unregistered")
        site = Address.objects.create(party=buyer, address_type="shipping", line1="Plot 4", city="Hubli",
                                      state="29", postal_code="580001")
        invoice = Invoice.objects.create(customer=buyer, invoice_date=datetime.date(2026, 9, 10),
                                         receivable_account=self.ar, currency=self.inr, shipping_address=site)
        line = InvoiceLine.objects.create(invoice=invoice, item=self.sack, quantity=D("1"), unit_price=D("1000"),
                                          revenue_account=self.revenue)
        line.taxes.set(self.pair)
        invoice.post()
        taxes = sorted((row.tax.code, row.amount) for row in line.recorded_taxes.all())
        self.assertEqual((taxes, invoice.place_of_supply), ([("IGST18", D("180.00"))], "29"),
                         f"Delivered to Karnataka: observed {taxes} at place {invoice.place_of_supply}")


from apps.gst.tests_itc04 import Itc04TestCase  # noqa: E402


class ClosedMonthChallanProbe(Itc04TestCase):
    """
    May is closed (its books and its returns signed off). A job-work
    challan dated 15 May goes out today: no entry is posted, so nothing
    asks the period, and the half-year's ITC-04 grows a challan.
    """

    def test_a_challan_is_not_issued_into_a_closed_month(self):
        from apps.gst.itc04 import itc04

        may = AccountingPeriod.objects.create(name="May", start_date=datetime.date(2026, 5, 1),
                                              end_date=datetime.date(2026, 5, 31))
        before = len(itc04(datetime.date(2026, 4, 1), datetime.date(2026, 9, 30))["sent"])
        may.close()
        try:
            challan = self.challan("50", day=datetime.date(2026, 5, 15))
        except ValidationError:
            return
        after = len(itc04(datetime.date(2026, 4, 1), datetime.date(2026, 9, 30))["sent"])
        self.fail(f"{challan.number} was issued on 15 May with May closed: ITC-04 lines {before} -> {after}")


class RepointProbe(InrBooks):
    """
    1,000 received and applied to X (1,000). Re-pointed to Y (500, unpaid):
    Y has 500 outstanding, so 1,000 cannot be applied to it. The ceiling adds
    back the allocation's old amount, which was X's, not Y's.
    """

    def test_an_allocation_re_pointed_to_a_smaller_invoice_is_refused(self):
        from apps.sales.models import InvoicePayment

        x, y = self.invoice("1000"), self.invoice("500")
        receipt = self.pay(self.customer, "receipt", "1000", datetime.date(2026, 9, 12), self.ar)
        allocation = InvoicePayment.objects.create(invoice=x, payment=receipt, amount=D("1000"))
        allocation.invoice = y
        try:
            allocation.save()
        except ValidationError:
            return
        y.refresh_from_db()
        self.fail(f"Re-pointed: Y (500) now reads {y.amount_due()} due, X reads {x.amount_due()}")

    def test_re_pointed_over_the_api(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        from apps.sales.models import Invoice, InvoicePayment

        x, y = self.invoice("1000"), self.invoice("500")
        receipt = self.pay(self.customer, "receipt", "1000", datetime.date(2026, 9, 12), self.ar)
        allocation = InvoicePayment.objects.create(invoice=x, payment=receipt, amount=D("1000"))
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("boss", "b@x.in", "pw"))
        response = client.patch(f"/api/sales/invoice-payments/{allocation.pk}/", {"invoice": y.pk}, format="json")
        self.assertEqual(response.status_code, 400,
                         f"{response.status_code}: Y now reads {Invoice.objects.get(pk=y.pk).amount_due()} due")

    def test_a_bill_allocation_re_pointed_to_a_smaller_bill_is_refused(self):
        from apps.purchasing.models import BillPayment

        x, y = self.bill("1", "1000"), self.bill("1", "500")
        paying = self.pay(self.vendor, "disbursement", "1000", datetime.date(2026, 9, 12), self.ap)
        allocation = BillPayment.objects.create(bill=x, payment=paying, amount=D("1000"))
        allocation.bill = y
        try:
            allocation.save()
        except ValidationError:
            return
        self.fail(f"Re-pointed: bill Y (500) now reads {y.amount_due()} due, X reads {x.amount_due()}")


class DocumentsIssuedProbe(GstReturnTestCase):
    """
    September: an invoice, a down payment on an order, another invoice. The
    three take INV numbers one after another. GSTR-1 table 13 says the
    invoice series ran from the first to the last: every number in that
    range was issued, so the total is 3 (or the down payment has its own
    series and the range holds 2).
    """

    def test_the_invoice_series_reported_holds_every_number_in_its_range(self):
        from apps.core.models import Company
        from apps.gst.returns import gstr1
        from apps.sales.models import SalesOrder, SalesOrderLine

        Company.objects.update(customer_deposit_account=Account.objects.create(
            code="2300", name="Deposits", account_type=AccountType.LIABILITY))
        buyer = self.party("MH-C", gstin=gstin("27AABCL1111L1Z"))
        first = self.sell(buyer, "1000")
        order = SalesOrder.objects.create(customer=buyer, order_date=datetime.date(2026, 9, 10), currency=self.inr)
        SalesOrderLine.objects.create(order=order, item=self.sack, uom=self.sack.uom, quantity=D("1"),
                                      unit_price=D("1000"), revenue_account=self.revenue)
        order.confirm()
        deposit = order.create_down_payment_invoice(self.ar, amount=D("300"), invoice_date=datetime.date(2026, 9, 10))
        deposit.post()
        last = self.sell(buyer, "2000")
        issued = gstr1(datetime.date(2026, 9, 1), datetime.date(2026, 9, 30))["documents"]
        row = next(row for row in issued if row["kind"] == "invoices")
        self.assertEqual((row["from"], row["to"], row["total"]), (first.number, last.number, 3),
                         f"Numbers issued: {first.number}, {deposit.number}, {last.number}; table 13 says {row}")


class ChallanOnTheStatementProbe(TdsTestCase):
    """
    700 deducted on 10 June and paid over by challan on 7 July from the
    bank. July's statement: opening 0, one line of -700, closing -700. The
    books say -700 too. The line is the challan; it cannot be matched (a
    challan is no Payment), so the only way to explain it is to post it
    again, and the books then say -1,400.
    """

    def test_a_challan_on_the_bank_statement_can_be_explained_and_closed(self):
        from apps.purchasing.tds import TdsChallan

        self.bill("35000", vendor=self.contractor(pan="AAACL1234F")).deduct_tds()
        TdsChallan.pay(self.contract, datetime.date(2026, 6, 1), datetime.date(2026, 7, 7), self.bank, "00001",
                       "0510002")
        july = BankStatement.objects.create(bank_account=self.bank, start_date=datetime.date(2026, 7, 1),
                                            end_date=datetime.date(2026, 7, 31), opening_balance=D("0"),
                                            closing_balance=D("-700"))
        line = BankStatementLine.objects.create(statement=july, date=datetime.date(2026, 7, 7), amount=D("-700"))
        self.assertEqual(july.reconciliation()["difference"], D("0"))
        line.post_to(self.tds_payable)  # the only way the line can be explained
        report = july.reconciliation()
        self.assertEqual((report["difference"], balance(self.tds_payable)), (D("0"), D("0")),
                         f"Explained the only way offered: difference {report['difference']}, TDS payable "
                         f"{balance(self.tds_payable)}, bank {report['ledger_balance']}; expected 0, 0, -700")
