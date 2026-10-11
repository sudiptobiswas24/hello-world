"""
GST on an advance for job work.

Job work is a service, and GST on a service is due when the advance is
received, not when the work is invoiced. The advance carries its tax;
drawing it down against the final invoice gives that tax back, since the
invoice charges tax on the whole; returning it gives the tax back too.
GSTR-1 reports what was received and not invoiced in the month (table
11A) and what earlier advances were adjusted (11B); GSTR-3B nets the two
into 3.1(a). An advance for goods bears none. Refusals first; every
figure is worked in scratchpad/l1_scenarios.py.
"""

import datetime

from django.core.exceptions import ValidationError

from apps.accounting.models import Account, AccountType
from apps.core.models import Company
from apps.inventory.models import Item
from apps.sales.models import InvoiceLine, InvoicePolicy, SalesOrder, SalesOrderLine, SuppliedItem

from .returns import gstr1, gstr1_json, gstr3b, month
from .tests import D, GstReturnTestCase, gstin

SEPTEMBER, OCTOBER = month("2026-09"), month("2026-10")
SEP, OCT = datetime.date(2026, 9, 10), datetime.date(2026, 10, 5)


class AdvanceTestCase(GstReturnTestCase):
    def setUp(self):
        super().setUp()
        self.deposits = Account.objects.create(code="2400", name="Customer advances",
                                               account_type=AccountType.LIABILITY)
        company = Company.get()
        company.customer_deposit_account = self.deposits
        company.save()
        self.settings.job_work_sac = "998821"
        self.settings.save()
        self.customer = self.party("C-JW", gstin=gstin("27AAACM1234K1Z"), gst_state="27",
                                   gst_registration="regular")
        self.conversion = Item.objects.create(sku="CONV", name="Conversion to sacks",
                                              uom=self.sack.uom, hsn_code="998821", item_type="service",
                                              track_inventory=False)
        self.granules = Item.objects.create(sku="PP", name="PP granules", uom=self.sack.uom)

    def job_work(self, quantity="1", price="10000", taxes=None, job_work=True):
        order = SalesOrder.objects.create(customer=self.customer, order_date=SEP, currency=self.inr,
                                          is_job_work=job_work, invoice_policy=InvoicePolicy.ORDERED)
        line = SalesOrderLine.objects.create(order=order, item=self.conversion, uom=self.sack.uom,
                                             quantity=D(quantity), unit_price=D(price),
                                             revenue_account=self.revenue)
        line.taxes.set(self.pair if taxes is None else taxes)
        if job_work:
            SuppliedItem.objects.create(order=order, item=self.granules)
        order.confirm()
        return order

    def advance(self, order, percent="30", day=SEP):
        deposit = order.create_down_payment_invoice(self.ar, percent=D(percent), invoice_date=day)
        deposit.post()
        return deposit

    def bill(self, order, day=OCT, quantity=None):
        if quantity is None:
            invoice = order.create_invoice(self.ar, invoice_date=day)
        else:
            from apps.sales.models import Invoice

            invoice = Invoice.objects.create(customer=self.customer, invoice_date=day, sales_order=order,
                                             receivable_account=self.ar, currency=self.inr)
            order_line = order.lines.get()
            line = InvoiceLine.objects.create(invoice=invoice, order_line=order_line, item=self.conversion,
                                              quantity=D(quantity), unit_price=order_line.unit_price,
                                              revenue_account=self.revenue)
            line.taxes.set(self.pair)
        invoice.post()
        return invoice

    def advances(self, period):
        result = gstr1(*period)
        return result["advances_received"], result["advances_adjusted"]

    def row(self, taxable, cgst, sgst):
        return {"supply": "intra", "place_of_supply": "27", "rate": D("18"), "taxable": D(taxable),
                "igst": D("0"), "cgst": D(cgst), "sgst": D(sgst), "cess": D("0")}


class RefusalTests(AdvanceTestCase):
    def test_an_advance_for_goods_bears_no_tax(self):
        order = self.job_work(job_work=False)
        deposit = order.create_down_payment_invoice(self.ar, percent=D("30"), invoice_date=SEP)
        self.assertFalse(deposit.lines.get().taxes.exists())
        deposit.lines.get().taxes.set(self.pair)
        with self.assertRaisesMessage(ValidationError, "No tax is due on an advance for goods"):
            deposit.post()

    def test_a_job_work_order_at_two_rates_takes_no_single_advance(self):
        order = SalesOrder.objects.create(customer=self.customer, order_date=SEP, currency=self.inr,
                                          is_job_work=True)
        for price, taxes in (("100", self.pair), ("50", [])):
            line = SalesOrderLine.objects.create(order=order, item=self.conversion, uom=self.sack.uom,
                                                 quantity=D("1"), unit_price=D(price),
                                                 revenue_account=self.revenue)
            line.taxes.set(taxes)
        SuppliedItem.objects.create(order=order, item=self.granules)
        order.confirm()
        with self.assertRaisesMessage(ValidationError, "different taxes"):
            order.create_down_payment_invoice(self.ar, percent=D("30"))

    def test_a_part_the_tax_cannot_reach_to_the_paisa_is_refused_naming_what_can(self):
        deposit = self.advance(self.job_work())
        with self.assertRaisesMessage(ValidationError, "give back 1180.05 or 1180.08"):
            deposit.create_credit_note(amount=D("1180.06"))
        self.assertEqual(deposit.deposit_unapplied(), D("3540.00"))


class TheAdvanceCarriesItsTaxTests(AdvanceTestCase):
    def test_received_in_september_and_invoiced_in_october(self):
        order = self.job_work()
        deposit = self.advance(order)
        line = deposit.lines.get()
        self.assertEqual((line.net_amount(), deposit.tax_total(), deposit.total()),
                         (D("3000.00"), D("540.00"), D("3540.00")))
        self.assertEqual((self.ledger("2201"), self.ledger("2202"), self.ledger("2400")),
                         (D("270.00"), D("270.00"), D("3000.00")))
        self.assertEqual(self.advances(SEPTEMBER), ([self.row("3000.00", "270.00", "270.00")], []))
        september = gstr3b(*SEPTEMBER)["3.1a"]
        self.assertEqual((september["taxable"], september["cgst"], september["sgst"]),
                         (D("3000.00"), D("270.00"), D("270.00")))

        invoice = self.bill(order)
        self.assertEqual((invoice.total(), invoice.amount_deposited(), invoice.amount_due()),
                         (D("11800.00"), D("3540.00"), D("8260.00")))
        # The advance's tax went back when it was drawn down: the invoice's alone remains.
        self.assertEqual((self.ledger("2201"), self.ledger("2202"), self.ledger("2400")),
                         (D("900.00"), D("900.00"), D("0.00")))
        self.assertEqual(self.advances(OCTOBER), ([], [self.row("3000.00", "270.00", "270.00")]))
        october = gstr3b(*OCTOBER)["3.1a"]
        self.assertEqual((october["taxable"], october["cgst"], october["sgst"]),
                         (D("7000.00"), D("630.00"), D("630.00")))
        payload = gstr1_json(gstr1(*OCTOBER))
        self.assertEqual(payload["txpd"], [{"pos": "27", "sply_ty": "INTRA", "itms": [
            {"rt": 18.0, "ad_amt": 3000.0, "iamt": 0.0, "camt": 270.0, "samt": 270.0, "csamt": 0.0}]}])
        self.assertEqual(payload["at"], [])
        self.assertNotIn("GSTR-1 table 11: advances", gstr1(*OCTOBER)["not_built"])

    def test_received_and_invoiced_in_one_month_is_in_neither_table(self):
        order = self.job_work()
        self.advance(order, day=SEP)
        self.bill(order, day=datetime.date(2026, 9, 25))
        self.assertEqual(self.advances(SEPTEMBER), ([], []))
        september = gstr3b(*SEPTEMBER)["3.1a"]
        self.assertEqual((september["taxable"], september["cgst"]), (D("10000.00"), D("900.00")))

    def test_part_given_back_and_the_rest_drawn_down_clears_the_tax_account(self):
        order = self.job_work()
        deposit = self.advance(order)
        note = deposit.create_credit_note(amount=D("1180"))
        self.assertEqual((note.lines.get().net_amount(), note.tax_total(), note.total()),
                         (D("1000.00"), D("180.00"), D("1180.00")))
        self.assertEqual(deposit.deposit_unapplied(), D("2360.00"))
        self.bill(order)
        self.assertEqual((self.ledger("2201"), self.ledger("2202"), self.ledger("2400")),
                         (D("900.00"), D("900.00"), D("0.00")))

    def test_drawn_down_in_part_then_returned_clears_to_the_paisa(self):
        # 9 x 1,234.57 = 11,111.13, tax 1,000.00 + 1,000.00; the whole taken
        # up front. Four invoiced (4,938.28 + 444.45 + 444.45) draw 5,827.18
        # down, reversing 444.45 a head. Given back by proportion alone, the
        # rest would reverse 555.56 a head where 555.55 is left: a paisa on
        # each GST account for good.
        order = self.job_work(quantity="9", price="1234.57")
        deposit = self.advance(order, percent="100")
        self.assertEqual((deposit.lines.get().net_amount(), deposit.total()), (D("11111.13"), D("13111.13")))
        invoice = self.bill(order, quantity="4")
        self.assertEqual(invoice.amount_deposited(), D("5827.18"))
        note = deposit.create_credit_note()  # the rest: 7,283.95
        self.assertEqual((note.lines.get().net_amount(), note.tax_total(), note.total()),
                         (D("6172.85"), D("1111.10"), D("7283.95")))
        self.assertEqual(deposit.deposit_unapplied(), D("0.00"))
        # Left on each GST account: the four units invoiced, and not a paisa more.
        self.assertEqual((self.ledger("2201"), self.ledger("2202"), self.ledger("2400")),
                         (D("444.45"), D("444.45"), D("0.00")))
