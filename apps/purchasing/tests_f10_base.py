import datetime
from decimal import Decimal as D

from django.utils import timezone

from apps.accounting.models import Account, AccountType
from apps.core.models import Company
from apps.inventory.models import Item, Lot, MovementType, StockMovement

from .models import SubcontractComponent
from .tests_subcontract import SubcontractTestCase


class F10AtBase(SubcontractTestCase):
    def batch(self, code, quantity, expires):
        made = Lot.objects.create(item=self.frame, code=code, expires_on=expires)
        StockMovement.objects.create(item=self.frame, warehouse=self.warehouse, movement_type=MovementType.RECEIPT,
                                     uom=self.frame.uom, quantity=D(quantity), unit_cost=D("8"), lot=made,
                                     occurred_at=timezone.now())

    def total_value(self):
        return sum((item.stock_value_at(place) for item in (self.frame, self.motor, self.assembly)
                    for place in (self.warehouse, self.subcontractor)), D("0"))

    def test_fractional_returns(self):
        Item.objects.filter(pk=self.frame.pk).update(tracking="lot")
        self.frame.refresh_from_db()
        StockMovement.objects.filter(item=self.frame).delete()
        company = Company.get()
        ppv = Account.objects.create(code="5900", name="PPV", account_type=AccountType.EXPENSE)
        company.purchase_price_variance_account = ppv
        company.save()
        self.batch("F-1", "1.11", datetime.date(2026, 12, 31))
        self.batch("F-2", "98.89", datetime.date(2027, 6, 30))
        order = self.subcontract_order("10", confirm=False)
        SubcontractComponent.objects.filter(item=self.frame).update(quantity_per=D("0.333"))
        order.confirm()
        order.issue_components(self.warehouse)
        inv0, value0 = self.balance(self.inventory), self.total_value()
        receipt = self.receive(order, "10")
        line = receipt.lines.get()
        for step in (1, 1, 3, 2, 1, 2):
            receipt.create_return(quantities={line: D(step)}, debit_bills=False)
        print("\nF10 AT BASE: shelf delta", self.total_value() - value0, "inventory account delta",
              self.balance(self.inventory) - inv0, "grni", self.balance(self.grni), "ppv", self.balance(ppv))
