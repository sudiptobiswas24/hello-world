"""
Planning and the floor, in a browser, by the people who do it: the
planner plans and firms what the plan says to make, the supervisor
releases it to the floor and reads the machine queue. Each step is read
back from the database.
"""

import re

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.manufacturing.orders import WorkOrder, WorkOrderStatus
from apps.planning.models import PlanningRun
from apps.planning.tests_mrp import PlanningTestCase

from .tests_browser import BrowserMixin


class ProductionInTheBrowserTests(BrowserMixin, PlanningTestCase, StaticLiveServerTestCase):
    def url(self, path):
        return f"{self.live_server_url}/app{path}"

    def test_the_planner_plans_and_firms_and_the_floor_releases(self):
        self.stock(self.tape, "2000")
        self.sell(self.fabric, "1000", self.day(30))

        planner = self.sign_in(self.person("Production Planner"), "/app/production/plan")
        planner.get_by_label("Warehouse to plan").select_option(label="P · Plant")
        planner.get_by_role("button", name="Plan now").click()
        planner.wait_for_url(re.compile(r"/production/plan/\d+$"))
        run = PlanningRun.objects.get()
        self.assertEqual(run.warehouse, self.plant)

        row = planner.locator("tbody tr", has_text="FAB-10X10")
        expect(row).to_contain_text("Make")
        row.get_by_role("button", name="Firm").click()
        expect(planner.locator(".toast", has_text="firmed").first).to_be_visible()
        work_order = WorkOrder.objects.get(item=self.fabric)
        self.assertEqual(work_order.status, WorkOrderStatus.DRAFT)
        expect(row.locator(f"a[href$=\"/production/work-orders/{work_order.pk}\"]")).to_have_count(1)

        floor = self.new_page()
        self.sign_in(self.person("Production Supervisor"), f"/app/production/work-orders/{work_order.pk}", page=floor)
        expect(floor.locator("main")).to_contain_text("FAB-10X10")
        expect(floor.get_by_role("button", name="Firm")).to_have_count(0)
        floor.get_by_role("button", name="Release").click()
        expect(floor.locator(".pill", has_text="released")).to_be_visible()
        work_order.refresh_from_db()
        self.assertEqual(work_order.status, WorkOrderStatus.RELEASED)

        floor.goto(self.url("/production/schedule"))
        expect(floor.locator("main")).to_contain_text(work_order.number)
        self.assertEqual(self.problems, [])

    def test_a_horizon_of_nothing_is_refused_in_words(self):
        planner = self.sign_in(self.person("Production Planner"), "/app/production/plan")
        planner.get_by_label("Days ahead").fill("0")
        planner.get_by_role("button", name="Plan now").click()
        expect(planner.locator(".toast-bad", has_text="at least 1")).to_be_visible()
        self.assertFalse(PlanningRun.objects.exists())

    def test_every_role_opens_what_it_reads_without_being_refused(self):
        from django.contrib.auth.models import Permission

        self.stock(self.tape, "2000")
        self.sell(self.fabric, "1000", self.day(30))
        run = self.plan()
        order = {o.item.sku: o for o in run.orders.all()}["FAB-10X10"].firm()
        screens = [
            ("planning.view_planningrun", "/production/plan", "Plan"),
            ("planning.view_planningrun", f"/production/plan/{run.pk}", "What to make, buy and move"),
            ("planning.view_plannedorder", "/production/planned", "Planned orders"),
            ("manufacturing.view_workorder", "/production/work-orders", "Work orders"),
            ("manufacturing.view_workorder", f"/production/work-orders/{order.pk}", "FAB-10X10"),
            ("manufacturing.view_workorder", "/production/schedule", "Machine schedule"),
        ]
        for role in ["Production Planner", "Production Supervisor", "Quality Inspector", "Sales Rep"]:
            with self.subTest(role=role):
                person = self.person(role)
                held = {f"{app}.{code}" for app, code in Permission.objects.filter(
                    group__user=person).values_list("content_type__app_label", "codename")}
                page = self.new_page()
                self.sign_in(person, "/app/", page=page)
                for permission, path, heading in screens:
                    if permission not in held:
                        continue
                    page.goto(self.url(path))
                    expect(page.locator("main").first).to_contain_text(heading)
                    page.wait_for_load_state("networkidle")
                self.assertEqual(self.problems, [], role)
