"""
A doff off tape line E-1: 60.0 kg gross on 8 bobbins of 0.5 kg, so 56
kg of 1,000-denier tape. Denier checks of 1,002, 998 and 1,005 average
1,001.67, inside 950 to 1,050: the batch is inspected and released.
Checks averaging 1,085 are off: booked only with a supervisor and a
reason, as a concession. With a tenacity minimum on the specification,
the line's denier is not the whole inspection: it waits for the lab.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.inventory.models import Item, TrackingMode
from apps.quality.models import Disposition, Reading, ReleaseStatus
from apps.quality.release import release_status

from .machines import Machine
from .orders import WorkCentre, WorkOrder
from .routing import Routing, RoutingOperation
from .station import CoreType, LineKind, LoomStation
from .station_tape import record_tape, void_doff
from .tests_orders import TODAY
from .tests_station import StationTestCase, at
from .tests_station_api import StationApiTestCase
from .woven import TapeSpecification

NOON = at(TODAY, 12)
ON = ["1002", "998", "1005"]
OFF = ["1080", "1090", "1085"]


def build_tape_line(test, **spec_extra):
    test.extruder = WorkCentre.objects.create(code="EXT", name="Tape line")
    test.e1 = Machine.objects.create(work_centre=test.extruder, code="E-1")
    routing = Routing.objects.create(code="R-EXT", name="Extrude")
    RoutingOperation.objects.create(routing=routing, sequence=10, name="Extrude",
                                    work_centre=test.extruder, setup_minutes=Decimal("0"),
                                    units_per_hour=Decimal("400"), rate_uom=test.kg)
    test.tape_item = Item.objects.create(sku="TAPE-LOT", name="Tape by the batch",
                                         uom=test.kg, tracking=TrackingMode.LOT)
    values = dict(code="T-LINE", tape_item=test.tape_item, denier=Decimal("1000"),
                  tape_width_mm=Decimal("2.5"), virgin_granule=test.virgin,
                  filler_item=test.filler, filler_percent=Decimal("8"),
                  extrusion_waste_percent=Decimal("3"), waste_recovered_percent=Decimal("0"),
                  routing=routing)
    values.update(spec_extra)
    test.tape_spec = TapeSpecification.objects.create(**values)
    test.tape_run = WorkOrder.objects.create(item=test.tape_item, bom=test.tape_spec.bom,
                                             quantity_ordered=Decimal("2000"), uom=test.kg,
                                             warehouse=test.plant)
    test.tape_run.release(TODAY)
    test.bobbin = CoreType.objects.create(code="BOB", tare_kg=Decimal("0.5"))
    test.tx = LoomStation.objects.create(code="TX-1", name="Tape take-up",
                                         warehouse=test.plant, kind=LineKind.EXTRUSION)
    test.tx.machines.set([test.e1])
    test.tx.supervisors.add(test.supervisor)


class TapeTestCase(StationTestCase):
    def setUp(self):
        super().setUp()
        build_tape_line(self)

    def doff(self, readings=ON, **extra):
        return record_tape(self.tx, self.operator, self.e1, "60.0", 8, self.bobbin, readings,
                           at=NOON, **extra)


class DoffTests(TapeTestCase):
    def test_weighed_numbered_measured_and_released(self):
        doff = self.doff()
        self.assertEqual((doff.lot.code, doff.net_kg, doff.mean_denier),
                         ("TP-260601-D-E1-01", Decimal("56.000"), Decimal("1001.67")))
        self.assertEqual(doff.lot.on_hand_at(self.plant), Decimal("56"))
        self.assertEqual((doff.inspection.posted, release_status(doff.lot)),
                         (True, ReleaseStatus.RELEASED))
        self.assertEqual(self.doff().lot.code, "TP-260601-D-E1-02")

    def test_off_denier_only_with_a_supervisor_and_a_reason(self):
        with self.assertRaisesMessage(ValidationError, "needs a supervisor's PIN"):
            self.doff(OFF)
        with self.assertRaisesMessage(ValidationError, "Say why tape off its denier"):
            self.doff(OFF, supervisor=self.supervisor)
        doff = self.doff(OFF, supervisor=self.supervisor, reason="Customer accepts heavy")
        self.assertEqual((doff.inspection.disposition, doff.conceded_by),
                         (Disposition.CONCESSION, self.supervisor))
        self.assertEqual(release_status(doff.lot), ReleaseStatus.RELEASED)

    def test_what_a_doff_must_be(self):
        with self.assertRaisesMessage(ValidationError, "Check the denier 3 times; 2"):
            self.doff(ON[:2])
        with self.assertRaisesMessage(ValidationError, "at least one bobbin"):
            record_tape(self.tx, self.operator, self.e1, "60", 0, self.bobbin, ON, at=NOON)
        with self.assertRaisesMessage(ValidationError, "whole bobbins"):
            record_tape(self.tx, self.operator, self.e1, "60", "eight", self.bobbin, ON,
                        at=NOON)
        with self.assertRaisesMessage(ValidationError, "is not more than 8 empty bobbins"):
            record_tape(self.tx, self.operator, self.e1, "4", 8, self.bobbin, ON, at=NOON)
        with self.assertRaisesMessage(ValidationError, "is not a tape line's station"):
            record_tape(self.station, self.operator, self.l17, "60", 8, self.bobbin, ON,
                        at=NOON)

    def test_only_a_run_made_to_a_tape_specification(self):
        from .bom import BillOfMaterials

        plain = BillOfMaterials.objects.create(item=self.tape_item, name="By hand",
                                               quantity_produced=Decimal("100"), uom=self.kg,
                                               is_default=False, version=2,
                                               routing=self.tape_spec.bom.routing)
        WorkOrder.objects.filter(pk=self.tape_run.pk).update(bom=plain)
        with self.assertRaisesMessage(ValidationError, "is not made to a tape specification"):
            self.doff()

    def test_only_this_stations_doffs(self):
        doff = self.doff()
        other = LoomStation.objects.create(code="TX-2", name="Two", warehouse=self.plant,
                                           kind=LineKind.EXTRUSION)
        other.supervisors.add(self.supervisor)
        with self.assertRaisesMessage(ValidationError, "was not weighed at TX-2"):
            void_doff(doff, other, self.supervisor, self.operator, "x")

    def test_withdrawn_it_comes_off_the_shelf_and_its_inspection_with_it(self):
        doff = self.doff()
        with self.assertRaisesMessage(ValidationError, "approved by somebody else"):
            void_doff(doff, self.tx, self.operator, self.operator, "Wrong line")
        void_doff(doff, self.tx, self.supervisor, self.operator, "Wrong line")
        doff.inspection.refresh_from_db()
        self.assertEqual((doff.lot.on_hand_at(self.plant), doff.inspection.voided_at is None),
                         (Decimal("0"), False))
        with self.assertRaisesMessage(ValidationError, "already withdrawn"):
            void_doff(doff, self.tx, self.supervisor, self.operator, "Again")
        with self.assertRaisesMessage(ValidationError, "Say why"):
            void_doff(self.doff(), self.tx, self.supervisor, self.operator, " ")


class LabFinishesTests(StationTestCase):
    def setUp(self):
        super().setUp()
        build_tape_line(self, min_tenacity_gpd=Decimal("4.5"))

    def test_the_line_measures_denier_and_the_lab_the_rest(self):
        doff = record_tape(self.tx, self.operator, self.e1, "60.0", 8, self.bobbin, ON, at=NOON)
        self.assertEqual((doff.inspection.posted, release_status(doff.lot)),
                         (False, ReleaseStatus.UNINSPECTED))
        line = doff.inspection.plan.lines.get(derived_from="tenacity")
        for number in range(line.sample_size):
            Reading.objects.create(inspection=doff.inspection, plan_line=line,
                                   value=Decimal("4.8"), sample_reference=f"T{number}")
        doff.inspection.post()
        self.assertEqual(release_status(doff.lot), ReleaseStatus.RELEASED)

    def test_withdrawn_before_the_lab_its_open_inspection_goes(self):
        from apps.quality.models import Inspection

        doff = record_tape(self.tx, self.operator, self.e1, "60.0", 8, self.bobbin, ON, at=NOON)
        void_doff(doff, self.tx, self.supervisor, self.operator, "Wrong line")
        self.assertFalse(Inspection.objects.filter(lot=doff.lot).exists())


class TapeApiTests(StationApiTestCase):
    def setUp(self):
        super().setUp()
        build_tape_line(self)
        self.base = f"/api/manufacturing/stations/{self.tx.code}/"

    def test_doffed_and_withdrawn_at_the_station(self):
        self.client.post(self.base + "sign-in/", {"pin": self.pin}, format="json")
        self.assertEqual(self.client.get(self.base).json()["kind"], "extrusion")
        response = self.client.post(self.base + "tape/", {
            "machine": "E-1", "gross_kg": "60.0", "bobbins": 8, "core": "BOB",
            "denier": ON}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["net_kg"], body["mean_denier"], body["awaiting_lab"]),
                         ("56.000", "1001.67", False))
        response = self.client.post(self.base + f"tape/{body['id']}/void/", {
            "supervisor_pin": self.supervisor_pin, "reason": "Wrong line"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        response = self.client.post(self.base + "tape/", {
            "machine": "E-1", "gross_kg": "60.0", "bobbins": 8, "core": "BOB",
            "denier": OFF}, format="json")
        self.assertEqual(response.status_code, 400)
