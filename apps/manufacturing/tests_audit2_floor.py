"""
Audit 2, floor lane: probes that assert what SHOULD be true. Each failing one is a finding.
Figures come from calc_audit2_floor/ (plain Python), never from output.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.inventory.models import Lot

from .orders import WorkOrder
from .rebatch import rebatch
from .tape_loads import CreelSide
from .tests_orders import TODAY
from .tests_station import at
from .tests_tape_loads import TapeLoadTestCase


class F1LockoutTests(TapeLoadTestCase):
    def test_a_login_with_a_valid_pin_cannot_guess_at_will(self):
        """80 wrong guesses at the station in ten minutes, a right PIN between every four, never lock it."""
        pin = self.operator.issue_pin()
        wrong = "999999" if pin != "999999" else "888888"
        now = at(TODAY, 10, 0)
        refused = 0
        for _ in range(20):
            for _ in range(4):
                try:
                    self.station.identify(wrong, now=now)
                except ValidationError as exc:
                    refused += 1 if "locked" not in str(exc) else 0
            try:
                self.station.identify(pin, now=now)
            except ValidationError:
                pass  # locked: the right PIN waits out the lock too
        self.assertLess(refused, 10, f"{refused} wrong PINs tried inside one ten-minute window, no lock")


class F2PromisedTapeTests(TapeLoadTestCase):
    def test_tape_on_a_backflushed_creel_is_not_rebatched_away(self):
        WorkOrder.objects.filter(pk=self.run.pk).update(backflush=True)
        self.load("D-1", "100", CreelSide.WARP, 9)
        a = Lot.objects.create(item=self.tape, code="N-1")
        b = Lot.objects.create(item=self.tape, code="N-2")
        with self.assertRaises(ValidationError):
            rebatch(self.tape, self.plant, [(self.doffs["D-1"], "100")], [(a, "60"), (b, "40")], "test")


from django.contrib.auth.models import Group, User  # noqa: E402
from django.core.management import call_command  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from .station import LoomStation  # noqa: E402
from .tests_station_api import StationApiTestCase  # noqa: E402


class F3ForgedScaleTests(StationApiTestCase):
    def test_the_tablet_login_cannot_make_a_scale_reading_of_its_own(self):
        """The Station role is the login on the tablet. A bridged station's scale weight is what the
        scale reported; the tablet's own login posting the reading is a typed weight with no
        supervisor's PIN."""
        call_command("setup_roles", verbosity=0)
        LoomStation.objects.filter(pk=self.station.pk).update(scale_code="SC-1", scale_bridged=True)
        tablet = User.objects.create_user("tablet")
        tablet.groups.add(Group.objects.get(name="Station"))
        self.client.force_authenticate(tablet)
        forged = self.client.post("/api/manufacturing/scale-readings/",
                                  {"scale": "SC-1", "gross_kg": "106.8", "stable": True}, format="json")
        self.assertEqual(forged.status_code, 403, "the tablet login posted a scale reading itself")


class F4WeighedRollTests(StationApiTestCase):
    def supervisor_client(self):
        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("sup")
        user.groups.add(Group.objects.get(name="Production Supervisor"))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_a_weighed_rolls_declared_metres_are_not_edited_over_the_api(self):
        """The roll's metres are what the weaver's piece pay and the exception flag stand on."""
        roll = self.weigh(declared="1006")
        client = self.supervisor_client()
        answer = client.patch(f"/api/manufacturing/fabric-rolls/{roll.pk}/", {"length_m": "2000"}, format="json")
        roll.refresh_from_db()
        self.assertEqual((answer.status_code >= 400, roll.length_m), (True, Decimal("1006")),
                         f"status {answer.status_code}; length_m is now {roll.length_m}")

    def test_a_weighed_roll_is_not_deleted_over_the_api(self):
        from .rolls import FabricRoll

        roll = self.weigh()
        client = self.supervisor_client()
        answer = client.delete(f"/api/manufacturing/fabric-rolls/{roll.pk}/")
        self.assertTrue(FabricRoll.objects.filter(pk=roll.pk).exists(),
                        f"status {answer.status_code}; the roll record is gone and its {roll.net_weight_kg} kg are still on the shelf")


class F5PostedFloorDocumentsTests(F4WeighedRollTests):
    def test_a_posted_production_entry_is_not_deleted_or_edited_over_the_api(self):
        from .orders import ProductionEntry

        roll = self.weigh()
        entry = roll.entry
        client = self.supervisor_client()
        patched = client.patch(f"/api/manufacturing/production-entries/{entry.pk}/",
                               {"quantity_produced": "1"}, format="json")
        deleted = client.delete(f"/api/manufacturing/production-entries/{entry.pk}/")
        entry = ProductionEntry.objects.filter(pk=entry.pk).first()
        self.assertTrue(entry is not None and entry.quantity_produced == roll.net_weight_kg,
                        f"patch {patched.status_code}, delete {deleted.status_code}")


from .costing import CostVersion, StandardCost  # noqa: E402
from .station_scale import ScaleReading  # noqa: E402
from .station_tape import record_tape  # noqa: E402
from .tests_station_tape import NOON, ON, TapeTestCase  # noqa: E402


class F6BobbinsAtABridgedStationTests(TapeTestCase):
    def test_a_bobbin_count_that_is_not_a_number_is_a_refusal_not_a_crash(self):
        """A net frame asks for the tare through a lambda that runs int(bobbins) before it is checked."""
        from django.utils import timezone

        LoomStation.objects.filter(pk=self.tx.pk).update(scale_code="SC-9", scale_bridged=True)
        self.tx.refresh_from_db()
        ScaleReading.objects.create(scale_code="SC-9", net_kg=Decimal("56"), stable=True,
                                    read_at=NOON)
        try:
            record_tape(self.tx, self.operator, self.e1, None, "eight", self.bobbin, ON, at=NOON)
        except ValidationError:
            return
        except Exception as exc:  # noqa: BLE001
            self.fail(f"{type(exc).__name__}: {exc} (an uncaught error, a 500 over the API)")
        self.fail("booked")


class F7PublishedStandardsTests(TapeTestCase):
    def published(self):
        version = CostVersion.objects.create(code="V1", name="Std", effective_from=TODAY)
        StandardCost.objects.create(version=version, item=self.virgin, material=Decimal("100"))
        version.publish(on_date=TODAY)
        return version

    def test_a_published_standard_is_not_edited(self):
        version = self.published()
        row = version.costs.get()
        row.material = Decimal("999")
        with self.assertRaises(ValidationError):
            row.save()

    def test_a_published_version_is_not_deleted(self):
        version = self.published()
        with self.assertRaises((ValidationError, Exception)):
            version.delete()
        self.assertTrue(CostVersion.objects.filter(pk=version.pk).exists())


class F8ShiftCodesThatShareALetterTests(TapeTestCase):
    def test_the_first_roll_of_each_shift_is_weighed_when_shifts_are_s1_and_s2(self):
        """A roll's code carries only the first letter of its shift's code, and the count is per shift."""
        from .shifts import Shift

        Shift.objects.filter(pk=self.day.pk).update(code="S1")
        Shift.objects.filter(pk=self.night.pk).update(code="S2")
        self.weigh(when=at(TODAY, 10, 42))
        try:
            self.weigh(when=at(TODAY, 21, 0))
        except Exception as exc:  # noqa: BLE001
            self.fail(f"second shift's first roll: {type(exc).__name__}: {str(exc)[:120]}")


from .bom import BillOfMaterials, BomComponent  # noqa: E402
from .tests_backflush import ReworkTestCase  # noqa: E402


class F9ReworkRecipeNeverEatsItsBatchTests(ReworkTestCase):
    def test_a_run_on_a_rework_recipe_with_no_line_for_the_failed_batch_is_not_released(self):
        """BillOfMaterials.check_rework says so and nothing calls it: the run draws no failed tape and
        books 480 kg of good tape out of 4 kg of polymer, leaving the failed 500 kg on the shelf."""
        empty = BillOfMaterials.objects.create(
            item=self.tape, name="Bad rework", version=3, quantity_produced=Decimal("100"),
            uom=self.kg, is_default=False, is_rework=True)
        BomComponent.objects.create(bom=empty, item=self.virgin, quantity=Decimal("4"), uom=self.kg,
                                    line_number=1)
        lot = self.failed_lot()
        order = WorkOrder.objects.create(item=self.tape, bom=empty, quantity_ordered=Decimal("480"),
                                         uom=self.kg, warehouse=self.plant, rework_of=lot)
        with self.assertRaises(ValidationError):
            order.release(TODAY)


from .station import CoreType  # noqa: E402
from .tests_station_tape import TapeApiTests  # noqa: E402


class F11RetiredCoreAtTheTapeLineTests(TapeApiTests):
    test_doffed_and_withdrawn_at_the_station = None
    test_the_station_is_told_a_doff_is_held_for_the_quality_manager = None

    def test_a_core_type_taken_out_of_use_is_not_taken_for_a_doff(self):
        """The loom exit asks is_active=True; the tape line, film line and roll-off look the code up bare."""
        CoreType.objects.filter(code="BOB").update(is_active=False)
        self.client.post(self.base + "sign-in/", {"pin": self.pin}, format="json")
        answer = self.client.post(self.base + "tape/", {
            "machine": "E-1", "gross_kg": "60.0", "bobbins": 8, "core": "BOB", "denier": ON}, format="json")
        self.assertGreaterEqual(answer.status_code, 400, "a doff was netted against a retired bobbin")


from .tests_costing import CostingTestCase  # noqa: E402


class F12RollingUpTwiceTests(CostingTestCase):
    def test_a_version_with_a_loop_is_rolled_up_again_after_a_price_moves(self):
        """roll_up overwrites the stated price that breaks the loop with the rolled figure, so the
        second roll-up of the same draft version finds no stated price and refuses."""
        from .tests_costing import RollingItUpTests

        RollingItUpTests.loop(self)
        version = self.version()
        version.roll_up([self.regrind])
        row = version.costs.get(item=self.virgin)
        row.material = Decimal("104")
        row.save()
        try:
            version.roll_up([self.regrind])
        except ValidationError as exc:
            self.fail(f"second roll-up refused: {str(exc)[:100]}")
