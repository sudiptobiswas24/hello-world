"""
Scanning in stores (L9), asked of the API as the people who scan.

Numbers worked in /home/user/work/calc_fix_scan/scenarios.py: lot A 30
(expires 01-12-2026) and lot B 100 (expires 01-03-2027) under a delivery
line of 50; a count scan of 5 sent twice and one of 3 make 8.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType
from apps.core.models import Party, PartyRole, PartyRoleAssignment

from .bins import StorageBin
from .models import Item, StockCount, StockTransfer
from .scanning import ScanKey, gtin_refusal
from .tests_tracking import TrackingTestCase

DAY = datetime.date(2026, 6, 1)


class ScanTestCase(TrackingTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.a = self.lot("A", "2026-12-01")
        self.b = self.lot("B", "2027-03-01")
        self.receive(self.a, "30")
        self.receive(self.b, "100")
        self.receive(None, "40")
        self.n01 = StorageBin.objects.create(warehouse=self.north, code="N-01")
        self.s01 = StorageBin.objects.create(warehouse=self.south, code="S-01")
        self.revenue = Account.objects.create(code="4000", name="Revenue", account_type=AccountType.INCOME)
        self.customer = Party.objects.create(code="C-1", name="Acme", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def refused(self, response, words):
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn(words, response.content.decode())

    def delivery(self, quantity="50"):
        from apps.sales.models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine

        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY, currency=self.usd)
        line = SalesOrderLine.objects.create(order=order, item=self.batched, uom=self.each,
                                             quantity=Decimal(quantity), unit_price=Decimal("20"),
                                             revenue_account=self.revenue)
        order.confirm()
        delivery = Delivery.objects.create(sales_order=order, delivery_date=DAY)
        DeliveryLine.objects.create(delivery=delivery, order_line=line, warehouse=self.north,
                                    quantity_shipped=Decimal(quantity))
        return delivery


class GtinTests(ScanTestCase):
    def test_a_bad_check_digit_is_refused_in_words(self):
        self.assertEqual(gtin_refusal("4006381333931"), "")
        self.assertEqual(gtin_refusal("96385074"), "")
        self.assertIn("check digit 2; its digits make 1", gtin_refusal("4006381333932"))
        self.plain.gtin = "4006381333932"
        with self.assertRaises(ValidationError):
            self.plain.save()

    def test_a_gtin_already_another_items_is_refused(self):
        self.plain.gtin = "4006381333931"
        self.plain.save()
        self.batched.gtin = "4006381333931"
        with self.assertRaisesMessage(ValidationError, "already P's"):
            self.batched.save()

    def test_through_the_api_a_bad_gtin_answers_beside_its_field(self):
        manager = self.as_("Stores Manager")
        response = manager.patch(f"/api/inventory/items/{self.plain.pk}/", {"gtin": "4006381333932"}, format="json")
        if response.status_code == 403:  # Whoever keeps the item master.
            manager = APIClient()
            manager.force_authenticate(User.objects.create_superuser("keeper"))
            response = manager.patch(f"/api/inventory/items/{self.plain.pk}/", {"gtin": "4006381333932"},
                                     format="json")
        self.refused(response, "gtin")

    def test_blank_is_no_gtin_and_many_items_have_none(self):
        self.plain.gtin = ""
        self.plain.save()
        self.batched.gtin = "  "
        self.batched.save()
        self.assertEqual(Item.objects.filter(gtin__isnull=True).count(), 3)


class ResolverTests(ScanTestCase):
    def test_an_unknown_code_answers_nothing_in_words(self):
        response = self.as_("Warehouse Staff").get("/api/inventory/scan/", {"code": "NOPE-1"})
        self.assertEqual(response.status_code, 404)
        self.assertIn("Nothing you may read is coded NOPE-1", response.content.decode())

    def test_an_item_by_its_code_and_by_its_gtin(self):
        self.plain.gtin = "4006381333931"
        self.plain.save()
        clerk = self.as_("Warehouse Staff")
        by_code = clerk.get("/api/inventory/scan/", {"code": "p"}).json()["matches"]
        by_gtin = clerk.get("/api/inventory/scan/", {"code": "4006381333931"}).json()["matches"]
        self.assertEqual([(row["kind"], row["id"]) for row in by_code], [("item", self.plain.pk)])
        self.assertEqual([(row["kind"], row["id"], row["by"]) for row in by_gtin], [("item", self.plain.pk, "gtin")])

    def test_a_bin_a_lot_and_a_posted_count_by_its_number(self):
        count = StockCount.objects.create(count_date=DAY, warehouse=self.north, reason=self.reason)
        StockCount.objects.filter(pk=count.pk).update(number="CNT-0001", posted=True)
        clerk = self.as_("Warehouse Staff")
        self.assertEqual(clerk.get("/api/inventory/scan/", {"code": "N-01"}).json()["matches"][0]["kind"], "bin")
        self.assertEqual(clerk.get("/api/inventory/scan/", {"code": "A"}).json()["matches"][0]["id"], self.a.pk)
        found = clerk.get("/api/inventory/scan/", {"code": "cnt-0001"}).json()["matches"]
        self.assertEqual([(row["kind"], row["state"], row["actions"]) for row in found],
                         [("stock_count", "posted", [])])

    def test_a_code_the_person_may_not_read_answers_as_nothing(self):
        count = StockCount.objects.create(count_date=DAY, warehouse=self.north, reason=self.reason)
        StockCount.objects.filter(pk=count.pk).update(number="CNT-0001", posted=True)
        rep = self.as_("Sales Rep")
        response = rep.get("/api/inventory/scan/", {"code": "CNT-0001"})
        self.assertEqual(response.status_code, 404, response.content)

    def test_open_drafts_offer_what_each_person_may_do(self):
        delivery = self.delivery()
        count = StockCount.objects.create(count_date=DAY, warehouse=self.north, reason=self.reason)
        clerk = self.as_("Warehouse Staff").get("/api/inventory/scan/open/").json()
        self.assertIn(("delivery", delivery.pk, ["pick"]), [(row["kind"], row["id"], row["actions"]) for row in clerk])
        self.assertIn(("stock_count", count.pk, ["count"]), [(row["kind"], row["id"], row["actions"]) for row in clerk])
        # A rep reads deliveries and may not pick: none is offered, and no count at all.
        rep = self.as_("Sales Rep").get("/api/inventory/scan/open/").json()
        self.assertEqual(rep, [])


class CountByScanTests(ScanTestCase):
    def setUp(self):
        super().setUp()
        self.count = StockCount.objects.create(count_date=DAY, warehouse=self.north, reason=self.reason)
        self.clerk = self.as_("Warehouse Staff")
        self.url = f"/api/inventory/stock-counts/{self.count.pk}/scan/"

    def test_the_same_scan_sent_twice_counts_once(self):
        first = self.clerk.post(self.url, {"code": "A", "bin": "N-01", "quantity": "5", "key": "k1"}, format="json")
        again = self.clerk.post(self.url, {"code": "A", "bin": "N-01", "quantity": "5", "key": "k1"}, format="json")
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(again.status_code, 200, again.content)
        self.assertTrue(again.json()["again"])
        self.clerk.post(self.url, {"code": "A", "bin": "N-01", "quantity": "3", "key": "k2"}, format="json")
        line = self.count.lines.get()
        self.assertEqual((line.lot, line.bin, line.counted_quantity), (self.a, self.n01, Decimal("8")))
        self.assertEqual(ScanKey.objects.count(), 2)

    def test_a_key_of_another_document_is_refused(self):
        other = StockCount.objects.create(count_date=DAY, warehouse=self.north, reason=self.reason)
        self.clerk.post(self.url, {"code": "A", "quantity": "5", "key": "k1"}, format="json")
        response = self.clerk.post(f"/api/inventory/stock-counts/{other.pk}/scan/",
                                   {"code": "A", "quantity": "5", "key": "k1"}, format="json")
        self.refused(response, "already recorded on another document")

    def test_a_tracked_item_by_its_code_asks_for_the_batch(self):
        response = self.clerk.post(self.url, {"code": "S", "quantity": "1", "key": "k4"}, format="json")
        self.refused(response, "say which batch was counted")

    def test_a_bin_of_another_warehouse_is_refused(self):
        response = self.clerk.post(self.url, {"code": "P", "bin": "S-01", "quantity": "1", "key": "k5"}, format="json")
        self.refused(response, "has no bin coded S-01")

    def test_a_scan_into_a_count_posted_meanwhile_is_refused_in_words(self):
        self.clerk.post(self.url, {"code": "P", "quantity": "40", "key": "k6"}, format="json")
        StockCount.objects.filter(pk=self.count.pk).update(posted=True)
        response = self.clerk.post(self.url, {"code": "P", "quantity": "1", "key": "k7"}, format="json")
        self.refused(response, "was posted meanwhile")

    def test_a_scanned_line_is_removed_before_posting(self):
        line = self.clerk.post(self.url, {"code": "P", "quantity": "2", "key": "k8"}, format="json").json()["line"]
        response = self.clerk.delete(f"/api/inventory/stock-count-lines/{line['id']}/")
        self.assertEqual(response.status_code, 204, response.content)
        again = self.clerk.post(self.url, {"code": "P", "quantity": "2", "key": "k8"}, format="json")
        self.assertIsNone(again.json()["line"])
        self.assertFalse(self.count.lines.exists())

    def test_a_reader_may_not_scan(self):
        response = self.as_("Sales Rep").post(self.url, {"code": "P", "quantity": "1", "key": "k9"}, format="json")
        self.assertIn(response.status_code, (403, 404))


class TransferByScanTests(ScanTestCase):
    def setUp(self):
        super().setUp()
        self.transfer = StockTransfer.objects.create(transfer_date=DAY, from_warehouse=self.north,
                                                     to_warehouse=self.south)
        self.clerk = self.as_("Warehouse Staff")
        self.url = f"/api/inventory/stock-transfers/{self.transfer.pk}/scan/"

    def test_from_bin_goods_to_bin(self):
        source = self.clerk.post(self.url, {"code": "N-01", "stage": "from"}, format="json")
        self.assertEqual(source.json()["bin"]["id"], self.n01.pk)
        for quantity in ("12", "8"):
            self.clerk.post(self.url, {"code": "A", "stage": "goods", "quantity": quantity, "from_bin": "N-01"},
                            format="json")
        to = self.clerk.post(self.url, {"code": "S-01", "stage": "to"}, format="json")
        self.assertEqual(to.status_code, 200, to.content)
        line = self.transfer.lines.get()
        self.assertEqual((line.lot, line.quantity, line.from_bin, line.to_bin),
                         (self.a, Decimal("20"), self.n01, self.s01))

    def test_a_tracked_item_by_its_code_is_refused(self):
        response = self.clerk.post(self.url, {"code": "S", "stage": "goods", "quantity": "1"}, format="json")
        self.refused(response, "scan the batch label")

    def test_a_scan_into_a_transfer_that_moved_is_refused(self):
        StockTransfer.objects.filter(pk=self.transfer.pk).update(status="received")
        response = self.clerk.post(self.url, {"code": "P", "stage": "goods", "quantity": "1"}, format="json")
        self.refused(response, "has moved stock since")


class PickByScanTests(ScanTestCase):
    def setUp(self):
        super().setUp()
        self.shipment = self.delivery("50")
        self.clerk = self.as_("Warehouse Staff")
        self.url = f"/api/sales/deliveries/{self.shipment.pk}/scan/"

    def test_fefo_refuses_the_later_batch_and_names_the_first(self):
        response = self.clerk.post(self.url, {"code": "B"}, format="json")
        self.refused(response, "take batch A first (expires 01-12-2026)")

    def test_the_first_batch_takes_what_it_has_and_the_next_takes_the_rest(self):
        first = self.clerk.post(self.url, {"code": "A"}, format="json")
        self.assertEqual(first.status_code, 200, first.content)
        second = self.clerk.post(self.url, {"code": "B"}, format="json")
        self.assertEqual(second.status_code, 200, second.content)
        lines = sorted((line.lot.code, line.quantity_shipped) for line in self.shipment.lines.all())
        self.assertEqual(lines, [("A", Decimal("30")), ("B", Decimal("20"))])

    def test_a_lot_of_another_item_is_refused(self):
        other = self.lot("Z9", "2026-08-01", item=self.serialised)
        self.receive(other, "1")
        response = self.clerk.post(self.url, {"code": "Z9"}, format="json")
        self.refused(response, "Batch Z9 is S; nothing on")

    def test_a_lot_release_holds_is_refused(self):
        from . import tracking

        def held(item, lot, warehouse=None, action="issue"):
            return f"Batch {lot.code} is held by quality." if lot and lot.code == "A" else ""
        tracking.register_lot_hold(held)
        self.addCleanup(tracking.LOT_HOLDS.remove, held)
        response = self.clerk.post(self.url, {"code": "A"}, format="json")
        self.refused(response, "Batch A is held by quality.")
        # And FEFO looks past it: B is the first batch that may go.
        self.assertEqual(self.clerk.post(self.url, {"code": "B"}, format="json").status_code, 200)

    def test_an_item_code_asks_for_the_batch(self):
        response = self.clerk.post(self.url, {"code": "S"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_a_scan_into_a_delivery_posted_meanwhile_is_refused(self):
        type(self.shipment).objects.filter(pk=self.shipment.pk).update(posted=True)
        self.refused(self.clerk.post(self.url, {"code": "A"}, format="json"), "was posted meanwhile")

    def test_a_picked_batch_is_taken_off_before_posting_and_its_quantity_comes_back(self):
        line = self.clerk.post(self.url, {"code": "A"}, format="json").json()["line"]
        response = self.clerk.post(f"/api/sales/deliveries/{self.shipment.pk}/unpick/", {"line": line["id"]},
                                   format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([(row.lot, row.quantity_shipped) for row in self.shipment.lines.all()],
                         [(None, Decimal("50"))])

    def test_a_whole_line_picked_keeps_its_quantity_when_taken_off(self):
        self.receive(self.a, "20")  # A now holds 50: the whole line.
        line = self.clerk.post(self.url, {"code": "A"}, format="json").json()["line"]
        self.clerk.post(f"/api/sales/deliveries/{self.shipment.pk}/unpick/", {"line": line["id"]}, format="json")
        self.assertEqual([(row.lot, row.quantity_shipped) for row in self.shipment.lines.all()],
                         [(None, Decimal("50"))])

    def test_a_posted_delivery_is_not_unpicked(self):
        line = self.clerk.post(self.url, {"code": "A"}, format="json").json()["line"]
        type(self.shipment).objects.filter(pk=self.shipment.pk).update(posted=True)
        response = self.clerk.post(f"/api/sales/deliveries/{self.shipment.pk}/unpick/", {"line": line["id"]},
                                   format="json")
        self.refused(response, "its return is a customer return")


class ReceiveByScanTests(ScanTestCase):
    def setUp(self):
        super().setUp()
        from apps.purchasing.models import GoodsReceipt, PurchaseOrder, PurchaseOrderLine

        self.vendor = Party.objects.create(code="V-1", name="Granules Ltd", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)
        self.order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=DAY)
        PurchaseOrderLine.objects.create(order=self.order, item=self.plain, uom=self.each, quantity=Decimal("100"),
                                         unit_price=Decimal("5"), warehouse=self.north)
        self.order.confirm()
        self.receipt = GoodsReceipt.objects.create(purchase_order=self.order, receipt_date=DAY)
        self.clerk = self.as_("Warehouse Staff")
        self.url = f"/api/purchasing/goods-receipts/{self.receipt.pk}/scan/"

    def test_an_item_scanned_twice_counts_up_its_line(self):
        for quantity in ("10", "15"):
            response = self.clerk.post(self.url, {"code": "P", "quantity": quantity}, format="json")
            self.assertEqual(response.status_code, 200, response.content)
        line = self.receipt.lines.get()
        self.assertEqual(line.quantity_received, Decimal("25"))

    def test_an_item_not_on_the_order_is_refused(self):
        self.refused(self.clerk.post(self.url, {"code": "S"}, format="json"), "is not on")

    def test_a_scan_into_a_posted_receipt_is_refused(self):
        type(self.receipt).objects.filter(pk=self.receipt.pk).update(posted=True)
        self.refused(self.clerk.post(self.url, {"code": "P"}, format="json"), "was posted meanwhile")

    def test_the_lot_label_sheet_prints_each_batch_once(self):
        from apps.purchasing.models import GoodsReceiptLine

        lot = self.lot("RX-1", item=self.plain)
        GoodsReceiptLine.objects.create(receipt=self.receipt, order_line=self.order.lines.get(), lot=lot,
                                        quantity_received=Decimal("1"))
        response = self.clerk.get(f"/api/purchasing/goods-receipts/{self.receipt.pk}/lot-labels/")
        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertEqual(page.count("<svg"), 1)
        self.assertIn("RX-1", page)


class BinLabelTests(ScanTestCase):
    def test_a_warehouses_bins_print_as_code_128(self):
        from apps.core import barcode

        response = self.as_("Warehouse Staff").get("/api/inventory/bins/labels/", {"warehouse": self.north.pk})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn(barcode.svg("N-01", height=50), response.content.decode())
        self.assertNotIn("S-01", response.content.decode())
