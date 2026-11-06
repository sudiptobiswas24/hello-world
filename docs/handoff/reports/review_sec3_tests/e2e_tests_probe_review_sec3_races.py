"""review_sec3: races on PostgreSQL for O124 (a line edited as its document posts), through the API as the role."""

import unittest
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.db import connection
from django.test import tag
from rest_framework.test import APIClient

from apps.accounting.models import JournalEntry
from apps.e2e import tests_races as races
from apps.inventory import tests_adjustments as stock_fixture
from apps.inventory.adjustments import StockAdjustment, StockAdjustmentLine
from apps.manufacturing import tests_orders as run_fixture
from apps.manufacturing.orders import MaterialIssue, MaterialIssueLine


def person(role):
    call_command("setup_roles", verbosity=0)
    user = User.objects.create_user(role.replace(" ", "_").lower())
    user.groups.add(Group.objects.get(name=role))
    api = APIClient()
    api.force_authenticate(user)
    return api


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class O124AdjustmentRaces(races.RaceCase):
    L = "/api/inventory/stock-adjustment-lines/"
    A = "/api/inventory/stock-adjustments/"

    def build(self):
        stock_fixture.AdjustmentTestCase.setUp(self)
        stock_fixture.AdjustmentTestCase.stock(self, "100", "5")
        self.api = person("Stores Manager")

    def booked(self, pk):
        doc = StockAdjustment.objects.get(pk=pk)
        lines = list(doc.lines.all())
        fields = [f.name for f in StockAdjustmentLine._meta.fields if "movement" in f.name and "reversal" not in f.name]
        return doc.posted, len(lines), [getattr(line, name) for line in lines for name in fields if getattr(line, name) is None]

    def test_a_line_added_as_the_adjustment_posts_is_posted_or_refused_never_left_unbooked(self):
        self.build()
        draft = stock_fixture.AdjustmentTestCase.adjust(self, "-10", post=False)
        add = lambda: self.api.post(self.L, {"adjustment": draft.pk, "item": self.item.pk, "uom": self.item.uom_id,
                                             "quantity": "-1"}, format="json")
        post = lambda: self.api.post(f"{self.A}{draft.pk}/post/", {}, format="json")
        got = []
        outcomes = races.race((JournalEntry, StockAdjustmentLine), lambda: got.append(("post", post().status_code)),
                              lambda: got.append(("add", add().status_code)))
        posted, count, unbooked = self.booked(draft.pk)
        self.assertEqual((posted, unbooked), (True, []), (got, outcomes, count))

    def test_a_line_moved_off_an_adjustment_as_it_posts_does_not_leave_it_empty_and_posted(self):
        self.build()
        a = stock_fixture.AdjustmentTestCase.adjust(self, "-10", post=False)
        b = stock_fixture.AdjustmentTestCase.adjust(self, "-1", post=False)
        line = a.lines.get()
        got = []
        outcomes = races.race((JournalEntry, StockAdjustmentLine),
                              lambda: got.append(("post", self.api.post(f"{self.A}{a.pk}/post/", {}, format="json").status_code)),
                              lambda: got.append(("move", self.api.patch(f"{self.L}{line.pk}/", {"adjustment": b.pk},
                                                                           format="json").status_code)))
        posted, count, unbooked = self.booked(a.pk)
        self.assertFalse(posted and count == 0, f"posted with no lines: {got} {outcomes}")
        self.assertEqual(unbooked, [], (got, outcomes))

    def test_two_lines_swapped_between_two_drafts_do_not_deadlock(self):
        self.build()
        a = stock_fixture.AdjustmentTestCase.adjust(self, "-10", post=False)
        b = stock_fixture.AdjustmentTestCase.adjust(self, "-1", post=False)
        la, lb = a.lines.get(), b.lines.get()
        got = []
        outcomes = races.race(StockAdjustmentLine,
                              lambda: got.append(self.api.patch(f"{self.L}{la.pk}/", {"adjustment": b.pk}, format="json").status_code),
                              lambda: got.append(self.api.patch(f"{self.L}{lb.pk}/", {"adjustment": a.pk}, format="json").status_code))
        self.assertTrue(all(o == "done" for o in outcomes), (outcomes, got))
        self.assertEqual(sorted(got), [200, 200], (got, outcomes))


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class O124IssueRaces(races.RaceCase):
    setUp = run_fixture.RunTestCase.setUp
    stock, order, issue, produce, balance, full_issue = (
        run_fixture.RunTestCase.stock, run_fixture.RunTestCase.order,
        run_fixture.RunTestCase.issue, run_fixture.RunTestCase.produce,
        run_fixture.RunTestCase.balance, run_fixture.RunTestCase.full_issue)

    def test_a_line_added_to_an_issue_as_it_posts_is_booked_or_refused(self):
        order = self.order("10")
        order.release(run_fixture.TODAY)
        issue = self.full_issue(order)
        api = person("Production Supervisor")
        item = order.components.first().item
        got = []
        add = lambda: got.append(("add", api.post("/api/manufacturing/material-issue-lines/", {
            "issue": issue.pk, "item": item.pk, "quantity": "1", "uom": self.kg.pk, "line_number": 50}, format="json").status_code))
        post = lambda: got.append(("post", MaterialIssue.objects.get(pk=issue.pk).post()))
        outcomes = races.race((JournalEntry, MaterialIssueLine), post, add)
        doc = MaterialIssue.objects.get(pk=issue.pk)
        unbooked = [line.pk for line in doc.lines.all() if line.stock_movement_id is None]
        self.assertEqual((doc.posted, unbooked), (True, []), (got, outcomes))


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class O196Race(races.RaceCase):
    """A Controller confirms a role for a login at the moment HR links the login to an employee: each checks the other's side unlocked."""

    def test_confirming_a_role_and_linking_the_login_at_once_leaves_a_controller_login_linked(self):
        import datetime

        from apps.core.roles import RoleProposal
        from apps.hr.models import Employee
        from apps.hr.tests import make_employee_party

        call_command("setup_roles", verbosity=0)

        def make(name, role):
            user = User.objects.create_user(name)
            user.groups.add(Group.objects.get(name=role))
            client = APIClient()
            client.force_authenticate(user)
            return user, client

        hr, hr_api = make("hr", "HR Admin")
        ctl, ctl_api = make("ctl", "Controller")
        b1 = User.objects.create_user("b1")
        boss = Employee.objects.create(party=make_employee_party("B1", "B1"), employee_number="B1",
                                       hire_date=datetime.date(2025, 1, 1))
        proposal = RoleProposal.objects.create(user=b1, group=Group.objects.get(name="Controller"), proposed_by=hr)
        got = []
        outcomes = races.race((RoleProposal, Employee),
                              lambda: got.append(("confirm", ctl_api.post(f"/api/core/role-proposals/{proposal.pk}/confirm/",
                                                                          {}, format="json").status_code)),
                              lambda: got.append(("link", hr_api.patch(f"/api/hr/employees/{boss.pk}/", {"user": b1.pk},
                                                                       format="json").status_code)))
        holds = User.objects.get(pk=b1.pk).groups.filter(name="Controller").exists()
        linked = Employee.objects.get(pk=boss.pk).user_id == b1.pk
        self.assertFalse(holds and linked, f"a Controller login linked to a person: {got} {outcomes}")
