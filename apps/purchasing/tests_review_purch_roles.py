"""
g22 review_purch: who may, asked of the API as a person in the role (the brief's AP Clerk is the AP Manager
here: setup_roles has no "AP Clerk"), and the paths a confirmed order can still be changed by.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from apps.accounting.models import Account, AccountType, Tax, TaxGroup, TaxScope, TdsRate, TdsSection
from apps.core.tests_roles import RolesTestCase
from apps.purchasing.models import Bill, BillLine, PurchaseApprovalPolicy, PurchaseOrder, PurchaseOrderLine
from apps.purchasing.tests_returns_and_landed import ProbeCase
from apps.purchasing.tests_tds import confirmed_rate
from apps.sales import tests_guards
from apps.core.tests_second_factor import staff_session

D = datetime.date
Z = Decimal
ROLES = ("Controller", "AP Manager", "Purchasing Clerk", "Stores Manager", "Bookkeeper")


class Who:
    def who(self, role):
        self._clients = getattr(self, "_clients", {})
        if role not in self._clients:
            self._clients[role] = self.as_(role)
        return self._clients[role]


class WhoMayKeepRatesNotesAndReturnsTests(Who, RolesTestCase, ProbeCase):
    def setUp(self):
        ProbeCase.setUp(self)
        RolesTestCase.setUp(self)
        self.tcs = Account.objects.create(code="1460", name="TCS receivable", account_type=AccountType.ASSET)
        self.tds_payable = Account.objects.create(code="2250", name="TDS payable", account_type=AccountType.LIABILITY)
        self.section = TdsSection.objects.create(code="194C", name="Contractors", mode="whole",
                                                 payable_account=self.tds_payable)

    def note(self, number=""):
        bill = self.hand_bill(None, "10", "100", link=False)
        bill.post()
        return Bill.objects.get(pk=bill.pk).create_debit_note(quantities={bill.lines.get(): Z("4")})

    def test_01_tds_rates_who_reads_creates_and_confirms(self):
        pending = TdsRate.objects.create(section=self.section, applies_from=D(2026, 9, 1), rate_percent=Z("2"),
                                         no_pan_rate_percent=Z("20"))
        seen = {}
        for role in ROLES:
            client = self.who(role)
            read = client.get("/api/accounting/tds-rates/").status_code
            made = client.post("/api/accounting/tds-rates/", {
                "section": self.section.pk, "applies_from": "2026-10-01", "rate_percent": "2",
                "no_pan_rate_percent": "20"}, format="json").status_code
            # The figures read go with it (review F9).
            confirmed = client.post(f"/api/accounting/tds-rates/{pending.pk}/confirm/", {
                "section": pending.section_id, "applies_from": str(pending.applies_from),
                "rate_percent": str(pending.rate_percent), "no_pan_rate_percent": str(pending.no_pan_rate_percent)},
                format="json").status_code
            seen[role] = (read, made, confirmed)
            TdsRate.objects.filter(pk=pending.pk).update(confirmed_on=None, confirmed_by=None)
            TdsRate.objects.filter(applies_from=D(2026, 10, 1)).delete()
        self.assertEqual(seen["Controller"], (200, 201, 200), seen)
        self.assertEqual(seen["AP Manager"], (200, 403, 403), seen)
        for role in ("Purchasing Clerk", "Stores Manager", "Bookkeeper"):
            self.assertEqual(seen[role], (403, 403, 403), seen)

    def test_02_a_rate_a_deduction_used_is_refused_in_words_over_the_api(self):
        rate = confirmed_rate(self.section, "2", "20")
        from apps.accounting.models import PartyTaxProfile
        PartyTaxProfile.objects.create(party=self.vendor, pan="AAAPL1234C", tds_section=self.section)
        bill = self.hand_bill(None, "1", "50000", link=False)
        bill.post()
        Bill.objects.get(pk=bill.pk).deduct_tds()
        client = self.who("Controller")
        patched = client.patch(f"/api/accounting/tds-rates/{rate.pk}/", {"rate_percent": "5"}, format="json")
        deleted = client.delete(f"/api/accounting/tds-rates/{rate.pk}/")
        self.assertEqual((patched.status_code, deleted.status_code), (400, 400), (patched.content, deleted.content))
        self.assertEqual(TdsRate.objects.get(pk=rate.pk).rate_percent, Z("2"))

    def test_03_who_records_the_suppliers_credit_note_number(self):
        note = self.note()
        url = f"/api/purchasing/bills/{note.pk}/supplier_note/"
        body = {"supplier_note_number": "CN-9", "supplier_note_date": "2026-01-20"}
        codes = {role: self.who(role).post(url, body, format="json").status_code
                 for role in ("Purchasing Clerk", "Stores Manager", "Bookkeeper")}
        self.assertEqual(codes, {"Purchasing Clerk": 403, "Stores Manager": 403, "Bookkeeper": 403})
        self.assertEqual(self.who("AP Manager").post(url, body, format="json").status_code, 200)
        again = self.who("AP Manager").post(url, {**body, "supplier_note_number": "CN-10"}, format="json")
        self.assertEqual(again.status_code, 400)
        self.assertEqual(Bill.objects.get(pk=note.pk).supplier_note_number, "CN-9")

    def test_04_a_purchasing_clerk_cannot_set_the_note_number_on_a_posted_note_by_a_plain_patch(self):
        note = self.note()
        for role in ("Purchasing Clerk", "AP Manager"):
            response = self.who(role).patch(f"/api/purchasing/bills/{note.pk}/",
                                            {"supplier_note_number": "CN-77", "supplier_note_date": "2026-01-20"},
                                            format="json")
            self.assertGreaterEqual(response.status_code, 400, (role, response.status_code))
        self.assertEqual(Bill.objects.get(pk=note.pk).supplier_note_number, "")

    def test_05_tcs_on_a_posted_bill_is_not_patchable_and_a_clerk_can_key_it_on_a_draft(self):
        draft = self.who("Purchasing Clerk").post("/api/purchasing/bills/", {
            "vendor": self.vendor.pk, "bill_date": "2026-01-10", "payable_account": self.payable.pk,
            "tcs_amount": "1000", "tcs_account": self.tcs.pk, "tcs_section": "206C(1F)"}, format="json")
        self.assertEqual(draft.status_code, 201, draft.content)
        bill = Bill.objects.get(pk=draft.json()["id"])
        BillLine.objects.create(bill=bill, description="Truck", quantity=Z("1"), unit_price=Z("100000"),
                                expense_account=self.expense)
        self.assertEqual(self.who("Purchasing Clerk").post(f"/api/purchasing/bills/{bill.pk}/post_bill/").status_code,
                         403)
        self.assertEqual(self.who("AP Manager").post(f"/api/purchasing/bills/{bill.pk}/post_bill/").status_code, 200)
        for role in ("Purchasing Clerk", "AP Manager", "Controller"):
            response = self.who(role).patch(f"/api/purchasing/bills/{bill.pk}/", {"tcs_amount": "5000"},
                                            format="json")
            self.assertGreaterEqual(response.status_code, 400, (role, response.status_code))
        self.assertEqual(Bill.objects.get(pk=bill.pk).tcs_amount, Z("1000.00"))

    def test_06_deducting_tds_is_not_open_to_a_clerk_or_stores(self):
        from apps.accounting.models import PartyTaxProfile
        confirmed_rate(self.section, "2", "20")
        PartyTaxProfile.objects.create(party=self.vendor, pan="AAAPL1234C", tds_section=self.section)
        bill = self.hand_bill(None, "1", "50000", link=False)
        bill.post()
        codes = {role: self.who(role).post(f"/api/purchasing/bills/{bill.pk}/deduct_tds/", {}, format="json").status_code
                 for role in ("Purchasing Clerk", "Stores Manager", "Bookkeeper")}
        self.assertEqual(codes, {"Purchasing Clerk": 403, "Stores Manager": 403, "Bookkeeper": 403})


    def test_07_whoever_may_not_post_bills_cannot_raise_a_posted_debit_note_by_returning_goods(self):
        """Pre-existing: return_receipt asks post_goodsreceipt, and its default debits the bills (posted at once)."""
        order = self.order("10", "100")
        receipt = self.receive_on(order, "10", D(2026, 1, 5))
        self.hand_bill(order.lines.get(), "10", "100", order=order).post()
        response = self.who("Warehouse Staff").post(f"/api/purchasing/goods-receipts/{receipt.pk}/return_receipt/",
                                                    {"quantities": {str(receipt.lines.get().pk): "4"}}, format="json")
        notes = Bill.objects.filter(debits__isnull=False, posted=True).count()
        self.assertTrue(notes == 0, f"HTTP {response.status_code}: Warehouse Staff (no purchasing.post_bill) posted {notes} debit note(s)")

    def test_08_a_rate_confirmed_twice_keeps_the_first_confirmer(self):
        rate = TdsRate.objects.create(section=self.section, applies_from=D(2026, 9, 1), rate_percent=Z("2"),
                                      no_pan_rate_percent=Z("20"))
        first, second = User.objects.create_user("first"), User.objects.create_user("second")
        TdsRate.objects.get(pk=rate.pk).confirm(first)
        with self.assertRaisesMessage(ValidationError, "is already confirmed"):
            TdsRate.objects.get(pk=rate.pk).confirm(second)
        self.assertEqual(TdsRate.objects.get(pk=rate.pk).confirmed_by_id, first.pk)


class ConfirmedPurchaseOrderPathsTests(Who, RolesTestCase, ProbeCase):
    def setUp(self):
        ProbeCase.setUp(self)
        RolesTestCase.setUp(self)
        PurchaseApprovalPolicy.objects.create(code="STD", name="Standard", max_order_value=Z("1000"))
        group = TaxGroup.objects.create(code="GST", name="GST")
        itc = Account.objects.create(code="1300", name="GST input", account_type=AccountType.ASSET)
        self.gst = Tax.objects.create(code="GST18", name="GST 18%", rate=Z("18"), group=group, scope=TaxScope.BOTH,
                                      collected_account=itc, paid_account=itc)

    def confirmed(self):
        order = self.order("18", "50")
        return order, order.lines.get()

    def test_01_a_clerk_patching_tax_onto_a_confirmed_line_cannot_take_the_order_past_its_limit(self):
        order, line = self.confirmed()
        response = self.who("Purchasing Clerk").patch(f"/api/purchasing/purchase-order-lines/{line.pk}/",
                                                      {"taxes": [self.gst.pk]}, format="json")
        total = PurchaseOrder.objects.get(pk=order.pk).total()
        self.assertTrue(response.status_code >= 400 or total <= Z("1000"),
                        f"HTTP {response.status_code}: order confirmed at 900 under a 1,000 limit now totals {total}")

    def test_02_a_clerk_raising_the_price_of_a_confirmed_line_past_the_limit_is_refused_in_words(self):
        order, line = self.confirmed()
        response = self.who("Purchasing Clerk").patch(f"/api/purchasing/purchase-order-lines/{line.pk}/",
                                                      {"unit_price": "60"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(PurchaseOrder.objects.get(pk=order.pk).total(), Z("900.00"))

    def test_03_the_admin_changelist_cannot_delete_a_received_order_line_in_bulk(self):
        order, line = self.confirmed()
        self.receive_on(order, "10", D(2026, 1, 5))
        draft = PurchaseOrderLine.objects.create(order=self.order("5", "5", confirm=False), item=self.item,
                                                 uom=self.uom, quantity=Z("1"), unit_price=Z("5"))
        admin = User.objects.create_superuser("root", "r@x.y", "x")
        staff_session(self.client, admin)
        url = "/admin/purchasing/purchaseorderline/"
        self.client.post(url, {"action": "delete_selected", "_selected_action": [draft.pk], "post": "yes"})
        self.assertFalse(PurchaseOrderLine.objects.filter(pk=draft.pk).exists(), "the probe cannot reach the admin")
        self.client.post(url, {"action": "delete_selected", "_selected_action": [line.pk], "post": "yes"})
        self.assertTrue(PurchaseOrderLine.objects.filter(pk=line.pk).exists(), "a line received 10 is gone")


class ConfirmedSalesOrderPathsTests(tests_guards.TradeRuleCase):
    def test_01_tax_patched_onto_a_confirmed_sales_line_cannot_take_the_order_past_its_limit(self):
        from apps.sales.models import ApprovalPolicy, SalesOrder

        ApprovalPolicy.objects.create(code="STD", name="Standard", max_order_value=Z("110"))
        order = self.make_order("1", "100")
        group = TaxGroup.objects.create(code="GST", name="GST")
        out = Account.objects.create(code="2300", name="GST output", account_type=AccountType.LIABILITY)
        gst = Tax.objects.create(code="GST18", name="GST 18%", rate=Z("18"), group=group, scope=TaxScope.BOTH,
                                 collected_account=out, paid_account=out)
        response = self.as_("Sales Rep").patch(f"/api/sales/sales-order-lines/{order.lines.get().pk}/",
                                               {"taxes": [gst.pk]}, format="json")
        total = SalesOrder.objects.get(pk=order.pk).total()
        self.assertTrue(response.status_code >= 400 or total <= Z("110"),
                        f"HTTP {response.status_code}: order confirmed at 100 under a 110 limit now totals {total}")

    def test_02_the_ar_manager_who_may_patch_it_cannot_take_the_order_past_its_limit(self):
        """The Sales Rep is refused the patch (403, test_01); the AR Manager may make it, and is asked the policy."""
        from apps.sales.models import ApprovalPolicy, SalesOrder

        ApprovalPolicy.objects.create(code="STD", name="Standard", max_order_value=Z("110"))
        order = self.make_order("1", "100")
        group = TaxGroup.objects.create(code="GST", name="GST")
        out = Account.objects.create(code="2300", name="GST output", account_type=AccountType.LIABILITY)
        gst = Tax.objects.create(code="GST18", name="GST 18%", rate=Z("18"), group=group, scope=TaxScope.BOTH,
                                 collected_account=out, paid_account=out)
        response = self.as_("AR Manager").patch(f"/api/sales/sales-order-lines/{order.lines.get().pk}/",
                                                {"taxes": [gst.pk]}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("above the 110.00 limit", str(response.json()))
        total = SalesOrder.objects.get(pk=order.pk).total()
        self.assertTrue(response.status_code >= 400 or total <= Z("110"),
                        f"HTTP {response.status_code}: order confirmed at 100 under a 110 limit now totals {total}")
