"""
The quality screens' server side, asked as the people who use them.

The roll R-001 of woven fabric, against a plan of 83.125 to 91.875 gsm
on the mean of three. The inspector reads 87, 88 and 89 (mean 88): it
passes and the roll is released. Whoever takes the reading does not set
the pass mark: the plan is the Quality Manager's.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.inventory.models import Item, TrackingMode
from apps.manufacturing.tests_complaints import ComplaintTestCase

from .calibration import CalibrationResult
from .models import Inspection
from .release import release_status
from .tests import TODAY, QualityTestCase
from .tests_calibration import CalibrationTestCase

D = datetime.date


def as_role(role, name=None):
    user = User.objects.create_user(name or role.replace(" ", "-").lower())
    user.groups.add(Group.objects.get(name=role))
    client = APIClient()
    client.force_authenticate(user)
    return client


class InspectingTests(QualityTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.gsm_plan = self.plan()

    def test_the_inspector_reads_and_posts_and_the_roll_is_released(self):
        inspector = as_role("Quality Inspector")
        response = inspector.post("/api/quality/inspections/", {
            "lot": self.roll.pk, "plan": self.gsm_plan.pk, "inspected_on": TODAY.isoformat(),
            "inspected_by": self.inspector.pk,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        inspection = response.json()["id"]
        [line] = inspector.get("/api/quality/plan-lines/", {"plan": self.gsm_plan.pk}).json()
        self.assertEqual(line["characteristic_label"], "GSM · Grammes per square metre")
        for index, value in enumerate(("87", "88", "89"), start=1):
            reading = inspector.post("/api/quality/readings/", {
                "inspection": inspection, "plan_line": line["id"], "value": value,
                "sample_reference": f"S{index}",
            }, format="json")
            self.assertEqual(reading.status_code, 201, reading.content)
        posted = inspector.post(f"/api/quality/inspections/{inspection}/post/", {}, format="json")
        self.assertEqual(posted.status_code, 200, posted.content)
        self.assertEqual((posted.json()["result"], posted.json()["lot_status"]), ("pass", "released"))
        self.assertEqual(release_status(self.roll), "released")

        [row] = inspector.get("/api/quality/inspections/", {"posted": "true"}).json()
        self.assertEqual(
            (row["lot_code"], row["item_label"], row["inspected_by_name"], row["readings"][0]["characteristic"]),
            ("R-001", "FAB-60-87 · Woven fabric", "Meera", "Grammes per square metre"))

    def test_the_inspector_does_not_set_the_pass_mark(self):
        # The fabric already has its plan, and a second over the same days is
        # refused; this one is for the liner.
        liner = Item.objects.create(sku="LIN-1", name="Liner", uom=self.kg, tracking=TrackingMode.LOT)
        body = {"item": liner.pk, "name": "Liner GSM", "is_mandatory": False}
        self.assertEqual(as_role("Quality Inspector").post("/api/quality/plans/", body, format="json")
                         .status_code, 403)
        made = as_role("Quality Manager").post("/api/quality/plans/", body, format="json")
        self.assertEqual(made.status_code, 201, made.content)

    def test_a_plan_lists_with_its_item_and_line_count(self):
        rows = as_role("Quality Inspector").get("/api/quality/plans/", {"item": self.fabric.pk}).json()
        self.assertEqual([(row["item_label"], row["lines_count"]) for row in rows],
                         [("FAB-60-87 · Woven fabric", 1)])

    def test_a_list_cannot_be_narrowed_by_what_it_does_not_know(self):
        response = as_role("Quality Inspector").get("/api/quality/inspections/", {"colour": "red"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("colour", str(response.json()))


class SuspectInspectionsTests(CalibrationTestCase):
    """
    The balance checked good on 10 January; inspections on 1 March and 1
    June measured on it; on 15 June it was found out and adjusted. Both
    inspections are suspect, and the calibration's own page says so.
    """

    def test_a_calibration_found_out_names_what_it_casts_doubt_on(self):
        call_command("setup_roles", verbosity=0)
        self.calibrate(D(2026, 1, 10))
        march = self.measured(D(2026, 3, 1)).post()
        june = self.measured(D(2026, 6, 1)).post()
        found = self.calibrate(D(2026, 6, 15), CalibrationResult.ADJUSTED)
        manager = as_role("Quality Manager")
        page = manager.get(f"/api/quality/calibrations/{found.pk}/").json()
        self.assertEqual([(row["id"], row["lot"]) for row in page["suspects"]],
                         [(march.pk, "R-001"), (june.pk, "R-001")])
        self.assertEqual(page["instrument_code"], "BAL-1")
        # A list does not ask every calibration for its suspects.
        rows = manager.get("/api/quality/calibrations/", {"instrument": self.balance.pk}).json()
        self.assertEqual({row["suspects"] for row in rows}, {None})
        self.assertEqual(Inspection.objects.filter(posted=True).count(), 2)


class ComplaintListTests(ComplaintTestCase):
    def test_the_list_names_the_customer_and_narrows_by_status(self):
        call_command("setup_roles", verbosity=0)
        complaint = self.complaint(quantity_affected=Decimal("600"))
        manager = as_role("Quality Manager")
        [row] = manager.get("/api/manufacturing/complaints/", {"status": "open"}).json()
        self.assertEqual((row["id"], row["customer_name"]), (complaint.pk, self.customer.name))
        self.assertEqual(manager.get("/api/manufacturing/complaints/", {"status": "closed"}).json(), [])
