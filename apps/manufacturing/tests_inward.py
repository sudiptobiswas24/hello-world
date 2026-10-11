"""
Sunrise Cement sends 5,000 kg of their own granule (batch SC-PP-1) on
their challan SC/114, to be made into tape for their order.

  A run for their order draws 3,000 kg and hands 200 back: 2,800 used.
  1,000 kg goes back to them unused. 5,000 - 2,800 - 1,000 = 1,200 on
  the shelf, which is what the store holds.
  The company's own granule stays at 100 a kilogramme throughout: the
  customer's material is on the premises and not on the books.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.inventory.models import Lot, MovementType, StockMovement, Warehouse
from apps.inventory.transfers import StockTransfer
from apps.sales.models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine

from .inward import (
    CustomerMaterialReceipt,
    CustomerMaterialReceiptLine,
    CustomerMaterialReturn,
    CustomerMaterialReturnLine,
    register,
)
from .orders import IssueDirection, MaterialIssue, MaterialIssueLine
from .tests_orders import TODAY
from .tests_trace import TraceTestCase

ARRIVED = datetime.date(2026, 5, 25)


class InwardTestCase(TraceTestCase):
    def setUp(self):
        super().setUp()
        self.sunrise = Party.objects.create(code="SUN", name="Sunrise Cement")
        PartyRoleAssignment.objects.create(party=self.sunrise, role=PartyRole.CUSTOMER)
        self.held = Warehouse.objects.create(code="H-SUN", name="Held for Sunrise",
                                             held_for=self.sunrise)
        self.their_lot = Lot.objects.create(item=self.virgin, code="SC-PP-1")
        self.their_order = SalesOrder.objects.create(
            customer=self.sunrise, order_date=datetime.date(2026, 5, 20), currency=self.usd)
        self.their_line = SalesOrderLine.objects.create(
            order=self.their_order, item=self.tape, uom=self.kg, warehouse=self.plant,
            quantity=Decimal("4000"), unit_price=Decimal("20"))
        self.their_run = self.run_for(self.their_line)
        self.their_run.release(TODAY)

    def receive(self, quantity="5000", challan="SC/114", post=True):
        receipt = CustomerMaterialReceipt.objects.create(
            customer=self.sunrise, warehouse=self.held, received_on=ARRIVED,
            their_challan=challan, their_challan_date=ARRIVED)
        CustomerMaterialReceiptLine.objects.create(
            receipt=receipt, item=self.virgin, lot=self.their_lot, quantity=Decimal(quantity),
            declared_value=Decimal(quantity) * 110)
        if post:
            receipt.post()
        return receipt

    def draw(self, run, quantity, warehouse=None, lot=None, back=None):
        issue = MaterialIssue.objects.create(
            work_order=run, issue_date=TODAY, warehouse=warehouse or self.held,
            direction=IssueDirection.RETURN if back else IssueDirection.ISSUE)
        MaterialIssueLine.objects.create(
            issue=issue, item=self.virgin, quantity=Decimal(quantity), uom=self.kg,
            lot=lot or self.their_lot, line_number=1, returns_line=back)
        issue.post()
        return issue

    def send_back(self, receipt, quantity):
        sent = CustomerMaterialReturn.objects.create(
            customer=self.sunrise, warehouse=self.held, returned_on=TODAY)
        CustomerMaterialReturnLine.objects.create(
            material_return=sent, receipt_line=receipt.lines.get(), item=self.virgin,
            lot=self.their_lot, quantity=Decimal(quantity))
        sent.post()
        return sent

    def the_month(self):
        receipt = self.receive()
        issue = self.draw(self.their_run, "3000")
        self.draw(self.their_run, "200", back=issue.lines.get())
        self.send_back(receipt, "1000")
        return receipt

    def deliver(self, customer, lot, quantity="100"):
        order = SalesOrder.objects.create(customer=customer, order_date=TODAY,
                                          currency=self.usd)
        line = SalesOrderLine.objects.create(order=order, item=self.tape, uom=self.kg,
                                             warehouse=self.plant, quantity=Decimal(quantity),
                                             unit_price=Decimal("120"))
        order.confirm()
        delivery = Delivery.objects.create(sales_order=order, delivery_date=TODAY)
        DeliveryLine.objects.create(delivery=delivery, order_line=line, warehouse=self.plant,
                                    quantity_shipped=Decimal(quantity), lot=lot)
        delivery.post()
        return delivery


class TheRegisterTests(InwardTestCase):
    def test_received_used_returned_and_on_the_shelf_foot(self):
        self.the_month()
        (row,) = register(self.sunrise, as_of=TODAY)
        self.assertEqual(
            (row["received"], row["consumed"], row["returned"], row["on_hand"], row["unexplained"]),
            (Decimal("5000"), Decimal("2800"), Decimal("1000"), Decimal("1200"), Decimal("0")))
        self.assertEqual(row["overdue"], [])

    def test_a_year_on_what_is_left_is_flagged(self):
        self.the_month()
        (row,) = register(self.sunrise, as_of=ARRIVED + datetime.timedelta(days=366))
        (late,) = row["overdue"]
        self.assertEqual((late["their_challan"], late["left"]), ("SC/114", Decimal("1200")))

    def test_none_of_it_is_the_companys_value(self):
        before = self.virgin.average_cost()
        self.the_month()
        self.assertEqual(self.virgin.average_cost(), before)
        self.assertEqual(self.virgin.stock_value_at(self.held), Decimal("0.00"))
        self.assertEqual(self.virgin.available_at(self.held), 0)
        issue = MaterialIssue.objects.filter(warehouse=self.held,
                                             direction=IssueDirection.ISSUE).get()
        self.assertEqual(issue.posted_value, Decimal("0"))


class InTests(InwardTestCase):
    def test_into_a_store_held_for_them_only(self):
        other = Warehouse.objects.create(code="H-OTH", name="Held for another",
                                         held_for=self.customer)
        receipt = self.receive(post=False)
        receipt.warehouse = other
        receipt.save()
        with self.assertRaisesMessage(ValidationError, "is not held for SUN - Sunrise Cement"):
            receipt.post()
        receipt.warehouse = self.plant
        receipt.save()
        with self.assertRaisesMessage(ValidationError, "is not held for SUN - Sunrise Cement"):
            receipt.post()

    def test_their_challan_cannot_postdate_the_goods(self):
        receipt = self.receive(post=False)
        receipt.their_challan_date = ARRIVED + datetime.timedelta(days=1)
        receipt.save()
        with self.assertRaisesMessage(ValidationError, "dated after the goods arrived"):
            receipt.post()

    def test_a_vendors_consignment_does_not_move_the_companys_average_either(self):
        before = self.virgin.average_cost()
        consigned = Warehouse.objects.create(
            code="CONS", name="Consignment",
            consignment_vendor=Party.objects.create(code="VEN", name="Granule vendor"))
        StockMovement.objects.create(item=self.virgin, warehouse=consigned,
                                     movement_type=MovementType.RECEIPT, uom=self.kg,
                                     quantity=Decimal("5000"), unit_cost=Decimal("0"),
                                     lot=self.polymer_lot, occurred_at=timezone.now())
        self.assertEqual(self.virgin.average_cost(), before)

    def test_a_posted_receipt_is_not_edited(self):
        receipt = self.receive()
        receipt.their_challan = "SC/999"
        with self.assertRaisesMessage(ValidationError, "is posted. Void it"):
            receipt.save()

    def test_the_same_challan_once(self):
        self.receive()
        with self.assertRaisesMessage(ValidationError, "came in on"):
            self.receive(challan="SC/114")

    def test_withdrawn_only_while_it_is_all_still_here(self):
        receipt = self.receive()
        self.draw(self.their_run, "100")
        with self.assertRaisesMessage(ValidationError, "gone into runs"):
            receipt.void("Wrong challan")
        second = self.receive("50", challan="SC/115")
        # The same batch: 50 more arrived, and 4,950 of it is still here.
        second.void("Typed twice")
        self.assertEqual(self.their_lot.on_hand_at(self.held), Decimal("4900"))
        with self.assertRaisesMessage(ValidationError, "posted; void it"):
            receipt.delete()


class UsedTests(InwardTestCase):
    def test_only_in_their_own_work(self):
        self.receive()
        ours = self.order("1000")
        ours.release(TODAY)
        with self.assertRaisesMessage(ValidationError, "not for any customer's order"):
            self.draw(ours, "100")
        theirs_not = self.run_for(self.sold(), "1000")
        theirs_not.release(TODAY)
        with self.assertRaisesMessage(ValidationError, "is for CEM - Deccan Cement"):
            self.draw(theirs_not, "100")

    def test_back_to_the_shelf_it_left(self):
        self.receive()
        issue = self.draw(self.their_run, "300")
        with self.assertRaisesMessage(ValidationError, "it goes back there"):
            self.draw(self.their_run, "100", warehouse=self.plant, back=issue.lines.get())
        ours = self.draw(self.their_run, "100", warehouse=self.plant, lot=self.polymer_lot)
        with self.assertRaisesMessage(ValidationError, "it goes back there"):
            self.draw(self.their_run, "100", warehouse=self.held, lot=self.polymer_lot,
                      back=ours.lines.get())

    def test_a_vendors_consignment_is_bought_before_it_is_used(self):
        vendor = Party.objects.create(code="VEN", name="Granule vendor")
        consigned = Warehouse.objects.create(code="CONS", name="Consignment",
                                             consignment_vendor=vendor)
        StockMovement.objects.create(item=self.virgin, warehouse=consigned,
                                     movement_type=MovementType.RECEIPT, uom=self.kg,
                                     quantity=Decimal("500"), unit_cost=Decimal("0"),
                                     lot=self.polymer_lot, occurred_at=timezone.now())
        with self.assertRaisesMessage(ValidationError, "draw it into your own"):
            self.draw(self.their_run, "100", warehouse=consigned, lot=self.polymer_lot)


class BackToThemTests(InwardTestCase):
    def test_no_more_than_came_in_on_that_challan(self):
        receipt = self.receive("500")
        self.send_back(receipt, "300")
        with self.assertRaisesMessage(ValidationError, "has 200 left to return"):
            self.send_back(receipt, "250")

    def test_what_goes_back_is_what_came_in_on_that_line(self):
        receipt = self.receive("500")
        other_lot = Lot.objects.create(item=self.virgin, code="SC-PP-2")
        sent = CustomerMaterialReturn.objects.create(
            customer=self.sunrise, warehouse=self.held, returned_on=TODAY)
        CustomerMaterialReturnLine.objects.create(
            material_return=sent, receipt_line=receipt.lines.get(), item=self.virgin,
            lot=other_lot, quantity=Decimal("10"))
        with self.assertRaisesMessage(ValidationError, "brought in PP-RAFFIA batch SC-PP-1; this line sends back PP-RAFFIA batch SC-PP-2"):
            sent.post()

    def test_no_more_than_is_on_the_shelf(self):
        receipt = self.receive("500")
        self.draw(self.their_run, "400")
        with self.assertRaisesMessage(ValidationError, "go back to the customer"):
            self.send_back(receipt, "200")

    def test_a_return_withdrawn_puts_it_back(self):
        receipt = self.receive("500")
        sent = self.send_back(receipt, "300")
        with self.assertRaisesMessage(ValidationError, "Material has gone back"):
            receipt.void("Wrong")
        sent.void("Lorry turned back")
        self.assertEqual(self.their_lot.on_hand_at(self.held), Decimal("500"))
        self.send_back(receipt, "500")


class NotTheCompanysToMoveTests(InwardTestCase):
    def test_not_shipped_transferred_bought_into_or_made_into(self):
        self.receive()
        with self.assertRaisesMessage(ValidationError, "Stock has moved through H-SUN"):
            self.held.held_for = self.customer
            self.held.save()
        self.held.refresh_from_db()
        transfer = StockTransfer(from_warehouse=self.held, to_warehouse=self.plant)
        with self.assertRaisesMessage(ValidationError, "not the company's to transfer"):
            transfer._check_warehouses()
        entry = self.produce(self.their_run, "100", lot=Lot.objects.create(
            item=self.tape, code="TAPE-H"))
        entry.warehouse = self.held
        entry.save()
        with self.assertRaisesMessage(ValidationError, "Output is valued into the company's own"):
            entry.post()
        with self.assertRaisesMessage(ValidationError, "cannot hold a vendor's stock and a"):
            Warehouse.objects.create(code="BOTH", name="Both", held_for=self.sunrise,
                                     consignment_vendor=self.customer)

    def test_their_material_handed_back_whole_stops_nothing(self):
        self.receive()
        issue = self.draw(self.their_run, "1000")
        self.draw(self.their_run, "1000", back=issue.lines.get())
        self.draw(self.their_run, "1000", warehouse=self.plant, lot=self.polymer_lot)
        made = Lot.objects.create(item=self.tape, code="TAPE-OURS")
        self.produce(self.their_run, "900", lot=made).post()
        self.deliver(self.customer, made)

    def test_their_tape_goes_to_them_and_nobody_else(self):
        self.receive()
        self.draw(self.their_run, "1000")
        made = Lot.objects.create(item=self.tape, code="TAPE-SUN")
        self.produce(self.their_run, "900", lot=made).post()
        with self.assertRaisesMessage(ValidationError, "Sunrise Cement's material"):
            self.deliver(self.customer, made)
        self.deliver(self.sunrise, made)


class InwardApiTests(InwardTestCase):
    def test_received_posted_returned_and_registered(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("stores"))
        base = "/api/manufacturing/"
        response = client.post(base + "customer-material-receipts/", {
            "customer": self.sunrise.pk, "warehouse": self.held.pk,
            "received_on": "2026-05-25", "their_challan": "SC/114",
            "their_challan_date": "2026-05-25"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        receipt = response.json()["id"]
        response = client.post(base + "customer-material-receipt-lines/", {
            "receipt": receipt, "item": self.virgin.pk, "lot": self.their_lot.pk,
            "quantity": "500", "declared_value": "55000"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        line = response.json()["id"]
        response = client.post(base + f"customer-material-receipts/{receipt}/post/")
        self.assertEqual(response.status_code, 200, response.content)
        response = client.patch(base + f"customer-material-receipt-lines/{line}/",
                                {"quantity": "600"}, format="json")
        self.assertEqual(response.status_code, 400)
        response = client.post(base + "customer-material-returns/", {
            "customer": self.sunrise.pk, "warehouse": self.held.pk,
            "returned_on": "2026-06-01"}, format="json")
        back = response.json()["id"]
        client.post(base + "customer-material-return-lines/", {
            "material_return": back, "receipt_line": line, "item": self.virgin.pk,
            "lot": self.their_lot.pk, "quantity": "700"}, format="json")
        response = client.post(base + f"customer-material-returns/{back}/post/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("has 500 left to return", response.json()[0])
        rows = client.get(base + "customer-material-receipts/register/",
                          {"customer": self.sunrise.pk}).json()
        self.assertEqual((rows[0]["received"], rows[0]["on_hand"]), ("500", "500"))
