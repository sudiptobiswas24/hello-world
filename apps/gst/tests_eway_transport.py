"""A sale's e-way bill takes the truck from its delivery, when there is just the one."""

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.sales.models import Delivery, Invoice, SalesOrder

from .ewaybill import prepare as prepare_eway
from .tests import DAY
from .tests_einvoice import EInvoiceTestCase


class TransportFromTheDeliveryTests(EInvoiceTestCase):
    def setUp(self):
        super().setUp()
        self.carrier = Party.objects.create(code="T-1", name="Deccan Roadways")
        PartyRoleAssignment.objects.create(party=self.carrier, role=PartyRole.VENDOR)
        self.sale = self.s1()
        self.order = SalesOrder.objects.create(customer=self.ka, order_date=DAY, currency=self.inr)
        Invoice.objects.filter(pk=self.sale.pk).update(sales_order=self.order)
        self.sale.refresh_from_db()

    def shipped(self):
        delivery = Delivery.objects.create(sales_order=self.order, delivery_date=DAY)
        Delivery.objects.filter(pk=delivery.pk).update(posted=True)
        delivery.refresh_from_db()
        delivery.record_transport(self.carrier, "LR-5521", DAY, "ka 25 ab 1234")
        return delivery

    def test_taken_from_the_one_delivery(self):
        self.shipped()
        bill = prepare_eway(self.sale, distance_km=560)
        self.assertEqual((bill.payload["vehicleNo"], bill.payload["transporterName"], bill.payload["transDocNo"]),
                         ("KA25AB1234", "Deccan Roadways", "LR-5521"))

    def test_not_guessed_between_two(self):
        self.shipped()
        self.shipped()
        bill = prepare_eway(self.sale, distance_km=560, vehicle_number="KA01XY9999")
        self.assertEqual((bill.payload["vehicleNo"], bill.payload["transDocNo"]), ("KA01XY9999", ""))
