"""
The pick list: the route through the shelves for a draft shipment, or
for every draft shipment of a day, before anything moves. Soonest batch
first, then the bins in walking order; two deliveries off one shelf
merge; what the shelf cannot meet is said in words on its row rather
than refusing the list.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.inventory.models import StorageBin
from apps.inventory.tests_picking import PickingTestCase

from .models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine, deliveries_to_pick, pick_list_for

JUNE_1 = datetime.date(2026, 6, 1)


class PickListTestCase(PickingTestCase):
    def setUp(self):
        super().setUp()
        self.a = StorageBin.objects.create(warehouse=self.warehouse, code="A", sequence=1)
        self.b = StorageBin.objects.create(warehouse=self.warehouse, code="B", sequence=2)

    def binned(self):
        self.warehouse.requires_bins = True
        self.warehouse.save()

    def draft(self, item, quantity, on=JUNE_1):
        order = SalesOrder.objects.create(customer=self.customer, order_date=on, currency=self.usd)
        line = SalesOrderLine.objects.create(order=order, item=item, uom=self.each, quantity=Decimal(quantity),
                                             unit_price=Decimal("20"), revenue_account=self.revenue)
        order.confirm()
        delivery = Delivery.objects.create(sales_order=order, delivery_date=on)
        DeliveryLine.objects.create(delivery=delivery, order_line=line, warehouse=self.warehouse,
                                    quantity_shipped=Decimal(quantity))
        return delivery

    @staticmethod
    def rows(rows):
        return [(row["bin"].code if row["bin"] else None, row["lot"].code if row["lot"] else None,
                 row["quantity"], row["for"], row["problem"]) for row in rows]


class RouteTests(PickListTestCase):
    def test_soonest_batch_first_then_the_bins_in_walking_order(self):
        self.binned()
        sooner, later = self.lot("SOONER", "2026-08-01"), self.lot("LATER", "2026-12-01")
        self.stock("20", lot=sooner, storage_bin=self.b)
        self.stock("50", lot=later, storage_bin=self.a)
        delivery = self.draft(self.batched, "35")
        self.assertEqual(self.rows(delivery.pick_list()),
                         [("A", "LATER", Decimal("15"), [f"Draft {delivery.pk}"], ""),
                          ("B", "SOONER", Decimal("20"), [f"Draft {delivery.pk}"], "")])

    def test_a_days_drafts_merge_on_the_shelf_and_the_rest_is_left_out(self):
        self.binned()
        self.stock("100", storage_bin=self.a)
        first, second = self.draft(self.plain, "10"), self.draft(self.plain, "15")
        self.draft(self.plain, "5", on=datetime.date(2026, 6, 2))
        self.ship(self.plain, "5", storage_bin=self.a)  # shipped: not to pick
        self.draft(self.plain, "5").delete()
        drafts = deliveries_to_pick(JUNE_1)
        self.assertEqual(list(drafts), [first, second])
        self.assertEqual(self.rows(pick_list_for(drafts)),
                         [("A", None, Decimal("25"), [f"Draft {first.pk}", f"Draft {second.pk}"], "")])
        self.assertEqual(self.rows(pick_list_for(deliveries_to_pick(JUNE_1, warehouse=self.warehouse))),
                         [("A", None, Decimal("25"), [f"Draft {first.pk}", f"Draft {second.pk}"], "")])

    def test_what_the_shelf_cannot_meet_is_said_on_the_row(self):
        self.stock("30")
        short = self.draft(self.plain, "100")
        self.assertEqual(self.rows(short.pick_list()),
                         [(None, None, Decimal("100"), [f"Draft {short.pk}"], "Only 30 here; 70 short.")])
        self.binned()
        self.stock("20", storage_bin=self.a)  # 30 loose and 20 binned: a binned warehouse cannot route the loose
        rows = short.pick_list()
        self.assertEqual((len(rows), rows[0]["quantity"]), (1, Decimal("100")))
        self.assertIn("no bin recorded", rows[0]["problem"])

    def test_two_drafts_off_one_shelf_add_up_to_more_than_is_there(self):
        self.binned()
        self.stock("50", storage_bin=self.a)
        first, second = self.draft(self.plain, "30"), self.draft(self.plain, "30")
        self.assertEqual(self.rows(pick_list_for([first, second])),
                         [("A", None, Decimal("60"), [f"Draft {first.pk}", f"Draft {second.pk}"], "Only 50 here; 10 short.")])

    def test_a_shipped_delivery_or_a_return_has_no_pick_list(self):
        self.stock("30")
        shipped, _line = self.ship(self.plain, "5")
        with self.assertRaisesMessage(ValidationError, "shipped already"):
            shipped.pick_list()
        taken_back = shipped.create_return(credit_invoices=False, quantities={shipped.lines.get(): Decimal("1")})
        with self.assertRaisesMessage(ValidationError, "a return"):
            taken_back.pick_list()


class OfficeTests(PickListTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.binned()
        self.stock("100", storage_bin=self.a)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_warehouse_reads_a_deliverys_route_and_the_days(self):
        first, second = self.draft(self.plain, "10"), self.draft(self.plain, "15")
        staff = self.as_("Warehouse Staff")
        one = staff.get(f"/api/sales/deliveries/{first.pk}/pick-list/")
        self.assertEqual(one.status_code, 200, one.content)
        self.assertEqual([(row["bin"], row["item"], row["lot"], row["quantity"], row["for"], row["problem"]) for row in one.json()["rows"]],
                         [("A", "P · Plain", "", "10.0000", [f"Draft {first.pk}"], "")])
        day = staff.get("/api/sales/deliveries/pick-list/", {"date": "2026-06-01", "warehouse": self.warehouse.pk})
        self.assertEqual(day.status_code, 200, day.content)
        self.assertEqual((day.json()["deliveries"], [(row["bin"], row["quantity"], row["for"]) for row in day.json()["rows"]]),
                         ([f"Draft {first.pk}", f"Draft {second.pk}"], [("A", "25.0000", [f"Draft {first.pk}", f"Draft {second.pk}"])]))
        nothing = staff.get("/api/sales/deliveries/pick-list/", {"date": "2026-06-02"}).json()
        self.assertEqual((nothing["deliveries"], nothing["rows"]), ([], []))
        for path in (f"/api/sales/deliveries/{first.pk}/pick-list/pdf/", "/api/sales/deliveries/pick-list/pdf/?date=2026-06-01"):
            paper = staff.get(path)
            self.assertEqual((paper.status_code, paper["Content-Type"]), (200, "application/pdf"), path)
        self.assertEqual(staff.get("/api/sales/deliveries/pick-list/", {"warehouse": 999999}).status_code, 404)

    def test_a_shipped_delivery_is_refused_in_words_and_the_books_may_not_look(self):
        shipped, _line = self.ship(self.plain, "5", storage_bin=self.a)
        staff = self.as_("Warehouse Staff")
        refused = staff.get(f"/api/sales/deliveries/{shipped.pk}/pick-list/")
        self.assertEqual((refused.status_code, "shipped already" in refused.content.decode()), (400, True), refused.content)
        books = self.as_("Bookkeeper")
        self.assertEqual(books.get(f"/api/sales/deliveries/{shipped.pk}/pick-list/").status_code, 403)
        self.assertEqual(books.get("/api/sales/deliveries/pick-list/").status_code, 403)
