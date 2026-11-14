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
        # A regular registrant's bill with an expense line to copy: the demo also holds an import, and a bill
        # priced on a rate contract, whose lines clear the accrual and name no expense account.
        posted = Bill.objects.filter(posted=True, is_prepayment=False, is_opening_balance=False,
                                     lines__item__sku="PP-RAFFIA", vendor__tax_profile__gst_registration="regular",
                                     lines__expense_account__isnull=False).first() or \
            Bill.objects.filter(posted=True, is_prepayment=False, is_opening_balance=False,
                                vendor__tax_profile__gst_registration="regular").first()
        # A regular registrant's, as the first choice asks: O416 gave the demo an open import bill from an overseas
        # vendor, and copied from it the line was refused for its expense account before either refusal asked here.
        bill = Bill.objects.create(vendor=posted.vendor, payable_account=posted.payable_account,
                                   bill_date=posted.bill_date, reference=reference)
        # An item nothing has come in of: one received and unbilled would be refused first, rightly, for naming
        # no order line.
        item = Item.objects.exclude(pk__in=GoodsReceiptLine.objects.values("order_line__item")).filter(item_type="goods").first()
        # Built in code a line takes its item's taxes (O459): one meant to name none says so.
        bill.lines.create(item=item, quantity=D("10"), unit_price=D("112.00"),
                          expense_account=posted.lines.first().expense_account,
                          given_taxes=Tax.objects.filter(code__in=["CGST9", "SGST9"]) if taxes else [])
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


class ALineBuiltInCodeTakesItsItemsTaxes(UntaxedLinesTestCase):
    """
    O459: a line built in code took no taxes, because only the API's line serializer applied the item's
    defaults. The 'Loom shed spares' requisition ordered by ravi gave a draft confirm refused. Worked in
    /home/user/work/calc_fix_demo/o459.py: 30 x 240.00 + 6 x 1,850.00 = 18,300.00; Loomtech is in Tamil
    Nadu, so IGST 18% 3,294.00, total 21,594.00.
    """

    def purchase_codes(self, line):
        return sorted(tax.code for tax in line.taxes.all())

    def test_an_order_made_from_a_requisition_takes_its_items_taxes_and_confirms(self):
        from apps.purchasing.models import PurchaseOrder, PurchaseRequisition

        ravi = self.as_("ravi")
        requisition = PurchaseRequisition.objects.get(justification="Loom shed spares for the quarter")
        made = ravi.post(f"/api/purchasing/requisitions/{requisition.pk}/order/",
                         {"vendor": Party.objects.get(code="V-SPAR").pk}, format="json")
        self.assertIn(made.status_code, (200, 201), made.content)
        order = PurchaseOrder.objects.get(reference=requisition.number)
        for line in order.lines.all():
            self.assertEqual(self.purchase_codes(line), sorted(t.code for t in line.item.purchase_taxes.all()))
            self.assertTrue(line.taxes.exists())
        confirmed = ravi.post(f"/api/purchasing/purchase-orders/{order.pk}/confirm/", format="json")
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        order.refresh_from_db()
        self.assertEqual((order.subtotal(), order.tax_total(), order.total()),
                         (D("18300.00"), D("3294.00"), D("21594.00")))

    def test_a_drop_ship_order_built_in_code_takes_its_items_taxes(self):
        from apps.purchasing.models import PurchaseOrder

        imran = self.as_("imran")
        order, _ = self.order(imran, "C-KONK", "SACK-RICE25", "100", unit_price="11.20")
        confirmed = imran.post(f"/api/sales/sales-orders/{order.pk}/confirm/", format="json")
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        order.refresh_from_db()
        made = PurchaseOrder.create_for_drop_ship(order, Party.objects.get(code="V-GRAN"))
        line = made.lines.get()
        self.assertEqual(self.purchase_codes(line), sorted(t.code for t in line.item.purchase_taxes.all()))
        self.assertTrue(line.taxes.exists())

    def test_a_copy_keeps_its_source_s_taxes_and_does_not_take_the_defaults(self):
        """An exempt customer's order names no tax; invoiced, the invoice names none either, not the item's."""
        from apps.sales.models import InvoicePolicy

        PartyTaxProfile.objects.filter(party__code="C-KONK").update(tax_exempt=True, exemption_reference="EX-1")
        imran = self.as_("imran")
        order, _ = self.order(imran, "C-KONK", "SACK-RICE25", "100", unit_price="11.20", taxes=[])
        SalesOrder.objects.filter(pk=order.pk).update(invoice_policy=InvoicePolicy.ORDERED)
        confirmed = imran.post(f"/api/sales/sales-orders/{order.pk}/confirm/", format="json")
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        order.refresh_from_db()
        receivable = Invoice.objects.filter(posted=True).first().receivable_account
        invoice = order.create_invoice(receivable)
        self.assertTrue(order.lines.get().item.sales_taxes.exists())  # the defaults it did not take
        self.assertFalse(invoice.lines.get().taxes.exists())

    def test_taxes_set_empty_through_the_api_stay_empty_on_a_later_save(self):
        imran = self.as_("imran")
        order, line = self.order(imran, "C-KONK", "SACK-RICE25", "100", unit_price="11.20", taxes=[])
        changed = imran.patch(f"/api/sales/sales-order-lines/{line['id']}/", {"quantity": "120"}, format="json")
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(changed.json()["taxes"], [])
        stored = order.lines.get()
        stored.save()
        self.assertFalse(stored.taxes.exists())


class AChargeLineTakesItsChargesTaxes(UntaxedLinesTestCase):
    """
    O488: a charge line added through the API named no taxes and took none, where SalesOrder.add_charge and
    PurchaseOrder.add_charge, called by tests only, gave it the charge's. Freight recharged at 1,000.00 went out
    with no GST. A line with no item now takes its charge's taxes for its side, through the party's fiscal
    position, as an item line takes its item's: one rule (TaxedLineMixin.default_taxes) for every line built,
    whoever builds it.

    Numbers, worked in /home/user/work/calc_g32_o488/cases.py: freight 1,000.00 under GST 18%;
    in the state CGST 9% 90.00 + SGST 9% 90.00, across a state line IGST 18% 180.00, total 1,180.00 either way;
    an export under LUT IGST 0% 0.00, total 1,000.00; named CGST 2.5% + SGST 2.5% 25.00 + 25.00, total 1,050.00;
    named none, 1,000.00.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from apps.accounting.models import AccountType, ChargeType

        cls.freight = ChargeType.objects.create(
            code="FRT", name="Freight", hsn_code="9965",
            revenue_account=Account.objects.create(code="4190", name="Freight recharged", account_type=AccountType.INCOME),
            expense_account=Account.objects.create(code="5190", name="Freight inward", account_type=AccountType.EXPENSE))
        cls.freight.taxes.set(Tax.objects.filter(code__in=["CGST9", "SGST9"]))

    def sales_charge(self, client, customer_code, **line):
        customer = Party.objects.get(code=customer_code)
        made = client.post("/api/sales/sales-orders/", {"customer": customer.pk, "order_date": str(timezone.localdate())},
                           format="json")
        self.assertEqual(made.status_code, 201, made.content)
        added = client.post("/api/sales/sales-order-lines/", {
            "order": made.json()["id"], "charge": self.freight.pk, "quantity": "1", "unit_price": "1000.00", **line},
            format="json")
        self.assertEqual(added.status_code, 201, added.content)
        return SalesOrder.objects.get(pk=made.json()["id"]), added.json()

    def purchase_charge(self, client, vendor_code, **line):
        from apps.purchasing.models import PurchaseOrder

        vendor = Party.objects.get(code=vendor_code)
        made = client.post("/api/purchasing/purchase-orders/", {"vendor": vendor.pk, "order_date": str(timezone.localdate())},
                           format="json")
        self.assertEqual(made.status_code, 201, made.content)
        added = client.post("/api/purchasing/purchase-order-lines/", {
            "order": made.json()["id"], "charge": self.freight.pk, "quantity": "1", "unit_price": "1000.00", **line},
            format="json")
        self.assertEqual(added.status_code, 201, added.content)
        return PurchaseOrder.objects.get(pk=made.json()["id"]), added.json()

    def codes(self, line):
        return sorted(Tax.objects.filter(pk__in=line["taxes"]).values_list("code", flat=True))

    def test_a_rep_s_freight_in_the_state_bears_cgst_and_sgst(self):
        imran = self.as_("imran")
        order, line = self.sales_charge(imran, "C-KONK")
        self.assertEqual(self.codes(line), ["CGST9", "SGST9"])
        self.assertEqual([(tax.code, amount) for tax, amount in order.lines.get().tax_amounts()],
                         [("CGST9", D("90.00")), ("SGST9", D("90.00"))])
        self.assertEqual(line["total"], "1180.00")

    def test_freight_across_a_state_line_bears_igst(self):
        order, _ = self.sales_charge(self.as_("rohit"), "C-GODA")  # Andhra Pradesh; the plant is in Maharashtra
        self.assertEqual([(tax.code, amount) for tax, amount in order.lines.get().tax_amounts()],
                         [("IGST18", D("180.00"))])
        self.assertEqual(order.total(), D("1180.00"))

    def test_freight_on_an_export_under_lut_is_stored_as_igst_nil(self):
        # Mapped through the customer's position as it is entered, as an item's taxes are (O427).
        order, line = self.sales_charge(self.as_("rohit"), "C-GULF")
        self.assertEqual(self.codes(line), ["IGST0"])
        self.assertEqual(order.total(), D("1000.00"))

    def test_a_vendor_s_freight_in_the_state_bears_cgst_and_sgst(self):
        order, line = self.purchase_charge(self.as_("ravi"), "V-WMB")  # Thane
        self.assertEqual(self.codes(line), ["CGST9", "SGST9"])
        self.assertEqual([(tax.code, amount) for tax, amount in order.lines.get().tax_amounts()],
                         [("CGST9", D("90.00")), ("SGST9", D("90.00"))])
        self.assertEqual(line["total"], "1180.00")

    def test_a_vendor_s_freight_across_a_state_line_bears_igst(self):
        order, _ = self.purchase_charge(self.as_("ravi"), "V-GRAN")  # Vadodara, Gujarat
        self.assertEqual([(tax.code, amount) for tax, amount in order.lines.get().tax_amounts()],
                         [("IGST18", D("180.00"))])
        self.assertEqual(order.total(), D("1180.00"))

    def test_taxes_named_win_over_the_charge_s(self):
        cgst, sgst = Tax.objects.get(code="CGST9"), Tax.objects.get(code="SGST9")
        named = [Tax.objects.create(code=f"{tax.gst_head.upper()}2.5", name=tax.name, rate=D("2.5"), gst_head=tax.gst_head,
                                    collected_account=tax.collected_account, paid_account=tax.paid_account).pk
                 for tax in (cgst, sgst)]
        _, sold = self.sales_charge(self.as_("imran"), "C-KONK", taxes=named)
        self.assertEqual(self.codes(sold), ["CGST2.5", "SGST2.5"])
        self.assertEqual(sold["total"], "1050.00")
        _, bought = self.purchase_charge(self.as_("ravi"), "V-WMB", taxes=named)
        self.assertEqual(self.codes(bought), ["CGST2.5", "SGST2.5"])
        self.assertEqual(bought["total"], "1050.00")

    def test_none_named_stays_none(self):
        imran = self.as_("imran")
        order, sold = self.sales_charge(imran, "C-KONK", taxes=[])
        self.assertEqual((sold["taxes"], sold["total"]), ([], "1000.00"))
        self.assertEqual(list(order.lines.get().taxes.all()), [])
        _, bought = self.purchase_charge(self.as_("ravi"), "V-WMB", taxes=[])
        self.assertEqual((bought["taxes"], bought["total"]), ([], "1000.00"))

    def test_a_charge_s_tax_for_the_other_side_is_not_taken(self):
        # One charge serves both directions; a tax kept for purchases only is not put on a sale, as an item's is not.
        sgst = Tax.objects.get(code="SGST9")
        purchase_only = Tax.objects.create(code="PCESS1", name="Purchase cess", rate=D("1"), scope="purchase",
                                           paid_account=sgst.paid_account)
        self.freight.taxes.add(purchase_only)
        _, sold = self.sales_charge(self.as_("imran"), "C-KONK")
        self.assertEqual(self.codes(sold), ["CGST9", "SGST9"])


class AChargeLineBooksToItsChargesAccount(UntaxedLinesTestCase):
    """
    O496: a charge line takes its charge's account for its side on every document, by one rule
    (TaxedLineMixin.take_its_charges_account). The bill line alone never asked: freight typed on a bill booked to
    the company's default purchase expense, not to the charge's "Freight inward", where the same freight on its
    purchase order went there. An account the line names is kept; a charge with no account for the side is refused,
    as an order line's always was; a note giving a line back is booked where that line was.

    Numbers, worked in /home/user/work/calc_g33_charges/o496.py: freight 1,000.00 from a vendor in the state at
    CGST 9% + SGST 9%: Dr Freight inward 1,000.00, Dr input CGST 90.00, Dr input SGST 90.00, Cr the vendor
    1,180.00; its debit note the same, each side the other way. Freight recharged on an invoice in the state:
    Cr Freight recharged 1,000.00, CGST 90.00, SGST 90.00, Dr the customer 1,180.00; its credit note reversed.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from apps.accounting.models import AccountType, ChargeType

        cls.inward = Account.objects.create(code="5191", name="Freight inward", account_type=AccountType.EXPENSE)
        cls.recharged = Account.objects.create(code="4191", name="Freight recharged", account_type=AccountType.INCOME)
        cls.freight = ChargeType.objects.create(code="FRB", name="Freight", hsn_code="9965",
                                                revenue_account=cls.recharged, expense_account=cls.inward)
        cls.freight.taxes.set(Tax.objects.filter(code__in=["CGST9", "SGST9"]))

    def bill(self, client, **line):
        vendor = Party.objects.get(code="V-WMB")  # Thane, in the plant's state
        made = client.post("/api/purchasing/bills/", {
            "vendor": vendor.pk, "bill_date": str(timezone.localdate()), "reference": f"WMB/{Bill.objects.count() + 1}",
            "new_lines": [{"charge": self.freight.pk, "quantity": "1", "unit_price": "1000.00", **line}]}, format="json")
        return made

    def entry(self, document):
        """What the entry booked, by account code: debit positive."""
        rows = {}
        for row in document.journal_entry.lines.all():
            rows[row.account.code] = rows.get(row.account.code, D("0")) + row.debit - row.credit
        return rows

    def test_freight_typed_on_a_bill_books_to_its_charge_and_its_debit_note_back(self):
        ravi = self.as_("ravi")  # AP Manager
        made = self.bill(ravi)
        self.assertEqual(made.status_code, 201, made.content)
        bill = Bill.objects.get(pk=made.json()["id"])
        line = bill.lines.get()
        self.assertEqual(line.expense_account, self.inward)
        posted = ravi.post(f"/api/purchasing/bills/{bill.pk}/post_bill/", format="json")
        self.assertEqual(posted.status_code, 200, posted.content)
        bill.refresh_from_db()
        cgst, sgst = Tax.objects.get(code="CGST9"), Tax.objects.get(code="SGST9")
        payable = bill.payable_account.code
        self.assertNotEqual(cgst.paid_account_id, sgst.paid_account_id)
        self.assertEqual(self.entry(bill), {
            "5191": D("1000.00"), cgst.paid_account.code: D("90.00"), sgst.paid_account.code: D("90.00"),
            payable: D("-1180.00")})

        noted = ravi.post(f"/api/purchasing/bills/{bill.pk}/debit_note/", {"memo": "Freight not owed"}, format="json")
        self.assertEqual(noted.status_code, 200, noted.content)
        note = Bill.objects.get(pk=noted.json()["id"])
        if not note.posted:
            again = ravi.post(f"/api/purchasing/bills/{note.pk}/post_bill/", format="json")
            self.assertEqual(again.status_code, 200, again.content)
            note.refresh_from_db()
        self.assertEqual(note.lines.get().expense_account, self.inward)
        self.assertEqual({code: -amount for code, amount in self.entry(note).items()}, self.entry(bill))

    def test_an_account_the_bill_line_names_is_kept(self):
        from apps.accounting.models import AccountType

        carriage = Account.objects.create(code="5192", name="Carriage", account_type=AccountType.EXPENSE)
        made = self.bill(self.as_("ravi"), expense_account=carriage.pk)
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual(Bill.objects.get(pk=made.json()["id"]).lines.get().expense_account, carriage)

    def test_a_charge_with_no_expense_account_is_refused_on_a_bill(self):
        # As on a purchase order: the add row does not offer it on either (expense_account__isnull=false).
        self.freight.expense_account = None
        self.freight.save()
        made = self.bill(self.as_("ravi"))
        self.assertEqual(made.status_code, 400, made.content)
        self.assertIn("has no expense account configured", str(made.json()))
        self.assertFalse(Bill.objects.filter(vendor__code="V-WMB", reference__startswith="WMB/").exists())

    def test_freight_on_an_invoice_books_to_its_charge_and_its_credit_note_back(self):
        rohit = self.as_("rohit")  # AR Manager
        customer = Party.objects.get(code="C-KONK")
        made = rohit.post("/api/sales/invoices/", {
            "customer": customer.pk, "invoice_date": str(timezone.localdate()),
            "new_lines": [{"charge": self.freight.pk, "quantity": "1", "unit_price": "1000.00"}]}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        invoice = Invoice.objects.get(pk=made.json()["id"])
        self.assertEqual(invoice.lines.get().revenue_account, self.recharged)
        posted = rohit.post(f"/api/sales/invoices/{invoice.pk}/post_invoice/", format="json")
        self.assertEqual(posted.status_code, 200, posted.content)
        invoice.refresh_from_db()
        booked = self.entry(invoice)
        self.assertEqual((booked["4191"], booked[invoice.receivable_account.code]), (D("-1000.00"), D("1180.00")))
        self.assertEqual(sum(booked.values(), D("0")), D("0"))
        noted = rohit.post(f"/api/sales/invoices/{invoice.pk}/credit_note/", {"memo": "Freight waived"}, format="json")
        self.assertEqual(noted.status_code, 200, noted.content)
        note = Invoice.objects.get(pk=noted.json()["id"])
        self.assertEqual(note.lines.get().revenue_account, self.recharged)
        self.assertEqual({code: -amount for code, amount in self.entry(note).items()}, booked)
