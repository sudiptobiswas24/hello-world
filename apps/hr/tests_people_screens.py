"""
The people, stores and plant screens' server side, asked as the people
who use them.

HR takes somebody on and makes the person with them; payroll keeps what
they are paid. The stores manager and the process engineer keep the item
master, but revaluing stock by a new standard cost is a posting, the
controller's. How an item is counted and valued is fixed once its stock
has moved. And an order's profitability is read only by whoever may see
the customer's orders.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import Party, PartyRole, UnitOfMeasure
from apps.inventory.models import Item

from .models import Employee


def as_(role):
    user = User.objects.create_user(role.replace(" ", "_").lower())
    user.groups.add(Group.objects.get(name=role))
    client = APIClient()
    client.force_authenticate(user)
    return client


class PeopleTestCase(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.each = UnitOfMeasure.objects.create(code="each", name="Each")


class TakingSomebodyOnTests(PeopleTestCase):
    def test_hr_makes_the_person_with_the_employee(self):
        hr = as_("HR Admin")
        made = hr.post("/api/hr/employees/", {"employee_number": "E-101", "new_name": "Ravi Kumar",
                                              "hire_date": "2026-10-01", "job_title": "Loom operator"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        employee = Employee.objects.get(employee_number="E-101")
        self.assertEqual((employee.party.name, employee.party.code), ("Ravi Kumar", "E-101"))
        self.assertTrue(employee.party.role_assignments.filter(role=PartyRole.EMPLOYEE).exists())
        [row] = hr.get("/api/hr/employees/", {"search": "ravi"}).json()
        self.assertEqual(row["name"], "Ravi Kumar")

    def test_with_neither_a_name_nor_a_person_it_says_so(self):
        response = as_("HR Admin").post("/api/hr/employees/", {"employee_number": "E-102", "hire_date": "2026-10-01"},
                                        format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Name the person", response.content.decode())
        self.assertFalse(Party.objects.filter(code="E-102").exists())


class ItemMasterTests(PeopleTestCase):
    def test_the_stores_manager_makes_an_item_and_its_other_units(self):
        stores = as_("Stores Manager")
        made = stores.post("/api/inventory/items/", {"sku": "BAG-50", "name": "Woven sack 50 kg", "uom": self.each.pk},
                           format="json")
        self.assertEqual(made.status_code, 201, made.content)
        bale = UnitOfMeasure.objects.create(code="bale", name="Bale")
        unit = stores.post("/api/inventory/item-units/", {"item": made.json()["id"], "uom": bale.pk, "factor": "500"},
                           format="json")
        self.assertEqual(unit.status_code, 201, unit.content)
        [row] = stores.get("/api/inventory/item-units/", {"item": made.json()["id"]}).json()
        self.assertEqual((row["uom_code"], row["factor"]), ("bale", "500.000000"))

    def test_a_new_standard_cost_is_the_controllers_to_post(self):
        item = Item.objects.create(sku="BAG-60", name="Woven sack 60 kg", uom=self.each, costing_method="standard")
        url = f"/api/inventory/items/{item.pk}/set_standard_cost/"
        self.assertEqual(as_("Stores Manager").post(url, {"standard_cost": "9"}, format="json").status_code, 403)
        self.assertEqual(as_("Process Engineer").post(url, {"standard_cost": "9"}, format="json").status_code, 403)
        response = as_("Controller").post(url, {"standard_cost": "9"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)

    def test_how_an_item_is_counted_is_fixed_once_its_stock_has_moved(self):
        from apps.inventory.models import StockMovement, Warehouse

        item = Item.objects.create(sku="PP-1", name="PP granule", uom=self.each)
        store = Warehouse.objects.create(code="RM", name="Raw material")
        StockMovement.objects.create(item=item, warehouse=store, quantity=Decimal("10"), uom=self.each,
                                     unit_cost=Decimal("100"), movement_type="receipt",
                                     occurred_at=datetime.datetime(2026, 9, 1, 10, tzinfo=datetime.timezone.utc))
        manager = as_("Stores Manager")
        kg = UnitOfMeasure.objects.create(code="kg", name="Kilogram")
        response = manager.patch(f"/api/inventory/items/{item.pk}/", {"uom": kg.pk}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("unit cannot change", response.content.decode())
        renamed = manager.patch(f"/api/inventory/items/{item.pk}/", {"name": "PP raffia granule"}, format="json")
        self.assertEqual(renamed.status_code, 200, renamed.content)
