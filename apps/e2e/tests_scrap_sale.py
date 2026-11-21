"""
Scrap sold to a recycler, tried end to end through the API as the
people who do it: 1,000 kg of reprocessed waste on the shelf at 60.00;
the rep takes an order for 500 kg at 28.00, stores ship it, the AR
manager invoices and posts. The shelf holds 500, cost of sales carries
30,000.00 (what the waste cost, not what it fetched), receivables
14,000.00. No special flow: an ordinary invoice of the waste item.
"""

from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.accounting.models import JournalLine
from apps.core.models import Party, PartyRole, PartyRoleAssignment, UnitOfMeasure, UnitOfMeasureCategory
from apps.inventory.models import Item, MovementType, StockMovement

from .tests_api_lifecycle import LifecycleTestCase


class ScrapSaleTests(LifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.kg = UnitOfMeasure.objects.create(code="kg", name="Kilogram", category=UnitOfMeasureCategory.WEIGHT)
        self.regrind = Item.objects.create(sku="REGRIND", name="Reprocessed waste", uom=self.kg, hsn_code="3915")
        StockMovement.objects.create(item=self.regrind, warehouse=self.warehouse, movement_type=MovementType.RECEIPT,
                                     uom=self.kg, quantity=Decimal("1000"), unit_cost=Decimal("60"),
                                     occurred_at=timezone.now())
        self.recycler = Party.objects.create(code="RCY-1", name="Pune Polymer Recyclers", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.recycler, role=PartyRole.CUSTOMER)

    def balance(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit"))
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))

    def test_waste_sells_through_an_ordinary_invoice(self):
        rep = self.as_("Sales Rep")
        order = self.ok(rep.post("/api/sales/sales-orders/", {
            "customer": self.recycler.pk, "order_date": "2026-03-01", "currency": self.usd.pk}, format="json"), 201)
        self.ok(rep.post("/api/sales/sales-order-lines/", {
            "order": order["id"], "item": self.regrind.pk, "uom": self.kg.pk, "quantity": "500",
            "unit_price": "28.00", "revenue_account": self.revenue.pk}, format="json"), 201)
        confirmed = self.ok(rep.post(f"/api/sales/sales-orders/{order['id']}/confirm/"))
        self.assertEqual(confirmed["status"], "confirmed")

        store = self.as_("Warehouse Staff")
        delivery = self.ok(store.post("/api/sales/deliveries/", {
            "sales_order": order["id"], "delivery_date": "2026-03-03"}, format="json"), 201)
        self.ok(store.post("/api/sales/delivery-lines/", {
            "delivery": delivery["id"], "order_line": confirmed["lines"][0]["id"],
            "warehouse": self.warehouse.pk, "quantity_shipped": "500"}, format="json"), 201)
        self.ok(store.post(f"/api/sales/deliveries/{delivery['id']}/post_delivery/"))
        self.assertEqual(self.regrind.on_hand_at(self.warehouse), Decimal("500"))
        self.assertEqual(self.balance(self.cogs), Decimal("30000.00"))

        manager = self.as_("AR Manager")
        invoice = self.ok(manager.post(f"/api/sales/sales-orders/{order['id']}/create_invoice/", {
            "receivable_account": self.ar.pk, "invoice_date": "2026-03-04"}, format="json"))
        posted = self.ok(manager.post(f"/api/sales/invoices/{invoice['id']}/post_invoice/"))
        self.assertEqual((posted["total"], posted["posted"]), ("14000.00", True))
        self.assertEqual(self.balance(self.ar), Decimal("14000.00"))
        self.assertEqual(self.balance(self.revenue), Decimal("-14000.00"))
