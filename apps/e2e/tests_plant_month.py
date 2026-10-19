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
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase
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
        self.pp = Item.objects.create(sku="PP-H030", name="PP granules H030SG", uom=self.kg, hsn_code="3902")
        self.mb = Item.objects.create(sku="MB-WHT", name="White masterbatch", uom=self.kg, hsn_code="3206")
        self.tape = Item.objects.create(sku="TAPE-90", name="PP tape 900 denier", uom=self.kg, hsn_code="5407")
        self.sweep = Item.objects.create(sku="SWEEP", name="Floor sweepings", uom=self.kg, hsn_code="3915")
        self.contract = TdsSection.objects.create(
            code="194C", name="Contractors", mode="whole", single_threshold=D("30000"),
            annual_threshold=D("100000"), payable_account=acct["2250"])
        self.pp_vendor = self.party("V-PP", "Gujarat Polymers", PartyRole.VENDOR, gstin=gstin("24AAACG1111A1Z"),
                                    pan="AAACG1111A")
        self.mb_vendor = self.party("V-MB", "Howrah Colours", PartyRole.VENDOR, gstin=gstin("19AAACM2222B1Z"),
                                    pan="AAACM2222B", msme_category="micro", udyam_number="UDYAM-WB-10-0012345")
        self.transporter = self.party("V-TRN", "Durgapur Roadways", PartyRole.VENDOR, gstin=gstin("19AAACT3333C1Z"),
                                      pan="AAACT3333C", tds_section=self.contract)

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
        self.mb_bill = self.get("AP Manager", f"/api/purchasing/bills/{self.mb_bill['id']}/")
        self.msme = self.get("AP Manager", "/api/purchasing/purchasing-reports/msme/",
                             {"start": "2026-10-01", "end": "2026-10-31", "as_of": "2026-10-31"})

    def run(self):
        self.open_books()
        self.confirm_rates()
        self.buy_polymer()
        self.buy_masterbatch()
        self.freight()
        self.pay_masterbatch()
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
        self.assertEqual((self.m.pp.on_hand_at(self.m.store), self.m.pp.stock_value_at(self.m.store)),
                         (D("9800"), D("1019200.00")))
        self.assertEqual((self.m.mb.on_hand_at(self.m.store), self.m.mb.stock_value_at(self.m.store)),
                         (D("450"), D("135000.00")))

