"""
Changing a press over, and the order of runs that costs least.

The matrix is a printing press's: going darker is cheap, going lighter
means washing every trace out.

    white → yellow   20      yellow → white   90
    yellow → black   20      black → yellow  120
    white → black    30      black → white   120

The press last ran white. Three runs are queued as planned — black,
then white, then yellow — which by hand costs 30 + 120 + 20 = 170
minutes of changeover. White first (nought, same family), then yellow
(20), then black (20) costs 40. The proposal saves 130 minutes, and
moves the black run from first to last.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.inventory.models import Item

from .bom import BillOfMaterials, BomComponent
from .changeover import (
    ChangeoverRule,
    SetupFamily,
    changeover_minutes,
    on_the_machine,
    queue_on,
    sequence,
)
from .machines import Machine
from .orders import TimeBooking, WorkOrder
from .routing import Routing, RoutingOperation
from .tests_orders import TODAY, RunTestCase

FLAT = Decimal("45")


class ChangeoverTestCase(RunTestCase):
    def setUp(self):
        super().setUp()
        self.press = self.loom
        self.routing = Routing.objects.create(code="R-PRINT", name="Print")
        RoutingOperation.objects.create(
            routing=self.routing, sequence=10, name="Print",
            work_centre=self.press, setup_minutes=FLAT,
            units_per_hour=Decimal("180"), rate_uom=self.kg,
        )
        self.items = {}
        for colour in ("WHITE", "YELLOW", "BLACK"):
            item = Item.objects.create(
                sku=f"BAG-{colour}", name=f"{colour} bag", uom=self.kg
            )
            bom = BillOfMaterials.objects.create(
                item=item, quantity_produced=Decimal("100"), uom=self.kg,
                routing=self.routing,
            )
            BomComponent.objects.create(
                bom=bom, item=self.virgin, quantity=Decimal("100"),
                uom=self.kg, line_number=1,
            )
            SetupFamily.objects.create(
                item=item, work_centre=self.press, family=colour,
            )
            self.items[colour] = item
        for before, after, minutes in (
            ("WHITE", "YELLOW", "20"), ("YELLOW", "WHITE", "90"),
            ("YELLOW", "BLACK", "20"), ("BLACK", "YELLOW", "120"),
            ("WHITE", "BLACK", "30"), ("BLACK", "WHITE", "120"),
        ):
            ChangeoverRule.objects.create(
                work_centre=self.press, from_family=before, to_family=after,
                minutes=Decimal(minutes),
            )

    def queued(self, colour, day, due=None, machine=None):
        item = self.items[colour]
        order = WorkOrder.objects.create(
            item=item, bom=item.boms.get(), quantity_ordered=Decimal("10"),
            uom=self.kg, warehouse=self.plant,
            scheduled_start=TODAY + datetime.timedelta(days=day),
            scheduled_end=due or TODAY + datetime.timedelta(days=day + 1),
        )
        order.release(TODAY)
        if machine is not None:
            operation = order.operations.get()
            operation.machine = machine
            operation.save()
        return order

    def book(self, order, day=0, machine=None):
        booking = TimeBooking.objects.create(
            work_order=order, operation=order.operations.get(),
            booking_date=TODAY + datetime.timedelta(days=day),
            minutes=Decimal("1"), machine=machine,
        )
        booking.post()
        return booking


class WhatAChangeoverCostsTests(ChangeoverTestCase):
    def minutes(self, before, after):
        return changeover_minutes(
            self.press, self.items[before] if before else None,
            self.items[after], FLAT,
        )

    def test_the_same_family_costs_nothing(self):
        self.assertEqual(self.minutes("WHITE", "WHITE"), Decimal("0"))

    def test_going_darker_is_cheap_and_lighter_dear(self):
        self.assertEqual(self.minutes("WHITE", "BLACK"), Decimal("30"))
        self.assertEqual(self.minutes("BLACK", "WHITE"), Decimal("120"))

    def test_an_unknown_last_run_takes_the_flat_setup(self):
        self.assertEqual(self.minutes(None, "WHITE"), FLAT)

    def test_an_item_in_no_family_takes_the_flat_setup(self):
        SetupFamily.objects.filter(item=self.items["BLACK"]).delete()
        self.assertEqual(self.minutes("WHITE", "BLACK"), FLAT)

    def test_a_catch_all_does_not_speak_for_an_item_in_no_family(self):
        """
        The case the guard is for: with an any-to-any rule present, an
        unfamilied item would otherwise be quoted the catch-all. Unknown
        is not "any family" — it is unknown, and the flat setup is what
        the plant had before anybody filled the matrix in.
        """
        ChangeoverRule.objects.create(
            work_centre=self.press, from_family="", to_family="",
            minutes=Decimal("60"),
        )
        SetupFamily.objects.filter(item=self.items["BLACK"]).delete()
        self.assertEqual(self.minutes("WHITE", "BLACK"), FLAT)
        self.assertEqual(self.minutes(None, "WHITE"), FLAT)

    def test_a_pair_no_rule_covers_takes_the_flat_setup(self):
        ChangeoverRule.objects.filter(
            from_family="WHITE", to_family="BLACK"
        ).delete()
        self.assertEqual(self.minutes("WHITE", "BLACK"), FLAT)

    def test_the_most_particular_rule_wins(self):
        """
        Four rules could answer white → black. Taken away one at a time
        from the most particular, each next one answers in turn.
        """
        for before, after, minutes in (
            ("WHITE", "", "31"), ("", "BLACK", "32"), ("", "", "33"),
        ):
            ChangeoverRule.objects.create(
                work_centre=self.press, from_family=before, to_family=after,
                minutes=Decimal(minutes),
            )
        answers = []
        for before, after in (
            ("WHITE", "BLACK"), ("WHITE", ""), ("", "BLACK"), ("", ""),
        ):
            answers.append(self.minutes("WHITE", "BLACK"))
            ChangeoverRule.objects.filter(
                from_family=before, to_family=after
            ).delete()
        self.assertEqual(answers, [
            Decimal("30"), Decimal("31"), Decimal("32"), Decimal("33"),
        ])
        self.assertEqual(self.minutes("WHITE", "BLACK"), FLAT)

    def test_a_rule_from_a_family_to_itself_is_refused(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ChangeoverRule.objects.create(
                work_centre=self.press, from_family="WHITE",
                to_family="WHITE", minutes=Decimal("5"),
            )

    def test_a_negative_changeover_is_refused(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ChangeoverRule.objects.create(
                work_centre=self.press, from_family="YELLOW",
                to_family="", minutes=Decimal("-1"),
            )


class WhatIsOnThePressTests(ChangeoverTestCase):
    def test_nothing_booked_is_nothing_known(self):
        self.assertIsNone(on_the_machine(self.press))

    def test_the_last_posted_booking_says(self):
        self.book(self.queued("WHITE", 0), day=0)
        self.book(self.queued("BLACK", 1), day=1)
        self.assertEqual(on_the_machine(self.press), self.items["BLACK"])

    def test_a_voided_booking_hands_the_answer_back(self):
        self.book(self.queued("WHITE", 0), day=0)
        self.book(self.queued("BLACK", 1), day=1).void(TODAY)
        self.assertEqual(on_the_machine(self.press), self.items["WHITE"])

    def test_one_press_in_a_bank_is_asked_about_on_its_own(self):
        first = Machine.objects.create(work_centre=self.press, code="P-1")
        second = Machine.objects.create(work_centre=self.press, code="P-2")
        self.book(self.queued("WHITE", 0), day=0, machine=first)
        self.book(self.queued("BLACK", 1), day=1, machine=second)
        self.assertEqual(on_the_machine(self.press, first), self.items["WHITE"])
        self.assertEqual(
            on_the_machine(self.press, second), self.items["BLACK"]
        )


class WhatIsQueuedTests(ChangeoverTestCase):
    def test_the_queue_is_in_planned_order(self):
        yellow = self.queued("YELLOW", 3)
        black = self.queued("BLACK", 1)
        self.assertEqual(
            [op.work_order for op in queue_on(self.press)], [black, yellow]
        )

    def test_a_started_run_is_on_the_press_not_in_the_queue(self):
        started = self.queued("WHITE", 0)
        self.book(started)
        waiting = self.queued("BLACK", 1)
        self.assertEqual(
            [op.work_order for op in queue_on(self.press)], [waiting]
        )

    def test_a_voided_start_puts_it_back_in_the_queue(self):
        run = self.queued("WHITE", 0)
        self.book(run).void(TODAY)
        self.assertEqual([op.work_order for op in queue_on(self.press)], [run])

    def test_a_closed_run_is_not_waiting(self):
        run = self.queued("WHITE", 0)
        run.close(TODAY)
        self.assertEqual(queue_on(self.press), [])


class TheOrderThatCostsLeastTests(ChangeoverTestCase):
    def setUp(self):
        super().setUp()
        self.book(self.queued("WHITE", 0), day=0)
        self.black = self.queued("BLACK", 1)
        self.white = self.queued("WHITE", 2)
        self.yellow = self.queued("YELLOW", 3)

    def test_the_planned_order_costs_what_it_costs(self):
        result = sequence(self.press)
        self.assertEqual(result["on_the_machine"], self.items["WHITE"])
        self.assertEqual(
            [row["changeover_minutes"] for row in result["planned"]],
            [Decimal("30"), Decimal("120"), Decimal("20")],
        )
        self.assertEqual(result["planned_minutes"], Decimal("170"))

    def test_the_proposal_changes_over_least(self):
        result = sequence(self.press)
        self.assertEqual(
            [row["operation"].work_order for row in result["proposed"]],
            [self.white, self.yellow, self.black],
        )
        self.assertEqual(result["proposed_minutes"], Decimal("40"))
        self.assertEqual(result["saved_minutes"], Decimal("130"))
        self.assertIsNone(result["note"])

    def test_it_names_the_runs_it_makes_later(self):
        self.assertEqual(sequence(self.press)["moved_later"], [
            self.black.operations.get(),
        ])


class NeverWorseThanThePlanTests(ChangeoverTestCase):
    def test_a_greedy_order_dearer_than_the_plan_is_not_offered(self):
        """
        Nearest-first is a heuristic and can lose. On white, with black
        then yellow planned: the plan costs 30 + 120 = 150; nearest-
        first takes yellow (20) and then black (20) — 40, a saving. So
        make the second step dear: yellow → black at 500 turns nearest-
        first into 20 + 500 = 520, worse than the plan's 150, and the
        plan must stand.
        """
        ChangeoverRule.objects.filter(
            from_family="YELLOW", to_family="BLACK"
        ).update(minutes=Decimal("500"))
        self.book(self.queued("WHITE", 0), day=0)
        black = self.queued("BLACK", 1)
        yellow = self.queued("YELLOW", 2)
        result = sequence(self.press)
        self.assertEqual(
            [row["operation"].work_order for row in result["proposed"]],
            [black, yellow],
        )
        self.assertEqual(result["saved_minutes"], Decimal("0"))
        self.assertEqual(result["moved_later"], [])
        self.assertIn("No order found", result["note"])

    def test_a_tie_goes_to_the_run_due_first(self):
        """
        Inside a proposal that saves something — an equal-cost reorder
        is never offered, the plan stands. On white, planned yellow, then
        white due day nine, then white due day three: 20 + 90 + 0 = 110.
        Both whites cost nothing next, so the one due on day three leads,
        then the other, then yellow: 20.
        """
        self.book(self.queued("WHITE", 0), day=0)
        yellow = self.queued("YELLOW", 1)
        later = self.queued("WHITE", 2, due=TODAY + datetime.timedelta(days=9))
        sooner = self.queued("WHITE", 3, due=TODAY + datetime.timedelta(days=3))
        result = sequence(self.press)
        self.assertEqual(result["planned_minutes"], Decimal("110"))
        self.assertEqual(
            [row["operation"].work_order for row in result["proposed"]],
            [sooner, later, yellow],
        )
        self.assertEqual(result["proposed_minutes"], Decimal("20"))

    def test_an_equal_cost_reorder_is_not_offered(self):
        """The plan is not churned for nothing."""
        self.book(self.queued("WHITE", 0), day=0)
        later = self.queued("WHITE", 1, due=TODAY + datetime.timedelta(days=9))
        sooner = self.queued("WHITE", 2, due=TODAY + datetime.timedelta(days=3))
        result = sequence(self.press)
        self.assertEqual(
            [row["operation"].work_order for row in result["proposed"]],
            [later, sooner],
        )
        self.assertEqual(result["saved_minutes"], Decimal("0"))


class OnePressInABankTests(ChangeoverTestCase):
    def test_a_press_sequences_its_own_queue_from_its_own_last_run(self):
        first = Machine.objects.create(work_centre=self.press, code="P-1")
        second = Machine.objects.create(work_centre=self.press, code="P-2")
        self.book(self.queued("BLACK", 0, machine=second), day=0, machine=second)
        mine = self.queued("WHITE", 1, machine=first)
        self.queued("YELLOW", 2, machine=second)
        result = sequence(self.press, first)
        self.assertIsNone(result["on_the_machine"])
        self.assertEqual(
            [row["operation"].work_order for row in result["planned"]], [mine]
        )
        # Nothing known about P-1's last run, so the flat setup.
        self.assertEqual(result["planned_minutes"], FLAT)

    def test_a_press_from_another_bank_is_refused(self):
        from .orders import WorkCentre

        other = WorkCentre.objects.create(code="CUT", name="Cutting")
        stranger = Machine.objects.create(work_centre=other, code="C-1")
        with self.assertRaisesMessage(ValidationError, "belongs to CUT"):
            sequence(self.press, stranger)


class AskedOverTheApiTests(ChangeoverTestCase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        user = get_user_model().objects.create_superuser(
            "planner", "planner@example.com", "x"
        )
        self.client = APIClient()
        self.client.force_authenticate(user)

    def test_the_proposal_comes_back_named(self):
        self.book(self.queued("WHITE", 0), day=0)
        black = self.queued("BLACK", 1)
        self.queued("WHITE", 2)
        self.queued("YELLOW", 3)
        response = self.client.get(
            f"/api/manufacturing/work-centres/{self.press.pk}/sequence/"
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["on_the_machine"], "BAG-WHITE")
        self.assertEqual(
            [row["item"] for row in body["proposed"]],
            ["BAG-WHITE", "BAG-YELLOW", "BAG-BLACK"],
        )
        self.assertEqual(Decimal(str(body["saved_minutes"])), Decimal("130"))
        self.assertEqual(body["moved_later"], [black.number])

    def test_an_unknown_press_is_refused_in_words(self):
        response = self.client.get(
            f"/api/manufacturing/work-centres/{self.press.pk}/sequence/"
            "?machine=NOPE"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("NOPE", str(response.json()))

    def test_the_matrix_is_listed(self):
        for path in ("setup-families", "changeover-rules"):
            self.assertEqual(
                self.client.get(f"/api/manufacturing/{path}/").status_code, 200
            )


class PurgedOnTheChangeTests(ChangeoverTestCase):
    """
    On an extruder the change also costs polymer run through to clear
    it. With minutes equal the sequencer takes the lesser purge:

        white → yellow  20 min, 5 kg     white → black  20 min, 15 kg
        yellow → black  20 min, 5 kg     black → yellow 20 min, 25 kg

    On white, planned black then yellow: 40 minutes, 15 + 25 = 40 kg.
    Yellow then black: 40 minutes, 5 + 5 = 10 kg; 30 kg saved.
    """

    def setUp(self):
        super().setUp()
        for before, after, kg in (("WHITE", "YELLOW", "5"), ("WHITE", "BLACK", "15"),
                                  ("YELLOW", "BLACK", "5"), ("BLACK", "YELLOW", "25")):
            ChangeoverRule.objects.filter(from_family=before, to_family=after).update(
                minutes=Decimal("20"), purge_kg=Decimal(kg))

    def test_a_change_says_what_it_purges(self):
        from .changeover import changeover

        self.assertEqual(changeover(self.press, self.items["BLACK"], self.items["YELLOW"], FLAT),
                         (Decimal("20.00"), Decimal("25.000")))
        self.assertEqual(changeover(self.press, self.items["WHITE"], self.items["WHITE"], FLAT),
                         (Decimal("0"), Decimal("0")))
        self.assertEqual(changeover(self.press, None, self.items["WHITE"], FLAT),
                         (FLAT, Decimal("0")))

    def test_between_equal_minutes_the_lesser_purge(self):
        self.book(self.queued("WHITE", 0), day=0)
        black = self.queued("BLACK", 1)
        yellow = self.queued("YELLOW", 2)
        result = sequence(self.press)
        self.assertEqual([row["operation"].work_order for row in result["proposed"]],
                         [yellow, black])
        self.assertEqual((result["saved_minutes"], result["planned_purge_kg"],
                          result["proposed_purge_kg"], result["saved_purge_kg"]),
                         (Decimal("0"), Decimal("40.000"), Decimal("10.000"),
                          Decimal("30.000")))
        self.assertEqual([row["purge_kg"] for row in result["proposed"]],
                         [Decimal("5.000"), Decimal("5.000")])
        self.assertIsNone(result["note"])

    def test_a_purge_is_not_negative(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ChangeoverRule.objects.create(work_centre=self.press, from_family="WHITE",
                                          to_family="GREEN", minutes=Decimal("5"),
                                          purge_kg=Decimal("-1"))

    def test_the_board_carries_it(self):
        from .dispatch import build

        self.book(self.queued("BLACK", 0), day=0)
        yellow = self.queued("YELLOW", 1)
        (row,) = [row for row in build() if row["order"] == yellow]
        self.assertEqual((row["changeover"], row["purge_kg"]),
                         (Decimal("20.00"), Decimal("25.000")))

    def test_asked_over_the_api(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(get_user_model().objects.create_superuser("purger"))
        self.book(self.queued("WHITE", 0), day=0)
        self.queued("BLACK", 1)
        yellow = self.queued("YELLOW", 2)
        body = client.get(f"/api/manufacturing/work-centres/{self.press.pk}/sequence/").json()
        self.assertEqual((body["planned_purge_kg"], body["proposed_purge_kg"],
                          body["saved_purge_kg"], body["proposed"][0]["purge_kg"]),
                         ("40.000", "10.000", "30.000", "5.000"))
        board = client.get("/api/manufacturing/dispatch/").json()
        rows = [row for rows in board.values() for row in rows if row["run"] == yellow.number]
        self.assertEqual([row["purge_kg"] for row in rows], ["25.000"])
