"""
Review probes for fix_core's O32 (g22 review_core): LeaveRequest.cancel is a dated correction. A leave already
taken is not given back ("only the future can be given back"), whatever day the cancel is dated.
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import LeaveRequest, LeaveStatus, LeaveType
from .tests_audit import AuditTestCase


class CancelDatingTests(AuditTestCase):
    def leave(self, start, end, name="C1"):
        person = self.employee(name)
        request = LeaveRequest.objects.create(employee=person, policy=self.policy, leave_type=LeaveType.VACATION,
                                              start_date=start, end_date=end)
        request.approve(by=self.boss)
        return LeaveRequest.objects.get(pk=request.pk), person

    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.day = lambda n: self.today + datetime.timedelta(days=n)  # noqa: E731

    def test_a_leave_not_yet_begun_is_cancelled_whole_by_default_and_on_its_first_day(self):
        request, person = self.leave(self.day(30), self.day(34))
        request.cancel(by=person)
        self.assertEqual(request.status, LeaveStatus.CANCELLED)
        again, person = self.leave(self.day(30), self.day(34), "C2")
        again.cancel(on_date=self.day(30), by=person)
        self.assertEqual(again.status, LeaveStatus.CANCELLED)

    def test_a_leave_not_yet_begun_is_not_cancelled_on_a_day_before_it_or_a_later_day_still_to_come(self):
        for offset in (-1, 29, 31):
            request, person = self.leave(self.day(30), self.day(34), f"D{offset}")
            with self.assertRaises(ValidationError, msg=offset):
                request.cancel(on_date=self.day(offset), by=person)
            request.refresh_from_db()
            self.assertEqual(request.status, LeaveStatus.APPROVED)

    def test_a_leave_under_way_is_cut_short_and_the_days_taken_stay_taken(self):
        request, person = self.leave(self.day(-2), self.day(2))
        request.cancel(by=person)
        request.refresh_from_db()
        self.assertEqual((request.status, request.end_date), (LeaveStatus.APPROVED, self.day(-1)))

    def test_a_leave_wholly_past_cannot_be_given_back_by_dating_the_cancel_on_its_first_day(self):
        # 28 to 24 days ago, taken. Default (today): "ended ... cannot be cancelled". Dated on its first day, the
        # whole of it was given back, the days already gone with it, by the employee, with nothing to stop it.
        request, person = self.leave(self.day(-28), self.day(-24))
        with self.assertRaises(ValidationError):
            request.cancel(by=person)
        with self.assertRaises(ValidationError):
            request.cancel(on_date=self.day(-28), by=person)
        request.refresh_from_db()
        self.assertEqual(request.status, LeaveStatus.APPROVED)

    def test_a_leave_under_way_cannot_be_wholly_given_back_by_dating_the_cancel_on_its_first_day(self):
        request, person = self.leave(self.day(-3), self.day(3))
        with self.assertRaises(ValidationError):
            request.cancel(on_date=self.day(-3), by=person)
        request.refresh_from_db()
        self.assertEqual(request.status, LeaveStatus.APPROVED)
