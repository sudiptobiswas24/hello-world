"""
Quotes asked for what a requisition still needs. Dana asks for 10
widgets, Granule House in mind, and 5 gaskets with nobody in mind;
approved, the request goes out for quotes as one RFQ of two lines with
Granule House invited; awarded, the order's lines are the requisition's
and it reads ordered. What is out for quotes is not asked about twice,
what was ordered in the meantime is not ordered again, and lines from
several requisitions make one request.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.inventory.models import Item

from .models import (
    PurchaseRequisitionLine,
    RequestForQuotation,
    RequisitionStatus,
    RfqStatus,
    open_requisition_lines,
    request_quotes,
)
from .tests_requisitions import RequisitionTestCase

DUE = datetime.date(2026, 1, 15)


class QuotesTestCase(RequisitionTestCase):
    def setUp(self):
        super().setUp()
        self.gasket = Item.objects.create(sku="GSK-1", name="Gasket", uom=self.uom)
        self.second = Party.objects.create(code="V-2", name="Beta Supplies", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.second, role=PartyRole.VENDOR)

    def approved(self, gasket=True):
        requisition = self.requisition("10", "5", submit=False)
        self.line.suggested_vendor = self.vendor
        self.line.save()
        if gasket:
            self.gasket_line = PurchaseRequisitionLine.objects.create(
                requisition=requisition, item=self.gasket, uom=self.uom, quantity=Decimal("5"),
                estimated_price=Decimal("2"))
        requisition.submit()
        requisition.approve(by=self.manager)
        return requisition

    def quoted(self, rfq, vendor_invitation, price="4"):
        rfq.issue()
        for line in rfq.lines.all():
            vendor_invitation.quote(line, Decimal(price))
        return vendor_invitation


class FromOneRequisitionTests(QuotesTestCase):
    def test_what_is_left_goes_out_as_one_request_to_the_vendor_in_mind(self):
        requisition = self.approved()
        rfq = requisition.create_rfq(response_due=DUE)
        self.assertEqual((rfq.requisition, rfq.status, rfq.response_due, rfq.description),
                         (requisition, RfqStatus.DRAFT, DUE, f"For {requisition.number}"))
        self.assertEqual([(line.item, line.quantity, line.requisition_line) for line in rfq.lines.all()],
                         [(self.item, Decimal("10"), self.line), (self.gasket, Decimal("5"), self.gasket_line)])
        self.assertEqual([invitation.vendor for invitation in rfq.invited.all()], [self.vendor])
        self.assertEqual((self.line.quantity_quoting(), self.line.quantity_open()), (Decimal("10"), Decimal("0")))
        with self.assertRaisesMessage(ValidationError, "ordered already or out for quotes"):
            requisition.create_rfq()
        # Cancelled, the request frees the need again.
        rfq.cancel()
        self.assertEqual(self.line.quantity_open(), Decimal("10"))

    def test_only_an_approved_requisition_and_only_what_is_not_ordered(self):
        submitted = self.requisition()
        with self.assertRaisesMessage(ValidationError, "only an approved requisition"):
            submitted.create_rfq()
        requisition = self.approved()
        requisition.create_order(self.second, lines=[self.line])
        rfq = requisition.create_rfq(lines=requisition.lines.all())
        self.assertEqual([(line.item, line.quantity) for line in rfq.lines.all()], [(self.gasket, Decimal("5"))])
        # Nobody in mind for the gasket and no agreed price: the buyer asks on the request.
        self.assertEqual(rfq.invited.count(), 0)

    def test_awarding_orders_the_requisition_and_marks_it_ordered(self):
        requisition = self.approved()
        rfq = requisition.create_rfq()
        invitation = self.quoted(rfq, rfq.invited.get())
        order = rfq.award(invitation, order_date=datetime.date(2026, 1, 20))
        self.assertEqual([(line.requisition_line, line.quantity, line.unit_price) for line in order.lines.all()],
                         [(self.line, Decimal("10"), Decimal("4")), (self.gasket_line, Decimal("5"), Decimal("4"))])
        requisition.refresh_from_db()
        self.assertEqual(requisition.status, RequisitionStatus.ORDERED)
        self.assertEqual((self.line.quantity_ordered(), self.line.quantity_quoting()), (Decimal("10"), Decimal("0")))
        # The reverse: the order cancelled, the need is open again.
        order.cancel()
        requisition.refresh_from_db()
        self.assertEqual((requisition.status, self.line.quantity_open()), (RequisitionStatus.APPROVED, Decimal("10")))

    def test_an_award_does_not_order_what_was_ordered_in_the_meantime(self):
        requisition = self.approved()
        rfq = requisition.create_rfq()
        invitation = self.quoted(rfq, rfq.invited.get())
        requisition.create_order(self.second, lines=[self.line])
        with self.assertRaisesMessage(ValidationError, "ordered in the meantime"):
            rfq.award(invitation)
        self.assertEqual(rfq.status, RfqStatus.SENT)


class AcrossRequisitionsTests(QuotesTestCase):
    def test_lines_from_several_requisitions_make_one_request_that_is_nobodys_own(self):
        first = self.approved()
        first_line = self.line
        second = self.approved(gasket=False)
        self.assertEqual([(row["line"], row["open"], row["vendor"]) for row in open_requisition_lines()],
                         [(first_line, Decimal("10"), self.vendor), (self.gasket_line, Decimal("5"), None),
                          (self.line, Decimal("10"), self.vendor)])
        rfq = request_quotes([first_line, self.line], issue_date=datetime.date(2026, 1, 5))
        self.assertEqual((rfq.requisition, rfq.issue_date, rfq.description),
                         (None, datetime.date(2026, 1, 5), f"For {first.number}, {second.number}"))
        self.assertEqual([line.requisition_line for line in rfq.lines.all()], [first_line, self.line])
        self.assertEqual([invitation.vendor for invitation in rfq.invited.all()], [self.vendor])
        self.assertEqual([row["line"] for row in open_requisition_lines()], [self.gasket_line])


class OfficeTests(QuotesTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_clerk_asks_from_the_requisition_or_across_them(self):
        requisition = self.approved()
        other = self.approved(gasket=False)
        clerk = self.as_("Purchasing Clerk")
        to_quote = clerk.get("/api/purchasing/purchasing-reports/open-requisition-lines/")
        self.assertEqual(to_quote.status_code, 200, to_quote.content)
        self.assertEqual([(row["requisition_number"], row["item_label"], row["open"], row["vendor_name"]) for row in to_quote.json()],
                         [(requisition.number, "WDG-1 · Widget", "10.0000", self.vendor.name),
                          (requisition.number, "GSK-1 · Gasket", "5.0000", ""),
                          (other.number, "WDG-1 · Widget", "10.0000", self.vendor.name)])

        made = clerk.post(f"/api/purchasing/requisitions/{requisition.pk}/rfq/", {"response_due": "2026-01-15"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        rfq = clerk.get(f"/api/purchasing/rfqs/{made.json()['rfq']}/").json()
        self.assertEqual((rfq["requisition"], rfq["requisition_number"], rfq["response_due"],
                          [(line["item_label"], line["requisition_number"]) for line in rfq["lines"]]),
                         (requisition.pk, requisition.number, "2026-01-15",
                          [("WDG-1 · Widget", requisition.number), ("GSK-1 · Gasket", requisition.number)]))
        again = clerk.post(f"/api/purchasing/requisitions/{requisition.pk}/rfq/", {}, format="json")
        self.assertEqual((again.status_code, "out for quotes" in again.content.decode()), (400, True), again.content)
        lines = clerk.get(f"/api/purchasing/requisitions/{requisition.pk}/").json()["lines"]
        self.assertEqual([(line["quantity_quoting"], line["quantity_open"]) for line in lines],
                         [("10.0000", "0.0000"), ("5.0000", "0.0000")])

        across = clerk.post("/api/purchasing/purchasing-reports/request-quotes/", {"lines": [self.line.pk]}, format="json")
        self.assertEqual(across.status_code, 201, across.content)
        self.assertEqual(RequestForQuotation.objects.get(pk=across.json()["rfq"]).requisition, other)
        nothing = clerk.post("/api/purchasing/purchasing-reports/request-quotes/", {"lines": []}, format="json")
        self.assertEqual(nothing.status_code, 400, nothing.content)
        unknown = clerk.post("/api/purchasing/purchasing-reports/request-quotes/", {"lines": [999999]}, format="json")
        self.assertEqual(unknown.status_code, 400, unknown.content)

    def test_who_may(self):
        self.approved()
        books = self.as_("Bookkeeper")
        self.assertEqual(books.get("/api/purchasing/purchasing-reports/open-requisition-lines/").status_code, 403)
        self.assertEqual(books.post("/api/purchasing/purchasing-reports/request-quotes/", {"lines": [self.line.pk]}, format="json").status_code, 403)

    def test_the_scorecard_reads_one_vendor_alone(self):
        clerk = self.as_("Purchasing Clerk")
        alone = clerk.get("/api/purchasing/purchasing-reports/vendor-performance/", {"vendor": self.vendor.pk})
        self.assertEqual((alone.status_code, alone.json()), (200, []), alone.content)
        self.assertEqual(clerk.get("/api/purchasing/purchasing-reports/vendor-performance/", {"vendor": 999999}).status_code, 404)
