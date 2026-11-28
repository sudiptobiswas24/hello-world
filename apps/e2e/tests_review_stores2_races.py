import unittest
from decimal import Decimal
from django.db import connection
from django.test import tag
from django.utils import timezone

from apps.core.models import UnitOfMeasure
from apps.inventory.models import Item, MovementType, StockMovement, Warehouse
from .tests_races import RaceCase, race


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class CostingMethodVersusFirstReceipt(RaceCase):
    def test_method_change_and_first_receipt_at_once(self):
        kg = UnitOfMeasure.objects.create(code="kgx", name="kg")
        item = Item.objects.create(sku="RACE", name="r", uom=kg)
        wh = Warehouse.objects.create(code="RW", name="W")

        def switch():
            i = Item.objects.get(pk=item.pk)
            i.costing_method = "fifo"
            i.save()

        def receive():
            StockMovement.objects.create(item=Item.objects.get(pk=item.pk), warehouse=wh, movement_type=MovementType.RECEIPT, uom=kg,
                                         quantity=Decimal("5"), value=Decimal("10.00"), occurred_at=timezone.now())

        outcomes = race((Item, StockMovement), switch, receive)
        item.refresh_from_db()
        print("RACE O330 outcomes", outcomes, "method now", item.costing_method, "movements", item.movements.count())
        self.assertFalse(item.movements.exists() and item.costing_method != "average", "method changed under stock")
