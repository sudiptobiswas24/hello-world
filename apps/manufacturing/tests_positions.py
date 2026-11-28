"""
Loom L-17 has a drive-side main bearing position that takes bearing
6205 and cannot run without it. Two bearings placed there thirty days
apart give a thirty-day life. Returning an issue takes its placement
away; a position on another machine is refused for this job; a critical
position names its spare. The critical list says 10 bearings are on the
shelf, and nothing once they are gone.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.inventory.models import Item

from .machines import Machine
from .maintenance import issue_spares, return_spares
from .orders import WorkCentre
from .positions import MachinePosition, SparePlacement, critical_spares
from .tests_orders import TODAY
from .tests_spares import SparesTestCase

DAY = datetime.timedelta(days=1)


class PositionTestCase(SparesTestCase):
    def setUp(self):
        super().setUp()
        self.machine = self.job.machine
        self.bearing_seat = MachinePosition.objects.create(machine=self.machine, code="brg-ds", name="Main bearing, drive side",
                                                           spare_item=self.bearing, is_critical=True)


class RefusedTests(PositionTestCase):
    def test_a_critical_position_names_its_spare(self):
        with self.assertRaisesMessage(ValidationError, "names the spare it takes"):
            MachinePosition.objects.create(machine=self.machine, code="SCREEN", is_critical=True)
        with self.assertRaisesMessage(ValidationError, "Name the position"):
            MachinePosition.objects.create(machine=self.machine, code="  ")

    def test_a_position_on_another_machine_is_not_this_jobs(self):
        elsewhere = Machine.objects.create(work_centre=WorkCentre.objects.create(code="PRINT", name="Printing"), code="PR-1")
        other = MachinePosition.objects.create(machine=elsewhere, code="CYL", spare_item=self.bearing)
        with self.assertRaisesMessage(ValidationError, "is not on"):
            issue_spares(self.job, self.plant, [(self.bearing, "1", None, other)], on_date=TODAY)
        self.assertEqual((SparePlacement.objects.count(), self.bearing.on_hand_at(self.plant)), (0, Decimal("10")))


class PlacedTests(PositionTestCase):
    def test_placed_and_its_life_read_from_the_gap(self):
        first = issue_spares(self.job, self.plant, [(self.bearing, "1", None, self.bearing_seat)], on_date=TODAY)
        self.assertEqual([(item.sku, quantity) for _, item, quantity, _ in self.bearing_seat.history()],
                         [("BRG-6205", Decimal("1"))])
        self.assertIsNone(self.bearing_seat.life_days())
        second_job = self.broken("60", on=TODAY + 30 * DAY)
        issue_spares(second_job, self.plant, [(self.bearing, "1", None, self.bearing_seat)], on_date=TODAY + 30 * DAY)
        self.assertEqual([days for _, _, _, days in self.bearing_seat.history()], [None, 30])
        self.assertEqual(self.bearing_seat.life_days(), Decimal("30"))
        self.assertEqual(SparePlacement.objects.filter(issue=first).count(), 1)

    def test_returned_the_placement_goes_with_it(self):
        issue = issue_spares(self.job, self.plant, [(self.bearing, "1", None, self.bearing_seat)], on_date=TODAY)
        return_spares(issue, on_date=TODAY)
        self.assertEqual((SparePlacement.objects.count(), self.bearing.on_hand_at(self.plant)), (0, Decimal("10")))

    def test_the_critical_list_says_what_is_on_the_shelf(self):
        [row] = critical_spares()
        self.assertEqual((row["machine"], row["item"], row["on_hand"], row["short"]),
                         (self.machine, self.bearing, Decimal("10"), False))
        issue_spares(self.job, self.plant, [(self.bearing, "10", None, self.bearing_seat)], on_date=TODAY)
        [row] = critical_spares()
        self.assertEqual((row["on_hand"], row["short"]), (Decimal("0"), True))


class PositionApiTests(PositionTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_maintenance_keeps_positions_and_issues_to_them(self):
        fitter = self.as_("Maintenance")
        screen = Item.objects.create(sku="SCR-120", name="Screen pack 120 mesh", uom=self.pcs)
        made = fitter.post("/api/manufacturing/machine-positions/", {
            "machine": self.machine.pk, "code": "screen", "name": "Screen pack", "spare_item": screen.pk,
            "is_critical": True}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual(made.json()["code"], "SCREEN")
        issued = fitter.post(f"/api/manufacturing/maintenance-jobs/{self.job.pk}/spares/", {
            "warehouse": self.plant.pk, "on_date": str(TODAY),
            "lines": [{"item": self.bearing.pk, "quantity": "1", "position": self.bearing_seat.pk}]}, format="json")
        self.assertEqual(issued.status_code, 201, issued.content)
        history = fitter.get(f"/api/manufacturing/machine-positions/{self.bearing_seat.pk}/history/").json()
        self.assertEqual(([(row["item"], row["quantity"]) for row in history["placements"]], history["life_days"]),
                         ([("BRG-6205", "1")], None))
        shelf = fitter.get("/api/manufacturing/machine-positions/critical-spares/").json()
        code = self.machine.code
        self.assertEqual([(row["position"], row["item"], row["on_hand"], row["short"]) for row in shelf],
                         [(f"{code} BRG-DS", "BRG-6205", "9", False), (f"{code} SCREEN", "SCR-120", "0", True)])
        self.assertEqual(self.as_("Warehouse Staff").post("/api/manufacturing/machine-positions/", {
            "machine": self.machine.pk, "code": "X"}, format="json").status_code, 403)
