"""
What the HR stub claimed to enforce, and now does.

Every rule in this module lived in `clean()`. Django never calls
`full_clean()` on save, and every test in the original suite called it
by hand — so the tests passed and the rules did nothing. A probe that
created records the way the rest of this codebase creates them found
thirteen holes out of thirteen checks.

One test per hole, named after the fact it protects.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.core.models import Party, PartyRole, PartyRoleAssignment

from .calendars import PublicHoliday, working_days
from .models import (
    Employee,
    EmploymentStatus,
    LeaveEntitlement,
    LeavePolicy,
    LeaveRequest,
    LeaveStatus,
    LeaveType,
    leave_balance,
    leave_summary,
    leave_taken,
)


class LeaveTestCase(TestCase):
    def setUp(self):
        self.policy = LeavePolicy.objects.create(
            code="HOL", name="Annual leave", leave_type="vacation",
            annual_days=Decimal("25"),
        )
        self.sick = LeavePolicy.objects.create(
            code="SICK", name="Sick leave", leave_type="sick",
            annual_days=Decimal("10"), allows_negative=True,
        )
        self.boss = self.employee("MGR")

    def employee(self, code, hire=datetime.date(2020, 1, 1), **kwargs):
        party = Party.objects.create(code=f"P-{code}", name=code)
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
        # Everyone after the boss reports to them, unless a test says
        # otherwise: only a manager decides leave.
        if getattr(self, "boss", None) is not None:
            kwargs.setdefault("manager", self.boss)
        return Employee.objects.create(
            party=party, employee_number=code, hire_date=hire, **kwargs
        )

    # A distinct sentinel, because None is a meaningful policy: a request
    # against no allowance is a real case and the default must not swallow it.
    UNSET = object()

    def request(self, employee, start, end, policy=UNSET, **kwargs):
        return LeaveRequest.objects.create(
            employee=employee,
            policy=self.policy if policy is self.UNSET else policy,
            leave_type=kwargs.pop("leave_type", LeaveType.VACATION),
            start_date=start, end_date=end, **kwargs
        )


class RulesActuallyRunTests(LeaveTestCase):
    """The clean()-versus-save() hole, one test per rule it hid."""

    def test_a_party_without_the_employee_role_is_refused_on_create(self):
        customer = Party.objects.create(code="C-1", name="Customer")
        with self.assertRaises(ValidationError):
            Employee.objects.create(
                party=customer, employee_number="X1",
                hire_date=datetime.date(2026, 1, 1),
            )

    def test_an_employee_cannot_be_saved_as_their_own_manager(self):
        person = self.employee("E1")
        person.manager = person
        with self.assertRaises(ValidationError):
            person.save()

    def test_a_termination_date_cannot_be_saved_without_the_status(self):
        person = self.employee("E2")
        person.termination_date = datetime.date(2026, 6, 1)
        with self.assertRaises(ValidationError):
            person.save()

    def test_a_termination_date_cannot_precede_the_hire_date(self):
        person = self.employee("E3")
        person.termination_date = datetime.date(2019, 6, 1)
        person.employment_status = EmploymentStatus.TERMINATED
        with self.assertRaises(ValidationError):
            person.save()

    def test_a_management_line_cannot_loop(self):
        # The self-reference check caught only the shortest case; every
        # walk up a longer loop — an approval chain, an org chart — runs
        # forever.
        first, second = self.employee("E4"), self.employee("E5")
        first.manager = second
        first.save()
        second.manager = first
        with self.assertRaises(ValidationError) as caught:
            second.save()
        self.assertIn("cannot loop", str(caught.exception))

    def test_a_three_deep_loop_is_refused_too(self):
        a, b, c = self.employee("E6"), self.employee("E7"), self.employee("E8")
        a.manager = b
        a.save()
        b.manager = c
        b.save()
        c.manager = a
        with self.assertRaises(ValidationError):
            c.save()

    def test_a_working_pattern_must_be_readable(self):
        person = self.employee("E9")
        person.working_days = "monday"
        with self.assertRaises(ValidationError):
            person.save()


class LeaveWithinEmploymentTests(LeaveTestCase):
    def test_a_leaver_cannot_book_leave_after_they_go(self):
        person = self.employee("L1")
        person.termination_date = datetime.date(2026, 3, 1)
        person.employment_status = EmploymentStatus.TERMINATED
        person.save()
        with self.assertRaises(ValidationError) as caught:
            self.request(person, datetime.date(2026, 6, 1), datetime.date(2026, 6, 5))
        self.assertIn("not employed", str(caught.exception))

    def test_leave_cannot_start_before_the_hire_date(self):
        person = self.employee("L2", hire=datetime.date(2026, 1, 1))
        with self.assertRaises(ValidationError):
            self.request(person, datetime.date(2025, 6, 1), datetime.date(2025, 6, 5))

    def test_leave_cannot_run_past_the_last_day(self):
        person = self.employee("L3")
        person.termination_date = datetime.date(2026, 6, 3)
        person.employment_status = EmploymentStatus.TERMINATED
        person.save()
        with self.assertRaises(ValidationError) as caught:
            self.request(person, datetime.date(2026, 6, 1), datetime.date(2026, 6, 10))
        self.assertIn("past the end", str(caught.exception))

    def test_leave_cannot_end_before_it_starts_on_create(self):
        person = self.employee("L4")
        with self.assertRaises(ValidationError):
            self.request(person, datetime.date(2026, 6, 10), datetime.date(2026, 6, 1))


class NoDoubleBookingTests(LeaveTestCase):
    def test_the_same_days_cannot_be_approved_twice(self):
        person = self.employee("D1")
        first = self.request(person, datetime.date(2026, 7, 1), datetime.date(2026, 7, 10))
        first.approve(by=self.boss)
        with self.assertRaises(ValidationError) as caught:
            self.request(person, datetime.date(2026, 7, 6), datetime.date(2026, 7, 15))
        self.assertIn("twice", str(caught.exception))

    def test_a_pending_request_blocks_an_overlap_too(self):
        # Two requests for the same week sitting in a queue is the same
        # double booking, found a week later.
        person = self.employee("D2")
        self.request(person, datetime.date(2026, 7, 1), datetime.date(2026, 7, 10))
        with self.assertRaises(ValidationError):
            self.request(person, datetime.date(2026, 7, 6), datetime.date(2026, 7, 15))

    def test_a_cancelled_request_frees_its_days(self):
        person = self.employee("D3")
        first = self.request(person, datetime.date(2027, 7, 1), datetime.date(2027, 7, 9))
        first.cancel()
        second = self.request(person, datetime.date(2027, 7, 6), datetime.date(2027, 7, 15))
        self.assertEqual(second.status, LeaveStatus.PENDING)

    def test_a_rejected_request_frees_its_days(self):
        person = self.employee("D4")
        first = self.request(person, datetime.date(2026, 7, 1), datetime.date(2026, 7, 10))
        first.reject(by=self.boss)
        self.request(person, datetime.date(2026, 7, 6), datetime.date(2026, 7, 15))

    def test_two_people_may_take_the_same_week(self):
        for code in ("D5", "D6"):
            self.request(
                self.employee(code), datetime.date(2026, 7, 1), datetime.date(2026, 7, 10)
            )


class WhoDecidesTests(LeaveTestCase):
    def test_nobody_approves_their_own_leave(self):
        person = self.employee("A1")
        booking = self.request(person, datetime.date(2026, 7, 1), datetime.date(2026, 7, 3))
        with self.assertRaises(ValidationError) as caught:
            booking.approve(by=person)
        self.assertIn("own leave", str(caught.exception))

    def test_nobody_rejects_their_own_leave_either(self):
        person = self.employee("A2")
        booking = self.request(person, datetime.date(2026, 7, 1), datetime.date(2026, 7, 3))
        with self.assertRaises(ValidationError):
            booking.reject(by=person)

    def test_a_decision_needs_a_decider(self):
        person = self.employee("A3")
        booking = self.request(person, datetime.date(2026, 7, 1), datetime.date(2026, 7, 3))
        with self.assertRaises(ValidationError):
            booking.approve(by=None)


class ApprovalHasAReverseTests(LeaveTestCase):
    # A holiday still to come, whatever day the tests run on: booked for July
    # 2026, these two became a holiday taken and given back once July passed.
    NEXT_JULY = datetime.date(timezone.localdate().year + 1, 7, 1)

    def test_an_approval_can_be_sent_back(self):
        person = self.employee("W1")
        booking = self.request(person, self.NEXT_JULY, self.NEXT_JULY + datetime.timedelta(days=2))
        booking.approve(by=self.boss)
        booking.withdraw_approval(by=self.boss)
        self.assertEqual(booking.status, LeaveStatus.PENDING)
        self.assertIsNone(booking.decided_by)
        self.assertIsNone(booking.days_taken)

    def test_sending_it_back_gives_the_days_back(self):
        person = self.employee("W2")
        booking = self.request(person, self.NEXT_JULY, self.NEXT_JULY + datetime.timedelta(days=2))
        booking.approve(by=self.boss)
        booking.withdraw_approval(by=self.boss)
        booking.cancel()
        self.assertEqual(leave_taken(person, self.policy, self.NEXT_JULY.year), Decimal("0"))

    def test_a_holiday_already_taken_is_not_sent_back_to_be_cancelled(self):
        # June's five days taken; sent back and cancelled in October, they were given back.
        person = self.employee("W7")
        booking = self.request(person, datetime.date(2026, 6, 8), datetime.date(2026, 6, 12))
        booking.approve(by=self.boss)
        with self.assertRaisesMessage(ValidationError, "it has been taken and its approval is not sent back"):
            booking.withdraw_approval(by=self.boss)
        self.assertEqual(leave_taken(person, self.policy, 2026), Decimal("5.00"))

    def test_a_holiday_under_way_is_not_sent_back_but_cancelled_from_today(self):
        person = self.employee("W8")
        today = timezone.localdate()
        if (today - datetime.timedelta(days=1)).year != today.year:
            self.skipTest("On 1 January no leave of this year is yet under way.")
        booking = self.request(person, today - datetime.timedelta(days=1), today,
                               policy=self.sick, leave_type=LeaveType.SICK)
        booking.approve(by=self.boss)
        with self.assertRaisesMessage(ValidationError, "the days taken since stay taken"):
            booking.withdraw_approval(by=self.boss)

    def test_approved_leave_is_not_deleted_but_cancelled(self):
        person = self.employee("W9")
        booking = self.request(person, self.NEXT_JULY, self.NEXT_JULY + datetime.timedelta(days=2))
        booking.approve(by=self.boss)
        with self.assertRaisesMessage(ValidationError, "cancel it rather than delete it"):
            booking.delete()
        booking.cancel()
        booking.delete()
        self.assertFalse(LeaveRequest.objects.filter(pk=booking.pk).exists())

    def test_leave_is_not_handed_to_somebody_else(self):
        person, other = self.employee("W10"), self.employee("W11")
        booking = self.request(person, self.NEXT_JULY, self.NEXT_JULY + datetime.timedelta(days=2))
        booking.approve(by=self.boss)
        booking.employee = other
        with self.assertRaisesMessage(ValidationError, "is not moved to another"):
            booking.save()

    def test_an_approved_future_holiday_can_be_cancelled(self):
        # The original cancel() took only pending requests, which made an
        # approved holiday permanent: somebody who comes back early had no
        # way to return the days.
        person = self.employee("W3")
        booking = self.request(person, datetime.date(2027, 7, 1), datetime.date(2027, 7, 9))
        booking.approve(by=self.boss)
        booking.cancel()
        self.assertEqual(booking.status, LeaveStatus.CANCELLED)
        self.assertEqual(leave_taken(person, self.policy, 2027), Decimal("0"))

    def test_cancelled_while_under_way_it_keeps_the_days_already_taken(self):
        # Coming back early is what cancel() took approved requests for, and it gave back every
        # day: Monday 5 to Friday 9 July, back on the Thursday, returned all five. Cancelled on
        # its first day, a leave has taken nothing and goes whole.
        person = self.employee("W6")
        booking = self.request(person, datetime.date(2027, 7, 5), datetime.date(2027, 7, 9))
        booking.approve(by=self.boss)
        booking.cancel(on_date=datetime.date(2027, 7, 8))
        booking.refresh_from_db()
        self.assertEqual((booking.status, booking.end_date, booking.days_taken),
                         (LeaveStatus.APPROVED, datetime.date(2027, 7, 7), Decimal("3.00")))

        unstarted = self.request(person, datetime.date(2027, 7, 12), datetime.date(2027, 7, 16))
        unstarted.approve(by=self.boss)
        unstarted.cancel(on_date=datetime.date(2027, 7, 12))
        self.assertEqual((unstarted.status, leave_taken(person, self.policy, 2027)),
                         (LeaveStatus.CANCELLED, Decimal("3.00")))

    def test_leave_already_taken_cannot_be_given_back(self):
        person = self.employee("W4")
        booking = self.request(person, datetime.date(2026, 2, 2), datetime.date(2026, 2, 6))
        booking.approve(by=self.boss)
        with self.assertRaises(ValidationError) as caught:
            booking.cancel()
        self.assertIn("has been taken", str(caught.exception))

    def test_approved_dates_cannot_be_edited(self):
        person = self.employee("W5")
        booking = self.request(person, datetime.date(2027, 7, 1), datetime.date(2027, 7, 9))
        booking.approve(by=self.boss)
        booking.end_date = datetime.date(2027, 7, 16)
        with self.assertRaises(ValidationError) as caught:
            booking.save()
        self.assertIn("can no longer change", str(caught.exception))


class ApprovedLeaveOverTheApiTests(LeaveTestCase):
    def test_hr_neither_deletes_approved_leave_nor_hands_it_to_somebody_else(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("hr-admin")
        user.groups.add(Group.objects.get(name="HR Admin"))
        client = APIClient()
        client.force_authenticate(user)
        person, other = self.employee("X1"), self.employee("X2")
        start = datetime.date(timezone.localdate().year + 1, 7, 1)
        booking = self.request(person, start, start + datetime.timedelta(days=2))
        booking.approve(by=self.boss)
        moved = client.patch(f"/api/hr/leave-requests/{booking.pk}/", {"employee": other.pk}, format="json")
        deleted = client.delete(f"/api/hr/leave-requests/{booking.pk}/")
        self.assertEqual((moved.status_code, deleted.status_code), (400, 400), (moved.content, deleted.content))
        booking.refresh_from_db()
        self.assertEqual((booking.employee, booking.status), (person, LeaveStatus.APPROVED))


class WorkingDayTests(LeaveTestCase):
    def test_a_week_off_is_five_days_not_seven(self):
        person = self.employee("C1")
        booking = self.request(person, datetime.date(2026, 7, 6), datetime.date(2026, 7, 12))
        self.assertEqual(booking.days(), Decimal("5.00"))

    def test_a_public_holiday_is_not_spent(self):
        PublicHoliday.objects.create(name="Bank holiday", date=datetime.date(2026, 7, 8))
        person = self.employee("C2")
        booking = self.request(person, datetime.date(2026, 7, 6), datetime.date(2026, 7, 10))
        self.assertEqual(booking.days(), Decimal("4.00"))

    def test_a_regional_holiday_only_applies_to_its_region(self):
        PublicHoliday.objects.create(
            name="Regional", date=datetime.date(2026, 7, 8), region="SCT"
        )
        here = self.employee("C3")
        there = self.employee("C4", holiday_region="SCT")
        span = (datetime.date(2026, 7, 6), datetime.date(2026, 7, 10))
        self.assertEqual(self.request(here, *span).days(), Decimal("5.00"))
        self.assertEqual(self.request(there, *span).days(), Decimal("4.00"))

    def test_a_company_wide_holiday_applies_to_a_region_as_well(self):
        # A regional employee gets their own set plus the company's, never
        # only one of the two.
        PublicHoliday.objects.create(name="Everyone", date=datetime.date(2026, 7, 8))
        PublicHoliday.objects.create(
            name="Regional", date=datetime.date(2026, 7, 9), region="SCT"
        )
        person = self.employee("C5", holiday_region="SCT")
        booking = self.request(person, datetime.date(2026, 7, 6), datetime.date(2026, 7, 10))
        self.assertEqual(booking.days(), Decimal("3.00"))

    def test_a_part_timer_is_not_charged_for_days_they_do_not_work(self):
        person = self.employee("C6", working_days="1234")
        booking = self.request(person, datetime.date(2026, 7, 6), datetime.date(2026, 7, 10))
        self.assertEqual(booking.days(), Decimal("4.00"))

    def test_a_holiday_on_a_day_off_gives_a_part_timer_nothing_back(self):
        PublicHoliday.objects.create(name="Friday off", date=datetime.date(2026, 7, 10))
        person = self.employee("C7", working_days="1234")
        booking = self.request(person, datetime.date(2026, 7, 6), datetime.date(2026, 7, 10))
        self.assertEqual(booking.days(), Decimal("4.00"))

    def test_a_half_day_is_half_a_day(self):
        person = self.employee("C8")
        booking = self.request(
            person, datetime.date(2026, 7, 6), datetime.date(2026, 7, 6), half_day=True
        )
        self.assertEqual(booking.days(), Decimal("0.50"))

    def test_a_half_day_must_be_one_day(self):
        person = self.employee("C9")
        with self.assertRaises(ValidationError):
            self.request(
                person, datetime.date(2026, 7, 6), datetime.date(2026, 7, 8), half_day=True
            )

    def test_what_an_approved_holiday_cost_is_frozen(self):
        # The working pattern and the holiday calendar both change; what
        # an approved holiday cost does not.
        person = self.employee("C10")
        booking = self.request(person, datetime.date(2027, 7, 5), datetime.date(2027, 7, 9))
        booking.approve(by=self.boss)
        self.assertEqual(booking.days(), Decimal("5.00"))
        PublicHoliday.objects.create(name="Added later", date=datetime.date(2027, 7, 7))
        self.assertEqual(booking.days(), Decimal("5.00"))
        self.assertEqual(booking.compute_days(), Decimal("4.00"))


class BalanceTests(LeaveTestCase):
    def test_a_full_year_grants_the_policy(self):
        person = self.employee("B1")
        self.assertEqual(self.policy.entitlement_for(person, 2026), Decimal("25.00"))

    def test_a_mid_year_joiner_is_prorated(self):
        person = self.employee("B2", hire=datetime.date(2026, 7, 1))
        # 184 days of 365.
        self.assertEqual(self.policy.entitlement_for(person, 2026), Decimal("12.60"))

    def test_a_leaver_is_prorated_too(self):
        person = self.employee("B3")
        person.termination_date = datetime.date(2026, 6, 30)
        person.employment_status = EmploymentStatus.TERMINATED
        person.save()
        self.assertEqual(self.policy.entitlement_for(person, 2026), Decimal("12.40"))

    def test_an_individual_arrangement_overrides_the_policy(self):
        person = self.employee("B4")
        LeaveEntitlement.objects.create(
            employee=person, policy=self.policy, year=2026, days=Decimal("30")
        )
        self.assertEqual(self.policy.entitlement_for(person, 2026), Decimal("30.00"))

    def test_carried_over_days_are_added(self):
        person = self.employee("B5")
        LeaveEntitlement.objects.create(
            employee=person, policy=self.policy, year=2026,
            days=Decimal("25"), carried_over=Decimal("3"),
        )
        self.assertEqual(self.policy.entitlement_for(person, 2026), Decimal("28.00"))

    def test_monthly_accrual_earns_it_as_the_year_passes(self):
        policy = LeavePolicy.objects.create(
            code="ACC", name="Accrued", leave_type="other",
            annual_days=Decimal("24"), accrues_monthly=True,
        )
        person = self.employee("B6")
        # Six months complete out of twelve, so half the allowance.
        earned = policy.entitlement_for(person, 2026, as_of=datetime.date(2026, 6, 30))
        self.assertEqual(earned, Decimal("12.00"))

    def test_monthly_accrual_counts_a_month_once_it_ends(self):
        policy = LeavePolicy.objects.create(
            code="ACC", name="Accrued", leave_type="other",
            annual_days=Decimal("24"), accrues_monthly=True,
        )
        person = self.employee("B6b")
        # 29 June: June has not ended, so five months are earned.
        self.assertEqual(
            policy.entitlement_for(person, 2026, as_of=datetime.date(2026, 6, 29)),
            Decimal("10.00"),
        )

    def test_a_mid_year_joiner_accrues_over_the_months_they_are_here(self):
        policy = LeavePolicy.objects.create(
            code="ACC2", name="Accrued", leave_type="other",
            annual_days=Decimal("24"), accrues_monthly=True,
        )
        person = self.employee("B6c", hire=datetime.date(2026, 7, 1))
        # Half a year here earns 12.10 of the 24; three of those six
        # months gone earns half of that again.
        full = policy.entitlement_for(person, 2026, as_of=datetime.date(2026, 12, 31))
        part = policy.entitlement_for(person, 2026, as_of=datetime.date(2026, 9, 30))
        self.assertEqual(part, (full / 2).quantize(Decimal("0.01")))

    def test_the_balance_is_what_is_left(self):
        person = self.employee("B7")
        booking = self.request(person, datetime.date(2026, 7, 6), datetime.date(2026, 7, 10))
        booking.approve(by=self.boss)
        self.assertEqual(leave_balance(person, self.policy, 2026), Decimal("20.00"))

    def test_a_pending_request_is_already_spoken_for(self):
        person = self.employee("B8")
        self.request(person, datetime.date(2026, 7, 6), datetime.date(2026, 7, 10))
        self.assertEqual(leave_balance(person, self.policy, 2026), Decimal("20.00"))

    def test_the_balance_comes_back_when_leave_is_cancelled(self):
        # Derived, never stored: a running balance drifts the moment a
        # request is cancelled and nobody finds out but the employee.
        person = self.employee("B9")
        booking = self.request(person, datetime.date(2027, 7, 5), datetime.date(2027, 7, 9))
        booking.approve(by=self.boss)
        self.assertEqual(leave_balance(person, self.policy, 2027), Decimal("20.00"))
        booking.cancel()
        self.assertEqual(leave_balance(person, self.policy, 2027), Decimal("25.00"))

    def test_more_than_the_allowance_is_refused(self):
        person = self.employee("B10")
        booking = self.request(person, datetime.date(2026, 3, 2), datetime.date(2026, 5, 29))
        with self.assertRaises(ValidationError) as caught:
            booking.approve(by=self.boss)
        self.assertIn("day(s) of", str(caught.exception))

    def test_a_request_that_exactly_uses_the_remainder_is_allowed(self):
        # The request is pending, so it is already counted against the
        # balance; counting it twice would refuse the last valid booking.
        person = self.employee("B11")
        LeaveEntitlement.objects.create(
            employee=person, policy=self.policy, year=2026, days=Decimal("5")
        )
        booking = self.request(person, datetime.date(2026, 7, 6), datetime.date(2026, 7, 10))
        booking.approve(by=self.boss)
        self.assertEqual(leave_balance(person, self.policy, 2026), Decimal("0.00"))

    def test_a_policy_that_allows_negative_lets_it_go_over(self):
        person = self.employee("B12")
        booking = self.request(
            person, datetime.date(2026, 3, 2), datetime.date(2026, 5, 29),
            policy=self.sick, leave_type=LeaveType.SICK,
        )
        booking.approve(by=self.boss)
        self.assertLess(leave_balance(person, self.sick, 2026), Decimal("0"))

    def test_a_request_with_no_policy_counts_against_nothing(self):
        person = self.employee("B13")
        booking = self.request(
            person, datetime.date(2026, 3, 2), datetime.date(2026, 5, 29), policy=None
        )
        booking.approve(by=self.boss)
        self.assertEqual(leave_balance(person, self.policy, 2026), Decimal("25.00"))

    def test_a_policy_only_takes_its_own_kind_of_leave(self):
        person = self.employee("B14")
        with self.assertRaises(ValidationError) as caught:
            self.request(
                person, datetime.date(2026, 7, 6), datetime.date(2026, 7, 10),
                policy=self.sick, leave_type=LeaveType.VACATION,
            )
        self.assertIn("grants sick leave", str(caught.exception))

    def test_the_summary_shows_every_allowance(self):
        person = self.employee("B15")
        booking = self.request(person, datetime.date(2026, 7, 6), datetime.date(2026, 7, 10))
        booking.approve(by=self.boss)
        rows = {row["policy"].code: row for row in leave_summary(person, 2026)}
        self.assertEqual(rows["HOL"]["taken"], Decimal("5.00"))
        self.assertEqual(rows["HOL"]["balance"], Decimal("20.00"))
        self.assertEqual(rows["SICK"]["balance"], Decimal("10.00"))


class OnlyTheirManagersDecideTests(LeaveTestCase):
    """
    Anyone but the person themselves could decide anyone's leave: the
    decision recorded a manager who need not manage them at all.
    """

    def booking(self, person):
        return self.request(person, datetime.date(2026, 7, 1), datetime.date(2026, 7, 3))

    def test_a_manager_who_is_not_theirs_is_refused(self):
        other_boss = self.employee("MGR-B", manager=None)
        booking = self.booking(self.employee("A1"))  # reports to self.boss
        for decide in (booking.approve, booking.reject):
            with self.subTest(decide=decide.__name__), \
                    self.assertRaisesMessage(ValidationError, "does not manage"):
                decide(by=other_boss)
        booking.refresh_from_db()
        self.assertEqual(booking.status, LeaveStatus.PENDING)

    def test_a_manager_above_and_the_departments_manager_may(self):
        from .models import Department

        head = self.employee("HEAD", manager=None)
        self.boss.manager = head
        self.boss.save()
        first = self.booking(self.employee("A2"))
        first.approve(by=head)
        self.assertEqual(first.decided_by, head)

        dept_head = self.employee("DEPT", manager=None)
        weaving = Department.objects.create(code="WV", name="Weaving", manager=dept_head)
        loner = self.employee("A3", manager=None, department=weaving)
        second = self.booking(loner)
        second.approve(by=dept_head)
        self.assertEqual(second.status, LeaveStatus.APPROVED)

    def test_someone_with_no_manager_is_decided_by_nobody_until_they_have_one(self):
        loner = self.employee("A4", manager=None)
        with self.assertRaisesMessage(ValidationError, "does not manage"):
            self.booking(loner).approve(by=self.boss)

    def test_only_their_manager_takes_a_yes_back(self):
        booking = self.booking(self.employee("A5"))
        booking.approve(by=self.boss)
        with self.assertRaisesMessage(ValidationError, "does not manage"):
            booking.withdraw_approval(by=self.employee("MGR-C", manager=None))
        with self.assertRaisesMessage(ValidationError, "say who"):
            booking.withdraw_approval(by=None)
        booking.refresh_from_db()
        self.assertEqual(booking.status, LeaveStatus.APPROVED)



class HrDecidesAnyonesLeaveTests(LeaveTestCase):
    """Someone with no manager could never have leave decided. HR may decide anyone's, the plant's choice."""

    def booking(self, person):
        return self.request(person, datetime.date(2026, 7, 1), datetime.date(2026, 7, 3))

    def test_hr_decides_for_someone_with_no_manager_and_no_department_manager(self):
        hr = self.employee("HR-1", manager=None)
        loner = self.booking(self.employee("L-1", manager=None))
        with self.assertRaisesMessage(ValidationError, "does not manage"):
            loner.approve(by=hr)
        loner.approve(by=hr, as_hr=True)
        self.assertEqual((loner.status, loner.decided_by), (LeaveStatus.APPROVED, hr))

    def test_hr_decides_for_someone_who_has_a_manager_too(self):
        hr = self.employee("HR-2", manager=None)
        managed = self.booking(self.employee("L-2"))  # reports to self.boss
        managed.reject(by=hr, reason="Year-end stock count", as_hr=True)
        self.assertEqual((managed.status, managed.decided_by), (LeaveStatus.REJECTED, hr))

    def test_not_even_hr_decides_their_own(self):
        hr = self.employee("HR-3", manager=None)
        own = self.booking(hr)
        with self.assertRaisesMessage(ValidationError, "cannot decide their own"):
            own.approve(by=hr, as_hr=True)
