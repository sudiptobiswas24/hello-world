"""
Where a sale's goods went, and on which trucks (g38: O544, O545, O546, known limit 20).

The numbers are worked in /home/user/work/calc_g38_gst/parts.py, by plain arithmetic:

  1,000 kg of sacks at 125.37 less 2% = 122,862.60; IGST 18% = 22,115.27; freight 3,000.00 with
  its 540.00 of tax billed alongside (the "other" value, 3,540.00). The invoice: 148,517.87.

  On three trucks of 333, 333 and 334 kg:
    truck 1 and 2   taxable 40,913.25   IGST 7,364.38   other 1,178.82   = 49,456.45 each
    truck 3 (rest)  taxable 41,036.10   IGST 7,386.51   other 1,182.36   = 49,604.97
  which foot to 148,517.87. A per-kilo value rounded and multiplied back up is 2.60 out.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import Address, AddressType, Country, Party, PartyRole, PartyRoleAssignment
from apps.inventory.models import Warehouse
from apps.sales.models import Delivery, DeliveryLine, Invoice, InvoiceLine, SalesOrder, SalesOrderLine

from .einvoice import build as build_einvoice
from .ewaybill import (CancelReason, EwayBill, consignments, lacking_where_they_went,
                       prepare as prepare_eway, _invoice_payload as build_eway)
from .tests import D, DAY, gstin
from .tests_einvoice import EInvoiceTestCase

EWAY_BILLS = "/api/gst/eway-bills/"


def person(role):
    call_command("setup_roles", verbosity=0)
    user = get_user_model().objects.create_user(role.lower().replace(" ", "-"))
    user.groups.add(Group.objects.get(name=role))
    client = APIClient()
    client.force_authenticate(user)
    return client


class TheTrucksTestCase(EInvoiceTestCase):
    """An order for 1,000 kg and freight, shipped and invoiced as the tests say."""

    def setUp(self):
        super().setUp()
        self.wh = Warehouse.objects.create(code="FG", name="Finished goods")
        self.carrier = Party.objects.create(code="T-1", name="Deccan Roadways")
        PartyRoleAssignment.objects.create(party=self.carrier, role=PartyRole.VENDOR)
        self.count = 0

    def ordered(self, customer=None, quantity="1000", price="125.37", freight="3000", **extra):
        self.sale = SalesOrder.objects.create(customer=customer or self.ka, order_date=DAY, currency=self.inr, **extra)
        self.line = SalesOrderLine.objects.create(order=self.sale, item=self.sack, quantity=D(quantity), unit_price=D(price),
                                                  discount_percent=D("2"), revenue_account=self.revenue, warehouse=self.wh)
        self.line.taxes.set(self.pair)
        if freight:
            charge = SalesOrderLine.objects.create(order=self.sale, charge=self.freight6, quantity=D("1"),
                                                   unit_price=D(freight), revenue_account=self.revenue)
            charge.taxes.set(self.pair)
        SalesOrder.objects.filter(pk=self.sale.pk).update(status="confirmed")
        self.sale.refresh_from_db()
        return self.sale

    def truck(self, quantity, vehicle="KA25AB1234", lr="LR-1", sale=None, line=None):
        """A delivery that shipped, as far as an e-way bill reads one: its lines, its number, its transport."""
        self.count += 1
        delivery = Delivery.objects.create(sales_order=sale or self.sale, delivery_date=DAY)
        DeliveryLine.objects.create(delivery=delivery, order_line=line or self.line, warehouse=self.wh,
                                    quantity_shipped=D(quantity))
        Delivery.objects.filter(pk=delivery.pk).update(posted=True, number=f"DL-2026-{self.count:05d}")
        delivery.refresh_from_db()
        delivery.record_transport(self.carrier, lr, DAY, vehicle)
        return delivery

    def came_back(self, delivery):
        back = Delivery.objects.create(sales_order=delivery.sales_order, delivery_date=DAY, reverses=delivery)
        for line in delivery.lines.all():
            DeliveryLine.objects.create(delivery=back, order_line=line.order_line, warehouse=self.wh,
                                        quantity_shipped=line.quantity_shipped, reverses_line=line)
        Delivery.objects.filter(pk=back.pk).update(posted=True, number=f"RET-{delivery.number}")
        return back

    def invoiced(self, post=True):
        invoice = self.sale.create_invoice(self.ar, invoice_date=DAY)
        if post:
            invoice.post()
        return invoice


# -- Known limit 20 -----------------------------------------------------------------------------------

class OneInvoiceOnSeveralTrucksTests(TheTrucksTestCase):
    def three(self):
        self.ordered()
        trucks = [self.truck("333", "KA25AB1234", "LR-1"), self.truck("333", "KA25AB5678", "LR-2"),
                  self.truck("334", "KA25AB9012", "LR-3")]
        return self.invoiced(), trucks

    def test_the_invoice_is_what_the_arithmetic_says(self):
        invoice, _ = self.three()
        payload = build_einvoice(invoice)
        self.assertEqual((payload["ValDtls"]["AssVal"], payload["ValDtls"]["IgstVal"], payload["ValDtls"]["TotInvVal"]),
                         (125862.6, 22655.27, 148517.87))

    def test_each_truck_is_tied_as_the_order_drafts_its_invoice(self):
        invoice, trucks = self.three()
        self.assertEqual([truck.pk for truck in consignments(invoice)], [truck.pk for truck in trucks])
        self.assertEqual(Delivery.objects.get(pk=trucks[0].pk).invoice_id, invoice.pk)

    def test_a_bill_for_the_whole_invoice_is_refused_and_says_which_trucks(self):
        invoice, _ = self.three()
        with self.assertRaisesMessage(ValidationError, "went on 3 deliveries (DL-2026-00001, DL-2026-00002, DL-2026-00003)"):
            prepare_eway(invoice, distance_km=560)

    def test_one_bill_a_truck_each_with_its_own_value_and_transport_footing_to_the_invoice(self):
        invoice, trucks = self.three()
        officer = person("GST Officer")
        made = []
        for truck in trucks:
            answer = officer.post(EWAY_BILLS, {"invoice": invoice.pk, "carried_on": truck.pk, "distance_km": 560},
                                  format="json")
            self.assertEqual(answer.status_code, 201, answer.data)
            made.append(answer.data)
        first, second, last = (row["payload"] for row in made)
        for payload, truck in ((first, trucks[0]), (second, trucks[1])):
            self.assertEqual((payload["docType"], payload["docNo"], payload["subSupplyType"]), ("CHL", truck.number, "9"))
            self.assertEqual((payload["itemList"][0]["quantity"], payload["totalValue"], payload["igstValue"],
                              payload["otherValue"], payload["totInvValue"]),
                             (333.0, 40913.25, 7364.38, 1178.82, 49456.45))
        # The last consignment carries the invoice (CGST Rule 55(5)(d)) and what rounding left.
        self.assertEqual((last["docType"], last["docNo"], last["subSupplyType"]), ("INV", invoice.number, "9"))
        self.assertEqual((last["itemList"][0]["quantity"], last["totalValue"], last["igstValue"], last["otherValue"],
                          last["totInvValue"]), (334.0, 41036.10, 7386.51, 1182.36, 49604.97))
        self.assertEqual([(row["vehicleNo"], row["transDocNo"], row["transporterName"]) for row in (first, second, last)],
                         [("KA25AB1234", "LR-1", "Deccan Roadways"), ("KA25AB5678", "LR-2", "Deccan Roadways"),
                          ("KA25AB9012", "LR-3", "Deccan Roadways")])
        whole = build_einvoice(invoice)["ValDtls"]
        for name, total in (("totalValue", Decimal("122862.60")), ("igstValue", Decimal(str(whole["IgstVal"])) - 540),
                            ("totInvValue", Decimal(str(whole["TotInvVal"])))):
            self.assertEqual(sum(Decimal(str(row[name])) for row in (first, second, last)), total, name)
        # Each lot is judged by its own value, and says it is one.
        self.assertEqual([row["required"] for row in made], [False, False, False])
        self.assertIn(f"Consignment DL-2026-00001 of {invoice.number}", made[0]["required_because"])
        self.assertEqual([row["carried_on_number"] for row in made], [truck.number for truck in trucks])
        self.assertEqual(made[0]["document_number"], "DL-2026-00001")
        # What was kept is what the database holds, row for row.
        kept = EwayBill.objects.get(pk=made[2]["id"])
        self.assertEqual((kept.invoice_id, kept.carried_on_id, kept.payload["totInvValue"]), (invoice.pk, trucks[2].pk, 49604.97))

    def test_an_earlier_trucks_share_does_not_move_when_a_later_truck_is_tied(self):
        self.ordered()
        first = self.truck("333")
        invoice = self.invoiced()
        alone = build_eway(invoice, first)[0]
        # Not the whole invoice: 333 of 1,000 have left, on a challan, at their own share.
        self.assertEqual((alone["docType"], alone["subSupplyType"], alone["totalValue"], alone["totInvValue"]),
                         ("CHL", "9", 40913.25, 49456.45))
        later = self.truck("667", "KA25AB5678", "LR-2")
        later.travel_on(invoice)
        self.assertEqual(build_eway(invoice, first)[0]["totInvValue"], 49456.45)
        rest = build_eway(invoice, later)[0]
        self.assertEqual((rest["docType"], rest["totalValue"], rest["igstValue"], rest["otherValue"], rest["totInvValue"]),
                         ("INV", 81949.35, 14750.89, 2361.18, 99061.42))

    def test_one_truck_that_carried_it_all_moves_on_the_invoice_whole(self):
        self.ordered()
        only = self.truck("1000", "ka 25 ab 1234", "LR-5521")
        invoice = self.invoiced()
        bill = prepare_eway(invoice, distance_km=560)
        payload = bill.payload
        self.assertEqual(bill.carried_on_id, only.pk)
        self.assertEqual((payload["docType"], payload["docNo"], payload["subSupplyType"], payload["totalValue"],
                          payload["igstValue"], payload["otherValue"], payload["totInvValue"], payload["vehicleNo"],
                          payload["transDocNo"]),
                         ("INV", invoice.number, "1", 122862.6, 22115.27, 3540.0, 148517.87, "KA25AB1234", "LR-5521"))
        self.assertTrue(bill.required)

    def test_a_truck_that_carried_more_than_the_invoice_bills_is_refused(self):
        self.ordered()
        self.truck("600")
        invoice = self.invoiced()
        over = self.truck("600", "KA25AB5678", "LR-2")
        over.travel_on(invoice)
        with self.assertRaisesMessage(ValidationError, "bills 1000 of"):
            prepare_eway(invoice, carried_on=over, distance_km=560)

    def test_a_delivery_that_is_not_the_invoices_is_refused(self):
        invoice, _ = self.three()
        other_order = self.ordered()
        stranger = self.truck("10", sale=other_order, line=self.line)
        with self.assertRaisesMessage(ValidationError, f"is not a shipment travelling on {invoice.number}"):
            prepare_eway(invoice, carried_on=stranger, distance_km=560)
        with self.assertRaisesMessage(ValidationError, "bills none of what"):
            stranger.travel_on(invoice)

    # -- the reverse, edit and delete paths

    def test_the_whole_bill_and_the_trucks_bills_never_both_stand(self):
        """An invoice from before deliveries were tied has a bill for the whole of it."""
        invoice, trucks = self.three()
        Delivery.objects.filter(invoice=invoice).update(invoice=None)
        whole = prepare_eway(invoice, distance_km=560, vehicle_number="KA01XY9999")
        self.assertEqual((whole.carried_on_id, whole.payload["docType"], whole.payload["totInvValue"]), (None, "INV", 148517.87))
        trucks[0].travel_on(invoice)
        with self.assertRaisesMessage(ValidationError, "made for the whole invoice before its deliveries were recorded"):
            prepare_eway(invoice, carried_on=trucks[0], distance_km=560)
        whole.delete()
        self.assertEqual(prepare_eway(invoice, carried_on=trucks[0], distance_km=560).carried_on_id, trucks[0].pk)

    def test_a_truck_is_not_moved_from_under_its_bill_and_is_once_the_bill_is_cancelled(self):
        invoice, trucks = self.three()
        staff = person("Warehouse Staff")
        url = f"/api/sales/deliveries/{trucks[0].pk}/travels_on/"
        bill = prepare_eway(invoice, carried_on=trucks[0], distance_km=560)
        for body in ({"invoice": None}, {"invoice": invoice.pk}):
            answer = staff.post(url, body, format="json")
            self.assertEqual(answer.status_code, 400, answer.data)
            self.assertIn("(not yet generated) standing", str(answer.data))
        bill.record("331000000002", timezone.now())
        self.assertIn("331000000002 standing", str(staff.post(url, {"invoice": None}, format="json").data))
        bill.cancel(CancelReason.DATA_ENTRY)
        answer = staff.post(url, {"invoice": None}, format="json")
        self.assertEqual((answer.status_code, answer.data["invoice"]), (200, None))
        self.assertEqual([truck.pk for truck in consignments(invoice)], [trucks[1].pk, trucks[2].pk])
        # And back on: the same action, by the same person.
        answer = staff.post(url, {"invoice": invoice.pk}, format="json")
        self.assertEqual((answer.status_code, answer.data["invoice"], answer.data["invoice_number"]),
                         (200, invoice.pk, invoice.number))

    def test_a_rep_may_not_say_which_invoice_a_truck_went_on(self):
        invoice, trucks = self.three()
        answer = person("Sales Rep").post(f"/api/sales/deliveries/{trucks[0].pk}/travels_on/", {"invoice": None}, format="json")
        self.assertIn(answer.status_code, (403, 404))
        self.assertEqual(Delivery.objects.get(pk=trucks[0].pk).invoice_id, invoice.pk)

    def test_a_shipment_returned_whole_with_no_bill_was_never_a_consignment_and_one_with_a_bill_was(self):
        invoice, trucks = self.three()
        bill = prepare_eway(invoice, carried_on=trucks[0], distance_km=560)
        bill.record("331000000003", timezone.now())
        self.came_back(trucks[0])
        self.came_back(trucks[1])
        self.assertEqual([truck.pk for truck in consignments(invoice)], [trucks[0].pk, trucks[2].pk])
        # What the first truck's bill said is kept, whatever came back after.
        self.assertEqual(EwayBill.objects.get(pk=bill.pk).payload["totInvValue"], 49456.45)
        with self.assertRaisesMessage(ValidationError, "comes back on the customer's own e-way bill"):
            Delivery.objects.get(reverses=trucks[0]).travel_on(invoice)

    def test_a_credit_note_leaves_the_trucks_and_their_bills_as_they_were(self):
        invoice, trucks = self.three()
        bill = prepare_eway(invoice, carried_on=trucks[2], distance_km=560)
        note = invoice.create_credit_note()
        self.assertTrue(note.posted)
        self.assertEqual(len(consignments(invoice)), 3)
        self.assertEqual(consignments(note), [])
        self.assertEqual(EwayBill.objects.get(pk=bill.pk).payload["totInvValue"], 49604.97)
        with self.assertRaisesMessage(ValidationError, "is not an invoice for goods"):
            trucks[0].travel_on(note)

    def test_a_draft_invoice_deleted_lets_its_trucks_go(self):
        self.ordered()
        truck = self.truck("1000")
        draft = self.invoiced(post=False)
        self.assertEqual(Delivery.objects.get(pk=truck.pk).invoice_id, draft.pk)
        draft.delete()
        self.assertIsNone(Delivery.objects.get(pk=truck.pk).invoice_id)
        again = self.invoiced()
        self.assertEqual(Delivery.objects.get(pk=truck.pk).invoice_id, again.pk)

    def test_a_delivery_posting_after_its_invoice_travels_on_it_when_there_is_just_the_one(self):
        self.ordered()
        invoice = self.invoiced()
        draft = Delivery.objects.create(sales_order=self.sale, delivery_date=DAY)
        DeliveryLine.objects.create(delivery=draft, order_line=self.line, warehouse=self.wh, quantity_shipped=D("400"))
        self.assertEqual(draft._the_invoice_it_would_travel_on(), invoice)
        second = Invoice.objects.create(customer=self.ka, invoice_date=DAY, receivable_account=self.ar, currency=self.inr)
        InvoiceLine.objects.create(invoice=second, order_line=self.line, item=self.sack, quantity=D("1"), unit_price=D("1"),
                                   revenue_account=self.revenue)
        self.assertIsNone(draft._the_invoice_it_would_travel_on())

    def test_the_trucks_of_an_invoice_are_listed_with_their_shares(self):
        invoice, trucks = self.three()
        prepare_eway(invoice, carried_on=trucks[1], distance_km=560)
        answer = person("GST Officer").get(EWAY_BILLS + f"consignments/?invoice={invoice.pk}")
        self.assertEqual(answer.status_code, 200, answer.data)
        self.assertEqual([(row["number"], row["document_type"], row["value"], bool(row["eway_bill"])) for row in answer.data],
                         [("DL-2026-00001", "CHL", Decimal("49456.45"), False),
                          ("DL-2026-00002", "CHL", Decimal("49456.45"), True),
                          ("DL-2026-00003", "INV", Decimal("49604.97"), False)])


# -- O545 ---------------------------------------------------------------------------------------------

class AnExportNamesItsPortTests(TheTrucksTestCase):
    """USD 17,100 of sacks to Gulf Bulk under LUT, at 83.456789: 1,427,111.09 in rupees, no tax."""

    def setUp(self):
        super().setUp()
        self.gulf = self.party("GULF", currency=self.usd, gst_registration="overseas", fiscal_position=self.lut)
        uae = Country.objects.create(code="AE", name="United Arab Emirates")
        self.address(self.gulf, "Jebel Ali Free Zone", "Dubai", "", "", country=uae)
        self.port = Address.objects.create(label="JNPT", line1="Jawaharlal Nehru Port, Sheva", city="Navi Mumbai",
                                           state="Maharashtra", postal_code="400707")

    def export(self, port=None, post=True):
        invoice = Invoice.objects.create(customer=self.gulf, invoice_date=DAY, receivable_account=self.ar,
                                         currency=self.usd, port_address=port)
        line = InvoiceLine.objects.create(invoice=invoice, item=self.sack, quantity=D("9000"), unit_price=D("1.9"),
                                          revenue_account=self.revenue)
        line.taxes.set(self.pair)
        if post:
            invoice.post()
        return invoice

    def test_it_does_not_post_naming_no_port_and_says_what_to_name(self):
        invoice = self.export(post=False)
        with self.assertRaisesMessage(ValidationError, "is an export of goods and names no place in India they leave the plant for"):
            invoice.post()
        invoice.refresh_from_db()
        self.assertEqual((invoice.posted, invoice.number, invoice.journal_entry_id), (False, "", None))

    def test_a_port_abroad_or_with_no_pin_or_state_is_refused_as_it_is_named(self):
        invoice = self.export(post=False)
        for change, words in (({"postal_code": ""}, "has no postal code"), ({"state": "Bombay"}, "is no GST state"),
                              ({"country": self.gulf.billing_address().country}, "is in United Arab Emirates")):
            port = Address.objects.create(line1="Berth 4", city="Somewhere", state="Maharashtra", postal_code="400707")
            for name, value in change.items():
                setattr(port, name, value)
            port.save()
            invoice.port_address = port
            with self.assertRaisesMessage(ValidationError, words):
                invoice.save()

    def test_posted_with_its_port_the_bill_and_the_e_invoice_are_made_out_to_it(self):
        invoice = self.export(self.port)
        self.assertEqual(invoice.port, {"label": "JNPT", "line1": "Jawaharlal Nehru Port, Sheva", "line2": "",
                                        "city": "Navi Mumbai", "pin": "400707", "state": "27"})
        self.assertEqual(invoice.delivery_state, "27")
        payload, required, because = build_eway(invoice)
        self.assertEqual((payload["subSupplyType"], payload["toGstin"], payload["toStateCode"], payload["actToStateCode"],
                          payload["toPincode"], payload["toPlace"], payload["toAddr1"], payload["transactionType"],
                          payload["totInvValue"]),
                         ("3", "URP", 99, 27, 400707, "Navi Mumbai", "Jawaharlal Nehru Port, Sheva", 2, 1427111.09))
        self.assertTrue(required)
        registered = build_einvoice(invoice)
        self.assertEqual(registered["ShipDtls"], {"Gstin": "URP", "LglNm": "GULF", "Addr1": "Jawaharlal Nehru Port, Sheva",
                                                  "Loc": "Navi Mumbai", "Pin": 400707, "Stcd": "27"})
        self.assertEqual((registered["BuyerDtls"]["Pin"], registered["BuyerDtls"]["Stcd"], registered["BuyerDtls"]["Pos"]),
                         (999999, "96", "96"))

    def test_the_port_is_a_fact_of_the_posting_not_the_address_as_it_reads_today(self):
        invoice = self.export(self.port)
        Address.objects.filter(pk=self.port.pk).update(line1="Mundra Port", city="Mundra", state="Gujarat", postal_code="370421")
        invoice = Invoice.objects.get(pk=invoice.pk)
        self.assertEqual((build_eway(invoice)[0]["toPincode"], build_eway(invoice)[0]["actToStateCode"]), (400707, 27))

    def test_a_posted_export_is_not_given_another_port_by_an_edit_or_by_the_action(self):
        invoice = self.export(self.port)
        invoice.port_address = None
        with self.assertRaisesMessage(ValidationError, "posted and immutable"):
            invoice.save()
        with self.assertRaisesMessage(ValidationError, "posted as delivered in Maharashtra (27); that stands"):
            invoice.record_where_it_went(None, port_address=self.port)

    def test_its_credit_note_asks_for_none_and_reads_its_invoices(self):
        invoice = self.export(self.port)
        note = invoice.create_credit_note()
        self.assertTrue(note.posted)
        self.assertEqual((note.port, note.delivery_state, note.where_it_went()), (None, "", ("27", invoice.port)))
        self.assertEqual(build_einvoice(note)["ShipDtls"]["Pin"], 400707)

    def test_the_order_names_it_and_its_invoice_carries_it(self):
        self.ordered(customer=self.gulf, freight=None, port_address=self.port)
        invoice = self.invoiced(post=False)
        self.assertEqual(invoice.port_address_id, self.port.pk)
        typed = Invoice.objects.create(customer=self.gulf, invoice_date=DAY, receivable_account=self.ar,
                                       currency=self.usd, sales_order=self.sale)
        self.assertEqual(typed.port_address_id, self.port.pk)

    def test_a_service_billed_abroad_names_none(self):
        invoice = Invoice.objects.create(customer=self.gulf, invoice_date=DAY, receivable_account=self.ar, currency=self.usd)
        line = InvoiceLine.objects.create(invoice=invoice, charge=self.freight6, quantity=D("1"), unit_price=D("100"),
                                          revenue_account=self.revenue)
        line.taxes.set(self.pair)
        invoice.post()
        self.assertEqual((invoice.posted, invoice.port, invoice.delivery_state), (True, None, ""))

    def test_through_the_api_as_the_people_who_do_it(self):
        """The rep names the port on the draft; the AR Manager's post is refused until it is named."""
        draft = self.export(post=False)
        manager = person("AR Manager")
        refused = manager.post(f"/api/sales/invoices/{draft.pk}/post_invoice/", {}, format="json")
        self.assertEqual(refused.status_code, 400, refused.data)
        self.assertIn("names no place in India", str(refused.data))
        get_user_model().objects.filter(username="ar-manager").delete()
        rep = get_user_model().objects.create_superuser("named-it")  # the rep's customer scope is not what is asked here
        client = APIClient()
        client.force_authenticate(rep)
        named = client.patch(f"/api/sales/invoices/{draft.pk}/", {"port_address": self.port.pk}, format="json")
        self.assertEqual((named.status_code, named.data["port_address"]), (200, self.port.pk))
        posted = person("AR Manager").post(f"/api/sales/invoices/{draft.pk}/post_invoice/", {}, format="json")
        self.assertEqual((posted.status_code, posted.data["delivery_state"], posted.data["port"]["pin"]), (200, "27", "400707"))

    def test_one_posted_before_the_port_was_asked_for_is_listed_and_given_one(self):
        invoice = self.export(self.port)
        Invoice.objects.filter(pk=invoice.pk).update(port=None, delivery_state="", port_address=None)
        invoice = Invoice.objects.get(pk=invoice.pk)
        with self.assertRaisesMessage(ValidationError, "records no place in India its goods left the plant for"):
            build_eway(invoice)
        officer = person("GST Officer")
        listed = officer.get(EWAY_BILLS + "lacking/")
        self.assertEqual([(row["number"], row["export"]) for row in listed.data], [(invoice.number, True)])
        refused = officer.post(EWAY_BILLS + "record-where-it-went/", {"invoice": invoice.pk, "state": "27"}, format="json")
        self.assertEqual(refused.status_code, 400, refused.data)
        self.assertIn("is an export: name the place in India", str(refused.data))
        done = officer.post(EWAY_BILLS + "record-where-it-went/", {"invoice": invoice.pk, "port_address": self.port.pk},
                            format="json")
        self.assertEqual((done.status_code, done.data["delivery_state"], done.data["recorded_by"]), (200, "27", "gst-officer"))
        self.assertEqual(officer.get(EWAY_BILLS + "lacking/").data, [])
        made = officer.post(EWAY_BILLS, {"invoice": invoice.pk, "distance_km": 160, "vehicle_number": "MH12AB1234"}, format="json")
        self.assertEqual((made.status_code, made.data["payload"]["toPincode"]), (201, 400707))


# -- O544 ---------------------------------------------------------------------------------------------

class APostedInvoiceThatSaysNoStateTests(TheTrucksTestCase):
    """
    Billed to the Karnataka buyer, delivered to a Maharashtra party's godown at Sinnar whose address
    gives no state: it posted before O533 refused that. 1,000 kg at 125 less 2% = 122,500.00.
    """

    def setUp(self):
        super().setUp()
        self.site = Address.objects.create(party=self.mh, address_type=AddressType.SHIPPING, line1="Gat 41",
                                           city="Sinnar", state="Maharashtra", postal_code="422103")
        invoice = self.invoice(self.ka, [(self.sack, "1000", "125", "2", self.pair)], post=False)
        invoice.shipping_address = self.site
        invoice.save()
        invoice.post()
        self.assertEqual(invoice.delivery_state, "27")  # kept as it posts, from now on
        # As it stood when it posted before any of this: no state on the site, and none kept.
        Address.objects.filter(pk=self.site.pk).update(state="")
        Invoice.objects.filter(pk=invoice.pk).update(delivery_state="")
        self.old = Invoice.objects.get(pk=invoice.pk)
        self.record = EWAY_BILLS + "record-where-it-went/"

    def test_it_is_a_dead_end_that_says_the_way_out_and_is_listed(self):
        with self.assertRaisesMessage(ValidationError, "Record the state its goods were delivered in on the invoice"):
            build_eway(self.old)
        with self.assertRaisesMessage(ValidationError, "is no GST state"):
            build_einvoice(self.old)
        ((invoice, why),) = lacking_where_they_went()
        self.assertEqual(invoice.pk, self.old.pk)
        self.assertIn("Gat 41, Sinnar, 422103) says no GST state", why)
        from apps.imports.management.commands.go_live_check import checks
        from apps.web.checks import _invoices_lacking_where_they_went

        (found,) = [row for row in checks() if row[1] == "GST: invoices that say nowhere their goods went"]
        self.assertIn(f"1 posted invoice(s) of goods record no state they were delivered in", found[2])
        self.assertIn(self.old.number, found[2])
        self.assertEqual(_invoices_lacking_where_they_went(DAY), 1)

    def test_the_officer_records_it_and_both_documents_build(self):
        officer = person("GST Officer")
        before = Invoice.objects.filter(pk=self.old.pk).values("posted_total", "place_of_supply", "journal_entry", "issued_as").get()
        answer = officer.post(self.record, {"invoice": self.old.pk, "state": "Maharashtra"}, format="json")
        self.assertEqual((answer.status_code, answer.data["delivery_state"], answer.data["recorded_by"]),
                         (200, "27", "gst-officer"), answer.data)
        kept = Invoice.objects.get(pk=self.old.pk)
        self.assertIsNotNone(kept.delivery_recorded_at)
        self.assertEqual(kept.delivery_recorded_by.username, "gst-officer")
        # Nothing it billed moved: the money, the place of supply, the entry, the particulars.
        self.assertEqual(Invoice.objects.filter(pk=self.old.pk).values("posted_total", "place_of_supply", "journal_entry",
                                                                       "issued_as").get(), before)
        payload = build_eway(kept)[0]
        self.assertEqual((payload["toStateCode"], payload["actToStateCode"], payload["toPincode"], payload["transactionType"],
                          payload["totalValue"]), (29, 27, 422103, 2, 122500.0))
        ship = build_einvoice(kept)["ShipDtls"]
        self.assertEqual((ship["Gstin"], ship["Stcd"], ship["Pin"]), (gstin("27AABCM1111A1Z"), "27", 422103))
        self.assertEqual(lacking_where_they_went(), [])
        opened = officer.get(f"/api/sales/invoices/{self.old.pk}/")
        self.assertEqual((opened.data["delivery_state"], opened.data["lacks_where_it_went"],
                          opened.data["delivery_recorded_by_name"]), ("27", "", "gst-officer"))

    def test_the_state_kept_as_it_posted_stands_when_the_address_is_edited_after(self):
        """A fact of the posting (CLAUDE.md, mistake 4): the godown's address later says Gujarat; the lorry went to 27."""
        godown = Address.objects.create(party=self.mh, address_type=AddressType.SHIPPING, line1="Gat 7",
                                        city="Igatpuri", state="Maharashtra", postal_code="422403")
        invoice = self.invoice(self.ka, [(self.sack, "10", "125", "0", self.pair)], post=False)
        invoice.shipping_address = godown
        invoice.save()
        invoice.post()
        Address.objects.filter(pk=godown.pk).update(state="Gujarat")
        invoice = Invoice.objects.get(pk=invoice.pk)
        self.assertEqual((invoice.delivery_state, build_eway(invoice)[0]["actToStateCode"]), ("27", 27))
        self.assertEqual(build_einvoice(invoice)["ShipDtls"]["Stcd"], "27")

    def test_the_posted_invoice_is_still_not_edited(self):
        self.old.delivery_state = "27"
        with self.assertRaisesMessage(ValidationError, "posted and immutable"):
            self.old.save()

    def test_typed_wrong_it_is_recorded_again_or_withdrawn_until_a_bill_reads_it(self):
        officer = person("GST Officer")
        self.assertEqual(officer.post(self.record, {"invoice": self.old.pk, "state": "24"}, format="json").status_code, 200)
        again = officer.post(self.record, {"invoice": self.old.pk, "state": "27"}, format="json")
        self.assertEqual((again.status_code, again.data["delivery_state"]), (200, "27"))
        made = officer.post(EWAY_BILLS, {"invoice": self.old.pk, "distance_km": 200, "vehicle_number": "MH12AB1234"},
                            format="json")
        self.assertEqual((made.status_code, made.data["payload"]["actToStateCode"]), (201, 27))
        bill = EwayBill.objects.get(pk=made.data["id"])
        bill.record("331000000009", timezone.now())
        for url, body in ((self.record, {"invoice": self.old.pk, "state": "24"}),
                          (EWAY_BILLS + "withdraw-where-it-went/", {"invoice": self.old.pk})):
            refused = officer.post(url, body, format="json")
            self.assertEqual(refused.status_code, 400, refused.data)
            self.assertIn("moves on e-way bill 331000000009", str(refused.data))
        bill.cancel(CancelReason.DATA_ENTRY)
        withdrawn = officer.post(EWAY_BILLS + "withdraw-where-it-went/", {"invoice": self.old.pk}, format="json")
        self.assertEqual((withdrawn.status_code, withdrawn.data["delivery_state"], withdrawn.data["recorded_at"]), (200, "", None))
        self.assertEqual(len(lacking_where_they_went()), 1)

    def test_what_is_refused(self):
        officer = person("GST Officer")
        for body, words in (({"invoice": self.old.pk, "state": "Bombay"}, "Give the state the goods were delivered in"),
                            ({"invoice": self.old.pk, "state": ""}, "Give the state the goods were delivered in"),
                            ({"invoice": self.old.pk, "state": "96"}, "Give the state the goods were delivered in"),
                            ({"invoice": self.old.pk, "port_address": self.site.pk}, "is not an export")):
            refused = officer.post(self.record, body, format="json")
            self.assertEqual(refused.status_code, 400, refused.data)
            self.assertIn(words, str(refused.data))
        self.assertEqual(Invoice.objects.get(pk=self.old.pk).delivery_state, "")
        with self.assertRaisesMessage(ValidationError, "Nothing was recorded on"):
            self.old.withdraw_where_it_went()

    def test_other_documents_it_is_not_for(self):
        draft = self.invoice(self.ka, [(self.sack, "10", "125", "0", self.pair)], post=False)
        with self.assertRaisesMessage(ValidationError, "is a draft"):
            draft.record_where_it_went(None, state="27")
        posted = self.invoice(self.ka, [(self.sack, "10", "125", "0", self.pair)])
        with self.assertRaisesMessage(ValidationError, "posted as delivered in Karnataka (29); that stands"):
            posted.record_where_it_went(None, state="27")
        service = self.invoice(self.ka, [(self.freight6, "1", "3000", "0", self.pair)])
        with self.assertRaisesMessage(ValidationError, "No goods move on"):
            service.record_where_it_went(None, state="27")
        note = posted.create_credit_note()
        with self.assertRaisesMessage(ValidationError, f"is a note on {posted.number}"):
            note.record_where_it_went(None, state="27")
        # An address that has a state after all lacks nothing.
        Address.objects.filter(pk=self.site.pk).update(state="27")
        with self.assertRaisesMessage(ValidationError, "already says Maharashtra (27)"):
            Invoice.objects.get(pk=self.old.pk).record_where_it_went(None, state="27")

    def test_its_credit_note_reads_what_was_recorded_on_it(self):
        note = self.old.create_credit_note()
        with self.assertRaisesMessage(ValidationError, "is no GST state"):
            build_einvoice(note)
        Invoice.objects.get(pk=self.old.pk).record_where_it_went(None, state="27")
        self.assertEqual(build_einvoice(Invoice.objects.get(pk=note.pk))["ShipDtls"]["Stcd"], "27")

    def test_who_may(self):
        rep = person("Sales Rep")
        self.assertEqual(rep.post(self.record, {"invoice": self.old.pk, "state": "27"}, format="json").status_code, 403)
        self.assertEqual(rep.get(EWAY_BILLS + "lacking/").status_code, 403)
        self.assertEqual(Invoice.objects.get(pk=self.old.pk).delivery_state, "")


class AJobWorkReturnToTheCustomersSiteInAnotherStateTests(TheTrucksTestCase):
    """
    O546, the delivery's half. Converted sacks go back to the Karnataka customer's own works at
    Kolhapur: 50 kg, stated at 4,750.00 (conversion billed 50 x 20 = 1,000.00).
    """

    def going_back(self, state):
        from .ewaybill import _delivery_payload

        site = Address.objects.create(party=self.ka, address_type=AddressType.SHIPPING, line1="Gat 12, Shiroli MIDC",
                                      city="Kolhapur", state=state, postal_code="416001")
        self.sale = SalesOrder.objects.create(customer=self.ka, order_date=DAY, currency=self.inr, is_job_work=True,
                                              shipping_address=site)
        self.line = SalesOrderLine.objects.create(order=self.sale, item=self.sack, quantity=D("50"), unit_price=D("20"),
                                                  revenue_account=self.revenue, warehouse=self.wh)
        return _delivery_payload(self.truck("50"), "4750")

    def test_the_bill_says_where_the_lorry_goes_and_whom_it_goes_back_to(self):
        payload, required, because = self.going_back("Maharashtra")
        self.assertEqual((payload["subSupplyType"], payload["toGstin"], payload["toStateCode"], payload["actToStateCode"],
                          payload["toPincode"], payload["transactionType"], payload["totInvValue"]),
                         ("6", gstin("29AABCK2222B1Z"), 29, 27, 416001, 2, 4750.0))
        self.assertFalse(required)

    def test_a_site_with_no_state_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "says its state is nothing"):
            self.going_back("")
