"""
Building ahead: 5,000 kg of fabric scheduled for the week of 8 June.

  The loom does 60 kg an hour with an hour's setup: 5,060 minutes. On
  a 24-hour loom the week has 10,080 free; on an eight-hour one, 3,360,
  and the commitment is refused unless somebody says why.
  With 2,000 kg sold for 11 June, that week wants 2,000, has 5,000
  coming, all of it scheduled, and leaves 3,000.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.manufacturing.orders import WorkOrderStatus

from .capacity import LoadBook
from .mps import MasterScheduleEntry, schedule_view
from .tests_base import TODAY
from .tests_mrp import PlanningTestCase

WEEK = datetime.date(2026, 6, 8)


class ScheduleTestCase(PlanningTestCase):
    def entry(self, quantity="5000", week=WEEK, item=None):
        return MasterScheduleEntry.objects.create(
            item=item or self.fabric, warehouse=self.plant, week_of=week,
            quantity=Decimal(quantity), reason="Pre-monsoon build")


class CommitTests(ScheduleTestCase):
    def test_committed_it_is_a_draft_run_the_plan_counts(self):
        entry = self.entry()
        (row,) = entry.rough_cut(TODAY)
        self.assertEqual((row["needed"], row["free"]), (Decimal("5060"), Decimal("10080")))
        entry.commit(on_date=TODAY)
        order = entry.work_order
        self.assertEqual((order.status, order.scheduled_start, order.scheduled_end,
                          order.quantity_ordered),
                         (WorkOrderStatus.DRAFT, WEEK, WEEK + datetime.timedelta(days=6),
                          Decimal("5000")))
        self.stock(self.tape, "9000")
        # Due at the end of its week, the 14th: it covers the 15th, and
        # would not cover the 11th, for which the plan rightly asks a run.
        self.sell(self.fabric, "2000", self.day(14))
        self.assertNotIn("FAB-10X10", self.orders())
        book = LoadBook(self.plant, TODAY, self.day(30))
        self.assertEqual(round(book.booked(self.loom, WEEK), 4), round(Decimal("5060") / 7, 4))

    def test_more_than_the_machines_have_only_saying_why(self):
        self.loom.available_hours_per_day = Decimal("8")
        self.loom.save()
        entry = self.entry()
        with self.assertRaisesMessage(ValidationError,
                                      "LOOM-1 needs 5060 minutes that week and has 3360 free"):
            entry.commit(on_date=TODAY)
        entry.commit("Sunday overtime agreed", on_date=TODAY)
        self.assertEqual(entry.overload_accepted, "Sunday overtime agreed")

    def test_what_cannot_be_scheduled(self):
        with self.assertRaisesMessage(ValidationError, "is not a Monday"):
            self.entry(week=datetime.date(2026, 6, 9))
        with self.assertRaisesMessage(ValidationError, "has gone"):
            self.entry(week=datetime.date(2026, 5, 18)).commit(on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "no recipe to make it by"):
            self.entry(item=self.virgin).commit(on_date=TODAY)
        blank = self.entry()
        MasterScheduleEntry.objects.filter(pk=blank.pk).update(reason=" ")
        blank.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "Say why it is scheduled"):
            blank.commit(on_date=TODAY)

    def test_this_week_starts_from_today(self):
        # The week of 1 June, planned on Wednesday 3 June: five days left.
        entry = self.entry(week=datetime.date(2026, 6, 1))
        (row,) = entry.rough_cut(datetime.date(2026, 6, 3))
        self.assertEqual(row["free"], Decimal("7200"))
        entry.commit(on_date=datetime.date(2026, 6, 3))
        self.assertEqual(entry.work_order.scheduled_start, datetime.date(2026, 6, 3))


class WithdrawTests(ScheduleTestCase):
    def test_withdrawn_while_the_run_is_a_draft(self):
        entry = self.entry()
        entry.commit(on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "Say why"):
            entry.withdraw(" ")
        entry.withdraw("Monsoon forecast revised")
        entry.work_order.refresh_from_db()
        self.assertEqual(entry.work_order.status, WorkOrderStatus.CANCELLED)
        with self.assertRaisesMessage(ValidationError, "not a standing commitment"):
            entry.withdraw("Again")

    def test_not_once_the_run_is_on_the_floor(self):
        entry = self.entry()
        entry.commit(on_date=TODAY)
        self.stock(self.tape, "9000")
        entry.work_order.release(TODAY)
        with self.assertRaisesMessage(ValidationError, "has been released"):
            entry.withdraw("Too late")

    def test_committed_is_committed(self):
        entry = self.entry()
        entry.commit(on_date=TODAY)
        entry.quantity = Decimal("1")
        with self.assertRaisesMessage(ValidationError, "is committed. Withdraw it"):
            entry.save()
        with self.assertRaisesMessage(ValidationError, "is committed; withdraw it"):
            entry.delete()
        with self.assertRaisesMessage(ValidationError, "already committed"):
            MasterScheduleEntry.objects.get(pk=entry.pk).commit(on_date=TODAY)


class WeeksTests(ScheduleTestCase):
    def test_wanted_coming_scheduled_and_left(self):
        self.entry().commit(on_date=TODAY)
        self.sell(self.fabric, "2000", self.day(10))
        rows = schedule_view(self.fabric, self.plant, TODAY, weeks=3, on_date=TODAY)
        self.assertEqual([(row["week_of"], row["wanted"], row["coming"],
                           row["of_which_scheduled"], row["projected"]) for row in rows],
                         [(TODAY, 0, 0, 0, 0),
                          (WEEK, Decimal("2000"), Decimal("5000"), Decimal("5000"),
                           Decimal("3000")),
                          (datetime.date(2026, 6, 15), 0, 0, 0, Decimal("3000"))])

    def test_a_withdrawn_week_is_not_scheduled(self):
        entry = self.entry()
        entry.commit(on_date=TODAY)
        entry.withdraw("Changed our mind")
        rows = schedule_view(self.fabric, self.plant, WEEK, weeks=1, on_date=TODAY)
        self.assertEqual((rows[0]["coming"], rows[0]["of_which_scheduled"]), (0, 0))


class ScheduleApiTests(ScheduleTestCase):
    def test_drafted_cut_committed_and_read(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("planner"))
        response = client.post("/api/planning/master-schedule/", {
            "item": self.fabric.pk, "warehouse": self.plant.pk, "week_of": "2026-06-08",
            "quantity": "5000", "reason": "Build ahead"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        pk = response.json()["id"]
        rows = client.get(f"/api/planning/master-schedule/{pk}/rough-cut/",
                          {"on": "2026-06-01"}).json()
        self.assertEqual(rows, [{"work_centre": "LOOM-1", "needed_minutes": "5060",
                                 "free_minutes": "10080"}])
        response = client.post(f"/api/planning/master-schedule/{pk}/commit/",
                               {"on": "2026-06-01"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        response = client.patch(f"/api/planning/master-schedule/{pk}/", {"quantity": "1"},
                                format="json")
        self.assertEqual(response.status_code, 400)
        body = client.get("/api/planning/master-schedule/weeks/", {
            "item": self.fabric.pk, "warehouse": self.plant.pk, "start": "2026-06-08",
            "weeks": "1", "on": "2026-06-01"}).json()
        self.assertEqual(body, [{"week_of": "2026-06-08", "wanted": "0", "coming": "5000",
                                 "of_which_scheduled": "5000", "projected": "5000"}])
        response = client.post("/api/planning/master-schedule/", {
            "item": self.fabric.pk, "warehouse": self.plant.pk, "week_of": "2026-06-09",
            "quantity": "5", "reason": "x"}, format="json")
        self.assertEqual(response.status_code, 400)


class RoughCutEdgesTests(ScheduleTestCase):
    def test_it_asks_what_the_run_must_start_with(self):
        # 2% rejected: 5,000 delivered is 5,102.0408 started, and an hour.
        self.fabric_bom.expected_reject_percent = Decimal("2")
        self.fabric_bom.save()
        (row,) = self.entry().rough_cut(TODAY)
        self.assertEqual(round(row["needed"], 4), Decimal("5162.0408"))

    def test_an_outside_step_holds_none_of_our_machines(self):
        from apps.manufacturing.routing import RoutingOperation

        RoutingOperation.objects.create(routing=self.weaving, sequence=20, name="Laminate",
                                        is_outside=True, outside_lead_days=5,
                                        outside_cost_per_unit=Decimal("2"), rate_uom=self.kg)
        self.assertEqual([row["work_centre"] for row in self.entry().rough_cut(TODAY)],
                         [self.loom])

    def test_a_recipe_with_no_routing_has_nothing_to_cut(self):
        self.fabric_bom.routing = None
        self.fabric_bom.save()
        entry = self.entry()
        self.assertEqual(entry.rough_cut(TODAY), [])
        entry.commit(on_date=TODAY)

    def test_a_phantom_is_never_made_on_its_own(self):
        from apps.manufacturing.bom import BillOfMaterials

        kit = self.fabric.__class__.objects.create(sku="KIT", name="Kit", uom=self.kg)
        BillOfMaterials.objects.create(item=kit, name="Kit", quantity_produced=Decimal("1"),
                                       uom=self.kg, is_phantom=True)
        with self.assertRaisesMessage(ValidationError, "no recipe to make it by"):
            self.entry(item=kit).commit(on_date=TODAY)


class WeeksEdgesTests(ScheduleTestCase):
    def test_asked_mid_week_it_starts_on_the_monday(self):
        rows = schedule_view(self.fabric, self.plant, datetime.date(2026, 6, 10), weeks=1,
                             on_date=TODAY)
        self.assertEqual(rows[0]["week_of"], WEEK)

    def test_what_was_due_before_the_first_week_is_still_owed_in_it(self):
        self.entry().commit(on_date=TODAY)
        self.sell(self.fabric, "1000", self.day(2))
        (row,) = schedule_view(self.fabric, self.plant, WEEK, weeks=1, on_date=TODAY)
        self.assertEqual((row["wanted"], row["coming"], row["projected"]),
                         (Decimal("1000"), Decimal("5000"), Decimal("4000")))
