"""
The how-it's-made screens' server side, asked as the people who use
them. The process engineer keeps the recipes, routes and machines; the
planner plans against them and may not change them, though the
changeover rules are the planner's. Each list names what a row is about.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.hr.models import Employee

from .models import BillOfMaterials, Machine, Shift
from .tests_orders import RunTestCase


class MakingScreensTestCase(RunTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user, _ = User.objects.get_or_create(username=role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client


class RecipeTests(MakingScreensTestCase):
    """
    Regrind made 1,000 kg a batch from virgin polymer with 2% of what goes
    in lost: 1,000 / 0.98 = 1,020.408163 kg goes in (the waste is a share
    of the input, not of the output).
    """

    def recipe(self):
        return {"item": self.regrind.pk, "version": 1, "quantity_produced": "1000", "uom": self.kg.pk,
                "is_default": True}

    def test_the_planner_does_not_change_a_recipe(self):
        response = self.as_("Production Planner").post("/api/manufacturing/boms/", self.recipe(), format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(BillOfMaterials.objects.filter(item=self.regrind).exists())

    def test_the_engineer_writes_one_and_the_list_names_it(self):
        engineer = self.as_("Process Engineer")
        made = engineer.post("/api/manufacturing/boms/", self.recipe(), format="json")
        self.assertEqual(made.status_code, 201, made.content)
        line = engineer.post("/api/manufacturing/bom-components/", {
            "bom": made.json()["id"], "item": self.virgin.pk, "quantity": "1000", "uom": self.kg.pk,
            "waste_percent": "2",
        }, format="json")
        self.assertEqual(line.status_code, 201, line.content)
        [row] = engineer.get("/api/manufacturing/boms/", {"item": self.regrind.pk}).json()
        self.assertEqual((row["item_label"], row["uom_code"]), ("REGRIND · Reprocessed waste", "kg"))
        [component] = row["components"]
        self.assertEqual((component["item_label"], Decimal(component["gross_quantity"])),
                         ("PP-RAFFIA · PP homopolymer", Decimal("1020.408163")))
        self.assertEqual(len(engineer.get("/api/manufacturing/boms/", {"search": "regrind"}).json()), 1)


class RouteAndMachineTests(MakingScreensTestCase):
    def test_a_routing_names_where_each_step_runs(self):
        engineer = self.as_("Process Engineer")
        routing = engineer.post("/api/manufacturing/routings/", {"code": "R-TAPE", "name": "Tape"},
                                format="json").json()
        step = engineer.post("/api/manufacturing/routing-operations/", {
            "routing": routing["id"], "sequence": 10, "name": "Extrude", "work_centre": self.loom.pk,
            "setup_minutes": "30", "units_per_hour": "250", "rate_uom": self.kg.pk,
        }, format="json")
        self.assertEqual(step.status_code, 201, step.content)
        [row] = engineer.get("/api/manufacturing/routings/", {"search": "R-TAPE"}).json()
        self.assertEqual([(op["name"], op["work_centre_name"]) for op in row["operations"]],
                         [("Extrude", "Extrusion line 1")])

    def test_machines_list_by_their_bank(self):
        engineer = self.as_("Process Engineer")
        made = engineer.post("/api/manufacturing/machines/", {"work_centre": self.loom.pk, "code": "EXT-1A"},
                             format="json")
        self.assertEqual(made.status_code, 201, made.content)
        [row] = engineer.get("/api/manufacturing/machines/", {"work_centre": self.loom.pk}).json()
        self.assertEqual((row["code"], row["work_centre_name"]), ("EXT-1A", "Extrusion line 1"))
        self.assertEqual(self.as_("Production Planner").post(
            "/api/manufacturing/machines/", {"work_centre": self.loom.pk, "code": "EXT-1B"},
            format="json").status_code, 403)
        self.assertEqual(Machine.objects.count(), 1)

    def test_the_planner_keeps_the_changeover_rules(self):
        planner = self.as_("Production Planner")
        made = planner.post("/api/manufacturing/changeover-rules/", {
            "work_centre": self.loom.pk, "from_family": "white", "to_family": "black", "minutes": "45",
            "purge_kg": "12.5"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        [row] = planner.get("/api/manufacturing/changeover-rules/", {"work_centre": self.loom.pk}).json()
        self.assertEqual((row["work_centre_name"], row["minutes"]), ("Extrusion line 1", "45.00"))


class CrewTests(MakingScreensTestCase):
    def test_a_crew_names_who_where_and_when(self):
        party = Party.objects.create(code="EMP-0901", name="Ravi")
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
        ravi = Employee.objects.create(party=party, employee_number="EMP-0901",
                                       hire_date=datetime.date(2020, 1, 1))
        night = Shift.objects.create(code="C", name="Night", starts_at=datetime.time(22))
        supervisor = self.as_("Production Supervisor")
        made = supervisor.post("/api/manufacturing/crew-assignments/", {
            "employee": ravi.pk, "work_centre": self.loom.pk, "shift": night.pk, "valid_from": "2026-06-01",
        }, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        [row] = supervisor.get("/api/manufacturing/crew-assignments/", {"shift": night.pk}).json()
        self.assertEqual((row["employee_name"], row["work_centre_name"], row["shift_name"]),
                         ("Ravi", "Extrusion line 1", "Night"))
