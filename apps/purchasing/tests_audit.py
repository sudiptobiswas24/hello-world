"""
Purchasing audit pass.

The two shapes that keep turning up in this codebase, on the purchase
side this time: a guard that exists on one path and not its mirror, and
an account that is only self-clearing if nothing ever differs.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.models import Account, AccountType
from apps.core.models import Company

from .models import Bill, BillLine, BillPolicy, PurchaseOrderLine
from .tests_lifecycle import PurchasingLifecycleTestCase


class AuditTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.ppv = Account.objects.create(
            code="5900", name="Purchase Price Variance", account_type=AccountType.EXPENSE
        )
        company = Company.get()
        company.purchase_price_variance_account = self.ppv
        company.default_purchase_expense_account = self.expense
        company.purchase_price_tolerance_percent = Decimal("10")
        company.save()

    def balance(self, account):
        from django.db.models import Sum

        from apps.accounting.models import JournalLine

        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit")
        )
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))

    def hand_bill(self, order, quantity, price, reference=""):
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=datetime.date(2026, 1, 10),
            purchase_order=order, payable_account=self.payable,
            currency=self.usd, reference=reference,
        )
        BillLine.objects.create(
            bill=bill, order_line=order.lines.first(), item=self.item,
            quantity=Decimal(quantity), unit_price=Decimal(price),
            expense_account=self.expense,
        )
        return bill


class OrderLineImmutabilityTests(AuditTestCase):
    """Sales guards these; the purchase side went without."""

    def test_the_quantity_cannot_drop_below_what_arrived(self):
        order = self.make_order("10", "5")
        self.receive(order, "8")
        line = order.lines.first()
        line.quantity = Decimal("2")

        with self.assertRaisesMessage(ValidationError, "cannot drop below"):
            line.save()

    def test_the_quantity_cannot_drop_below_what_was_billed(self):
        order = self.make_order("10", "5")
        self.receive(order, "10")
        order.create_bill(self.payable).post()
        line = order.lines.first()
        line.quantity = Decimal("3")

        with self.assertRaisesMessage(ValidationError, "cannot drop below"):
            line.save()

    def test_the_price_cannot_move_after_billing(self):
        """The agreed price is what the three-way match checks against, so
        moving it retrospectively approves whatever was billed."""
        order = self.make_order("10", "5")
        self.receive(order, "10")
        order.create_bill(self.payable).post()
        line = order.lines.first()
        line.unit_price = Decimal("99")

        with self.assertRaisesMessage(ValidationError, "price can no longer change"):
            line.save()

    def test_the_price_can_still_move_before_billing(self):
        order = self.make_order("10", "5", confirm=False)
        line = order.lines.first()
        line.unit_price = Decimal("6")
        line.save()
        self.assertEqual(order.lines.first().unit_price, Decimal("6"))

    def test_a_received_line_cannot_be_removed(self):
        order = self.make_order("10", "5")
        self.receive(order, "4")
        with self.assertRaises(Exception):
            order.lines.first().delete()


class GrniClearingTests(AuditTestCase):
    """GRNI only self-clears if it is cleared at what it accrued."""

    def test_a_price_difference_leaves_no_residue(self):
        order = self.make_order("10", "5")
        self.receive(order, "10")

        self.hand_bill(order, "10", "5.40").post()

        self.assertEqual(self.balance(self.grni), Decimal("0"))
        self.assertEqual(self.balance(self.ppv), Decimal("4.00"))

    def test_billing_under_the_agreed_price_credits_variance(self):
        order = self.make_order("10", "5")
        self.receive(order, "10")

        self.hand_bill(order, "10", "4.50").post()

        self.assertEqual(self.balance(self.grni), Decimal("0"))
        self.assertEqual(self.balance(self.ppv), Decimal("-5.00"))

    def test_no_variance_means_no_variance_line(self):
        order = self.make_order("10", "5")
        self.receive(order, "10")
        bill = self.hand_bill(order, "10", "5")
        bill.post()

        self.assertEqual(self.balance(self.ppv), Decimal("0"))
        self.assertFalse(bill.journal_entry.lines.filter(account=self.ppv).exists())

    def test_a_partial_bill_clears_only_its_share(self):
        order = self.make_order("10", "5")
        self.receive(order, "10")
        self.hand_bill(order, "4", "5").post()

        # 10 received accrued 50; 4 billed clears 20.
        self.assertEqual(self.balance(self.grni), Decimal("-30"))

    def test_a_variance_with_no_account_is_refused_not_absorbed(self):
        company = Company.get()
        company.purchase_price_variance_account = None
        company.save()
        order = self.make_order("10", "5")
        self.receive(order, "10")

        with self.assertRaisesMessage(ValidationError, "no purchase price variance account"):
            self.hand_bill(order, "10", "5.40").post()

    def test_a_bill_with_no_receipt_does_not_touch_grni(self):
        """Stock is created by receiving it, never by being billed for it.
        Debiting GRNI here leaves a balance nothing will ever offset."""
        bill = self.make_bill("3", "5")
        bill.post()

        self.assertEqual(self.balance(self.grni), Decimal("0"))
        self.assertEqual(self.balance(self.expense), Decimal("15"))

    def test_a_service_line_still_expenses(self):
        from apps.inventory.models import Item

        service = Item.objects.create(
            sku="SVC", name="Consulting", uom=self.uom, track_inventory=False
        )
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=datetime.date(2026, 1, 10),
            payable_account=self.payable, currency=self.usd,
        )
        BillLine.objects.create(
            bill=bill, item=service, quantity=Decimal("1"),
            unit_price=Decimal("500"), expense_account=self.expense,
        )
        bill.post()

        self.assertEqual(self.balance(self.expense), Decimal("500"))
        self.assertEqual(self.balance(self.grni), Decimal("0"))


class DuplicateVendorInvoiceTests(AuditTestCase):
    """Paying the same invoice twice because it arrived by post and by
    email is the commonest way money leaves AP by accident."""

    def test_the_same_vendor_reference_cannot_be_used_twice(self):
        self.make_bill_with_reference("INV-77").post()
        with self.assertRaisesMessage(ValidationError, "already on file"):
            self.make_bill_with_reference("INV-77")

    def test_a_different_vendor_may_use_the_same_number(self):
        from apps.core.models import Party, PartyRole, PartyRoleAssignment

        self.make_bill_with_reference("INV-77").post()
        other = Party.objects.create(code="V-2", name="Other", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.VENDOR)
        self.vendor = other

        bill = self.make_bill_with_reference("INV-77")
        bill.post()
        self.assertTrue(bill.posted)

    def test_bills_with_no_reference_are_unconstrained(self):
        self.make_bill("1", "5").post()
        self.make_bill("1", "5").post()

    def test_a_debit_note_may_carry_its_bill_s_reference(self):
        bill = self.make_bill_with_reference("INV-77")
        bill.post()
        note = bill.create_debit_note()
        self.assertEqual(note.reference, "INV-77")

    def make_bill_with_reference(self, reference):
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=datetime.date(2026, 1, 10),
            reference=reference, payable_account=self.payable, currency=self.usd,
        )
        BillLine.objects.create(
            bill=bill, description="Service", quantity=Decimal("1"),
            unit_price=Decimal("100"), expense_account=self.expense,
        )
        return bill
