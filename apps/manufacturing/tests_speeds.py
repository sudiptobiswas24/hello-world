"""
Machine speeds from how the machine is set and what it is making.

  Tape line rated 600 kg an hour, 240 tapes wound at 300 m a minute.
  1,000-denier tape: 240 x 1,000 g/9,000 m x 300 x 60 = 480 kg an hour,
  under the rating: the take-up sets the pace. 1,500 denier would wind
  720, so the extruder's 600 does. At 85 per cent: 408 and 510.
  1,000 kg of 1,000-denier tape with an hour's setup: 60 + 1,000/408 x
  60 = 207.0588 minutes.
  Circular loom at 180 rpm with 6 shuttles on a 10 x 10 mesh, 60 cm
  tube of 1,000-denier tape (87.4891 GSM, 104.9869 g a metre):
  164.5919 m an hour, 17.28 kg; at 90 per cent 15.552.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.planning.capacity import LoadBook

from .orders import SpeedBasis, WorkCentre
from .routing import Routing, RoutingOperation
from .tests_woven import WovenTestCase


class SpeedTestCase(WovenTestCase):
    def setUp(self):
        super().setUp()
        self.line = WorkCentre.objects.create(
            code="TAPE-1", name="Tape line", capacity_per_hour=Decimal("600"),
            capacity_uom=self.kg, speed_basis=SpeedBasis.TAPE_LINE, tape_ends=240,
            line_speed_m_per_min=Decimal("300"), efficiency_percent=Decimal("85"))
        self.loom = WorkCentre.objects.create(
            code="CL-1", name="Circular loom", speed_basis=SpeedBasis.CIRCULAR_LOOM,
            loom_rpm=Decimal("180"), shuttles=6, efficiency_percent=Decimal("90"))
        self.extrude = self.step(self.line, "R-TAPE")
        self.weave = self.step(self.loom, "R-LOOM")

    def step(self, centre, code):
        routing = Routing.objects.create(code=code, name=code)
        return RoutingOperation.objects.create(routing=routing, sequence=10, name=code,
                                               work_centre=centre,
                                               setup_minutes=Decimal("60"))


class TapeLineTests(SpeedTestCase):
    def test_the_take_up_sets_the_pace_below_the_rating(self):
        spec = self.tape(routing=self.extrude.routing)
        self.assertEqual(self.line.speed_for(spec.bom), (Decimal("480"), self.kg))
        self.assertEqual(self.extrude.rate(self.kg, spec.bom), Decimal("408"))
        self.assertEqual(self.extrude.rate(self.kg, spec.bom, achieved=False), Decimal("480"))
        self.assertEqual(round(self.extrude.minutes_for(Decimal("1000"), self.kg,
                                                        bom=spec.bom), 4),
                         Decimal("207.0588"))

    def test_the_extruder_caps_a_heavy_tape(self):
        spec = self.tape(denier=Decimal("1500"), routing=self.extrude.routing)
        self.assertEqual(self.extrude.rate(self.kg, spec.bom), Decimal("510"))

    def test_with_no_rating_the_take_up_alone(self):
        WorkCentre.objects.filter(pk=self.line.pk).update(capacity_per_hour=None)
        self.line.refresh_from_db()
        spec = self.tape(denier=Decimal("1500"), routing=self.extrude.routing)
        self.assertEqual(self.line.speed_for(spec.bom), (Decimal("720"), self.kg))

    def test_the_finite_plan_books_the_computed_speed(self):
        spec = self.tape(routing=self.extrude.routing)
        from apps.inventory.models import Warehouse

        book = LoadBook(Warehouse.objects.create(code="W", name="W"),
                        spec.bom.created_at.date(), spec.bom.created_at.date())
        minutes = book.run_minutes(self.extrude, self.tape_item, Decimal("1000"), self.kg,
                                   spec.bom)
        self.assertEqual(round(minutes, 4), Decimal("207.0588"))


class LoomTests(SpeedTestCase):
    def test_picks_a_minute_over_picks_a_metre_at_the_fabrics_weight(self):
        fabric = self.fabric(routing=self.weave.routing)
        rate, unit = self.loom.speed_for(fabric.bom)
        self.assertEqual((round(rate, 4), unit), (Decimal("17.2800"), self.kg))
        self.assertEqual(round(self.weave.rate(self.kg, fabric.bom), 4), Decimal("15.5520"))


class StatedTests(SpeedTestCase):
    def test_a_stated_rate_is_still_worn_down_by_efficiency(self):
        stated = WorkCentre.objects.create(code="PR-1", name="Printer",
                                           capacity_per_hour=Decimal("100"),
                                           capacity_uom=self.kg,
                                           efficiency_percent=Decimal("80"))
        step = self.step(stated, "R-PR")
        self.assertEqual((step.rate(self.kg), step.rate(self.kg, achieved=False)),
                         (Decimal("80"), Decimal("100")))

    def test_a_product_with_no_specification_runs_at_the_rating(self):
        from .bom import BillOfMaterials

        plain = BillOfMaterials.objects.create(item=self.tape_item, name="By hand",
                                               quantity_produced=Decimal("100"),
                                               uom=self.kg, routing=self.extrude.routing)
        self.assertEqual(self.extrude.rate(self.kg, plain), Decimal("510"))
        self.assertEqual(self.extrude.rate(self.kg), Decimal("510"))

    def test_a_timing_without_its_settings_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "needs tapes wound at once"):
            WorkCentre.objects.create(code="T-2", name="x", speed_basis=SpeedBasis.TAPE_LINE,
                                      line_speed_m_per_min=Decimal("300"))
        with self.assertRaisesMessage(ValidationError, "needs a line speed"):
            WorkCentre.objects.create(code="T-3", name="x", speed_basis=SpeedBasis.TAPE_LINE,
                                      tape_ends=240)
        with self.assertRaisesMessage(ValidationError, "needs revolutions a minute"):
            WorkCentre.objects.create(code="L-2", name="x",
                                      speed_basis=SpeedBasis.CIRCULAR_LOOM, shuttles=6)
        with self.assertRaisesMessage(ValidationError, "needs a number of shuttles"):
            WorkCentre.objects.create(code="L-3", name="x",
                                      speed_basis=SpeedBasis.CIRCULAR_LOOM,
                                      loom_rpm=Decimal("180"), shuttles=0)


class StatedMachineMakingFabricTests(SpeedTestCase):
    def test_a_stated_machine_is_not_timed_as_a_loom(self):
        printer = WorkCentre.objects.create(code="PR-2", name="Stated",
                                            capacity_per_hour=Decimal("50"),
                                            capacity_uom=self.kg)
        step = self.step(printer, "R-PR2")
        fabric = self.fabric(routing=self.weave.routing)
        self.assertEqual(step.rate(self.kg, fabric.bom), Decimal("50"))


from .tests_orders import TODAY, RunTestCase  # noqa: E402


class ReleasedRunTests(RunTestCase):
    def test_the_run_is_planned_at_what_the_line_achieves_and_judged_at_its_rating(self):
        # 180 kg an hour rated, 90 per cent achieved: 1,000 kg is 30
        # minutes' setup and 1,000 / 162 hours: 400.3704 minutes, stored
        # at the column's two places.
        WorkCentre.objects.filter(pk=self.loom.pk).update(
            capacity_per_hour=Decimal("180"), capacity_uom=self.kg,
            efficiency_percent=Decimal("90"))
        routing = Routing.objects.create(code="R-X", name="Extrude")
        RoutingOperation.objects.create(routing=routing, sequence=10, name="Extrude",
                                        work_centre=self.loom, setup_minutes=Decimal("30"))
        self.bom.routing = routing
        self.bom.save()
        run = self.order()
        run.release(TODAY)
        operation = run.operations.get()
        self.assertEqual((operation.units_per_hour, operation.planned_minutes),
                         (Decimal("180.0000"), Decimal("400.37")))

    def test_a_tape_run_is_planned_at_the_speed_its_denier_winds(self):
        from apps.inventory.models import Item, TrackingMode

        from .woven import TapeSpecification

        WorkCentre.objects.filter(pk=self.loom.pk).update(
            capacity_per_hour=Decimal("600"), capacity_uom=self.kg,
            efficiency_percent=Decimal("85"), speed_basis=SpeedBasis.TAPE_LINE,
            tape_ends=240, line_speed_m_per_min=Decimal("300"))
        routing = Routing.objects.create(code="R-T", name="Tape")
        RoutingOperation.objects.create(routing=routing, sequence=10, name="Extrude",
                                        work_centre=self.loom, setup_minutes=Decimal("30"))
        item = Item.objects.create(sku="TAPE-S", name="Tape", uom=self.kg,
                                   tracking=TrackingMode.LOT)
        spec = TapeSpecification.objects.create(
            code="TS", tape_item=item, denier=Decimal("1000"), tape_width_mm=Decimal("2.5"),
            virgin_granule=self.virgin, filler_item=self.filler, filler_percent=Decimal("8"),
            extrusion_waste_percent=Decimal("3"), waste_recovered_percent=Decimal("0"),
            routing=routing)
        from .orders import WorkOrder

        run = WorkOrder.objects.create(item=item, bom=spec.bom, quantity_ordered=Decimal("1000"),
                                       uom=self.kg, warehouse=self.plant, work_centre=self.loom)
        run.release(TODAY)
        operation = run.operations.get()
        # Wound at 480 an hour, achieved at 408: 30 + 1,000 / 408 x 60.
        self.assertEqual((operation.units_per_hour, operation.planned_minutes),
                         (Decimal("480.0000"), Decimal("177.06")))


class SpeedApiTests(SpeedTestCase):
    def test_set_through_the_api_and_refused_without_its_settings(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("works"))
        response = client.patch(f"/api/manufacturing/work-centres/{self.line.pk}/",
                                {"efficiency_percent": "90"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        response = client.post("/api/manufacturing/work-centres/", {
            "code": "T-9", "name": "New line", "speed_basis": "tape_line",
            "available_hours_per_day": "24", "working_days": "1234567"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("needs tapes wound at once", response.content.decode())
