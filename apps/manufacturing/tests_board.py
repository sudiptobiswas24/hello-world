"""
The run board reads what each run has done: a draft is not released; a
released run with nothing booked waits; one with output or time on it
runs; one whose output is complete waits to be closed; a closed one
shows for a week.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from .board import board
from .tests_orders import TODAY, RunTestCase


class BoardTests(RunTestCase):
    def columns(self):
        return {column["key"]: [card["number"] or f"draft {card['id']}" for card in column["cards"]] for column in board(TODAY)}

    def test_each_run_sits_in_the_column_its_facts_put_it_in(self):
        draft = self.order("100")
        waiting = self.order("100")
        waiting.release(on_date=TODAY)
        running = self.order("100")
        running.release(on_date=TODAY)
        self.produce(running, "40").post()
        done = self.order("100")
        done.release(on_date=TODAY)
        self.produce(done, "100").post()
        closed = self.order("100")
        closed.release(on_date=TODAY)
        self.produce(closed, "100").post()
        closed.close(on_date=TODAY)
        cancelled = self.order("100")
        cancelled.cancel()
        self.assertEqual(self.columns(), {
            "draft": [f"draft {draft.pk}"], "waiting": [waiting.number], "running": [running.number],
            "to_close": [done.number], "closed": [closed.number],
        })
        card = next(column for column in board(TODAY) if column["key"] == "running")["cards"][0]
        self.assertEqual((card["quantity_ordered"], card["quantity_produced"], card["uom"], card["late"], card["work_centre"]),
                         (Decimal("100"), Decimal("40"), "kg", False, "EXT-1"))

    def test_late_is_past_due_and_not_closed_and_closed_runs_fall_off_after_a_week(self):
        late = self.order("100")
        late.scheduled_end = TODAY - datetime.timedelta(days=1)
        late.save()
        old = self.order("100")
        old.release(on_date=TODAY - datetime.timedelta(days=10))
        self.produce(old, "100").post()
        old.close(on_date=TODAY - datetime.timedelta(days=10))
        # Closed ten days before the day the board is read for, whatever the clock says now.
        type(old).objects.filter(pk=old.pk).update(closed_at=timezone.make_aware(
            datetime.datetime.combine(TODAY - datetime.timedelta(days=10), datetime.time(12))))
        columns = {column["key"]: column["cards"] for column in board(TODAY)}
        self.assertEqual((columns["draft"][0]["late"], columns["closed"]), (True, []))

    def test_the_floor_reads_it_and_the_books_do_not(self):
        call_command("setup_roles", verbosity=0)
        self.order("100")

        def as_(role):
            user = User.objects.create_user(role.replace(" ", "_").lower())
            user.groups.add(Group.objects.get(name=role))
            client = APIClient()
            client.force_authenticate(user)
            return client

        seen = as_("Production Supervisor").get("/api/manufacturing/work-orders/board/")
        self.assertEqual((seen.status_code, [column["key"] for column in seen.json()]),
                         (200, ["draft", "waiting", "running", "to_close", "closed"]), seen.content)
        self.assertEqual(len(seen.json()[0]["cards"]), 1)
        self.assertEqual(as_("Bookkeeper").get("/api/manufacturing/work-orders/board/").status_code, 403)
