"""
A laminated 60 x 100 cm sack, hems 3 and 2, so cut at 105 cm; coated at
18 GSM, give or take 10%: 16.2 to 19.8. Three pairs of 100 cm² discs,
coated 1.01, 1.00 and 0.99 g over uncoated 0.82 g, are 19, 18 and 17
GSM: a mean of 18.00, in. Discs 0.22 to 0.24 g heavier are 23.00: off,
taken only with a supervisor and a reason. 2,100 m coated is 2,000
sacks passed by the coating step; 2,104 m is 2,003, the part-sack not
a sack.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.inventory.models import Item, Lot

from .machines import Machine
from .orders import ProductionEntry, WorkCentre, WorkOrder
from .routing import Routing, RoutingOperation
from .station import LineKind, LoomStation
from .station_coat import CoatingCheck, record_coating, void_coating
from .tests_bag_counts import ConversionTestCase
from .tests_orders import TODAY
from .tests_station import at
from .tests_station_api import StationApiTestCase
from .woven import BagSpecification

ON = [["1.01", "0.82"], ["1.00", "0.82"], ["0.99", "0.82"]]
OFF = [["1.05", "0.82"], ["1.04", "0.82"], ["1.06", "0.82"]]


def build_coater(test):
    coat = Item.objects.create(sku="LAM-PP", name="Lamination grade PP", uom=test.kg,
                               standard_cost=Decimal("120"))
    test.coater = WorkCentre.objects.create(code="COAT", name="Coating line")
    test.k1 = Machine.objects.create(work_centre=test.coater, code="K-1")
    routing = Routing.objects.create(code="R-LAM", name="Coat and convert")
    RoutingOperation.objects.create(routing=routing, sequence=10, name="Coat",
                                    work_centre=test.coater, setup_minutes=Decimal("0"),
                                    units_per_hour=Decimal("3000"), rate_uom=test.pcs)
    RoutingOperation.objects.create(routing=routing, sequence=20, name="Cut and stitch",
                                    work_centre=test.cutting, setup_minutes=Decimal("0"),
                                    units_per_hour=Decimal("600"), rate_uom=test.pcs)
    test.lam_bag = Item.objects.create(sku="BAG-LAM", name="Laminated sack", uom=test.pcs,
                                       tracking="lot")
    spec = BagSpecification(
        code="B-LAM", bag_item=test.lam_bag, fabric=test.spec, bag_width_cm=Decimal("60"),
        bag_length_cm=Decimal("100"), bottom_hem_cm=Decimal("3"), top_hem_cm=Decimal("2"),
        thread_item=Item.objects.get(sku="THREAD"), thread_grams_per_bag=Decimal("1.2"),
        conversion_waste_percent=Decimal("2.5"), waste_recovered_percent=Decimal("0"),
        is_laminated=True, lamination_gsm=Decimal("18"), routing=routing)
    spec.set_coating([(coat, 100)])
    spec.save()
    test.lam_spec = spec
    test.lam_run = WorkOrder.objects.create(item=test.lam_bag, bom=spec.bom,
                                            quantity_ordered=Decimal("5000"), uom=test.pcs,
                                            warehouse=test.plant)
    test.lam_run.release(TODAY)
    test.kx = LoomStation.objects.create(code="KX-1", name="Coater", warehouse=test.plant,
                                         kind=LineKind.COATING)
    test.kx.machines.set([test.k1])
    test.kx.supervisors.add(test.supervisor)


class CoatingTestCase(ConversionTestCase):
    def setUp(self):
        super().setUp()
        build_coater(self)

    def check(self, metres="2100", samples=ON, **extra):
        return record_coating(self.kx, self.operator, self.k1, metres, samples,
                              at=at(TODAY, 10), **extra)


class CoatingCheckTests(CoatingTestCase):
    def test_weighed_against_the_sack_and_counted_in_sacks(self):
        check = self.check()
        self.assertEqual((check.mean_gsm, check.lower_gsm, check.upper_gsm, check.passed),
                         (Decimal("18.00"), Decimal("16.200"), Decimal("19.800"), True))
        self.assertEqual((check.report.operation.name, check.report.quantity_good,
                          check.report.machine, check.samples[0]),
                         ("Coat", Decimal("2000.0000"), self.k1, ["1.01", "0.82"]))
        self.assertEqual(self.check("2104").report.quantity_good, Decimal("2003.0000"))

    def test_the_limit_itself_is_in(self):
        self.assertTrue(self.check(samples=[["1.018", "0.82"]] * 3).passed)
        self.assertTrue(self.check(samples=[["0.982", "0.82"]] * 3).passed)

    def test_held_to_the_sacks_own_tolerance(self):
        # 19 GSM is inside 10% of 18 and outside 5%: 17.1 to 18.9.
        BagSpecification.objects.filter(pk=self.lam_spec.pk).update(
            lamination_tolerance_percent=Decimal("5"))
        with self.assertRaisesMessage(ValidationError, "needs a supervisor's PIN"):
            self.check(samples=[["1.01", "0.82"]] * 3)
        check = self.check(samples=[["0.991", "0.82"]] * 3)
        self.assertEqual((check.lower_gsm, check.upper_gsm, check.passed),
                         (Decimal("17.100"), Decimal("18.900"), True))

    def test_a_smaller_disc_weighs_less_for_the_same_coating(self):
        check = self.check(samples=[["0.500", "0.410"]] * 3, disc_sq_cm="50")
        self.assertEqual(check.mean_gsm, Decimal("18.00"))

    def test_off_weight_only_with_a_supervisor_and_a_reason(self):
        with self.assertRaisesMessage(ValidationError, "needs a supervisor's PIN"):
            self.check(samples=OFF)
        with self.assertRaisesMessage(ValidationError, "Say why coating off its weight"):
            self.check(samples=OFF, supervisor=self.supervisor)
        self.assertFalse(CoatingCheck.objects.exists())
        check = self.check(samples=OFF, supervisor=self.supervisor, reason="Die lip cleaned")
        self.assertEqual((check.passed, check.mean_gsm, check.conceded_by, check.reason),
                         (False, Decimal("23.00"), self.supervisor, "Die lip cleaned"))

    def test_what_a_check_must_be(self):
        with self.assertRaisesMessage(ValidationError, "Weigh 3 pairs of discs; 2"):
            self.check(samples=ON[:2])
        with self.assertRaisesMessage(ValidationError, "two weights: coated and uncoated"):
            self.check(samples=[["1.00"]] * 3)
        with self.assertRaisesMessage(ValidationError, "A coated disc's weight is a number"):
            self.check(samples=[["heavy", "0.82"]] * 3)
        with self.assertRaisesMessage(ValidationError, "An uncoated disc's weight is more"):
            self.check(samples=[["1.00", "0"]] * 3)
        with self.assertRaisesMessage(ValidationError, "The metres coated is more"):
            self.check("0")
        with self.assertRaisesMessage(ValidationError, "less than one sack's cut length"):
            self.check("1.04")
        with self.assertRaisesMessage(ValidationError, "The disc's area is more"):
            self.check(disc_sq_cm="0")
        with self.assertRaisesMessage(ValidationError, "is not a coater's station"):
            record_coating(self.cv, self.operator, self.c1, "2100", ON, at=at(TODAY, 10))
        self.assertFalse(CoatingCheck.objects.exists())

    def test_an_unlaminated_sack_has_nothing_to_check(self):
        BagSpecification.objects.filter(pk=self.lam_spec.pk).update(is_laminated=False,
                                                                     lamination_gsm=0)
        with self.assertRaisesMessage(ValidationError, "is not laminated"):
            self.check()

    def test_the_limits_are_the_ones_it_was_held_to(self):
        check = self.check()
        BagSpecification.objects.filter(pk=self.lam_spec.pk).update(
            lamination_tolerance_percent=Decimal("2"))
        check.refresh_from_db()
        self.assertEqual((check.lower_gsm, check.upper_gsm, check.target_gsm),
                         (Decimal("16.200"), Decimal("19.800"), Decimal("18.00")))


class WithdrawnTests(CoatingTestCase):
    def test_withdrawn_its_count_goes_with_it(self):
        check = self.check()
        with self.assertRaisesMessage(ValidationError, "approved by somebody else"):
            void_coating(check, self.kx, self.operator, self.operator, "Wrong run")
        with self.assertRaisesMessage(ValidationError, "Say why"):
            void_coating(check, self.kx, self.supervisor, self.operator, " ")
        void_coating(check, self.kx, self.supervisor, self.operator, "Wrong run")
        check.report.refresh_from_db()
        self.assertIsNotNone(check.report.voided_at)
        with self.assertRaisesMessage(ValidationError, "already withdrawn"):
            void_coating(check, self.kx, self.supervisor, self.operator, "Again")

    def test_only_this_stations_checks(self):
        check = self.check()
        other = LoomStation.objects.create(code="KX-2", name="Two", warehouse=self.plant,
                                           kind=LineKind.COATING)
        other.supervisors.add(self.supervisor)
        with self.assertRaisesMessage(ValidationError, "was not checked at KX-2"):
            void_coating(check, other, self.supervisor, self.operator, "x")

    def test_not_once_the_sacks_are_cut(self):
        check = self.check()
        entry = ProductionEntry.objects.create(
            work_order=self.lam_run, entry_date=TODAY, warehouse=self.plant,
            quantity_produced=Decimal("1500"), uom=self.pcs, work_centre=self.cutting,
            lot=Lot.objects.create(item=self.lam_bag, code="LAM-1"))
        entry.post()
        with self.assertRaisesMessage(ValidationError, "has already taken"):
            void_coating(check, self.kx, self.supervisor, self.operator, "Wrong run")
        check.refresh_from_db()
        self.assertIsNone(check.voided_at)


class CoatingApiTests(StationApiTestCase):
    def setUp(self):
        super().setUp()
        from .tests_bag_counts import build_conversion

        build_conversion(self)
        build_coater(self)
        self.base = f"/api/manufacturing/stations/{self.kx.code}/"

    def test_checked_and_withdrawn_at_the_coater(self):
        self.client.post(self.base + "sign-in/", {"pin": self.pin}, format="json")
        self.assertEqual(self.client.get(self.base).json()["kind"], "coating")
        response = self.client.post(self.base + "coating/", {
            "machine": "K-1", "metres": "2100", "samples": ON}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["run"], body["sacks"], body["mean_gsm"], body["limits"],
                          body["passed"]),
                         (self.lam_run.number, "2000.0000", "18.00", ["16.200", "19.800"], True))
        response = self.client.post(self.base + f"coating/{body['id']}/void/", {
            "supervisor_pin": self.supervisor_pin, "reason": "Wrong run"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        response = self.client.post(self.base + "coating/", {
            "machine": "K-1", "metres": "2100", "samples": OFF}, format="json")
        self.assertEqual(response.status_code, 400)
