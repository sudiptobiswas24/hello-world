"""
Audit probe (audit2_stores): the demo plant's "Stock agrees with the ledger" after write-offs and
write-ups at figures that do not come out even.
"""

import datetime
from decimal import Decimal
from io import StringIO

from django.test import TestCase

from apps.accounting.models import Account, AccountType
from apps.inventory.models import (
    AdjustmentDirection, AdjustmentReason, Item, StockAdjustment, StockAdjustmentLine, Warehouse)
from apps.web.health import integrity

from .tests_load_demo import load

D = Decimal


class TheHealthCheckAfterAwkwardAdjustments(TestCase):
    def test_it(self):
        load(password="Trial-2026", stdout=StringIO(), stderr=StringIO())
        expense = Account.objects.create(code="6999", name="Probe shrink", account_type=AccountType.EXPENSE)
        income = Account.objects.create(code="4999", name="Probe found", account_type=AccountType.INCOME)
        down = AdjustmentReason.objects.create(code="PD", name="probe down", account=expense,
                                               direction=AdjustmentDirection.DECREASE)
        up = AdjustmentReason.objects.create(code="PU", name="probe up", account=income,
                                             direction=AdjustmentDirection.INCREASE)
        plant = Warehouse.objects.get(code__iexact="PLANT") if Warehouse.objects.filter(code__iexact="PLANT").exists() \
            else Warehouse.objects.order_by("pk").first()
        for sku, q in (("PP-RAFFIA", "0.3337"), ("UV-MB", "1.0001"), ("TAPE-900", "7.7777"), ("SACK-CEM50", "3")):
            item = Item.objects.get(sku=sku)
            for reason, sign, cost in ((down, -1, None), (up, 1, "111.1111")):
                a = StockAdjustment.objects.create(adjustment_date=datetime.date(2026, 7, 2), warehouse=plant,
                                                   reason=reason)
                StockAdjustmentLine.objects.create(adjustment=a, item=item, uom=item.uom, quantity=D(q) * sign,
                                                   unit_cost=D(cost) if cost else None)
                a.post()
        found = {f["label"]: f for f in integrity(fresh=True)["findings"]}
        stock = found["Stock agrees with the ledger"]
        self.assertTrue(stock["ok"], stock)
