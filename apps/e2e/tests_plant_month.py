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

    7  A loom billed 1 Oct, 6,00,000.00 + GST, capitalised and depreciated
       5,000.00 for October; GSTR-1, GSTR-3B and the TDS and TCS returns
       read; the month closed. Profit 1,53,363.00.

    6  People: two monthly staff (one past 58: no EPS), a leaver on 21 Oct
       with an advance part recovered on the final slip, and a day-rated
       shift worker with three days absent and eight hours' overtime, his
       advance recovered; PF, ESI, West Bengal PT (LWF is June's and
       December's); the bank file paid and one transfer returned.

The floor (stations, the scale bridge, piece work, the quality hold, the
recall, job work, the re-batch) is PlantFloorTests, below, on its own books.

Model calls, where no screen exists:
  - the master data the month opens with (accounts, items, parties, tax
    profiles, the company, the BOM, manufacturing settings, an adjustment
    reason, an asset category), as an import before go-live would leave it;
  - the books' opening (BooksOpening): no screen keeps it (O251);
  - GST settings' e-invoicing start day;
  - spreading the transporter's freight over the receipts
    (BillLine.allocate_landed_cost: the admin's alone);
  - the sweepings' unit cost on the count-in line, while the API cannot
    give it (test_sweepings_counted_in_keep_the_cost_they_were_given).

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

from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounting.models import (
    Account,
    AccountType,
    JournalLine,
    PartyTaxProfile,
    TdsSection,
)
from apps.core.models import (
    Company,
    Currency,
    ExchangeRate,
    Party,
    PartyRole,
    PartyRoleAssignment,
    PaymentTerms,
    UnitOfMeasure,
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
                                 ("5300", "Settlement discounts", X),
                                 ("2400", "Net pay payable", L), ("2410", "PF payable", L), ("2420", "ESI payable", L),
                                 ("2430", "PT payable", L), ("2440", "LWF payable", L), ("1400", "Salary advances", A),
                                 ("6000", "Wages", X), ("6100", "Employer contributions", X)):
            acct[code] = Account.objects.create(code=code, name=name, account_type=kind)
        self.acct = acct
        self.inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        self.usd = Currency.objects.create(code="USD", name="US Dollar")
        ExchangeRate.objects.create(currency=self.usd, rate=D("84.00"), valid_from=OCT(1))
        ExchangeRate.objects.create(currency=self.usd, rate=D("84.50"), valid_from=OCT(30))
        Company.objects.create(
            name="Bengal Polysacks", base_currency=self.inr, tan="CALB01234E",
            default_inventory_account=acct["1300"], default_cogs_account=acct["5010"],
            grni_account=acct["2150"], customer_deposit_account=acct["2300"],
            purchase_price_variance_account=acct["5150"], fx_gain_account=acct["4900"],
            fx_loss_account=acct["5910"], settlement_discount_account=acct["5300"],
            bad_debt_account=acct["5200"], net_pay_account=acct["2400"])
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
        from apps.accounting.models import BooksOpening, TcsSection
        from apps.assets.models import AssetCategory
        from apps.inventory.models import AdjustmentReason

        # No screen keeps the books' opening (O251): a model call.
        BooksOpening.objects.create(opened_on=datetime.date(2026, 9, 30), opening_account=acct["3000"])
        for code, name, kind in (("1500", "Plant and machinery", A), ("1590", "Accumulated depreciation", A),
                                 ("5600", "Depreciation", X), ("5700", "Machinery bought", X)):
            acct[code] = Account.objects.create(code=code, name=name, account_type=kind)
        self.looms = AssetCategory.objects.create(code="LOOM", name="Circular looms", asset_account=acct["1500"],
                                                  accumulated_account=acct["1590"], expense_account=acct["5600"],
                                                  default_life_months=120)
        self.loom_maker = self.party("V-LM", "Kolkata Loom Works", PartyRole.VENDOR, gstin=gstin("19AAACK6666F1Z"),
                                     pan="AAACK6666F")

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
            "ack_date": f"{invoice['invoice_date']}T10:30:00+05:30",
            "signed_qr": signed_qr(Irn=IRN[:-len(str(invoice["id"]))] + str(invoice["id"]), DocNo=invoice["number"],
                                   SellerGstin=gstin("19AAACB1234C1Z"),
                                   BuyerGstin=record["payload"]["BuyerDtls"]["Gstin"],
                                   TotInvVal=float(invoice["total"]))})
        eway = self.post(officer, "/api/gst/eway-bills/", {"invoice": invoice["id"], "vehicle_number": vehicle})
        self.post(officer, f"/api/gst/eway-bills/{eway['id']}/record/", {
            "number": f"33{invoice['id']:010d}", "generated_at": f"{invoice['invoice_date']}T10:00:00+05:30"})

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
            if not note.get("posted"):
                # Warehouse Staff may not post invoices, so their return leaves the note a draft for the AR side
                # to post (O292): the receivable moves when the AR Manager posts it, the same day.
                note = self.post("AR Manager", f"/api/sales/invoices/{note['id']}/post_invoice/")
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

    # -- 6. people -------------------------------------------------------------------

    def pay_component(self, code, kind, basis, sequence, **more):
        made = self.post("Payroll Officer", "/api/hr/pay-components/", {
            "code": code, "name": code, "kind": kind, "basis": basis, "sequence": sequence, **more})
        self.components[code] = made["id"]
        return made["id"]

    def slab(self, code, amount, above="0", up_to=None, month=None):
        self.post("Payroll Officer", "/api/hr/pay-component-slabs/", {
            "component": self.components[code], "above": above, "up_to": up_to, "amount": amount, "month": month,
            "state": "WB"})

    def set_up_pay(self):
        a = self.acct
        self.components = {}
        earn = {"expense_account": a["6000"].pk, "reduces_for_unpaid_leave": True, "is_ordinary_wages": True}
        self.pay_component("BASIC", "earning", "fixed", 10, **earn)
        self.pay_component("HRA", "earning", "fixed", 20, **earn)
        self.pay_component("OT", "earning", "overtime", 30, expense_account=a["6000"].pk)
        pf = {"basis": "percent", "base_components": [self.components["BASIC"]], "base_ceiling": "15000",
              "rounding": "rupee", "liability_account": a["2410"].pk, "remit_by_day": 15}
        self.pay_component("PF", "deduction", sequence=50, statutory="pf", **pf)
        self.pay_component("PF-ER", "employer_cost", sequence=60, statutory="pf_employer",
                           expense_account=a["6100"].pk, **pf)
        esi = {"basis": "percent", "rounding": "rupee_up", "liability_account": a["2420"].pk, "remit_by_day": 15,
               "base_components": [self.components[c] for c in ("BASIC", "HRA", "OT")],
               "coverage_components": [self.components[c] for c in ("BASIC", "HRA")], "coverage_ceiling": "21000",
               "coverage_period_months": 6}
        self.pay_component("ESI", "deduction", sequence=70, statutory="esi", **esi)
        self.pay_component("ESI-ER", "employer_cost", sequence=80, statutory="esi_employer",
                           expense_account=a["6100"].pk, **esi)
        self.pay_component("PT", "deduction", "slab", 90, statutory="pt", liability_account=a["2430"].pk)
        for above, up_to, tax in (("10000", "15000", "110"), ("15000", "25000", "130"), ("25000", "40000", "150"),
                                  ("40000", None, "200")):
            self.slab("PT", tax, above, up_to)
        self.pay_component("LWF", "deduction", "slab", 92, statutory="lwf", liability_account=a["2440"].pk)
        self.pay_component("LWF-ER", "employer_cost", "slab", 93, statutory="lwf_employer",
                           liability_account=a["2440"].pk, expense_account=a["6100"].pk)
        for code, share in (("LWF", "3"), ("LWF-ER", "15")):
            self.slab(code, "0")
            self.slab(code, share, month=6)
            self.slab(code, share, month=12)
        self.pay_component("ADV", "deduction", "fixed", 99, liability_account=a["1400"].pk)

    def hire(self, number, name, hired, born, pay, uan, esi=None, **more):
        person = self.post("HR Admin", "/api/hr/employees/", {
            "employee_number": number, "new_name": name, "hire_date": hired, "working_days": "123456",
            "holiday_region": "", "work_state": "WB", "uan": uan, "esi_number": esi or "", **more})
        for code, amount in {**pay, "PF": "12", "PF-ER": "12", "ESI": "0.75", "ESI-ER": "3.25", "PT": "1",
                             "LWF": "1", "LWF-ER": "1"}.items():
            self.post("Payroll Officer", "/api/hr/compensation/", {
                "employee": person["id"], "component": self.components[code], "amount": amount,
                "effective_from": "2026-04-01" if code not in pay else hired})
        self.post("Payroll Officer", "/api/hr/pay-details/", {
            "employee": person["id"], "account_holder": name, "account_number": f"3001{uan[-8:]}",
            "ifsc": "SBIN0001234", "pan": f"ABCPD{uan[-4:]}K", "date_of_birth": born})
        self.staff[number] = person
        return person

    def give_advance(self, number, day, amount, instalment):
        from apps.hr.models import Employee

        party = Employee.objects.get(pk=self.staff[number]["id"]).party_id
        payment = self.post("Controller", "/api/accounting/payments/", {
            "party": party, "direction": "disbursement", "payment_date": str(day), "amount": amount,
            "currency": self.inr.pk, "bank_account": self.acct["1200"].pk,
            "counterpart_account": self.acct["1400"].pk, "method": "neft"})
        self.post("Controller", f"/api/accounting/payments/{payment['id']}/post_payment/")
        self.statement_line(day, f"NEFT SALARY ADVANCE {number}", f"-{amount}", payment)
        return self.post("Controller", "/api/hr/salary-advances/", {
            "employee": self.staff[number]["id"], "kind": "advance", "payment": payment["id"],
            "component": self.components["ADV"], "amount": amount, "instalment": instalment,
            "recover_from": "2026-10-01"})

    def pay_people(self):
        self.staff = {}
        with on(OCT(1)):
            self.set_up_pay()
            self.hire("M1", "Subrata Ghosh", "2018-04-01", "1990-03-15", {"BASIC": "18000", "HRA": "7000"},
                      "100200300401")
            self.hire("M2", "Anil Banerjee", "2010-06-01", "1967-05-10", {"BASIC": "14000", "HRA": "4000"},
                      "100200300402", esi="3100200302")
            self.hire("L1", "Rina Mondal", "2023-07-01", "1995-08-20", {"BASIC": "12000", "HRA": "3000"},
                      "100200300403", esi="3100200303")
            self.hire("D1", "Bapi Sardar", "2025-01-06", "1998-11-02", {"BASIC": "13500", "OT": "125"},
                      "100200300404", esi="3100200304", paid_by_attendance=True)
            self.advances = {"L1": self.give_advance("L1", OCT(1), "6000.00", "3000.00")}
        with on(OCT(5)):
            self.advances["D1"] = self.give_advance("D1", OCT(5), "4000.00", "2000.00")
        with on(OCT(21), hour=18):
            self.ok(self.as_("HR Admin").patch(f"/api/hr/employees/{self.staff['L1']['id']}/",
                                               {"termination_date": "2026-10-21", "employment_status": "terminated"}, format="json"))
        with on(OCT(31), hour=12):
            day = OCT(1)
            while day <= OCT(31):
                if day.isoweekday() != 7:
                    absent = day in (OCT(7), OCT(8), OCT(9))
                    self.post("Production Supervisor", "/api/hr/attendance/", {
                        "employee": self.staff["D1"]["id"], "on": str(day), "status": "absent" if absent else "present",
                        "overtime_hours": "4" if day in (OCT(12), OCT(13)) else "0"})
                day += datetime.timedelta(days=1)
            officer = "Payroll Officer"
            made = self.post(officer, "/api/hr/pay-runs/", {
                "period_start": "2026-10-01", "period_end": "2026-10-31", "pay_date": "2026-10-31",
                "name": "October 2026"})
            base = f"/api/hr/pay-runs/{made['id']}/"
            self.post(officer, base + "calculate/")
            self.refuse("R9 a pay run posted by the Payroll Officer", officer, base + "post/")
            self.pay_run = self.post("Controller", base + "post/")
            self.ecr = self.as_(officer).get(base + "ecr/").content.decode()
            self.esi = self.as_(officer).get(base + "esi/").content.decode()
            self.pt_register = self.get(officer, "/api/hr/statutory-registers/", {"kind": "pt", "month": "2026-10"})
            self.lwf_register = self.get(officer, "/api/hr/statutory-registers/", {"kind": "lwf", "month": "2026-10"})
            transfer = self.post(officer, "/api/hr/salary-files/", {"run": made["id"]})
            from apps.hr.models import Employee

            payment = self.post("Controller", "/api/accounting/payments/", {
                "party": Employee.objects.get(pk=self.staff["M1"]["id"]).party_id, "direction": "disbursement",
                "payment_date": "2026-10-31", "amount": transfer["total"], "currency": self.inr.pk,
                "bank_account": self.acct["1200"].pk, "counterpart_account": self.acct["2400"].pk,
                "method": "neft", "reference": transfer["number"]})
            self.post("Controller", f"/api/accounting/payments/{payment['id']}/post_payment/")
            self.post("Controller", f"/api/hr/salary-files/{transfer['id']}/mark_paid/", {"payment": payment["id"]})
            self.statement_line(OCT(31), f"BULK NEFT SALARY {transfer['number']}", f"-{transfer['total']}", payment)
            self.salary_file = self.get("Controller", f"/api/hr/salary-files/{transfer['id']}/")
            row = next(row for row in self.salary_file["rows"] if row["employee"] == "D1") \
                if "rows" in self.salary_file else None
            back = self.post("Controller", "/api/accounting/payments/", {
                "party": Employee.objects.get(pk=self.staff["D1"]["id"]).party_id, "direction": "receipt",
                "payment_date": "2026-10-31", "amount": "9352.00", "currency": self.inr.pk,
                "bank_account": self.acct["1200"].pk, "counterpart_account": self.acct["2400"].pk,
                "method": "neft", "reference": "RTN A/C CLOSED"})
            self.post("Controller", f"/api/accounting/payments/{back['id']}/post_payment/")
            self.post("Controller", f"/api/hr/salary-files/{transfer['id']}/return_transfer/",
                      {"row": row["id"], "receipt": back["id"]})
            self.statement_line(OCT(31), "NEFT RETURN A/C CLOSED BAPI SARDAR", "9352.00", back)
            self.salary_file = self.get("Controller", f"/api/hr/salary-files/{transfer['id']}/")
            self.payslips = {slip["employee_number"] if "employee_number" in slip else slip["employee"]: slip
                             for slip in self.get("Controller", "/api/hr/payslips/", {"run": made["id"]})}
            self.advance_rows = self.get("Controller", "/api/hr/salary-advances/")
            self.pay_run = self.get("Controller", base)

    # -- 7. close -------------------------------------------------------------------

    def buy_a_loom(self):
        bill = self.post("Purchasing Clerk", "/api/purchasing/bills/", {
            "vendor": self.loom_maker.pk, "bill_date": str(OCT(1)), "reference": "KLW/26-27/088",
            "payable_account": self.acct["2000"].pk, "currency": self.inr.pk})
        self.post("Purchasing Clerk", "/api/purchasing/bill-lines/", {
            "bill": bill["id"], "description": "Six-shuttle circular loom, 750 mm", "quantity": "1",
            "unit_price": "600000.00", "expense_account": self.acct["5700"].pk,
            "taxes": [self.tax("CGST9"), self.tax("SGST9")]})
        bill = self.post("AP Manager", f"/api/purchasing/bills/{bill['id']}/post_bill/")
        [self.loom] = self.post("Controller", f"/api/purchasing/bills/{bill['id']}/capitalise/", {
            "line": bill["lines"][0]["id"], "category": self.looms.pk, "name": "Loom CL-07",
            "in_service_date": str(OCT(1)), "life_months": 120})
        self.post("Controller", f"/api/assets/assets/{self.loom['id']}/place-in-service/",
                  {"on_date": str(OCT(1))})
        self.loom_bill = bill

    def close(self):
        controller = "Controller"
        self.loom_charged = self.post(controller, "/api/assets/assets/depreciate-all/", {"through": "2026-10-31"})
        self.loom = self.get(controller, f"/api/assets/assets/{self.loom['id']}/")
        self.asset_register = self.get(controller, "/api/assets/assets/register/", {"as_of": "2026-10-31"})
        self.gstr1 = self.get("GST Officer", "/api/gst/gstr1/", {"period": "2026-10"})
        self.gstr3b = self.get("GST Officer", "/api/gst/gstr3b/", {"period": "2026-10"})
        self.tds_return = self.get(controller, "/api/purchasing/tds-deductions/quarter/",
                                   {"from": "2026-10-01", "to": "2026-12-31"})["rows"]
        self.tcs_return = self.get(controller, "/api/sales/tcs-collections/quarter/",
                                   {"from": "2026-10-01", "to": "2026-12-31"})["rows"]
        self.ar_aging = self.get("AR Manager", "/api/sales/invoices/aging/", {"as_of": "2026-10-31"})
        self.ap_aging = self.get("AP Manager", "/api/purchasing/purchasing-reports/aging/", {"as_of": "2026-10-31"})
        period = self.post(controller, "/api/accounting/periods/", {
            "name": "October 2026", "start_date": "2026-10-01", "end_date": "2026-10-31"})
        self.refuse("R8 the month closed by the Bookkeeper", "Bookkeeper",
                    f"/api/accounting/periods/{period['id']}/close/")
        self.period = self.post(controller, f"/api/accounting/periods/{period['id']}/close/")

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
                "end_date": "2026-10-31", "opening_balance": "0", "closing_balance": "79175.00"})
        with on(OCT(20)):
            self.pay_masterbatch()
        with on(OCT(1)):
            self.buy_a_loom()
        with on(OCT(11)):
            self.make_tape()
        self.sell_to_hooghly()
        self.export()
        self.sell_scrap()
        self.pay_people()
        with on(OCT(31), hour=18):
            self.close()
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
            "R8 the month closed by the Bookkeeper": 403,
            "R9 a pay run posted by the Payroll Officer": 403,
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

    def test_a_return_credits_an_invoice_that_drew_an_advance(self):
        """
        Register row (this report): 200 kg returned on the delivery whose invoice drew down a 1,00,000.00
        advance. create_return holds the invoices it credits, not the deposits each draws down, which
        Invoice.locked_before_it names; the credit note takes the deposit after the returned stock.
        """
        # customer_return answers 200 with the return and its notes (sales/views.py); the note's figures
        # are test_the_return_is_credited's.
        self.assertEqual(self.m.findings["a return credits an invoice that drew an advance"], 200)

    # -- 5. scrap --------------------------------------------------------------------

    def test_tcs_collected_on_the_scrap_and_paid_over(self):
        # 23,600.00 with GST, 2% = 472.00, collected with the invoice and paid by challan on 31 Oct.
        self.assertEqual((self.m.scrap_invoice["total"], self.m.scrap_invoice["amount_due"]), ("24072.00", "0.00"))
        self.assertEqual((self.m.tcs_challan["amount"], self.bal("2260")), ("472.00", D("0")))

    def test_sweepings_counted_in_keep_the_cost_they_were_given(self):
        """
        Register row (this report): 500 kg of sweepings, never stocked, counted in at 30.00. The line's
        unit_cost is read-only over the API, and posting refuses a gain with no cost to take.
        """
        given = self.m.findings["a count-in line keeps the cost it was given"]
        self.assertIsNotNone(given)
        self.assertEqual(D(given), D("30.00"))

    # -- 6. people -------------------------------------------------------------------

    def pay_component(self, code, kind, basis, sequence, **more):
        made = self.post("Payroll Officer", "/api/hr/pay-components/", {
            "code": code, "name": code, "kind": kind, "basis": basis, "sequence": sequence, **more})
        self.components[code] = made["id"]
        return made["id"]

    def slab(self, code, amount, above="0", up_to=None, month=None):
        self.post("Payroll Officer", "/api/hr/pay-component-slabs/", {
            "component": self.components[code], "above": above, "up_to": up_to, "amount": amount, "month": month,
            "state": "WB"})

    def set_up_pay(self):
        a = self.acct
        self.components = {}
        earn = {"expense_account": a["6000"].pk, "reduces_for_unpaid_leave": True, "is_ordinary_wages": True}
        self.pay_component("BASIC", "earning", "fixed", 10, **earn)
        self.pay_component("HRA", "earning", "fixed", 20, **earn)
        self.pay_component("OT", "earning", "overtime", 30, expense_account=a["6000"].pk)
        pf = {"basis": "percent", "base_components": [self.components["BASIC"]], "base_ceiling": "15000",
              "rounding": "rupee", "liability_account": a["2410"].pk, "remit_by_day": 15}
        self.pay_component("PF", "deduction", sequence=50, statutory="pf", **pf)
        self.pay_component("PF-ER", "employer_cost", sequence=60, statutory="pf_employer",
                           expense_account=a["6100"].pk, **pf)
        esi = {"basis": "percent", "rounding": "rupee_up", "liability_account": a["2420"].pk, "remit_by_day": 15,
               "base_components": [self.components[c] for c in ("BASIC", "HRA", "OT")],
               "coverage_components": [self.components[c] for c in ("BASIC", "HRA")], "coverage_ceiling": "21000",
               "coverage_period_months": 6}
        self.pay_component("ESI", "deduction", sequence=70, statutory="esi", **esi)
        self.pay_component("ESI-ER", "employer_cost", sequence=80, statutory="esi_employer",
                           expense_account=a["6100"].pk, **esi)
        self.pay_component("PT", "deduction", "slab", 90, statutory="pt", liability_account=a["2430"].pk)
        for above, up_to, tax in (("10000", "15000", "110"), ("15000", "25000", "130"), ("25000", "40000", "150"),
                                  ("40000", None, "200")):
            self.slab("PT", tax, above, up_to)
        self.pay_component("LWF", "deduction", "slab", 92, statutory="lwf", liability_account=a["2440"].pk)
        self.pay_component("LWF-ER", "employer_cost", "slab", 93, statutory="lwf_employer",
                           liability_account=a["2440"].pk, expense_account=a["6100"].pk)
        for code, share in (("LWF", "3"), ("LWF-ER", "15")):
            self.slab(code, "0")
            self.slab(code, share, month=6)
            self.slab(code, share, month=12)
        self.pay_component("ADV", "deduction", "fixed", 99, liability_account=a["1400"].pk)

    def hire(self, number, name, hired, born, pay, uan, esi=None, **more):
        person = self.post("HR Admin", "/api/hr/employees/", {
            "employee_number": number, "new_name": name, "hire_date": hired, "working_days": "123456",
            "holiday_region": "", "work_state": "WB", "uan": uan, "esi_number": esi or "", **more})
        for code, amount in {**pay, "PF": "12", "PF-ER": "12", "ESI": "0.75", "ESI-ER": "3.25", "PT": "1",
                             "LWF": "1", "LWF-ER": "1"}.items():
            self.post("Payroll Officer", "/api/hr/compensation/", {
                "employee": person["id"], "component": self.components[code], "amount": amount,
                "effective_from": "2026-04-01" if code not in pay else hired})
        self.post("Payroll Officer", "/api/hr/pay-details/", {
            "employee": person["id"], "account_holder": name, "account_number": f"3001{uan[-8:]}",
            "ifsc": "SBIN0001234", "pan": f"ABCPD{uan[-4:]}K", "date_of_birth": born})
        self.staff[number] = person
        return person

    def give_advance(self, number, day, amount, instalment):
        from apps.hr.models import Employee

        party = Employee.objects.get(pk=self.staff[number]["id"]).party_id
        payment = self.post("Controller", "/api/accounting/payments/", {
            "party": party, "direction": "disbursement", "payment_date": str(day), "amount": amount,
            "currency": self.inr.pk, "bank_account": self.acct["1200"].pk,
            "counterpart_account": self.acct["1400"].pk, "method": "neft"})
        self.post("Controller", f"/api/accounting/payments/{payment['id']}/post_payment/")
        self.statement_line(day, f"NEFT SALARY ADVANCE {number}", f"-{amount}", payment)
        return self.post("Controller", "/api/hr/salary-advances/", {
            "employee": self.staff[number]["id"], "kind": "advance", "payment": payment["id"],
            "component": self.components["ADV"], "amount": amount, "instalment": instalment,
            "recover_from": "2026-10-01"})

    def pay_people(self):
        self.staff = {}
        with on(OCT(1)):
            self.set_up_pay()
            self.hire("M1", "Subrata Ghosh", "2018-04-01", "1990-03-15", {"BASIC": "18000", "HRA": "7000"},
                      "100200300401")
            self.hire("M2", "Anil Banerjee", "2010-06-01", "1967-05-10", {"BASIC": "14000", "HRA": "4000"},
                      "100200300402", esi="3100200302")
            self.hire("L1", "Rina Mondal", "2023-07-01", "1995-08-20", {"BASIC": "12000", "HRA": "3000"},
                      "100200300403", esi="3100200303")
            self.hire("D1", "Bapi Sardar", "2025-01-06", "1998-11-02", {"BASIC": "13500", "OT": "125"},
                      "100200300404", esi="3100200304", paid_by_attendance=True)
            self.advances = {"L1": self.give_advance("L1", OCT(1), "6000.00", "3000.00")}
        with on(OCT(5)):
            self.advances["D1"] = self.give_advance("D1", OCT(5), "4000.00", "2000.00")
        with on(OCT(21), hour=18):
            self.ok(self.as_("HR Admin").patch(f"/api/hr/employees/{self.staff['L1']['id']}/",
                                               {"termination_date": "2026-10-21", "employment_status": "terminated"}, format="json"))
        with on(OCT(31), hour=12):
            day = OCT(1)
            while day <= OCT(31):
                if day.isoweekday() != 7:
                    absent = day in (OCT(7), OCT(8), OCT(9))
                    self.post("Production Supervisor", "/api/hr/attendance/", {
                        "employee": self.staff["D1"]["id"], "on": str(day), "status": "absent" if absent else "present",
                        "overtime_hours": "4" if day in (OCT(12), OCT(13)) else "0"})
                day += datetime.timedelta(days=1)
            officer = "Payroll Officer"
            made = self.post(officer, "/api/hr/pay-runs/", {
                "period_start": "2026-10-01", "period_end": "2026-10-31", "pay_date": "2026-10-31",
                "name": "October 2026"})
            base = f"/api/hr/pay-runs/{made['id']}/"
            self.post(officer, base + "calculate/")
            self.refuse("R9 a pay run posted by the Payroll Officer", officer, base + "post/")
            self.pay_run = self.post("Controller", base + "post/")
            self.ecr = self.as_(officer).get(base + "ecr/").content.decode()
            self.esi = self.as_(officer).get(base + "esi/").content.decode()
            self.pt_register = self.get(officer, "/api/hr/statutory-registers/", {"kind": "pt", "month": "2026-10"})
            self.lwf_register = self.get(officer, "/api/hr/statutory-registers/", {"kind": "lwf", "month": "2026-10"})
            transfer = self.post(officer, "/api/hr/salary-files/", {"run": made["id"]})
            from apps.hr.models import Employee

            payment = self.post("Controller", "/api/accounting/payments/", {
                "party": Employee.objects.get(pk=self.staff["M1"]["id"]).party_id, "direction": "disbursement",
                "payment_date": "2026-10-31", "amount": transfer["total"], "currency": self.inr.pk,
                "bank_account": self.acct["1200"].pk, "counterpart_account": self.acct["2400"].pk,
                "method": "neft", "reference": transfer["number"]})
            self.post("Controller", f"/api/accounting/payments/{payment['id']}/post_payment/")
            self.post("Controller", f"/api/hr/salary-files/{transfer['id']}/mark_paid/", {"payment": payment["id"]})
            self.statement_line(OCT(31), f"BULK NEFT SALARY {transfer['number']}", f"-{transfer['total']}", payment)
            self.salary_file = self.get("Controller", f"/api/hr/salary-files/{transfer['id']}/")
            row = next(row for row in self.salary_file["rows"] if row["employee"] == "D1") \
                if "rows" in self.salary_file else None
            back = self.post("Controller", "/api/accounting/payments/", {
                "party": Employee.objects.get(pk=self.staff["D1"]["id"]).party_id, "direction": "receipt",
                "payment_date": "2026-10-31", "amount": "9352.00", "currency": self.inr.pk,
                "bank_account": self.acct["1200"].pk, "counterpart_account": self.acct["2400"].pk,
                "method": "neft", "reference": "RTN A/C CLOSED"})
            self.post("Controller", f"/api/accounting/payments/{back['id']}/post_payment/")
            self.post("Controller", f"/api/hr/salary-files/{transfer['id']}/return_transfer/",
                      {"row": row["id"], "receipt": back["id"]})
            self.statement_line(OCT(31), "NEFT RETURN A/C CLOSED BAPI SARDAR", "9352.00", back)
            self.salary_file = self.get("Controller", f"/api/hr/salary-files/{transfer['id']}/")
            self.payslips = {slip["employee_number"] if "employee_number" in slip else slip["employee"]: slip
                             for slip in self.get("Controller", "/api/hr/payslips/", {"run": made["id"]})}
            self.advance_rows = self.get("Controller", "/api/hr/salary-advances/")
            self.pay_run = self.get("Controller", base)

    # -- 6. people -------------------------------------------------------------------

    def test_each_slip_is_what_the_month_owed(self):
        # gross, deductions, net, employer: calc_month/month.py section 6.
        self.assertEqual({number: (slip["gross"], slip["deductions"], slip["net"], slip["employer_cost"])
                          for number, slip in self.m.payslips.items()}, {
            "M1": ("25000.00", "1930.00", "23070.00", "1800.00"),
            "M2": ("18000.00", "1945.00", "16055.00", "2265.00"),
            "L1": ("10000.00", "5000.00", "5000.00", "1285.00"),
            "D1": ("13000.00", "3648.00", "9352.00", "1863.00"),
        })
        lines = {number: {line["component"]: line["amount"] for line in slip["lines"]}
                 for number, slip in self.m.payslips.items()}
        # 24 of 27 days at 13,500 and 8 overtime hours at 125; 18 of 27 to the leaver's last day.
        self.assertEqual((lines["D1"]["BASIC"], lines["D1"]["OT"], lines["L1"]["BASIC"], lines["L1"]["HRA"]),
                         ("12000.00", "1000.00", "8000.00", "2000.00"))
        # The leaver's final slip takes what the cap leaves of the advance: 5,000 less 960 PF and 75 ESI.
        self.assertEqual((lines["L1"]["ADV"], lines["D1"]["ADV"]), ("3965.00", "2000.00"))

    def test_net_pay_payable_foots_to_the_slips_not_yet_paid(self):
        unpaid = {number for number, slip in self.m.payslips.items() if not slip["paid"]}
        self.assertEqual((unpaid, self.m.pay_run["unpaid_net"]), ({"D1"}, "9352.00"))
        self.assertEqual(-self.bal("2400"), D(self.m.pay_run["unpaid_net"]))
        self.assertEqual([(row["employee"], row["returned_by"] is not None) for row in self.m.salary_file["rows"]
                          if row["returned_by"]], [("D1", True)])

    def test_the_advance_ledger_foots_to_the_employee_receivable(self):
        from apps.hr.models import Employee

        balances = {Employee.objects.get(pk=row["employee"]).employee_number: D(row["balance"])
                    for row in self.m.advance_rows}
        self.assertEqual(balances, {"L1": D("2035.00"), "D1": D("2000.00")})
        self.assertEqual(self.bal("1400"), sum(balances.values()))
        by_party = {number: sum((line.debit - line.credit for line in JournalLine.objects.filter(
            account=self.m.acct["1400"], entry__posted=True, party=Employee.objects.get(employee_number=number).party)),
            D("0")) for number in balances}
        self.assertEqual(by_party, balances)

    def test_the_ecr_reads_what_was_posted(self):
        # UAN, name, gross, EPF, EPS, EDLI wages, EPF, EPS, difference, NCP days, refund. M2 is past 58: no EPS.
        self.assertEqual(self.m.ecr.splitlines(), [
            "100200300404#~#Bapi Sardar#~#13000#~#12000#~#12000#~#12000#~#1440#~#1000#~#440#~#3#~#0",
            "100200300403#~#Rina Mondal#~#10000#~#8000#~#8000#~#8000#~#960#~#666#~#294#~#0#~#0",
            "100200300401#~#Subrata Ghosh#~#25000#~#15000#~#15000#~#15000#~#1800#~#1250#~#550#~#0#~#0",
            "100200300402#~#Anil Banerjee#~#18000#~#14000#~#0#~#14000#~#1680#~#0#~#1680#~#0#~#0",
        ])

    def test_the_esi_file_reads_what_was_posted(self):
        # M1's 25,000 is past the 21,000 coverage: not insured.
        self.assertEqual(self.m.esi.splitlines()[1:], [
            "3100200304,Bapi Sardar,24,13000,0,", "3100200303,Rina Mondal,18,10000,0,21/10/2026",
            "3100200302,Anil Banerjee,27,18000,0,"])

    def test_the_pt_and_lwf_registers_read_what_was_posted(self):
        self.assertEqual([(row["employee"], row["wages"], row["tax"], row["state"])
                          for row in self.m.pt_register["rows"]],
                         [("D1", "13000.00", "110.00", "WB"), ("M1", "25000.00", "130.00", "WB"),
                          ("M2", "18000.00", "130.00", "WB")])
        # The leaver's 10,000 is in West Bengal's nil band; October asks no welfare fund.
        self.assertEqual(self.m.lwf_register["rows"], [])

    def test_payroll_booked_its_cost(self):
        self.assertEqual((self.bal("6000"), self.bal("6100")), (D("66000.00"), D("7213.00")))

    # -- 7. close -------------------------------------------------------------------

    def test_the_loom_is_depreciated_for_october_and_the_register_foots(self):
        # 6,00,000.00 over 120 months: 5,000.00 for a whole October.
        self.assertEqual((self.bal("1500"), self.bal("1590"), self.bal("5600")),
                         (D("600000.00"), D("-5000.00"), D("5000.00")))
        self.m.register_rows = self.m.asset_register
        rows = self.m.asset_register if isinstance(self.m.asset_register, list) else \
            self.m.asset_register.get("rows", self.m.asset_register.get("assets", []))
        [row] = rows
        self.assertEqual(D(str(row.get("book_value", row.get("net_book_value")))),
                         self.bal("1500") + self.bal("1590"))

    def test_gstr1_reads_what_was_posted(self):
        g = self.m.gstr1
        self.assertEqual([(row["number"], row["value"], row["items"][0]["taxable"], row["items"][0]["cgst"])
                          for row in g["b2b"]],
                         [(self.m.dom_invoice["number"], "708000.00", "600000.00", "54000.00"),
                          (self.m.scrap_invoice["number"], "24072.00", "20000.00", "1800.00")])
        self.assertEqual([(row["value"], row["type"], row["items"][0]["igst"]) for row in g["exp"]],
                         [("168000.00", "without_payment", "0.00")])
        self.assertEqual([(row["value"], row["original"], row["items"][0]["cgst"]) for row in g["cdnr"]],
                         [("35400.00", self.m.dom_invoice["number"], "2700.00")])

    def test_gstr3b_reads_what_was_posted(self):
        g = self.m.gstr3b
        self.assertEqual((g["3.1a"]["taxable"], g["3.1a"]["cgst"], g["3.1a"]["sgst"]),
                         ("590000.00", "53100.00", "53100.00"))
        self.assertEqual(g["3.1b"]["taxable"], "168000.00")
        self.assertEqual((g["4A5"]["igst"], g["4A5"]["cgst"], g["4A5"]["sgst"]), ("176400.00", "66150.00", "66150.00"))
        self.assertEqual(g["5"], {"inter": "0", "intra": "39200.00"})

    def test_the_tds_and_tcs_returns_read_what_was_posted(self):
        self.assertEqual([(row["section"], row["base"], row["amount"], row["challan"]) for row in self.m.tds_return],
                         [("194C", "39200.00", "784.00", "")])
        self.assertEqual([(row["section"], row["base"], row["amount"], row["paid_on"])
                          for row in self.m.tcs_return], [("394(1)", "23600.00", "472.00", "2026-10-31")])

    def test_the_month_is_closed(self):
        self.assertTrue(self.m.period["closed"])

    # -- what must be true at the end ------------------------------------------------

    def test_the_control_accounts_stand_where_the_month_leaves_them(self):
        expected = {
            "2150": D("0"),  # GRNI: received and billed
            "2300": D("0"),  # customer deposits: taken and drawn down
            "2250": D("-784.00"),  # TDS on the freight, due 7 November
            "2260": D("0"),  # TCS on the scrap, paid
            "2201": D("-53100.00"), "2202": D("-53100.00"), "2203": D("0"),  # GST output
            "1311": D("66150.00"), "1312": D("66150.00"), "1313": D("176400.00"),  # GST input
            "4900": D("-1000.00"), "5910": D("0"),  # exchange
            "1900": D("0"),  # suspense
            "1350": D("0"),  # work in progress
            "2400": D("-9352.00"),  # net pay payable: D1's transfer came back
            "2410": D("-11760.00"), "2420": D("-1641.00"), "2430": D("-370.00"),  # PF, ESI, PT, due in November
            "2440": D("0"),  # LWF: West Bengal's is June's and December's
            "1400": D("4035.00"),  # salary advances still owed
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
        self.assertEqual({k: v for k, v in due.items() if v}, {"V-PP": D("1156400.00"), "V-TRN": D("38416.00"),
                                                                     "V-LM": D("708000.00")})
        self.assertEqual(self.bal("2000"), -sum(due.values()))

    def test_aging_foots_to_the_control_accounts(self):
        # As of 31 Oct, everything net 30 and current: Hooghly due 14 Nov (15 Oct + 30); the freight and the
        # polymer 8 Nov (9 Oct + 30); the loom 31 Oct (1 Oct + 30), due that day and not yet over.
        ar, ap = self.m.ar_aging, self.m.ap_aging
        self.assertEqual({bucket: rows["total"] for bucket, rows in ar.items() if rows["count"]},
                         {"current": "572600.00"})
        self.assertEqual(D(ar["current"]["total"]), self.bal("1100"))
        self.assertEqual(sorted((row["vendor"][:5].strip(), row["due_date"], row["days_overdue"])
                                for row in ap["current"]["bills"]),
                         [("V-LM", "2026-10-31", 0), ("V-PP", "2026-11-08", 0), ("V-TRN", "2026-11-08", 0)])
        self.assertEqual({bucket: rows["total"] for bucket, rows in ap.items() if rows["count"]},
                         {"current": "1902816.00"})
        self.assertEqual(-D(ap["current"]["total"]), self.bal("2000"))

    def test_the_bank_and_its_statement_agree(self):
        self.assertEqual(self.bal("1200"), D("79175.00"))
        report = self.m.get("Bookkeeper", f"/api/accounting/bank-statements/{self.m.statement['id']}/reconciliation/")
        self.m.reconciliation = report
        self.assertEqual(D(str(report.get("difference", report.get("unexplained", "0")))), D("0"), report)

    def test_the_trial_balance_balances_and_the_profit_is_the_equity_it_made(self):
        totals = JournalLine.objects.filter(entry__posted=True).aggregate(d=Sum("debit"), c=Sum("credit"))
        self.assertEqual(totals["d"], totals["c"])
        kinds = lambda *types: sum((balance(a) for a in Account.objects.filter(account_type__in=types)), D("0"))
        profit = -kinds(AccountType.INCOME, AccountType.EXPENSE)
        self.assertEqual(profit, D("153363.00"))
        # No capital came in: what the month made is what the balance sheet gained.
        self.assertEqual(kinds(AccountType.ASSET) + kinds(AccountType.LIABILITY, AccountType.EQUITY), profit)







# -- the floor, on its own books ---------------------------------------------------------------------------------
#
# The stations' fixtures (apps/manufacturing/tests_station*.py) keep their own company, accounts and stock, on
# their own day (1 June 2026), so the floor is replayed here on those books rather than the month's. What the
# fixture puts on the shelf it puts there without a document; the floor's footing is therefore of movements:
# everything the floor did moved the shelf and the inventory account by the same amount.

from apps.manufacturing.tests_orders import TODAY
from apps.manufacturing.tests_station import at
from apps.manufacturing.tests_station_api import StationApiTestCase
from apps.manufacturing.tests_station_tape import OFF, ON, build_tape_line


class PlantFloorTests(StationApiTestCase):
    """
    The floor's day, every step through the stations and the screens:

      - the scale bridge posts 106.8 kg with its key, and again: answered once;
      - the weaver signs in at the loom station with a PIN, puts tape batch T-1, all 100 kg of it, on L-30's warp,
        and weighs a roll off L-30 from the scale, naming himself as its weaver by his PIN: 104.4 kg net on a 2.4 kg core, 1,006 m declared;
      - the tape line's operator doffs two batches at TX-1: one in denier, one at 1,085 against 950 to
        1,050, booked on the supervisor's PIN and held for the Quality Manager, who concedes it;
      - 5 kg of the line's waste is booked back as regrind;
      - the good batch is re-batched into TP-A (30 kg) and TP-B (26 kg) for two creels;
      - T-1 is then found at 1,085 denier and rejected: the roll woven from it is held;
      - the weaver's piece work, 1,006 m at 0.50, is paid in June's run: 503.00;
      - both runs are closed.

    Model calls (no screen): the fixture's books and stock; loom L-30 of the plant's own; the loom station bridged to scale SC-1; the
    tape batch T-1 made lot-tracked and stocked; the company's net pay account; the bridge's login; the
    specification-made inspection plans set advisory, and T-1's mandatory for its rejection.
    """

    def setUp(self):
        # The fixture's opening stock on the floor's day, before anything the floor does: stamped by the
        # wall clock, it would replay after the day's issues and value them from an empty shelf.
        opening = patch("django.utils.timezone.now", return_value=at(TODAY, 6))
        opening.start()
        self.addCleanup(opening.stop)
        super().setUp()
        build_tape_line(self, regrind_item=self.regrind, waste_recovered_percent=Decimal(80))
        call_command("setup_roles", verbosity=0)
        clock = patch("django.utils.timezone.now", return_value=at(TODAY, 10, 42))
        clock.start()
        self.addCleanup(clock.stop)
        self.people = {}
        self.items = [self.virgin, self.regrind, self.filler, self.colour, self.fabric, self.tape_item,
                      self.spec.warp_tape.tape_item]
        self.shelf_before = sum((item.stock_value_at(self.plant) for item in self.items), D("0"))
        self.floor()

    def as_(self, role, employee=None):
        if role not in self.people:
            user = User.objects.create_user(f"floor-{role.replace(' ', '_').lower()}")
            user.groups.add(Group.objects.get(name=role))
            if employee is not None:
                employee.user = user
                employee.save()
            client = APIClient()
            client.force_authenticate(user)
            self.people[role] = client
        return self.people[role]

    def ok(self, response, status=(200, 201)):
        return Month.ok(self, response, status)

    def at_station(self, station, path, data):
        return self.ok(self.client.post(f"/api/manufacturing/stations/{station}/{path}", data, format="json"))

    def floor(self):
        from apps.inventory.models import Item, Lot
        from apps.manufacturing.station import LoomStation
        from apps.quality.models import InspectionPlan

        manager = self.as_("Quality Manager", self.employee("EMP-0300", "Asha Sen"))
        # Advisory plans, as a plant that does not inspect each roll keeps them. A plan computed from a
        # specification is rebuilt from it and takes no edit over the API: a model call, as the suite's own.
        InspectionPlan.objects.filter(item__in=(self.fabric, self.tape_item, self.spec.warp_tape.tape_item)).update(
            is_mandatory=False)

        # The scale bridge (its own login, as deploy/scale_bridge has it).
        LoomStation.objects.filter(pk=self.station.pk).update(scale_code="SC-1", scale_bridged=True)
        bridge = APIClient()
        bridge_user = User.objects.create_user("bridge-sc1")
        bridge_user.user_permissions.add(Permission.objects.get(codename="add_scalereading"))
        bridge.force_authenticate(bridge_user)
        reading = {"scale": "SC-1", "gross_kg": "106.8", "stable": True, "key": "SC-1:1780292520:7:gross:106.8"}
        self.first_post = bridge.post("/api/manufacturing/scale-readings/", reading, format="json")
        self.second_post = bridge.post("/api/manufacturing/scale-readings/", reading, format="json")

        # The weaver at the loom station.
        warp = self.spec.warp_tape.tape_item
        Item.objects.filter(pk=warp.pk).update(tracking="lot")
        warp.refresh_from_db()
        self.t1 = Lot.objects.create(item=warp, code="T-1")
        self.stock(warp, "100", "120", lot=self.t1)
        self.shelf_before = sum((item.stock_value_at(self.plant) for item in self.items), D("0"))
        # A loom of the plant's own (L-17 and L-20 are a contractor's, whose weavers it pays itself).
        from apps.manufacturing.machines import Machine

        self.station.machines.add(Machine.objects.create(work_centre=self.centre, code="L-30"))
        self.ok(self.sign_in())
        self.loaded = self.at_station("LX-1", "load-tape/", {"machine": "L-30", "doff": "T-1", "kg": "100",
                                                              "side": "warp"})
        self.weighed = self.ok(self.roll(loom="L-30", gross_kg="", weaver_pin=self.pin))

        # The tape line, given its granules by the supervisor.
        supervisor = self.as_("Production Supervisor")
        issue = self.ok(supervisor.post("/api/manufacturing/material-issues/", {
            "work_order": self.tape_run.pk, "issue_date": str(TODAY), "warehouse": self.plant.pk}, format="json"))
        for number, (item, quantity) in enumerate(((self.virgin, "110"), (self.filler, "10")), start=1):
            self.ok(supervisor.post("/api/manufacturing/material-issue-lines/", {
                "issue": issue["id"], "item": item.pk, "quantity": quantity, "uom": self.kg.pk,
                "line_number": number}, format="json"))
        self.ok(supervisor.post(f"/api/manufacturing/material-issues/{issue['id']}/post/"))
        self.at_station("TX-1", "sign-in/", {"pin": self.pin})
        self.good_doff = self.at_station("TX-1", "tape/", {"machine": "E-1", "gross_kg": "60.0", "bobbins": 8,
                                                           "core": "BOB", "denier": ON})
        self.held_doff = self.at_station("TX-1", "tape/", {
            "machine": "E-1", "gross_kg": "60.0", "bobbins": 8, "core": "BOB", "denier": OFF,
            "supervisor_pin": self.supervisor_pin, "reason": "Customer accepts heavy"})
        self.waste = self.at_station("TX-1", "run-waste/", {"machine": "E-1", "kg": "5"})
        # The good batch split for two creels: a re-batch, value for value.
        good = Lot.objects.get(code=self.good_doff["batch"])
        self.tape_value_before_rebatch = self.tape_item.stock_value_at(self.plant)
        self.inventory_before_rebatch = balance(self.inventory)
        self.rebatch = self.ok(supervisor.post("/api/manufacturing/rebatches/record/", {
            "item": self.tape_item.pk, "warehouse": self.plant.pk, "reason": "Split for two creels",
            "rebatched_on": str(TODAY), "taken": [{"lot": good.pk, "quantity": "56"}],
            "made": [{"code": "TP-A", "quantity": "30"}, {"code": "TP-B", "quantity": "26"}]}, format="json"))
        self.rebatched = {code: Lot.objects.get(code=code).on_hand_at(self.plant) for code in ("TP-A", "TP-B")}
        self.tape_value_after_rebatch = self.tape_item.stock_value_at(self.plant)
        self.inventory_after_rebatch = balance(self.inventory)
        awaiting = self.ok(manager.get("/api/quality/inspections/awaiting/"))
        self.awaiting = awaiting
        held = next(row for row in (awaiting if isinstance(awaiting, list) else awaiting["results"])
                    if row["number"] == self.held_doff["inspection"])
        self.decided = self.ok(manager.post(f"/api/quality/inspections/{held['id']}/decide/", {
            "disposition": "concession", "note": "Heavy tape for the cement order, agreed with the buyer"},
            format="json"))

        # The recall: T-1 measured after it was woven, and rejected.
        plan = self.spec.warp_tape.inspection_plan
        InspectionPlan.objects.filter(pk=plan.pk).update(is_mandatory=True)
        line = plan.lines.get(derived_from="denier")
        rejection = self.ok(manager.post("/api/quality/inspections/", {
            "lot": self.t1.pk, "plan": plan.pk, "inspected_on": str(TODAY)}, format="json"))
        for number in range(line.sample_size):
            self.ok(manager.post("/api/quality/readings/", {
                "inspection": rejection["id"], "plan_line": line.pk, "value": "1085",
                "sample_reference": f"S{number}"}, format="json"))
        self.rejection = self.ok(manager.post(f"/api/quality/inspections/{rejection['id']}/post/"))
        stores = self.as_("Stores Manager")
        self.fabric_lots = {row["code"]: row for row in self.ok(stores.get("/api/inventory/lots/",
                                                                             {"item": self.fabric.pk}))}

        # Piece work, paid in June's run.
        from apps.accounting.models import Account, AccountType

        wages = Account.objects.create(code="6000", name="Wages", account_type=AccountType.EXPENSE)
        self.net_pay = Account.objects.create(code="2400", name="Net pay payable", account_type=AccountType.LIABILITY)
        Company.objects.update(net_pay_account=self.net_pay)
        officer = self.as_("Payroll Officer")
        piece = self.ok(officer.post("/api/hr/pay-components/", {
            "code": "PIECE", "name": "Metres woven", "kind": "earning", "basis": "per_unit",
            "measure": "metres_woven", "expense_account": wages.pk, "sequence": 10}, format="json"))
        self.ok(officer.post("/api/hr/compensation/", {"employee": self.operator.pk, "component": piece["id"],
                                                       "amount": "0.50", "effective_from": "2026-04-01"},
                             format="json"))
        run = self.ok(officer.post("/api/hr/pay-runs/", {"period_start": "2026-06-01", "period_end": "2026-06-30",
                                                         "pay_date": "2026-06-30", "name": "June"}, format="json"))
        self.ok(officer.post(f"/api/hr/pay-runs/{run['id']}/calculate/", {"employees": [self.operator.pk]},
                             format="json"))
        self.ok(self.as_("Controller").post(f"/api/hr/pay-runs/{run['id']}/post/"))
        [self.piece_slip] = self.ok(officer.get("/api/hr/payslips/", {"run": run["id"]}))

        # The runs closed.
        self.closed = [self.ok(supervisor.post(f"/api/manufacturing/work-orders/{order.pk}/close/",
                                               {"date": str(TODAY)}, format="json"))
                       for order in (self.run, self.tape_run)]


    def bal(self, account):
        return balance(account)

    def test_the_bridge_posts_a_reading_once_whatever_it_resends(self):
        first, second = self.first_post.json(), self.second_post.json()
        self.assertEqual((self.first_post.status_code, self.second_post.status_code, first["id"]),
                         (201, 200, second["id"]))
        self.assertEqual(D(second["gross_kg"]), D("106.8"))

    def test_the_roll_is_weighed_from_the_scale_and_credited_to_its_weaver(self):
        roll = self.weighed
        self.assertEqual((roll["source"], roll["net_kg"], roll["metres_from_weight"], roll["variance_percent"],
                          roll["is_exception"], roll["woven_by"]["number"]),
                         ("scale", "104.400", "1000.00", "0.60", False, "EMP-0142"))

    def test_the_off_denier_batch_was_held_for_the_quality_manager_and_decided(self):
        self.assertIsNone(self.good_doff["held"])
        self.assertEqual((self.good_doff["net_kg"], self.good_doff["mean_denier"]), ("56.000", "1001.67"))
        self.assertIn("The batch is HELD", self.held_doff["held"])
        self.assertEqual((self.held_doff["mean_denier"], self.decided["disposition"]), ("1085.00", "concession"))
        from apps.hr.models import Employee

        # The decider is the person the Quality Manager's login is (a party), not the login.
        self.assertEqual(self.decided["decided_by"], Employee.objects.get(employee_number="EMP-0300").party_id)

    def test_the_recall_holds_the_cloth_woven_from_the_rejected_tape(self):
        self.assertEqual((self.rejection["result"], self.rejection["disposition"]), ("fail", "reject"))
        [(code, lot)] = self.fabric_lots.items()
        self.assertEqual(code, self.weighed["code"])
        self.assertIn("made from TAPE-FOR-F-60 / T-1, rejected", lot["held_because"])

    def test_the_weavers_piece_work_is_paid_in_the_run(self):
        [line] = self.piece_slip["lines"]
        self.assertEqual((line["component"], line["quantity"], line["rate"], line["amount"]),
                         ("PIECE", "1006.0000", "0.5000", "503.00"))
        self.assertEqual((self.piece_slip["net"], self.bal(self.net_pay)), ("503.00", D("-503.00")))

    def test_the_runs_close_with_nothing_in_work_in_progress(self):
        self.assertEqual([row["status"] for row in self.closed], ["closed", "closed"])
        self.assertEqual(self.bal(self.wip), D("0"))

    def test_regrind_and_cloth_go_on_the_shelf_at_their_cost(self):
        self.assertEqual((self.regrind.on_hand_at(self.plant), self.regrind.stock_value_at(self.plant)),
                         (D("505"), D("30300.00")))  # 500 at 60.00 opening, 5 back at 60.00
        self.assertEqual(self.fabric.stock_value_at(self.plant), D("12783.67"))
        self.assertEqual(self.bal(self.scrap), D("0"))

    def test_a_rebatch_moves_stock_between_batches_and_no_value(self):
        self.assertEqual(self.rebatched, {"TP-A": D("30"), "TP-B": D("26")})
        self.assertEqual(self.tape_value_after_rebatch, self.tape_value_before_rebatch)
        self.assertEqual(self.inventory_after_rebatch, self.inventory_before_rebatch)

    def test_the_shelf_moves_with_the_inventory_account(self):
        """
        O269: two doffs of 56 kg at the run's planned 95.83505100 a kilo are each 5,366.762856.
        ProductionEntry.post books each rounded, 5,366.76, and each movement keeps that money, so the
        shelf moves 10,733.52 with the ledger rather than summing the unrounded 10,733.53. One
        instance of the shelf and the ledger keeping money to different places, fixed for every path.
        """
        moved = sum((item.stock_value_at(self.plant) for item in self.items), D("0")) - self.shelf_before
        self.assertEqual(self.bal(self.inventory), moved)


from apps.manufacturing.tests_jobwork import JobWorkTestCase


class JobWorkOutAndBackTests(JobWorkTestCase):
    """
    One challan out and back, on the outside-operation fixture's books (1 June 2026): the run of 1,000 kg of
    tape is coated by a laminator at an agreed 2.00 a kilo. The Purchasing Clerk orders the coating against
    the run's outside step; the Stores Manager sends the run's fabric on a challan (1,000 kg valued 95,000.00,
    5%); the coated 1,000 kg comes back on a receipt against the order, into the run: 2,000.00 into work in
    progress against GRNI; the bill clears GRNI. The run takes its material, books its output and closes.

    Model calls: the fixture's books and stock (as PlantFloorTests); the laminator made a vendor; the coating
    service item; GRNI and price variance named on the company.
    """

    def setUp(self):
        opening = patch("django.utils.timezone.now", return_value=at(TODAY, 6))
        opening.start()
        self.addCleanup(opening.stop)
        super().setUp()
        call_command("setup_roles", verbosity=0)
        clock = patch("django.utils.timezone.now", return_value=at(TODAY, 10))
        clock.start()
        self.addCleanup(clock.stop)
        from apps.accounting.models import Account, AccountType
        from apps.core.models import PartyRole, PartyRoleAssignment

        PartyRoleAssignment.objects.create(party=self.laminator, role=PartyRole.VENDOR)
        Company.objects.update(grni_account=self.grni, purchase_price_variance_account=self.variance)
        self.payable = Account.objects.create(code="2000", name="Payables", account_type=AccountType.LIABILITY)
        self.service = Item.objects.create(sku="SVC-COAT", name="Coating, by the kilo", uom=self.kg,
                                           track_inventory=False)
        self.people = {}
        self.items = [self.virgin, self.regrind, self.filler, self.colour, self.tape]
        self.shelf_before = sum((item.stock_value_at(self.plant) for item in self.items), D("0"))
        self.out_and_back()

    def as_(self, role):
        return PlantFloorTests.as_(self, role)

    def ok(self, response, status=(200, 201)):
        return Month.ok(self, response, status)

    def out_and_back(self):
        clerk, stores, store, supervisor = (self.as_(role) for role in (
            "Purchasing Clerk", "Stores Manager", "Warehouse Staff", "Production Supervisor"))
        po = self.ok(clerk.post("/api/purchasing/purchase-orders/", {
            "vendor": self.laminator.pk, "order_date": str(TODAY), "currency": self.usd.pk}, format="json"))
        self.ok(clerk.post("/api/purchasing/purchase-order-lines/", {
            "order": po["id"], "item": self.service.pk, "work_order_operation": self.coat.pk,
            "description": "Coating, 1,000 kg",
            "uom": self.kg.pk, "quantity": "1000", "unit_price": "2.00"}, format="json"))
        po = self.ok(clerk.post(f"/api/purchasing/purchase-orders/{po['id']}/confirm/"))
        challan = self.ok(stores.post("/api/manufacturing/job-work-challans/", {
            "job_worker": self.laminator.pk, "challan_date": str(TODAY)}, format="json"))
        self.ok(stores.post("/api/manufacturing/job-work-lines/", {
            "challan": challan["id"], "operation": self.coat.pk, "description": "Woven fabric for coating",
            "hsn_code": "63053300", "quantity": "1000", "value": "95000.00", "tax_rate": "5"}, format="json"))
        self.challan = self.ok(stores.post(f"/api/manufacturing/job-work-challans/{challan['id']}/post/"))
        receipt = self.ok(store.post("/api/purchasing/goods-receipts/", {
            "purchase_order": po["id"], "receipt_date": str(TODAY), "reference": "LAM/DC/55"}, format="json"))
        self.ok(store.post("/api/purchasing/goods-receipt-lines/", {
            "receipt": receipt["id"], "order_line": po["lines"][0]["id"], "warehouse": self.plant.pk,
            "quantity_received": "1000"},
            format="json"))
        self.ok(store.post(f"/api/purchasing/goods-receipts/{receipt['id']}/post_receipt/"))
        self.grni_after_receipt = balance(self.grni)
        from apps.manufacturing.jobwork import allocation

        self.back = allocation(self.lamination.operations.get(is_outside=True))
        bill = self.ok(clerk.post(f"/api/purchasing/purchase-orders/{po['id']}/create_bill/", {
            "payable_account": self.payable.pk, "bill_date": str(TODAY), "reference": "LAM/26/310"}, format="json"))
        self.bill = self.ok(self.as_("AP Manager").post(f"/api/purchasing/bills/{bill['id']}/post_bill/"))
        run = f"/api/manufacturing/work-orders/{self.lamination.pk}/"
        issue = self.ok(supervisor.post("/api/manufacturing/material-issues/", {
            "work_order": self.lamination.pk, "issue_date": str(TODAY), "warehouse": self.plant.pk}, format="json"))
        for number, (item, quantity) in enumerate(((self.virgin, "750"), (self.regrind, "150"), (self.filler, "80"),
                                                   (self.colour, "20")), start=1):
            self.ok(supervisor.post("/api/manufacturing/material-issue-lines/", {
                "issue": issue["id"], "item": item.pk, "quantity": quantity, "uom": self.kg.pk,
                "line_number": number}, format="json"))
        self.ok(supervisor.post(f"/api/manufacturing/material-issues/{issue['id']}/post/"))
        entry = self.ok(supervisor.post("/api/manufacturing/production-entries/", {
            "work_order": self.lamination.pk, "entry_date": str(TODAY), "warehouse": self.plant.pk,
            "quantity_produced": "1000", "uom": self.kg.pk}, format="json"))
        self.ok(supervisor.post(f"/api/manufacturing/production-entries/{entry['id']}/post/"))
        self.closed = self.ok(supervisor.post(run + "close/", {"date": str(TODAY)}, format="json"))


    def test_the_challan_goes_out_numbered_with_the_job_workers_registration(self):
        [line] = self.challan["lines"]
        self.assertEqual((self.challan["posted"], self.challan["number"][:4], self.challan["job_worker_gstin"]),
                         (True, "JWC-", "29AABCE5678F1ZD"))
        self.assertEqual((line["quantity"], line["value"], line["tax_rate"]), ("1000.0000", "95000.00", "5.00"))

    def test_what_went_out_came_back(self):
        [row] = self.back.values()
        self.assertEqual((D(row["sent"]), D(row["back"]), D(row["outstanding"])), (D("1000"), D("1000"), D("0")))

    def test_the_coating_accrues_into_the_run_and_the_bill_clears_it(self):
        self.assertEqual((self.grni_after_receipt, self.bill["total"], balance(self.grni)),
                         (D("-2000.00"), "2000.00", D("0")))

    def test_the_run_closes_with_nothing_in_work_in_progress_and_the_shelf_foots(self):
        self.assertEqual((self.closed["status"], balance(self.wip)), ("closed", D("0")))
        moved = sum((item.stock_value_at(self.plant) for item in self.items), D("0")) - self.shelf_before
        self.assertEqual(balance(self.inventory), moved)
