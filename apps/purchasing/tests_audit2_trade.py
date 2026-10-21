"""Probes of the second audit, trade lane (purchasing)."""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounting.models import Account, AccountType
from .models import Bill, BillLine
from .tests_lifecycle import PurchasingLifecycleTestCase


from .tests_money import MoneyCase  # noqa: E402


class PolicyInTheBaseCurrency(MoneyCase):
    def test_P5_a_euro_order_worth_4_5_million_base_asks_the_1_million_limit(self):
        from apps.core.models import Currency, ExchangeRate
        from .models import PurchaseApprovalPolicy, PurchaseOrder, PurchaseOrderLine

        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(currency=eur, rate=Decimal("90"), valid_from=datetime.date(2026, 1, 1))
        PurchaseApprovalPolicy.objects.create(code="P", name="Limit", max_order_value=Decimal("1000000"))
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=datetime.date(2026, 1, 5), currency=eur)
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                         unit_price=Decimal("5000"))
        with self.assertRaises(ValidationError):
            order.confirm()
