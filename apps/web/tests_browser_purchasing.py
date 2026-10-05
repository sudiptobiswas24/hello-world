"""
Buying, in a browser, by the people who do it here: the buyer orders,
the store takes the goods in and names the batch on the bags, accounts
bill it and pay. Each step is read back from the database and the
ledger, not just the screen. Then the refusals a screen must show.
"""

import datetime
import re
from decimal import Decimal

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.accounting.models import Account, AccountType, Payment
from apps.core.models import Company, Party, PartyRole, PartyRoleAssignment
from apps.inventory.tracking import Lot
from apps.purchasing.models import (
    Bill,
    GoodsReceipt,
    PurchaseApprovalPolicy,
    PurchaseOrder,
)

from .tests_browser import BrowserTestCase


class PurchasingInTheBrowserTests(BrowserTestCase):
    def setUp(self):
        super().setUp()
        self.payable = Account.objects.create(code="2000", name="Payables", account_type=AccountType.LIABILITY)
        company = Company.get()
        company.default_payable_account = self.payable
        company.default_bank_account = self.bank
        company.save()
        self.vendor = Party.objects.create(code="V-1", name="Granule House", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)

    def url(self, path):
        return f"{self.live_server_url}/app{path}"

    def raise_order(self, page, quantity, price=None):
        page.goto(self.url("/purchasing/orders/new"))
        page.get_by_role("combobox", name="Vendor").fill("Granule")
        page.get_by_role("option", name=re.compile("Granule House")).click()
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/purchasing/orders/\d+$"))
        page.get_by_role("combobox", name="Item to add").fill("WDG")
        page.get_by_role("option", name=re.compile("WDG-1")).click()
        page.get_by_label("Quantity to add").fill(quantity)
        if price:
            page.get_by_label("Unit price to add").fill(price)
        page.get_by_role("button", name="Add line").click()
        return PurchaseOrder.objects.get(pk=int(page.url.rsplit("/", 1)[1]))

    def test_an_order_from_first_line_to_the_vendor_paid(self):
        self.item.tracking = "lot"
        self.item.save()

        # The buyer orders at the price agreed on the phone. They may not
        # take goods in.
        buyer = self.sign_in(self.person("Purchasing Clerk"), "/app/purchasing/orders")
        order = self.raise_order(buyer, "100", price="4")
        expect(buyer.locator(".lines tbody tr", has_text="Widget")).to_have_count(1)
        buyer.get_by_role("button", name="Confirm", exact=True).click()
        expect(buyer.locator(".pill", has_text="confirmed")).to_be_visible()
        order.refresh_from_db()
        line = order.lines.get()
        self.assertEqual((order.status, line.quantity, line.unit_price), ("confirmed", Decimal("100"), Decimal("4")))
        expect(buyer.get_by_role("button", name="Receive", exact=True)).to_have_count(0)

        # The store takes it in, names the batch, and posts.
        store = self.new_page()
        self.sign_in(self.person("Warehouse Staff"), f"/app/purchasing/orders/{order.pk}", page=store)
        store.get_by_role("button", name="Receive", exact=True).click()
        store.wait_for_url(re.compile(r"/purchasing/goods-in/\d+$"))
        receipt = GoodsReceipt.objects.get(pk=int(store.url.rsplit("/", 1)[1]))
        expect(store.get_by_role("note")).to_contain_text("Name the batch")
        batch = store.get_by_label("Batch of WDG-1 - Widget")
        batch.fill("PP-2611-A")
        batch.press("Enter")
        expect(store.get_by_role("note")).to_have_count(0)
        store.get_by_role("button", name="Post receipt").click()
        expect(store.locator(".pill", has_text="Received")).to_be_visible()
        receipt.refresh_from_db()
        self.assertTrue(receipt.posted)
        lot = Lot.objects.get(item=self.item, code="PP-2611-A")
        self.assertEqual(lot.on_hand_at(self.warehouse), Decimal("100"))
        self.assertEqual(self.balance(self.grni), Decimal("-400.00"))

        # Accounts bill it from the order, and pay it.
        accounts = self.new_page()
        self.sign_in(self.person("AP Manager"), f"/app/purchasing/orders/{order.pk}", page=accounts)
        accounts.get_by_role("button", name="Bill", exact=True).click()
        accounts.wait_for_url(re.compile(r"/purchasing/bills/\d+$"))
        bill = Bill.objects.get(pk=int(accounts.url.rsplit("/", 1)[1]))
        accounts.get_by_role("button", name="Post", exact=True).click()
        expect(accounts.locator(".toast", has_text="posted").first).to_be_visible()
        bill.refresh_from_db()
        self.assertTrue(bill.posted)
        self.assertEqual(bill.amount_due(), Decimal("400.00"))
        self.assertEqual(self.balance(self.grni), Decimal("0.00"))
        self.assertEqual(self.balance(self.payable), Decimal("-400.00"))

        accounts.get_by_role("link", name="Pay", exact=True).click()
        accounts.get_by_label("Amount").fill("400")
        accounts.get_by_label("Reference").fill("NEFT 7781")
        accounts.get_by_role("button", name="Record and post").click()
        accounts.wait_for_url(re.compile(r"/purchasing/payments/\d+\?bill="))
        payment = Payment.objects.get(reference="NEFT 7781")
        self.assertEqual((payment.posted, payment.direction, payment.party), (True, "disbursement", self.vendor))
        row = accounts.locator("tr.highlight")
        expect(row).to_contain_text(bill.number)
        row.get_by_role("button", name="Apply").click()
        expect(accounts.get_by_text("All applied")).to_be_visible()
        bill.refresh_from_db()
        self.assertEqual(bill.amount_due(), Decimal("0.00"))
        self.assertEqual(self.balance(self.payable), Decimal("0.00"))
        self.assertEqual(self.balance(self.bank), Decimal("-400.00"))
        self.assertEqual(self.problems, [])

    def test_a_line_with_no_agreed_price_is_refused_in_words(self):
        buyer = self.sign_in(self.person("Purchasing Clerk"), "/app/purchasing/orders")
        order = self.raise_order(buyer, "10")
        expect(buyer.locator(".toast-bad", has_text="No agreed price")).to_be_visible()
        self.assertEqual(order.lines.count(), 0)

    def test_an_order_over_the_limit_waits_for_a_signature(self):
        PurchaseApprovalPolicy.objects.create(code="STD", name="Standard", max_order_value=Decimal("100"))
        buyer = self.sign_in(self.person("Purchasing Clerk"), "/app/purchasing/orders")
        order = self.raise_order(buyer, "100", price="4")
        expect(buyer.get_by_role("note")).to_contain_text("above the 100")
        expect(buyer.get_by_role("button", name="Approve", exact=True)).to_have_count(0)
        buyer.get_by_role("button", name="Confirm", exact=True).click()
        expect(buyer.locator(".toast-bad", has_text="needs approval")).to_be_visible()
        order.refresh_from_db()
        self.assertEqual(order.status, "draft")

        approver = self.new_page()
        self.sign_in(self.person("AP Manager"), f"/app/purchasing/orders/{order.pk}", page=approver)
        approver.get_by_role("button", name="Approve", exact=True).click()
        expect(approver.get_by_role("note")).to_have_count(0)
        order.refresh_from_db()
        self.assertIsNotNone(order.approved_at)

        buyer.reload()
        buyer.get_by_role("button", name="Confirm", exact=True).click()
        expect(buyer.locator(".pill", has_text="confirmed")).to_be_visible()

    def test_a_tracked_receipt_without_its_batch_is_refused_and_stays_a_draft(self):
        self.item.tracking = "lot"
        self.item.save()
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date="2026-03-01")
        order.lines.create(item=self.item, uom=self.uom, quantity=Decimal("5"), unit_price=Decimal("4"))
        order.confirm()
        receipt = order.create_receipt(warehouse=self.warehouse)
        store = self.sign_in(self.person("Warehouse Staff"), f"/app/purchasing/goods-in/{receipt.pk}")
        store.get_by_role("button", name="Post receipt").click()
        expect(store.locator(".toast-bad", has_text="must say which")).to_be_visible()
        expect(store.locator(".pill", has_text="Draft")).to_be_visible()
        receipt.refresh_from_db()
        self.assertFalse(receipt.posted)

    def test_stock_on_hand_reads_the_ledger(self):
        page = self.sign_in(self.person("Warehouse Staff"), "/app/stores/on-hand")
        row = page.locator("tbody tr", has_text="WDG-1")
        expect(row).to_contain_text("500")
        expect(row).to_contain_text("2,000.00")  # 500 at 4
        page.get_by_label("Find an item").fill("nothing like it")
        expect(page.get_by_text("No item held matches that.")).to_be_visible()

    def test_every_role_opens_what_it_reads_without_being_refused(self):
        from django.contrib.auth.models import Permission

        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date="2026-03-01")
        order.lines.create(item=self.item, uom=self.uom, quantity=Decimal("5"), unit_price=Decimal("4"))
        order.confirm()
        receipt = order.create_receipt(warehouse=self.warehouse)
        receipt.post()
        bill = order.create_bill(self.payable)
        bill.post()
        screens = [
            ("purchasing.view_purchaseorder", f"/purchasing/orders/{order.pk}", order.number),
            ("purchasing.view_goodsreceipt", f"/purchasing/goods-in/{receipt.pk}", receipt.number),
            ("purchasing.view_bill", f"/purchasing/bills/{bill.pk}", bill.number),
            ("core.view_party", f"/purchasing/vendors/{self.vendor.pk}", self.vendor.name),
            ("inventory.view_stockmovement", "/stores/on-hand", "Stock on hand"),
            ("purchasing.view_purchaseorder", "/purchasing/aging", "Payables by age"),
        ]
        for role in ["Purchasing Clerk", "Warehouse Staff", "AP Manager", "Sales Rep"]:
            with self.subTest(role=role):
                person = self.person(role)
                held = {f"{app}.{code}" for app, code in Permission.objects.filter(
                    group__user=person).values_list("content_type__app_label", "codename")}
                page = self.new_page()
                self.sign_in(person, "/app/", page=page)
                for permission, path, heading in screens:
                    if permission not in held:
                        continue
                    page.goto(self.url(path))
                    expect(page.locator("main").first).to_contain_text(heading)
                    page.wait_for_load_state("networkidle")
                self.assertEqual(self.problems, [], role)

    def test_a_short_delivery_is_closed_short_and_reopened_by_the_buyer(self):
        from apps.purchasing.models import GoodsReceiptLine, PurchaseOrderLine

        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=datetime.date(2026, 3, 1),
                                             currency=self.usd)
        line = PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom,
                                                quantity=Decimal("10"), unit_price=Decimal("4"))
        order.confirm()
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=datetime.date(2026, 3, 2))
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=line, warehouse=self.warehouse,
                                        quantity_received=Decimal("7"))
        receipt.post()

        buyer = self.sign_in(self.person("Purchasing Clerk"), f"/app/purchasing/orders/{order.pk}")
        row = buyer.locator(".lines tbody tr", has_text="Widget")
        row.get_by_role("button", name=re.compile(r"^Close .*Widget short$")).click()
        close = row.get_by_role("button", name="Close", exact=True)
        expect(close).to_be_disabled()  # no reason, no close
        row.get_by_label(re.compile(r"^Why the rest of .*Widget will not come$")).fill("Vendor out of stock till March")
        close.click()
        expect(row.get_by_text("Closed short")).to_be_visible()
        line.refresh_from_db()
        self.assertEqual((line.closed_short_reason, line.quantity_open()),
                         ("Vendor out of stock till March", Decimal("0")))

        row.get_by_role("button", name="Reopen").click()
        expect(row.get_by_role("button", name=re.compile(r"^Close .*Widget short$"))).to_be_visible()
        line.refresh_from_db()
        self.assertEqual(line.quantity_open(), Decimal("3"))

        store = self.new_page()
        self.sign_in(self.person("Warehouse Staff"), f"/app/purchasing/orders/{order.pk}", page=store)
        expect(store.locator(".lines tbody tr", has_text="Widget")).to_be_visible()
        expect(store.get_by_role("button", name=re.compile("short$"))).to_have_count(0)
        self.assertEqual(self.problems, [])
