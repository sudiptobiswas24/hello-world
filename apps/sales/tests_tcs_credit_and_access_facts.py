"""
g22 review of fix_money, sales side: O243 (TCS on an advance), O245, O77, and who may. Probes assert what SHOULD
be true on d554c46; a FAIL line is a finding.
"""
import datetime
import random
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.accounting.models import JournalLine
from apps.gst import tests_einvoice as ein
from apps.sales import tests_tcs as tcs
from apps.sales.models import Invoice, InvoiceLine, SalesOrder, SalesOrderLine, outstanding_balance
from apps.sales.tcs import TcsChallan, TcsCollection, TcsRate

D = Decimal
SEP10, SEP20, OCT5, OCT8 = (datetime.date(2026, 9, 10), datetime.date(2026, 9, 20),
                            datetime.date(2026, 10, 5), datetime.date(2026, 10, 8))


def say(ok, claim, detail=""):
    print(("PASS " if ok else "FAIL ") + claim + (f"  [{detail}]" if detail else ""))
    return ok


class TcsOnAScrapAdvance(tcs.TcsTestCase):
    def setUp(self):
        super().setUp()
        from apps.accounting.models import Account, AccountType
        from apps.core.models import Company
        deposits = Account.objects.create(code="2400", name="Customer advances", account_type=AccountType.LIABILITY)
        company = Company.get()
        company.customer_deposit_account = deposits
        company.save()

    def order_with_advance(self, percent=30, day=SEP10, kg="2000"):
        order = SalesOrder.objects.create(customer=self.mh, order_date=day, currency=self.inr)
        SalesOrderLine.objects.create(order=order, item=self.scrap, uom=self.scrap.uom, quantity=D(kg),
                                      unit_price=D("45"), revenue_account=self.revenue)
        order.confirm()
        advance = order.create_down_payment_invoice(self.ar, percent=percent, invoice_date=day)
        advance.post()
        return order, advance

    def goods(self, order, kg, day):
        invoice = Invoice.objects.create(customer=self.mh, invoice_date=day, receivable_account=self.ar,
                                         currency=self.inr, sales_order=order)
        line = InvoiceLine.objects.create(invoice=invoice, item=self.scrap, quantity=D(kg), unit_price=D("45"),
                                          revenue_account=self.revenue, order_line=order.lines.get())
        line.taxes.set(self.pair)
        invoice.post()
        return invoice

    def payable(self):
        return self.ledger("2290")

    def test_the_rate_that_stood_on_the_receipts_day_is_kept_when_the_rate_moves(self):
        rate = TcsRate.objects.create(section=self.section, applies_from=datetime.date(2026, 10, 1),
                                      rate_percent=D("2"), no_pan_rate_percent=D("5"))
        rate.confirm(self.accountant)
        order, advance = self.order_with_advance()
        invoice = self.goods(order, "2000", OCT5)
        (adv_row,) = advance.tcs_collected.all()
        (inv_row,) = invoice.tcs_collected.all()
        ok = (adv_row.amount == D("270") and adv_row.rate_percent == D("1") and inv_row.base == D("79200.00")
              and inv_row.rate_percent == D("2") and inv_row.amount == D("1584") and self.payable() == D("1854"))
        say(ok, "O243 advance at 1% on its day (270), the rest at the 2% of the invoice's day (1,584): 1,854 payable",
            f"advance {adv_row.amount}@{adv_row.rate_percent} invoice {inv_row.amount}@{inv_row.rate_percent} base {inv_row.base} ledger {self.payable()}")
        # the advance was drawn down by the invoice, so crediting the goods gives back both on the one note
        note = self.credit(invoice, "2000", OCT8)
        say(note.tcs_amount == D("1854") and self.payable() == 0,
            "O243 goods credited (advance drawn down): one note gives back 1,584 at 2% and 270 at 1%, account 0",
            f"note {note.tcs_amount} ledger {self.payable()}")
        self.assertTrue(ok)
        self.assertEqual((note.tcs_amount, self.payable()), (D("1854"), D("0")))

    def test_a_part_refund_of_the_advance_then_the_goods(self):
        order, advance = self.order_with_advance()
        note = advance.create_credit_note(amount=D("9000"))
        after_refund = self.payable()
        invoice = self.goods(order, "2000", SEP20)
        total_tcs = self.payable()
        ok = (note.tcs_amount == D("90") and after_refund == D("180") and total_tcs == D("1062"))
        say(ok, "O243 27,000 taken (270), 9,000 refunded (90 back), then 2,000 kg invoiced: 1,062 payable in all",
            f"note tcs {note.tcs_amount} after refund {after_refund} total {total_tcs} invoice tcs {invoice.tcs_amount}")
        self.assertTrue(ok)

    def test_the_challan_clears_the_month_and_a_later_refund_is_not_lost(self):
        order, advance = self.order_with_advance()
        invoice = self.goods(order, "2000", SEP20)
        challan = TcsChallan.pay(self.section, datetime.date(2026, 9, 1), OCT5, self.bank, "00099", "0510308")
        cleared = self.payable()
        say(cleared == 0 and challan.amount == D("1062.00"), "O243 the September challan pays 1,062 and clears the TCS payable",
            f"challan {challan.amount} ledger {cleared}")
        note = self.credit(invoice, "2000", OCT8)
        after = self.payable()
        detail = f"note gave back {note.tcs_amount}; account reads {after} after the September challan"
        try:
            second = TcsChallan.pay(self.section, datetime.date(2026, 10, 1), datetime.date(2026, 11, 5), self.bank,
                                    "00100", "0510308")
            detail += f"; October challan {second.amount} -> ledger {self.payable()}"
        except ValidationError as exc:
            detail += f"; October challan refused: {exc.messages}"
        say("later month" in detail and self.payable() == D("-1062"),
            "O243 (by design) a give-back after its month's challan is carried: account reads -1,062 and October's challan is refused in words",
            detail)
        self.assertEqual(cleared, 0)

    def test_random_sequences_keep_the_ledger_and_the_documents_in_step(self):
        rnd = random.Random(11)
        bad = []
        for n in range(30):
            percent = rnd.choice([10, 25, 30, 40, 55])
            order, advance = self.order_with_advance(percent=percent)
            invoices = []
            ops = rnd.sample(["inv", "inv", "credit", "credit", "refund"], k=rnd.randint(2, 5))
            log = []
            try:
                for op in ops:
                    if op == "inv":
                        remaining = D("2000") - sum((i.lines.get().quantity for i in invoices), D("0"))
                        kg = rnd.choice([D("100"), D("500"), D("777"), D("1000")])
                        if kg > remaining:
                            continue
                        invoices.append(self.goods(order, str(kg), SEP20))
                        log.append(("inv", str(kg)))
                    elif op == "credit" and invoices:
                        inv = rnd.choice(invoices)
                        left = inv.lines.get().quantity_creditable()
                        if left <= 0:
                            continue
                        kg = min(left, rnd.choice([D("50"), D("333"), left]))
                        self.credit(inv, str(kg), OCT8)
                        log.append(("credit", str(kg)))
                    elif op == "refund":
                        left = advance.deposit_unapplied()
                        if left <= 0:
                            continue
                        advance.create_credit_note(amount=min(left, D(rnd.choice(["500", "1234.56", "9000"]))))
                        log.append(("refund", "x"))
                    if self.payable() < 0 or outstanding_balance(self.mh) != -self.ledger("1100"):
                        bad.append((n, percent, log[:], str(self.payable()), str(outstanding_balance(self.mh)),
                                    str(-self.ledger("1100"))))
                        break
                # wind everything back: credit all goods, refund the rest of the advance
                for inv in invoices:
                    left = inv.lines.get().quantity_creditable()
                    if left > 0:
                        self.credit(inv, str(left), OCT8)
                left = advance.deposit_unapplied()
                if left > 0:
                    advance.create_credit_note()
                if self.payable() != 0:
                    bad.append((n, percent, log, "wound back, payable", str(self.payable())))
            except ValidationError as exc:
                bad.append((n, percent, log, "refused", exc.messages[:1]))
            # clean slate for the next sequence is not needed: the ledger should be 0 again
        say(not bad, "O243 30 random sequences of invoice/credit/refund wind back to a zero TCS payable and the documents match AR",
            f"{len(bad)} bad: {bad[:3]}")
        self.assertEqual(bad, [])


class TheReturnAndTheChallan(tcs.TcsTestCase):
    def test_an_edited_section_name_does_not_change_a_filed_collection_or_its_challan_or_the_ledger_memo(self):
        from apps.sales.tcs import tcs_return

        invoice = self.t1()
        challan = TcsChallan.pay(self.section, datetime.date(2026, 9, 1), datetime.date(2026, 10, 5), self.bank,
                                 "00042", "0510308")
        before = tcs_return(datetime.date(2026, 7, 1), datetime.date(2026, 9, 30))
        self.section.name, self.section.code = "Scrap (renamed)", "206C(1H)"
        self.section.save()
        after = tcs_return(datetime.date(2026, 7, 1), datetime.date(2026, 9, 30))
        row_b, row_a = before[0], after[0]
        keys = ["section", "collection_code", "base", "rate_percent", "amount"]
        same = {k: row_b[k] == row_a[k] for k in keys}
        say(all(same.values()), "O245 return rows keep the section the law named on the day after the section is edited",
            str({k: (row_b[k], row_a[k]) for k, v in same.items() if not v}))
        self.assertTrue(all(same.values()))


class CreditLimitAcrossCurrencies(ein.EInvoiceTestCase):
    def test_the_exposure_endpoint_and_the_credit_check_agree_after_a_rate_change(self):
        from apps.core.models import ExchangeRate
        from apps.sales.models import CustomerProfile, outstanding_in_base

        call_command("setup_roles", verbosity=0)
        profile = CustomerProfile.objects.create(party=self.mh, credit_limit=D("100000"))
        # a dollar invoice at the 83.456789 of 1 Sept, then the dollar moves
        invoice = self.invoice(self.mh, [(self.sack, "1000", "1.37", "0", self.pair)], currency=self.usd)
        before = outstanding_in_base(self.mh)
        ExchangeRate.objects.create(currency=self.usd, rate=D("90"), valid_from=datetime.date(2026, 9, 15))
        after = outstanding_in_base(self.mh)
        say(after == before, "O77 exposure in the base of an invoice already booked does not move with the rate",
            f"{before} -> {after} (booked rate {invoice.exchange_rate})")
        user = User.objects.create_user("ctrl")
        user.groups.add(Group.objects.get(name="AR Manager"))
        client = APIClient()
        client.force_authenticate(user)
        data = client.get(f"/api/sales/customer-profiles/{profile.pk}/exposure/").json()
        say(D(str(data.get("outstanding", "-1"))) == after, "O77 the exposure endpoint reads what the check reads",
            f"{data}")


class WhoMay(ein.EInvoiceTestCase):
    def test_the_new_endpoints_by_role(self):
        from apps.accounting.urls import router as accounting_router
        from apps.accounting.lut_api import LetterOfUndertakingViewSet

        call_command("setup_roles", verbosity=0)
        lut_prefix = [p for p, v, b in accounting_router.registry if v is LetterOfUndertakingViewSet][0]
        probes = {
            "debit_note": ("post", "/api/sales/invoices/999999/debit_note/"),
            "credit_note(mirror)": ("post", "/api/sales/invoices/999999/credit_note/"),
            "take_discount(sales)": ("post", "/api/sales/invoices/999999/take_settlement_discount/"),
            "take_discount(purch)": ("post", "/api/purchasing/bills/999999/take_settlement_discount/"),
            "bill match_report": ("get", "/api/purchasing/bills/999999/match_report/"),
            "req suggested": ("get", "/api/purchasing/requisitions/999999/suggested_vendors/"),
            "stmt unmatched_entries": ("get", "/api/accounting/bank-statements/999999/unmatched_entries/"),
            "stmt reconciliation": ("get", "/api/accounting/bank-statements/999999/reconciliation/"),
            "LUT list": ("get", f"/api/accounting/{lut_prefix}/"),
            "LUT create": ("post", f"/api/accounting/{lut_prefix}/"),
            "seeded list": ("get", "/api/accounting/seeded-defaults/"),
            "seeded review": ("post", "/api/accounting/seeded-defaults/999999/review/"),
            "tcs collections": ("get", "/api/sales/tcs-collections/"),
        }
        table = {}
        for group in Group.objects.order_by("name"):
            user = User.objects.create_user("u-" + group.name.replace(" ", "-").lower())
            user.groups.add(group)
            client = APIClient()
            client.force_authenticate(user)
            row = {}
            for label, (method, url) in probes.items():
                response = getattr(client, method)(url, {}, format="json")
                row[label] = response.status_code
            table[group.name] = row
        labels = list(probes)
        print("ROLE MATRIX (403 = refused; 404/400/200/201 = let through to the object)")
        print("%-22s" % "" + " ".join("%-6s" % l[:6] for l in labels))
        for name, row in table.items():
            print("%-22s" % name + " ".join("%-6s" % row[l] for l in labels))
        let = lambda label: sorted(n for n, r in table.items() if r[label] != 403)
        print("debit_note allowed:", let("debit_note"))
        print("credit_note allowed:", let("credit_note(mirror)"))
        print("take_discount(sales) allowed:", let("take_discount(sales)"))
        print("take_discount(purch) allowed:", let("take_discount(purch)"))
        print("LUT create allowed:", let("LUT create"))
        print("seeded review allowed:", let("seeded review"))
        mirror_ok = let("debit_note") == let("credit_note(mirror)")
        say(mirror_ok, "O235 who may raise a debit note is who may raise a credit note", f"{let('debit_note')} vs {let('credit_note(mirror)')}")
        self.assertTrue(mirror_ok)
