"""
The floor documents' screens, server side, asked as the supervisor who
keeps them. Figures from the conversion fixture: virgin polymer on the
shelf at ₹100 a kg, and the extruder at ₹360 an hour, ₹6 a minute.

- 600 kg of virgin issued to a 4,000 kg run: 600 × 100 = ₹60,000.
- 60 minutes booked on its one step: 60 × 6 = ₹360.

A booking names its step and the run comes with it. The planner reads
these but does not write them.
"""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from .orders import MaterialIssue, TimeBooking
from .tests_conversion import ConversionTestCase
from .tests_orders import TODAY


class FloorScreensTestCase(ConversionTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.run_ = self.routed()

    def as_(self, role):
        user, _ = User.objects.get_or_create(username=role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client


class IssueTests(FloorScreensTestCase):
    def test_the_planner_does_not_issue_material(self):
        response = self.as_("Production Planner").post("/api/manufacturing/material-issues/", {
            "work_order": self.run_.pk, "issue_date": str(TODAY), "warehouse": self.plant.pk}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(MaterialIssue.objects.exists())

    def test_the_supervisor_issues_and_posts_and_the_list_names_the_run(self):
        supervisor = self.as_("Production Supervisor")
        issue = supervisor.post("/api/manufacturing/material-issues/", {
            "work_order": self.run_.pk, "issue_date": str(TODAY), "warehouse": self.plant.pk}, format="json")
        self.assertEqual(issue.status_code, 201, issue.content)
        number = issue.json()["id"]
        line = supervisor.post("/api/manufacturing/material-issue-lines/", {
            "issue": number, "item": self.virgin.pk, "quantity": "600", "uom": self.kg.pk}, format="json")
        self.assertEqual(line.status_code, 201, line.content)
        posted = supervisor.post(f"/api/manufacturing/material-issues/{number}/post/", {}, format="json")
        self.assertEqual(posted.status_code, 200, posted.content)
        self.assertEqual(Decimal(posted.json()["posted_value"]), Decimal("60000"))

        [row] = supervisor.get("/api/manufacturing/material-issues/", {"work_order": self.run_.pk}).json()
        self.assertEqual((row["work_order_number"], row["makes"], row["lines"][0]["item_label"]),
                         (self.run_.number, "TAPE-1000 · PP tape, 1000 denier", "PP-RAFFIA · PP homopolymer"))


    def test_the_supervisor_draws_nothing_out_of_the_quarantine_bay(self):
        """Audit, 9 October: 40 kg went into a run out of the bay a delivery refused."""
        from django.utils import timezone

        from apps.inventory.models import MovementType, StockMovement, Warehouse

        bay = Warehouse.objects.create(code="QC", name="Quality hold", is_quarantine=True)
        StockMovement.objects.create(item=self.virgin, warehouse=bay, movement_type=MovementType.RECEIPT,
                                     uom=self.kg, quantity=Decimal("100"), unit_cost=Decimal("100"),
                                     occurred_at=timezone.now())
        supervisor = self.as_("Production Supervisor")
        issue = supervisor.post("/api/manufacturing/material-issues/", {
            "work_order": self.run_.pk, "issue_date": str(TODAY), "warehouse": bay.pk}, format="json")
        self.assertEqual(issue.status_code, 201, issue.content)
        number = issue.json()["id"]
        line = supervisor.post("/api/manufacturing/material-issue-lines/", {
            "issue": number, "item": self.virgin.pk, "quantity": "40", "uom": self.kg.pk}, format="json")
        self.assertEqual(line.status_code, 201, line.content)
        posted = supervisor.post(f"/api/manufacturing/material-issues/{number}/post/", {}, format="json")
        self.assertEqual(posted.status_code, 400, posted.content)
        self.assertIn("holds goods awaiting inspection", posted.content.decode())
        self.assertEqual(self.virgin.on_hand_at(bay), Decimal("100"))


class BookingTests(FloorScreensTestCase):
    def test_a_booking_names_its_step_and_the_run_comes_with_it(self):
        supervisor = self.as_("Production Supervisor")
        [step] = supervisor.get("/api/manufacturing/work-order-operations/",
                                {"work_order__status": "released", "search": self.run_.number}).json()
        self.assertEqual(step["label"], f"{self.run_.number} · 10 Extrude · TAPE-1000")
        made = supervisor.post("/api/manufacturing/time-bookings/", {
            "operation": step["id"], "booking_date": str(TODAY), "minutes": "60"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        booking = TimeBooking.objects.get()
        self.assertEqual(booking.work_order, self.run_)
        posted = supervisor.post(f"/api/manufacturing/time-bookings/{booking.pk}/post/", {}, format="json")
        self.assertEqual(posted.status_code, 200, posted.content)
        self.assertEqual(Decimal(posted.json()["posted_value"]), Decimal("360"))
        [row] = supervisor.get("/api/manufacturing/time-bookings/", {"work_order": self.run_.pk}).json()
        self.assertEqual((row["work_order_number"], row["operation_name"]), (self.run_.number, "10 Extrude"))

    def test_a_step_of_another_run_is_refused_as_it_is_written(self):
        other = self.routed("1000")
        refused = self.as_("Production Supervisor").post("/api/manufacturing/time-bookings/", {
            "work_order": self.run_.pk, "operation": other.operations.get().pk, "booking_date": str(TODAY),
            "minutes": "60"}, format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("belongs to", str(refused.json()))
        self.assertFalse(TimeBooking.objects.exists())


class OutputTests(FloorScreensTestCase):
    def test_the_list_names_the_run_and_what_it_made(self):
        entry = self.produce(self.run_, "500")
        [row] = self.as_("Production Supervisor").get("/api/manufacturing/production-entries/",
                                                      {"posted": "false"}).json()
        self.assertEqual((row["id"], row["work_order_number"], row["makes"], row["uom_code"]),
                         (entry.pk, self.run_.number, "TAPE-1000 · PP tape, 1000 denier", "kg"))
