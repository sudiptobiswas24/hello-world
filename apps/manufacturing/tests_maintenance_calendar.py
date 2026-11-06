"""
The maintenance calendar: a month, day by day, with the jobs due or
done on each and the schedules whose calendar clock runs out with no
job raised. The gearbox service last done on the 15th of May, every 30
days, is due on the 14th of June; raised as a job it sits on that day
as a job, not as a schedule due.
"""

import datetime

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from .models import WorkCentre
from .maintenance import calendar_month
from .tests_maintenance import MaintenanceTestCase

MAY_15 = datetime.date(2026, 5, 15)
JUNE_1 = datetime.date(2026, 6, 1)


class CalendarTests(MaintenanceTestCase):
    def test_each_day_carries_what_falls_on_it(self):
        raised = self.schedule(days=30, last=MAY_15)
        job = raised.raise_job()
        waiting = self.schedule(days=36, last=MAY_15)   # due 20 June, no job
        self.schedule(days=60, last=MAY_15)             # due 14 July: next month
        month = calendar_month(2026, 6, today=JUNE_1)
        self.assertEqual((len(month), month[0]["date"], month[-1]["date"]),
                         (30, JUNE_1, datetime.date(2026, 6, 30)))
        by_day = {day["date"]: day for day in month}
        self.assertEqual([(row["id"], row["title"], row["where"], row["done"], row["overdue"], row["is_breakdown"])
                          for row in by_day[datetime.date(2026, 6, 14)]["jobs"]],
                         [(job.pk, "Gearbox service", "EXT-1", False, False, False)])
        self.assertEqual(by_day[datetime.date(2026, 6, 14)]["due"], [])
        self.assertEqual([(row["id"], row["name"], row["overdue"]) for row in by_day[datetime.date(2026, 6, 20)]["due"]],
                         [(waiting.pk, "Gearbox service", False)])
        self.assertEqual(sum(len(day["jobs"]) + len(day["due"]) for day in month), 2)
        # Read on the 25th, both are late.
        later = {day["date"]: day for day in calendar_month(2026, 6, today=datetime.date(2026, 6, 25))}
        self.assertEqual((later[datetime.date(2026, 6, 14)]["jobs"][0]["overdue"], later[datetime.date(2026, 6, 20)]["due"][0]["overdue"]),
                         (True, True))
        elsewhere = WorkCentre.objects.create(code="PRINT-2", name="Printing 2")
        self.assertEqual(sum(len(day["jobs"]) + len(day["due"]) for day in calendar_month(2026, 6, work_centre=elsewhere, today=JUNE_1)), 0)

    def test_the_maintenance_department_reads_it_and_the_books_do_not(self):
        call_command("setup_roles", verbosity=0)
        self.schedule(days=30, last=MAY_15).raise_job()

        def as_(role):
            user = User.objects.create_user(role.replace(" ", "_").lower())
            user.groups.add(Group.objects.get(name=role))
            client = APIClient()
            client.force_authenticate(user)
            return client

        fitter = as_("Maintenance")
        seen = fitter.get("/api/manufacturing/maintenance-jobs/calendar/", {"year": "2026", "month": "6"})
        self.assertEqual((seen.status_code, seen.json()["month"], len(seen.json()["days"])), (200, 6, 30), seen.content)
        self.assertEqual(len(seen.json()["days"][13]["jobs"]), 1)
        self.assertEqual(fitter.get("/api/manufacturing/maintenance-jobs/calendar/", {"year": "2026", "month": "13"}).status_code, 400)
        self.assertEqual(as_("Bookkeeper").get("/api/manufacturing/maintenance-jobs/calendar/").status_code, 403)
