"""
Extruder bank EXT-1 with lines E-1 and E-2. E-1 runs 10, 12 and 8 hours
on three days (30), plus 5 hours booked and taken back; E-2 runs 4.

E-1 breaks down three times in those days: 90 minutes and 150 minutes,
both repaired, and 60 minutes still being worked on. So three failures
in 30 running hours, one every 10; and the two repaired took 120
minutes on average. E-2 breaks once, 30 minutes: one in 4 hours, 30
minutes to repair. The bank as a whole: four failures in 34 hours, one
every 8.5, and (90 + 150 + 30) / 3 = 90 minutes to repair.

A breakdown cancelled as raised in error is no failure, and one after
the window is not in it. The first repair took a fitter 60 and 45
minutes: 105 minutes of labour.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from .machines import Machine
from .maintenance import MaintenanceJob, MaintenanceLabour, raise_breakdown, reliability
from .shifts import Downtime, DowntimeReason
from .tests_maintenance import MaintenanceTestCase
from .tests_orders import TODAY

DAY = datetime.timedelta(days=1)


class BreakdownTestCase(MaintenanceTestCase):
    def setUp(self):
        super().setUp()
        from apps.core.models import Party, PartyRole, PartyRoleAssignment
        from apps.hr.models import Employee

        self.e1, self.e2 = (Machine.objects.create(work_centre=self.loom, code=code)
                            for code in ("E-1", "E-2"))
        self.broke = DowntimeReason.objects.create(code="BRK", name="Breakdown")
        self.changeover = DowntimeReason.objects.create(code="CHG", name="Changeover",
                                                        is_planned=True)
        party = Party.objects.create(code="EMP-0301", name="Fitter")
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
        self.fitter = Employee.objects.create(party=party, employee_number="EMP-0301",
                                              hire_date=datetime.date(2020, 1, 1))

    def stopped(self, minutes, machine=None, on=TODAY, reason=None):
        return Downtime.objects.create(work_centre=self.loom, machine=machine or self.e1,
                                       shift_date=on, reason=reason or self.broke,
                                       minutes=Decimal(minutes))

    def broken(self, minutes, machine=None, on=TODAY, fault="Screen blocked"):
        return raise_breakdown(self.stopped(minutes, machine, on), fault)

    def ran(self, hours, machine, on=TODAY):
        from .orders import TimeBooking

        order = self.routed() if not hasattr(self, "_run") else self._run
        self._run = order
        booking = TimeBooking.objects.create(
            work_order=order, operation=order.operations.get(), booking_date=on,
            machine=machine, minutes=Decimal(hours) * 60)
        booking.post()
        return booking


class RaisedFromAStoppageTests(BreakdownTestCase):
    def test_raised_on_the_stoppage_it_came_from(self):
        stoppage = self.stopped("90")
        job = raise_breakdown(stoppage, "  Screen   blocked ", technician=self.fitter)
        self.assertEqual((job.is_breakdown, job.downtime, job.machine, job.due_on,
                          job.planned_minutes, job.fault, job.technician, job.schedule),
                         (True, stoppage, self.e1, TODAY, Decimal("90"), "Screen blocked",
                          self.fitter, None))
        job = raise_breakdown(self.stopped("30"), "Heater", planned_minutes="240")
        self.assertEqual(job.planned_minutes, Decimal("240"))

    def test_what_is_not_a_breakdown(self):
        with self.assertRaisesMessage(ValidationError, "is planned; a breakdown is not"):
            raise_breakdown(self.stopped("30", reason=self.changeover), "x")
        with self.assertRaisesMessage(ValidationError, "Say what failed"):
            raise_breakdown(self.stopped("30"), "  ")
        stoppage = self.stopped("30", on=TODAY + DAY)
        stoppage.void("Booked twice")
        with self.assertRaisesMessage(ValidationError, "was withdrawn"):
            raise_breakdown(stoppage, "x")
        with self.assertRaisesMessage(ValidationError, "takes some time"):
            raise_breakdown(self.stopped("30", on=TODAY + 2 * DAY), "x", planned_minutes="0")

    def test_one_stoppage_one_breakdown(self):
        stoppage = self.stopped("30")
        job = raise_breakdown(stoppage, "Heater")
        with self.assertRaisesMessage(ValidationError, "is already raised on"):
            raise_breakdown(stoppage, "Heater again")
        job.cancel("Raised in error")
        self.assertEqual(raise_breakdown(stoppage, "Heater").downtime, stoppage)


class RepairedTests(BreakdownTestCase):
    def test_done_on_the_stoppage_it_already_has(self):
        job = self.broken("90")
        before = Downtime.objects.count()
        job.complete(on_date=TODAY, cause="Contaminated regrind", action="Screen changed")
        job.refresh_from_db()
        self.assertEqual(Downtime.objects.count(), before)
        self.assertEqual((job.done_on, job.actual_minutes, job.cause, job.action_taken),
                         (TODAY, Decimal("90.00"), "Contaminated regrind", "Screen changed"))

    def test_a_repair_says_what_was_done(self):
        job = self.broken("90")
        with self.assertRaisesMessage(ValidationError, "Say what was done"):
            job.complete(on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "is the stoppage's"):
            job.complete(on_date=TODAY, minutes="45", action="Screen changed")

    def test_labour_on_it_until_it_is_done(self):
        job = self.broken("90")
        MaintenanceLabour.objects.create(job=job, technician=self.fitter, worked_on=TODAY,
                                         minutes=Decimal("60"))
        row = MaintenanceLabour.objects.create(job=job, technician=self.fitter,
                                               worked_on=TODAY, minutes=Decimal("45"))
        self.assertEqual(job.labour_minutes(), Decimal("105"))
        job.complete(on_date=TODAY, action="Screen changed")
        with self.assertRaisesMessage(ValidationError, "is done; its labour is as booked"):
            MaintenanceLabour.objects.create(job=job, technician=self.fitter,
                                             worked_on=TODAY, minutes=Decimal("5"))
        with self.assertRaisesMessage(ValidationError, "is done; its labour is as booked"):
            row.delete()

    def test_a_cancelled_job_is_kept_as_it_was(self):
        job = self.broken("90")
        job.cancel("Raised in error")
        job.notes = "Changed afterwards"
        with self.assertRaisesMessage(ValidationError, "was cancelled"):
            job.save()
        with self.assertRaisesMessage(ValidationError, "was cancelled"):
            MaintenanceLabour.objects.create(job=job, technician=self.fitter,
                                             worked_on=TODAY, minutes=Decimal("5"))

    def test_a_breakdown_is_a_record_and_is_not_deleted(self):
        job = self.broken("90")
        with self.assertRaisesMessage(ValidationError, "is a record of a failure"):
            job.delete()
        service = self.schedule(days=90).raise_job(as_of=TODAY)
        MaintenanceLabour.objects.create(job=service, technician=self.fitter,
                                         worked_on=TODAY, minutes=Decimal("5"))
        with self.assertRaisesMessage(ValidationError, "is a record of a failure"):
            service.delete()
        untouched = self.schedule(days=30).raise_job(as_of=TODAY)
        untouched.delete()
        self.assertFalse(MaintenanceJob.objects.filter(pk=untouched.pk).exists())

    def test_a_cancelled_job_is_neither_open_nor_done(self):
        job = self.broken("90")
        with self.assertRaisesMessage(ValidationError, "Say why"):
            job.cancel(" ")
        job.cancel("Raised in error")
        self.assertFalse(job.is_open())
        self.assertFalse(MaintenanceJob.objects.open().filter(pk=job.pk).exists())
        with self.assertRaisesMessage(ValidationError, "was cancelled"):
            job.complete(on_date=TODAY, action="x")
        with self.assertRaisesMessage(ValidationError, "was cancelled"):
            job.cancel("Again")
        done = self.broken("30", on=TODAY + DAY)
        done.complete(on_date=TODAY + DAY, action="Fixed")
        with self.assertRaisesMessage(ValidationError, "was already done"):
            done.cancel("Too late")


class TheStoppageUnderItTests(BreakdownTestCase):
    def test_not_withdrawn_while_a_job_rests_on_it(self):
        job = self.broken("90")
        with self.assertRaisesMessage(ValidationError, "rests on this stoppage"):
            job.downtime.void("Booked wrong")
        job.cancel("Raised in error")
        job.downtime.void("Booked wrong")

    def test_nor_the_stoppage_a_service_became(self):
        schedule = self.schedule(days=90)
        job = schedule.raise_job(as_of=TODAY)
        stoppage = job.complete(on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "rests on this stoppage"):
            stoppage.void("Booked wrong")

    def test_a_done_job_is_as_it_was_done(self):
        job = self.broken("90")
        job.complete(on_date=TODAY, action="Screen changed")
        job.notes = "Changed afterwards"
        with self.assertRaisesMessage(ValidationError, "is done; it is as it was done"):
            job.save()
        with self.assertRaisesMessage(ValidationError, "is done; it is as it was done"):
            job.delete()


class CancelledJobsTakeNoCapacityTests(BreakdownTestCase):
    def test_a_cancelled_service_is_off_the_board(self):
        from .maintenance import due_now

        schedule = self.schedule(days=90)
        job = schedule.raise_job(as_of=TODAY)
        self.assertEqual(due_now(TODAY), [])
        job.cancel("Machine sold")
        self.assertEqual(due_now(TODAY), [schedule])
        self.assertEqual(schedule.raise_job(as_of=TODAY).schedule, schedule)


class ReliabilityTests(BreakdownTestCase):
    def setUp(self):
        super().setUp()
        for hours, day in (("10", 0), ("12", 1), ("8", 2)):
            self.ran(hours, self.e1, TODAY + day * DAY)
        self.ran("5", self.e1, TODAY).void(memo="Wrong machine")
        self.ran("4", self.e2, TODAY + DAY)
        first = self.broken("90")
        MaintenanceLabour.objects.create(job=first, technician=self.fitter, worked_on=TODAY,
                                         minutes=Decimal("60"))
        MaintenanceLabour.objects.create(job=first, technician=self.fitter, worked_on=TODAY,
                                         minutes=Decimal("45"))
        first.complete(on_date=TODAY, action="Screen changed")
        self.broken("150", on=TODAY + 2 * DAY).complete(on_date=TODAY + 2 * DAY,
                                                         action="Heater band")
        self.broken("60", on=TODAY + 2 * DAY)
        self.broken("30", machine=self.e2, on=TODAY + DAY).complete(on_date=TODAY + DAY,
                                                                    action="Belt")
        self.broken("45", on=TODAY + DAY).cancel("Raised in error")
        late = self.broken("20", on=TODAY + 5 * DAY)
        MaintenanceLabour.objects.create(job=late, technician=self.fitter,
                                         worked_on=TODAY + 5 * DAY, minutes=Decimal("30"))

    def test_each_machine_on_its_own_hours(self):
        e1 = reliability(TODAY, TODAY + 2 * DAY, machine=self.e1)
        self.assertEqual((e1["failures"], e1["repaired"], e1["run_hours"], e1["mtbf_hours"],
                          e1["mttr_minutes"], e1["labour_minutes"]),
                         (3, 2, Decimal("30"), Decimal("10"), Decimal("120"), Decimal("105")))
        e2 = reliability(TODAY, TODAY + 2 * DAY, machine=self.e2)
        self.assertEqual((e2["failures"], e2["mtbf_hours"], e2["mttr_minutes"]),
                         (1, Decimal("4"), Decimal("30")))

    def test_the_bank(self):
        bank = reliability(TODAY, TODAY + 2 * DAY, work_centre=self.loom)
        self.assertEqual((bank["failures"], bank["run_hours"], bank["mtbf_hours"],
                          bank["mttr_minutes"]),
                         (4, Decimal("34"), Decimal("8.5"), Decimal("90")))

    def test_a_bank_on_its_own_hours(self):
        from .orders import WorkCentre

        press = WorkCentre.objects.create(code="PRESS", name="Baling press")
        stoppage = Downtime.objects.create(work_centre=press, shift_date=TODAY,
                                           reason=self.broke, minutes=Decimal("40"))
        raise_breakdown(stoppage, "Hydraulic leak")
        found = reliability(TODAY, TODAY + 2 * DAY, work_centre=press)
        self.assertEqual((found["failures"], found["run_hours"], found["mtbf_hours"]),
                         (1, Decimal("0"), Decimal("0")))

    def test_no_failures_is_no_figure_not_infinity(self):
        quiet = reliability(TODAY + 10 * DAY, TODAY + 11 * DAY, machine=self.e1)
        self.assertEqual((quiet["failures"], quiet["mtbf_hours"], quiet["mttr_minutes"]),
                         (0, None, None))


class BreakdownApiTests(BreakdownTestCase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_superuser("fitter-desk"))
        self.base = "/api/manufacturing/maintenance-jobs/"

    def test_raised_worked_and_closed(self):
        stoppage = self.stopped("90")
        response = self.client.post(self.base + "breakdown/", {
            "downtime": stoppage.pk, "fault": "Screen blocked",
            "technician": self.fitter.pk}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        job = response.json()
        self.assertEqual((job["is_breakdown"], job["fault"], job["downtime"]),
                         (True, "Screen blocked", stoppage.pk))
        again = self.client.post(self.base + "breakdown/", {
            "downtime": stoppage.pk, "fault": "Again"}, format="json")
        self.assertEqual(again.status_code, 400)
        response = self.client.post(f"{self.base}{job['id']}/labour/", {
            "technician": self.fitter.pk, "worked_on": str(TODAY), "minutes": "60"},
            format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["labour_minutes"], "60.00")
        for bad in ({"worked_on": "someday", "minutes": "60"},
                    {"worked_on": str(TODAY), "minutes": "lots"},
                    {"worked_on": str(TODAY), "minutes": "NaN"},
                    {"worked_on": str(TODAY), "minutes": "0"}):
            response = self.client.post(f"{self.base}{job['id']}/labour/",
                                        {"technician": self.fitter.pk, **bad}, format="json")
            self.assertEqual(response.status_code, 400, bad)
        response = self.client.post(f"{self.base}{job['id']}/complete/", {
            "on_date": str(TODAY), "cause": "Regrind", "action": "Screen changed"},
            format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["action_taken"], response.json()["actual_minutes"]),
                         ("Screen changed", "90.00"))
        response = self.client.patch(f"{self.base}{job['id']}/", {"notes": "later"},
                                     format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.delete(f"{self.base}{job['id']}/").status_code, 400)

    def test_cancelled_and_reliability(self):
        job = self.broken("90")
        response = self.client.post(f"{self.base}{job.pk}/cancel/", {"reason": "Error"},
                                    format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["cancelled_reason"], "Error")
        self.ran("10", self.e1)
        self.broken("30").complete(on_date=TODAY, action="Fixed")
        response = self.client.get(self.base + "reliability/", {
            "start": str(TODAY), "end": str(TODAY), "machine": "E-1"})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["failures"], body["mtbf_hours"], body["mttr_minutes"]),
                         (1, "10.00", "30.00"))
        response = self.client.get(self.base + "reliability/", {
            "start": str(TODAY), "end": str(TODAY)})
        self.assertEqual(response.status_code, 400)
