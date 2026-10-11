"""
A 60 x 100 cm sack contracted at 110.000 g, give or take 3%: 106.700 to
113.300 g on the mean of ten bags. (The recipe computes 111.436 g, inside
the contract.)

  Ten bags at 110.5, 109.8, 111.2, 110.1 and 109.4, twice: 110.200 g,
  in. Ten averaging 114.000 g: out, booked only if a supervisor takes
  it. Ten at exactly 113.300: in, the limits are inclusive.
  On C-1, operator A counts 500 in weight and 500 taken off weight; on
  C-2, operator B counts 400. At 0.12 a bag, A is paid 120.00 and B
  48.00; a bundle A counts on the contractor's C-3 pays nothing.
  C-1's bundles are 0.2 and 4.0 g over 110: a mean deviation of
  (0.18% + 3.64%) / 2 = 1.91%. C-2's is 0.18%.
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.core.models import Party, UnitOfMeasure, UnitOfMeasureCategory
from apps.hr.payroll import ComponentBasis, ComponentKind, EmployeeCompensation, PayComponent, PayRun
from apps.inventory.models import Item, Lot
from apps.quality.models import Disposition

from .conversion import BagCount, record_bags, summary, void_bags
from .machines import Machine
from .orders import WorkCentre, WorkOrder
from .routing import Routing, RoutingOperation
from .station import LoomStation
from .tests_orders import TODAY
from .tests_station import StationTestCase, at
from .tests_station_api import StationApiTestCase
from .woven import BagSpecification

IN = ["110.5", "109.8", "111.2", "110.1", "109.4"] * 2
OUT = ["114.0", "114.4", "113.9", "114.6", "113.1"] * 2


def build_conversion(test):
    test.pcs = UnitOfMeasure.objects.create(code="pcs", name="Pieces",
                                            category=UnitOfMeasureCategory.COUNT)
    test.bag = Item.objects.create(sku="BAG-60X100", name="Woven sack 60 x 100", uom=test.pcs,
                                   tracking="lot")
    thread = Item.objects.create(sku="THREAD", name="Thread", uom=test.kg,
                                 standard_cost=Decimal("300"))
    Item.objects.filter(pk=test.fabric.pk).update(standard_cost=Decimal("140"))
    test.cutting = WorkCentre.objects.create(code="CONV", name="Cutting and stitching")
    test.c1, test.c2 = (Machine.objects.create(work_centre=test.cutting, code=code)
                        for code in ("C-1", "C-2"))
    test.c3 = Machine.objects.create(work_centre=test.cutting, code="C-3",
                                     contractor=Party.objects.create(code="CON-B",
                                                                     name="Contractor B"))
    routing = Routing.objects.create(code="R-CONV", name="Convert")
    RoutingOperation.objects.create(routing=routing, sequence=10, name="Cut and stitch",
                                    work_centre=test.cutting, setup_minutes=Decimal("0"),
                                    units_per_hour=Decimal("600"), rate_uom=test.pcs)
    test.bag_spec = BagSpecification.objects.create(
        code="B1", bag_item=test.bag, fabric=test.spec, bag_width_cm=Decimal("60"),
        bag_length_cm=Decimal("100"), bottom_hem_cm=Decimal("3"), top_hem_cm=Decimal("2"),
        thread_item=thread, thread_grams_per_bag=Decimal("1.2"),
        conversion_waste_percent=Decimal("2.5"), waste_recovered_percent=Decimal("0"),
        target_grams=Decimal("110.000"), weight_tolerance_percent=Decimal("3"),
        routing=routing)
    test.bag_run = WorkOrder.objects.create(item=test.bag, bom=test.bag_spec.bom,
                                            quantity_ordered=Decimal("5000"), uom=test.pcs,
                                            warehouse=test.plant)
    test.bag_run.release(TODAY)
    test.cv = LoomStation.objects.create(code="CV-1", name="Conversion 1", warehouse=test.plant)
    test.cv.machines.set([test.c1, test.c2, test.c3])
    test.cv.supervisors.add(test.supervisor)


class ConversionTestCase(StationTestCase):
    def setUp(self):
        super().setUp()
        build_conversion(self)
        self.other = self.employee("EMP-0200", "Operator B")

    def count(self, bags="500", sample=IN, machine=None, operator=None, **extra):
        return record_bags(self.cv, operator or self.operator, machine or self.c1, bags,
                           sample, at=at(TODAY, 10), **extra)


class WeighedBeforeBookedTests(ConversionTestCase):
    def test_in_weight_it_is_a_batch_in_stock_with_its_inspection(self):
        count = self.count()
        self.assertEqual((count.bags, count.passed, count.target_grams, count.sample_mean_grams),
                         (500, True, Decimal("110.000000"), Decimal("110.200")))
        lot = count.inspection.lot
        self.assertEqual(lot.code, "BG-260601-D-C1-01")
        self.assertEqual(lot.on_hand_at(self.plant), Decimal("500"))
        self.assertEqual((count.inspection.disposition, count.inspection.result),
                         (Disposition.ACCEPT, "pass"))
        reading = count.inspection.readings.first()
        self.assertEqual((reading.lower_limit, reading.upper_limit),
                         (Decimal("106.700000"), Decimal("113.300000")))

    def test_off_weight_is_not_booked_by_the_operator(self):
        with self.assertRaisesMessage(ValidationError, "needs a supervisor's PIN"):
            self.count(sample=OUT)
        with self.assertRaisesMessage(ValidationError, "somebody else, not the operator"):
            self.count(sample=OUT, supervisor=self.operator, reason="Fine")
        with self.assertRaisesMessage(ValidationError, "Say why"):
            self.count(sample=OUT, supervisor=self.supervisor)
        self.assertEqual((BagCount.objects.count(), Lot.objects.filter(item=self.bag).count()),
                         (0, 0))

    def test_only_this_stations_supervisor_takes_it(self):
        with self.assertRaisesMessage(ValidationError, "does not supervise CV-1"):
            self.count(sample=OUT, supervisor=self.other, reason="Looks fine")

    def test_off_weight_taken_by_a_supervisor_says_who_and_why(self):
        count = self.count(sample=OUT, supervisor=self.supervisor, reason="Customer agreed +4%")
        self.assertEqual((count.passed, count.supervisor, count.sample_mean_grams),
                         (False, self.supervisor, Decimal("114.000")))
        self.assertEqual((count.inspection.disposition, count.inspection.decided_by),
                         (Disposition.CONCESSION, self.supervisor.party))

    def test_the_limit_itself_is_in(self):
        self.assertTrue(self.count(sample=["113.3"] * 10).passed)

    def test_the_whole_sample(self):
        with self.assertRaisesMessage(ValidationError, "Weigh 10 bags"):
            self.count(sample=IN[:9])
        with self.assertRaisesMessage(ValidationError, "is a number of grammes"):
            self.count(sample=["heavy"] * 10)
        with self.assertRaisesMessage(ValidationError, "at least one bag"):
            self.count(bags="0")

    def test_a_bundle_is_a_batch(self):
        Item.objects.filter(pk=self.bag.pk).update(tracking="none")
        with self.assertRaisesMessage(ValidationError, "not tracked by batch"):
            self.count()


class VoidTests(ConversionTestCase):
    def test_a_supervisor_takes_it_back_whole(self):
        count = self.count()
        with self.assertRaisesMessage(ValidationError, "somebody else, not the operator"):
            void_bags(count, self.operator, "Miscounted")
        with self.assertRaisesMessage(ValidationError, "Say why"):
            void_bags(count, self.supervisor, " ")
        void_bags(count, self.supervisor, "Miscounted")
        count.refresh_from_db()
        self.assertFalse(count.is_standing())
        self.assertIsNotNone(count.inspection.voided_at)
        self.assertEqual(count.inspection.lot.on_hand_at(self.plant), Decimal("0"))
        with self.assertRaisesMessage(ValidationError, "already void"):
            void_bags(count, self.supervisor, "Again")
        with self.assertRaisesMessage(ValidationError, "Void it and count again"):
            count.bags = 400
            count.save()


    def test_withdrawn_when_quality_voided_its_inspection_first(self):
        """
        Audit, 9 October: void_bags voided the inspection without asking
        whether quality had already done so, as void_gauged asks, and was
        refused: 500 bags stayed on the shelf for good.
        """
        count = self.count()
        lot = count.inspection.lot
        count.inspection.void("Scale read wrong; weighing again")
        void_bags(count, self.supervisor, "Miscounted")
        count.refresh_from_db()
        self.assertFalse(count.is_standing())
        self.assertEqual(count.inspection.voided_reason, "Scale read wrong; weighing again")
        self.assertEqual(lot.on_hand_at(self.plant), Decimal("0"))


class PerMachineAndOperatorTests(ConversionTestCase):
    def the_shift(self):
        self.count()
        self.count(sample=OUT, supervisor=self.supervisor, reason="Customer agreed")
        self.count("400", machine=self.c2, operator=self.other)
        self.count("300", machine=self.c3)

    def test_what_each_machine_and_each_person_turned_out(self):
        self.the_shift()
        found = summary(TODAY, TODAY, station=self.cv)
        machines = {row["who"]: row for row in found["by_machine"]}
        self.assertEqual((machines["C-1"]["bundles"], machines["C-1"]["bags"],
                          machines["C-1"]["conceded"], machines["C-1"]["mean_deviation_percent"]),
                         (2, 1000, 1, Decimal("1.91")))
        self.assertEqual((machines["C-2"]["bags"], machines["C-2"]["mean_deviation_percent"]),
                         (400, Decimal("0.18")))
        people = {row["who"]: row["bags"] for row in found["by_operator"]}
        self.assertEqual(people, {"EMP-0142 Operator": 1300, "EMP-0200 Operator B": 400})

    def test_paid_by_the_bag_on_the_plants_own_machines(self):
        self.the_shift()
        from apps.accounting.models import Account, AccountType

        expense = Account.objects.create(code="6000", name="Wages",
                                         account_type=AccountType.EXPENSE)
        per_bag = PayComponent.objects.create(code="PC-BAG", name="Bags", kind=ComponentKind.EARNING,
                                              basis=ComponentBasis.PER_UNIT,
                                              measure="bags_converted", expense_account=expense)
        for person in (self.operator, self.other):
            EmployeeCompensation.objects.create(employee=person, component=per_bag,
                                                amount=Decimal("0.12"),
                                                effective_from=TODAY)
        run = PayRun.objects.create(period_start=TODAY, period_end=TODAY, pay_date=TODAY)
        run.calculate(employees=[self.operator, self.other])
        paid = {slip.employee: (slip.lines.get().quantity, slip.lines.get().amount)
                for slip in run.payslips.all()}
        self.assertEqual(paid[self.operator], (Decimal("1000"), Decimal("120.00")))
        self.assertEqual(paid[self.other], (Decimal("400"), Decimal("48.00")))

    def test_a_voided_count_pays_nobody(self):
        from apps.manufacturing.conversion import bags_converted

        count = self.count("400", machine=self.c2, operator=self.other)
        void_bags(count, self.supervisor, "Counted twice")
        self.assertEqual(bags_converted(self.other, TODAY), [])


class BagStationApiTests(StationApiTestCase):
    def setUp(self):
        super().setUp()
        build_conversion(self)
        self.cv_base = f"/api/manufacturing/stations/{self.cv.code}/"

    def post_cv(self, path, data):
        return self.client.post(self.cv_base + path, data, format="json")

    def test_counted_refused_conceded_voided_and_reported(self):
        self.post_cv("sign-in/", {"pin": self.pin})
        response = self.post_cv("bags/", {"machine": "C-1", "bags": 500, "sample_grams": IN})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["batch"], body["sample_mean_grams"], body["passed"]),
                         ("BG-260601-D-C1-01", "110.200", True))
        response = self.post_cv("bags/", {"machine": "C-1", "bags": 500, "sample_grams": OUT})
        self.assertEqual(response.status_code, 400)
        response = self.post_cv("bags/", {"machine": "C-1", "bags": 500, "sample_grams": OUT,
                                          "supervisor_pin": self.supervisor_pin,
                                          "reason": "Customer agreed"})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["conceded_by"]["number"], "EMP-0087")
        response = self.post_cv(f"bags/{body['id']}/void/",
                                {"supervisor_pin": self.supervisor_pin, "reason": "Miscounted"})
        self.assertEqual(response.status_code, 200, response.content)
        report = APIClient()
        report.force_authenticate(User.objects.create_superuser("manager"))
        rows = report.get(f"/api/manufacturing/station-reports/{self.cv.code}/bags/",
                          {"start": "2026-06-01", "end": "2026-06-01"}).json()
        self.assertEqual([(row["who"], row["bags"], row["conceded"]) for row in rows["by_machine"]],
                         [("C-1", 500, 1)])


    def test_withdrawn_at_the_station_after_quality_voided_its_inspection(self):
        self.post_cv("sign-in/", {"pin": self.pin})
        body = self.post_cv("bags/", {"machine": "C-1", "bags": 500, "sample_grams": IN}).json()
        count = BagCount.objects.get(pk=body["id"])
        count.inspection.void("Scale read wrong; weighing again")
        response = self.post_cv(f"bags/{body['id']}/void/",
                                {"supervisor_pin": self.supervisor_pin, "reason": "Miscounted"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(count.inspection.lot.on_hand_at(self.plant), Decimal("0"))


class AnInspectedSpecificationTests(ConversionTestCase):
    def test_its_window_closes_though_its_limits_were_rounded_to_store(self):
        # 110.123 g at 3.33%: a margin of 3.6670959 g, stored at six places
        # as 3.667096. Compared unrounded, the plan would never read back
        # equal and closing the window would count as changing the limits.
        self.bag_spec.target_grams = Decimal("110.123")
        self.bag_spec.weight_tolerance_percent = Decimal("3.33")
        self.bag_spec.save()
        self.count()
        self.bag_spec.valid_to = TODAY
        self.bag_spec.save()
        line = self.bag_spec.inspection_plan.lines.get()
        self.assertEqual((line.lower_limit, line.upper_limit),
                         (Decimal("106.455904"), Decimal("113.790096")))
        self.bag_spec.weight_tolerance_percent = Decimal("3")
        with self.assertRaisesMessage(ValidationError, "stage a new one from the day"):
            self.bag_spec.save()


class WeighedOnANamedScaleTests(BagStationApiTests):
    def test_the_weights_rely_on_the_scales_calibration(self):
        from apps.quality.calibration import Calibration, Instrument

        scale = Instrument.objects.create(code="SC-CV", name="Conversion scale",
                                          interval_days=90)
        calibration = Calibration.objects.create(instrument=scale, calibrated_on=TODAY,
                                                 result="pass", performed_by="Lab")
        calibration.post()
        self.post_cv("sign-in/", {"pin": self.pin})
        response = self.post_cv("bags/", {"machine": "C-1", "bags": 500, "sample_grams": IN,
                                          "scale": "SC-CV"})
        self.assertEqual(response.status_code, 201, response.content)
        count = BagCount.objects.get(pk=response.json()["id"])
        self.assertEqual({reading.calibration for reading in count.inspection.readings.all()},
                         {calibration})
        response = self.post_cv("bags/", {"machine": "C-1", "bags": 500, "sample_grams": IN,
                                          "scale": "NOPE"})
        self.assertEqual(response.status_code, 400)
