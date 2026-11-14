"""
The Plant screens' server side, asked as the people who use it.

Stoppages on loom E-1: two breakdowns of 90 and 30 minutes and a
changeover of 60. By reason: Breakdown 120 minutes in 2 stoppages,
66.7% of the 180; Changeover 60 in 1, 33.3%.

Electricity: the energy fixture's two days (tests_energy). The meter
read 500 kWh: 120 and 250 on intervals with bookings (370 to runs), 130
on the night nothing was booked (idle); at 8.50 a kWh, 4,250.00. Read on
1 and 2 Sep; asked 1 to 3 Sep, the 3rd was not read.
"""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from .maintenance import MaintenanceLabour
from .tests_breakdowns import BreakdownTestCase
from .tests_energy import SEP, EnergyTestCase
from .tests_orders import TODAY


def as_role(role, name=None):
    user = User.objects.create_user(name or role.replace(" ", "-").lower())
    user.groups.add(Group.objects.get(name=role))
    client = APIClient()
    client.force_authenticate(user)
    return client


class StoppagesTests(BreakdownTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.stopped("90")
        self.stopped("30", machine=self.e2)
        self.stopped("60", reason=self.changeover)

    def test_by_reason_the_most_hours_first(self):
        client = as_role("Production Supervisor")
        day = TODAY.isoformat()
        response = client.get("/api/manufacturing/downtime/by-reason/",
                              {"start": day, "end": day, "work_centre": self.loom.code})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual([(row["reason"], row["stoppages"], row["minutes"], row["share"], row["planned"])
                          for row in body["rows"]],
                         [("BRK", 2, "120.00", "66.7", False), ("CHG", 1, "60.00", "33.3", True)])
        self.assertEqual(body["total_minutes"], "180.00")
        one = client.get("/api/manufacturing/downtime/by-reason/",
                         {"start": day, "end": day, "machine": self.e2.code}).json()
        self.assertEqual([(row["reason"], row["minutes"]) for row in one["rows"]], [("BRK", "30.00")])

    def test_each_stoppage_says_where_and_why(self):
        rows = as_role("Production Supervisor").get(
            "/api/manufacturing/downtime/", {"reason__is_planned": "false"}).json()
        self.assertEqual(sorted((row["serves"], row["reason_name"], row["is_planned"]) for row in rows),
                         [("E-1", "Breakdown", False), ("E-2", "Breakdown", False)])

    def test_a_list_refuses_a_filter_it_does_not_apply(self):
        response = as_role("Production Supervisor").get("/api/manufacturing/downtime/", {"meter": "5"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("cannot be narrowed by meter", str(response.json()))


class JobsTests(BreakdownTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.job = self.broken("90")
        MaintenanceLabour.objects.create(job=self.job, technician=self.fitter,
                                         worked_on=TODAY, minutes=Decimal("45"))

    def test_the_fitters_office_reads_a_job_with_its_time(self):
        client = as_role("Maintenance")
        [row] = client.get("/api/manufacturing/maintenance-jobs/",
                           {"done_on__isnull": "true", "cancelled_at__isnull": "true"}).json()
        self.assertEqual((row["serves"], row["title"], row["status"]), ("E-1", "Screen blocked", "open"))
        self.assertNotIn("labour", row)  # the list stays one query a page
        detail = client.get(f"/api/manufacturing/maintenance-jobs/{self.job.pk}/").json()
        self.assertEqual([(line["technician_name"], line["minutes"]) for line in detail["labour"]],
                         [("Fitter", "45.00")])
        self.assertEqual((detail["spares"], detail["spares_value"]), ([], "0"))

    def test_the_fitters_office_does_not_run_the_floor(self):
        client = as_role("Maintenance")
        self.assertEqual(client.post("/api/manufacturing/work-orders/", {}, format="json").status_code, 403)
        self.assertEqual(client.get("/api/manufacturing/production-entries/").status_code, 403)


class ElectricitySummaryTests(EnergyTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.the_two_days()

    def test_each_meter_to_runs_idle_and_unread(self):
        client = as_role("Maintenance")
        response = client.get("/api/manufacturing/energy-meters/summary/",
                              {"start": SEP(1).isoformat(), "end": SEP(3).isoformat()})
        self.assertEqual(response.status_code, 200, response.content)
        [row] = response.json()
        self.assertEqual((row["code"], row["kwh"], row["run_kwh"], row["idle_kwh"], row["cost"],
                          row["readings"], row["days_unread"]),
                         ("EM-L17", "500.000", "370.000", "130.000", "4250.00", 3, 1))

    def test_a_window_holding_part_of_it(self):
        [row] = as_role("Maintenance").get("/api/manufacturing/energy-meters/summary/",
                                           {"start": SEP(2).isoformat(), "end": SEP(2).isoformat()}).json()
        self.assertEqual((row["kwh"], row["run_kwh"], row["idle_kwh"]), ("250.000", "250.000", "0.000"))

