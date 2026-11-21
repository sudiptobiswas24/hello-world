"""
Deccan Cement's terms, as the plant meets them.

  On hold for an overdue March bill: no order of theirs is confirmed and
  nothing is loaded for them; what they send back is still taken in.
  Lifted, the same order confirms and ships.
  FOR destination, bales of 500, "Batch and month on the back", carried by
  Sharma Roadways: a new order records the first three, a new delivery the
  carrier. Changed on the customer afterwards, the order keeps what it
  recorded, and once anything has shipped its freight terms are fixed.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.accounting.trade_terms import freight_meta
from apps.sales.models import CustomerProfile, Delivery, DeliveryLine, SalesOrder, SalesOrderLine

from .tests_bales import BaleTestCase
from .tests_orders import TODAY


class CustomerTermsTestCase(BaleTestCase):
    def setUp(self):
        super().setUp()
        self.sharma = Party.objects.create(code="SRW", name="Sharma Roadways")
        PartyRoleAssignment.objects.create(party=self.sharma, role=PartyRole.VENDOR)
        self.profile = CustomerProfile.objects.create(
            party=self.cement, freight_terms="for_destination", sacks_per_bale=500,
            marking="Batch and month on the back", transporter=self.sharma)

    def order(self, bags="100", confirm=True):
        order = SalesOrder.objects.create(customer=self.cement, order_date=TODAY)
        SalesOrderLine.objects.create(order=order, item=self.bag, uom=self.pcs, warehouse=self.plant,
                                      quantity=Decimal(bags), unit_price=Decimal("12"))
        if confirm:
            order.confirm()
        return order

    def ship(self, order, bags="100"):
        delivery = Delivery.objects.create(sales_order=order, delivery_date=TODAY)
        DeliveryLine.objects.create(delivery=delivery, order_line=order.lines.get(), warehouse=self.plant,
                                    lot=self.b1, quantity_shipped=Decimal(bags))
        delivery.post()
        return delivery

    def hold(self, reason="March bill 45 days overdue"):
        self.profile.credit_hold, self.profile.credit_hold_reason = True, reason
        self.profile.save()


class CreditHoldTests(CustomerTermsTestCase):
    def test_a_hold_says_why(self):
        with self.assertRaisesMessage(ValidationError, "Say why they are on hold."):
            self.hold(reason="   ")

    def test_no_order_is_confirmed_while_held(self):
        order = self.order(confirm=False)
        self.hold()
        with self.assertRaisesMessage(ValidationError, "is on credit hold (March bill 45 days overdue): "
                                                       "no order of theirs is confirmed"):
            order.confirm()
        self.profile.credit_hold = False
        self.profile.save()
        order.confirm()

    def test_nothing_is_loaded_on_an_order_confirmed_before_the_hold(self):
        order = self.order()
        self.hold()
        with self.assertRaisesMessage(ValidationError, "nothing is dispatched to them until accounts lift it"):
            self.ship(order)
        self.assertFalse(Delivery.objects.filter(sales_order=order, posted=True).exists())

    def test_what_they_send_back_is_taken_in_whatever_the_hold(self):
        shipped = self.ship(self.order())
        self.hold()
        back = shipped.create_return(credit_invoices=False)
        self.assertTrue(Delivery.objects.get(pk=back.pk).posted)


class RecordedTermsTests(CustomerTermsTestCase):
    def test_a_new_order_records_how_the_goods_travel_and_are_packed(self):
        order = self.order(confirm=False)
        self.profile.freight_terms, self.profile.sacks_per_bale, self.profile.marking = "ex_works", 250, ""
        self.profile.save()
        order.refresh_from_db()
        self.assertEqual((order.freight_terms, order.sacks_per_bale, order.marking),
                         ("for_destination", 500, "Batch and month on the back"))

    def test_the_order_may_say_otherwise(self):
        order = SalesOrder.objects.create(customer=self.cement, order_date=TODAY, freight_terms="to_pay",
                                          sacks_per_bale=1000)
        self.assertEqual((order.freight_terms, order.sacks_per_bale), ("to_pay", 1000))

    def test_freight_terms_are_fixed_once_anything_has_shipped(self):
        order = self.order()
        order.freight_terms = "ex_works"
        order.save()
        self.ship(order)
        order.freight_terms = "to_pay"
        with self.assertRaisesMessage(ValidationError, "its freight terms and Incoterm cannot change now"):
            order.save()
        order.refresh_from_db()
        order.marking = "Brand only"  # how the next run marks them is still theirs to change
        order.save()

    def test_a_delivery_takes_their_usual_carrier(self):
        delivery = self.ship(self.order())
        self.assertEqual(delivery.transporter, self.sharma)

    def test_the_papers_print_the_terms_and_the_port_on_its_own_line(self):
        order = SalesOrder.objects.create(customer=self.cement, order_date=TODAY, incoterm="FOB",
                                          port_of_discharge="Jebel Ali")
        self.assertEqual(freight_meta(order), [["Freight", "FOR destination"], ["Incoterm", "FOB"],
                                               ["Port of discharge", "Jebel Ali"]])


class TheProfileItselfTests(CustomerTermsTestCase):
    def test_the_carrier_is_someone_we_deal_with(self):
        stranger = Party.objects.create(code="XYZ", name="Some Lorry")
        self.profile.transporter = stranger
        with self.assertRaisesMessage(ValidationError, "Some Lorry is not a vendor"):
            self.profile.save()

    def test_a_carrier_who_since_stopped_being_a_vendor_does_not_stop_other_changes(self):
        PartyRoleAssignment.objects.filter(party=self.sharma).delete()
        self.profile.credit_limit = Decimal("500000")
        self.profile.save()

    def test_a_bale_holds_some_sacks(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            CustomerProfile.objects.filter(pk=self.profile.pk).update(sacks_per_bale=0)
