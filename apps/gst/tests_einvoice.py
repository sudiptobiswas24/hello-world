"""
E-invoices and e-way bills for a Maharashtra plant's invoices.

  S1  Karnataka buyer, inter-state. 1,000 kg of sacks at 125, 2% off:
      125,000.00 less 2,500.00 is 122,500.00, IGST 18% 22,050.00.
      Freight 3,000.00 (SAC 996511), IGST 540.00.
      Assessable 125,500.00, IGST 22,590.00, invoice 148,090.00.
      On the lorry: goods 122,500.00 + 22,050.00; the other 3,540.00 is
      the freight and its tax.
  S2  Maharashtra buyer: 100 kg at 150 = 15,000.00, CGST and SGST
      1,350.00 each, 17,700.00. Under the 50,000 limit.
  S3  Export at 83.456789 to the dollar: 1,000 kg at 1.37 is 1,370.00
      USD = 114,335.80 rupees; unit price 114.336. Two lines of 0.05
      USD are 4.17 each, 8.34 together, but the invoice's 0.10 USD is
      8.35: 0.01 of rounding.
  S4  A credit for 100 kg of S1's sacks: 12,500.00 less 250.00 is
      12,250.00, IGST 2,205.00, 14,455.00.
"""

import base64
import datetime
import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounting.models import ChargeType
from apps.core.models import Address, AddressType, Company, Country, ExchangeRate
from apps.sales.models import Invoice, InvoiceLine

from .einvoice import EInvoice, build, invoice_stamp, prepare
from .ewaybill import CancelReason, EwayBill, TransportMode
from .ewaybill import prepare as prepare_eway
from .returns import gstr1
from .tests import DAY, SEPTEMBER, D, GstReturnTestCase, gstin

IRN = "a" * 64


def signed_qr(**claims):
    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return ".".join([part({"alg": "RS256"}), part({"data": json.dumps(claims), "iss": "NIC"}),
                     "c2lnbmF0dXJl"])


class EInvoiceTestCase(GstReturnTestCase):
    def setUp(self):
        super().setUp()
        self.settings.einvoicing_from = datetime.date(2026, 4, 1)
        self.settings.save()
        plant = Address.objects.create(line1="Plot 12, MIDC Chakan", city="Pune",
                                       state="Maharashtra", postal_code="410501")
        Company.objects.update(address=plant)
        self.ka = self.party("KA-REG", gstin=gstin("29AABCK2222B1Z"))
        self.address(self.ka, "14 Gokul Road", "Hubballi", "Karnataka", "580030")
        self.mh = self.party("MH-REG", gstin=gstin("27AABCM1111A1Z"))
        self.address(self.mh, "3 Market Yard", "Nashik", "27", "422001")
        self.freight6 = ChargeType.objects.create(
            code="FRT6", name="Freight", revenue_account=self.revenue, hsn_code="996511")
        ExchangeRate.objects.create(currency=self.usd, rate=D("83.456789"),
                                    valid_from=datetime.date(2026, 9, 1))

    def address(self, party, line1, city, state, pin, kind=AddressType.BILLING, country=None):
        return Address.objects.create(party=party, address_type=kind, line1=line1, city=city,
                                      state=state, postal_code=pin, is_primary=True,
                                      country=country)

    def invoice(self, customer, lines, currency=None, day=DAY, post=True):
        invoice = Invoice.objects.create(customer=customer, invoice_date=day,
                                         receivable_account=self.ar,
                                         currency=currency or self.inr)
        for what, quantity, price, discount, taxes in lines:
            kind = {"charge": what} if isinstance(what, ChargeType) else {"item": what}
            line = InvoiceLine.objects.create(invoice=invoice, quantity=D(quantity),
                                              unit_price=D(price), discount_percent=D(discount),
                                              revenue_account=self.revenue, **kind)
            line.taxes.set(taxes)
        if post:
            invoice.post()
        return invoice

    def s1(self):
        return self.invoice(self.ka, [(self.sack, "1000", "125", "2", self.pair),
                                      (self.freight6, "1", "3000", "0", self.pair)])

    def export(self, lines):
        abroad = self.party("EXPORT", currency=self.usd, gst_registration="overseas",
                            fiscal_position=self.lut)
        uae = Country.objects.create(code="AE", name="United Arab Emirates")
        self.address(abroad, "Jebel Ali Free Zone", "Dubai", "", "", country=uae)
        return self.invoice(abroad, lines, currency=self.usd)

    def registered(self, invoice):
        record = prepare(invoice)
        record.record(IRN, "112610000000001", timezone.now(), signed_qr(
            Irn=IRN, DocNo=invoice.number, SellerGstin=self.settings.gstin,
            BuyerGstin=record.payload["BuyerDtls"]["Gstin"],
            TotInvVal=record.payload["ValDtls"]["TotInvVal"]))
        return record


class WhatIsRegisteredTests(EInvoiceTestCase):
    def test_an_inter_state_sale_with_a_discount_and_freight(self):
        invoice = self.s1()
        payload = build(invoice)
        self.assertEqual(payload["TranDtls"]["SupTyp"], "B2B")
        self.assertEqual(payload["DocDtls"], {"Typ": "INV", "No": invoice.number,
                                              "Dt": "10/09/2026"})
        self.assertEqual(payload["SellerDtls"], {
            "Gstin": self.settings.gstin, "LglNm": "Deccan Polysacks",
            "TrdNm": "Deccan Polysacks", "Addr1": "Plot 12, MIDC Chakan", "Loc": "Pune",
            "Pin": 410501, "Stcd": "27"})
        self.assertEqual(payload["BuyerDtls"]["Pos"], "29")
        self.assertEqual((payload["BuyerDtls"]["Stcd"], payload["BuyerDtls"]["Pin"]),
                         ("29", 580030))
        sacks, freight = payload["ItemList"]
        self.assertEqual(sacks, {
            "SlNo": "1", "PrdDesc": str(self.sack), "IsServc": "N", "HsnCd": "63053300",
            "Qty": 1000.0, "Unit": "KGS", "UnitPrice": 125.0, "TotAmt": 125000.0,
            "Discount": 2500.0, "AssAmt": 122500.0, "GstRt": 18.0, "IgstAmt": 22050.0,
            "CgstAmt": 0.0, "SgstAmt": 0.0, "CesRt": 0.0, "CesAmt": 0.0,
            "TotItemVal": 144550.0})
        self.assertEqual((freight["IsServc"], freight["AssAmt"], freight["IgstAmt"],
                          "Unit" in freight), ("Y", 3000.0, 540.0, False))
        self.assertEqual(payload["ValDtls"], {
            "AssVal": 125500.0, "CgstVal": 0.0, "SgstVal": 0.0, "IgstVal": 22590.0,
            "CesVal": 0.0, "StCesVal": 0.0, "Discount": 0.0, "OthChrg": 0.0,
            "RndOffAmt": 0.0, "TotInvVal": 148090.0})
        self.assertNotIn("ShipDtls", payload)

    def test_it_says_what_the_return_says(self):
        invoice = self.s1()
        payload = build(invoice)
        (row,) = gstr1(*SEPTEMBER)["b2b"]
        self.assertEqual(Decimal(str(payload["ValDtls"]["TotInvVal"])), row["value"])
        self.assertEqual(Decimal(str(payload["ValDtls"]["AssVal"])),
                         sum(item["taxable"] for item in row["items"]))
        self.assertEqual(Decimal(str(payload["ValDtls"]["IgstVal"])),
                         sum(item["igst"] for item in row["items"]))

    def test_inside_the_state(self):
        payload = build(self.invoice(self.mh, [(self.sack, "100", "150", "0", self.pair)]))
        (item,) = payload["ItemList"]
        self.assertEqual((item["GstRt"], item["CgstAmt"], item["SgstAmt"], item["IgstAmt"]),
                         (18.0, 1350.0, 1350.0, 0.0))
        self.assertEqual(payload["ValDtls"]["TotInvVal"], 17700.0)

    def test_shipped_somewhere_other_than_billed(self):
        self.address(self.ka, "Godown 4, APMC", "Belagavi", "Karnataka", "590016",
                     kind=AddressType.SHIPPING)
        ship = build(self.s1())["ShipDtls"]
        self.assertEqual((ship["Loc"], ship["Pin"], ship["Stcd"]), ("Belagavi", 590016, "29"))

    def test_an_export_in_rupees_at_its_own_rate(self):
        payload = build(self.export([(self.sack, "1000", "1.37", "0", [self.zero])]))
        self.assertEqual(payload["TranDtls"]["SupTyp"], "EXPWOP")
        self.assertEqual((payload["BuyerDtls"]["Gstin"], payload["BuyerDtls"]["Pos"],
                          payload["BuyerDtls"]["Stcd"], payload["BuyerDtls"]["Pin"]),
                         ("URP", "96", "96", 999999))
        (item,) = payload["ItemList"]
        self.assertEqual((item["UnitPrice"], item["TotAmt"], item["AssAmt"], item["Discount"]),
                         (114.336, 114335.8, 114335.8, 0.0))
        self.assertEqual(payload["ExpDtls"], {"ForCur": "USD", "CntCode": "AE"})

    def test_a_paisa_of_rounding_between_the_lines_and_the_total(self):
        payload = build(self.export([(self.sack, "1", "0.05", "0", [self.zero]),
                                     (self.sack, "1", "0.05", "0", [self.zero])]))
        self.assertEqual([item["TotItemVal"] for item in payload["ItemList"]], [4.17, 4.17])
        self.assertEqual((payload["ValDtls"]["RndOffAmt"], payload["ValDtls"]["TotInvVal"]),
                         (0.01, 8.35))

    def test_a_credit_note_names_its_invoice(self):
        invoice = self.s1()
        note = invoice.create_credit_note(quantities={invoice.lines.order_by("id")[0]: D("100")})
        payload = build(note)
        self.assertEqual(payload["DocDtls"]["Typ"], "CRN")
        self.assertEqual(payload["RefDtls"], {"PrecDocDtls": [
            {"InvNo": invoice.number, "InvDt": "10/09/2026"}]})
        (item,) = payload["ItemList"]
        self.assertEqual((item["TotAmt"], item["Discount"], item["AssAmt"], item["IgstAmt"]),
                         (12500.0, 250.0, 12250.0, 2205.0))
        self.assertEqual(payload["ValDtls"]["TotInvVal"], 14455.0)


class WhatIsNotRegisteredTests(EInvoiceTestCase):
    def test_before_e_invoicing_began(self):
        self.settings.einvoicing_from = datetime.date(2026, 10, 1)
        self.settings.save()
        with self.assertRaisesMessage(ValidationError, "before e-invoicing began"):
            build(self.s1())

    def test_an_unregistered_buyer(self):
        retail = self.party("KA-UNREG", gst_state="29", gst_registration="unregistered")
        with self.assertRaisesMessage(ValidationError, "unregistered buyer"):
            build(self.invoice(retail, [(self.sack, "1", "100", "0", self.pair)]))

    def test_a_draft(self):
        draft = self.invoice(self.ka, [(self.sack, "1", "100", "0", self.pair)], post=False)
        with self.assertRaisesMessage(ValidationError, "not posted"):
            build(draft)

    def test_four_digits_of_hsn(self):
        with self.assertRaisesMessage(ValidationError, "HSN 9965"):
            build(self.invoice(self.ka, [(self.sack, "1", "100", "0", self.pair),
                                         (self.freight, "1", "50", "0", self.pair)]))

    def test_a_quantity_finer_than_the_portal_takes(self):
        with self.assertRaisesMessage(ValidationError, "three decimals"):
            build(self.invoice(self.ka, [(self.sack, "10.1235", "100", "0", self.pair)]))

    def test_a_buyer_with_no_address(self):
        bare = self.party("KA-BARE", gstin=gstin("29AABCB3333B1Z"))
        with self.assertRaisesMessage(ValidationError, "no address"):
            build(self.invoice(bare, [(self.sack, "1", "100", "0", self.pair)]))

    def test_an_address_in_another_state_than_the_registration(self):
        Address.objects.filter(party=self.ka).update(state="Goa")
        with self.assertRaisesMessage(ValidationError, "is in Goa"):
            build(self.s1())

    def test_a_name_too_long_is_refused_not_cut(self):
        Address.objects.filter(party=self.ka).update(line1="Gokul Road " * 10)
        with self.assertRaisesMessage(ValidationError, "the portal takes 1 to 100"):
            build(self.s1())

    def test_a_plant_with_no_pin(self):
        Address.objects.filter(party__isnull=True).update(postal_code="")
        with self.assertRaisesMessage(ValidationError, "six-digit PIN"):
            build(self.s1())


class WhatThePortalAnsweredTests(EInvoiceTestCase):
    def test_kept_once_it_is_shown_to_be_this_invoices(self):
        record = self.registered(self.s1())
        self.assertEqual(record.irn, IRN)
        with self.assertRaisesMessage(ValidationError, "already registered"):
            prepare(record.invoice)
        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            record.delete()
        record.payload = {}
        with self.assertRaisesMessage(ValidationError, "does not change"):
            record.save()

    def test_another_invoices_answer_is_refused(self):
        first, second = self.s1(), self.s1()
        record = prepare(second)
        answer = signed_qr(Irn=IRN, DocNo=first.number, SellerGstin=self.settings.gstin,
                           BuyerGstin=record.payload["BuyerDtls"]["Gstin"], TotInvVal=148090)
        with self.assertRaisesMessage(ValidationError, "another document's"):
            record.record(IRN, "1", timezone.now(), answer)
        answer = signed_qr(Irn=IRN, DocNo=second.number, SellerGstin=self.settings.gstin,
                           BuyerGstin=record.payload["BuyerDtls"]["Gstin"], TotInvVal=148000)
        with self.assertRaisesMessage(ValidationError, "worth 148000"):
            record.record(IRN, "1", timezone.now(), answer)
        with self.assertRaisesMessage(ValidationError, "not a signed QR"):
            record.record(IRN, "1", timezone.now(), "not-a-token")
        with self.assertRaisesMessage(ValidationError, "64 hexadecimal"):
            record.record("xyz", "1", timezone.now(), answer)
        before = timezone.make_aware(datetime.datetime(2026, 9, 9, 18, 0))
        with self.assertRaisesMessage(ValidationError, "on or after the invoice"):
            record.record(IRN, "1", before, answer)
        record.refresh_from_db()
        self.assertEqual(record.irn, "")

    def test_prepared_again_until_answered(self):
        invoice = self.s1()
        first = prepare(invoice)
        Address.objects.filter(party=self.ka).update(city="Hubli-Dharwad")
        second = prepare(Invoice.objects.get(pk=invoice.pk))
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.payload["BuyerDtls"]["Loc"], "Hubli-Dharwad")

    def test_an_old_invoice_carries_a_warning(self):
        invoice = self.invoice(self.ka, [(self.sack, "1", "100", "0", self.pair)],
                               day=timezone.localdate() - datetime.timedelta(days=31))
        self.assertIn("31 days old", prepare(invoice).warnings()[0])


class ThePrintedInvoiceTests(EInvoiceTestCase):
    def test_not_sent_without_its_irn_and_printed_with_it(self):
        invoice = self.s1()
        self.ka.email = "buyer@example.com"
        self.ka.save()
        with self.assertRaisesMessage(ValidationError, "no IRN yet"):
            invoice.email_to_customer()
        self.registered(invoice)
        stamp = invoice_stamp(invoice)
        self.assertEqual(stamp["rows"][0], ("IRN", IRN))
        self.assertTrue(invoice.render_pdf().startswith(b"%PDF"))
        invoice.email_to_customer()
        self.assertEqual(len(mail.outbox), 1)

    def test_an_invoice_that_needs_none_is_sent_as_before(self):
        retail = self.party("KA-UNREG", gst_state="29", gst_registration="unregistered")
        invoice = self.invoice(retail, [(self.sack, "1", "100", "0", self.pair)])
        self.assertIsNone(invoice_stamp(invoice))


class InvoiceEwayBillTests(EInvoiceTestCase):
    def test_the_goods_on_the_lorry_and_the_rest_of_the_invoice(self):
        bill = prepare_eway(self.s1(), vehicle_number="ka 25-ab 1234", distance_km=560)
        payload = bill.payload
        self.assertEqual((payload["subSupplyType"], payload["docType"], payload["toGstin"],
                          payload["toStateCode"], payload["actToStateCode"],
                          payload["transactionType"]),
                         ("1", "INV", self.ka.tax_profile.gstin, 29, 29, 1))
        (item,) = payload["itemList"]
        self.assertEqual((item["hsnCode"], item["quantity"], item["taxableAmount"],
                          item["igstRate"]), (63053300, 1000.0, 122500.0, 18.0))
        self.assertEqual((payload["totalValue"], payload["igstValue"], payload["otherValue"],
                          payload["totInvValue"]), (122500.0, 22050.0, 3540.0, 148090.0))
        self.assertEqual((payload["vehicleNo"], payload["transDistance"], payload["transMode"]),
                         ("KA25AB1234", "560", "1"))
        self.assertTrue(bill.required)

    def test_under_the_limit_it_is_built_and_says_so(self):
        bill = prepare_eway(self.invoice(self.mh, [(self.sack, "100", "150", "0", self.pair)]),
                            vehicle_number="MH15CD4321")
        self.assertFalse(bill.required)
        self.assertIn("not above the limit of 50000", bill.required_because)
        self.assertEqual((bill.payload["cgstValue"], bill.payload["totInvValue"]),
                         (1350.0, 17700.0))

    def test_bill_to_one_place_ship_to_another(self):
        self.address(self.ka, "Godown 4, APMC", "Belagavi", "Karnataka", "590016",
                     kind=AddressType.SHIPPING)
        invoice = self.s1()
        bill = prepare_eway(invoice, vehicle_number="KA25AB1234")
        self.assertEqual((bill.payload["transactionType"], bill.payload["toPincode"]),
                         (2, 590016))

    def test_an_export_goes_to_the_port(self):
        invoice = self.export([(self.sack, "1000", "1.37", "0", [self.zero])])
        port = self.address(invoice.customer, "JNPT, Nhava Sheva", "Uran", "Maharashtra",
                            "400707", kind=AddressType.SHIPPING)
        Invoice.objects.filter(pk=invoice.pk).update(shipping_address=port)
        payload = prepare_eway(Invoice.objects.get(pk=invoice.pk),
                               vehicle_number="MH46AB1234").payload
        self.assertEqual((payload["subSupplyType"], payload["toGstin"], payload["toStateCode"],
                          payload["actToStateCode"], payload["toPincode"],
                          payload["totInvValue"]),
                         ("3", "URP", 99, 27, 400707, 114335.8))

    def test_how_it_goes_must_be_said(self):
        invoice = self.s1()
        with self.assertRaisesMessage(ValidationError, "give the vehicle number"):
            prepare_eway(invoice)
        with self.assertRaisesMessage(ValidationError, "not the shape of a vehicle"):
            prepare_eway(invoice, vehicle_number="KA-1")
        with self.assertRaisesMessage(ValidationError, "transport document"):
            prepare_eway(invoice, mode=TransportMode.RAIL)
        with self.assertRaisesMessage(ValidationError, "0 to 4000"):
            prepare_eway(invoice, vehicle_number="KA25AB1234", distance_km=4001)
        bill = prepare_eway(invoice, transporter_id="29AABCT1234F1ZX",
                            transporter_name="Sri Balaji Roadways")
        self.assertEqual((bill.payload["vehicleNo"], bill.payload["transporterId"]),
                         ("", "29AABCT1234F1ZX"))
        for shape in ("22BH1234AB", "TR09AB1234"):
            self.assertEqual(prepare_eway(invoice, vehicle_number=shape).vehicle_number, shape)

    def test_what_moves_no_goods_or_comes_back(self):
        invoice = self.s1()
        with self.assertRaisesMessage(ValidationError, "only services"):
            prepare_eway(self.invoice(self.ka, [(self.freight6, "1", "3000", "0", self.pair)]),
                         vehicle_number="KA25AB1234")
        note = invoice.create_credit_note()
        with self.assertRaisesMessage(ValidationError, "credit note"):
            prepare_eway(note, vehicle_number="KA25AB1234")

    def test_one_standing_until_cancelled_and_only_within_a_day(self):
        invoice = self.s1()
        bill = prepare_eway(invoice, vehicle_number="KA25AB1234")
        with self.assertRaisesMessage(ValidationError, "twelve digits"):
            bill.record("1234", timezone.now())
        generated = timezone.now()
        with self.assertRaisesMessage(ValidationError, "before " + invoice.number):
            bill.record("331000000001", timezone.make_aware(datetime.datetime(2026, 9, 9)))
        with self.assertRaisesMessage(ValidationError, "expire before"):
            bill.record("331000000001", generated, generated)
        bill.record("331000000001", generated, generated + datetime.timedelta(days=3))
        with self.assertRaisesMessage(ValidationError, "already moves on e-way bill"):
            prepare_eway(invoice, vehicle_number="KA25AB1234")
        with self.assertRaisesMessage(ValidationError, "more than 24 hours"):
            bill.cancel(CancelReason.DATA_ENTRY, now=generated + datetime.timedelta(hours=25))
        with self.assertRaisesMessage(ValidationError, "other reason"):
            bill.cancel(CancelReason.OTHERS)
        with self.assertRaisesMessage(ValidationError, "cancel it"):
            bill.delete()
        bill.cancel(CancelReason.DATA_ENTRY)
        again = prepare_eway(invoice, vehicle_number="KA25AB9999")
        self.assertNotEqual(again.pk, bill.pk)
        self.assertEqual(EwayBill.objects.filter(invoice=invoice).count(), 2)


class EInvoiceApiTests(EInvoiceTestCase):
    def test_prepared_answered_and_moved(self):
        invoice = self.s1()
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("gst"))
        response = client.post("/api/gst/e-invoices/", {"invoice": invoice.pk}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        record = response.json()
        answer = signed_qr(Irn=IRN, DocNo=invoice.number, SellerGstin=self.settings.gstin,
                           BuyerGstin=record["payload"]["BuyerDtls"]["Gstin"], TotInvVal=148090)
        response = client.post(f"/api/gst/e-invoices/{record['id']}/record/", {
            "irn": IRN, "ack_number": "112610000000001", "ack_date": "2026-09-10T11:30",
            "signed_qr": answer}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(EInvoice.objects.get().irn, IRN)
        response = client.post("/api/gst/eway-bills/", {"invoice": invoice.pk,
                               "vehicle_number": "KA25AB1234"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        bill = response.json()["id"]
        response = client.post(f"/api/gst/eway-bills/{bill}/record/", {
            "number": "331000000001", "generated_at": timezone.now().isoformat()},
            format="json")
        self.assertEqual(response.status_code, 200, response.content)
        response = client.post(f"/api/gst/eway-bills/{bill}/cancel/",
                               {"reason": "1"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        response = client.post("/api/gst/eway-bills/", {"invoice": invoice.pk}, format="json")
        self.assertEqual(response.status_code, 400)
