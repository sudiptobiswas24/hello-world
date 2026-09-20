from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.core.models import UnitOfMeasure
from .models import Item, MovementType, StockMovement, Warehouse


class StockLevelTests(TestCase):
    def setUp(self):
        self.uom = UnitOfMeasure.objects.create(code="pcs", name="Pieces")
        self.warehouse = Warehouse.objects.create(code="WH1", name="Main Warehouse")
        self.item = Item.objects.create(sku="ITEM-1", name="Widget", uom=self.uom)

    def _move(self, movement_type, quantity):
        StockMovement.objects.create(
            item=self.item,
            warehouse=self.warehouse,
            movement_type=movement_type,
            uom=self.item.uom,
            quantity=Decimal(quantity),
            occurred_at=timezone.now(),
        )

    def test_on_hand_is_derived_from_movements(self):
        self._move(MovementType.RECEIPT, "10")
        self._move(MovementType.ISSUE, "-3")
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("7"))

    def test_on_hand_with_no_movements_is_zero(self):
        self.assertEqual(self.item.on_hand_at(self.warehouse), 0)

    def test_on_hand_is_scoped_per_warehouse(self):
        other_warehouse = Warehouse.objects.create(code="WH2", name="Secondary Warehouse")
        self._move(MovementType.RECEIPT, "5")
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("5"))
        self.assertEqual(self.item.on_hand_at(other_warehouse), 0)
