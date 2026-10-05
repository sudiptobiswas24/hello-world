"""
Leave in a browser, as the people who do it: the weaver asks, their
manager approves, a colleague sees none of it, and a login nobody has
linked to an employee is told why it cannot ask.
"""

import datetime
import re

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.hr.models import LeaveRequest, LeaveStatus
from apps.hr.tests_leave import LeaveTestCase

from .tests_browser import BrowserMixin


class LeaveInTheBrowserTests(BrowserMixin, LeaveTestCase, StaticLiveServerTestCase):
    def linked(self, role, employee):
        from django.contrib.auth.models import Group, User

        from .tests_browser import PASSWORD

        # One login a person: two in one role need names of their own.
        user = User.objects.create_user(f"{employee.employee_number.lower()}", password=PASSWORD)
        user.groups.add(Group.objects.get(name=role))
        employee.user = user
        employee.save()
        return user

    def test_asked_for_by_the_weaver_and_approved_by_their_manager(self):
        weaver = self.employee("W-1")  # reports to self.boss
        asker = self.sign_in(self.linked("Employee Self Service", weaver), "/app/payroll/leave/new")
        asker.get_by_label("Allowance").select_option(label="Annual leave")
        asker.get_by_label("From").fill("2026-11-02")
        asker.get_by_label("To", exact=True).fill("2026-11-04")
        asker.get_by_label("Reason").fill("Sister's wedding")
        asker.get_by_role("button", name="Ask for it").click()
        asker.wait_for_url(re.compile(r"/payroll/leave/\d+$"))
        leave = LeaveRequest.objects.get()
        self.assertEqual((leave.employee, leave.policy, leave.start_date, leave.status),
                         (weaver, self.policy, datetime.date(2026, 11, 2), LeaveStatus.PENDING))
        expect(asker.get_by_role("button", name="Approve", exact=True)).to_have_count(0)  # nobody approves their own

        manager = self.new_page()
        self.sign_in(self.linked("Line Manager", self.boss), "/app/payroll/leave", page=manager)
        manager.locator("tbody tr", has_text="W-1").click()
        # The request's own page, not the list: there "Approve" also names
        # the Approved filter, and a slow run clicked that instead.
        manager.wait_for_url(re.compile(rf"/payroll/leave/{leave.pk}$"))
        manager.get_by_role("button", name="Approve", exact=True).click()
        expect(manager.locator(".pill", has_text="approved")).to_be_visible()
        leave.refresh_from_db()
        self.assertEqual((leave.status, leave.decided_by), (LeaveStatus.APPROVED, self.boss))

        colleague = self.new_page()
        self.sign_in(self.linked("Employee Self Service", self.employee("W-2")), "/app/payroll/leave", page=colleague)
        expect(colleague.locator("tbody tr:not(.skeleton)", has_text="W-1")).to_have_count(0)
        expect(colleague.get_by_text("No leave requests").first).to_be_visible()
        self.assertEqual(self.problems, [])

    def test_hr_decides_anyones_and_another_teams_manager_cannot_open_it(self):
        weaver = self.employee("W-3")  # reports to self.boss
        leave = self.request(weaver, datetime.date(2026, 11, 9), datetime.date(2026, 11, 9))
        hr = self.sign_in(self.linked("HR Admin", self.employee("HR-1", manager=None)),
                          f"/app/payroll/leave/{leave.pk}")
        hr.get_by_role("button", name="Approve", exact=True).click()
        expect(hr.locator(".pill", has_text="approved")).to_be_visible()
        leave.refresh_from_db()
        self.assertEqual(leave.status, LeaveStatus.APPROVED)
        self.assertEqual(self.problems, [])

        stranger = self.new_page()
        self.sign_in(self.linked("Line Manager", self.employee("MGR-2", manager=None)),
                     f"/app/payroll/leave/{leave.pk}", page=stranger)
        expect(stranger.get_by_role("heading", name="Not found")).to_be_visible()
        self.problems.clear()  # the refusal is the point here

    def test_a_login_with_no_employee_is_told_why_it_cannot_ask(self):
        page = self.sign_in(self.login_for("Employee Self Service"), "/app/payroll/leave/new")
        expect(page.get_by_role("note")).to_contain_text("not linked to an employee")
        expect(page.get_by_role("button", name="Ask for it")).to_have_count(0)
        self.assertEqual(self.problems, [])
