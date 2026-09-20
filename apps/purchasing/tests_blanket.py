"""
Blanket orders and call-off releases.

A negotiated price and volume over a period, drawn down by releases. It
used to be inexpressible, so the commitment either lived in someone's
filing cabinet or was faked as a purchase order that never fully
received — and a purchase order left open forever silently skews every
report that reads open orders.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.core.models import Party, PartyRole, PartyRoleAssignment

from .models import (
    BlanketOrder,
    BlanketOrderLine,
    BlanketStatus,
    OrderStatus,
    VendorPrice,
)
from .tests_lifecycle import PurchasingLifecycleTestCase


class BlanketTestCase(PurchasingLifecycleTestCase):
    def agreement(self, quantity="1000", price="4", confirm=True,
                  start=datetime.date(2026, 1, 1), end=datetime.date(2026, 12, 31)):
        blanket = BlanketOrder.objects.create(
            vendor=self.vendor, start_date=start, end_date=end, currency=self.usd
        )
        self.line = BlanketOrderLine.objects.create(
            blanket=blanket, item=self.item, uom=self.uom,
            quantity=Decimal(quantity), unit_price=Decimal(price),
        )
        if confirm:
            blanket.confirm()
        return blanket


class BlanketLifecycleTests(BlanketTestCase):
    def test_confirming_numbers_it(self):
        blanket = self.agreement()
        self.assertEqual(blanket.number, "BPO-2026-00001")
        self.assertEqual(blanket.status, BlanketStatus.CONFIRMED)

    def test_a_draft_has_no_number(self):
        blanket = self.agreement(confirm=False)
        self.assertEqual(blanket.number, "")

    def test_an_empty_agreement_cannot_be_confirmed(self):
        blanket = BlanketOrder.objects.create(
            vendor=self.vendor, start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 12, 31), currency=self.usd,
        )
        with self.assertRaisesMessage(ValidationError, "no lines"):
            blanket.confirm()

    def test_it_cannot_end_before_it_starts(self):
        blanket = BlanketOrder(
            vendor=self.vendor, start_date=datetime.date(2026, 12, 31),
            end_date=datetime.date(2026, 1, 1),
        )
        with self.assertRaisesMessage(ValidationError, "cannot end before it starts"):
            blanket.clean()

    def test_the_total_is_the_committed_value(self):
        self.assertEqual(self.agreement("1000", "4").total(), Decimal("4000"))

    def test_a_vendor_must_hold_the_vendor_role(self):
        customer = Party.objects.create(code="C-9", name="Not a vendor")
        with self.assertRaisesMessage(ValidationError, "does not have the Vendor role"):
            BlanketOrder(
                vendor=customer, start_date=datetime.date(2026, 1, 1),
                end_date=datetime.date(2026, 12, 31),
            ).clean()


class ReleaseTests(BlanketTestCase):
    def test_a_release_becomes_a_real_purchase_order(self):
        blanket = self.agreement("1000", "4")

        order = blanket.release({self.line: Decimal("100")},
                                order_date=datetime.date(2026, 2, 1))

        self.assertEqual(order.vendor, self.vendor)
        self.assertEqual(order.lines.get().quantity, Decimal("100"))
        self.assertEqual(order.total(), Decimal("400"))

    def test_the_agreed_price_holds_even_if_vendor_prices_move(self):
        """The whole point of committing to a volume is that the price is
        fixed for it."""
        blanket = self.agreement("1000", "4")
        VendorPrice.objects.create(
            vendor=self.vendor, item=self.item, currency=self.usd,
            unit_price=Decimal("9"),
        )

        order = blanket.release({self.line: Decimal("100")},
                                order_date=datetime.date(2026, 2, 1))

        self.assertEqual(order.lines.get().unit_price, Decimal("4"))

    def test_releases_draw_the_commitment_down(self):
        blanket = self.agreement("1000", "4")
        blanket.release({self.line: Decimal("300")}, order_date=datetime.date(2026, 2, 1))
        blanket.release({self.line: Decimal("200")}, order_date=datetime.date(2026, 3, 1))

        self.assertEqual(self.line.quantity_released(), Decimal("500"))
        self.assertEqual(self.line.quantity_remaining(), Decimal("500"))

    def test_releasing_more_than_committed_is_refused(self):
        blanket = self.agreement("1000", "4")
        blanket.release({self.line: Decimal("900")}, order_date=datetime.date(2026, 2, 1))

        with self.assertRaisesMessage(ValidationError, "is left on this agreement"):
            blanket.release({self.line: Decimal("200")},
                            order_date=datetime.date(2026, 3, 1))

    def test_a_cancelled_release_gives_the_volume_back(self):
        """An order called off and then called back off again was never
        bought."""
        blanket = self.agreement("1000", "4")
        order = blanket.release({self.line: Decimal("400")},
                                order_date=datetime.date(2026, 2, 1))

        order.cancel()

        self.assertEqual(self.line.quantity_released(), Decimal("0"))
        self.assertEqual(self.line.quantity_remaining(), Decimal("1000"))

    def test_a_draft_agreement_cannot_be_released_against(self):
        blanket = self.agreement(confirm=False)
        with self.assertRaisesMessage(ValidationError, "Only a confirmed agreement"):
            blanket.release({self.line: Decimal("10")},
                            order_date=datetime.date(2026, 2, 1))

    def test_a_release_outside_the_period_is_refused(self):
        blanket = self.agreement()
        with self.assertRaisesMessage(ValidationError, "is outside it"):
            blanket.release({self.line: Decimal("10")},
                            order_date=datetime.date(2027, 2, 1))

    def test_releasing_nothing_is_refused(self):
        blanket = self.agreement()
        with self.assertRaisesMessage(ValidationError, "Nothing to release"):
            blanket.release({self.line: Decimal("0")},
                            order_date=datetime.date(2026, 2, 1))

    def test_another_agreement_s_line_is_refused(self):
        first = self.agreement("1000", "4")
        first_line = self.line
        self.agreement("500", "6")

        with self.assertRaisesMessage(ValidationError, "different agreement"):
            self.agreement("100", "1").release(
                {first_line: Decimal("10")}, order_date=datetime.date(2026, 2, 1)
            )

    def test_a_release_can_carry_an_expected_date(self):
        blanket = self.agreement()
        order = blanket.release(
            {self.line: Decimal("10")}, order_date=datetime.date(2026, 2, 1),
            expected_date=datetime.date(2026, 2, 20),
        )
        self.assertEqual(order.lines.get().expected_date, datetime.date(2026, 2, 20))


class BlanketClosingTests(BlanketTestCase):
    def test_closing_leaves_existing_releases_alone(self):
        """They are purchase orders in their own right and the vendor has
        committed against them."""
        blanket = self.agreement("1000", "4")
        order = blanket.release({self.line: Decimal("100")},
                                order_date=datetime.date(2026, 2, 1))
        order.confirm()

        blanket.close()

        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.CONFIRMED)
        self.assertEqual(blanket.status, BlanketStatus.CLOSED)

    def test_a_closed_agreement_cannot_be_released_against(self):
        blanket = self.agreement()
        blanket.close()
        with self.assertRaisesMessage(ValidationError, "Only a confirmed agreement"):
            blanket.release({self.line: Decimal("10")},
                            order_date=datetime.date(2026, 2, 1))

    def test_it_cannot_be_closed_twice(self):
        blanket = self.agreement()
        blanket.close()
        with self.assertRaisesMessage(ValidationError, "already closed"):
            blanket.close()
