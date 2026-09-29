"""
A fabric roll off L-17 — 104.4 kg net, 1,006 m — laminated on K-1, some
printed on the flexo P-1, and cut on the BCS C-2.

Mounted at the laminator it is issued to the laminated-sack run by batch.
1,000 m come off it laminated. The fabric in them weighs 104.4 / 1,006
kg a metre, 103.777 kg; the sack's coating is 18 GSM over 1.2 m² of
fabric a metre, 1,200 m², so 21.6 kg. Off at 125.38 kg net it added
18.002 GSM: inside 16.2 to 19.8. At 130.38 kg it added 22.169: off,
taken only by a supervisor with a reason.

A bundle cut on C-2 while that laminated roll is mounted names it, and
it names the fabric roll.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.inventory.models import Item, Lot

from .machines import Machine
from .orders import WorkCentre, WorkOrderOperation
from .process_rolls import (
    ProcessRoll,
    RollKind,
    RollMount,
    dismount_roll,
    mount_roll,
    roll_chain,
    void_mount,
    void_roll,
    weigh_roll,
)
from .station import LineKind, LoomStation
from .tests_orders import TODAY
from .tests_station import at
from .tests_station_coat import CoatingTestCase

LATER = at(TODAY, 11)


class RollsTestCase(CoatingTestCase):
    def setUp(self):
        super().setUp()
        from apps.quality.models import InspectionPlan

        # The fabric's generated GSM plan is mandatory, and a roll weighed at
        # the loom exit is not an inspection: made advisory here, as a plant
        # that does not inspect each roll would, so rolls may be mounted.
        InspectionPlan.objects.filter(item=self.fabric).update(is_mandatory=False)
        self.fabric_roll = self.weigh()
        LoomStation.objects.filter(pk=self.cv.pk).update(kind=LineKind.CONVERSION)
        self.cv.refresh_from_db()
        self.fabric_lot = self.fabric_roll.lot
        # The laminated run's cutting on C-2, so C-1's plain sacks stay apart.
        WorkOrderOperation.objects.filter(work_order=self.lam_run, name="Cut and stitch").update(
            machine=self.c2)

    def mount(self, code=None, station=None, machine=None, when=None):
        return mount_roll(station or self.kx, self.operator, machine or self.k1,
                          code or self.fabric_lot.code, at=when or at(TODAY, 10, 50))

    def laminated(self, gross="127.78", **extra):
        return weigh_roll(self.kx, self.operator, self.k1, gross, self.core, "1000",
                          at=LATER, **extra)


class MountedTests(RollsTestCase):
    def test_a_stock_roll_is_issued_to_the_run_by_its_batch(self):
        mount = self.mount()
        line = mount.issue.lines.get()
        self.assertEqual((line.lot, line.quantity, mount.issue.work_order, mount.operation.name),
                         (self.fabric_lot, Decimal("104.4000"), self.lam_run, "Coat"))
        self.assertEqual(self.fabric_lot.on_hand_at(self.plant), Decimal("0"))

    def test_what_cannot_be_mounted(self):
        with self.assertRaisesMessage(ValidationError, "No roll NOPE"):
            self.mount("NOPE")
        other = Lot.objects.create(item=self.tape_item(), code="TAPE-9")
        with self.assertRaisesMessage(ValidationError, "does not use it"):
            self.mount("TAPE-9")
        self.assertIsNotNone(other)
        self.mount()
        dismount_roll(self.kx, self.operator, self.k1, at=at(TODAY, 10, 55))
        with self.assertRaisesMessage(ValidationError, "is not in"):
            self.mount()
        with self.assertRaisesMessage(ValidationError, "is not a station that mounts rolls"):
            mount_roll(self.station, self.operator, self.l17, self.fabric_lot.code,
                       at=at(TODAY, 10, 50))

    def test_the_next_roll_finishes_the_one_before(self):
        first = self.mount()
        second_roll = self.weigh(when=at(TODAY, 10, 45))
        second = self.mount(second_roll.lot.code, when=at(TODAY, 11, 30))
        first.refresh_from_db()
        self.assertEqual((first.finished_at, second.finished_at), (at(TODAY, 11, 30), None))

    def tape_item(self):
        return Item.objects.create(sku="TAPE-X", name="Tape", uom=self.kg)


class TakenOffTests(RollsTestCase):
    def test_what_is_left_goes_back_to_the_store(self):
        mount = self.mount()
        with self.assertRaisesMessage(ValidationError, "more than the 104.4000"):
            dismount_roll(self.kx, self.operator, self.k1, remaining_kg="105",
                          at=at(TODAY, 10, 55))
        dismount_roll(self.kx, self.operator, self.k1, remaining_kg="20", at=at(TODAY, 10, 55))
        mount.refresh_from_db()
        self.assertEqual((self.fabric_lot.on_hand_at(self.plant), mount.returned.lines.get().lot),
                         (Decimal("20.0000"), self.fabric_lot))
        with self.assertRaisesMessage(ValidationError, "Nothing is mounted"):
            dismount_roll(self.kx, self.operator, self.k1, at=at(TODAY, 10, 56))

    def test_a_roll_made_here_has_nothing_to_return(self):
        self.mount()
        roll = self.laminated()
        mount_roll(self.cv, self.operator, self.c2, roll.code, at=at(TODAY, 11, 30))
        with self.assertRaisesMessage(ValidationError, "was not issued from the store"):
            dismount_roll(self.cv, self.operator, self.c2, remaining_kg="5",
                          at=at(TODAY, 11, 40))


class LaminatedTests(RollsTestCase):
    def test_weighed_against_what_the_sack_adds(self):
        self.mount()
        roll = self.laminated()
        self.assertEqual((roll.kind, roll.net_kg, roll.added_gsm, roll.expected_gsm,
                          roll.lower_gsm, roll.upper_gsm, roll.passed),
                         (RollKind.COATED, Decimal("125.380"), Decimal("18.002"),
                          Decimal("18.000"), Decimal("16.200"), Decimal("19.800"), True))
        self.assertEqual(roll.code, "LM-260601-D-K1-01")
        self.assertTrue(self.laminated("125.74").passed)

    def test_off_its_weight_only_with_a_supervisor_and_a_reason(self):
        self.mount()
        with self.assertRaisesMessage(ValidationError, "needs a supervisor's PIN"):
            self.laminated("132.78")
        with self.assertRaisesMessage(ValidationError, "Say why a roll off its lamination"):
            self.laminated("132.78", supervisor=self.supervisor)
        roll = self.laminated("132.78", supervisor=self.supervisor, reason="Die lip cleaned")
        self.assertEqual((roll.passed, roll.added_gsm, roll.conceded_by),
                         (False, Decimal("22.169"), self.supervisor))

    def test_what_a_roll_off_must_be(self):
        with self.assertRaisesMessage(ValidationError, "mount the roll first"):
            self.laminated()
        self.mount()
        with self.assertRaisesMessage(ValidationError, "is not more than the"):
            self.laminated("2")
        with self.assertRaisesMessage(ValidationError, "is not a station that weighs rolls off"):
            weigh_roll(self.cv, self.operator, self.c2, "127.78", self.core, "1000", at=LATER)


class TheBundleKnowsItsRollTests(RollsTestCase):
    def test_bundle_to_laminated_roll_to_fabric_roll(self):
        from .conversion import record_bags

        self.mount()
        roll = self.laminated()
        mount_roll(self.cv, self.operator, self.c2, roll.code, at=at(TODAY, 11, 30))
        grams = str(self.lam_spec.bag_grams().quantize(Decimal("0.001")))
        count = record_bags(self.cv, self.operator, self.c2, "500", [grams] * 10,
                            at=at(TODAY, 12))
        self.assertEqual([step["code"] for step in roll_chain(count.mount)],
                         [roll.code, self.fabric_lot.code])
        with self.assertRaisesMessage(ValidationError, "has already been mounted"):
            mount_roll(self.kx, self.operator, self.k1, roll.code, at=at(TODAY, 12, 5))


class WithdrawnTests(RollsTestCase):
    def test_a_mount_goes_back_while_nothing_was_made_from_it(self):
        mount = self.mount()
        with self.assertRaisesMessage(ValidationError, "Say why"):
            void_mount(mount, self.kx, self.supervisor, self.operator, " ")
        roll = self.laminated()
        with self.assertRaisesMessage(ValidationError, "Something was made from"):
            void_mount(mount, self.kx, self.supervisor, self.operator, "Wrong roll")
        void_roll(roll, self.kx, self.supervisor, self.operator, "Wrong roll")
        void_mount(mount, self.kx, self.supervisor, self.operator, "Wrong roll")
        self.assertEqual(self.fabric_lot.on_hand_at(self.plant), Decimal("104.4000"))
        with self.assertRaisesMessage(ValidationError, "already withdrawn"):
            void_mount(mount, self.kx, self.supervisor, self.operator, "Again")
        with self.assertRaisesMessage(ValidationError, "was withdrawn"):
            mount_roll(self.cv, self.operator, self.c2, roll.code, at=at(TODAY, 11, 30))

    def test_a_returned_remainder_goes_back_with_it(self):
        mount = self.mount()
        dismount_roll(self.kx, self.operator, self.k1, remaining_kg="20", at=at(TODAY, 10, 55))
        mount.refresh_from_db()
        void_mount(mount, self.kx, self.supervisor, self.operator, "Wrong roll")
        self.assertEqual(self.fabric_lot.on_hand_at(self.plant), Decimal("104.4000"))

    def test_a_roll_is_withdrawn_only_while_it_is_not_mounted(self):
        self.mount()
        roll = self.laminated()
        mount = mount_roll(self.cv, self.operator, self.c2, roll.code, at=at(TODAY, 11, 30))
        with self.assertRaisesMessage(ValidationError, "has been mounted since"):
            void_roll(roll, self.kx, self.supervisor, self.operator, "x")
        with self.assertRaisesMessage(ValidationError, "was not weighed at"):
            void_roll(roll, self.cv, self.supervisor, self.operator, "x")
        with self.assertRaisesMessage(ValidationError, "was not mounted at"):
            void_mount(mount, self.kx, self.supervisor, self.operator, "x")


class FlexoTests(RollsTestCase):
    def test_a_printed_roll_names_the_laminated_one(self):
        press = WorkCentre.objects.create(code="FLEXO", name="Flexo")
        p1 = Machine.objects.create(work_centre=press, code="P-1")
        WorkOrderOperation.objects.bulk_create([WorkOrderOperation(
            work_order=self.lam_run, sequence=15, name="Print", work_centre=press,
            units_per_hour=Decimal("3000"), planned_minutes=Decimal("100"))])
        px = LoomStation.objects.create(code="PX-1", name="Flexo", warehouse=self.plant,
                                        kind=LineKind.PRINTING)
        px.machines.set([p1])
        self.mount()
        laminated = self.laminated()
        mount_roll(px, self.operator, p1, laminated.code, at=at(TODAY, 11, 30))
        printed = weigh_roll(px, self.operator, p1, "127.9", self.core, "998",
                             at=at(TODAY, 12))
        self.assertEqual((printed.kind, printed.added_gsm, printed.passed, printed.code[:3]),
                         (RollKind.PRINTED, None, None, "PR-"))
        mount = mount_roll(self.cv, self.operator, self.c2, printed.code, at=at(TODAY, 12, 30))
        self.assertEqual([step["code"] for step in roll_chain(mount)],
                         [printed.code, laminated.code, self.fabric_lot.code])
        self.assertEqual(ProcessRoll.objects.count(), 2)
        self.assertEqual(RollMount.objects.filter(voided_at__isnull=True).count(), 3)


class RollsApiTests(RollsTestCase):
    def test_mounted_weighed_and_withdrawn_at_the_station(self):
        from django.contrib.auth.models import Permission, User
        from rest_framework.test import APIClient

        device = User.objects.create_user("coater")
        device.user_permissions.add(Permission.objects.get(codename="weigh_at_station"))
        client = APIClient()
        client.force_authenticate(device)
        pin, supervisor_pin = self.operator.issue_pin(), self.supervisor.issue_pin()
        base = f"/api/manufacturing/stations/{self.kx.code}/"
        client.post(base + "sign-in/", {"pin": pin}, format="json")
        response = client.post(base + "mount/", {"machine": "K-1",
                                                 "roll": self.fabric_lot.code}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        mount = response.json()
        response = client.post(base + "roll-off/", {"machine": "K-1", "gross_kg": "127.78",
                                                    "core": "C-76", "metres": "1000"},
                               format="json")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["kind"], body["added_gsm"], body["passed"]),
                         ("coated", "18.002", True))
        response = client.post(base + f"roll-off/{body['id']}/void/",
                               {"supervisor_pin": supervisor_pin, "reason": "Test"},
                               format="json")
        self.assertEqual(response.status_code, 200, response.content)
        response = client.post(base + "dismount/", {"machine": "K-1", "remaining_kg": "10"},
                               format="json")
        self.assertEqual(response.status_code, 200, response.content)
        response = client.post(base + f"mount/{mount['id']}/void/",
                               {"supervisor_pin": supervisor_pin, "reason": "Test"},
                               format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.fabric_lot.on_hand_at(self.plant), Decimal("104.4000"))


class WhatTheMachineMayTakeTests(RollsTestCase):
    """Found by mutation: each guard here once survived being removed."""

    def test_a_roll_made_for_one_run_does_not_go_into_another(self):
        self.mount()
        roll = self.laminated()
        # C-1 is on the plain sack's cutting; the laminated roll is not for it.
        step = (WorkOrderOperation.objects.filter(work_centre=self.cutting)
                .exclude(work_order=self.lam_run).order_by("pk").first())
        WorkOrderOperation.objects.filter(pk=step.pk).update(machine=self.c1)
        with self.assertRaisesMessage(ValidationError, "was made for"):
            mount_roll(self.cv, self.operator, self.c1, roll.code, at=at(TODAY, 11, 30))

    def test_a_backflushed_run_is_not_issued_the_roll(self):
        from .orders import WorkOrder

        WorkOrder.objects.filter(pk=self.lam_run.pk).update(backflush=True)
        mount = self.mount()
        self.assertIsNone(mount.issue)
        self.assertEqual(self.fabric_lot.on_hand_at(self.plant), Decimal("104.4000"))

    def test_withdrawn_once(self):
        self.mount()
        roll = self.laminated()
        void_roll(roll, self.kx, self.supervisor, self.operator, "Wrong roll")
        with self.assertRaisesMessage(ValidationError, "already withdrawn"):
            void_roll(roll, self.kx, self.supervisor, self.operator, "Again")


class WhatARollOffMustBeTests(RollsTestCase):
    def test_the_core_alone_is_not_a_roll(self):
        # The 76 mm core weighs 2.4 kg: 2.4 on the scale is nothing on it.
        self.mount()
        with self.assertRaisesMessage(ValidationError, "is not more than the"):
            self.laminated("2.4")

    def test_below_the_lower_limit_is_off_as_above_the_upper(self):
        # 123.097 net: (123.097 - 103.777) x 1000 / 1200 = 16.100, under 16.2;
        # 123.22 net gives 16.202, just inside.
        self.mount()
        with self.assertRaisesMessage(ValidationError, "needs a supervisor's PIN"):
            self.laminated("125.497")
        self.assertEqual(self.laminated("125.62").added_gsm, Decimal("16.202"))

    def test_an_unlaminated_sack_has_nothing_to_weigh(self):
        from .woven import BagSpecification

        self.mount()
        BagSpecification.objects.filter(pk=self.lam_spec.pk).update(is_laminated=False,
                                                                  lamination_gsm=0)
        with self.assertRaisesMessage(ValidationError, "is not laminated"):
            self.laminated()

    def test_a_bought_roll_has_no_weight_a_metre_to_weigh_against(self):
        # Fabric bought in has a batch but no loom-exit weighing.
        bought = Lot.objects.create(item=self.fabric, code="BOUGHT-1")
        self.stock(self.fabric, "100", "150", lot=bought)
        self.mount("BOUGHT-1")
        with self.assertRaisesMessage(ValidationError, "never weighed and measured"):
            self.laminated()

    def test_film_names_the_roll(self):
        # 20 micron BOPP over both faces is 20 x 0.91 = 18.2 GSM on top of
        # the 18 of coating; 12 micron metallic over the front face alone
        # is 12 x 0.91 / 2 = 5.46.
        from .woven import BagSpecification

        film = Item.objects.create(sku="FILM", name="Film", uom=self.kg)
        specs = BagSpecification.objects.filter(pk=self.lam_spec.pk)
        self.mount()
        specs.update(bopp_film_item=film, bopp_micron=Decimal("20"), bopp_faces=2)
        roll = self.laminated(supervisor=self.supervisor, reason="Film trial")
        self.assertEqual((roll.kind, roll.expected_gsm, roll.code[:3]),
                         (RollKind.BOPP, Decimal("36.200"), "BP-"))
        specs.update(bopp_film_item=None, bopp_micron=0, metallic_film_item=film,
                     metallic_micron=Decimal("12"), metallic_coverage_percent=Decimal("100"))
        roll = self.laminated(supervisor=self.supervisor, reason="Film trial")
        self.assertEqual((roll.kind, roll.expected_gsm, roll.code[:3]),
                         (RollKind.METALLIC, Decimal("23.460"), "MT-"))

    def test_held_to_the_sacks_own_tolerance(self):
        # 5% of 18 is 17.1 to 18.9.
        from .woven import BagSpecification

        BagSpecification.objects.filter(pk=self.lam_spec.pk).update(
            lamination_tolerance_percent=Decimal("5"))
        self.mount()
        roll = self.laminated()
        self.assertEqual((roll.lower_gsm, roll.upper_gsm, roll.passed),
                         (Decimal("17.100"), Decimal("18.900"), True))


class BundleTraceApiTests(RollsTestCase):
    def test_a_bundle_names_its_rolls(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        from .conversion import record_bags

        self.mount()
        roll = self.laminated()
        mount_roll(self.cv, self.operator, self.c2, roll.code, at=at(TODAY, 11, 30))
        grams = str(self.lam_spec.bag_grams().quantize(Decimal("0.001")))
        count = record_bags(self.cv, self.operator, self.c2, "500", [grams] * 10,
                            at=at(TODAY, 12))
        office = APIClient()
        office.force_authenticate(User.objects.create_superuser("planner"))
        body = office.get(f"/api/manufacturing/lot-trace/{count.inspection.lot_id}/rolls/").json()
        self.assertEqual((body["cut_on"], [row["code"] for row in body["rolls"]]),
                         ("C-2", [roll.code, self.fabric_lot.code]))
        response = office.get(f"/api/manufacturing/lot-trace/{self.fabric_lot.pk}/rolls/")
        self.assertEqual(response.status_code, 400)
