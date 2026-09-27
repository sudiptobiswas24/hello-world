"""
The detailed schedule, worked by hand.

A bank of two continuous looms, L-1 and L-2, at 60 kg an hour with half
an hour's setup, so a run of n kg takes 30 + n minutes. The day starts
at 08:00, when the first shift does. From 08:00 on the 1st:

    B  600 kg, due the 2nd   L-1  08:00 -> 18:30
    A 1200 kg, due the 3rd   L-2  08:00 -> 04:30 on the 2nd
    C  600 kg, due the 4th   L-1  18:30 -> 05:00 on the 2nd
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APIClient

from apps.hr.calendars import WorkingCalendar

from .changeover import SetupFamily
from .dispatch import Clock, build, commit, dispatch_list
from .machines import Machine
from .orders import TimeBooking, WorkCentre, WorkOrder
from .routing import Routing, RoutingOperation
from .shifts import Shift
from .tests_orders import TODAY, RunTestCase


def at(day, hour, minute=0):
    return datetime.datetime.combine(day, datetime.time(hour, minute))


def aware(day, hour, minute=0):
    return timezone.make_aware(at(day, hour, minute))


D1, D2, D3, D4 = (TODAY + datetime.timedelta(days=n) for n in range(4))


class DispatchTestCase(RunTestCase):
    def setUp(self):
        super().setUp()
        Shift.objects.create(code="D", name="Day", starts_at=datetime.time(8),
                             hours=Decimal("12"))
        self.bank = WorkCentre.objects.create(
            code="WEAVE", name="Weaving", capacity_per_hour=Decimal("60"),
            capacity_uom=self.kg, available_hours_per_day=Decimal("24"),
            working_days="1234567",
        )
        self.l1 = Machine.objects.create(work_centre=self.bank, code="L-1")
        self.l2 = Machine.objects.create(work_centre=self.bank, code="L-2")
        self.routing = Routing.objects.create(code="R-W", name="Weave")
        RoutingOperation.objects.create(
            routing=self.routing, sequence=10, name="Weave", work_centre=self.bank,
            setup_minutes=Decimal("30"), units_per_hour=Decimal("60"), rate_uom=self.kg,
        )
        self.bom.routing = self.routing
        self.bom.save()
        self.stock(self.virgin, "9000", "100")

    def run_of(self, quantity, due):
        order = self.order(quantity)
        WorkOrder.objects.filter(pk=order.pk).update(scheduled_end=due)
        order.refresh_from_db()
        order.release(TODAY)
        return order

    def schedule(self):
        return build(start_at=aware(D1, 8))

    def row(self, rows, order, sequence=10):
        return next(r for r in rows if r["order"] == order
                    and r["operation"].sequence == sequence)


class WhoRunsWhatTests(DispatchTestCase):
    def setUp(self):
        super().setUp()
        self.a = self.run_of("1200", D3)
        self.b = self.run_of("600", D2)
        self.c = self.run_of("600", D4)

    def test_worked_by_hand(self):
        rows = self.schedule()
        expected = {
            self.b: ("L-1", at(D1, 8), at(D1, 18, 30)),
            self.a: ("L-2", at(D1, 8), at(D2, 4, 30)),
            self.c: ("L-1", at(D1, 18, 30), at(D2, 5)),
        }
        for order, (machine, start, finish) in expected.items():
            with self.subTest(order=order.number):
                row = self.row(rows, order)
                self.assertEqual(row["machine"].code, machine)
                self.assertEqual((timezone.make_naive(row["start"]),
                                  timezone.make_naive(row["finish"])), (start, finish))

    def test_a_run_finishing_after_it_is_due_is_late(self):
        WorkOrder.objects.filter(pk=self.b.pk).update(scheduled_end=TODAY - datetime.timedelta(days=1))
        rows = self.schedule()
        self.assertTrue(self.row(rows, self.b)["late"])
        self.assertFalse(self.row(rows, self.a)["late"])

    def test_finishing_on_the_day_it_is_due_is_on_time(self):
        WorkOrder.objects.filter(pk=self.b.pk).update(scheduled_end=D1)
        self.assertFalse(self.row(self.schedule(), self.b)["late"])

    def test_one_family_after_another_changes_over_nothing(self):
        """C follows B on L-1: the same tape, so no setup, 04:30 not 05:00."""
        SetupFamily.objects.create(item=self.tape, work_centre=self.bank, family="WHITE")
        row = self.row(self.schedule(), self.c)
        self.assertEqual(row["changeover"], Decimal("0"))
        self.assertEqual(timezone.make_naive(row["finish"]), at(D2, 4, 30))

    def test_the_board_lists_each_machine_in_running_order(self):
        board = dispatch_list(self.schedule())
        self.assertEqual([r["order"] for r in board["L-1"]], [self.b, self.c])
        self.assertEqual([r["order"] for r in board["L-2"]], [self.a])

    def test_proposing_writes_nothing_and_committing_does(self):
        rows = self.schedule()
        operation = self.row(rows, self.b)["operation"]
        operation.refresh_from_db()
        self.assertIsNone(operation.planned_start)
        self.assertEqual(commit(rows), 3)
        operation.refresh_from_db()
        self.assertEqual((operation.machine, operation.planned_start),
                         (self.l1, aware(D1, 8)))

    def test_a_committed_machine_is_kept_on_the_next_build(self):
        rows = self.schedule()
        commit(rows)
        # Pinning C to L-2 by hand overrides where it would go.
        self.row(rows, self.c)["operation"].__class__.objects.filter(
            pk=self.row(rows, self.c)["operation"].pk).update(machine=self.l2)
        self.assertEqual(self.row(self.schedule(), self.c)["machine"], self.l2)


class WhatHasStartedTests(DispatchTestCase):
    def test_it_stays_on_its_machine_with_what_is_left(self):
        b = self.run_of("600", D2)
        operation = b.operations.get()
        operation.machine = self.l2
        operation.save()
        TimeBooking.objects.create(work_order=b, operation=operation, booking_date=TODAY,
                                   minutes=Decimal("100")).post()
        row = self.row(self.schedule(), b)
        self.assertEqual(row["machine"], self.l2)
        # 630 planned, 100 booked: 530 more from 08:00 is 16:50, and a
        # roll half woven pays no second changeover.
        self.assertEqual(timezone.make_naive(row["finish"]), at(D1, 16, 50))
        self.assertEqual(row["changeover"], Decimal("0"))

    def test_a_run_waits_for_its_own_start_date(self):
        b = self.run_of("600", D3)
        WorkOrder.objects.filter(pk=b.pk).update(scheduled_start=D2)
        row = self.row(self.schedule(), b)
        self.assertEqual(timezone.make_naive(row["start"]), at(D2, 8))

    def test_a_finished_step_is_not_scheduled(self):
        b = self.run_of("600", D2)
        TimeBooking.objects.create(work_order=b, operation=b.operations.get(), booking_date=TODAY,
                                   minutes=Decimal("630"), quantity_completed=Decimal("600")).post()
        self.assertEqual(self.schedule(), [])

    def test_cancelled_and_draft_runs_are_not_on_the_board(self):
        self.order("600")
        cancelled = self.run_of("600", D2)
        cancelled.cancel()
        self.assertEqual(self.schedule(), [])


class ARoutingIsAChainTests(DispatchTestCase):
    def setUp(self):
        super().setUp()
        self.stitch = WorkCentre.objects.create(
            code="STITCH", name="Stitching", capacity_per_hour=Decimal("120"),
            capacity_uom=self.kg, available_hours_per_day=Decimal("8"),
            working_days="1234567",
        )
        RoutingOperation.objects.create(
            routing=self.routing, sequence=20, name="Stitch", work_centre=self.stitch,
            setup_minutes=Decimal("0"), units_per_hour=Decimal("120"), rate_uom=self.kg,
        )
        RoutingOperation.objects.create(
            routing=self.routing, sequence=30, name="Coat", is_outside=True,
            outside_lead_days=5, outside_cost_per_unit=Decimal("1"), rate_uom=self.kg,
        )

    def test_each_step_waits_for_the_one_before(self):
        b = self.run_of("600", D2)
        rows = self.schedule()
        weave, stitch, coat = (self.row(rows, b, n) for n in (10, 20, 30))
        # Weaving ends 18:30, after the stitching line's 08:00-16:00 day,
        # so stitching starts at 08:00 on the 2nd: 300 minutes, to 13:00.
        self.assertEqual(timezone.make_naive(weave["finish"]), at(D1, 18, 30))
        self.assertEqual((timezone.make_naive(stitch["start"]), timezone.make_naive(stitch["finish"])),
                         (at(D2, 8), at(D2, 13)))
        self.assertIsNone(stitch["machine"])
        self.assertEqual(stitch["resource"], "STITCH")
        # The vendor's five days follow, on the world's clock.
        self.assertTrue(coat["outside"])
        self.assertEqual(timezone.make_naive(coat["finish"]), at(D2, 13) + datetime.timedelta(days=5))
        self.assertEqual([r["order"] for r in dispatch_list(rows)["outside"]], [b])

    def test_a_step_after_the_vendor_waits_for_the_vendor(self):
        RoutingOperation.objects.create(
            routing=self.routing, sequence=40, name="Pack", work_centre=self.stitch,
            setup_minutes=Decimal("0"), units_per_hour=Decimal("120"), rate_uom=self.kg,
        )
        b = self.run_of("600", D2)
        pack = self.row(self.schedule(), b, 40)
        # Coating is back at 13:00 on the 7th. Pack is 300 minutes: 180 to
        # the 16:00 close, then 120 from 08:00 on the 8th.
        d7, d8 = (D1 + datetime.timedelta(days=n) for n in (6, 7))
        self.assertEqual((timezone.make_naive(pack["start"]), timezone.make_naive(pack["finish"])),
                         (at(d7, 13), at(d8, 10)))

    def test_what_the_vendor_has_sent_back_in_full_is_not_scheduled(self):
        from apps.accounting.models import Account, AccountType

        from .outside import OutsideMovement

        grni = Account.objects.create(code="2150", name="GRNI",
                                      account_type=AccountType.LIABILITY)
        b = self.run_of("600", D2)
        OutsideMovement.objects.create(
            operation=b.operations.get(sequence=30), movement_date=TODAY,
            quantity=Decimal("600"), value=Decimal("600"), credit_account=grni,
        ).post()
        sequences = [r["operation"].sequence for r in self.schedule() if r["order"] == b]
        self.assertEqual(sequences, [10, 20])


class ClockTests(DispatchTestCase):
    def test_an_eight_hour_day(self):
        clock = Clock(WorkingCalendar("1234567", ""), Decimal("8"), datetime.time(8))
        self.assertEqual(clock.advance(at(D1, 15), 120), (at(D1, 15), at(D2, 9)))

    def test_a_moment_before_the_day_starts_waits_for_it(self):
        clock = Clock(WorkingCalendar("1234567", ""), Decimal("8"), datetime.time(8))
        self.assertEqual(clock.advance(at(D1, 6), 60), (at(D1, 8), at(D1, 9)))

    def test_days_off_are_skipped(self):
        # The 5th of June 2026 is a Friday; Monday to Friday only.
        clock = Clock(WorkingCalendar("12345", ""), Decimal("24"), datetime.time(8))
        friday = datetime.date(2026, 6, 5)
        began, finish = clock.advance(at(friday, 20), 1230)
        # Friday's day runs to 08:00 Saturday: 720 minutes. 510 more on Monday.
        self.assertEqual((began, finish), (at(friday, 20), at(datetime.date(2026, 6, 8), 16, 30)))

    def test_nothing_to_do_takes_no_time(self):
        clock = Clock(WorkingCalendar("1234567", ""), Decimal("8"), datetime.time(8))
        self.assertEqual(clock.advance(at(D1, 9), 0), (at(D1, 9), at(D1, 9)))


class DispatchApiTests(DispatchTestCase):
    def setUp(self):
        super().setUp()
        self.b = self.run_of("600", D2)
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_superuser("dispatcher"))

    def test_the_board_and_committing_it(self):
        from unittest.mock import patch

        with patch("apps.manufacturing.dispatch.timezone.now", return_value=aware(D1, 8)):
            board = self.client.get("/api/manufacturing/dispatch/").json()
            self.assertEqual(board["L-1"][0]["run"], self.b.number)
            self.assertEqual(board["L-1"][0]["finish"][:16], "2026-06-01T18:30")
            response = self.client.post("/api/manufacturing/dispatch/commit/")
        self.assertEqual(response.json(), {"committed": 1})
        self.assertEqual(self.b.operations.get().machine, self.l1)

    def test_committing_takes_its_permission(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_user("viewer"))
        self.assertEqual(client.post("/api/manufacturing/dispatch/commit/").status_code, 403)
