"""
The BCS bank: C-1, C-2 and C-3 on 24 hours, two people a machine, a
12-hour day shift and a 12-hour night.

Four on days run two machines: 2 x 12 x 60 = 1,440 minutes. Two on
nights run one more (720), so 2,160 of the 4,320 the machines could
give. With one of the night pair on leave the night runs nothing: 1,440;
on half a day's leave, one and a half people still run nothing.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.hr.models import LeaveRequest, LeaveStatus, LeaveType

from .machines import Machine
from .manning import CrewAssignment, heads, manned_machines
from .orders import WorkCentre
from .tests_bag_counts import ConversionTestCase
from .tests_orders import TODAY


class CrewTestCase(ConversionTestCase):
    def setUp(self):
        super().setUp()
        WorkCentre.objects.filter(pk=self.cutting.pk).update(operators_per_machine=2)
        Machine.objects.filter(work_centre=self.cutting).update(available_hours_per_day=24)
        self.cutting.refresh_from_db()
        self.days = [self.employee(f"D-{n}", f"Day {n}") for n in range(4)]
        self.nights = [self.employee(f"N-{n}", f"Night {n}") for n in range(2)]
        for person in self.days:
            CrewAssignment.objects.create(employee=person, work_centre=self.cutting,
                                          shift=self.day, valid_from=TODAY)
        for person in self.nights:
            CrewAssignment.objects.create(employee=person, work_centre=self.cutting,
                                          shift=self.night, valid_from=TODAY)

    def away(self, person, half_day=False, status=LeaveStatus.APPROVED):
        return LeaveRequest.objects.create(employee=person, leave_type=LeaveType.SICK,
                                           start_date=TODAY, end_date=TODAY,
                                           half_day=half_day, status=status)


class WhatTheCrewCanRunTests(CrewTestCase):
    def test_the_lesser_of_the_machines_and_the_crew(self):
        self.assertEqual((manned_machines(self.cutting, self.day, TODAY),
                          manned_machines(self.cutting, self.night, TODAY)), (2, 1))
        self.assertEqual(self.cutting.minutes_on(TODAY), Decimal("2160"))

    def test_someone_on_leave_is_not_on_the_shift(self):
        self.away(self.nights[0])
        self.assertEqual(heads(self.cutting, self.night, TODAY), Decimal("1"))
        self.assertEqual(self.cutting.minutes_on(TODAY), Decimal("1440"))

    def test_half_a_days_leave_is_half_a_person(self):
        self.away(self.nights[0], half_day=True)
        self.assertEqual(heads(self.cutting, self.night, TODAY), Decimal("1.5"))
        self.assertEqual(self.cutting.minutes_on(TODAY), Decimal("1440"))

    def test_leave_not_yet_approved_is_not_leave(self):
        self.away(self.nights[0], status=LeaveStatus.PENDING)
        self.assertEqual(heads(self.cutting, self.night, TODAY), Decimal("2"))

    def test_a_crew_never_runs_more_machines_than_there_are(self):
        for n in range(6):
            CrewAssignment.objects.create(
                employee=self.employee(f"X-{n}", "Extra"), work_centre=self.cutting,
                shift=self.night, valid_from=TODAY)
        # Eight on nights could run four; there are three.
        self.assertEqual(self.cutting.minutes_on(TODAY), Decimal("1440") + Decimal("2160"))

    def test_an_assignment_ended_or_not_begun_is_not_the_crew(self):
        CrewAssignment.objects.filter(employee=self.nights[1]).update(
            valid_to=TODAY - datetime.timedelta(days=1), valid_from=TODAY - datetime.timedelta(days=9))
        self.assertEqual(heads(self.cutting, self.night, TODAY), Decimal("1"))
        CrewAssignment.objects.filter(employee=self.nights[0]).update(
            valid_from=TODAY + datetime.timedelta(days=1))
        self.assertEqual(heads(self.cutting, self.night, TODAY), Decimal("0"))

    def test_a_bank_that_says_nothing_of_its_crew_is_not_limited_by_it(self):
        WorkCentre.objects.filter(pk=self.cutting.pk).update(operators_per_machine=None)
        self.cutting.refresh_from_db()
        self.assertIsNone(manned_machines(self.cutting, self.day, TODAY))
        self.assertEqual(self.cutting.minutes_on(TODAY), Decimal("4320"))

    def test_a_window_counts_each_day(self):
        self.away(self.nights[0])
        self.assertEqual(self.cutting.minutes_available(TODAY, TODAY + datetime.timedelta(days=1)),
                         Decimal("1440") + self.cutting.minutes_on(TODAY + datetime.timedelta(days=1)))


class OneCrewAtATimeTests(CrewTestCase):
    def test_a_person_in_two_crews_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "is already on CONV D"):
            CrewAssignment.objects.create(employee=self.days[0], work_centre=self.cutting,
                                          shift=self.night, valid_from=TODAY)

    def test_after_the_first_ends_they_move(self):
        CrewAssignment.objects.filter(employee=self.days[0]).update(
            valid_to=TODAY + datetime.timedelta(days=6))
        CrewAssignment.objects.create(employee=self.days[0], work_centre=self.cutting,
                                      shift=self.night, valid_from=TODAY + datetime.timedelta(days=7))
        with self.assertRaisesMessage(ValidationError, "is already on"):
            CrewAssignment.objects.create(employee=self.days[0], work_centre=self.cutting,
                                          shift=self.night, valid_from=TODAY + datetime.timedelta(days=3),
                                          valid_to=TODAY + datetime.timedelta(days=4))


class PlannedAndDispatchedOnTheCrewTests(CrewTestCase):
    def test_the_planners_book(self):
        from apps.planning.capacity import LoadBook

        self.away(self.nights[0])
        book = LoadBook(self.plant, TODAY, TODAY + datetime.timedelta(days=30))
        self.assertEqual(book.capacity_minutes(self.cutting, TODAY), Decimal("1440"))

    def test_the_board_uses_only_the_machines_a_shift_can_run(self):
        from django.utils import timezone

        from .dispatch import build

        start = timezone.make_aware(datetime.datetime.combine(TODAY, datetime.time(8)))
        used = {row["machine"] for row in build(start_at=start)
                if row["order"] == self.bag_run and row["machine"] is not None}
        self.assertTrue(used <= {self.c1, self.c2})

    def test_nobody_crewed_holds_the_work(self):
        from django.utils import timezone

        from .dispatch import build

        CrewAssignment.objects.all().delete()
        start = timezone.make_aware(datetime.datetime.combine(TODAY, datetime.time(8)))
        held = {row["held"] for row in build(start_at=start) if row["order"] == self.bag_run}
        self.assertEqual(held, {"Nobody is crewed to run CONV."})


class CrewApiTests(CrewTestCase):
    def test_the_crew_on_a_day(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("foreman"))
        self.away(self.nights[0])
        body = client.get(f"/api/manufacturing/work-centres/{self.cutting.pk}/crew/"
                          f"?date={TODAY}").json()
        self.assertEqual((body["minutes"], [(row["shift"], row["heads"], row["machines_crewed"])
                                            for row in body["shifts"]]),
                         ("1440.00", [("D", "4", 2), ("N", "1", 0)]))
        self.assertEqual(client.get(f"/api/manufacturing/work-centres/{self.cutting.pk}/crew/"
                                    ).status_code, 400)
        rows = client.get(f"/api/manufacturing/crew-assignments/?work_centre={self.cutting.pk}"
                          ).json()
        self.assertEqual(len(rows), 6)


class TheEdgesTests(CrewTestCase):
    """Found by mutation."""

    def test_someone_who_has_left_is_not_on_the_crew(self):
        from apps.hr.models import Employee

        Employee.objects.filter(pk=self.nights[1].pk).update(
            termination_date=TODAY - datetime.timedelta(days=1))
        self.assertEqual(heads(self.cutting, self.night, TODAY), Decimal("1"))

    def test_a_stint_that_ends_before_the_next_begins_is_no_clash(self):
        CrewAssignment.objects.filter(employee=self.nights[1]).update(
            valid_from=TODAY + datetime.timedelta(days=10))
        CrewAssignment.objects.create(employee=self.nights[1], work_centre=self.cutting,
                                      shift=self.day, valid_from=TODAY,
                                      valid_to=TODAY + datetime.timedelta(days=5))
        self.assertEqual(heads(self.cutting, self.day, TODAY), Decimal("5"))
