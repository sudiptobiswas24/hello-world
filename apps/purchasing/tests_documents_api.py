"""
Requisitions, requests for quotation and blanket orders through the API,
as the people who use them, and the holes each had once a screen could
reach it.

A requisition is asked for, approved by whoever holds the budget, and
ordered; what was approved is what is ordered, so its lines are fixed
from the moment it is submitted. An RFQ's lines are fixed when it goes
out, quotes come in only while it is out, and the award is the record
of how the vendor was chosen. A blanket order's price and volume are
fixed once confirmed, and releases call it off.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.core.models import Party, PartyRole, PartyRoleAssignment

from .models import (
    BlanketOrder,
    PurchaseOrder,
    PurchaseRequisition,
    PurchaseRequisitionLine,
    RequisitionStatus,
)
from .tests_lifecycle import PurchasingLifecycleTestCase


class DocumentsTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.employee = Party.objects.create(code="E-1", name="Dana")
        PartyRoleAssignment.objects.create(party=self.employee, role=PartyRole.EMPLOYEE)
        self.second = Party.objects.create(code="V-2", name="Beta Supplies", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.second, role=PartyRole.VENDOR)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def ok(self, response, status=200):
        self.assertEqual(response.status_code, status, response.content)
        return response.json()

    def refused(self, response, words, status=400):
        self.assertEqual(response.status_code, status, response.content)
        self.assertIn(words, response.content.decode())


class RequisitionTests(DocumentsTestCase):
    def asked(self, manager):
        made = self.ok(manager.post("/api/purchasing/requisitions/", {
            "requested_by": self.employee.pk, "request_date": "2026-01-05",
            "justification": "Granules for February"}, format="json"), 201)
        line = self.ok(manager.post("/api/purchasing/requisition-lines/", {
            "requisition": made["id"], "item": self.item.pk, "uom": self.uom.pk, "quantity": "10",
            "estimated_price": "5", "expense_account": self.expense.pk}, format="json"), 201)
        return made["id"], line["id"]

    def test_asked_for_approved_and_ordered(self):
        # Asked for by one person and approved by another: nobody approves their own request.
        asker, manager = self.as_("Employee Self Service"), self.as_("AP Manager")
        requisition, _ = self.asked(asker)
        self.assertEqual(self.ok(asker.post(f"/api/purchasing/requisitions/{requisition}/submit/"))["status"],
                         "submitted")
        approved = self.ok(manager.post(f"/api/purchasing/requisitions/{requisition}/approve/",
                                        {"note": "Within budget"}, format="json"))
        self.assertEqual((approved["status"], approved["decision_note"]), ("approved", "Within budget"))
        made = self.ok(manager.post(f"/api/purchasing/requisitions/{requisition}/order/", {
            "vendor": self.vendor.pk, "order_date": "2026-01-10"}, format="json"), 201)
        order = PurchaseOrder.objects.get(pk=made["order"])
        self.assertEqual(order.lines.get().quantity, Decimal("10"))
        self.assertEqual(self.ok(manager.get(f"/api/purchasing/requisitions/{requisition}/"))["status"], "ordered")
        lines = self.ok(manager.get("/api/purchasing/purchase-order-lines/",
                                    {"requisition_line__requisition": requisition}))
        self.assertEqual(len(lines), 1)

    def test_the_buyer_does_not_approve(self):
        manager = self.as_("AP Manager")
        requisition, _ = self.asked(manager)
        manager.post(f"/api/purchasing/requisitions/{requisition}/submit/")
        self.assertEqual(self.as_("Purchasing Clerk").post(
            f"/api/purchasing/requisitions/{requisition}/approve/").status_code, 403)

    def test_what_was_approved_is_what_is_ordered(self):
        asker, manager = self.as_("Employee Self Service"), self.as_("AP Manager")
        requisition, line = self.asked(asker)
        asker.post(f"/api/purchasing/requisitions/{requisition}/submit/")
        self.ok(manager.post(f"/api/purchasing/requisitions/{requisition}/approve/"))
        self.refused(manager.patch(f"/api/purchasing/requisition-lines/{line}/", {"quantity": "1000"},
                                   format="json"), "what is approved is what is ordered")
        self.refused(manager.post("/api/purchasing/requisition-lines/", {
            "requisition": requisition, "item": self.item.pk, "uom": self.uom.pk, "quantity": "5"},
            format="json"), "what is approved is what is ordered")
        self.refused(manager.patch(f"/api/purchasing/requisitions/{requisition}/", {"needed_by": "2026-01-06"},
                                   format="json"), "changed while it is a draft")
        self.refused(manager.delete(f"/api/purchasing/requisitions/{requisition}/"), "only a draft is deleted")
        self.assertEqual(PurchaseRequisitionLine.objects.get(pk=line).quantity, Decimal("10"))

    def test_an_order_called_off_gives_the_requisition_back(self):
        asker, manager = self.as_("Employee Self Service"), self.as_("AP Manager")
        requisition, _ = self.asked(asker)
        asker.post(f"/api/purchasing/requisitions/{requisition}/submit/")
        self.ok(manager.post(f"/api/purchasing/requisitions/{requisition}/approve/"))
        made = self.ok(manager.post(f"/api/purchasing/requisitions/{requisition}/order/",
                                    {"vendor": self.vendor.pk}, format="json"), 201)
        PurchaseOrder.objects.get(pk=made["order"]).cancel()
        self.assertEqual(PurchaseRequisition.objects.get(pk=requisition).status, RequisitionStatus.APPROVED)
        again = self.ok(manager.post(f"/api/purchasing/requisitions/{requisition}/order/",
                                     {"vendor": self.second.pk}, format="json"), 201)
        self.assertEqual(PurchaseOrder.objects.get(pk=again["order"]).vendor, self.second)


class RfqTests(DocumentsTestCase):
    def out(self, buyer):
        rfq = self.ok(buyer.post("/api/purchasing/rfqs/", {"issue_date": "2026-01-01", "currency": self.usd.pk},
                                 format="json"), 201)["id"]
        line = self.ok(buyer.post("/api/purchasing/rfq-lines/", {
            "rfq": rfq, "item": self.item.pk, "uom": self.uom.pk, "quantity": "100"}, format="json"), 201)["id"]
        invited = [self.ok(buyer.post("/api/purchasing/rfq-invitations/", {"rfq": rfq, "vendor": vendor.pk},
                                      format="json"), 201)["id"] for vendor in (self.vendor, self.second)]
        self.assertEqual(self.ok(buyer.post(f"/api/purchasing/rfqs/{rfq}/issue/"))["status"], "sent")
        return rfq, line, invited

    def test_put_out_compared_and_awarded(self):
        buyer = self.as_("Purchasing Clerk")
        rfq, line, (first, second) = self.out(buyer)
        for invitation, price in ((first, "5.00"), (second, "4.50")):
            self.ok(buyer.post(f"/api/purchasing/rfqs/{rfq}/quote/", {
                "invitation": invitation, "line": line, "unit_price": price, "lead_time_days": 7},
                format="json"))
        compared = self.ok(buyer.get(f"/api/purchasing/rfqs/{rfq}/comparison/"))
        cheapest = [quote["vendor"] for quote in compared["lines"][0]["quotes"] if quote["is_cheapest"]]
        self.assertEqual(cheapest, ["Beta Supplies"])
        self.assertEqual({row["vendor"]: row["total"] for row in compared["totals"]},
                         {self.vendor.name: "500.00", "Beta Supplies": "450.00"})
        made = self.ok(buyer.post(f"/api/purchasing/rfqs/{rfq}/award/", {"invitation": second,
                                                                          "order_date": "2026-01-20"},
                                  format="json"), 201)
        order = PurchaseOrder.objects.get(pk=made["order"])
        self.assertEqual((order.vendor, order.lines.get().unit_price), (self.second, Decimal("4.50")))
        self.assertEqual(order.lines.get().expected_date, datetime.date(2026, 1, 27))

    def test_fixed_once_it_goes_out_and_closed_once_awarded(self):
        buyer = self.as_("Purchasing Clerk")
        rfq, line, (first, second) = self.out(buyer)
        self.refused(buyer.patch(f"/api/purchasing/rfq-lines/{line}/", {"quantity": "50"}, format="json"),
                     "before anyone quotes on them")
        self.refused(buyer.delete(f"/api/purchasing/rfq-invitations/{first}/"), "take them off only a draft")
        self.ok(buyer.post(f"/api/purchasing/rfqs/{rfq}/quote/", {"invitation": second, "line": line,
                                                                   "unit_price": "4.50"}, format="json"))
        self.ok(buyer.post(f"/api/purchasing/rfqs/{rfq}/award/", {"invitation": second}, format="json"), 201)
        self.refused(buyer.post(f"/api/purchasing/rfqs/{rfq}/quote/", {"invitation": first, "line": line,
                                                                        "unit_price": "3.00"}, format="json"),
                     "quotes are given while the request is out")
        self.refused(buyer.post(f"/api/purchasing/rfq-invitations/{first}/decline/"),
                     "declines while the request is out")
        late = Party.objects.create(code="V-3", name="Gamma Ltd")
        PartyRoleAssignment.objects.create(party=late, role=PartyRole.VENDOR)
        self.refused(buyer.post("/api/purchasing/rfq-invitations/", {"rfq": rfq, "vendor": late.pk},
                                format="json"), "vendors are asked before it is awarded")

    def test_nobody_quotes_on_a_draft(self):
        buyer = self.as_("Purchasing Clerk")
        rfq = self.ok(buyer.post("/api/purchasing/rfqs/", {"issue_date": "2026-01-01"}, format="json"), 201)["id"]
        line = self.ok(buyer.post("/api/purchasing/rfq-lines/", {
            "rfq": rfq, "item": self.item.pk, "uom": self.uom.pk, "quantity": "100"}, format="json"), 201)["id"]
        invitation = self.ok(buyer.post("/api/purchasing/rfq-invitations/", {"rfq": rfq, "vendor": self.vendor.pk},
                                        format="json"), 201)["id"]
        self.refused(buyer.post(f"/api/purchasing/rfqs/{rfq}/quote/", {"invitation": invitation, "line": line,
                                                                        "unit_price": "5"}, format="json"),
                     "quotes are given while the request is out")


class BlanketTests(DocumentsTestCase):
    def agreed(self, buyer):
        blanket = self.ok(buyer.post("/api/purchasing/blanket-orders/", {
            "vendor": self.vendor.pk, "start_date": "2026-01-01", "end_date": "2026-12-31"}, format="json"),
            201)["id"]
        line = self.ok(buyer.post("/api/purchasing/blanket-order-lines/", {
            "blanket": blanket, "item": self.item.pk, "uom": self.uom.pk, "quantity": "1000",
            "unit_price": "4"}, format="json"), 201)["id"]
        self.assertEqual(self.ok(buyer.post(f"/api/purchasing/blanket-orders/{blanket}/confirm/"))["status"],
                         "confirmed")
        return blanket, line

    def test_agreed_and_called_off(self):
        buyer = self.as_("Purchasing Clerk")
        blanket, line = self.agreed(buyer)
        made = self.ok(buyer.post(f"/api/purchasing/blanket-orders/{blanket}/release/", {
            "quantities": {str(line): "300"}, "order_date": "2026-03-01"}, format="json"), 201)
        order = PurchaseOrder.objects.get(pk=made["order"])
        self.assertEqual((order.lines.get().quantity, order.lines.get().unit_price),
                         (Decimal("300"), Decimal("4.00")))
        [row] = self.ok(buyer.get(f"/api/purchasing/blanket-orders/{blanket}/"))["lines"]
        self.assertEqual((row["quantity_released"], row["quantity_remaining"]), ("300.0000", "700.0000"))
        self.assertEqual(len(self.ok(buyer.get("/api/purchasing/purchase-order-lines/",
                                               {"blanket_line__blanket": blanket}))), 1)

    def test_the_price_and_volume_agreed_stay_agreed(self):
        buyer = self.as_("Purchasing Clerk")
        blanket, line = self.agreed(buyer)
        self.refused(buyer.patch(f"/api/purchasing/blanket-order-lines/{line}/", {"unit_price": "3"},
                                 format="json"), "releases are priced from them")
        self.refused(buyer.patch(f"/api/purchasing/blanket-orders/{blanket}/", {"vendor": self.second.pk},
                                 format="json"), "changed while it is a draft")
        self.refused(buyer.post(f"/api/purchasing/blanket-orders/{blanket}/release/", {
            "quantities": {str(line): "1200"}, "order_date": "2026-03-01"}, format="json"), "Only 1000")
        self.assertEqual(BlanketOrder.objects.get(pk=blanket).vendor, self.vendor)
