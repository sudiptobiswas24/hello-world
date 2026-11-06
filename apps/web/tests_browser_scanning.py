"""
Scanning in stores (L9) in the browser, at a handheld's width, as the
people who scan: the store (Warehouse Staff) receives, picks, moves and
counts; the Production Supervisor issues to a run. The count goes on with
the network cut and its scans land once when it returns.

Numbers (calc_fix_scan/scenarios.py): batch A 30 (expires 01-12-2026) and
B 100 under a pick of 50: A takes 30, B the other 20. A count of A in
N-01: 5, then 2 with the network cut, makes 7.
"""

import re
from decimal import Decimal

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.inventory.models import StockCount, StockTransfer
from apps.inventory.scanning import ScanKey
from apps.inventory import tests_scanning as stores
from apps.manufacturing import tests_scanning as runs

from .tests_browser import PASSWORD, BrowserMixin

DAY = stores.DAY

PHONE = {"width": 390, "height": 844}


def scan(page, box, code):
    field = page.get_by_role("textbox", name=box)
    field.fill(code)
    field.press("Enter")


class ScanningInTheBrowserTests(BrowserMixin, stores.ScanTestCase, StaticLiveServerTestCase):
    def on_the_phone(self, role, path):
        page = self.sign_in(self.person(role), "/app/")
        page.set_viewport_size(PHONE)
        page.goto(f"{self.live_server_url}/app{path}")
        return page

    def lines(self, page):
        return page.get_by_role("list", name="Lines").locator("li")

    def test_the_store_counts_with_the_network_cut(self):
        count = StockCount.objects.create(count_date=DAY, warehouse=self.north, reason=self.reason)
        page = self.on_the_phone("Warehouse Staff", "/stores/scan")
        scan(page, "Code", "n-01")
        expect(page.get_by_role("list", name="What it is")).to_contain_text("N-01 in N - North")
        labels = page.get_by_role("link", name="Print bin labels")
        expect(labels).to_have_attribute("href", f"/api/inventory/bins/labels/?warehouse={self.north.pk}")
        sheet = page.request.get(f"{self.live_server_url}/api/inventory/bins/labels/?warehouse={self.north.pk}")
        self.assertEqual(sheet.status, 200)
        self.assertIn('aria-label="N-01"', sheet.text())
        offered = page.get_by_role("region", name="Open to scan into")
        expect(offered).to_contain_text(f"Draft count #{count.pk}")
        offered.get_by_role("link", name="Count").click()
        page.wait_for_url(re.compile(rf"/stores/scan-count/{count.pk}$"))
        self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), PHONE["width"])

        scan(page, "Bin", "N-01")
        expect(page.get_by_text("Counting in N-01")).to_be_visible()
        page.get_by_label("Quantity").fill("5")
        scan(page, "Item or batch", "A")
        expect(self.lines(page)).to_have_count(1)
        expect(self.lines(page).first).to_contain_text("5")

        self.context.set_offline(True)
        expect(page.get_by_text("No network: scans are kept on this device")).to_be_visible()
        page.get_by_label("Quantity").fill("2")
        scan(page, "Item or batch", "A")
        waiting = page.get_by_role("region", name="Waiting to be sent")
        expect(waiting).to_contain_text("A × 2 in N-01")
        self.assertEqual(count.lines.get().counted_quantity, Decimal("5"))

        self.context.set_offline(False)
        expect(waiting).to_have_count(0)
        expect(self.lines(page).first).to_contain_text("7")
        line = count.lines.get()
        self.assertEqual((line.lot, line.bin, line.counted_quantity), (self.a, self.n01, Decimal("7")))
        self.assertEqual(ScanKey.objects.filter(document="inventory.stockcount", document_id=count.pk).count(), 2)

    def test_the_store_picks_first_expired_first_and_takes_one_back(self):
        shipment = self.delivery("50")
        page = self.on_the_phone("Warehouse Staff", f"/stores/scan-pick/{shipment.pk}")
        scan(page, "Batch", "B")
        expect(page.get_by_role("alert")).to_contain_text("take batch A first (expires 01-12-2026)")
        scan(page, "Batch", "A")
        expect(self.lines(page).filter(has_text="batch A")).to_contain_text("30")
        scan(page, "Batch", "B")
        expect(self.lines(page).filter(has_text="batch B")).to_contain_text("20")
        self.assertEqual(sorted((line.lot.code, line.quantity_shipped) for line in shipment.lines.all()),
                         [("A", Decimal("30")), ("B", Decimal("20"))])

        self.lines(page).filter(has_text="batch B").get_by_role("button", name="Take off").click()
        expect(self.lines(page).filter(has_text="no batch")).to_contain_text("20")
        self.assertEqual(sorted((line.lot.code if line.lot else "", line.quantity_shipped)
                                for line in shipment.lines.all()), [("", Decimal("20")), ("A", Decimal("30"))])

        self.context.set_offline(True)
        expect(page.get_by_role("alert").filter(has_text="No network")).to_be_visible()
        self.context.set_offline(False)

    def test_the_store_receives_by_scan(self):
        from apps.purchasing.models import GoodsReceipt, PurchaseOrder, PurchaseOrderLine
        from apps.core.models import Party, PartyRole, PartyRoleAssignment

        vendor = Party.objects.create(code="V-1", name="Granules Ltd", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=vendor, role=PartyRole.VENDOR)
        order = PurchaseOrder.objects.create(vendor=vendor, order_date=DAY)
        PurchaseOrderLine.objects.create(order=order, item=self.plain, uom=self.each, quantity=Decimal("100"),
                                         unit_price=Decimal("5"), warehouse=self.north)
        order.confirm()
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=DAY)
        page = self.on_the_phone("Warehouse Staff", f"/stores/scan-receive/{receipt.pk}")
        for quantity in ("10", "15"):
            page.get_by_label("Quantity").fill(quantity)
            scan(page, "Item or batch", "P")
        expect(self.lines(page).first).to_contain_text("25")
        scan(page, "Item or batch", "S")
        expect(page.get_by_role("alert")).to_contain_text(f"S is not on {order.number}")
        self.lines(page).first.get_by_role("button", name="Remove").click()
        expect(page.get_by_text("Nothing scanned yet.")).to_be_visible()
        self.assertFalse(receipt.lines.exists())

    def test_the_store_moves_stock_bin_to_bin(self):
        transfer = StockTransfer.objects.create(transfer_date=DAY, from_warehouse=self.north,
                                                to_warehouse=self.south)
        page = self.on_the_phone("Warehouse Staff", f"/stores/scan-transfer/{transfer.pk}")
        scan(page, "From bin", "N-01")
        expect(page.get_by_text("Taking from N-01")).to_be_visible()
        page.get_by_label("Quantity").fill("12")
        scan(page, "Item or batch", "A")
        expect(self.lines(page).first).to_contain_text("12")
        scan(page, "To bin", "S-01")
        expect(page.get_by_role("textbox", name="To bin")).to_be_enabled()
        expect(page.get_by_text("To S-01")).to_be_visible()
        line = transfer.lines.get()
        self.assertEqual((line.lot, line.quantity, line.from_bin, line.to_bin),
                         (self.a, Decimal("12"), self.n01, self.s01))


class IssuingInTheBrowserTests(BrowserMixin, runs.IssueByScanTests, StaticLiveServerTestCase):
    def test_the_supervisor_issues_batches_to_a_run(self):
        from django.contrib.auth.models import User

        # The fixture made the supervisor already (its API client); the browser signs in as the same person.
        supervisor = User.objects.get(username="production_supervisor")
        supervisor.set_password(PASSWORD)
        supervisor.save()
        page = self.sign_in(supervisor, "/app/")
        page.set_viewport_size(PHONE)
        page.goto(f"{self.live_server_url}/app/stores/scan")
        page.get_by_role("region", name="Open to scan into").get_by_role("link", name="Issue").click()
        page.wait_for_url(re.compile(rf"/stores/scan-issue/{self.document.pk}$"))
        scan(page, "Batch", "V2")
        expect(page.get_by_role("alert")).to_contain_text("take batch V1 first")
        scan(page, "Batch", "V1")
        lines = page.get_by_role("list", name="Lines").locator("li")
        expect(lines.filter(has_text="batch V1")).to_contain_text("200")
        scan(page, "Batch", "V2")
        expect(lines.filter(has_text="batch V2")).to_contain_text("100")
        self.assertEqual(sorted((line.lot.code, line.quantity) for line in self.document.lines.all()),
                         [("V1", Decimal("200")), ("V2", Decimal("100"))])

    # The fixture's own tests are not run again here.
    test_fefo_names_the_batch_to_take_first = None
    test_the_first_batch_takes_what_it_has_and_the_next_the_rest = None
    test_a_batch_release_holds_is_refused_in_its_words = None
    test_a_scan_into_an_issue_posted_meanwhile_is_refused = None
    test_the_store_may_not_scan_into_a_run = None
    test_a_scanned_batch_is_taken_off_before_posting_and_its_quantity_comes_back = None
