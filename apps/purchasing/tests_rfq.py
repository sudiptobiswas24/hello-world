"""
Requests for quotation and vendor comparison.

Not a purchase order in a different state, which is how Odoo models it.
An RFQ goes to several vendors at once, collects prices that are not
agreements, and ends in one being awarded while the rest are declined.
Folding that into a purchase order means either a fake order per vendor
— each polluting the open-order reports — or one order whose vendor
keeps changing, which loses the comparison that was the point.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.core.models import Party, PartyRole, PartyRoleAssignment

from .models import (
    RequestForQuotation,
    RfqInvitation,
    RfqLine,
    RfqStatus,
    VendorPrice,
)
from .tests_lifecycle import PurchasingLifecycleTestCase


class RfqTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.second = self.make_vendor("V-2", "Beta Supplies")
        self.third = self.make_vendor("V-3", "Gamma Ltd")

    def make_vendor(self, code, name):
        vendor = Party.objects.create(code=code, name=name, default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=vendor, role=PartyRole.VENDOR)
        return vendor

    def rfq(self, quantity="100", issue=True, vendors=None):
        rfq = RequestForQuotation.objects.create(
            issue_date=datetime.date(2026, 1, 1),
            response_due=datetime.date(2026, 1, 15), currency=self.usd,
        )
        self.line = RfqLine.objects.create(
            rfq=rfq, item=self.item, uom=self.uom, quantity=Decimal(quantity)
        )
        self.invitations = [
            RfqInvitation.objects.create(rfq=rfq, vendor=vendor)
            for vendor in (vendors or [self.vendor, self.second, self.third])
        ]
        if issue:
            rfq.issue()
        return rfq


class RfqLifecycleTests(RfqTestCase):
    def test_issuing_numbers_it(self):
        rfq = self.rfq()
        self.assertEqual(rfq.number, "RFQ-2026-00001")
        self.assertEqual(rfq.status, RfqStatus.SENT)

    def test_an_rfq_with_no_lines_cannot_issue(self):
        rfq = RequestForQuotation.objects.create(issue_date=datetime.date(2026, 1, 1))
        RfqInvitation.objects.create(rfq=rfq, vendor=self.vendor)
        with self.assertRaisesMessage(ValidationError, "no lines"):
            rfq.issue()

    def test_an_rfq_with_no_vendors_cannot_issue(self):
        rfq = RequestForQuotation.objects.create(issue_date=datetime.date(2026, 1, 1))
        RfqLine.objects.create(
            rfq=rfq, item=self.item, uom=self.uom, quantity=Decimal("10")
        )
        with self.assertRaisesMessage(ValidationError, "no vendors invited"):
            rfq.issue()

    def test_only_vendors_can_be_invited(self):
        rfq = self.rfq(issue=False)
        customer = Party.objects.create(code="C-9", name="Not a vendor")
        with self.assertRaisesMessage(ValidationError, "does not have the Vendor role"):
            RfqInvitation(rfq=rfq, vendor=customer).clean()

    def test_a_vendor_is_invited_once(self):
        rfq = self.rfq(issue=False)
        with self.assertRaises(Exception):
            RfqInvitation.objects.create(rfq=rfq, vendor=self.vendor)

    def test_cancelling(self):
        rfq = self.rfq()
        rfq.cancel()
        self.assertEqual(rfq.status, RfqStatus.CANCELLED)


class QuotingTests(RfqTestCase):
    def test_a_vendor_quotes_a_line(self):
        rfq = self.rfq()
        quote = self.invitations[0].quote(self.line, "4.50", lead_time_days=10)

        self.assertEqual(quote.unit_price, Decimal("4.50"))
        self.assertIsNotNone(self.invitations[0].responded_at)

    def test_requoting_replaces_rather_than_duplicates(self):
        rfq = self.rfq()
        self.invitations[0].quote(self.line, "4.50")
        self.invitations[0].quote(self.line, "4.20")

        self.assertEqual(self.line.quotes.count(), 1)
        self.assertEqual(self.line.quotes.get().unit_price, Decimal("4.20"))

    def test_declining_is_an_answer_worth_keeping(self):
        rfq = self.rfq()
        self.invitations[2].decline(note="No capacity this quarter")

        self.assertTrue(self.invitations[2].declined)
        self.assertEqual(self.invitations[2].notes, "No capacity this quarter")

    def test_a_declined_vendor_cannot_then_quote(self):
        rfq = self.rfq()
        self.invitations[2].decline()
        with self.assertRaisesMessage(ValidationError, "declined to quote"):
            self.invitations[2].quote(self.line, "4")

    def test_a_vendor_who_quoted_cannot_then_decline(self):
        rfq = self.rfq()
        self.invitations[0].quote(self.line, "4")
        with self.assertRaisesMessage(ValidationError, "already quoted"):
            self.invitations[0].decline()

    def test_another_rfq_s_line_is_refused(self):
        first = self.rfq()
        first_line = self.line
        second = self.rfq()
        with self.assertRaisesMessage(ValidationError, "different RFQ"):
            self.invitations[0].quote(first_line, "4")


class ComparisonTests(RfqTestCase):
    def quoted(self):
        rfq = self.rfq("100")
        self.invitations[0].quote(self.line, "5.00", lead_time_days=7)
        self.invitations[1].quote(self.line, "4.20", lead_time_days=21)
        return rfq

    def test_it_lines_the_quotes_up_and_flags_the_cheapest(self):
        rfq = self.quoted()
        row = rfq.comparison()[0]

        prices = {q["vendor"]: q for q in row["quotes"]}
        self.assertEqual(len(row["quotes"]), 2)
        self.assertTrue(prices[self.second]["is_cheapest"])
        self.assertFalse(prices[self.vendor]["is_cheapest"])
        self.assertEqual(prices[self.second]["total"], Decimal("420.00"))

    def test_it_names_who_has_not_answered(self):
        rfq = self.quoted()
        self.assertEqual(rfq.comparison()[0]["missing"], [self.third])

    def test_cheapest_is_flagged_not_chosen(self):
        """Lead time, quality and who answers the phone are not in this
        table, and a system that picked for you would pretend otherwise."""
        rfq = self.quoted()
        row = rfq.comparison()[0]
        by_vendor = {q["vendor"]: q for q in row["quotes"]}

        self.assertEqual(by_vendor[self.second]["lead_time_days"], 21)
        self.assertEqual(by_vendor[self.vendor]["lead_time_days"], 7)
        self.assertEqual(rfq.status, RfqStatus.SENT)

    def test_a_part_quote_gets_no_total(self):
        """A part-quote compared against a full one is not a comparison,
        and showing it as a smaller number is actively misleading."""
        rfq = self.rfq("100")
        second_line = RfqLine.objects.create(
            rfq=rfq, item=self.item, uom=self.uom, quantity=Decimal("50")
        )
        self.invitations[0].quote(self.line, "5")
        self.invitations[0].quote(second_line, "5")
        self.invitations[1].quote(self.line, "4")  # only one line

        totals = rfq.vendor_totals()
        by_vendor = {invitation.vendor: total for invitation, total in totals.items()}

        self.assertEqual(by_vendor[self.vendor], Decimal("750"))
        self.assertIsNone(by_vendor[self.second])


class AwardTests(RfqTestCase):
    def quoted(self):
        rfq = self.rfq("100")
        self.invitations[0].quote(self.line, "5.00", lead_time_days=7)
        self.invitations[1].quote(self.line, "4.20", lead_time_days=21)
        return rfq

    def test_awarding_creates_an_order_at_the_quoted_price(self):
        rfq = self.quoted()

        order = rfq.award(self.invitations[1], order_date=datetime.date(2026, 1, 20))

        self.assertEqual(order.vendor, self.second)
        self.assertEqual(order.lines.get().unit_price, Decimal("4.20"))
        self.assertEqual(order.reference, rfq.number)
        self.assertEqual(rfq.status, RfqStatus.AWARDED)

    def test_the_quoted_lead_time_becomes_the_expected_date(self):
        rfq = self.quoted()
        order = rfq.award(self.invitations[1], order_date=datetime.date(2026, 1, 20))
        self.assertEqual(order.lines.get().expected_date, datetime.date(2026, 2, 10))

    def test_a_vendor_who_did_not_quote_everything_cannot_be_awarded(self):
        rfq = self.rfq("100")
        RfqLine.objects.create(
            rfq=rfq, item=self.item, uom=self.uom, quantity=Decimal("50")
        )
        self.invitations[0].quote(self.line, "5")

        with self.assertRaisesMessage(ValidationError, "did not quote"):
            rfq.award(self.invitations[0])

    def test_an_unissued_rfq_cannot_be_awarded(self):
        rfq = self.rfq(issue=False)
        self.invitations[0].quote(self.line, "5")
        with self.assertRaisesMessage(ValidationError, "Only an issued RFQ"):
            rfq.award(self.invitations[0])

    def test_the_winning_price_can_become_an_agreement(self):
        """A price won in competition is exactly the kind worth checking
        against next time."""
        rfq = self.quoted()

        rfq.award(self.invitations[1], order_date=datetime.date(2026, 1, 20),
                  record_prices=True)

        price = VendorPrice.objects.get(vendor=self.second, item=self.item)
        self.assertEqual(price.unit_price, Decimal("4.20"))
        self.assertEqual(price.min_quantity, Decimal("100"))
        self.assertEqual(price.lead_time_days, 21)

    def test_it_does_not_become_an_agreement_by_default(self):
        """A quote for one order is not always an ongoing agreement."""
        rfq = self.quoted()
        rfq.award(self.invitations[1], order_date=datetime.date(2026, 1, 20))
        self.assertFalse(VendorPrice.objects.exists())

    def test_an_awarded_rfq_cannot_be_cancelled(self):
        rfq = self.quoted()
        rfq.award(self.invitations[1], order_date=datetime.date(2026, 1, 20))
        with self.assertRaisesMessage(ValidationError, "cancel the purchase order"):
            rfq.cancel()
