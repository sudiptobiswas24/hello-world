"""Review probes for O293 (review_blockers). Not product tests."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User, Group
from django.core.exceptions import ValidationError

from apps.core.models import Currency, ExchangeRate
from .models import (PurchaseApprovalPolicy, PurchaseOrder, PurchaseOrderLine, Budget)
from .tests_budgets import BudgetTestCase


class Probes(BudgetTestCase):
    def setUp(self):
        super().setUp()
        self.eur = Currency.objects.create(code="EUR", name="Euro")
        self.rate = ExchangeRate.objects.create(currency=self.eur, rate=Decimal("90"), valid_from=datetime.date(2026, 1, 1))

    def eur_order(self, quantity, price, on=datetime.date(2026, 3, 1), confirm=False):
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=on, currency=self.eur)
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal(quantity),
                                         unit_price=Decimal(price), expense_account=self.expense)
        if confirm:
            order.confirm()
        return order

    def test_order_dated_before_first_rate_breaks_budget_reads(self):
        old = self.eur_order("1", "10", on=datetime.date(2025, 12, 1), confirm=True)
        print("confirmed old EUR order, status", old.status)
        Budget.objects.filter(pk=self.budget.pk).update(start_date=datetime.date(2025, 1, 1))
        try:
            print("available:", Budget.objects.get(pk=self.budget.pk).available())
        except Exception as e:
            print("BUDGET READ BREAKS:", type(e).__name__, e)
        PurchaseApprovalPolicy.objects.create(code="P", name="B")
        other = self.order_of("1", "10", confirm=False)
        try:
            print("approval_reasons other order:", other.approval_reasons())
        except Exception as e:
            print("OTHER ORDER APPROVAL BREAKS:", type(e).__name__, e)

    def test_tiers_and_rate_moves(self):
        from .models import ApprovalTier
        pol = PurchaseApprovalPolicy.objects.create(code="P", name="T")
        small, big = Group.objects.create(name="small"), Group.objects.create(name="big")
        ApprovalTier.objects.create(policy=pol, group=small, up_to_amount=Decimal("1000000"))
        ApprovalTier.objects.create(policy=pol, group=big, up_to_amount=Decimal("10000000"))
        u_small = User.objects.create_user("s"); u_small.groups.add(small)
        u_big = User.objects.create_user("b"); u_big.groups.add(big)
        o = self.eur_order("10", "5000")  # 4.5M base
        for u in (u_small, u_big):
            try:
                o.check_approver(u); print(u.username, "may approve 4.5M")
            except ValidationError as e:
                print(u.username, "refused:", e.messages)
        # 20 EUR order, 1800 base: small ok
        o2 = self.eur_order("2", "10")
        o2.check_approver(u_small); print("small ok on 1800")
        # rate moves: new rate row after order date does not change
        ExchangeRate.objects.create(currency=self.eur, rate=Decimal("100"), valid_from=datetime.date(2026, 3, 15))
        print("after later rate:", o.total_in_base())
        # approve at 90, then correct the row to 100 and see figures
        pol.max_order_value = Decimal("5000000"); pol.save()
        o.approve(by=u_big)
        o.confirm()
        self.rate.rate = Decimal("100"); self.rate.save()
        line = o.lines.first()
        line.quantity = Decimal("9"); 
        try:
            line.save(); print("cut after rate corrected: ok")
        except ValidationError as e:
            print("CUT REFUSED after rate correction:", e.messages)
