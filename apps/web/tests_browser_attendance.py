"""
The shift register in the browser, as the people who keep it: the
supervisor marks a day (06:17 on the 06:00 shift is 17 minutes late);
payroll checks the reader's punch file, keeps it, and reads the days a
day-rated worker still has unmarked. Each step is read back from the
database.
"""

import datetime
import re

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.hr.attendance import AttendanceDay
from apps.hr.models import Employee
from apps.manufacturing.shifts import Shift

from .tests_browser import BrowserTestCase

PUNCHES = """employee_number,date,shift,in,out
W1,2026-10-06,A,06:03,14:10
W1,2026-10-07,A,06:00,
"""


class AttendanceInTheBrowserTests(BrowserTestCase):
    def setUp(self):
        super().setUp()
        party = Party.objects.create(code="P-W1", name="Ravi Kumar")
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
        self.worker = Employee.objects.create(party=party, employee_number="W1", hire_date=datetime.date(2020, 1, 1),
                                              working_days="123456", paid_by_attendance=True)
        Shift.objects.create(code="A", name="Morning", starts_at=datetime.time(6), hours=8)

    def test_marked_by_the_supervisor_and_read_in_by_payroll(self):
        page = self.sign_in(self.person("Production Supervisor"), "/app/payroll/attendance/new")
        page.get_by_role("combobox", name="Who").fill("W1")
        page.get_by_role("option", name=re.compile("Ravi Kumar")).click()
        page.get_by_label("Day").fill("2026-10-05")
        page.get_by_label("Came in").select_option(label="Present")
        page.get_by_label("Shift").fill("A")
        page.get_by_label("In", exact=True).fill("06:17")
        page.get_by_label("Out", exact=True).fill("14:05")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/payroll/attendance/\d+$"))
        expect(page.locator("main")).to_contain_text("Late by")
        marked = AttendanceDay.objects.get()
        self.assertEqual((marked.on, marked.late_minutes, marked.source), (datetime.date(2026, 10, 5), 17, "manual"))
        self.assertEqual(self.problems, [])

        page = self.sign_in(self.person("Payroll Officer"), "/app/payroll/punch-file", page=self.new_page())
        page.get_by_label("The file").fill(PUNCHES)
        page.get_by_role("button", name="Check").click()
        expect(page.locator("main")).to_contain_text("1 day(s) new")
        expect(page.locator("main")).to_contain_text("To enter by hand")
        self.assertEqual(AttendanceDay.objects.count(), 1)
        page.get_by_role("button", name="Keep").click()
        expect(page.locator("main")).to_contain_text("kept.")
        read_in = AttendanceDay.objects.get(on=datetime.date(2026, 10, 6))
        self.assertEqual((read_in.late_minutes, read_in.source), (3, "import"))

        page.goto(f"{self.live_server_url}/app/payroll/attendance-gaps?start=2026-10-05&end=2026-10-08")
        # The 7th (punched in only) and the 8th; the 5th was marked and the 6th read in.
        expect(page.locator("main")).to_contain_text("07 Oct 2026")
        expect(page.locator("main")).to_contain_text("08 Oct 2026")
        expect(page.locator("main")).not_to_contain_text("06 Oct 2026")
        self.assertEqual(self.problems, [])
