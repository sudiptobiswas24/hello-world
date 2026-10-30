"""
The gate in the browser, as the people who keep it (L17, L18): the Security Guard records a lorry in,
weighs it from the bridge and sees it out; the Stores Manager approves a pass the guard wrote and the
guard takes back what returns; the stores names the weighbridge slip on a receipt and sees what was
short; the Purchase Manager settles the claim. Numbers from /home/user/work/calc_fix_gate/scenarios.py (A).
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Permission, User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.inventory.gate import GatePass, GatePassLine, GateVisit, PassStatus, VisitStatus, Weighing
from apps.inventory.tests_gate import GateTestCase
from apps.manufacturing.station_scale import post_reading
from apps.purchasing.tests_weight_claims import WeighedReceiptTestCase
from apps.purchasing.weight_claims import WeightClaim

from .tests_browser import BrowserMixin


class TheGateInTheBrowserTests(BrowserMixin, GateTestCase, StaticLiveServerTestCase):
    def test_the_guard_records_a_lorry_in_weighs_it_from_the_bridge_and_sees_it_out(self):
        page = self.sign_in(self.person("Security Guard"), "/app/stores/gate/new")
        page.get_by_label("Vehicle").fill("MH 12 AB 1234")
        page.get_by_label("Driver", exact=True).fill("Ramesh")
        page.get_by_label("For").select_option(label="In, against a purchase order")
        page.get_by_label("Papers").fill("PO-2026-00001")
        page.get_by_role("button", name="Create").click()
        expect(page.locator("main")).to_contain_text("MH12AB1234")
        visit = GateVisit.objects.get()
        page.get_by_role("button", name="Weigh").click()
        page.get_by_role("form", name="Weigh").get_by_role("button", name="Weigh").click()
        page.wait_for_url("**/stores/weighings/*")
        self.assertTrue(page.url.endswith(f"/stores/weighings/{Weighing.objects.get().pk}"))
        # Nothing typed: the guard is offered the bridge alone.
        expect(page.get_by_role("button", name="Gross from the scale")).to_be_visible()
        expect(page.get_by_role("button", name="Type a weight")).to_have_count(0)
        post_reading("WB-1", "25860.000", True)
        page.get_by_role("button", name="Gross from the scale").click()
        self.toast(page, "Gross weighed")
        post_reading("WB-1", "15920.000", True)
        page.get_by_role("button", name="Tare from the scale").click()
        self.toast(page, "Tare weighed")
        expect(page.locator("main")).to_contain_text("9940.000")
        self.assertEqual(Weighing.objects.get().net_kg(), Decimal("9940.000"))
        page.goto(f"{self.live_server_url}/app/stores/gate/{visit.pk}")
        page.get_by_role("button", name="Out", exact=True).click()
        self.toast(page, "Left")
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.OUT)
        self.assertEqual(self.problems, [])

    def test_the_stores_manager_approves_the_guards_pass_and_the_guard_takes_back_what_returns(self):
        visit = GateVisit.objects.create(vehicle_number="MH12AB1234", driver_name="Ramesh", purpose="gate_pass")
        made = GatePass.objects.create(kind="returnable", party_name="Shree Rewinding Works", visit=visit,
                                       expected_return_date=datetime.date.today() + datetime.timedelta(days=7))
        line = GatePassLine.objects.create(gate_pass=made, item=self.polymer, quantity=Decimal("3"))
        manager = self.sign_in(self.person("Stores Manager"), f"/app/stores/gate-passes/{made.pk}")
        manager.get_by_role("button", name="Approve").click()
        self.toast(manager, "Approved")
        guard = self.sign_in(self.person("Security Guard"), f"/app/stores/gate/{visit.pk}", page=self.new_page())
        expect(guard.get_by_role("button", name="Approve")).to_have_count(0)
        guard.get_by_role("button", name="Out", exact=True).click()
        self.toast(guard, "Left")
        guard.goto(f"{self.live_server_url}/app/stores/gate-passes/{made.pk}")
        guard.get_by_role("button", name="Back in").click()
        form = guard.get_by_role("form", name="Back in")
        form.get_by_label("What").select_option(str(line.pk))
        form.get_by_label("How many").fill("3")
        form.get_by_label("Condition").select_option("damaged")
        form.get_by_label("Note").fill("bearing seized")
        form.get_by_role("button", name="Back in").click()
        self.toast(guard, "Recorded")
        made.refresh_from_db()
        self.assertEqual(made.status, PassStatus.RETURNED)
        self.assertEqual(self.problems, [])


class WeighedReceiptInTheBrowserTests(BrowserMixin, WeighedReceiptTestCase, StaticLiveServerTestCase):
    def test_the_stores_receives_on_the_slip_and_the_purchase_manager_settles_the_claim(self):
        self.manager = None  # the API client of the fixture is not used here
        line = self.order()
        weighing = self.weighing()
        receipt = self.receipt(line, "9940", None, documented=None, post=False)
        stores = self.sign_in(self.person("Warehouse Staff"), f"/app/purchasing/goods-in/{receipt.pk}")
        stores.get_by_label("Weighbridge slip").select_option(str(weighing.pk))
        expect(stores.locator("main")).to_contain_text("9,940")
        invoice = stores.get_by_label("Invoice weight (kg)")
        invoice.fill("10000")
        invoice.blur()
        expect(stores.locator(".weight-comparison")).to_contain_text("beyond the tolerance")
        stores.get_by_role("button", name="Post receipt").click()
        self.toast(stores, "received")
        receipt.refresh_from_db()
        self.assertTrue(receipt.posted)
        self.assertEqual((receipt.weighing_id, receipt.documented_weight_kg), (weighing.pk, Decimal("10000.000")))

        claim = WeightClaim.objects.create(receipt=receipt, against="transporter", transporter=self.transporter)
        self.bill(line)
        buyer = self.sign_in(self.person("Purchase Manager"), f"/app/purchasing/weight-claims/{claim.pk}",
                             page=self.new_page())
        expect(buyer.locator("main")).to_contain_text("5,700.00")
        buyer.get_by_role("button", name="Settle").click()
        buyer.get_by_role("form", name="Settle").get_by_role("button", name="Settle").click()
        self.toast(buyer, "Settled")
        claim.refresh_from_db()
        self.assertEqual((claim.status, self.balance(self.claims), self.balance(self.grni)),
                         ("settled", Decimal("5700.00"), Decimal("0.00")))
        self.assertEqual(self.problems, [])
