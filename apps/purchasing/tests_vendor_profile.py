"""
A vendor's standing and terms, as purchasing and accounts meet them.

  Blocked for short-weight bags (a reason is required): no order to them
  is confirmed; unblocked, the same order confirms. On trial: an order to
  them waits for approval. Payments held over a quality claim: nothing is
  paid to them, though their refund is taken in; lifted, the payment posts.
  Net 30 when the order was placed, Net 60 by the bill: the bill keeps 30.
  Ex works from Shanghai; once goods are in, the order's freight is fixed.
  They usually take 21 days: planning reads it where no price says.
  The buyer keeps the freight and the lead time, but does not unblock a
  vendor or release their money; the AP Manager may.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType, Payment, PaymentDirection
from apps.core.models import PaymentTerms

from .models import ApprovalStatus, PurchaseOrder, VendorProfile, VendorStanding
from .pricing import resolve_lead_time
from .tests_lifecycle import PurchasingLifecycleTestCase

PROFILES = "/api/purchasing/vendor-profiles/"
VENDORS = "/api/purchasing/vendors/"


class VendorTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.profile = VendorProfile.objects.create(party=self.vendor)

    def stand(self, standing, reason="Short-weight bags in August"):
        self.profile.standing, self.profile.standing_reason = standing, reason
        self.profile.save()


class StandingTests(VendorTestCase):
    def test_a_trial_or_a_block_says_why(self):
        with self.assertRaisesMessage(ValidationError, "Say why they are on trial or blocked."):
            self.stand(VendorStanding.BLOCKED, reason=" ")

    def test_no_order_is_confirmed_to_a_blocked_vendor(self):
        order = self.make_order(confirm=False)
        self.stand(VendorStanding.BLOCKED)
        with self.assertRaisesMessage(ValidationError, "is blocked (Short-weight bags in August): no new order"):
            order.confirm()
        self.assertEqual(PurchaseOrder.objects.get(pk=order.pk).number, "")
        self.stand(VendorStanding.APPROVED, reason="")
        order.confirm()

    def test_an_order_to_a_vendor_on_trial_is_approved_first(self):
        self.stand(VendorStanding.TRIAL, reason="First order of BOPP film")
        order = self.make_order(confirm=False)
        self.assertEqual(order.approval_status(), ApprovalStatus.PENDING)
        self.assertIn("is on trial (First order of BOPP film)", " ".join(order.approval_reasons()))
        with self.assertRaisesMessage(ValidationError, "needs approval"):
            order.confirm()
        approver = User.objects.create_superuser("approver")
        order.approve(by=approver)
        order.confirm()


class PaymentHoldTests(VendorTestCase):
    def setUp(self):
        super().setUp()
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)

    def payment(self, direction=PaymentDirection.DISBURSEMENT):
        return Payment.objects.create(party=self.vendor, direction=direction, amount=Decimal("500"),
                                      payment_date=datetime.date(2026, 1, 20), bank_account=self.bank,
                                      counterpart_account=self.payable, currency=self.usd)

    def test_a_hold_says_why(self):
        self.profile.payment_hold = True
        with self.assertRaisesMessage(ValidationError, "Say why their payments are held."):
            self.profile.save()

    def test_nothing_is_paid_while_held_and_their_refund_is_taken(self):
        self.profile.payment_hold, self.profile.payment_hold_reason = True, "Quality claim QC-12 open"
        self.profile.save()
        paying = self.payment()
        with self.assertRaisesMessage(ValidationError, "payments are held (Quality claim QC-12 open)"):
            paying.post()
        self.assertFalse(Payment.objects.get(pk=paying.pk).posted)
        self.payment(PaymentDirection.RECEIPT).post()
        self.profile.payment_hold = False
        self.profile.save()
        paying.post()


class TermsTheOrderKeepsTests(VendorTestCase):
    def test_a_bill_from_the_order_keeps_the_terms_it_was_placed_on(self):
        order = self.make_order()
        self.assertEqual(order.payment_terms, self.terms)
        self.vendor.payment_terms = PaymentTerms.objects.create(code="N60", name="Net 60", net_days=60)
        self.vendor.save()
        self.receive(order, "10")
        bill = order.create_bill(self.payable, bill_date=datetime.date(2026, 1, 10))
        self.assertEqual(bill.payment_terms, self.terms)

    def test_freight_is_recorded_and_fixed_once_goods_are_in(self):
        self.profile.freight_terms, self.profile.incoterm, self.profile.port_of_loading = "ex_works", "FOB", "Shanghai"
        self.profile.save()
        order = self.make_order()
        self.assertEqual((order.freight_terms, order.incoterm, order.port_of_loading), ("ex_works", "FOB", "Shanghai"))
        order.incoterm = "CIF"
        order.save()  # nothing in yet: still the order's to change
        self.receive(order, "5")
        order.incoterm = "FOB"
        with self.assertRaisesMessage(ValidationError, "its freight terms and Incoterm cannot change now"):
            order.save()

    def test_planning_reads_how_long_they_usually_take(self):
        self.assertIsNone(resolve_lead_time(self.item, self.vendor))
        self.profile.lead_time_days = 21
        self.profile.save()
        self.assertEqual(resolve_lead_time(self.item, self.vendor), 21)


class WhoMaySetWhatTests(VendorTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_role(self, *roles):
        user = User.objects.create_user("-".join(roles).replace(" ", "").lower())
        user.groups.add(*Group.objects.filter(name__in=roles))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_buyer_keeps_the_terms_but_not_the_controls(self):
        buyer = self.as_role("Purchasing Clerk")
        at = f"{PROFILES}{self.profile.pk}/"
        self.assertEqual(buyer.patch(at, {"lead_time_days": 21, "freight_terms": "ex_works"}, format="json").status_code, 200)
        for change in ({"standing": "blocked", "standing_reason": "Late twice"},
                       {"payment_hold": True, "payment_hold_reason": "Claim"}):
            with self.subTest(change=change):
                self.assertEqual(buyer.patch(at, change, format="json").status_code, 403)
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.standing, self.profile.payment_hold), (VendorStanding.APPROVED, False))
        manager = self.as_role("AP Manager")
        self.assertEqual(manager.patch(at, {"standing": "blocked", "standing_reason": "Late twice"},
                                       format="json").status_code, 200)

    def test_a_new_vendor_in_one_go_and_its_controls_asked_there_too(self):
        buyer = self.as_role("Purchasing Clerk")
        body = {"party": {"code": "V-BOPP", "name": "Gujarat BOPP Films"},
                "addresses": [{"address_type": "billing", "line1": "GIDC Plot 4", "city": "Vapi"}],
                "bank_accounts": [{"account_name": "Gujarat BOPP Films", "bank_name": "SBI", "account_number": "30012345678",
                                   "ifsc": "SBIN0001234"}],
                "terms": {"lead_time_days": 14, "our_account_number": "DPS-221"}}
        made = buyer.post(VENDORS, body, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual(VendorProfile.objects.get(party_id=made.json()["id"]).lead_time_days, 14)
        blocked = buyer.post(VENDORS, {**body, "party": {"code": "V-X", "name": "X"},
                                       "terms": {"standing": "blocked", "standing_reason": "x"}}, format="json")
        self.assertEqual(blocked.status_code, 403)
        self.assertFalse(VendorProfile.objects.filter(party__code="V-X").exists())
        from apps.core.models import Party
        self.assertFalse(Party.objects.filter(code="V-X").exists())
