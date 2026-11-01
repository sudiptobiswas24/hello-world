"""
O395 and O396, through the API as the people who do the work, on the demo plant.

A line typed on screen carried no tax: an item had no default taxes, the
picker was too small to use, and nothing refused a taxable line with no tax.
A rep's order for 20,000 rice bags to Konkan Agro Foods (Maharashtra, as the
plant is) was confirmed, shipped, invoiced and posted at 2,24,000.00 with no
GST. A bill posted without the vendor's invoice number, which the input
credit and the duplicate-invoice check both key on.

Numbers, worked in /home/user/work/calc_fix_tax/cases.py:
  20,000 x 11.20 = 2,24,000.00; CGST 9% 20,160.00, SGST 9% 20,160.00, total 2,64,320.00;
  across a state line IGST 18% 40,320.00, the same total.
  5,000 kg x 112.00 = 5,60,000.00; CGST 50,400.00, SGST 50,400.00, total 6,60,800.00.
"""

from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounting.models import Account, PartyTaxProfile, Tax
from apps.core.models import Party
from apps.inventory.models import Item
from apps.purchasing.models import Bill, GoodsReceiptLine
from apps.sales.models import Invoice, SalesOrder
from apps.web.tests_load_demo import load

D = Decimal
User = get_user_model()


class UntaxedLinesTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        load(password="Trial-2026", stdout=StringIO(), stderr=StringIO())

    def as_(self, username):
        client = APIClient()
        client.force_authenticate(User.objects.get(username=username))
        return client

    def order(self, client, customer_code, sku, quantity, **line):
        customer = Party.objects.get(code=customer_code)
        made = client.post("/api/sales/sales-orders/", {"customer": customer.pk, "order_date": str(timezone.localdate())},
                           format="json")
        self.assertEqual(made.status_code, 201, made.content)
        order = made.json()["id"]
        added = client.post("/api/sales/sales-order-lines/", {
            "order": order, "item": Item.objects.get(sku=sku).pk, "quantity": quantity, **line}, format="json")
        self.assertEqual(added.status_code, 201, added.content)
        return SalesOrder.objects.get(pk=order), added.json()


class ALineTakesItsItemsTaxes(UntaxedLinesTestCase):
    def test_a_rep_s_line_with_no_taxes_named_takes_the_items(self):
        imran = self.as_("imran")
        order, line = self.order(imran, "C-KONK", "SACK-RICE25", "20000", unit_price="11.20")
        self.assertEqual(sorted(Tax.objects.filter(pk__in=line["taxes"]).values_list("code", flat=True)),
                         ["CGST9", "SGST9"])
        self.assertEqual(order.total(), D("264320.00"))
        confirmed = imran.post(f"/api/sales/sales-orders/{order.pk}/confirm/", format="json")
        self.assertEqual(confirmed.status_code, 200, confirmed.content)

    def test_across_a_state_line_the_same_defaults_are_integrated_tax(self):
        # Godavari Fertilisers is in Andhra Pradesh; the plant is in Maharashtra.
        rohit = self.as_("rohit")
        order, _ = self.order(rohit, "C-GODA", "SACK-RICE25", "20000", unit_price="11.20")
        line = order.lines.get()
        self.assertEqual([tax.code for tax in line.effective_taxes()], ["IGST18"])
        self.assertEqual(line.tax_total(), D("40320.00"))
        self.assertEqual(order.total(), D("264320.00"))

    def test_a_purchase_line_takes_the_items_purchase_taxes(self):
        ravi = self.as_("ravi")
        vendor = Party.objects.get(code="V-GRAN")
        made = ravi.post("/api/purchasing/purchase-orders/", {"vendor": vendor.pk, "order_date": str(timezone.localdate())},
                         format="json")
        self.assertEqual(made.status_code, 201, made.content)
        added = ravi.post("/api/purchasing/purchase-order-lines/", {
            "order": made.json()["id"], "item": Item.objects.get(sku="PP-RAFFIA").pk, "quantity": "5000",
            "unit_price": "112.00"}, format="json")
        self.assertEqual(added.status_code, 201, added.content)
        self.assertEqual(sorted(Tax.objects.filter(pk__in=added.json()["taxes"]).values_list("code", flat=True)),
                         ["CGST9", "SGST9"])
        self.assertEqual(added.json()["total"], "660800.00")


class ALineWithNoTaxIsRefused(UntaxedLinesTestCase):
    def test_an_order_whose_line_names_no_tax_is_not_confirmed(self):
        imran = self.as_("imran")
        order, line = self.order(imran, "C-KONK", "SACK-RICE25", "20000", unit_price="11.20", taxes=[])
        self.assertEqual(line["taxes"], [])  # named none on purpose: kept so, then refused
        refused = imran.post(f"/api/sales/sales-orders/{order.pk}/confirm/", format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("names no tax", str(refused.json()))
        order.refresh_from_db()
        self.assertEqual(order.status, "draft")

    def test_an_exempt_customer_s_order_is_confirmed_without_tax(self):
        PartyTaxProfile.objects.filter(party__code="C-KONK").update(tax_exempt=True, exemption_reference="EX-1")
        imran = self.as_("imran")
        order, _ = self.order(imran, "C-KONK", "SACK-RICE25", "100", unit_price="11.20", taxes=[])
        confirmed = imran.post(f"/api/sales/sales-orders/{order.pk}/confirm/", format="json")
        self.assertEqual(confirmed.status_code, 200, confirmed.content)

    def test_an_invoice_whose_line_names_no_tax_is_not_posted(self):
        invoice = Invoice.objects.filter(posted=False, is_down_payment=False).exclude(lines=None).first()
        if invoice is None:  # every demo invoice is posted: draft one from a posted one's customer
            posted = Invoice.objects.filter(posted=True, is_down_payment=False).first()
            invoice = Invoice.objects.create(customer=posted.customer, receivable_account=posted.receivable_account,
                                             invoice_date=posted.invoice_date)
            invoice.lines.create(item=Item.objects.get(sku="SACK-RICE25"), quantity=D("10"), unit_price=D("11.20"),
                                 revenue_account=posted.lines.first().revenue_account)
        for line in invoice.lines.all():
            line.taxes.clear()
        refused = self.as_("rohit").post(f"/api/sales/invoices/{invoice.pk}/post_invoice/", format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("names no tax", str(refused.json()))


class ABillNeedsItsTaxesAndTheVendorsNumber(UntaxedLinesTestCase):
    def draft_bill(self, reference="GHP/26/0412", taxes=True):
        posted = Bill.objects.filter(posted=True, is_prepayment=False, is_opening_balance=False,
                                     lines__item__sku="PP-RAFFIA").first() or \
            Bill.objects.filter(posted=True, is_prepayment=False, is_opening_balance=False).first()
        bill = Bill.objects.create(vendor=posted.vendor, payable_account=posted.payable_account,
                                   bill_date=posted.bill_date, reference=reference)
        # An item nothing has come in of: one received and unbilled would be refused first, rightly, for naming
        # no order line.
        item = Item.objects.exclude(pk__in=GoodsReceiptLine.objects.values("order_line__item")).filter(item_type="goods").first()
        line = bill.lines.create(item=item, quantity=D("10"), unit_price=D("112.00"),
                                 expense_account=posted.lines.first().expense_account)
        if taxes:
            line.taxes.set(Tax.objects.filter(code__in=["CGST9", "SGST9"]))
        return bill

    def test_a_bill_without_the_vendor_s_invoice_number_is_not_posted(self):
        bill = self.draft_bill(reference="")
        refused = self.as_("ravi").post(f"/api/purchasing/bills/{bill.pk}/post_bill/", format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("vendor's invoice number", str(refused.json()))
        bill.refresh_from_db()
        self.assertFalse(bill.posted)

    def test_a_bill_whose_line_names_no_tax_is_not_posted(self):
        bill = self.draft_bill(taxes=False)
        refused = self.as_("ravi").post(f"/api/purchasing/bills/{bill.pk}/post_bill/", format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("names no tax", str(refused.json()))


class ALineWithNoTaxIsLawfulWhereNoGstIsCharged(UntaxedLinesTestCase):
    """The refusal is for a taxable good stripped of its tax, not for every line without one (table 5)."""

    def draft(self, vendor, item, reference="UNREG/1"):
        posted = Bill.objects.filter(posted=True, is_prepayment=False, is_opening_balance=False).first()
        bill = Bill.objects.create(vendor=vendor, payable_account=posted.payable_account,
                                   bill_date=posted.bill_date, reference=reference)
        bill.lines.create(item=item, quantity=D("10"), unit_price=D("50.00"),
                          expense_account=Account.objects.get(code="5400"))  # repairs: an expense, not stock
        return bill

    def free_item(self):
        return Item.objects.exclude(pk__in=GoodsReceiptLine.objects.values("order_line__item")).filter(
            item_type="goods").first()

    def test_an_unregistered_supplier_s_bill_carries_no_gst_and_posts(self):
        vendor = Party.objects.create(code="V-LOCAL", name="Local hardware shop")
        vendor.role_assignments.create(role="vendor")
        PartyTaxProfile.objects.create(party=vendor, gst_state="27", gst_registration="unregistered")
        bill = self.draft(vendor, self.free_item(), reference="")
        posted = self.as_("ravi").post(f"/api/purchasing/bills/{bill.pk}/post_bill/", format="json")
        self.assertEqual(posted.status_code, 200, posted.content)

    def test_an_item_that_declares_no_tax_is_a_non_gst_supply_and_posts(self):
        item = self.free_item()
        item.purchase_taxes.clear()  # diesel, say: outside GST, reported in table 5
        bill = self.draft(Bill.objects.filter(posted=True).first().vendor, item)
        posted = self.as_("ravi").post(f"/api/purchasing/bills/{bill.pk}/post_bill/", format="json")
        self.assertEqual(posted.status_code, 200, posted.content)
