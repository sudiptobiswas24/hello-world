"""
What the purchasing and stores screens ask the server, asked as the
people who use them. The mirror of sales' tests_screens_api: Receive on
an order, the "to receive" and "still owed" lists, partial returns and
debit notes, batches named on arrival, and names on every list.
Refusals first.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.inventory.models import Warehouse
from apps.inventory.tracking import Lot

from .models import Bill, GoodsReceipt, PurchaseOrderLine, bills_still_owed
from .tests_prepayments import PrepaymentTestCase

DAY = datetime.date(2026, 1, 7)


class ScreensTestCase(PrepaymentTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user, _ = User.objects.get_or_create(username=role.replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def receive_order(self, order, role="Warehouse Staff", **body):
        return self.as_(role).post(f"/api/purchasing/purchase-orders/{order.pk}/receive/", body, format="json")

    def bill_order(self, order):
        bill = order.create_bill(self.payable, bill_date=DAY)
        bill.post()
        return bill


class ReceiveRefusalTests(ScreensTestCase):
    def test_a_draft_order_is_not_received(self):
        order = self.make_order(confirm=False)
        response = self.receive_order(order, warehouse=self.warehouse.pk)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("confirmed", str(response.json()))
        self.assertEqual(GoodsReceipt.objects.count(), 0)

    def test_a_line_arriving_nowhere_is_refused_by_the_field(self):
        response = self.receive_order(self.make_order())
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("warehouse", response.json())
        self.assertEqual(GoodsReceipt.objects.count(), 0)

    def test_one_draft_waits_at_a_time(self):
        order = self.make_order()
        self.assertEqual(self.receive_order(order, warehouse=self.warehouse.pk).status_code, 201)
        response = self.receive_order(order, warehouse=self.warehouse.pk)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("already waiting", str(response.json()))
        self.assertEqual(order.goods_receipts.count(), 1)

    def test_nothing_left_is_said_so(self):
        order = self.make_order(quantity="10")
        self.receive(order, "10")
        response = self.receive_order(order, warehouse=self.warehouse.pk)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("Nothing is left", str(response.json()))

    def test_a_clerk_who_may_not_receive_is_refused(self):
        order = self.make_order()
        response = self.receive_order(order, role="AP Manager", warehouse=self.warehouse.pk)
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(GoodsReceipt.objects.count(), 0)

    def test_a_mistyped_date_is_refused_in_words(self):
        response = self.receive_order(self.make_order(), warehouse=self.warehouse.pk, receipt_date="05/01/2026")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("is not a date", str(response.json()))


class ReceiveTests(ScreensTestCase):
    def test_drafts_what_is_to_come_and_moves_nothing(self):
        order = self.make_order(quantity="10")
        response = self.receive_order(order, warehouse=self.warehouse.pk, receipt_date="2026-01-06")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertFalse(body["posted"])
        self.assertEqual((body["order_number"], body["vendor_name"]), (order.number, "Supplier"))
        [line] = body["lines"]
        self.assertEqual(Decimal(line["quantity_received"]), Decimal("10"))
        self.assertEqual((line["warehouse"], line["description"], line["tracking"]),
                         (self.warehouse.pk, "WDG-1 - Widget", "none"))
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("0"))

    def test_after_part_received_drafts_the_rest(self):
        order = self.make_order(quantity="10")
        self.receive(order, "4")
        response = self.receive_order(order, warehouse=self.warehouse.pk)
        self.assertEqual(Decimal(response.json()["lines"][0]["quantity_received"]), Decimal("6"))

    def test_the_lines_own_warehouse_wins(self):
        other = Warehouse.objects.create(code="WH2", name="Godown")
        order = self.make_order()
        order.lines.update(warehouse=other)
        response = self.receive_order(order, warehouse=self.warehouse.pk)
        self.assertEqual(response.json()["lines"][0]["warehouse"], other.pk)

    def test_what_went_back_is_to_come_again(self):
        order = self.make_order(quantity="10")
        receipt = self.receive(order, "10")
        receipt.create_return(quantities={receipt.lines.get(): Decimal("3")}, debit_bills=False)
        response = self.receive_order(order, warehouse=self.warehouse.pk)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Decimal(response.json()["lines"][0]["quantity_received"]), Decimal("3"))

    def test_the_draft_posts_into_stock(self):
        order = self.make_order(quantity="10")
        receipt_id = self.receive_order(order, warehouse=self.warehouse.pk).json()["id"]
        response = self.as_("Warehouse Staff").post(f"/api/purchasing/goods-receipts/{receipt_id}/post_receipt/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("10"))
        self.assertEqual(order.lines.get().quantity_open(), Decimal("0"))


class BatchOnArrivalTests(ScreensTestCase):
    def setUp(self):
        super().setUp()
        self.item.tracking = "lot"
        self.item.save()

    def draft(self):
        order = self.make_order(quantity="10")
        return self.receive_order(order, warehouse=self.warehouse.pk).json()["lines"][0]

    def name_batch(self, line, batch, role="Warehouse Staff"):
        return self.as_(role).patch(f"/api/purchasing/goods-receipt-lines/{line['id']}/",
                                    {"batch": batch}, format="json")

    def test_an_unnamed_batch_is_refused_at_posting(self):
        line = self.draft()
        self.assertEqual(line["tracking"], "lot")
        response = self.as_("Warehouse Staff").post(
            f"/api/purchasing/goods-receipts/{line['receipt']}/post_receipt/")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("must say which", str(response.json()))
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("0"))

    def test_a_new_batch_is_made_with_the_line_and_posts(self):
        line = self.draft()
        response = self.name_batch(line, " PP-2611-A ")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["lot_code"], "PP-2611-A")
        lot = Lot.objects.get(item=self.item, code="PP-2611-A")
        response = self.as_("Warehouse Staff").post(
            f"/api/purchasing/goods-receipts/{line['receipt']}/post_receipt/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(lot.on_hand_at(self.warehouse), Decimal("10"))

    def test_a_known_batch_is_reused_not_doubled(self):
        Lot.objects.create(item=self.item, code="PP-1")
        line = self.draft()
        self.assertEqual(self.name_batch(line, "PP-1").status_code, 200)
        self.assertEqual(Lot.objects.filter(item=self.item, code="PP-1").count(), 1)

    def test_naming_a_new_batch_takes_the_right_to(self):
        from django.contrib.auth.models import Permission

        line = self.draft()
        # Someone who may correct a receipt line but not add batches.
        group = Group.objects.create(name="Receipt corrector")
        group.permissions.set(Permission.objects.filter(
            codename__in=["change_goodsreceiptline", "view_goodsreceiptline"]))
        response = self.name_batch(line, "PP-9", role="Receipt corrector")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("batch", response.json())
        self.assertFalse(Lot.objects.filter(code="PP-9").exists())

    def test_a_refused_line_leaves_no_batch(self):
        line = self.draft()
        response = self.as_("Warehouse Staff").patch(
            f"/api/purchasing/goods-receipt-lines/{line['id']}/",
            {"batch": "PP-7", "quantity_received": "-5"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json(), {"quantity_received": ["Quantity received must be more than 0."]})
        self.assertFalse(Lot.objects.filter(code="PP-7").exists())


class PartialCorrectionsTests(ScreensTestCase):
    """The API took no part for these; the models always had."""

    def test_part_of_a_receipt_goes_back(self):
        order = self.make_order(quantity="10")
        receipt = self.receive(order, "10")
        line = receipt.lines.get()
        response = self.as_("Warehouse Staff").post(
            f"/api/purchasing/goods-receipts/{receipt.pk}/return_receipt/",
            {"quantities": {str(line.pk): "4"}, "debit_bills": False}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("6"))
        self.assertEqual(order.lines.get().quantity_open(), Decimal("4"))
        self.assertEqual(Bill.objects.filter(debits__isnull=False).count(), 0)

    def test_a_return_debits_the_bill_unless_told_not_to(self):
        order = self.make_order(quantity="10")
        receipt = self.receive(order, "10")
        bill = self.bill_order(order)
        response = self.as_("Warehouse Staff").post(
            f"/api/purchasing/goods-receipts/{receipt.pk}/return_receipt/",
            {"quantities": {str(receipt.lines.get().pk): "2"}}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        bill.refresh_from_db()
        self.assertEqual(bill.amount_debited(), Decimal("10.00"))  # 2 at 5

    def test_part_of_a_bill_is_debited(self):
        order = self.make_order(quantity="10")
        self.receive(order, "10")
        bill = self.bill_order(order)
        response = self.as_("AP Manager").post(
            f"/api/purchasing/bills/{bill.pk}/debit_note/",
            {"quantities": {str(bill.lines.get().pk): "3"}, "memo": "Torn"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Decimal(response.json()["total"]), Decimal("15.00"))
        self.assertEqual(bill.amount_due(), Decimal("35.00"))

    def test_a_line_from_another_document_is_refused(self):
        first = self.receive(self.make_order(), "10")
        second = self.receive(self.make_order(), "10")
        response = self.as_("Warehouse Staff").post(
            f"/api/purchasing/goods-receipts/{first.pk}/return_receipt/",
            {"quantities": {str(second.lines.get().pk): "1"}}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("not on this receipt", str(response.json()))
        self.assertEqual(GoodsReceipt.objects.filter(reverses__isnull=False).count(), 0)

    def test_a_quantity_that_is_not_one_is_refused(self):
        bill = self.bill_order(self.receive(self.make_order(), "10").purchase_order)
        for typed in ["three", "NaN", "Infinity"]:
            with self.subTest(typed=typed):
                response = self.as_("AP Manager").post(
                    f"/api/purchasing/bills/{bill.pk}/debit_note/",
                    {"quantities": {str(bill.lines.get().pk): typed}}, format="json")
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("is not a quantity", str(response.json()))
        self.assertFalse(Bill.objects.filter(debits=bill).exists())


class ToReceiveTests(ScreensTestCase):
    def test_only_confirmed_orders_with_goods_to_come(self):
        owed = self.make_order(quantity="10")
        part = self.make_order(quantity="10")
        self.receive(part, "3")
        done = self.make_order(quantity="10")
        self.receive(done, "10")
        self.make_order(confirm=False)
        response = self.as_("Warehouse Staff").get("/api/purchasing/purchase-orders/", {"to_receive": "true"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual({row["id"] for row in response.json()}, {owed.pk, part.pk})
        self.assertEqual(response["X-Total-Count"], "2")

    def test_a_charge_is_never_to_come(self):
        from apps.accounting.models import ChargeType

        order = self.make_order(quantity="10", confirm=False)
        freight = ChargeType.objects.create(code="FRT", name="Freight", expense_account=self.expense)
        PurchaseOrderLine.objects.create(order=order, charge=freight, quantity=Decimal("1"),
                                         unit_price=Decimal("50"))
        order.confirm()
        self.receive(order, "10")
        response = self.as_("Warehouse Staff").get("/api/purchasing/purchase-orders/", {"to_receive": "true"})
        self.assertEqual(response.json(), [])


class BillsStillOwedAgreeWithAmountDueTests(ScreensTestCase):
    """bills_still_owed() decides in SQL what Bill.amount_due() decides in Python."""

    def test_every_way_of_settling(self):
        def bill():
            order = self.make_order(quantity="10", price="5")  # 50.00
            self.receive(order, "10")
            return self.bill_order(order)

        owed, settled = {}, {}
        owed["unpaid"] = bill()
        owed["part paid"] = bill()
        self.pay(owed["part paid"], "20")
        settled["paid"] = bill()
        self.pay(settled["paid"], "50")
        settled["debited"] = bill()
        settled["debited"].create_debit_note(memo="Wrong goods")
        # The debit only takes it to nothing: 30 paid, 50 debited.
        settled["part paid then debited"] = bill()
        self.pay(settled["part paid then debited"], "30")
        settled["part paid then debited"].create_debit_note(memo="Returned")
        owed["part debited"] = bill()
        owed["part debited"].create_debit_note(quantities={owed["part debited"].lines.get(): Decimal("4")})
        owed["paid by a bounced cheque"] = bill()
        bounced = self.pay(owed["paid by a bounced cheque"], "50").payment
        bounced.void(memo="Bounced")
        order = self.make_order(quantity="10", price="5")
        self.prepay(order, percent=30)
        self.receive(order, "10")
        owed["part met from a prepayment"] = self.bill_order(order)
        order = self.make_order(quantity="10", price="5")
        self.prepay(order, percent=100)
        self.receive(order, "10")
        settled["met from a prepayment"] = self.bill_order(order)

        answer = set(bills_still_owed(Bill.objects.all()))
        for name, document in owed.items():
            self.assertIn(document.pk, answer, f"{name}: due {document.amount_due()}")
        for name, document in settled.items():
            self.assertNotIn(document.pk, answer, f"{name}: due {document.amount_due()}")
        self.assertEqual(answer, {each.pk for each in Bill.objects.all()
                                  if each.posted and not each.debits_id and each.amount_due() > 0})
        self.assertEqual(owed["part debited"].amount_due(), Decimal("30.00"))
        self.assertEqual(owed["part met from a prepayment"].amount_due(), Decimal("35.00"))

    def test_the_list_says_the_same(self):
        open_bill = self.bill_order(self.receive(self.make_order(), "10").purchase_order)
        paid = self.bill_order(self.receive(self.make_order(), "10").purchase_order)
        self.pay(paid, "50")
        response = self.as_("AP Manager").get("/api/purchasing/bills/", {"open": "true"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([row["id"] for row in response.json()], [open_bill.pk])


class NamesOnListsTests(ScreensTestCase):
    def test_orders_bills_and_payments_say_who_and_what(self):
        order = self.make_order(quantity="10", price="5")
        self.receive(order, "10")
        bill = self.bill_order(order)
        applied = self.pay(bill, "50")
        client = self.as_("AP Manager")
        [row] = client.get("/api/purchasing/purchase-orders/").json()
        self.assertEqual((row["vendor_name"], row["receipt_status"], Decimal(row["total"])),
                         ("Supplier", "full", Decimal("50.00")))
        [line] = row["lines"]
        self.assertEqual((line["label"], Decimal(line["net_amount"]), Decimal(line["quantity_open"])),
                         ("WDG-1 - Widget", Decimal("50.00"), Decimal("0")))
        [row] = client.get("/api/purchasing/bills/").json()
        self.assertEqual((row["vendor_name"], row["lines"][0]["label"]), ("Supplier", "WDG-1 - Widget"))
        [row] = client.get("/api/purchasing/bill-payments/", {"payment": applied.payment_id}).json()
        self.assertEqual((row["bill_number"], row["payment_number"]), (bill.number, applied.payment.number))

    def test_the_stock_ledger_and_valuation_name_what_they_show(self):
        self.receive(self.make_order(quantity="10", price="5"), "10")
        client = self.as_("Warehouse Staff")
        [movement] = client.get("/api/inventory/stock-movements/").json()
        self.assertEqual((movement["item_sku"], movement["item_name"], movement["warehouse_code"]),
                         ("WDG-1", "Widget", "WH1"))
        report = client.get("/api/inventory/stock-reports/valuation/", {"warehouse": self.warehouse.pk}).json()
        [row] = report["rows"]
        self.assertEqual((row["item_id"], row["item_name"], row["warehouse_name"], row["uom"]),
                         (self.item.pk, "Widget", "Main", "each"))
        self.assertEqual(Decimal(str(row["quantity"])), Decimal("10"))
        other = Warehouse.objects.create(code="WH2", name="Godown")
        self.assertEqual(client.get("/api/inventory/stock-reports/valuation/",
                                    {"warehouse": other.pk}).json()["rows"], [])

    def test_a_new_vendor_is_made_with_its_role(self):
        response = self.as_("Purchasing Clerk").post(
            "/api/core/parties/", {"code": "V-9", "name": "Granule House", "role": "vendor"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        listed = self.as_("Purchasing Clerk").get("/api/core/parties/", {"role_assignments__role": "vendor"}).json()
        self.assertIn(response.json()["id"], [row["id"] for row in listed])

    def test_the_lists_do_not_ask_once_per_row(self):
        def count(url):
            from django.db import connection
            from django.test.utils import CaptureQueriesContext

            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(client.get(url).status_code, 200)
            return len(queries)

        # Someone who reads all three: orders, bills and what arrived.
        client = self.as_("AP Manager")
        self.as_("Warehouse Staff")
        User.objects.get(username="AP_Manager").groups.add(Group.objects.get(name="Warehouse Staff"))
        for _ in range(2):
            self.bill_order(self.receive(self.make_order(), "10").purchase_order)
        urls = ["/api/purchasing/purchase-orders/", "/api/purchasing/bills/",
                "/api/purchasing/goods-receipts/"]
        for url in urls:
            count(url)
        before = {url: count(url) for url in urls}
        for _ in range(3):
            self.bill_order(self.receive(self.make_order(), "10").purchase_order)
        self.assertEqual({url: count(url) for url in urls}, before)



class ApprovalOverTheApiTests(ScreensTestCase):
    """Purchasing had the permission and the tiers and no endpoint to use them."""

    def setUp(self):
        super().setUp()
        from .models import PurchaseApprovalPolicy

        self.policy = PurchaseApprovalPolicy.objects.create(code="STD", name="Standard", max_order_value=Decimal("40"))

    def test_what_holds_it_up_is_said(self):
        order = self.make_order(quantity="10", price="5", confirm=False)  # 50
        body = self.as_("Purchasing Clerk").get(f"/api/purchasing/purchase-orders/{order.pk}/approval/").json()
        self.assertEqual(body["status"], "pending")
        self.assertIn("above the 40", " ".join(body["reasons"]))

    def test_the_buyer_cannot_sign_their_own_spend(self):
        order = self.make_order(quantity="10", price="5", confirm=False)
        response = self.as_("Purchasing Clerk").post(f"/api/purchasing/purchase-orders/{order.pk}/approve/")
        self.assertEqual(response.status_code, 403, response.content)
        order.refresh_from_db()
        self.assertIsNone(order.approved_at)

    def test_an_approver_outside_the_tier_is_refused_by_name(self):
        from .models import ApprovalTier

        ApprovalTier.objects.create(policy=self.policy, group=Group.objects.get(name="Controller"),
                                    up_to_amount=Decimal("1000"))
        order = self.make_order(quantity="10", price="5", confirm=False)
        response = self.as_("AP Manager").post(f"/api/purchasing/purchase-orders/{order.pk}/approve/")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("cannot approve", str(response.json()))

    def test_approved_it_confirms(self):
        order = self.make_order(quantity="10", price="5", confirm=False)
        clerk = self.as_("Purchasing Clerk")
        self.assertEqual(clerk.post(f"/api/purchasing/purchase-orders/{order.pk}/confirm/").status_code, 400)
        response = self.as_("AP Manager").post(f"/api/purchasing/purchase-orders/{order.pk}/approve/",
                                               {"note": "Monsoon stock"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(clerk.post(f"/api/purchasing/purchase-orders/{order.pk}/confirm/").status_code, 200)
        order.refresh_from_db()
        self.assertEqual((order.status, order.approval_note), ("confirmed", "Monsoon stock"))
