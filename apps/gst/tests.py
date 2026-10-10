import datetime
from decimal import Decimal

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounting.gst import GstSettings, gstin_check_character
from apps.accounting.models import (
    Account,
    AccountType,
    ChargeType,
    FiscalPosition,
    FiscalPositionTaxMapping,
    JournalLine,
    PartyTaxProfile,
    Tax,
)
from apps.core.models import (
    Company,
    Currency,
    ExchangeRate,
    Party,
    PartyRole,
    PartyRoleAssignment,
    UnitOfMeasure,
)
from apps.inventory.models import Item, ItemUnit
from apps.purchasing.models import Bill, BillLine, PurchaseOrder, PurchaseOrderLine
from apps.sales.models import Invoice, InvoiceLine, SalesOrder, SalesOrderLine

from .models import UnitQuantityCode
from .returns import gstr1, gstr1_json, gstr3b, month

SEPTEMBER = month("2026-09")
DAY = datetime.date(2026, 9, 10)


def gstin(first_fourteen):
    return first_fourteen + gstin_check_character(first_fourteen)


def D(value):
    return Decimal(value)


class GstReturnTestCase(TestCase):
    def setUp(self):
        self.inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        self.usd = Currency.objects.create(code="USD", name="US Dollar")
        ExchangeRate.objects.create(currency=self.usd, rate=D("83"),
                                    valid_from=datetime.date(2026, 1, 1))
        Company.objects.create(name="Deccan Polysacks", base_currency=self.inr)

        kg = UnitOfMeasure.objects.create(code="kg", name="Kilogram")
        UnitQuantityCode.objects.create(uom=kg, code="KGS")
        self.sack = Item.objects.create(sku="SACK", name="Woven sack", uom=kg,
                                        hsn_code="63053300")

        def account(code, kind):
            return Account.objects.create(code=code, name=code, account_type=kind)

        self.ar = account("1100", AccountType.ASSET)
        self.ap = account("2000", AccountType.LIABILITY)
        self.revenue = account("4000", AccountType.INCOME)
        self.expense = account("5000", AccountType.EXPENSE)
        self.freight = ChargeType.objects.create(
            code="FRT", name="Freight", revenue_account=self.revenue, hsn_code="9965"
        )

        def tax(code, rate, head, out, into):
            return Tax.objects.create(
                code=code, name=code, rate=D(rate), gst_head=head,
                collected_account=account(out, AccountType.LIABILITY),
                paid_account=account(into, AccountType.ASSET),
            )

        self.cgst = tax("CGST9", "9", "cgst", "2201", "1311")
        self.sgst = tax("SGST9", "9", "sgst", "2202", "1312")
        self.igst = tax("IGST18", "18", "igst", "2203", "1313")
        self.zero = tax("IGST0", "0", "igst", "2204", "1314")
        self.pair = [self.cgst, self.sgst]

        def position(code, cgst_to):
            fiscal = FiscalPosition.objects.create(code=code, name=code)
            FiscalPositionTaxMapping.objects.create(
                fiscal_position=fiscal, source_tax=self.cgst, target_tax=cgst_to
            )
            FiscalPositionTaxMapping.objects.create(
                fiscal_position=fiscal, source_tax=self.sgst, target_tax=None
            )
            return fiscal

        inter = position("INTER", self.igst)
        self.lut = position("LUT", self.zero)
        self.settings = GstSettings.objects.create(
            gstin=gstin("27AABCD1234E1Z"), interstate_position=inter
        )

    def party(self, code, role=PartyRole.CUSTOMER, currency=None, **profile):
        party = Party.objects.create(code=code, name=code,
                                     default_currency=currency or self.inr)
        PartyRoleAssignment.objects.create(party=party, role=role)
        PartyTaxProfile.objects.create(party=party, **profile)
        return party

    def sell(self, customer, price, quantity="1", taxes=None, currency=None, day=DAY,
             freight=None):
        invoice = Invoice.objects.create(
            customer=customer, invoice_date=day, receivable_account=self.ar,
            currency=currency or self.inr,
        )
        line = InvoiceLine.objects.create(
            invoice=invoice, item=self.sack, quantity=D(quantity), unit_price=D(price),
            revenue_account=self.revenue,
        )
        line.taxes.set(self.pair if taxes is None else taxes)
        if freight:
            charge = InvoiceLine.objects.create(
                invoice=invoice, charge=self.freight, quantity=D("1"),
                unit_price=D(freight), revenue_account=self.revenue,
            )
            charge.taxes.set(self.pair)
        invoice.post()
        return invoice

    def buy(self, vendor, price, quantity="1", taxes=None, reference=""):
        bill = Bill.objects.create(vendor=vendor, bill_date=DAY, payable_account=self.ap,
                                   reference=reference)
        line = BillLine.objects.create(
            bill=bill, item=self.sack, quantity=D(quantity), unit_price=D(price),
            expense_account=self.expense,
        )
        line.taxes.set(self.pair if taxes is None else taxes)
        bill.post()
        return bill

    def ledger(self, code):
        rows = JournalLine.objects.filter(account__code=code, entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit"))
        return (rows["credit"] or D("0")) - (rows["debit"] or D("0"))


class MonthTests(TestCase):
    def test_a_month(self):
        self.assertEqual(month("2026-02"),
                         (datetime.date(2026, 2, 1), datetime.date(2026, 2, 28)))

    def test_not_a_month(self):
        for bad in ["2026", "2026-13", "Sept", None]:
            with self.subTest(period=bad), self.assertRaises(ValidationError):
                month(bad)


class SeptemberReturnTests(GstReturnTestCase):
    """One month of a sack plant's trade, every table touched."""

    def setUp(self):
        super().setUp()
        mh = self.party("MH-REG", gstin=gstin("27AABCM1111A1Z"))
        ka = self.party("KA-REG", gstin=gstin("29AABCK2222B1Z"))
        ka_retail = self.party("KA-UNREG", gst_state="29", gst_registration="unregistered")
        mh_retail = self.party("MH-UNREG", gst_state="27", gst_registration="unregistered")
        sez = self.party("SEZ", gstin=gstin("24AABCS3333C1Z"), gst_registration="sez",
                         fiscal_position=self.lut)
        abroad = self.party("EXPORT", currency=self.usd, gst_registration="overseas",
                            fiscal_position=self.lut)
        exempt = self.party("EXEMPT", gst_state="27", gst_registration="unregistered",
                            tax_exempt=True, exemption_reference="NOTIF-2/2017")

        self.a = self.sell(mh, "1000", freight="100")
        self.b = self.sell(ka, "1000", quantity="2")
        self.c = self.sell(ka_retail, "150000")
        self.d = self.sell(mh_retail, "500")
        self.e = self.sell(ka_retail, "800")
        self.f = self.sell(sez, "5000")
        self.g = self.sell(abroad, "100", currency=self.usd)
        self.h = self.sell(exempt, "300")
        self.b_note = self.b.create_credit_note(
            quantities={self.b.lines.get(): D("1")})
        self.c_note = self.c.create_credit_note()
        self.d_note = self.d.create_credit_note()
        for note in (self.b_note, self.c_note, self.d_note):
            Invoice.objects.filter(pk=note.pk).update(invoice_date=DAY)
        self.sell(mh, "999", day=datetime.date(2026, 10, 1))  # next month

        ka_mill = self.party("KA-MILL", role=PartyRole.VENDOR,
                             gstin=gstin("29AABCV4444D1Z"))
        backyard = self.party("BACKYARD", role=PartyRole.VENDOR, gst_state="27",
                              gst_registration="unregistered")
        self.bill = self.buy(ka_mill, "100", quantity="10", reference="KM-1")
        self.bill_note = self.bill.create_debit_note(
            quantities={self.bill.lines.get(): D("2")})
        Bill.objects.filter(pk=self.bill_note.pk).update(bill_date=DAY)
        self.buy(backyard, "1000", reference="BY-1")
        self.buy(ka_mill, "250", taxes=[], reference="KM-2")

    def items(self, **totals):
        row = {"rate": D("18"), "taxable": D("0"), "igst": D("0"), "cgst": D("0"),
               "sgst": D("0"), "cess": D("0")}
        return row | {key: D(value) for key, value in totals.items()}

    def test_b2b_is_invoice_by_invoice_with_sez(self):
        b2b = {row["number"]: row for row in gstr1(*SEPTEMBER)["b2b"]}

        self.assertEqual(set(b2b), {self.a.number, self.b.number, self.f.number})
        self.assertEqual(b2b[self.a.number]["type"], "regular")
        self.assertEqual(b2b[self.a.number]["items"],
                         [self.items(taxable="1100", cgst="99", sgst="99")])
        self.assertEqual(b2b[self.a.number]["value"], D("1298.00"))
        self.assertEqual(b2b[self.b.number]["items"], [self.items(taxable="2000", igst="360")])
        self.assertEqual(b2b[self.b.number]["place_of_supply"], "29")
        self.assertEqual(b2b[self.f.number]["type"], "sez_without_payment")
        self.assertEqual(b2b[self.f.number]["items"],
                         [self.items(rate="0", taxable="5000")])

    def test_a_large_inter_state_sale_to_the_unregistered_is_b2cl(self):
        (row,) = gstr1(*SEPTEMBER)["b2cl"]
        self.assertEqual((row["number"], row["place_of_supply"]), (self.c.number, "29"))
        self.assertEqual(row["items"], [self.items(taxable="150000", igst="27000")])

    def test_b2cs_is_a_summary_with_credit_notes_netted(self):
        """D and its credit note cancel; only E is left."""
        self.assertEqual(gstr1(*SEPTEMBER)["b2cs"], [
            {"supply": "inter", "place_of_supply": "29"}
            | self.items(taxable="800", igst="144"),
        ])

    def test_an_export_is_valued_in_rupees(self):
        (row,) = gstr1(*SEPTEMBER)["exp"]
        self.assertEqual((row["type"], row["value"]), ("without_payment", D("8300.00")))
        self.assertEqual(row["place_of_supply"], "96")
        self.assertEqual(row["items"], [self.items(rate="0", taxable="8300")])

    def test_credit_notes_follow_their_invoices(self):
        result = gstr1(*SEPTEMBER)
        (registered,) = result["cdnr"]
        self.assertEqual((registered["number"], registered["original"]),
                         (self.b_note.number, self.b.number))
        self.assertEqual(registered["items"], [self.items(taxable="1000", igst="180")])
        (unregistered,) = result["cdnur"]
        self.assertEqual((unregistered["number"], unregistered["type"]),
                         (self.c_note.number, "b2cl"))

    def test_exempt_supplies_go_to_the_nil_table(self):
        self.assertEqual(gstr1(*SEPTEMBER)["nil"], {
            "intra_unregistered": {"nil": D("0"), "exempt": D("300"), "non_gst": D("0")},
        })

    def test_the_hsn_summary_is_split_and_nets_credit_notes(self):
        result = gstr1(*SEPTEMBER)

        def rows(table):
            return {(r["hsn"], r["uqc"], r["rate"]): (r["quantity"], r["taxable"], r["igst"],
                                                      r["cgst"]) for r in result[table]}

        self.assertEqual(rows("hsn_b2b"), {
            ("63053300", "KGS", D("0")): (D("1"), D("5000"), D("0"), D("0")),
            ("63053300", "KGS", D("18")): (D("2"), D("2000"), D("180"), D("90")),
            ("9965", "NA", D("18")): (D("0"), D("100"), D("0"), D("9")),
        })
        self.assertEqual(rows("hsn_b2c"), {
            ("63053300", "KGS", D("0")): (D("2"), D("8600"), D("0"), D("0")),
            ("63053300", "KGS", D("18")): (D("1"), D("800"), D("144"), D("0")),
        })

    def test_documents_issued(self):
        issued = {row["kind"]: row["total"] for row in gstr1(*SEPTEMBER)["documents"]}
        self.assertEqual(issued, {"invoices": 8, "credit_notes": 3})

    def test_each_nature_of_document_goes_out_as_its_own_number(self):
        details = gstr1_json(gstr1(*SEPTEMBER))["doc_issue"]["doc_det"]
        self.assertEqual([(row["doc_num"], row["docs"][0]["totnum"]) for row in details], [(1, 8), (5, 3)])

    def test_3b_outward_tax_is_what_the_ledger_holds(self):
        result = gstr3b(*SEPTEMBER)
        taxed = result["3.1a"]

        self.assertEqual(taxed["taxable"], D("2900"))
        self.assertEqual((taxed["igst"], taxed["cgst"], taxed["sgst"]),
                         (D("324"), D("99"), D("99")))
        self.assertEqual(result["3.1b"]["taxable"], D("13300"))
        self.assertEqual(result["3.1c"]["taxable"], D("300"))
        self.assertEqual(result["3.2"],
                         [{"place_of_supply": "29", "taxable": D("800"), "igst": D("144")}])
        # September's ledger less October's one invoice (89.91 each way).
        self.assertEqual(self.ledger("2203"), taxed["igst"] + result["3.1b"]["igst"])
        self.assertEqual(self.ledger("2201") - D("89.91"), taxed["cgst"])
        self.assertEqual(self.ledger("2202") - D("89.91"), taxed["sgst"])

    def test_3b_credit_is_claimed_only_from_registered_suppliers(self):
        result = gstr3b(*SEPTEMBER)

        self.assertEqual(result["4A5"], {"igst": D("144"), "cgst": D("0"), "sgst": D("0"),
                                         "cess": D("0")})
        self.assertEqual(-self.ledger("1313"), D("144"))
        # The backyard supplier's 90 + 90 sits in the ledger, unclaimed, and said so.
        self.assertEqual(-self.ledger("1311"), D("90"))
        self.assertEqual(result["warnings"], [
            f"{Bill.objects.get(reference='BY-1').number}: the supplier's registration is "
            "unregistered, so tax on its bill is not claimed as credit."
        ])
        self.assertEqual(result["5"], {"inter": D("250"), "intra": D("0")})

    def test_a_return_does_not_move_when_the_world_does(self):
        before = (gstr1(*SEPTEMBER), gstr3b(*SEPTEMBER))

        profile = PartyTaxProfile.objects.get(party__code="MH-REG")
        profile.gstin = gstin("29AABCM1111A1Z")
        profile.gst_state = ""
        profile.save()
        self.sack.hsn_code = "39269099"
        self.sack.save()
        Tax.objects.filter(pk=self.igst.pk).update(rate=D("12"), gst_head="")

        self.assertEqual((gstr1(*SEPTEMBER), gstr3b(*SEPTEMBER)), before)

    def test_the_offline_tool_shape(self):
        data = gstr1_json(gstr1(*SEPTEMBER))

        self.assertEqual(data["fp"], "092026")
        self.assertEqual(data["gstin"], self.settings.gstin)
        ctins = {entry["ctin"] for entry in data["b2b"]}
        self.assertEqual(len(ctins), 3)
        sez = next(inv for entry in data["b2b"] for inv in entry["inv"]
                   if inv["inum"] == self.f.number)
        self.assertEqual((sez["inv_typ"], sez["idt"], sez["pos"]), ("SEWOP", "10-09-2026", "24"))
        self.assertEqual(sez["itms"][0]["itm_det"]["txval"], 5000.0)
        self.assertEqual(data["exp"][0]["exp_typ"], "WOPAY")
        self.assertEqual(data["b2cs"][0]["sply_ty"], "INTER")
        self.assertEqual(data["cdnur"][0]["typ"], "B2CL")
        self.assertEqual(data["nil"]["inv"][0]["sply_ty"], "INTRAB2C")
        self.assertEqual(len(data["hsn"]["b2b"]), 3)


class RefusalTests(GstReturnTestCase):
    def test_a_period_holding_an_unrecorded_document_is_refused(self):
        customer = self.party("MH", gstin=gstin("27AABCM1111A1Z"))
        invoice = self.sell(customer, "1000")
        Invoice.objects.filter(pk=invoice.pk).update(taxes_recorded=False)

        with self.assertRaisesMessage(ValidationError, invoice.number):
            gstr1(*SEPTEMBER)
        with self.assertRaisesMessage(ValidationError, "before taxes were recorded"):
            gstr3b(*SEPTEMBER)

    def test_an_unrecorded_bill_is_refused_too(self):
        vendor = self.party("V", role=PartyRole.VENDOR, gstin=gstin("29AABCV4444D1Z"))
        bill = self.buy(vendor, "100")
        Bill.objects.filter(pk=bill.pk).update(taxes_recorded=False)
        with self.assertRaisesMessage(ValidationError, bill.number):
            gstr3b(*SEPTEMBER)

    def test_no_gst_no_return(self):
        GstSettings.objects.update(is_active=False)
        with self.assertRaisesMessage(ValidationError, "not set up"):
            gstr1(*SEPTEMBER)

    def test_down_payments_and_their_credit_notes_are_not_supplies(self):
        customer = self.party("MH", gstin=gstin("27AABCM1111A1Z"))
        deposits = Account.objects.create(code="2300", name="Deposits",
                                          account_type=AccountType.LIABILITY)
        company = Company.get()
        company.customer_deposit_account = deposits
        company.save()
        order = SalesOrder.objects.create(customer=customer, order_date=DAY)
        SalesOrderLine.objects.create(order=order, item=self.sack, uom=self.sack.uom, quantity=D("1"),
                                      unit_price=D("5000"), revenue_account=self.revenue)
        order.confirm()  # an advance is taken on an order agreed, not on a draft
        deposit = Invoice.objects.create(customer=customer, invoice_date=DAY, sales_order=order,
                                         receivable_account=self.ar, is_down_payment=True)
        InvoiceLine.objects.create(invoice=deposit, description="Advance", quantity=D("1"),
                                   unit_price=D("5000"), revenue_account=deposits)
        deposit.post()
        note = deposit.create_credit_note()
        Invoice.objects.filter(pk=note.pk).update(invoice_date=DAY)

        result = gstr1(*SEPTEMBER)
        self.assertEqual(result["nil"], {})
        # Neither is a supply, but each took a number in its series, and table 13
        # accounts for every number given out (O59).
        self.assertEqual([(row["kind"], row["total"]) for row in result["documents"]],
                         [("invoices", 1), ("credit_notes", 1)])
        self.assertEqual(gstr3b(*SEPTEMBER)["3.1c"]["taxable"], D("0"))

    def test_the_registration_cannot_move_under_recorded_documents(self):
        settings = GstSettings.objects.get()
        settings.gstin = gstin("29AABCD1234E1Z")
        settings.save()  # nothing recorded yet: a typo can still be fixed
        self.sell(self.party("MH", gstin=gstin("29AABCM1111A1Z")), "100")
        settings.gstin = gstin("27AABCD1234E1Z")
        with self.assertRaisesMessage(ValidationError, "must not change with it"):
            settings.save()

    def test_prepayments_and_their_debit_notes_are_not_supplies(self):
        vendor = self.party("V", role=PartyRole.VENDOR, gstin=gstin("27AABCV1111A1Z"))
        prepaid = Account.objects.create(code="1400", name="Prepaid",
                                         account_type=AccountType.ASSET)
        company = Company.get()
        company.vendor_prepayment_account = prepaid
        company.save()
        order = PurchaseOrder.objects.create(vendor=vendor, order_date=DAY)
        PurchaseOrderLine.objects.create(order=order, item=self.sack, uom=self.sack.uom, quantity=D("1"),
                                         unit_price=D("5000"))
        order.confirm()  # an advance is paid on an order agreed, not on a draft
        prepayment = Bill.objects.create(vendor=vendor, bill_date=DAY, payable_account=self.ap,
                                         purchase_order=order, is_prepayment=True)
        BillLine.objects.create(bill=prepayment, description="Advance", quantity=D("1"),
                                unit_price=D("5000"), expense_account=prepaid)
        prepayment.post()
        note = prepayment.create_debit_note()
        Bill.objects.filter(pk=note.pk).update(bill_date=DAY)

        result = gstr3b(*SEPTEMBER)
        self.assertEqual(result["5"], {"inter": D("0"), "intra": D("0")})
        self.assertEqual(result["4A5"], {"igst": D("0"), "cgst": D("0"), "sgst": D("0"),
                                         "cess": D("0")})

    def test_a_composition_or_sez_supplier_is_not_claimed(self):
        for code, registration, first in [("COMP", "composition", "27AABCC5555E1Z"),
                                          ("SEZV", "sez", "24AABCZ6666F1Z")]:
            vendor = self.party(code, role=PartyRole.VENDOR, gstin=gstin(first),
                                gst_registration=registration)
            self.buy(vendor, "100", reference=code)
        result = gstr3b(*SEPTEMBER)
        self.assertEqual(result["4A5"]["cgst"] + result["4A5"]["igst"], D("0"))
        self.assertEqual(len(result["warnings"]), 2)

    def test_warnings_for_what_the_portal_will_reject(self):
        customer = self.party("MH", gstin=gstin("27AABCM1111A1Z"))
        Item.objects.filter(pk=self.sack.pk).update(hsn_code="")
        self.sack.refresh_from_db()
        invoice = self.sell(customer, "1000")
        Invoice.objects.filter(pk=invoice.pk).update(number="INV-2026-000000001")

        warnings = gstr1(*SEPTEMBER)["warnings"]
        self.assertTrue(any("no HSN" in warning for warning in warnings))
        self.assertTrue(any("longer than the 16" in warning for warning in warnings))

    def test_a_unit_with_no_gst_code_is_reported_as_other(self):
        customer = self.party("MH", gstin=gstin("27AABCM1111A1Z"))
        UnitQuantityCode.objects.all().delete()
        self.sell(customer, "1000")

        result = gstr1(*SEPTEMBER)
        self.assertEqual(result["hsn_b2b"][0]["uqc"], "OTH")
        self.assertTrue(any("no GST unit code" in warning for warning in result["warnings"]))


class ReturnApiTests(GstReturnTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("accountant")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def grant(self):
        self.user.user_permissions.add(Permission.objects.get(codename="compile_returns"))
        self.user = User.objects.get(pk=self.user.pk)
        self.client.force_authenticate(self.user)

    def test_compiling_a_return_takes_its_own_permission(self):
        self.assertEqual(self.client.get("/api/gst/gstr1/?period=2026-09").status_code, 403)

    def test_each_return(self):
        self.grant()
        for path in ("gstr1", "gstr1/json", "gstr3b"):
            with self.subTest(path=path):
                response = self.client.get(f"/api/gst/{path}/?period=2026-09")
                self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.client.get("/api/gst/gstr1/json/?period=2026-09").json()["fp"],
                         "092026")

    def test_a_bad_period_or_no_gst_is_a_sentence_not_a_crash(self):
        self.grant()
        self.assertEqual(self.client.get("/api/gst/gstr3b/").status_code, 400)
        self.assertEqual(self.client.get("/api/gst/gstr3b/?period=Sept").status_code, 400)
        GstSettings.objects.update(is_active=False)
        response = self.client.get("/api/gst/gstr3b/?period=2026-09")
        self.assertEqual(response.status_code, 400)
        self.assertIn("not set up", str(response.content))


class EdgeTests(GstReturnTestCase):
    def items(self, **totals):
        row = {"rate": D("18"), "taxable": D("0"), "igst": D("0"), "cgst": D("0"),
               "sgst": D("0"), "cess": D("0")}
        return row | {key: D(value) for key, value in totals.items()}

    def test_the_hsn_summary_counts_in_the_unit_the_quantity_is_in(self):
        pieces = UnitOfMeasure.objects.create(code="pcs", name="Pieces")
        UnitQuantityCode.objects.create(uom=pieces, code="PCS")
        ItemUnit.objects.create(item=self.sack, uom=pieces, factor=D("0.1"))
        customer = self.party("MH", gstin=gstin("27AABCM1111A1Z"))
        order = SalesOrder.objects.create(customer=customer, order_date=DAY, currency=self.inr)
        line = SalesOrderLine.objects.create(order=order, item=self.sack, uom=pieces,
                                             quantity=D("100"), unit_price=D("12"),
                                             revenue_account=self.revenue)
        line.taxes.set(self.pair)
        order.confirm()
        invoice = order.create_invoice(self.ar, invoice_date=DAY)
        invoice.post()
        rows = {(r["hsn"], r["uqc"]): r["quantity"] for r in gstr1(*SEPTEMBER)["hsn_b2b"]}
        self.assertEqual(rows, {("63053300", "PCS"): D("100")})

    def test_a_large_sale_inside_the_state_stays_in_the_summary(self):
        retail = self.party("MH-UNREG", gst_state="27", gst_registration="unregistered")
        self.sell(retail, "200000")
        result = gstr1(*SEPTEMBER)
        self.assertEqual(result["b2cl"], [])
        self.assertEqual(result["b2cs"], [
            {"supply": "intra", "place_of_supply": "27"}
            | self.items(taxable="200000", cgst="18000", sgst="18000"),
        ])

    def test_the_b2cl_limit_is_on_invoice_value_and_is_exclusive(self):
        """84,745.76 + 18% is exactly 1,00,000.00: not more than the limit."""
        retail = self.party("KA-UNREG", gst_state="29", gst_registration="unregistered")
        at_limit = self.sell(retail, "84745.76")
        over = self.sell(retail, "84745.77")
        self.assertEqual(at_limit.total(), D("100000.00"))

        result = gstr1(*SEPTEMBER)
        self.assertEqual([row["number"] for row in result["b2cl"]], [over.number])
        self.assertEqual(result["b2cs"][0]["taxable"], D("84745.76"))

    def test_nil_rated_and_non_gst_are_told_apart(self):
        customer = self.party("MH-REG", gstin=gstin("27AABCM1111A1Z"))
        duty = Tax.objects.create(code="DUTY", name="Duty", rate=D("0"), gst_head="other",
                                  collected_account=self.revenue, paid_account=self.expense)
        self.sell(customer, "400", taxes=[self.zero])
        self.sell(customer, "70", taxes=[duty])

        self.assertEqual(gstr1(*SEPTEMBER)["nil"], {
            "intra_registered": {"nil": D("400"), "exempt": D("0"), "non_gst": D("70")},
        })
        result = gstr3b(*SEPTEMBER)
        self.assertEqual((result["3.1c"]["taxable"], result["3.1e"]["taxable"]),
                         (D("400"), D("70")))
        self.assertEqual(result["3.1a"]["taxable"], D("0"))

    def test_cess_is_its_own_column_not_part_of_the_rate(self):
        customer = self.party("KA-REG", gstin=gstin("29AABCK2222B1Z"))
        cess = Tax.objects.create(code="CESS1", name="Cess", rate=D("1"), gst_head="cess",
                                  collected_account=self.igst.collected_account,
                                  paid_account=self.igst.paid_account)
        self.sell(customer, "1000", taxes=[self.igst, cess])

        (row,) = gstr1(*SEPTEMBER)["b2b"]
        self.assertEqual(row["items"], [self.items(taxable="1000", igst="180", cess="10")])

    def test_zero_rated_supplies_with_tax_paid(self):
        """An SEZ unit or an export with IGST paid rather than under LUT."""
        sez = self.party("SEZ", gstin=gstin("24AABCS3333C1Z"), gst_registration="sez")
        abroad = self.party("EXPORT", gst_registration="overseas")
        self.sell(sez, "1000")
        self.sell(abroad, "2000")

        result = gstr1(*SEPTEMBER)
        self.assertEqual(result["b2b"][0]["type"], "sez_with_payment")
        self.assertEqual(result["exp"][0]["type"], "with_payment")
        self.assertEqual(result["exp"][0]["place_of_supply"], "96")
        summary = gstr3b(*SEPTEMBER)
        self.assertEqual((summary["3.1b"]["taxable"], summary["3.1b"]["igst"]),
                         (D("3000"), D("540")))
        self.assertEqual(summary["3.1a"]["taxable"], D("0"))
        self.assertEqual(gstr1_json(result)["exp"][0]["exp_typ"], "WPAY")

    def test_a_composition_buyer_across_the_line_is_in_table_3_2(self):
        dealer = self.party("COMP", gstin=gstin("29AABCC5555E1Z"),
                            gst_registration="composition")
        registered = self.party("KA-REG", gstin=gstin("29AABCK2222B1Z"))
        self.sell(dealer, "1000")
        self.sell(registered, "5000")

        self.assertEqual(gstr3b(*SEPTEMBER)["3.2"],
                         [{"place_of_supply": "29", "taxable": D("1000"), "igst": D("180")}])

    def test_a_document_with_no_place_of_supply_is_flagged(self):
        nowhere = self.party("NOWHERE")
        invoice = self.sell(nowhere, "100", taxes=[])
        result = gstr1(*SEPTEMBER)
        self.assertIn(
            f"{invoice.number} has no place of supply: its party had no state when it posted.",
            result["warnings"],
        )
        self.assertEqual(list(result["nil"]), ["intra_unregistered"])

    def test_a_note_against_an_invoice_from_before_recording(self):
        customer = self.party("MH-REG", gstin=gstin("27AABCM1111A1Z"))
        august = self.sell(customer, "1000", day=datetime.date(2026, 8, 20))
        august.lines.get().recorded_taxes.all().delete()
        Invoice.objects.filter(pk=august.pk).update(
            taxes_recorded=False, party_gstin="", party_registration="", place_of_supply="",
        )
        note = Invoice.objects.get(pk=august.pk).create_credit_note()
        Invoice.objects.filter(pk=note.pk).update(invoice_date=DAY)

        (row,) = gstr1(*SEPTEMBER)["cdnr"]
        self.assertEqual((row["number"], row["original"]), (note.number, august.number))
        self.assertEqual(row["items"], [self.items(taxable="1000", cgst="90", sgst="90")])

    def test_services_report_no_unit(self):
        """A service's quantity means nothing to the portal: NA and nil,
        whether billed as a charge or as a service item."""
        customer = self.party("MH-REG", gstin=gstin("27AABCM1111A1Z"))
        job_work = Item.objects.create(sku="JOB", name="Job work", uom=self.sack.uom,
                                       hsn_code="998821")
        ChargeType.objects.filter(pk=self.freight.pk).update(hsn_code="")
        invoice = Invoice.objects.create(customer=customer, invoice_date=DAY,
                                         receivable_account=self.ar, currency=self.inr)
        for kwargs in ({"item": job_work}, {"charge": self.freight}):
            line = InvoiceLine.objects.create(
                invoice=invoice, quantity=D("5"), unit_price=D("100"),
                revenue_account=self.revenue, **kwargs,
            )
            line.taxes.set(self.pair)
        invoice.post()

        rows = {r["hsn"]: (r["uqc"], r["quantity"]) for r in gstr1(*SEPTEMBER)["hsn_b2b"]}
        self.assertEqual(rows, {"998821": ("NA", D("0")), "": ("NA", D("0"))})

    def test_non_gst_purchases_are_their_own_row_of_table_5(self):
        vendor = self.party("KA-MILL", role=PartyRole.VENDOR, gstin=gstin("29AABCV4444D1Z"))
        duty = Tax.objects.create(code="DUTY", name="Duty", rate=D("0"), gst_head="other",
                                  collected_account=self.revenue, paid_account=self.expense)
        self.buy(vendor, "70", taxes=[duty], reference="D-1")
        self.buy(vendor, "30", taxes=[], reference="D-2")

        result = gstr3b(*SEPTEMBER)
        self.assertEqual(result["5"], {"inter": D("30"), "intra": D("0")})
        self.assertEqual(result["5_non_gst"], {"inter": D("70"), "intra": D("0")})


class DocumentsIssuedTests(GstReturnTestCase):
    """
    September: an invoice, a down payment on an order, another invoice. The
    three take INV numbers one after another, and table 13 says the series
    ran from the first to the last: 3 issued, none cancelled
    (calc_stat/o59_table13.py).
    """

    def test_the_invoice_series_reported_holds_every_number_in_its_range(self):
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
        self.assertEqual((row["from"], row["to"], row["total"], row["cancelled"]), (first.number, last.number, 3, 0))
        self.assertEqual(deposit.number, "INV-2026-00002")
