"""
October 2026 at a woven-sack plant in West Bengal, through the API, as the
people who do each step.

Every expected figure is worked in calc_month/month.py (the g22 month
agent's scratchpad script), never read off the output. The story:

    1  PP granules from Gujarat, IGST: 10,000 kg ordered at 100.00, received
       6,000 (3 Oct) and 3,800 (8 Oct, the truck short), billed 9,800 for
       9,80,000.00 + 1,76,400.00. Masterbatch from a West Bengal micro
       enterprise, CGST + SGST: 500 kg at 300.00 into inspection, 50 failed
       and sent back, 450 billed for 1,59,300.00 and paid by NEFT on 20 Oct.
       Freight for the polymer from a transporter under 194C, 39,200.00:
       784.00 deducted, landed on the two receipts by value (24,000.00 and
       15,200.00), so the polymer stands at 104.00 a kilo.
    2  5,000 kg of tape from 4,850 kg PP and 150 kg masterbatch: 5,49,400.00,
       109.88 a kilo.
    4  Hooghly Cement: 4,000 kg at 150.00 + GST, 7,08,000.00, an advance of
       1,00,000.00 drawn down, 200 kg returned and credited (35,400.00), a
       cheque of 3,00,000.00 deposited, bounced and voided: 5,72,600.00 owed.
       An export of 1,000 kg at USD 2.00 under LUT, booked at 84.00 and paid
       at 84.50: 1,000.00 exchange gain.
    5  500 kg of sweepings counted in at 30.00, sold at 40.00 + GST, 23,600.00,
       TCS 472.00 collected and paid by challan.

Model calls, where no screen exists (each listed in the report):
  - the master data the month opens with (accounts, items, parties, the
    company), as an import before go-live would leave it;
  - spreading the transporter's freight over the receipts
    (BillLine.allocate_landed_cost: the admin's alone).

Two-factor sign-in (L12): the roles that post payments or keep pay hold a
guarded right. The suite's API tests authenticate with force_authenticate,
which DRF honours without a session, so the Gate never sees them; this file
does the same.
"""

import datetime
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase
from unittest import expectedFailure
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType, JournalLine, PartyTaxProfile, TdsSection
from apps.core.models import (
    Company, Currency, ExchangeRate, Party, PartyRole, PartyRoleAssignment, PaymentTerms, UnitOfMeasure,
    UnitOfMeasureCategory,
)
from apps.inventory.models import Item, Warehouse

D = Decimal


def gstin(prefix14):
    from apps.accounting.gst import gstin_check_character

    return prefix14 + gstin_check_character(prefix14)


def OCT(day):
    return datetime.date(2026, 10, day)


@contextmanager
def on(day, hour=11):
    """The plant's clock at `hour` in Kolkata on `day`: a document dated by the server is dated in the story."""
    moment = datetime.datetime(day.year, day.month, day.day, hour, tzinfo=ZoneInfo("Asia/Kolkata"))
    with patch("django.utils.timezone.now", return_value=moment):
        yield


class Refused(AssertionError):
    pass


class Month:
    """The month, step by step. Each step keeps what the screens answered."""

    def __init__(self):
        call_command("setup_roles", verbosity=0)
        call_command("setup_indian_books", gstin=gstin("19AAACB1234C1Z"), verbosity=0, stdout=_Null())
        self.people = {}
        self.said = {}
        self.refusals = {}
        self.findings = {}

    # -- people ----------------------------------------------------------------

    def as_(self, role):
        if role not in self.people:
            user = User.objects.create_user(role.replace(" ", "_").lower())
            user.groups.add(Group.objects.get(name=role))
            if role == "Sales Rep":
                from apps.sales.tests_base import carries_every_customer

                carries_every_customer(user)
            client = APIClient()
            client.force_authenticate(user)
            self.people[role] = client
        return self.people[role]

    def ok(self, response, status=(200, 201)):
        status = (status,) if isinstance(status, int) else status
        if response.status_code not in status:
            if response.status_code == 500:
                from apps.core.errors import ServerError

                error = ServerError.objects.order_by("-pk").first()
                raise Refused(f"500 {error.path}: {error.kind}: {error.message}\n{error.traceback[-2500:]}")
            raise Refused(f"{response.status_code} {response.request['PATH_INFO']}: {response.content[:600]!r}")
        return response.json() if response["Content-Type"].startswith("application/json") else response

    def post(self, role, url, data=None, status=(200, 201)):
        return self.ok(self.as_(role).post(url, data or {}, format="json"), status)

    def get(self, role, url, params=None):
        return self.ok(self.as_(role).get(url, params or {}))

    def refuse(self, name, role, url, data=None):
        """A refusal, asked before the step it guards; its status is kept for the test that names it."""
        self.refusals[name] = self.as_(role).post(url, data or {}, format="json").status_code

    # -- the books the month opens with (model calls: master data) ------------

    def open_books(self):
        acct = {a.code: a for a in Account.objects.all()}
        A, L, I, X = AccountType.ASSET, AccountType.LIABILITY, AccountType.INCOME, AccountType.EXPENSE
        for code, name, kind in (("1350", "Work in progress", A), ("2150", "Goods received not invoiced", L),
                                 ("2300", "Customer deposits", L), ("5010", "Cost of sales", X),
                                 ("5150", "Purchase price variance", X), ("4900", "Exchange gain", I),
                                 ("5910", "Exchange loss", X), ("4800", "Stock gains", I),
                                 ("5020", "Material variance", X), ("5030", "Scrap", X),
                                 ("5040", "Conversion absorbed", X), ("5050", "Conversion variance", X),
                                 ("1900", "Suspense", A), ("5200", "Bad debts", X),
                                 ("5300", "Settlement discounts", X)):
            acct[code] = Account.objects.create(code=code, name=name, account_type=kind)
        self.acct = acct
        self.inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        self.usd = Currency.objects.create(code="USD", name="US Dollar")
        ExchangeRate.objects.create(currency=self.usd, rate=D("84.00"), valid_from=OCT(1))
        ExchangeRate.objects.create(currency=self.usd, rate=D("84.50"), valid_from=OCT(30))
        Company.objects.create(
            name="Bengal Polysacks", base_currency=self.inr,
            default_inventory_account=acct["1300"], default_cogs_account=acct["5010"],
            grni_account=acct["2150"], customer_deposit_account=acct["2300"],
            purchase_price_variance_account=acct["5150"], fx_gain_account=acct["4900"],
            fx_loss_account=acct["5910"], settlement_discount_account=acct["5300"],
            bad_debt_account=acct["5200"])
        self.terms = PaymentTerms.objects.create(code="N30", name="Net 30", net_days=30)
        self.kg = UnitOfMeasure.objects.create(code="kg", name="Kilogram", category=UnitOfMeasureCategory.WEIGHT)
        self.qc = Warehouse.objects.create(code="QC", name="Held for inspection", is_quarantine=True)
        self.store = Warehouse.objects.create(code="STORE", name="Stores")
        self.pp = Item.objects.create(sku="PP-H030", name="PP granules H030SG", uom=self.kg, hsn_code="390210")
        self.mb = Item.objects.create(sku="MB-WHT", name="White masterbatch", uom=self.kg, hsn_code="320611")
        self.tape = Item.objects.create(sku="TAPE-90", name="PP tape 900 denier", uom=self.kg, hsn_code="540720")
        self.sweep = Item.objects.create(sku="SWEEP", name="Floor sweepings", uom=self.kg, hsn_code="391510")
        from apps.manufacturing.bom import BillOfMaterials, BomComponent
        from apps.manufacturing.orders import ManufacturingSettings

        ManufacturingSettings.objects.create(
            wip_account=acct["1350"], variance_account=acct["5020"], scrap_account=acct["5030"],
            conversion_absorbed_account=acct["5040"], conversion_variance_account=acct["5050"])
        self.tape_bom = BillOfMaterials.objects.create(item=self.tape, quantity_produced=D("1000"), uom=self.kg)
        for number, (item, quantity) in enumerate(((self.pp, "970"), (self.mb, "30")), start=1):
            BomComponent.objects.create(bom=self.tape_bom, item=item, quantity=D(quantity), uom=self.kg,
                                        line_number=number)
        self.contract = TdsSection.objects.create(
            code="194C", name="Contractors", mode="whole", single_threshold=D("30000"),
            annual_threshold=D("100000"), payable_account=acct["2250"])
        self.pp_vendor = self.party("V-PP", "Gujarat Polymers", PartyRole.VENDOR, gstin=gstin("24AAACG1111A1Z"),
                                    pan="AAACG1111A")
        self.mb_vendor = self.party("V-MB", "Howrah Colours", PartyRole.VENDOR, gstin=gstin("19AAACM2222B1Z"),
                                    pan="AAACM2222B", msme_category="micro", udyam_number="UDYAM-WB-10-0012345")
        self.transporter = self.party("V-TRN", "Durgapur Roadways", PartyRole.VENDOR, gstin=gstin("19AAACT3333C1Z"),
                                      pan="AAACT3333C", tds_section=self.contract)
        from apps.accounting.gst import GstSettings
        from apps.accounting.models import FiscalPosition
        from apps.core.models import Address, AddressType

        GstSettings.objects.update(einvoicing_from=datetime.date(2026, 4, 1))
        Company.objects.update(address=Address.objects.create(
            line1="Plot 7, Bamunara Industrial Estate", city="Durgapur", state="West Bengal", postal_code="713212"))
        self.hooghly = self.party("C-HC", "Hooghly Cement Works", PartyRole.CUSTOMER, gstin=gstin("19AAACH4444D1Z"),
                                  pan="AAACH4444D")
        from apps.accounting.models import TcsSection
        from apps.inventory.models import AdjustmentReason

        self.scrap_section = TcsSection.objects.get(code="SCRAP")
        Item.objects.filter(pk=self.sweep.pk).update(tcs_section=self.scrap_section)
        self.found = AdjustmentReason.objects.create(code="FOUND", name="Counted in", account=acct["4800"])
        self.recycler = self.party("C-RCY", "Asansol Polymer Recyclers", PartyRole.CUSTOMER,
                                   gstin=gstin("19AAACR5555E1Z"), pan="AAACR5555E")
        Address.objects.create(party=self.recycler, address_type=AddressType.BILLING, line1="GT Road, Burnpur",
                               city="Asansol", state="West Bengal", postal_code="713325", is_primary=True)
        Address.objects.create(party=self.hooghly, address_type=AddressType.BILLING, line1="NH-6, Uluberia",
                               city="Howrah", state="West Bengal", postal_code="711316", is_primary=True)
        self.importer = self.party("C-EXP", "Gulf Coast Sacks LLC", PartyRole.CUSTOMER, currency=self.usd,
                                   fiscal_position=FiscalPosition.objects.get(code="EXPORT-LUT"), gst_registration="overseas")

    def party(self, code, name, role, currency=None, **tax):
        party = Party.objects.create(code=code, name=name, default_currency=currency or self.inr,
                                     payment_terms=self.terms)
        PartyRoleAssignment.objects.create(party=party, role=role)
        PartyTaxProfile.objects.create(party=party, **tax)
        return party

    # -- the rates the accountant confirms ------------------------------------

    def confirm_rates(self):
        made = self.post("Controller", "/api/accounting/tds-rates/", {
            "section": self.contract.pk, "applies_from": "2026-04-01", "rate_percent": "2",
            "no_pan_rate_percent": "20", "authority": "s.393 Income-tax Act 2025"})
        url = f"/api/accounting/tds-rates/{made['id']}/confirm/"
        read = {k: made[k] for k in ("section", "applies_from", "rate_percent", "no_pan_rate_percent")}
        self.refuse("R3 a TDS rate confirmed by the AP Manager", "AP Manager", url, read)
        self.post("Controller", url, read)

    # -- 1. polymer bought -----------------------------------------------------

    def tax(self, code):
        from apps.accounting.models import Tax

        return Tax.objects.get(code=code).pk

    def order(self, vendor, day, item, quantity, price):
        po = self.post("Purchasing Clerk", "/api/purchasing/purchase-orders/", {
            "vendor": vendor.pk, "order_date": str(day), "currency": self.inr.pk})
        self.post("Purchasing Clerk", "/api/purchasing/purchase-order-lines/", {
            "order": po["id"], "item": item.pk, "uom": self.kg.pk, "quantity": quantity, "unit_price": price,
            "taxes": [self.tax("CGST9"), self.tax("SGST9")]})
        return self.post("Purchasing Clerk", f"/api/purchasing/purchase-orders/{po['id']}/confirm/")

    def receive(self, po, day, quantity, reference):
        receipt = self.post("Warehouse Staff", "/api/purchasing/goods-receipts/", {
            "purchase_order": po["id"], "receipt_date": str(day), "reference": reference})
        self.post("Warehouse Staff", "/api/purchasing/goods-receipt-lines/", {
            "receipt": receipt["id"], "order_line": po["lines"][0]["id"], "warehouse": self.qc.pk,
            "quantity_received": quantity})
        return self.post("Warehouse Staff", f"/api/purchasing/goods-receipts/{receipt['id']}/post_receipt/")

    def bill(self, po, day, reference):
        bill = self.post("Purchasing Clerk", f"/api/purchasing/purchase-orders/{po['id']}/create_bill/", {
            "payable_account": self.acct["2000"].pk, "bill_date": str(day), "reference": reference})
        url = f"/api/purchasing/bills/{bill['id']}/post_bill/"
        if "R1 a bill posted by the Purchasing Clerk" not in self.refusals:
            self.refuse("R1 a bill posted by the Purchasing Clerk", "Purchasing Clerk", url)
        return self.post("AP Manager", url)

    def buy_polymer(self):
        self.pp_po = self.order(self.pp_vendor, OCT(1), self.pp, "10000", "100.00")
        self.pp_r1 = self.receive(self.pp_po, OCT(3), "6000", "GP/DC/7781")
        self.pp_r2 = self.receive(self.pp_po, OCT(8), "3800", "GP/DC/7790")
        for receipt in (self.pp_r1, self.pp_r2):
            self.post("Quality Inspector", f"/api/purchasing/goods-receipts/{receipt['id']}/accept/",
                      {"warehouse": self.store.pk})
        self.pp_bill = self.bill(self.pp_po, OCT(9), "GP/2627/1450")

    def buy_masterbatch(self):
        self.mb_po = self.order(self.mb_vendor, OCT(1), self.mb, "500", "300.00")
        self.mb_r = self.receive(self.mb_po, OCT(4), "500", "HC/DC/310")
        url = f"/api/purchasing/goods-receipts/{self.mb_r['id']}/"
        line = self.mb_r["lines"][0]["id"]
        self.refuse("R4 goods rejected with no reason", "Quality Inspector", url + "reject/",
                    {"quantities": {str(line): "50"}})
        self.mb_reject = self.post("Quality Inspector", url + "reject/", {
            "quantities": {str(line): "50"}, "note": "Shade off the approved panel"})
        self.post("Quality Inspector", url + "accept/", {"warehouse": self.store.pk})
        self.mb_bill = self.bill(self.mb_po, OCT(6), "HC/26-27/0412")

    def freight(self):
        made = self.post("Controller", "/api/accounting/charge-types/", {
            "code": "FRT-IN", "name": "Freight inward", "hsn_code": "9965", "capitalise_into_inventory": True,
            "expense_account": self.acct["5100"].pk, "revenue_account": self.acct["4000"].pk, "taxes": []})
        bill = self.post("Purchasing Clerk", "/api/purchasing/bills/", {
            "vendor": self.transporter.pk, "bill_date": str(OCT(9)), "reference": "DR/1187",
            "payable_account": self.acct["2000"].pk, "currency": self.inr.pk})
        self.post("Purchasing Clerk", "/api/purchasing/bill-lines/", {
            "bill": bill["id"], "charge": made["id"], "description": "Freight, two trucks of PP, Vapi to Durgapur",
            "quantity": "1", "unit_price": "39200.00", "expense_account": self.acct["5100"].pk})
        self.post("AP Manager", f"/api/purchasing/bills/{bill['id']}/post_bill/")
        url = f"/api/purchasing/bills/{bill['id']}/deduct_tds/"
        self.refuse("R2 TDS asked by the Purchasing Clerk", "Purchasing Clerk", url)
        self.tds = self.post("AP Manager", url)
        # No screen spreads a separate bill's freight over receipts: the admin's alone.
        from apps.purchasing.models import BillLine, GoodsReceiptLine

        charge = BillLine.objects.get(bill_id=bill["id"])
        charge.allocate_landed_cost(GoodsReceiptLine.objects.filter(
            receipt_id__in=(self.pp_r1["id"], self.pp_r2["id"])).order_by("pk"), on_date=OCT(9))
        self.freight_bill = self.get("AP Manager", f"/api/purchasing/bills/{bill['id']}/")

    def pay_masterbatch(self):
        payment = self.post("AP Manager", "/api/accounting/payments/", {
            "party": self.mb_vendor.pk, "direction": "disbursement", "payment_date": str(OCT(20)),
            "amount": "159300.00", "currency": self.inr.pk, "bank_account": self.acct["1200"].pk,
            "counterpart_account": self.acct["2000"].pk, "method": "neft", "reference": "UTR SBIN226293001"})
        self.post("AP Manager", f"/api/accounting/payments/{payment['id']}/post_payment/")
        self.post("AP Manager", "/api/purchasing/bill-payments/", {
            "bill": self.mb_bill["id"], "payment": payment["id"], "amount": "159300.00"})
        self.statement_line(OCT(20), "NEFT HOWRAH COLOURS SBIN226293001", "-159300.00", payment)
        self.mb_bill = self.get("AP Manager", f"/api/purchasing/bills/{self.mb_bill['id']}/")
        self.msme = self.get("AP Manager", "/api/purchasing/purchasing-reports/msme/",
                             {"start": "2026-10-01", "end": "2026-10-31", "as_of": "2026-10-31"})

    # -- 2. tape --------------------------------------------------------------

    def make_tape(self):
        sup = "Production Supervisor"
        run = self.post(sup, "/api/manufacturing/work-orders/", {
            "item": self.tape.pk, "bom": self.tape_bom.pk, "quantity_ordered": "5000", "uom": self.kg.pk,
            "warehouse": self.store.pk, "scheduled_end": str(OCT(12))})
        base = f"/api/manufacturing/work-orders/{run['id']}/"
        self.post(sup, base + "release/", {"date": str(OCT(11))})
        issue = self.post(sup, "/api/manufacturing/material-issues/", {
            "work_order": run["id"], "issue_date": str(OCT(11)), "warehouse": self.store.pk})
        for number, (item, quantity) in enumerate(((self.pp, "4850"), (self.mb, "150")), start=1):
            self.post(sup, "/api/manufacturing/material-issue-lines/", {
                "issue": issue["id"], "item": item.pk, "quantity": quantity, "uom": self.kg.pk,
                "line_number": number})
        self.post(sup, f"/api/manufacturing/material-issues/{issue['id']}/post/")
        entry = self.post(sup, "/api/manufacturing/production-entries/", {
            "work_order": run["id"], "entry_date": str(OCT(12)), "warehouse": self.store.pk,
            "quantity_produced": "5000", "uom": self.kg.pk})
        self.post(sup, f"/api/manufacturing/production-entries/{entry['id']}/post/")
        self.tape_run = self.post(sup, base + "close/", {"date": str(OCT(12))})

    # -- 4. selling --------------------------------------------------------------

    def sell(self, customer, day, quantity, price, currency, taxes=("CGST9", "SGST9")):
        rep = "Sales Rep"
        so = self.post(rep, "/api/sales/sales-orders/", {
            "customer": customer.pk, "order_date": str(day), "currency": currency.pk})
        self.post(rep, "/api/sales/sales-order-lines/", {
            "order": so["id"], "item": self.tape.pk, "uom": self.kg.pk, "quantity": quantity, "unit_price": price,
            "revenue_account": self.acct["4000"].pk, "taxes": [self.tax(code) for code in taxes]})
        return self.post(rep, f"/api/sales/sales-orders/{so['id']}/confirm/")

    def ship(self, so, day, quantity):
        store = "Warehouse Staff"
        delivery = self.post(store, "/api/sales/deliveries/", {"sales_order": so["id"], "delivery_date": str(day)})
        self.post(store, "/api/sales/delivery-lines/", {
            "delivery": delivery["id"], "order_line": so["lines"][0]["id"], "warehouse": self.store.pk,
            "quantity_shipped": quantity})
        return self.post(store, f"/api/sales/deliveries/{delivery['id']}/post_delivery/")

    def invoice(self, so, day):
        ar = "AR Manager"
        draft = self.post(ar, f"/api/sales/sales-orders/{so['id']}/create_invoice/", {
            "receivable_account": self.acct["1100"].pk, "invoice_date": str(day)})
        url = f"/api/sales/invoices/{draft['id']}/post_invoice/"
        if "R5 an invoice posted by the Sales Rep" not in self.refusals:
            self.refuse("R5 an invoice posted by the Sales Rep", "Sales Rep", url)
        return self.post(ar, url)

    def receive_money(self, party, day, amount, currency, method, reference, invoice=None, allocate=None):
        ar = "AR Manager"
        payment = self.post(ar, "/api/accounting/payments/", {
            "party": party.pk, "direction": "receipt", "payment_date": str(day), "amount": amount,
            "currency": currency.pk, "bank_account": self.acct["1200"].pk,
            "counterpart_account": self.acct["1100"].pk, "method": method, "reference": reference})
        self.post(ar, f"/api/accounting/payments/{payment['id']}/post_payment/")
        if invoice is not None:
            self.post(ar, "/api/sales/invoice-payments/", {
                "invoice": invoice["id"], "payment": payment["id"], "amount": allocate or amount})
        return payment

    def statement_line(self, day, words, amount, payment=None):
        line = self.post("Bookkeeper", "/api/accounting/bank-statement-lines/", {
            "statement": self.statement["id"], "date": str(day), "description": words, "amount": amount})
        if payment is not None:
            self.post("Bookkeeper", f"/api/accounting/bank-statements/{self.statement['id']}/match/",
                      {"line": line["id"], "payment": payment["id"]})
        return line

    def gst_documents(self, invoice, vehicle):
        from apps.gst.tests_einvoice import IRN, signed_qr

        officer = "GST Officer"
        record = self.post(officer, "/api/gst/e-invoices/", {"invoice": invoice["id"]})
        self.post(officer, f"/api/gst/e-invoices/{record['id']}/record/", {
            "irn": IRN[:-len(str(invoice["id"]))] + str(invoice["id"]), "ack_number": f"11261000000000{invoice['id']}",
            "ack_date": f"{invoice['invoice_date']}T11:30",
            "signed_qr": signed_qr(Irn=IRN[:-len(str(invoice["id"]))] + str(invoice["id"]), DocNo=invoice["number"],
                                   SellerGstin=gstin("19AAACB1234C1Z"),
                                   BuyerGstin=record["payload"]["BuyerDtls"]["Gstin"],
                                   TotInvVal=float(invoice["total"]))})
        eway = self.post(officer, "/api/gst/eway-bills/", {"invoice": invoice["id"], "vehicle_number": vehicle})
        self.post(officer, f"/api/gst/eway-bills/{eway['id']}/record/", {
            "number": f"3310000000{invoice['id']:02d}", "generated_at": f"{invoice['invoice_date']}T10:00:00+05:30"})

    def sell_to_hooghly(self):
        with on(OCT(10)):
            self.dom_so = self.sell(self.hooghly, OCT(10), "4000", "150.00", self.inr)
            self.advance = self.post("AR Manager", f"/api/sales/sales-orders/{self.dom_so['id']}/down_payment/", {
                "receivable_account": self.acct["1100"].pk, "invoice_date": str(OCT(10)), "amount": "100000"})
            self.advance = self.post("AR Manager", f"/api/sales/invoices/{self.advance['id']}/post_invoice/")
            self.advance_paid = self.receive_money(self.hooghly, OCT(10), "100000.00", self.inr, "neft",
                                                   "UTR HDFC226283310", self.advance)
            self.statement_line(OCT(10), "NEFT HOOGHLY CEMENT ADVANCE", "100000.00", self.advance_paid)
        with on(OCT(15)):
            self.dom_delivery = self.ship(self.dom_so, OCT(15), "4000")
            self.dom_invoice = self.invoice(self.dom_so, OCT(15))
            self.gst_documents(self.dom_invoice, "WB23D4521")
        with on(OCT(22)):
            line = self.dom_delivery["lines"][0]["id"]
            url = f"/api/sales/deliveries/{self.dom_delivery['id']}/customer_return/"
            first = self.as_("Warehouse Staff").post(url, {"quantities": {str(line): "200"}}, format="json")
            self.findings["a return credits an invoice that drew an advance"] = first.status_code
            if first.status_code == 201 or first.status_code == 200:
                self.returned = first.json()
                [note] = self.returned["credit_notes"]
            else:
                # The way round while it fails: the goods back uncredited, then the note from the invoice.
                self.returned = self.post("Warehouse Staff", url, {"quantities": {str(line): "200"},
                                                                   "credit_invoices": False})
                invoice_line = self.dom_invoice["lines"][0]["id"]
                note = self.post("AR Manager", f"/api/sales/invoices/{self.dom_invoice['id']}/credit_note/",
                                 {"quantities": {str(invoice_line): "200"}})
            self.credit_note = self.get("AR Manager", f"/api/sales/invoices/{note['id']}/")
        with on(OCT(25)):
            self.cheque = self.receive_money(self.hooghly, OCT(25), "300000.00", self.inr, "cheque",
                                             "Chq 004512 UCO Bank", self.dom_invoice)
        with on(OCT(26)):
            self.statement_line(OCT(26), "CLG CHQ 004512 HOOGHLY CEMENT", "300000.00", self.cheque)
        with on(OCT(28)):
            url = f"/api/accounting/payments/{self.cheque['id']}/void/"
            self.refuse("R6 a payment voided by the Sales Rep", "Sales Rep", url, {"memo": "Returned unpaid"})
            self.post("AR Manager", url, {"memo": "Returned unpaid: funds insufficient", "date": str(OCT(28))})
            self.statement_line(OCT(28), "CHQ RTN 004512 FUNDS INSUFF", "-300000.00", self.cheque)
        self.dom_invoice = self.get("AR Manager", f"/api/sales/invoices/{self.dom_invoice['id']}/")

    def export(self):
        from apps.accounting.gst import GstSettings

        with on(OCT(1)):
            self.post("GST Officer", "/api/accounting/letters-of-undertaking/", {
                "registration": GstSettings.objects.get().pk, "arn": "AD190326000123X", "financial_year": "2026-27",
                "filed_on": "2026-03-20", "valid_from": "2026-04-01", "valid_to": "2027-03-31"})
        with on(OCT(16)):
            self.exp_so = self.sell(self.importer, OCT(16), "1000", "2.00", self.usd)
            self.exp_delivery = self.ship(self.exp_so, OCT(16), "1000")
            self.exp_invoice = self.invoice(self.exp_so, OCT(16))
        with on(OCT(30)):
            self.exp_paid = self.receive_money(self.importer, OCT(30), "2000.00", self.usd, "swift",
                                               "SWIFT MT103 BOFAUS3N 7781", self.exp_invoice)
            self.statement_line(OCT(30), "INWARD REMIT USD 2000 GULF COAST SACKS", "169000.00", self.exp_paid)
        self.exp_invoice = self.get("AR Manager", f"/api/sales/invoices/{self.exp_invoice['id']}/")

    # -- 5. scrap ------------------------------------------------------------------

    def sell_scrap(self):
        with on(OCT(24)):
            count = self.post("Stores Manager", "/api/inventory/stock-adjustments/", {
                "adjustment_date": str(OCT(24)), "warehouse": self.store.pk, "reason": self.found.pk,
                "memo": "Sweepings baled at the month's floor count"})
            line = self.post("Stores Manager", "/api/inventory/stock-adjustment-lines/", {
                "adjustment": count["id"], "item": self.sweep.pk, "uom": self.kg.pk, "quantity": "500",
                "unit_cost": "30.00"})
            self.findings["a count-in line keeps the cost it was given"] = line["unit_cost"]
            if line["unit_cost"] is None:
                # No screen sets it (the serializer reads it only); posting asks for it. A model call.
                from apps.inventory.models import StockAdjustmentLine

                StockAdjustmentLine.objects.filter(pk=line["id"]).update(unit_cost=D("30.00"))
            self.post("Stores Manager", f"/api/inventory/stock-adjustments/{count['id']}/post/")
            rep = "Sales Rep"
            so = self.post(rep, "/api/sales/sales-orders/", {
                "customer": self.recycler.pk, "order_date": str(OCT(24)), "currency": self.inr.pk})
            self.post(rep, "/api/sales/sales-order-lines/", {
                "order": so["id"], "item": self.sweep.pk, "uom": self.kg.pk, "quantity": "500", "unit_price": "40.00",
                "revenue_account": self.acct["4100"].pk, "taxes": [self.tax("CGST9"), self.tax("SGST9")]})
            so = self.post(rep, f"/api/sales/sales-orders/{so['id']}/confirm/")
            self.ship_scrap = self.ship(so, OCT(24), "500")
            draft = self.post("AR Manager", f"/api/sales/sales-orders/{so['id']}/create_invoice/", {
                "receivable_account": self.acct["1100"].pk, "invoice_date": str(OCT(24))})
            url = f"/api/sales/invoices/{draft['id']}/post_invoice/"
            self.refusals["R7 scrap invoiced before the TCS rate is confirmed"] = self.as_("AR Manager").post(
                url, {}, format="json").status_code
            [rate] = [row for row in self.get("Controller", "/api/accounting/tcs-rates/",
                                              {"section": self.scrap_section.pk})
                      if row["applies_from"] == "2026-04-01"]
            read = {k: rate[k] for k in ("section", "applies_from", "rate_percent", "no_pan_rate_percent",
                                         "law_section")}
            self.post("Controller", f"/api/accounting/tcs-rates/{rate['id']}/confirm/", read)
            self.scrap_invoice = self.post("AR Manager", url)
            self.gst_documents(self.scrap_invoice, "WB39B7704")
        with on(OCT(29)):
            self.scrap_paid = self.receive_money(self.recycler, OCT(29), self.scrap_invoice["total"], self.inr,
                                                 "neft", "UTR AXIS226302114", self.scrap_invoice)
            self.statement_line(OCT(29), "NEFT ASANSOL POLYMER RECYCLERS", self.scrap_invoice["total"],
                                self.scrap_paid)
        with on(OCT(31)):
            self.tcs_challan = self.post("Controller", "/api/sales/tcs-challans/pay/", {
                "section": self.scrap_section.pk, "month": "2026-10-01", "date": str(OCT(31)),
                "bank_account": self.acct["1200"].pk, "challan_number": "02211", "bsr_code": "0002390"})
            line = self.statement_line(OCT(31), "OLTAS TCS CHALLAN 02211", "-472.00")
            self.post("Bookkeeper", f"/api/accounting/bank-statements/{self.statement['id']}/match/",
                      {"line": line["id"], "entry": self.tcs_challan["journal_entry"]})
        self.scrap_invoice = self.get("AR Manager", f"/api/sales/invoices/{self.scrap_invoice['id']}/")

    def run(self):
        with on(OCT(1)):
            self.open_books()
            self.confirm_rates()
        with on(OCT(3)):
            self.buy_polymer()
        with on(OCT(4)):
            self.buy_masterbatch()
        with on(OCT(9)):
            self.freight()
        with on(OCT(1)):
            self.statement = self.post("Bookkeeper", "/api/accounting/bank-statements/", {
                "bank_account": self.acct["1200"].pk, "reference": "SBI OCT 2026", "start_date": "2026-10-01",
                "end_date": "2026-10-31", "opening_balance": "0", "closing_balance": "133300.00"})
        with on(OCT(20)):
            self.pay_masterbatch()
        with on(OCT(11)):
            self.make_tape()
        self.sell_to_hooghly()
        self.export()
        self.sell_scrap()
        return self


class _Null:
    def write(self, *args, **kwargs):
        pass

    def flush(self):
        pass


def balance(account):
    rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(d=Sum("debit"), c=Sum("credit"))
    return (rows["d"] or D("0")) - (rows["c"] or D("0"))


class PlantMonthTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.m = Month().run()

    def bal(self, code):
        return balance(self.m.acct[code])

    # -- refusals, asked before the step each guards --------------------------

    def test_each_hand_does_only_its_own_part(self):
        self.assertEqual(self.m.refusals, {
            "R1 a bill posted by the Purchasing Clerk": 403,
            "R2 TDS asked by the Purchasing Clerk": 403,
            "R3 a TDS rate confirmed by the AP Manager": 403,
            "R4 goods rejected with no reason": 400,
            "R5 an invoice posted by the Sales Rep": 403,
            "R6 a payment voided by the Sales Rep": 403,
            "R7 scrap invoiced before the TCS rate is confirmed": 400,
        })

    # -- 1. polymer bought ------------------------------------------------------

    def test_the_bills_read_as_matched(self):
        self.assertEqual((self.m.pp_bill["total"], self.m.mb_bill["total"]), ("1156400.00", "159300.00"))

    def test_the_masterbatch_bill_is_paid_and_not_late(self):
        self.assertEqual((self.m.mb_bill["amount_due"], self.m.mb_bill["settlement_status"]), ("0.00", "paid"))

    def test_the_micro_vendor_was_paid_inside_the_acts_days(self):
        [row] = self.m.msme
        self.assertEqual((row["due"], row["paid_on"], row["days_late"], row["unpaid"]),
                         ("2026-11-05", "2026-10-20", 0, "0.00"))

    def test_grni_clears(self):
        self.assertEqual(self.bal("2150"), D("0"))

    def test_tds_deducted_on_the_freight(self):
        self.assertEqual(self.bal("2250"), D("-784.00"))
        self.assertEqual(self.m.freight_bill["amount_due"], "38416.00")

    def test_the_polymer_stands_at_its_landed_cost(self):
        # 4,950 kg left of 9,800 at (9,80,000 + 39,200) / 9,800 = 104.00.
        self.assertEqual((self.m.pp.on_hand_at(self.m.store), self.m.pp.stock_value_at(self.m.store)),
                         (D("4950"), D("514800.00")))

    def test_nothing_is_left_held_for_inspection(self):
        self.assertEqual([(item.sku, item.on_hand_at(self.m.qc)) for item in (self.m.pp, self.m.mb)
                          if item.on_hand_at(self.m.qc)], [])

    # -- 2. tape -------------------------------------------------------------------

    def test_the_run_closed_with_nothing_in_work_in_progress(self):
        self.assertEqual((self.m.tape_run["status"], self.bal("1350"), self.bal("5020")),
                         ("closed", D("0"), D("0")))

    # -- 4. selling ---------------------------------------------------------------

    def test_the_advance_is_drawn_down_and_the_deposits_account_clears(self):
        self.assertEqual((self.m.advance["total"], self.bal("2300")), ("100000.00", D("0")))

    def test_the_bounced_cheque_leaves_the_invoice_owed_and_the_bank_where_it_was(self):
        # 7,08,000 less the 1,00,000 advance and the 35,400 credit; the 3,00,000 cheque voided.
        self.assertEqual((self.m.dom_invoice["total"], self.m.dom_invoice["amount_due"]),
                         ("708000.00", "572600.00"))
        self.assertEqual(self.m.credit_note["total"], "35400.00")

    def test_the_export_is_paid_and_its_exchange_difference_booked(self):
        self.assertEqual((self.m.exp_invoice["total"], self.m.exp_invoice["amount_due"]), ("2000.00", "0.00"))
        self.assertEqual((self.bal("4900"), self.bal("5910"), self.bal("2203")), (D("-1000.00"), D("0"), D("0")))

    @expectedFailure
    def test_a_return_credits_an_invoice_that_drew_an_advance(self):
        """
        Register row (this report): 200 kg returned on the delivery whose invoice drew down a 1,00,000.00
        advance. create_return holds the invoices it credits, not the deposits each draws down, which
        Invoice.locked_before_it names; the credit note takes the deposit after the returned stock.
        """
        self.assertEqual(self.m.findings["a return credits an invoice that drew an advance"], 201)

    # -- 5. scrap --------------------------------------------------------------------

    def test_tcs_collected_on_the_scrap_and_paid_over(self):
        # 23,600.00 with GST, 2% = 472.00, collected with the invoice and paid by challan on 31 Oct.
        self.assertEqual((self.m.scrap_invoice["total"], self.m.scrap_invoice["amount_due"]), ("24072.00", "0.00"))
        self.assertEqual((self.m.tcs_challan["amount"], self.bal("2260")), ("472.00", D("0")))

    @expectedFailure
    def test_sweepings_counted_in_keep_the_cost_they_were_given(self):
        """
        Register row (this report): 500 kg of sweepings, never stocked, counted in at 30.00. The line's
        unit_cost is read-only over the API, and posting refuses a gain with no cost to take.
        """
        given = self.m.findings["a count-in line keeps the cost it was given"]
        self.assertIsNotNone(given)
        self.assertEqual(D(given), D("30.00"))

    # -- what must be true at the end ------------------------------------------------

    def test_the_control_accounts_stand_where_the_month_leaves_them(self):
        expected = {
            "2150": D("0"),  # GRNI: received and billed
            "2300": D("0"),  # customer deposits: taken and drawn down
            "2250": D("-784.00"),  # TDS on the freight, due 7 November
            "2260": D("0"),  # TCS on the scrap, paid
            "2201": D("-53100.00"), "2202": D("-53100.00"), "2203": D("0"),  # GST output
            "1311": D("12150.00"), "1312": D("12150.00"), "1313": D("176400.00"),  # GST input
            "4900": D("-1000.00"), "5910": D("0"),  # exchange
            "1900": D("0"),  # suspense
            "1350": D("0"),  # work in progress
        }
        self.assertEqual({code: self.bal(code) for code in expected}, expected)

    def test_the_shelf_foots_to_the_inventory_account_item_by_item(self):
        shelf = {item.sku: (item.on_hand_at(self.m.store), item.stock_value_at(self.m.store))
                 for item in (self.m.pp, self.m.mb, self.m.tape, self.m.sweep)}
        self.assertEqual(shelf, {"PP-H030": (D("4950"), D("514800.00")), "MB-WHT": (D("300"), D("90000.00")),
                                 "TAPE-90": (D("200"), D("21976.00")), "SWEEP": (D("0"), D("0.00"))})
        self.assertEqual(self.bal("1300"), sum(value for _q, value in shelf.values()))
        self.assertEqual(self.bal("1300"), D("626776.00"))

    def test_receivables_and_payables_foot_by_party(self):
        from apps.purchasing.models import Bill
        from apps.sales.models import Invoice

        owed = {}
        for invoice in Invoice.objects.filter(posted=True):
            owed[invoice.customer.code] = owed.get(invoice.customer.code, D("0")) + invoice.amount_due_base() \
                if hasattr(invoice, "amount_due_base") else owed.get(invoice.customer.code, D("0")) + \
                invoice.amount_due()
        self.assertEqual({k: v for k, v in owed.items() if v}, {"C-HC": D("572600.00")})
        self.assertEqual(self.bal("1100"), D("572600.00"))
        due = {}
        for bill in Bill.objects.filter(posted=True):
            due[bill.vendor.code] = due.get(bill.vendor.code, D("0")) + bill.amount_due()
        self.assertEqual({k: v for k, v in due.items() if v}, {"V-PP": D("1156400.00"), "V-TRN": D("38416.00")})
        self.assertEqual(self.bal("2000"), -sum(due.values()))

    def test_the_bank_and_its_statement_agree(self):
        self.assertEqual(self.bal("1200"), D("133300.00"))
        report = self.m.get("Bookkeeper", f"/api/accounting/bank-statements/{self.m.statement['id']}/reconciliation/")
        self.m.reconciliation = report
        self.assertEqual(D(str(report.get("difference", report.get("unexplained", "0")))), D("0"), report)

    def test_the_trial_balance_balances_and_the_profit_is_the_equity_it_made(self):
        totals = JournalLine.objects.filter(entry__posted=True).aggregate(d=Sum("debit"), c=Sum("credit"))
        self.assertEqual(totals["d"], totals["c"])
        kinds = lambda *types: sum((balance(a) for a in Account.objects.filter(account_type__in=types)), D("0"))
        profit = -kinds(AccountType.INCOME, AccountType.EXPENSE)
        self.assertEqual(profit, D("231576.00"))
        # No capital came in: what the month made is what the balance sheet gained.
        self.assertEqual(kinds(AccountType.ASSET) + kinds(AccountType.LIABILITY, AccountType.EQUITY), profit)


