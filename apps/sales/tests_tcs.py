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
        with self.assertRaisesMessage(ValidationError, "The 206C(1) rate of 2% from 01 Sep 2026 is not confirmed "
                                                       "by the accountant"):
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
