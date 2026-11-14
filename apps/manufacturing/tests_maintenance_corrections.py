"""
A maintenance job corrected after the fact, and the ways round it.

The gearbox on EXT-1 is serviced every ninety days and was last done on
3 March, so it falls due on Monday 1 June. Completed that day it falls
due again on 30 August. Completed on the wrong loom and reopened, the
clock goes back to 3 March, so it is due again on 1 June, and its
480-minute stoppage stops counting. Done again on Sunday 31 May, it
next falls due on 29 August.

A breakdown keeps the stoppage it was raised on whatever its repair
said. A stoppage under a job keeps the job's machine, and once the job
is done the stoppage is as the job was done on it.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from .maintenance import MaintenanceJob
from .shifts import Downtime
from .tests_breakdowns import BreakdownTestCase
from .tests_orders import TODAY

DAY = datetime.timedelta(days=1)
SEED = datetime.date(2026, 3, 3)


class CorrectionTestCase(BreakdownTestCase):
    def gearbox(self, last=SEED):
        return self.schedule(days=90, last=last)

    def done_service(self, schedule=None, on=TODAY):
        schedule = schedule or self.gearbox()
        job = schedule.raise_job(as_of=TODAY)
        job.complete(on_date=on)
        job.refresh_from_db()
        return schedule, job


class ACompletionMadeInErrorTests(CorrectionTestCase):
    def test_an_open_job_has_no_completion_to_withdraw(self):
        job = self.gearbox().raise_job(as_of=TODAY)
        with self.assertRaisesMessage(ValidationError, "is not done; there is nothing to reopen"):
            job.reopen("Wrong loom")

    def test_a_cancelled_job_is_not_reopened(self):
        job = self.gearbox().raise_job(as_of=TODAY)
        job.cancel("Raised twice")
        with self.assertRaisesMessage(ValidationError, "was cancelled"):
            job.reopen("Wrong loom")

    def test_it_says_why(self):
        _, job = self.done_service()
        with self.assertRaisesMessage(ValidationError, "Say why the completion is withdrawn"):
            job.reopen("  ")

    def test_not_once_the_schedule_has_moved_on_to_a_later_job(self):
        schedule, job = self.done_service()
        later = schedule.raise_job(as_of=TODAY)
        with self.assertRaisesMessage(ValidationError, "has been raised on the schedule since"):
            job.reopen("Wrong loom")
        # Withdrawn itself, the later job no longer stands in the way.
        later.cancel("Raised on a completion made in error")
        job.reopen("Wrong loom")
        job.refresh_from_db()
        self.assertTrue(job.is_open())

    def test_not_over_a_clock_set_by_hand_since(self):
        schedule, job = self.done_service()
        schedule.last_done_on = TODAY - 2 * DAY
        schedule.save()
        with self.assertRaisesMessage(ValidationError, "by hand since"):
            job.reopen("Wrong loom")
        schedule.refresh_from_db()
        self.assertEqual(schedule.last_done_on, TODAY - 2 * DAY)

    def test_a_service_goes_back_on_the_board_as_the_completion_found_it(self):
        schedule, job = self.done_service()
        stoppage = job.downtime
        self.assertEqual((schedule.due_on(TODAY), job.replaced_last_done_on, stoppage.minutes),
                         (datetime.date(2026, 8, 30), SEED, Decimal("480.00")))

        job.reopen("Done on the wrong loom")
        job.refresh_from_db()
        schedule.refresh_from_db()
        stoppage.refresh_from_db()
        self.assertEqual((job.done_on, job.actual_minutes, job.downtime_id, job.replaced_last_done_on),
                         (None, None, None, None))
        self.assertTrue(MaintenanceJob.objects.open().filter(pk=job.pk).exists())
        self.assertEqual((schedule.last_done_on, schedule.due_on(TODAY), schedule.is_due(TODAY)),
                         (SEED, TODAY, True))
        # The stoppage stays on the record, withdrawn, and says why.
        self.assertIsNotNone(stoppage.voided_at)
        self.assertEqual(stoppage.voided_reason,
                         "Gearbox service on EXT-1, 2026-06-01 reopened: Done on the wrong loom")

        # And done again, it is done as any service is.
        again = job.complete(on_date=TODAY - DAY, minutes=Decimal("400"))
        job.refresh_from_db()
        schedule.refresh_from_db()
        self.assertEqual((again.minutes, job.actual_minutes, job.replaced_last_done_on),
                         (Decimal("400"), Decimal("400.00"), SEED))
        self.assertEqual((schedule.last_done_on, schedule.due_on(TODAY)),
                         (TODAY - DAY, datetime.date(2026, 8, 29)))

    def test_a_first_service_reopened_is_due_again_at_once(self):
        schedule, job = self.done_service(self.gearbox(last=None))
        self.assertIsNone(job.replaced_last_done_on)
        job.reopen("Not done yet")
        schedule.refresh_from_db()
        self.assertEqual((schedule.last_done_on, schedule.due_on(TODAY)), (None, TODAY))

    def test_a_repair_reopened_keeps_the_stoppage_it_was_raised_on(self):
        job = self.broken("90")
        job.complete(on_date=TODAY, cause="Regrind", action="Screen changed")
        job.reopen("Still leaking")
        job.refresh_from_db()
        stoppage = Downtime.objects.get(pk=job.downtime_id)
        self.assertEqual((job.done_on, job.actual_minutes, job.cause, job.action_taken),
                         (None, None, "", ""))
        self.assertIsNone(stoppage.voided_at)
        job.complete(on_date=TODAY + DAY, action="Die cleaned")
        job.refresh_from_db()
        self.assertEqual((job.done_on, job.actual_minutes, job.action_taken),
                         (TODAY + DAY, Decimal("90.00"), "Die cleaned"))


class WhenAJobIsDoneTests(CorrectionTestCase):
    def test_not_on_a_day_that_has_not_come(self):
        schedule = self.gearbox()
        job = schedule.raise_job(as_of=TODAY)
        tomorrow = timezone.localdate() + DAY
        with self.assertRaisesMessage(ValidationError, "that day has not come"):
            job.complete(on_date=tomorrow)
        job.refresh_from_db()
        schedule.refresh_from_db()
        self.assertEqual((job.done_on, schedule.last_done_on), (None, SEED))
        self.assertFalse(Downtime.objects.exists())

    def test_not_before_the_service_before_it(self):
        schedule, _ = self.done_service()
        job = schedule.raise_job(as_of=TODAY)
        with self.assertRaisesMessage(ValidationError, "was last done on 2026-06-01"):
            job.complete(on_date=TODAY - 5 * DAY)
        schedule.refresh_from_db()
        self.assertEqual(schedule.last_done_on, TODAY)
        # On the same day as the last one is a second service that day, not an earlier one.
        job.complete(on_date=TODAY)

    def test_a_repair_not_before_its_stoppage(self):
        job = self.broken("60", on=TODAY)
        with self.assertRaisesMessage(ValidationError, "it was not repaired on 2026-05-29, before it broke"):
            job.complete(on_date=TODAY - 3 * DAY, action="Fixed")
        job.complete(on_date=TODAY, action="Fixed")


class AJobStaysWhereItWasRaisedTests(CorrectionTestCase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(User.objects.create_superuser("planner-desk"))
        self.base = "/api/manufacturing/maintenance-jobs/"

    def test_a_second_job_is_not_typed_onto_a_schedule_on_the_board(self):
        schedule = self.gearbox()
        schedule.raise_job(as_of=TODAY)
        response = self.api.post(self.base, {
            "schedule": schedule.pk, "work_centre": self.loom.pk, "due_on": str(TODAY),
            "planned_minutes": "60"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        # Typed in, it is a one-off: a schedule's job is raised from the schedule.
        self.assertIsNone(response.json()["schedule"])
        self.assertEqual(schedule.jobs.open().count(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MaintenanceJob.objects.create(schedule=schedule, work_centre=self.loom,
                                          due_on=TODAY, planned_minutes=Decimal("60"))

    def test_a_schedules_job_is_on_the_schedules_machine(self):
        schedule = self.schedule(days=90)
        schedule.machine = self.e1
        schedule.save()
        with self.assertRaisesMessage(ValidationError, "A job is where its schedule is: E-1"):
            MaintenanceJob.objects.create(schedule=schedule, work_centre=self.loom, machine=self.e2,
                                          due_on=TODAY, planned_minutes=Decimal("60"))

    def test_a_repair_stays_on_the_machine_that_stopped(self):
        job = self.broken("90", machine=self.e1)
        response = self.api.patch(f"{self.base}{job.pk}/", {"machine": self.e2.pk}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        job.refresh_from_db()
        self.assertEqual(job.machine_id, self.e1.pk)
        # What may change on an open job still does.
        response = self.api.patch(f"{self.base}{job.pk}/", {"notes": "Fitter on nights"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)

    def test_a_one_off_stays_on_its_machine(self):
        job = MaintenanceJob.objects.create(work_centre=self.loom, machine=self.e1, due_on=TODAY,
                                            planned_minutes=Decimal("60"))
        response = self.api.patch(f"{self.base}{job.pk}/", {"machine": self.e2.pk}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("cancel this one and raise another", str(response.content))


class TheStoppageUnderAJobTests(CorrectionTestCase):
    def test_a_done_jobs_stoppage_is_as_the_job_was_done_on_it(self):
        _, job = self.done_service()
        stoppage = Downtime.objects.get(pk=job.downtime_id)
        stoppage.minutes = Decimal("30")
        with self.assertRaisesMessage(ValidationError, "reopen it to correct the stoppage"):
            stoppage.save()
        stoppage = Downtime.objects.get(pk=job.downtime_id)
        stoppage.notes = "Gearbox and coupling"
        stoppage.save()  # a note is not what happened
        with self.assertRaisesMessage(ValidationError, "reopen it first"):
            stoppage.void("Wrong loom")
        with self.assertRaisesMessage(ValidationError, "reopen it first"):
            stoppage.delete()

    def test_a_repairs_stoppage_keeps_its_machine_and_stays_a_failure(self):
        job = self.broken("90", machine=self.e1)
        stoppage = Downtime.objects.get(pk=job.downtime_id)
        stoppage.machine = self.e2
        with self.assertRaisesMessage(ValidationError, "cancel it to book the stoppage somewhere else"):
            stoppage.save()
        stoppage = Downtime.objects.get(pk=job.downtime_id)
        stoppage.reason = self.changeover
        with self.assertRaisesMessage(ValidationError, "is planned; cancel the job"):
            stoppage.save()
        with self.assertRaisesMessage(ValidationError, "cancel it first"):
            Downtime.objects.get(pk=job.downtime_id).delete()

    def test_an_open_repairs_stoppage_is_corrected_and_the_repair_closes_on_it(self):
        job = MaintenanceJob.objects.select_related("downtime").get(pk=self.broken("90").pk)
        stoppage = Downtime.objects.get(pk=job.downtime_id)
        stoppage.minutes = Decimal("120")
        stoppage.save()
        # The job in hand read the stoppage before it was corrected.
        self.assertEqual(job.downtime.minutes, Decimal("90.00"))
        job.complete(on_date=TODAY, action="Screen changed")
        job.refresh_from_db()
        self.assertEqual(job.actual_minutes, Decimal("120.00"))

    def test_a_stoppage_number_is_the_sequences_not_typed(self):
        stoppage = self.stopped("45")
        api = APIClient()
        api.force_authenticate(User.objects.create_superuser("shift-desk"))
        response = api.patch(f"/api/manufacturing/downtime/{stoppage.pk}/", {"number": "DT-OWN-1"},
                             format="json")
        self.assertEqual(response.status_code, 200, response.content)
        stoppage.refresh_from_db()
        self.assertTrue(stoppage.number.startswith("DT-"))
        self.assertNotEqual(stoppage.number, "DT-OWN-1")


class ReopeningThroughTheApiTests(CorrectionTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_maintenance_reopens_it_and_the_planner_reads_it(self):
        schedule, job = self.done_service()
        url = f"/api/manufacturing/maintenance-jobs/{job.pk}/reopen/"
        planner = self.as_("Production Planner")
        self.assertEqual(planner.get(f"/api/manufacturing/maintenance-jobs/{job.pk}/").status_code, 200)
        self.assertEqual(planner.post(url, {"reason": "Wrong loom"}, format="json").status_code, 403)

        fitter = self.as_("Maintenance")
        response = fitter.post(url, {"reason": ""}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        response = fitter.post(url, {"reason": "Wrong loom"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["status"], response.json()["done_on"]), ("open", None))
        schedule.refresh_from_db()
        self.assertEqual(schedule.last_done_on, SEED)
