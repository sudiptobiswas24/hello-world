"""
The loom exit station, weighing rolls off contractor-run looms.

The arithmetic throughout: the specification is 87 GSM, 60 cm lay-flat
and tubular, so a metre of it is 0.6 x 2 = 1.2 square metres and weighs
104.4 grammes. A roll of 104.4 kg net supports exactly 1,000 metres;
on a 2.4 kg core it reads 106.8 kg gross.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.hr.models import Employee, EmploymentStatus

from .machines import Machine
from .orders import WorkCentre, WorkOrder
from .routing import Routing, RoutingOperation
from .shifts import Shift
from .station import CoreType, LoomStation, record_roll, roll_code, run_on
from .tests_orders import TODAY
from .tests_rolls import RollTestCase


def at(day, hour, minute=0):
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour, minute)))


class StationTestCase(RollTestCase):
    def setUp(self):
        super().setUp()
        self.spec = self.specification()
        self.centre = WorkCentre.objects.create(code="WEAVE", name="Weaving")
        self.contractor = Party.objects.create(code="CON-A", name="Contractor A")
        self.l17, self.l20 = (
            Machine.objects.create(work_centre=self.centre, code=code,
                                   contractor=self.contractor)
            for code in ("L-17", "L-20")
        )
        routing = Routing.objects.create(code="R-WEAVE", name="Weave")
        RoutingOperation.objects.create(
            routing=routing, sequence=10, name="Weave", work_centre=self.centre,
            setup_minutes=Decimal("0"), units_per_hour=Decimal("40"), rate_uom=self.kg,
        )
        self.spec.routing = routing
        self.spec.save()
        self.stock(self.spec.warp_tape.tape_item, "5000", "120")
        self.run = self.released_run()

        self.day = Shift.objects.create(code="D", name="Day", starts_at=datetime.time(8),
                                        hours=Decimal("12"))
        self.night = Shift.objects.create(code="N", name="Night",
                                          starts_at=datetime.time(20), hours=Decimal("12"))
        self.core = CoreType.objects.create(code="C-76", tare_kg=Decimal("2.4"))
        self.station = LoomStation.objects.create(code="LX-1", name="Loom exit 1",
                                                  warehouse=self.plant)
        self.station.machines.set([self.l17, self.l20])
        self.operator = self.employee("EMP-0142", "Operator")
        self.supervisor = self.employee("EMP-0087", "Supervisor")
        self.station.supervisors.add(self.supervisor)

    def released_run(self, quantity="5000"):
        order = WorkOrder.objects.create(
            item=self.fabric, bom=self.spec.bom, quantity_ordered=Decimal(quantity),
            uom=self.kg, warehouse=self.plant,
        )
        order.release(TODAY)
        return order

    def employee(self, number, name, **extra):
        party = Party.objects.create(code=number, name=name)
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
        return Employee.objects.create(party=party, employee_number=number,
                                       hire_date=datetime.date(2020, 1, 1), **extra)

    def weigh(self, gross="106.8", declared="1006", machine=None, when=None, **extra):
        return record_roll(
            self.station, extra.pop("operator", self.operator), machine or self.l17,
            Decimal(gross), self.core, Decimal(declared),
            at=when or at(TODAY, 10, 42), **extra,
        )


class ARollOffTheLoomTests(StationTestCase):
    def test_within_tolerance_it_becomes_stock_and_names_who_weighed_it(self):
        roll = self.weigh()

        self.assertEqual(roll.lot.code, "FR-260601-D-L17-01")
        self.assertEqual(roll.net_weight_kg, Decimal("104.4"))
        self.assertEqual(roll.core_weight_kg, Decimal("2.4"))
        self.assertEqual(roll.metres_from_weight, Decimal("1000.00"))
        self.assertEqual(roll.metres_variance_percent, Decimal("0.60"))
        self.assertFalse(roll.is_metres_exception)
        self.assertEqual((roll.weighed_by, roll.weight_source, roll.shift_date),
                         (self.operator, "scale", TODAY))
        self.assertTrue(roll.entry.posted)
        self.assertEqual(roll.entry.work_order, self.run)
        self.assertEqual(self.fabric.on_hand_at(self.plant), Decimal("104.4"))

    def test_over_tolerance_it_is_still_stock_and_an_exception(self):
        roll = self.weigh(declared="1062")
        self.assertEqual(roll.metres_variance_percent, Decimal("6.20"))
        self.assertTrue(roll.is_metres_exception)
        self.assertEqual(self.fabric.on_hand_at(self.plant), Decimal("104.4"))

    def test_under_is_an_exception_too(self):
        self.assertTrue(self.weigh(declared="970").is_metres_exception)

    def test_exactly_at_the_limit_is_within_it(self):
        self.assertFalse(self.weigh(declared="1020").is_metres_exception)
        self.assertTrue(self.weigh(declared="1020.1").is_metres_exception)

    def test_flat_cloth_is_one_layer(self):
        """The same 104.4 kg of flat cloth is twice the metres."""
        type(self.spec).objects.filter(pk=self.spec.pk).update(weave="flat")
        roll = self.weigh(declared="2000")
        self.assertFalse(roll.is_tubular)
        self.assertEqual(roll.metres_from_weight, Decimal("2000.00"))

    def test_the_tolerance_it_was_held_to_is_kept(self):
        roll = self.weigh(declared="1015")
        LoomStation.objects.filter(pk=self.station.pk).update(
            metres_tolerance_percent=Decimal("1"))
        roll.refresh_from_db()
        self.assertEqual(roll.metres_tolerance_percent, Decimal("2.00"))
        self.assertFalse(roll.is_metres_exception)

    def test_rolls_are_numbered_per_loom_per_shift(self):
        self.weigh()
        second = self.weigh(when=at(TODAY, 11, 50))
        other_loom = self.weigh(machine=self.l20)
        self.assertEqual(second.lot.code, "FR-260601-D-L17-02")
        self.assertEqual(other_loom.lot.code, "FR-260601-D-L20-01")

    def test_the_night_counts_its_own_rolls(self):
        self.weigh()
        roll = self.weigh(when=at(TODAY, 21, 5))
        self.assertEqual(roll.lot.code, "FR-260601-N-L17-01")

    def test_two_in_the_morning_is_last_nights_shift(self):
        roll = self.weigh(when=at(TODAY + datetime.timedelta(days=1), 2, 14))
        self.assertEqual(roll.shift, self.night)
        self.assertEqual(roll.shift_date, TODAY)
        self.assertEqual(roll.lot.code, "FR-260601-N-L17-01")


class WhatIsRefusedTests(StationTestCase):
    def test_a_core_heavier_than_the_roll(self):
        with self.assertRaisesMessage(ValidationError, "not more than"):
            self.weigh(gross="2.4")

    def test_no_declared_metres(self):
        with self.assertRaisesMessage(ValidationError, "loom counter"):
            self.weigh(declared="0")

    def test_nobody_signed_in(self):
        with self.assertRaisesMessage(ValidationError, "Nobody is signed in"):
            self.weigh(operator=None)

    def test_a_loom_the_station_does_not_serve(self):
        l30 = Machine.objects.create(work_centre=self.centre, code="L-30")
        with self.assertRaisesMessage(ValidationError, "does not weigh for L-30"):
            self.weigh(machine=l30)

    def test_a_station_out_of_use(self):
        LoomStation.objects.filter(pk=self.station.pk).update(is_active=False)
        self.station.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "not in use"):
            self.weigh()

    def test_no_shift_at_that_hour(self):
        Shift.objects.filter(pk=self.night.pk).update(is_active=False)
        with self.assertRaisesMessage(ValidationError, "No shift runs"):
            self.weigh(when=at(TODAY, 22))

    def test_no_specification_in_force(self):
        type(self.spec).objects.filter(pk=self.spec.pk).update(
            valid_to=TODAY - datetime.timedelta(days=1))
        with self.assertRaisesMessage(ValidationError, "No fabric specification"):
            self.weigh()

    def test_nothing_is_left_behind_when_it_is_refused(self):
        type(self.spec).objects.filter(pk=self.spec.pk).update(
            valid_to=TODAY - datetime.timedelta(days=1))
        with self.assertRaises(ValidationError):
            self.weigh()
        self.assertEqual(self.fabric.lots.count(), 0)


class WhichRunTests(StationTestCase):
    def test_no_released_run(self):
        self.run.cancel()
        with self.assertRaisesMessage(ValidationError, "No released run is on L-17"):
            self.weigh()

    def test_two_runs_are_refused_not_guessed(self):
        other = self.released_run("1000")
        with self.assertRaisesMessage(ValidationError, "is on 2 released runs"):
            run_on(self.l17)
        self.assertIn(other.number, str(self._refusal()))

    def _refusal(self):
        try:
            run_on(self.l17)
        except ValidationError as error:
            return error

    def test_a_run_that_names_the_loom_wins(self):
        other = self.released_run("1000")
        other.operations.update(machine=self.l17)
        self.assertEqual(run_on(self.l17), other)
        self.assertEqual(run_on(self.l20), self.run)


class WhoMayWeighTests(StationTestCase):
    def test_not_somebody_who_has_left(self):
        Employee.objects.filter(pk=self.operator.pk).update(
            termination_date=TODAY - datetime.timedelta(days=1))
        self.operator.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "does not work here"):
            self.weigh()

    def test_nor_approved_by_one(self):
        Employee.objects.filter(pk=self.supervisor.pk).update(
            employment_status=EmploymentStatus.TERMINATED)
        self.supervisor.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "not approving"):
            self.weigh(source="manual", supervisor=self.supervisor, reason="scale_offline")


class ATypedWeightTests(StationTestCase):
    def manual(self, **extra):
        extra.setdefault("supervisor", self.supervisor)
        extra.setdefault("reason", "scale_offline")
        return self.weigh(source="manual", **extra)

    def test_approved_by_a_supervisor_with_a_reason(self):
        roll = self.manual()
        self.assertEqual((roll.weight_source, roll.approved_by, roll.override_reason),
                         ("manual", self.supervisor, "scale_offline"))

    def test_needs_a_supervisor(self):
        with self.assertRaisesMessage(ValidationError, "supervisor's PIN"):
            self.manual(supervisor=None)

    def test_not_the_operator_approving_their_own(self):
        self.station.supervisors.add(self.operator)
        with self.assertRaisesMessage(ValidationError, "somebody else"):
            self.manual(supervisor=self.operator)

    def test_only_a_supervisor_of_this_station(self):
        stranger = self.employee("EMP-0999", "Someone")
        with self.assertRaisesMessage(ValidationError, "does not approve weights"):
            self.manual(supervisor=stranger)

    def test_needs_a_reason(self):
        with self.assertRaisesMessage(ValidationError, "reason the scale"):
            self.manual(reason="")

    def test_other_needs_saying(self):
        with self.assertRaisesMessage(ValidationError, "other reason"):
            self.manual(reason="other", note="  ")
        self.assertEqual(self.manual(reason="other", note="Hook broken").override_note,
                         "Hook broken")

    def test_a_scale_weight_names_no_approver_whoever_is_passed(self):
        roll = self.weigh(supervisor=self.supervisor, reason="scale_offline", note="x")
        self.assertEqual((roll.approved_by, roll.override_reason, roll.override_note),
                         (None, "", ""))

    def test_an_unknown_source(self):
        with self.assertRaisesMessage(ValidationError, "not a weight source"):
            self.weigh(source="guess")

    def test_the_database_refuses_an_unapproved_manual_roll_too(self):
        roll = self.weigh()
        with self.assertRaises(IntegrityError), transaction.atomic():
            type(roll).objects.filter(pk=roll.pk).update(weight_source="manual")


class PinTests(StationTestCase):
    def test_a_pin_is_issued_and_finds_its_person(self):
        pin = self.operator.issue_pin()
        self.assertRegex(pin, r"^\d{6}$")
        self.assertNotIn(pin, self.operator.pin_digest)
        self.assertEqual(Employee.by_pin(pin), self.operator)

    def test_a_revoked_pin_finds_nobody(self):
        pin = self.operator.issue_pin()
        self.operator.revoke_pin()
        self.assertIsNone(Employee.by_pin(pin))

    def test_someone_who_has_left_is_not_let_in(self):
        pin = self.operator.issue_pin()
        Employee.objects.filter(pk=self.operator.pk).update(
            termination_date=datetime.date(2026, 1, 31))
        self.assertIsNone(Employee.by_pin(pin, on_date=datetime.date(2026, 6, 1)))
        Employee.objects.filter(pk=self.operator.pk).update(
            termination_date=None, employment_status=EmploymentStatus.TERMINATED)
        self.assertIsNone(Employee.by_pin(pin))

    def test_nonsense_finds_nobody(self):
        self.operator.issue_pin()
        for pin in ("", None, "12ab", " "):
            self.assertIsNone(Employee.by_pin(pin))

    def test_a_pin_of_one_digit_repeated_is_never_issued(self):
        from unittest.mock import patch

        with patch("apps.hr.models.secrets.randbelow", side_effect=[111111, 482910]):
            self.assertEqual(self.operator.issue_pin(), "482910")

    def test_a_pin_somebody_holds_is_never_issued_again(self):
        from unittest.mock import patch

        with patch("apps.hr.models.secrets.randbelow", side_effect=[482910]):
            self.operator.issue_pin()
        with patch("apps.hr.models.secrets.randbelow", side_effect=[482910, 305117]):
            self.assertEqual(self.supervisor.issue_pin(), "305117")

    def test_two_people_never_share_one(self):
        pins = {self.employee(f"E-{n}", f"P{n}").issue_pin() for n in range(30)}
        self.assertEqual(len(pins), 30)


class StationLockTests(StationTestCase):
    def test_the_right_pin_signs_in(self):
        pin = self.operator.issue_pin()
        self.assertEqual(self.station.identify(pin), self.operator)

    def test_five_wrong_lock_the_station(self):
        pin = self.operator.issue_pin()
        now = timezone.now()
        for _ in range(5):
            with self.assertRaisesMessage(ValidationError, "not recognised"):
                self.station.identify("000000" if pin != "000000" else "111111", now=now)
        with self.assertRaisesMessage(ValidationError, "locked"):
            self.station.identify(pin, now=now)

    def test_four_wrong_then_right_resets_the_count(self):
        pin = self.operator.issue_pin()
        wrong = "999999" if pin != "999999" else "888888"
        now = timezone.now()
        for _ in range(4):
            with self.assertRaises(ValidationError):
                self.station.identify(wrong, now=now)
        self.station.identify(pin, now=now)
        for _ in range(4):
            with self.assertRaises(ValidationError):
                self.station.identify(wrong, now=now)
        self.assertEqual(self.station.identify(pin, now=now), self.operator)

    def test_a_success_and_failures_on_one_clock_reading_still_lock(self):
        """Ordered by time alone, an earlier success could sort after the
        failures that share its timestamp and reset the count."""
        pin = self.operator.issue_pin()
        wrong = "999999" if pin != "999999" else "888888"
        now = timezone.now()
        self.station.identify(pin, now=now)
        for _ in range(5):
            with self.assertRaises(ValidationError):
                self.station.identify(wrong, now=now)
        self.assertTrue(self.station.is_locked(now))

    def test_the_lock_lifts_after_ten_minutes(self):
        pin = self.operator.issue_pin()
        wrong = "999999" if pin != "999999" else "888888"
        now = timezone.now()
        for _ in range(5):
            with self.assertRaises(ValidationError):
                self.station.identify(wrong, now=now)
        later = now + datetime.timedelta(minutes=11)
        self.assertEqual(self.station.identify(pin, now=later), self.operator)


class RollCodeTests(StationTestCase):
    def test_the_code_reads_day_shift_loom_and_count(self):
        self.assertEqual(roll_code(self.l20, self.night, datetime.date(2026, 10, 6)),
                         "FR-261006-N-L20-01")
