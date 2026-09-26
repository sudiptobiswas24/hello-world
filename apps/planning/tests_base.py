"""
A sack plant with an order book, for the planning suite.

Two levels deep on purpose: a woven sack made from fabric, fabric made
from tape, tape made from polymer and regrind. Every number asserted
in the suite is worked out from these figures in the comment beside
it.
"""

import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.accounting.models import Account, AccountType
from apps.core.models import (
    Company,
    Currency,
    Party,
    PartyRole,
    PartyRoleAssignment,
    UnitOfMeasure,
    UnitOfMeasureCategory,
)
from apps.inventory.models import Item, MovementType, StockMovement, Warehouse
from apps.manufacturing.bom import (
    BillOfMaterials,
    BomByproduct,
    BomComponent,
    ByproductValuation,
)
from apps.manufacturing.orders import ManufacturingSettings, WorkCentre
from apps.manufacturing.routing import Routing, RoutingOperation
from apps.purchasing.models import ReorderRule
from apps.sales.models import SalesOrder, SalesOrderLine

TODAY = datetime.date(2026, 6, 1)


class PlantTestCase(TestCase):
    def setUp(self):
        self.inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        self.kg = UnitOfMeasure.objects.create(
            code="kg", name="Kilogram", category=UnitOfMeasureCategory.WEIGHT
        )
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.inventory = acc("1200", "Inventory", AccountType.ASSET)
        self.cogs = acc("5000", "Cost of sales", AccountType.EXPENSE)
        self.revenue = acc("4000", "Revenue", AccountType.INCOME)
        self.wip = acc("1250", "Work in progress", AccountType.ASSET)
        self.variance = acc("5100", "Production variance", AccountType.EXPENSE)
        self.scrap = acc("5200", "Production scrap", AccountType.EXPENSE)
        Company.objects.create(
            name="Deccan Polysacks", base_currency=self.inr,
            default_inventory_account=self.inventory,
            default_cogs_account=self.cogs,
        )
        ManufacturingSettings.objects.create(
            wip_account=self.wip, variance_account=self.variance,
            scrap_account=self.scrap,
        )
        self.plant = Warehouse.objects.create(code="P", name="Plant")

        make = lambda sku, name, cost=None: Item.objects.create(
            sku=sku, name=name, uom=self.kg, standard_cost=cost,
        )
        self.virgin = make("PP-RAFFIA", "PP homopolymer")
        # Reground trim carries a standard recovery value, well under
        # virgin granule, which is what a run releases it at.
        self.regrind = make("REGRIND", "Reprocessed waste", Decimal("60"))
        self.tape = make("TAPE-1000", "PP tape, 1000 denier")
        self.fabric = make("FAB-10X10", "Woven fabric, 10x10")

        self.extruder = WorkCentre.objects.create(
            code="EXT-1", name="Extrusion line 1",
            capacity_per_hour=Decimal("180"), capacity_uom=self.kg,
            available_hours_per_day=Decimal("24"),
        )
        self.loom = WorkCentre.objects.create(
            code="LOOM-1", name="Circular loom 1",
            capacity_per_hour=Decimal("60"), capacity_uom=self.kg,
            available_hours_per_day=Decimal("24"),
        )

        self.extrusion = Routing.objects.create(code="R-EXT", name="Extrude")
        RoutingOperation.objects.create(
            routing=self.extrusion, sequence=10, name="Extrude",
            work_centre=self.extruder, setup_minutes=Decimal("60"),
        )
        self.weaving = Routing.objects.create(code="R-WEAVE", name="Weave")
        RoutingOperation.objects.create(
            routing=self.weaving, sequence=10, name="Weave",
            work_centre=self.loom, setup_minutes=Decimal("60"),
        )

        # 100 kg of tape from 75 virgin and 15 regrind, 3% of the input
        # lost, and 2.474227 kg of that loss comes back as regrind.
        self.tape_bom = BillOfMaterials.objects.create(
            item=self.tape, name="Tape 1000 den", quantity_produced=Decimal("100"),
            uom=self.kg, routing=self.extrusion,
        )
        for index, (item, net) in enumerate(
            ((self.virgin, "75"), (self.regrind, "15")), start=1
        ):
            BomComponent.objects.create(
                bom=self.tape_bom, item=item, quantity=Decimal(net), uom=self.kg,
                waste_percent=Decimal("3"), line_number=index,
            )
        BomByproduct.objects.create(
            bom=self.tape_bom, item=self.regrind, quantity=Decimal("2.474227"),
            uom=self.kg, valuation=ByproductValuation.STANDARD,
        )

        # 100 kg of fabric from 100 kg of tape, 2% lost on the loom.
        self.fabric_bom = BillOfMaterials.objects.create(
            item=self.fabric, name="Fabric 10x10", quantity_produced=Decimal("100"),
            uom=self.kg, routing=self.weaving,
        )
        BomComponent.objects.create(
            bom=self.fabric_bom, item=self.tape, quantity=Decimal("100"),
            uom=self.kg, waste_percent=Decimal("2"), line_number=1,
        )

        self.customer = Party.objects.create(code="C-1", name="Cement Co")
        PartyRoleAssignment.objects.create(
            party=self.customer, role=PartyRole.CUSTOMER
        )
        self.buyer = Party.objects.create(code="E-1", name="Ravi")
        PartyRoleAssignment.objects.create(party=self.buyer, role=PartyRole.EMPLOYEE)
        self.vendor = Party.objects.create(code="V-1", name="Reliance")
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)

    # -- helpers ---------------------------------------------------------

    def stock(self, item, quantity, cost="100"):
        return StockMovement.objects.create(
            item=item, warehouse=self.plant, movement_type=MovementType.RECEIPT,
            uom=self.kg, quantity=Decimal(quantity), unit_cost=Decimal(cost),
            occurred_at=timezone.now(),
        )

    def sell(self, item, quantity, due, price="90"):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=TODAY, currency=self.inr,
        )
        line = SalesOrderLine.objects.create(
            order=order, item=item, uom=self.kg, quantity=Decimal(quantity),
            unit_price=Decimal(price), revenue_account=self.revenue,
            warehouse=self.plant, delivery_date=due,
        )
        order.confirm()
        return line

    def issue_everything(self, order):
        """Draw exactly what a released run says it needs."""
        from apps.manufacturing.orders import MaterialIssue, MaterialIssueLine

        issue = MaterialIssue.objects.create(
            work_order=order, issue_date=TODAY, warehouse=self.plant,
        )
        for index, component in enumerate(order.components.all(), start=1):
            MaterialIssueLine.objects.create(
                issue=issue, item=component.item,
                quantity=component.quantity_required, uom=component.uom,
                line_number=index,
            )
        issue.post()
        return issue

    def rule(self, item, minimum="0", target="0", multiple_of=None, vendor=None):
        return ReorderRule.objects.create(
            item=item, warehouse=self.plant, minimum=Decimal(minimum),
            target=Decimal(target),
            multiple_of=Decimal(multiple_of) if multiple_of else None,
            vendor=vendor,
        )

    def day(self, offset):
        return TODAY + datetime.timedelta(days=offset)
