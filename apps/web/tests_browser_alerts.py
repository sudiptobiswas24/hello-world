"""
The inspector writes down what was found on the floor; the quality
manager decides it was not a defect, saying why; the maintenance
department reads the month.
"""

import datetime
import re

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.hr.models import Employee
from apps.manufacturing.models import QualityAlert, WorkCentre

from .tests_browser import BrowserTestCase


class AlertsInTheBrowserTests(BrowserTestCase):
    def setUp(self):
        super().setUp()
        WorkCentre.objects.create(code="LOOM-3", name="Loom shed 3")
        priya = Party.objects.create(code="PRIYA", name="Priya Nair")
        PartyRoleAssignment.objects.create(party=priya, role=PartyRole.EMPLOYEE)
        self.priya = Employee.objects.create(party=priya, employee_number="PRIYA", hire_date=datetime.date(2026, 1, 1))

    def test_raised_on_the_floor_decided_by_the_manager_and_the_month_read(self):
        page = self.sign_in(self.person("Quality Inspector"), "/app/quality/alerts/new")
        page.get_by_label("What was found").fill("GSM low on loom 3")
        page.get_by_label("How bad").select_option("high")
        page.get_by_label("Work centre").select_option(str(WorkCentre.objects.get(code="LOOM-3").pk))
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/quality/alerts/\d+$"))
        alert = QualityAlert.objects.get()
        self.assertEqual((alert.title, alert.severity, alert.work_centre.code, alert.number[:3]),
                         ("GSM low on loom 3", "high", "LOOM-3", "QA-"))

        manager = self.sign_in(self.person("Quality Manager"), f"/app/quality/alerts/{alert.pk}", page=self.new_page())
        manager.get_by_role("button", name="Not a defect").click()
        form = manager.get_by_role("form", name="Not a defect")
        form.get_by_label("Why").fill("Gauge out of calibration")
        form.get_by_role("combobox", name="Decided by").fill("Priya")
        manager.get_by_role("option", name=re.compile("Priya Nair")).click()
        form.get_by_role("button", name="Not a defect").click()
        self.toast(manager, "Recorded as not a defect")
        alert.refresh_from_db()
        self.assertEqual((alert.status, alert.cancelled_reason, alert.decided_by), ("cancelled", "Gauge out of calibration", self.priya))

        fitter = self.sign_in(self.person("Maintenance"), "/app/plant/calendar?year=2026&month=6", page=self.new_page())
        expect(fitter.get_by_role("heading", name="Maintenance calendar")).to_be_visible()
        expect(fitter.get_by_text("June 2026")).to_be_visible()
        expect(fitter.locator("table.calendar tbody tr")).to_have_count(5)
        self.assertEqual(self.problems, [])
