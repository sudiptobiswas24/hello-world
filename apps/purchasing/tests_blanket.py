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
        self.agreement("1000", "4")
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

    def test_a_release_is_not_raised_or_reopened_against_a_closed_agreement(self):
        # Releases made stand; more called off them does not. Raised after the close, a release
        # took 150 at the price the agreement fixed, and a line closed short reopened the same.
        blanket = self.agreement("1000", "4")
        order = blanket.release({self.line: Decimal("100")}, order_date=datetime.date(2026, 2, 1))
        order.confirm()
        self.receive(order, "60")
        line = order.lines.get()
        line.close_short("Vendor out of granules")
        raised = blanket.release({self.line: Decimal("100")}, order_date=datetime.date(2026, 3, 1)).lines.get()
        blanket.close()

        raised.quantity = Decimal("150")
        with self.assertRaisesMessage(ValidationError, f"{blanket.number} is closed"):
            raised.save()
        with self.assertRaisesMessage(ValidationError, f"{blanket.number} is closed"):
            line.reopen()
        raised.refresh_from_db()
        self.assertEqual((raised.quantity, self.line.quantity_released()), (Decimal("100"), Decimal("160")))
        raised.quantity = Decimal("80")  # less is still a release's own business
        raised.save()


class WhatAShortReleaseGivesBackTests(BlanketTestCase):
    """
    1,000 agreed; 100 called off, 60 came and the rest was closed short.
    The 40 never bought go back to the agreement: 940 left, and all 940 can
    be called off. Reopened once they are, the short line would call off 40
    more than the agreement holds, and is refused.
    """

    def short_release(self):
        blanket = self.agreement("1000", "4")
        order = blanket.release({self.line: Decimal("100")}, order_date=datetime.date(2026, 2, 1))
        order.confirm()
        self.receive(order, "60")
        line = order.lines.get()
        line.close_short("Vendor out of granules")
        return blanket, line

    def test_what_never_came_is_called_off_again(self):
        blanket, _ = self.short_release()
        self.assertEqual(self.line.quantity_remaining(), Decimal("940"))
        blanket.release({self.line: Decimal("940")}, order_date=datetime.date(2026, 3, 1))
        self.assertEqual(self.line.quantity_remaining(), Decimal("0"))

    def test_reopened_it_must_fit_what_the_agreement_has_left(self):
        blanket, line = self.short_release()
        blanket.release({self.line: Decimal("930")}, order_date=datetime.date(2026, 3, 1))
        with self.assertRaisesMessage(ValidationError, "has 10 of WDG-1 - Widget left to call off; reopened, "
                                                       "this line would call off 40 more"):
            line.reopen()
        line.refresh_from_db()
        self.assertTrue(line.is_closed_short())

    def test_reopened_where_it_fits_the_rest_is_called_off_again(self):
        _, line = self.short_release()
        line.reopen()
        self.assertEqual(self.line.quantity_remaining(), Decimal("900"))


class ALineMadeInCodeIsAReleaseTooTests(BlanketTestCase):
    """
    O249. A purchase order line naming an agreement's line, made in code on an order that exists rather than by
    release(), was asked nothing: 1,000 @ 4 committed, 900 released, and a line of 200 made beside it took the
    agreement to 1,100 of 1,000, or called off a closed agreement. The edit was asked; the make now asks the same
    check (calc_g41_lines/o249_o357.py).
    """

    def made(self, order, quantity):
        from .models import PurchaseOrderLine

        return PurchaseOrderLine.objects.create(order=order, blanket_line=self.line, item=self.item, uom=self.uom,
                                                quantity=Decimal(quantity), unit_price=Decimal("4"))

    def test_past_what_the_agreement_commits_it_is_refused_and_within_it_made_and_given_back(self):
        blanket = self.agreement("1000", "4")
        order = blanket.release({self.line: Decimal("900")}, order_date=datetime.date(2026, 2, 1))
        with self.assertRaisesMessage(ValidationError, "The agreement commits 1000 of WDG-1 - Widget; "
                                                       "releasing 1100 would exceed it."):
            self.made(order, "200")
        self.assertEqual((order.lines.count(), self.line.quantity_remaining()), (1, Decimal("100")))
        last = self.made(order, "100")
        self.assertEqual(self.line.quantity_remaining(), Decimal("0"))
        # Its reverse: deleted from the draft, the 100 is the agreement's to call off again.
        last.delete()
        self.assertEqual(self.line.quantity_remaining(), Decimal("100"))
        # And the edit, the same check: 900 raised to 1,100.
        first = order.lines.get()
        first.quantity = Decimal("1100")
        with self.assertRaisesMessage(ValidationError, "releasing 1100 would exceed it"):
            first.save()

    def test_a_closed_agreement_is_not_called_off_by_a_line_made_in_code(self):
        blanket = self.agreement("1000", "4")
        order = blanket.release({self.line: Decimal("100")}, order_date=datetime.date(2026, 2, 1))
        blanket.close()
        with self.assertRaisesMessage(ValidationError, "Only a confirmed agreement can be released against"):
            self.made(order, "10")
        self.assertEqual(self.line.quantity_released(), Decimal("100"))


class ACallOffIsOfTheAgreementTests(BlanketTestCase):
    """
    O581. release() made its order to the agreement's vendor, of the line's item, on a day within the agreement;
    a purchase order line made or moved in code was asked none of it. Now release(), the line's save() and an
    order whose vendor or date changes ask the one function, BlanketOrder.refuse_a_call_off
    (calc_g41_lines/o581.py). 1,000 @ 4 with V-1 for WDG-1, 2026; 100 released on 1 February.
    """

    def setUp(self):
        super().setUp()
        from apps.inventory.models import Item

        self.blanket = self.agreement("1000", "4")
        self.released = self.blanket.release({self.line: Decimal("100")}, order_date=datetime.date(2026, 2, 1))
        self.bolt = Party.objects.create(code="V-BOLT", name="Bolt Supply")
        PartyRoleAssignment.objects.create(party=self.bolt, role=PartyRole.VENDOR)
        self.gadget = Item.objects.create(sku="WDG-2", name="Gadget", uom=self.uom)

    def order(self, vendor=None, day=datetime.date(2026, 3, 1)):
        from .models import PurchaseOrder

        return PurchaseOrder.objects.create(vendor=vendor or self.vendor, order_date=day, currency=self.usd)

    def made(self, order, item=None):
        from .models import PurchaseOrderLine

        return PurchaseOrderLine.objects.create(order=order, blanket_line=self.line, item=item or self.item,
                                                uom=self.uom, quantity=Decimal("10"), unit_price=Decimal("4"))

    def assert_still_100(self):
        self.assertEqual(self.line.quantity_released(), Decimal("100"))

    def test_a_line_on_another_vendor_s_order_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "BPO-2026-00001 is an agreement with V-1 - Supplier; an "
                                                       "order to V-BOLT - Bolt Supply does not call it off."):
            self.made(self.order(vendor=self.bolt))
        self.assert_still_100()

    def test_a_line_of_another_item_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "agrees WDG-1 - Widget on this line; a line of WDG-2 - Gadget "
                                                       "does not call it off"):
            self.made(self.order(), item=self.gadget)
        self.assert_still_100()

    def test_a_line_on_an_order_dated_outside_the_agreement_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "This agreement runs 01 Jan 2026 to 31 Dec 2026; 05 Jan 2027 "
                                                       "is outside it."):
            self.made(self.order(day=datetime.date(2027, 1, 5)))
        self.assert_still_100()

    def test_a_released_line_moved_to_another_item_or_vendor_is_refused(self):
        line = self.released.lines.get()
        line.item = self.gadget
        with self.assertRaisesMessage(ValidationError, "a line of WDG-2 - Gadget does not call it off"):
            line.save()
        line = self.released.lines.get()
        line.order = self.order(vendor=self.bolt)
        with self.assertRaisesMessage(ValidationError, "an order to V-BOLT - Bolt Supply does not call it off"):
            line.save()
        self.assert_still_100()

    def test_a_released_order_moved_to_another_vendor_or_day_is_refused_and_any_other_edit_stands(self):
        from .models import PurchaseOrder

        order = PurchaseOrder.objects.get(pk=self.released.pk)
        order.vendor = self.bolt
        with self.assertRaisesMessage(ValidationError, "an order to V-BOLT - Bolt Supply does not call it off"):
            order.save()
        order = PurchaseOrder.objects.get(pk=self.released.pk)
        order.order_date = datetime.date(2027, 1, 5)
        with self.assertRaisesMessage(ValidationError, "05 Jan 2027 is outside it"):
            order.save()
        order = PurchaseOrder.objects.get(pk=self.released.pk)
        order.reference = "Call-off 1"
        order.save()
        # Nothing more called off, nothing moved: an edit of a released line stands after the agreement closed.
        self.blanket.close()
        line = self.released.lines.get()
        line.expected_date = datetime.date(2026, 2, 20)
        line.save()
        self.assert_still_100()

