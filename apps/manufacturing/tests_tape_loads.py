"""
Doffs D-1, D-2 and D-3, 100 kg each of the fabric's tape.

At 09:00 30 kg of D-1 goes on L-17's warp creel, at 09:05 20 kg of D-2
on its weft. The roll off at 10:42 was woven from D-1 and D-2. At 11:00
25 kg of D-3 goes on the weft; the roll off at 13:00 was woven from
D-1 (still on the warp), D-2 (on the weft until 11:00) and D-3. D-1 has
70 kg left in the store.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.inventory.models import Item, Lot

from .orders import WorkOrder
from .tape_loads import CreelSide, TapeLoad, load_tape, tape_for, void_load
from .tests_orders import TODAY
from .tests_station import StationTestCase, at
from .trace import recall


class TapeLoadTestCase(StationTestCase):
    def setUp(self):
        super().setUp()
        self.tape = self.spec.warp_tape.tape_item
        Item.objects.filter(pk=self.tape.pk).update(tracking="lot")
        self.tape.refresh_from_db()
        self.doffs = {}
        for code in ("D-1", "D-2", "D-3"):
            lot = Lot.objects.create(item=self.tape, code=code)
            self.stock(self.tape, "100", "120", lot=lot)
            self.doffs[code] = lot

    def load(self, code, kg, side, hour, minute=0):
        return load_tape(self.station, self.operator, self.l17, code, kg, side,
                         at=at(TODAY, hour, minute))

    def two_rolls(self):
        self.load("D-1", "30", CreelSide.WARP, 9)
        self.load("D-2", "20", CreelSide.WEFT, 9, 5)
        first = self.weigh(when=at(TODAY, 10, 42))
        self.load("D-3", "25", CreelSide.WEFT, 11)
        second = self.weigh(when=at(TODAY, 13))
        return first, second


class WovenFromTests(TapeLoadTestCase):
    def test_each_roll_names_the_doffs_on_its_loom(self):
        first, second = self.two_rolls()
        self.assertEqual([load.lot.code for load in tape_for(first)], ["D-1", "D-2"])
        self.assertEqual([load.lot.code for load in tape_for(second)], ["D-1", "D-2", "D-3"])

    def test_only_what_was_loaded_is_issued(self):
        self.two_rolls()
        self.assertEqual(self.doffs["D-1"].on_hand_at(self.plant), Decimal("70.0000"))
        line = TapeLoad.objects.get(lot=self.doffs["D-3"]).issue.lines.get()
        self.assertEqual((line.lot, line.quantity, line.issue.work_order),
                         (self.doffs["D-3"], Decimal("25.0000"), self.run))

    def test_another_loom_is_its_own(self):
        self.load("D-1", "30", CreelSide.WARP, 9)
        load_tape(self.station, self.operator, self.l20, "D-2", "20", CreelSide.WARP,
                  at=at(TODAY, 9, 5))
        # Both looms weave the run; L-20's doff is not in L-17's roll.
        roll = self.weigh(when=at(TODAY, 10, 42))
        self.assertEqual([load.lot.code for load in tape_for(roll)], ["D-1"])


class WhatALoadMustBeTests(TapeLoadTestCase):
    def test_refused(self):
        with self.assertRaisesMessage(ValidationError, "No doff D-9"):
            self.load("D-9", "10", CreelSide.WARP, 9)
        with self.assertRaisesMessage(ValidationError, "has 100 kg in"):
            self.load("D-1", "101", CreelSide.WARP, 9)
        with self.assertRaisesMessage(ValidationError, "is not warp or weft"):
            self.load("D-1", "10", "side", 9)
        other = Item.objects.create(sku="OTHER", name="Other", uom=self.kg, tracking="lot")
        Lot.objects.create(item=other, code="X-1")
        with self.assertRaisesMessage(ValidationError, "does not weave it"):
            self.load("X-1", "10", CreelSide.WARP, 9)
        from .station import LineKind, LoomStation

        LoomStation.objects.filter(pk=self.station.pk).update(kind=LineKind.CONVERSION)
        self.station.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "is not a loom station"):
            self.load("D-1", "10", CreelSide.WARP, 9)


class WithdrawnTests(TapeLoadTestCase):
    def test_while_nothing_was_woven_from_it(self):
        self.two_rolls()
        woven = TapeLoad.objects.get(lot=self.doffs["D-3"])
        with self.assertRaisesMessage(ValidationError, "has come off L-17 since"):
            void_load(woven, self.station, self.supervisor, self.operator, "Wrong doff")
        late = self.load("D-3", "10", CreelSide.WEFT, 13, 30)
        with self.assertRaisesMessage(ValidationError, "Say why"):
            void_load(late, self.station, self.supervisor, self.operator, " ")
        void_load(late, self.station, self.supervisor, self.operator, "Wrong doff")
        self.assertEqual(self.doffs["D-3"].on_hand_at(self.plant), Decimal("75.0000"))
        with self.assertRaisesMessage(ValidationError, "already withdrawn"):
            void_load(late, self.station, self.supervisor, self.operator, "Again")
        # And a withdrawn load is in no roll's trace.
        third = self.weigh(when=at(TODAY, 15))
        self.assertEqual([load.lot.code for load in tape_for(third)], ["D-1", "D-3"])


class ALoadsIssueGoesWithItTests(TapeLoadTestCase):
    def test_not_voided_while_the_load_stands(self):
        # Voided on its own, the load still named the doff on the creel
        # with nothing issued for it, and could never be withdrawn.
        load = self.load("D-1", "30", CreelSide.WARP, 9)
        with self.assertRaisesMessage(ValidationError, "Void that, and this goes with it"):
            load.issue.void()
        self.assertEqual(self.doffs["D-1"].on_hand_at(self.plant), Decimal("70"))
        void_load(load, self.station, self.supervisor, self.operator, "Wrong doff")
        self.assertEqual(self.doffs["D-1"].on_hand_at(self.plant), Decimal("100"))


class ABackflushedLoomDrawsByTheDoffTests(TapeLoadTestCase):
    """
    Found by probing. On a run that backflushes, a load issues nothing and
    the output draws its own tape; but the draw named no batch, so a run
    on batch-kept tape could not book a roll at all, and a 100 kg doff
    could be loaded twice at 100 kg because neither load had left the
    shelf. The output now draws the doffs on the run's creels, by batch.
    """

    def setUp(self):
        super().setUp()
        WorkOrder.objects.filter(pk=self.run.pk).update(backflush=True)
        self.run.refresh_from_db()

    def test_a_doff_is_not_loaded_past_its_weight(self):
        self.load("D-1", "100", CreelSide.WARP, 9)
        with self.assertRaisesMessage(ValidationError, "has 0 kg in"):
            self.load("D-1", "100", CreelSide.WEFT, 9, 5)
        self.assertEqual(TapeLoad.objects.filter(lot=self.doffs["D-1"]).count(), 1)

    def test_its_roll_draws_the_doffs_on_its_creels(self):
        self.load("D-1", "100", CreelSide.WARP, 9)
        self.load("D-2", "100", CreelSide.WEFT, 9, 5)
        roll = self.weigh(when=at(TODAY, 10, 42))
        drawn = [(line.lot.code, line.quantity)
                 for line in roll.entry.backflush_issue.lines.order_by("line_number")]
        self.assertEqual([code for code, _ in drawn], ["D-1", "D-2"])
        self.assertEqual(drawn[0][1], Decimal("100"))
        self.assertEqual(self.doffs["D-1"].on_hand_at(self.plant), Decimal("0"))
        made = [lot for row in recall(self.doffs["D-1"])["descendants"] for lot in row["made"]]
        self.assertIn(roll.lot, made)

    def test_not_past_what_its_creels_hold(self):
        self.load("D-1", "30", CreelSide.WARP, 9)
        with self.assertRaisesMessage(ValidationError, "creels hold 30 kg of it not yet drawn"):
            self.weigh(when=at(TODAY, 10, 42))

    def test_a_load_its_output_drew_on_is_not_withdrawn(self):
        load = self.load("D-1", "100", CreelSide.WARP, 9)
        self.produce(self.run, "50", lot=Lot.objects.create(item=self.fabric, code="F-OFFICE")).post()
        with self.assertRaisesMessage(ValidationError, "output has drawn on"):
            void_load(load, self.station, self.supervisor, self.operator, "Wrong doff")


class TracedOverTheApiTests(TapeLoadTestCase):
    def test_a_roll_names_its_doffs(self):
        from django.contrib.auth.models import Permission, User
        from rest_framework.test import APIClient

        device = User.objects.create_user("loom-exit")
        device.user_permissions.add(Permission.objects.get(codename="weigh_at_station"))
        client = APIClient()
        client.force_authenticate(device)
        base = f"/api/manufacturing/stations/{self.station.code}/"
        client.post(base + "sign-in/", {"pin": self.operator.issue_pin()}, format="json")
        response = client.post(base + "load-tape/", {"machine": "L-17", "doff": "D-1",
                                                     "kg": "30", "side": "warp"},
                               format="json")
        self.assertEqual(response.status_code, 201, response.content)
        # Loaded now, over the API; the roll comes off after it.
        import datetime

        from django.utils import timezone

        roll = self.weigh(when=timezone.now() + datetime.timedelta(minutes=5))
        office = APIClient()
        office.force_authenticate(User.objects.create_superuser("planner"))
        body = office.get(f"/api/manufacturing/lot-trace/{roll.lot_id}/doffs/").json()
        self.assertEqual([(row["doff"], row["side"]) for row in body["doffs"]],
                         [("D-1", "warp")])

    def test_a_roll_booked_in_the_office_says_why_it_has_none(self):
        # Found crawling every endpoint over each test's data: a roll with
        # no time on a loom crashed the trace. There is no moment to read
        # the creel at, which is an answer, not a server error.
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        from .rolls import FabricRoll

        roll = self.weigh()
        FabricRoll.objects.filter(pk=roll.pk).update(weighed_at=None)
        office = APIClient()
        office.force_authenticate(User.objects.create_superuser("planner"))
        response = office.get(f"/api/manufacturing/lot-trace/{roll.lot_id}/doffs/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("was not weighed at a loom", response.content.decode())


class TheEdgesTests(TapeLoadTestCase):
    """Found by mutation: each guard here once survived being removed."""

    def test_only_a_loom_this_station_loads(self):
        from .machines import Machine

        other = Machine.objects.create(work_centre=self.centre, code="L-99")
        with self.assertRaisesMessage(ValidationError, "does not load L-99"):
            load_tape(self.station, self.operator, other, "D-1", "10", CreelSide.WARP,
                      at=at(TODAY, 9))

    def test_a_backflushed_run_draws_its_own(self):
        from .orders import WorkOrder

        WorkOrder.objects.filter(pk=self.run.pk).update(backflush=True)
        load = self.load("D-1", "30", CreelSide.WARP, 9)
        self.assertIsNone(load.issue)
        self.assertEqual(self.doffs["D-1"].on_hand_at(self.plant), Decimal("100"))

    def test_withdrawn_where_it_was_loaded_by_a_supervisor(self):
        from .station import LoomStation

        load = self.load("D-1", "30", CreelSide.WARP, 9)
        other = LoomStation.objects.create(code="LX-9", name="Other", warehouse=self.plant)
        with self.assertRaisesMessage(ValidationError, "was not loaded at LX-9"):
            void_load(load, other, self.supervisor, self.operator, "x")
        with self.assertRaisesMessage(ValidationError, "approved by somebody else"):
            void_load(load, self.station, self.operator, self.operator, "x")
