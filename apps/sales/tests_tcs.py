"""
TCS on the plant's scrap sales (section 206C(1); 394 from April 2026). O219.

Figures from calc_fix_acct/scenarios.py. A Maharashtra buyer with its PAN
inside its GSTIN; PP scrap at 9% CGST + 9% SGST; the rate 1% from 1 April
2026, 5% without a PAN, both confirmed by the accountant in setUp.

  T1  2,000 kg at 45: 90,000.00 + 8,100.00 + 8,100.00 = 106,200.00;
      TCS 1% 1,062; invoice 107,262.00.
  T2  The same to a buyer with no PAN: 5% is 5,310.
  T3  Credit 500 kg: 26,550.00, TCS back 266, the note 26,816.00.
  T4  Credit the other 1,500 kg: 79,650.00 at 1% is 797, but the last of
      it takes what is left, 796.
  T5  September's challan after T1 and T3: 796. T1 paid in September,
      T3 dated in October: October nets to -266 and is refused.
  T6  A Form 127 declaration covering the day: nothing collected,
      106,200.00, and the paper says why.
"""

import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase, tag
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType, TcsDeclaration, TcsRate, TcsSection
from apps.gst.einvoice import build
from apps.gst.tests import DAY
from apps.gst.tests_einvoice import EInvoiceTestCase
from apps.gst.tests_tax_invoice import printed_text
from apps.inventory.models import Item

from .documents import render_invoice_pdf, tax_invoice
from .tcs import TcsChallan, TcsCollection, tcs_return

OCTOBER = datetime.date(2026, 10, 5)


class TcsTestCase(EInvoiceTestCase):
    def setUp(self):
        super().setUp()
        self.tcs_payable = Account.objects.create(code="2290", name="TCS payable", account_type=AccountType.LIABILITY)
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET,
                                           holds_money=True)
        self.section = TcsSection.objects.create(code="206C(1)", name="Scrap", form_code="6CE",
                                                 payable_account=self.tcs_payable)
        self.rate = TcsRate.objects.create(section=self.section, applies_from=datetime.date(2026, 4, 1),
                                           rate_percent=D("1"), no_pan_rate_percent=D("5"))
        self.accountant = User.objects.create_user("accountant")
        self.rate.confirm(self.accountant)
        self.scrap = Item.objects.create(sku="SCRAP", name="PP scrap", uom=self.sack.uom, hsn_code="39159090",
                                         tcs_section=self.section)

    def t1(self, customer=None, post=True):
        return self.invoice(customer or self.mh, [(self.scrap, "2000", "45", "0", self.pair)], post=post)

    def credit(self, invoice, kg, day):
        return invoice.create_credit_note(quantities={invoice.lines.get(): D(kg)}, on_date=day)


class CollectedOnTheInvoiceTests(TcsTestCase):
    def test_collected_on_the_value_with_its_gst_and_owed_with_the_invoice(self):
        invoice = self.t1()
        (row,) = invoice.tcs_collected.all()
        self.assertEqual((row.base, row.rate_percent, row.amount, row.rate, row.pan),
                         (D("106200.00"), D("1"), D("1062.00"), self.rate, self.mh.tax_profile.pan_on_file()))
        self.assertEqual((invoice.tcs_amount, invoice.total(), invoice.posted_total, invoice.amount_due()),
                         (D("1062.00"), D("107262.00"), D("107262.00"), D("107262.00")))
        self.assertEqual(self.ledger("2290"), D("1062.00"))
        self.assertEqual(-self.ledger("1100"), D("107262.00"))

    def test_a_buyer_with_no_pan_at_the_rate_for_no_pan(self):
        nopan = self.party("NOPAN")
        self.address(nopan, "4 Station Road", "Nashik", "Maharashtra", "422001")
        self.assertEqual(self.t1(nopan).tcs_amount, D("5310.00"))

    def test_goods_it_does_not_apply_to_collect_nothing(self):
        invoice = self.invoice(self.mh, [(self.sack, "100", "150", "0", self.pair)])
        self.assertEqual((invoice.tcs_amount, invoice.tcs_collected.count(), invoice.total()),
                         (D("0"), 0, D("17700.00")))

    def test_a_declaration_exempts_and_is_kept(self):
        declaration = TcsDeclaration.objects.create(
            party=self.mh, section=self.section, pan=self.mh.tax_profile.pan_on_file(), reference="F127/26-27/04",
            received_on=datetime.date(2026, 4, 2),
            valid_from=datetime.date(2026, 4, 1), valid_to=datetime.date(2027, 3, 31))
        invoice = self.t1()
        (row,) = invoice.tcs_collected.all()
        self.assertEqual((row.amount, row.declaration, row.journal_entry, invoice.total()),
                         (D("0"), declaration, None, D("106200.00")))
        self.assertEqual(self.ledger("2290"), D("0"))
        text = " ".join(" ".join(printed_text(render_invoice_pdf, invoice)).split())
        self.assertIn("TCS u/s 206C(1) not collected: the buyer's declaration, Form 127 F127/26-27/04 received "
                      "02 Apr 2026.", text)
        with self.assertRaisesMessage(ValidationError, "exempted under this declaration"):
            declaration.delete()
        (quarter,) = tcs_return(datetime.date(2026, 7, 1), datetime.date(2026, 9, 30))
        self.assertEqual((quarter["not_collected"], quarter["amount"], quarter["base"]), ("B", D("0"), D("106200.00")))


class RefusedTests(TcsTestCase):
    def test_not_at_a_rate_the_accountant_has_not_confirmed(self):
        TcsRate.objects.create(section=self.section, applies_from=datetime.date(2026, 9, 1), rate_percent=D("2"),
                               no_pan_rate_percent=D("5"))
        invoice = self.t1(post=False)
        with self.assertRaisesMessage(ValidationError, "TCS under 206C(1) at 2% from 01 Sep 2026 is not confirmed by the accountant, so this cannot be posted"):
            invoice.post()
        invoice.refresh_from_db()
        self.assertFalse(invoice.posted)
        self.assertEqual(TcsCollection.objects.count(), 0)

    def test_not_before_any_rate_applies(self):
        invoice = self.invoice(self.mh, [(self.scrap, "10", "45", "0", self.pair)], day=datetime.date(2026, 3, 31),
                               post=False)
        with self.assertRaisesMessage(ValidationError, "has no rate from 31 Mar 2026 or before"):
            invoice.post()

    def test_a_rate_collected_at_is_not_changed(self):
        self.t1()
        self.rate.rate_percent = D("2")
        with self.assertRaisesMessage(ValidationError, "Invoices collected at this rate"):
            self.rate.save()

    def test_a_changed_rate_waits_for_the_accountant_again(self):
        self.rate.rate_percent = D("2")
        self.rate.save()
        self.rate.refresh_from_db()
        self.assertIsNone(self.rate.confirmed_on)


class GivenBackByTheCreditNoteTests(TcsTestCase):
    def test_at_the_rate_collected_and_the_last_takes_what_is_left(self):
        invoice = self.t1()
        first = self.credit(invoice, "500", datetime.date(2026, 9, 20))
        self.assertEqual((first.tcs_amount, first.total()), (D("266.00"), D("26816.00")))
        last = self.credit(invoice, "1500", datetime.date(2026, 9, 25))
        self.assertEqual(last.tcs_amount, D("796.00"))
        self.assertEqual(self.ledger("2290"), D("0"))
        self.assertEqual(self.ledger("1100"), D("0"))
        invoice.refresh_from_db()
        self.assertEqual(invoice.amount_due(), D("0"))


class ChallanTests(TcsTestCase):
    def test_the_month_net_of_what_was_given_back_and_its_void(self):
        invoice = self.t1()
        self.credit(invoice, "500", datetime.date(2026, 9, 20))
        challan = TcsChallan.pay(self.section, "2026-09-01", OCTOBER, self.bank, "00123", "0510308")
        self.assertEqual(challan.amount, D("796.00"))
        self.assertEqual(self.ledger("2290"), D("0"))
        rows = tcs_return(datetime.date(2026, 7, 1), datetime.date(2026, 9, 30))
        self.assertEqual([(row["amount"], row["challan"]) for row in rows],
                         [(D("1062.00"), "0510308/00123"), (D("-266.00"), "0510308/00123")])
        with self.assertRaisesMessage(ValidationError, "Nothing collected under 206C(1) to the end of September "
                                                       "2026 is waiting"):
            TcsChallan.pay(self.section, "2026-09-01", OCTOBER, self.bank, "00124", "0510308")
        challan.void()
        again = TcsChallan.pay(self.section, "2026-09-01", OCTOBER, self.bank, "00125", "0510308")
        self.assertEqual(again.amount, D("796.00"))

    def test_a_give_back_after_its_month_was_paid_is_set_against_a_later_one(self):
        invoice = self.t1()
        TcsChallan.pay(self.section, "2026-09-01", OCTOBER, self.bank, "00123", "0510308")
        self.credit(invoice, "500", OCTOBER)
        with self.assertRaisesMessage(ValidationError, "nets to -266.00"):
            TcsChallan.pay(self.section, "2026-10-01", OCTOBER, self.bank, "00124", "0510308")


class OnPaperAndInTheEInvoiceTests(TcsTestCase):
    def test_the_print_and_the_registration_carry_it(self):
        invoice = self.t1()
        printed = tax_invoice(invoice)
        self.assertEqual([(row["section"], row["rate"], row["base"], row["amount"]) for row in printed["tcs"]],
                         [("206C(1)", D("1"), D("106200.00"), D("1062.00"))])
        text = " ".join(" ".join(printed_text(render_invoice_pdf, invoice)).split())
        self.assertIn("TCS collected u/s 206C(1) @ 1% on 106,200.00 1,062.00", text)
        self.assertIn("107,262.00", text)
        values = build(invoice)["ValDtls"]
        self.assertEqual((values["OthChrg"], values["RndOffAmt"], values["TotInvVal"]), (1062.0, 0.0, 107262.0))


class TcsIsNoPartOfTheValueOfSupplyTests(TcsTestCase):
    """
    TCS is income tax collected with the sale, not part of its value for GST (CBIC Circular
    76/50/2018-GST as corrected on 7 March 2019): every taxable value leaves it out, and only the
    invoice's whole value counts it, as an other charge.

      T1 again: taxable 90,000.00, GST 16,200.00, TCS 1,062, invoice 107,262.00.
      T8  1,000 kg at 42.35 = 42,350.00, GST 7,623.00, 49,973.00; TCS 1% of it 499.73 is 500,
          50,473.00: past the e-way bill limit of 50,000 only if TCS were counted.
    """

    def test_the_gst_computed_on_the_supply_alone(self):
        invoice = self.t1()
        self.assertEqual([(row.gst_head, row.taxable, row.amount) for row in invoice.lines.get().recorded_taxes.all()],
                         [("cgst", D("90000.00"), D("8100.00")), ("sgst", D("90000.00"), D("8100.00"))])

    def test_gstr1_and_gstr3b_report_the_supply_and_the_invoice_value_carries_tcs(self):
        from apps.gst.returns import gstr1, gstr3b
        from apps.gst.tests import SEPTEMBER

        self.t1()
        (row,) = gstr1(*SEPTEMBER)["b2b"]
        (item,) = row["items"]
        self.assertEqual((row["value"], item["taxable"], item["cgst"], item["sgst"]),
                         (D("107262.00"), D("90000.00"), D("8100.00"), D("8100.00")))
        summary = gstr3b(*SEPTEMBER)["3.1a"]
        self.assertEqual((summary["taxable"], summary["cgst"]), (D("90000.00"), D("8100.00")))

    def test_the_e_invoice_counts_it_only_in_the_invoice_value(self):
        values = build(self.t1())["ValDtls"]
        self.assertEqual((values["AssVal"], values["CgstVal"], values["OthChrg"], values["TotInvVal"]),
                         (90000.0, 8100.0, 1062.0, 107262.0))

    def test_the_e_way_bill_leaves_it_out_of_the_value_and_the_limit(self):
        from apps.gst.ewaybill import _invoice_payload

        invoice = self.invoice(self.mh, [(self.scrap, "1000", "42.35", "0", self.pair)])
        self.assertEqual((invoice.tcs_amount, invoice.total()), (D("500.00"), D("50473.00")))
        payload, required, because = _invoice_payload(invoice)
        self.assertEqual((payload["totalValue"], payload["otherValue"], payload["totInvValue"]),
                         (42350.0, 500.0, 50473.0))
        self.assertEqual((required, because), (False, "Worth 49973.00, not above the limit of 50000.00."))


class ThroughTheApiTests(TcsTestCase):
    def client_as(self, role, name):
        user = User.objects.create_user(name)
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_controller_confirms_and_receivables_pays_over(self):
        call_command("setup_roles", verbosity=0)
        rate = TcsRate.objects.create(section=self.section, applies_from=datetime.date(2027, 4, 1),
                                      rate_percent=D("1"), no_pan_rate_percent=D("5"))
        url = f"/api/accounting/tcs-rates/{rate.pk}/confirm/"
        self.assertEqual(self.client_as("AR Manager", "ar").post(url).status_code, 403)
        confirmed = self.client_as("Controller", "controller").post(url)
        self.assertEqual((confirmed.status_code, confirmed.json()["confirmed_by"]), (200, User.objects.get(
            username="controller").pk))
        self.t1()
        receivables = User.objects.get(username="ar")
        client = APIClient()
        client.force_authenticate(receivables)
        paid = client.post("/api/sales/tcs-challans/pay/", {
            "section": self.section.pk, "month": "2026-09-01", "date": "2026-10-05", "bank_account": self.bank.pk,
            "challan_number": "00123", "bsr_code": "0510308"}, format="json")
        self.assertEqual((paid.status_code, paid.json()["amount"]), (201, "1062.00"))
        quarter = client.get("/api/sales/tcs-collections/quarter/", {"from": "2026-07-01", "to": "2026-09-30"})
        self.assertEqual((quarter.status_code, quarter.json()[0]["amount"]), (200, "1062.00"))


class TheAuditSeesAPayableAccountReadLiveTests(TcsTestCase):
    """Review F3: a payable account a collection or deduction keeps, read again through its section."""

    def test_a_challan_reading_the_sections_account_is_reported(self):
        from apps.core.management.commands.audit_invariants import Command, app_sources

        sources = app_sources()
        self.assertEqual(Command().kept_settings_read_live(["sales", "purchasing"], sources), [])
        sources["accounting"] = {
            path: text.replace("account_id=account_id, debit=", "account=section.payable_account, debit=")
            if path.name == "challans.py" else text for path, text in sources["accounting"].items()}
        findings = [text for _, text in Command().kept_settings_read_live(["sales", "purchasing"], sources)]
        self.assertIn("sales.TcsCollection keeps its own payable_account, but accounting.challans.pay_over reads "
                      "`section.payable_account` as it stands.", findings)
        self.assertIn("purchasing.TdsDeduction keeps its own payable_account, but accounting.challans.pay_over "
                      "reads `section.payable_account` as it stands.", findings)


class TheSectionTheLawNamesOnTheDayTests(TcsTestCase):
    """Review F13: 206C(1) of the 1961 Act until 31 March 2026, 394(1) of the 2025 Act from 1 April 2026."""

    def test_kept_on_each_collection_as_it_stood(self):
        later = TcsRate.objects.create(section=self.section, applies_from=datetime.date(2026, 9, 15),
                                       section_code="394(1)", rate_percent=D("1"), no_pan_rate_percent=D("5"))
        later.confirm(self.accountant)
        before, after = self.t1(), self.invoice(self.mh, [(self.scrap, "2000", "45", "0", self.pair)],
                                                day=datetime.date(2026, 9, 16))
        self.assertEqual([row.section_code for row in (before.tcs_collected.get(), after.tcs_collected.get())],
                         ["206C(1)", "394(1)"])
        text = " ".join(" ".join(printed_text(render_invoice_pdf, after)).split())
        self.assertIn("TCS collected u/s 394(1) @ 1% on 106,200.00", text)
        self.section.code = "RENAMED"
        self.section.save()
        self.assertEqual([row["section"] for row in tcs_return(datetime.date(2026, 7, 1), datetime.date(2026, 9, 30))],
                         ["206C(1)", "394(1)"])
        call_command("setup_roles", verbosity=0)
        controller = User.objects.create_user("controller")
        controller.groups.add(Group.objects.get(name="Controller"))
        client = APIClient()
        client.force_authenticate(controller)
        listed = client.get("/api/sales/tcs-collections/").json()
        self.assertEqual(sorted(row["section_code"] for row in listed), ["206C(1)", "394(1)"])


class FreightOnAScrapInvoiceTests(TcsTestCase):
    """
    Review U2 (calc_fix_acct2/u2.py). Freight is part of what the buyer pays for the scrap:
      U2a 2,000 kg at 45 and freight 3,000, all at 9% + 9%: base 106,200.00 + 3,540.00 = 109,740.00, TCS 1,097.
      U2b the same with 100 kg of sacks at 150: the freight's share by the goods' value, 3,540.00 x
          90,000 / 105,000 = 3,034.29; base 109,234.29, TCS 1,092.
    """

    def test_on_an_invoice_of_scrap_alone(self):
        invoice = self.invoice(self.mh, [(self.scrap, "2000", "45", "0", self.pair),
                                         (self.freight6, "1", "3000", "0", self.pair)])
        self.assertEqual((invoice.tcs_collected.get().base, invoice.tcs_amount), (D("109740.00"), D("1097.00")))

    def test_beside_other_goods_by_their_value(self):
        invoice = self.invoice(self.mh, [(self.scrap, "2000", "45", "0", self.pair),
                                         (self.sack, "100", "150", "0", self.pair),
                                         (self.freight6, "1", "3000", "0", self.pair)])
        self.assertEqual((invoice.tcs_collected.get().base, invoice.tcs_amount), (D("109234.29"), D("1092.00")))


class AnAdvanceOnAScrapOrderTests(TcsTestCase):
    """
    O243 (review U1): 206C(1) (394 from April 2026) collects at the debit or the receipt, whichever is earlier.
    An order of 2,000 kg of scrap at 45 (90,000) takes a 30% advance of 27,000, which collects 270. The
    invoice for the goods, 106,200 with its GST, counts the advance and collects 792 on the 79,200 left:
    1,062 in all, as the invoice alone would (calc_fix_money/g22/o243.py).
    """

    def setUp(self):
        super().setUp()
        from apps.core.models import Company
        from apps.sales.models import SalesOrder, SalesOrderLine

        Company.objects.update(customer_deposit_account=Account.objects.create(
            code="2300", name="Customer deposits", account_type=AccountType.LIABILITY))
        self.order = SalesOrder.objects.create(customer=self.mh, order_date=DAY, currency=self.inr)
        SalesOrderLine.objects.create(order=self.order, item=self.scrap, uom=self.scrap.uom, quantity=D("2000"),
                                      unit_price=D("45"), revenue_account=self.revenue)
        self.order.confirm()
        self.advance = self.order.create_down_payment_invoice(self.ar, percent=30)
        self.advance.post()

    def goods(self, kg):
        from apps.sales.models import Invoice, InvoiceLine

        invoice = Invoice.objects.create(customer=self.mh, invoice_date=DAY, receivable_account=self.ar,
                                         currency=self.inr, sales_order=self.order)
        line = InvoiceLine.objects.create(invoice=invoice, item=self.scrap, quantity=D(kg), unit_price=D("45"),
                                          revenue_account=self.revenue, order_line=self.order.lines.get())
        line.taxes.set(self.pair)
        invoice.post()
        return invoice

    def test_collected_on_the_advance_and_counted_against_the_invoice(self):
        (row,) = self.advance.tcs_collected.all()
        self.assertEqual((row.base, row.amount, self.advance.tcs_amount), (D("27000.00"), D("270"), D("270")))
        invoice = self.goods("2000")
        (row,) = invoice.tcs_collected.all()
        self.assertEqual((row.base, row.amount), (D("79200.00"), D("792")))
        self.assertEqual(self.ledger("2290"), D("1062.00"))
        # The deposit holds its value, not the TCS: drawn whole as the invoice posts, the customer owes the
        # rest with the invoice's TCS, 107,262 in all with the advance and its 270.
        self.assertEqual((self.advance.deposit_unapplied(), invoice.amount_due()), (D("0.00"), D("79992.00")))

    def test_the_rest_of_the_order_can_still_be_taken_ahead(self):
        # 27,000 taken (270 TCS beside it): the other 63,000 is not refused as 27,270 + 63,000 > 90,000.
        rest = self.order.create_down_payment_invoice(self.ar, amount="63000")
        rest.post()
        self.assertEqual((self.order.deposit_total(), rest.tcs_amount), (D("90000.00"), D("630")))

    def test_drawn_down_across_two_invoices(self):
        first, second = self.goods("500"), self.goods("1500")
        self.assertEqual([(row.base, row.amount) for inv in (first, second) for row in inv.tcs_collected.all()],
                         [(D("0.00"), D("0")), (D("79200.00"), D("792"))])
        self.assertEqual(self.ledger("2290"), D("1062.00"))

    def test_the_advance_refunded_gives_its_tcs_back(self):
        note = self.advance.create_credit_note(on_date=datetime.date(2026, 9, 20))
        self.assertEqual(note.tcs_amount, D("270"))
        self.assertEqual(self.ledger("2290"), D("0"))

    def test_the_invoice_that_drew_the_advance_credited_gives_both_back(self):
        # The advance drawn into the invoice is refunded through the invoice's note: 792 and 270.
        invoice = self.goods("2000")
        note = self.credit(invoice, "2000", datetime.date(2026, 9, 20))
        self.assertEqual((note.tcs_amount, self.ledger("2290")), (D("1062"), D("0")))

    def test_part_credited_gives_back_its_own_first(self):
        invoice = self.goods("2000")
        note = self.credit(invoice, "500", datetime.date(2026, 9, 20))
        # 26,550 of the invoice's own 79,200 at 1%: 265.50, 266.
        self.assertEqual((note.tcs_amount, self.ledger("2290")), (D("266"), D("796.00")))


class ARowTheUpgradeCouldNotTellTests(TcsTestCase):
    """sales 0072 leaves a row's section empty where the books cannot say it; it is never printed or filed blank."""

    def test_is_neither_printed_nor_filed_under_a_guess(self):
        invoice = self.t1()
        row = invoice.tcs_collected.get()
        TcsCollection.objects.filter(pk=row.pk).update(section_code="")
        said = f"TCS row {row.pk} on {invoice.number} of 10 Sep 2026 does not say which section it was issued under"
        with self.assertRaisesMessage(ValidationError, said):
            tcs_return(DAY, DAY)
        with self.assertRaisesMessage(ValidationError, said):
            tax_invoice(type(invoice).objects.get(pk=invoice.pk))


def _migrated_to_the_leaves():
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


def _at(*when):
    return datetime.datetime(*when, tzinfo=datetime.timezone.utc)


class _SalesTakenBack(TransactionTestCase):
    """Only sales is taken back; the accounts, sections and entries are made as they stand now, by id."""

    def taken_back(self):
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        self.addCleanup(_migrated_to_the_leaves)
        self.apps = executor.loader.project_state(self.before).apps
        now = executor.loader.project_state(executor.loader.graph.leaf_nodes()).apps
        self.now = now
        account = now.get_model("accounting", "Account")
        self.receivable = account.objects.create(code="1200", name="Receivables", account_type="asset")
        self.owed, self.moved = (account.objects.create(code=code, name=code, account_type="liability")
                                 for code in ("2290", "2291"))
        self.customer = now.get_model("core", "Party").objects.create(code="C-MH", name="Pune Recyclers")
        self.sections = now.get_model("accounting", "TcsSection")

    def entry(self, memo, *lines):
        made = self.now.get_model("accounting", "JournalEntry").objects.create(date=DAY, memo=memo, posted=True)
        for onto, debit, credit in lines:
            self.now.get_model("accounting", "JournalLine").objects.create(
                entry=made, account=onto, debit=D(debit), credit=D(credit))
        return made

    def collected(self, number, section, amount, day=DAY, memo=None, lines=(), **more):
        invoice = self.apps.get_model("sales", "Invoice").objects.create(
            customer_id=self.customer.pk, invoice_date=day, receivable_account_id=self.receivable.pk,
            number=number, posted=True)
        entry = self.entry(memo or f"TCS {section.code} on {number}", *lines) if (lines or memo) else None
        return self.apps.get_model("sales", "TcsCollection").objects.create(
            invoice=invoice, section_id=section.pk, base=D(amount) * 100, rate_percent=D("1"), amount=D(amount),
            date=day, journal_entry_id=entry.pk if entry else None, **more)

    def forward(self):
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        return executor.loader.project_state(self.after).apps.get_model("sales", "TcsCollection")

    def back(self):
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        return executor.loader.project_state(self.before).apps.get_model("sales", "TcsCollection")


# Slow: each unwinds sales's latest migrations and replays them.
@tag("migration")
class CollectionsAlreadyPostedMigrationTests(_SalesTakenBack):
    """
    sales 0071: a row posted before it kept posted_account keeps the account its own entry booked to the
    payable side (a collection's credit, a give-back's debit), never the one its section names at the
    upgrade. From calc_fix_mig/scenarios.py:

      C1  1,062.00 on INV-1, Dr 1200 / Cr 2290, while SCRAP read 2290       -> 2290
      G1    266.00 given back by CN-1, Dr 2290 / Cr 1200                    -> 2290 (its debit)
      --  SCRAP moved to 2291: C1 and G1 are the rows whose section moved after they posted
      C2  1,062.00 on INV-2, Dr 1200 / Cr 2291                              -> 2291
      C3      0.00 on INV-3: collected nothing, no entry                    -> none, as the constraint allows
      C4    100.00 on INV-4 under a section wired to the receivable: Dr 1200 / Cr 1200 -> 1200
          (the old read took a line to anything but 1200, found none, and the constraint refused)
      C5     50.00 on INV-5 with no entry: no code made it -> the upgrade stops and names it;
          taken off, it runs.
      Back: the column goes, and the five rows and their amounts stay.
    """

    before = [("sales", "0070_tcs")]
    after = [("sales", "0071_posted_account")]

    def test_each_keeps_the_account_its_own_entry_booked(self):
        self.taken_back()
        scrap = self.sections.objects.create(code="206C(1)", name="Scrap", payable_account_id=self.owed.pk)
        wired = self.sections.objects.create(code="206C(1F)", name="Wired to the receivable",
                                             payable_account_id=self.receivable.pk)
        c1 = self.collected("INV-1", scrap, "1062", lines=[(self.receivable, "1062", "0"), (self.owed, "0", "1062")])
        self.collected("CN-1", scrap, "266", gives_back=c1,
                       lines=[(self.owed, "266", "0"), (self.receivable, "0", "266")])
        self.sections.objects.filter(pk=scrap.pk).update(payable_account_id=self.moved.pk)
        self.collected("INV-2", scrap, "1062", lines=[(self.receivable, "1062", "0"), (self.moved, "0", "1062")])
        self.collected("INV-3", scrap, "0")
        self.collected("INV-4", wired, "100", lines=[(self.receivable, "100", "0"), (self.receivable, "0", "100")])
        c5 = self.collected("INV-5", scrap, "50")

        with self.assertRaisesMessage(RuntimeError, f"TCS collection {c5.pk} of 50.00 on invoice {c5.invoice_id} "
                                                    "(2026-09-10): it has no entry"):
            self.forward()
        self.apps.get_model("sales", "TcsCollection").objects.filter(pk=c5.pk).delete()
        kept = self.forward()
        self.assertEqual(sorted(kept.objects.values_list("invoice__number", "amount", "posted_account_id")),
                         [("CN-1", D("266.00"), self.owed.pk), ("INV-1", D("1062.00"), self.owed.pk),
                          ("INV-2", D("1062.00"), self.moved.pk), ("INV-3", D("0.00"), None),
                          ("INV-4", D("100.00"), self.receivable.pk)])

        back = self.back()
        self.assertNotIn("posted_account", {field.name for field in back._meta.get_fields()})
        self.assertEqual(sorted(back.objects.values_list("invoice__number", "amount")),
                         [("CN-1", D("266.00")), ("INV-1", D("1062.00")), ("INV-2", D("1062.00")),
                          ("INV-3", D("0.00")), ("INV-4", D("100.00"))])


@tag("migration")
class SectionsAlreadyCollectedUnderMigrationTests(_SalesTakenBack):
    """
    sales 0072: a row collected before it kept its section keeps the code it was issued under, read off
    its own entry's memo as it posted; never the section's code at the upgrade. SCRAP was made "206C(1)"
    on 1 April 2025 and renamed "394(1)" on 1 April 2026 (calc_fix_mig/scenarios.py):

      N1  INV-1, 10 Sep 2025, 1,062.00, memo "TCS 206C(1) on INV-1"          -> 206C(1)
      N2  INV-3, 12 Sep 2025, collected nothing (no entry), made before the rename -> empty: nothing says
      N6  INV-5, 15 Sep 2025, 1,062.00, a memo not in the code's shape, before the rename -> empty
      N4  INV-2, 16 Apr 2026, 1,062.00, memo "TCS 394(1) on INV-2"          -> 394(1)
      N5  INV-4, 17 Apr 2026, collected nothing, made after the rename      -> 394(1): unsaved since
      N3  CN-1, 20 Apr 2026, gives 266.00 of INV-1 back, memo "TCS 394(1) given back ..." -> 206C(1),
          its collection's
      The old read gave all six 394(1): a 2025 invoice under the 2025 Act, reprinted and returned so.
      Back: the column goes, and the six rows stay.
    """

    before = [("sales", "0071_posted_account")]
    after = [("sales", "0072_tcs_section_by_date")]

    def test_each_keeps_the_code_it_was_issued_under(self):
        self.taken_back()
        scrap = self.sections.objects.create(code="206C(1)", name="Scrap", payable_account_id=self.owed.pk)
        self.sections.objects.filter(pk=scrap.pk).update(created_at=_at(2025, 4, 1, 10), updated_at=_at(2025, 4, 1, 10))
        rows = self.apps.get_model("sales", "TcsCollection").objects

        def made(row, when):
            rows.filter(pk=row.pk).update(created_at=when, updated_at=when)
            return row

        def money(number, amount):
            return [(self.receivable, amount, "0"), (self.owed, "0", amount)]

        n1 = made(self.collected("INV-1", scrap, "1062", datetime.date(2025, 9, 10), lines=money("INV-1", "1062"),
                                 posted_account_id=self.owed.pk), _at(2025, 9, 10, 10))
        made(self.collected("INV-3", scrap, "0", datetime.date(2025, 9, 12)), _at(2025, 9, 12, 10))
        made(self.collected("INV-5", scrap, "1062", datetime.date(2025, 9, 15), memo="Scrap TCS",
                            lines=money("INV-5", "1062"), posted_account_id=self.owed.pk), _at(2025, 9, 15, 10))
        self.sections.objects.filter(pk=scrap.pk).update(code="394(1)", updated_at=_at(2026, 4, 1, 10))
        scrap.code = "394(1)"
        made(self.collected("INV-2", scrap, "1062", datetime.date(2026, 4, 16), lines=money("INV-2", "1062"),
                            posted_account_id=self.owed.pk), _at(2026, 4, 16, 10))
        made(self.collected("INV-4", scrap, "0", datetime.date(2026, 4, 17)), _at(2026, 4, 17, 10))
        made(self.collected("CN-1", scrap, "266", datetime.date(2026, 4, 20),
                            memo="TCS 394(1) given back by CN-1 on INV-1",
                            lines=[(self.owed, "266", "0"), (self.receivable, "0", "266")],
                            posted_account_id=self.owed.pk, gives_back=n1), _at(2026, 4, 20, 10))

        kept = self.forward()
        self.assertEqual(sorted(kept.objects.values_list("invoice__number", "section_code")),
                         [("CN-1", "206C(1)"), ("INV-1", "206C(1)"), ("INV-2", "394(1)"), ("INV-3", ""),
                          ("INV-4", "394(1)"), ("INV-5", "")])

        back = self.back()
        self.assertNotIn("section_code", {field.name for field in back._meta.get_fields()})
        self.assertEqual(back.objects.count(), 6)


class TheReturnAndTheChallanKeepTheirCodesTests(TcsTestCase):
    """O245: the collection code and the challan's section are kept at the event, not read from the section."""

    def test_an_edited_section_does_not_re_code_a_filed_quarter_or_a_paid_challan(self):
        from apps.sales.tcs import TcsChallan, tcs_return

        self.t1()
        challan = TcsChallan.pay(self.section, datetime.date(2026, 9, 1), datetime.date(2026, 10, 5), self.bank,
                                 "00042", "0510308")
        self.section.form_code, self.section.code = "6CF", "206C(1H)"
        self.section.save()
        rows = tcs_return(datetime.date(2026, 7, 1), datetime.date(2026, 9, 30))
        challan.refresh_from_db()
        self.assertEqual(([row["collection_code"] for row in rows], challan.section_code), (["6CE"], "206C(1)"))



class NoConfirmedRateIsARefusalTests(TcsTestCase):
    """
    Integrator's decision (g22 review): a scrap invoice or a scrap advance with no confirmed rate in force on
    its day is refused at posting, naming the section and where the accountant confirms it; never posted
    collecting nothing.
    """

    def setUp(self):
        super().setUp()
        TcsRate.objects.create(section=self.section, applies_from=datetime.date(2026, 9, 1), rate_percent=D("2"),
                               no_pan_rate_percent=D("5"))

    def test_the_invoice_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "TCS under 206C(1) at 2% from 01 Sep 2026 is not confirmed"):
            self.t1()
        self.assertEqual(self.ledger("2290"), D("0"))

    def test_the_advance_is_refused_naming_where(self):
        from apps.core.models import Company
        from apps.sales.models import SalesOrder, SalesOrderLine

        Company.objects.update(customer_deposit_account=Account.objects.create(
            code="2300", name="Customer deposits", account_type=AccountType.LIABILITY))
        order = SalesOrder.objects.create(customer=self.mh, order_date=DAY, currency=self.inr)
        SalesOrderLine.objects.create(order=order, item=self.scrap, uom=self.scrap.uom, quantity=D("100"),
                                      unit_price=D("45"), revenue_account=self.revenue)
        order.confirm()
        advance = order.create_down_payment_invoice(self.ar, percent=30)
        with self.assertRaisesMessage(ValidationError, "under Settings > TCS rates"):
            advance.post()



class ADebitNoteOnScrapCollectsTcsTests(TcsTestCase):
    """
    A debit note of 1,000 on T1 (2,000 kg of scrap, 106,200 with GST, 1,062 collected): its 1,000 and 180 GST
    are 1,180 of value TCS collects on, 12 at 1%; the credit note reversing it gives the 12 back.
    """

    def test_collected_on_the_debit_note_and_given_back_by_its_reverse(self):
        invoice = self.t1()
        note = invoice.debit("1000", "Rate agreed at 45.50", on_date=DAY)
        (row,) = note.tcs_collected.all()
        self.assertEqual((row.base, row.amount, note.total(), self.ledger("2290")),
                         (D("1180.00"), D("12"), D("1192.00"), D("1074.00")))
        back = note.create_credit_note(on_date=DAY)
        self.assertEqual((back.tcs_amount, self.ledger("2290")), (D("12"), D("1062.00")))
