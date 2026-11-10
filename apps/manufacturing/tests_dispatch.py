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

    def test_on_a_machine_now_out_of_use_it_is_held_not_moved(self):
        b = self.run_of("600", D2)
        operation = b.operations.get()
        operation.machine = self.l2
        operation.save()
        TimeBooking.objects.create(work_order=b, operation=operation, booking_date=TODAY,
                                   minutes=Decimal("100")).post()
        Machine.objects.filter(pk=self.l2.pk).update(is_active=False)
        rows = self.schedule()
        row = self.row(rows, b)
        self.assertEqual((row["machine"], row["start"], row["late"]), (self.l2, None, None))
        self.assertIn("L-2, which is out of use", row["held"])
        self.assertEqual(dispatch_list(rows)["held"], [row])
        self.assertEqual(commit(rows), 0)
        operation.refresh_from_db()
        self.assertEqual((operation.machine, operation.planned_start), (self.l2, None))

    def test_a_pin_that_never_started_moves_and_says_so(self):
        b = self.run_of("600", D2)
        operation = b.operations.get()
        operation.machine = self.l2
        operation.save()
        Machine.objects.filter(pk=self.l2.pk).update(is_active=False)
        row = self.row(self.schedule(), b)
        self.assertEqual((row["machine"], row["moved_from"], row["held"]), (self.l1, "L-2", None))

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

    def test_the_steps_after_a_held_one_wait_on_the_board(self):
        b = self.run_of("600", D2)
        weave = b.operations.get(sequence=10)
        weave.machine = self.l1
        weave.save()
        TimeBooking.objects.create(work_order=b, operation=weave, booking_date=TODAY,
                                   minutes=Decimal("100")).post()
        Machine.objects.filter(pk=self.l1.pk).update(is_active=False)
        rows = self.schedule()
        stitch, coat = self.row(rows, b, 20), self.row(rows, b, 30)
        self.assertEqual((stitch["held"], coat["held"]),
                         ("Waits for step 10, which is held.",) * 2)
        self.assertEqual(len(dispatch_list(rows)["held"]), 3)

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


class AnOperationHoldsItsRunTests(DispatchTestCase):
    """
    O254: an operation's save decided on its run's status as the object in hand had it. A run closed by another
    transaction meanwhile still read released, and a loom change landed on a closed run. Held and read again,
    it is refused.
    """

    def test_a_loom_change_on_a_run_closed_meanwhile_is_refused(self):
        from django.core.exceptions import ValidationError

        from .orders import WorkOrder, WorkOrderStatus

        b = self.run_of("600", D2)
        operation = b.operations.select_related("work_order").get()
        WorkOrder.objects.filter(pk=b.pk).update(status=WorkOrderStatus.CLOSED)
        operation.machine = self.l2
        with self.assertRaisesMessage(ValidationError, "its routing was frozen when it was released"):
            operation.save()
        operation.refresh_from_db()
        self.assertNotEqual(operation.machine_id, self.l2.pk)


class ALoomSlowerThanItsBankTests(DispatchTestCase):
    """
    O481: a machine's own rate. The bank runs 60 kg an hour; L-1 states
    nothing and is the bank's speed, L-2 states 45, three quarters of it.
    A run of n kg is 30 + n minutes on L-1 and 30 + 4n/3 on L-2: the setup
    is a setup on either. Worked in calc_g35_c/dispatch_on_the_machine.py:

        B  600 kg, due the 2nd   L-1  08:00 -> 18:30        (L-2: 21:50)
        A 1200 kg, due the 3rd   L-2  08:00 -> 11:10 on 2nd (L-1: 15:00 on 2nd)
        C  600 kg, due the 4th   L-1  18:30 -> 05:00 on 2nd

    Before the fix L-2 was planned at the bank's speed and A came off at
    04:30 on the 2nd, six hours and forty minutes before it could.
    """

    def setUp(self):
        super().setUp()
        Machine.objects.filter(pk=self.l2.pk).update(capacity_per_hour=Decimal("45"),
                                                     capacity_uom=self.kg)
        self.a = self.run_of("1200", D3)
        self.b = self.run_of("600", D2)
        self.c = self.run_of("600", D4)

    def board(self):
        rows = self.schedule()
        return {order.number: (self.row(rows, order)["resource"],
                               timezone.make_naive(self.row(rows, order)["start"]),
                               timezone.make_naive(self.row(rows, order)["finish"]))
                for order in (self.a, self.b, self.c)}

    def expected(self):
        return {
            self.b.number: ("L-1", at(D1, 8), at(D1, 18, 30)),
            self.a.number: ("L-2", at(D1, 8), at(D2, 11, 10)),
            self.c.number: ("L-1", at(D1, 18, 30), at(D2, 5)),
        }

    def test_the_slower_loom_takes_longer(self):
        self.assertEqual(self.board(), self.expected())

    def test_a_rate_in_another_unit_is_read_in_the_banks(self):
        """45,000 g an hour is 45 kg an hour: the same board."""
        from apps.core.models import UnitOfMeasure, UnitOfMeasureCategory

        gramme = UnitOfMeasure.objects.create(code="g", name="Gramme",
                                              category=UnitOfMeasureCategory.WEIGHT,
                                              base_unit=self.kg, conversion_factor=Decimal("0.001"))
        Machine.objects.filter(pk=self.l2.pk).update(capacity_per_hour=Decimal("45000"),
                                                     capacity_uom=gramme)
        self.assertEqual(self.board(), self.expected())

    def test_a_machine_with_no_rate_runs_at_the_banks(self):
        """Neither states its own: both at 30 + n, A on L-2 by 04:30 on the 2nd as before."""
        Machine.objects.filter(pk=self.l2.pk).update(capacity_per_hour=None, capacity_uom=None)
        self.assertEqual(self.board()[self.a.number], ("L-2", at(D1, 8), at(D2, 4, 30)))

    def test_a_bank_with_no_rate_leaves_the_operations_own(self):
        """Nothing says how much slower L-2 is than an unrated bank: the routing's 60 kg stands."""
        WorkCentre.objects.filter(pk=self.bank.pk).update(capacity_per_hour=None, capacity_uom=None)
        self.assertEqual(self.board()[self.a.number], ("L-2", at(D1, 8), at(D2, 4, 30)))

    def test_a_rate_that_counts_something_else_is_refused_not_guessed(self):
        """L-2 rated in pieces against a bank in kg: off the board, so everything goes on L-1."""
        from apps.core.models import UnitOfMeasure, UnitOfMeasureCategory

        piece = UnitOfMeasure.objects.create(code="pc", name="Piece",
                                             category=UnitOfMeasureCategory.COUNT)
        Machine.objects.filter(pk=self.l2.pk).update(capacity_per_hour=Decimal("45"),
                                                     capacity_uom=piece)
        board = self.board()
        self.assertEqual({machine for machine, _, _ in board.values()}, {"L-1"})
        Machine.objects.filter(pk=self.l1.pk).update(is_active=False)
        row = self.row(self.schedule(), self.b)
        self.assertIsNone(row["finish"])
        self.assertIn("L-2 is rated in pc an hour and WEAVE in kg", row["held"])

    def test_begun_on_the_slow_loom_it_finishes_at_its_speed(self):
        """830 minutes on L-2, 100 booked: 730 more from 08:00 is 20:10, not 16:50."""
        operation = self.b.operations.get()
        operation.machine = self.l2
        operation.save()
        TimeBooking.objects.create(work_order=self.b, operation=operation, booking_date=TODAY,
                                   minutes=Decimal("100")).post()
        row = self.row(self.schedule(), self.b)
        self.assertEqual((row["machine"], timezone.make_naive(row["finish"])),
                         (self.l2, at(D1, 20, 10)))

    def test_a_machine_at_nought_is_refused_when_it_is_rated(self):
        """
        Refused, not fallen back: a loom that makes nothing an hour never
        finishes, and reading it as the bank's speed would plan work onto
        it. The database refuses it, and the API says so beside the field.
        """
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            Machine.objects.filter(pk=self.l2.pk).update(capacity_per_hour=Decimal("0"))
        from django.core.management import call_command

        call_command("setup_roles", verbosity=0)
        from django.contrib.auth.models import Group

        engineer = User.objects.create_user("engineer")
        engineer.groups.add(Group.objects.get(name="Process Engineer"))
        client = APIClient()
        client.force_authenticate(engineer)
        response = client.patch(f"/api/manufacturing/machines/{self.l2.pk}/",
                                {"capacity_per_hour": "0"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.l2.refresh_from_db()
        self.assertEqual(self.l2.capacity_per_hour, Decimal("45"))

    def test_the_planner_sees_and_commits_it_through_the_api(self):
        from unittest.mock import patch

        from django.contrib.auth.models import Group
        from django.core.management import call_command

        call_command("setup_roles", verbosity=0)
        planner = User.objects.create_user("planner")
        planner.groups.add(Group.objects.get(name="Production Planner"))
        client = APIClient()
        client.force_authenticate(planner)
        with patch("apps.manufacturing.dispatch.timezone.now", return_value=aware(D1, 8)):
            board = client.get("/api/manufacturing/dispatch/").json()
            self.assertEqual([(row["run"], row["finish"][:16]) for row in board["L-2"]],
                             [(self.a.number, "2026-06-02T11:10")])
            self.assertEqual(client.post("/api/manufacturing/dispatch/commit/").json(),
                             {"committed": 3})
        operation = self.a.operations.get()
        self.assertEqual((operation.machine, timezone.make_naive(operation.planned_finish)),
                         (self.l2, at(D2, 11, 10)))

    def test_the_capacity_report_counts_a_run_on_the_slow_loom_at_its_speed(self):
        """
        In minutes of the bank's work (O521). A on L-2 holds it 30 + 1600 = 1630
        minutes of its clock, which at 45 of 60 is 1222.5 of the bank's; B and C on
        no machine, 630 each: 2482.5. The bank offers 1,440 + 1,080 a day, 10,080
        over four days, not 11,520.
        """
        from .routing import capacity_report

        operation = self.a.operations.get()
        operation.machine = self.l2
        operation.save()
        report = capacity_report(self.bank, D1, D4)
        self.assertEqual(report["load_minutes"] + report["unscheduled_minutes"], Decimal("2482.5"))
        self.assertEqual(report["available_minutes"], Decimal("10080"))

    def test_a_bank_offers_each_machine_at_its_own_speed(self):
        """O521: L-1 at the bank's 60 and L-2 at 45, 24 hours each: 2,520 minutes a day, not 2,880."""
        from apps.planning.capacity import LoadBook

        self.assertEqual(self.bank.minutes_on(D1), Decimal("2520"))
        book = LoadBook(self.order("1").warehouse, D1, D4)
        self.assertEqual(book.capacity_minutes(self.bank, D1), Decimal("2520"))
        self.l2.refresh_from_db()
        self.assertEqual(book.free(self.bank, D1, pool=(self.l2,)), Decimal("1080"))


class ARunEarnsItsMachinesMinutesTests(DispatchTestCase):
    """
    O522: B, 600 kg, put on L-2 at 45 kg an hour in a bank of 60, takes 30 + 800 = 830
    minutes there, recorded as it is put there. Its time variance earns those, not the
    bank's 630, and its traveller prints them; a re-rating afterwards changes neither.
    """

    def setUp(self):
        super().setUp()
        Machine.objects.filter(pk=self.l2.pk).update(capacity_per_hour=Decimal("45"),
                                                     capacity_uom=self.kg)
        self.l2.refresh_from_db()
        self.b = self.run_of("600", D2)
        self.operation = self.b.operations.get()

    def test_put_on_the_slow_loom_it_records_its_minutes_there(self):
        self.assertIsNone(self.operation.machine_minutes)
        self.operation.machine = self.l2
        self.operation.save()
        self.operation.refresh_from_db()
        self.assertEqual(self.operation.machine_minutes, Decimal("830.00"))
        # Re-rated afterwards: what was recorded stands.
        Machine.objects.filter(pk=self.l2.pk).update(capacity_per_hour=Decimal("30"))
        self.operation.name = self.operation.name
        self.operation.save()
        self.operation.refresh_from_db()
        self.assertEqual(self.operation.machine_minutes, Decimal("830.00"))
        # Moved to L-1, the bank's speed: the planned minutes stand again.
        self.operation.machine = self.l1
        self.operation.save()
        self.operation.refresh_from_db()
        self.assertIsNone(self.operation.machine_minutes)
        self.assertEqual(self.operation.minutes_as_assigned(), Decimal("630.00"))

    def test_the_dispatchers_commit_records_them(self):
        Machine.objects.filter(pk=self.l1.pk).update(is_active=False)
        commit(self.schedule())
        self.operation.refresh_from_db()
        self.assertEqual((self.operation.machine, self.operation.machine_minutes),
                         (self.l2, Decimal("830.00")))

    def test_made_to_its_machines_speed_it_took_no_longer_than_it_earned(self):
        """830 minutes booked for the whole run on L-2: no variance, where it read 200 adverse."""
        self.operation.machine = self.l2
        self.operation.save()
        TimeBooking.objects.create(work_order=self.b, operation=self.operation, booking_date=TODAY,
                                   minutes=Decimal("830")).post()
        self.full_issue(self.b)
        self.produce(self.b, str(self.b.started_quantity())).post()
        self.assertEqual(self.b.time_variance_minutes(), Decimal("0"))

    def test_the_traveller_prints_the_minutes_on_its_machine(self):
        from .traveller import traveller

        self.operation.machine = self.l2
        self.operation.save()
        step = traveller(self.b)["steps"][0]
        self.assertEqual((step["machine"], step["planned_minutes"]), ("L-2", "830"))


class PerformanceAgainstTheMachinesOwnRateTests(DispatchTestCase):
    """
    O523: a booking on L-2, rated 45 kg an hour in a bank of 60, that made 45 kg in
    its hour ran flat out. Its ideal minutes, recorded as it posts, are 45 / 45 x 60 =
    60: performance 1, where at the operation's 60 kg it read 45 / 60 = 0.75.
    """

    def setUp(self):
        super().setUp()
        Machine.objects.filter(pk=self.l2.pk).update(capacity_per_hour=Decimal("45"),
                                                     capacity_uom=self.kg)
        self.l2.refresh_from_db()
        self.b = self.run_of("600", D2)
        self.operation = self.b.operations.get()

    def book(self, machine, minutes="60", made="45"):
        booking = TimeBooking.objects.create(work_order=self.b, operation=self.operation,
                                             booking_date=TODAY, machine=machine,
                                             minutes=Decimal(minutes),
                                             quantity_completed=Decimal(made))
        booking.post()
        return booking

    def test_flat_out_on_the_slow_loom_is_full_performance(self):
        from . import oee

        booking = self.book(self.l2)
        self.assertEqual(booking.ideal_minutes, Decimal("60.0000"))
        result = oee.performance(self.bank, TODAY, TODAY, machine=self.l2)
        self.assertEqual(result["ratio"], Decimal("1"))

    def test_recorded_at_the_booking_not_read_again(self):
        from . import oee

        self.book(self.l2)
        Machine.objects.filter(pk=self.l2.pk).update(capacity_per_hour=Decimal("60"))
        self.assertEqual(oee.performance(self.bank, TODAY, TODAY, machine=self.l2)["ratio"],
                         Decimal("1"))

    def test_on_the_banks_speed_it_is_the_operations_rate(self):
        """L-1 states no rate: 45 kg in an hour is 45 minutes' work, 0.75."""
        from . import oee

        booking = self.book(self.l1)
        self.assertEqual(booking.ideal_minutes, Decimal("45.0000"))
        self.assertEqual(oee.performance(self.bank, TODAY, TODAY, machine=self.l1)["ratio"],
                         Decimal("0.75"))
