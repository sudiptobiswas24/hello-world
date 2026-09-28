"""
A run through three steps: 10 Print, 20 Cut, 30 Stitch.

  Print counts 1,000 good. Cut counts 900 good and spoils 50 (mis-cut):
  it took 950 of the 1,000, so 50 wait before it.
  Stitch books 800 good and 25 scrap, 20 of it open seams and 5 that no
  line explains: it took 825 of the 900, so 75 wait before it.
  Cut counting 51 more would take 1,001; stitch booking 76 more, 901.
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from .machines import Machine
from .orders import ManufacturingSettings, WorkCentre
from .routing import Routing, RoutingOperation
from .scrap import OperationReport, ProductionScrap, ScrapReason, flow, report, scrap_report
from .tests_orders import TODAY, RunTestCase


class StepsTestCase(RunTestCase):
    def setUp(self):
        super().setUp()
        self.routing = Routing.objects.create(code="R-BAG", name="Print, cut, stitch")
        for sequence, code, name in ((10, "PR", "Print"), (20, "CT", "Cut"),
                                     (30, "ST", "Stitch")):
            centre = WorkCentre.objects.create(code=code, name=name)
            setattr(self, code.lower(), centre)
            RoutingOperation.objects.create(routing=self.routing, sequence=sequence,
                                            name=name, work_centre=centre,
                                            units_per_hour=Decimal("500"), rate_uom=self.kg)
        self.bom.routing = self.routing
        self.bom.save()
        self.miscut = ScrapReason.objects.create(code="MISCUT", name="Cut off size")
        self.seam = ScrapReason.objects.create(code="SEAM", name="Open seam")
        self.run_ = self.order()
        self.run_.release(TODAY)

    def step(self, name, run=None):
        return (run or self.run_).operations.get(name=name)

    def book(self, produced, scrapped="0", lines=(), run=None):
        entry = self.produce(run or self.run_, produced, scrapped)
        for reason, quantity, step in lines:
            ProductionScrap.objects.create(entry=entry, reason=reason,
                                           quantity=Decimal(quantity),
                                           operation=self.step(step) if step else None)
        entry.post()
        return entry

    def through_the_steps(self):
        report(self.step("Print"), "1000", on_date=TODAY)
        report(self.step("Cut"), "900", on_date=TODAY)
        self.book("0", "50", [(self.miscut, "50", "Cut")])
        return self.book("800", "25", [(self.seam, "20", "Stitch")])


class FlowTests(StepsTestCase):
    def test_what_each_step_passed_on_spoiled_and_left_waiting(self):
        self.through_the_steps()
        rows = {row["operation"]: row for row in flow(self.run_)}
        self.assertEqual([(rows[name]["good"], rows[name]["scrap"], rows[name]["waiting_before"])
                          for name in ("Print", "Cut", "Stitch")],
                         [(Decimal("1000"), Decimal("0"), None),
                          (Decimal("900"), Decimal("50"), Decimal("50")),
                          (Decimal("800"), Decimal("25"), Decimal("75"))])
        self.assertEqual(rows["Stitch"]["scrap_by_reason"],
                         {"SEAM": Decimal("20"), "unexplained": Decimal("5")})
        self.assertEqual(rows["Cut"]["scrap_by_reason"], {"MISCUT": Decimal("50")})

    def test_no_step_takes_more_than_came_to_it(self):
        self.through_the_steps()
        with self.assertRaisesMessage(ValidationError, "1 more than came to it"):
            report(self.step("Cut"), "51", on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "1 more than came to it"):
            self.book("76")
        with self.assertRaisesMessage(ValidationError, "1 more than came to it"):
            self.book("0", "51", [(self.miscut, "51", "Cut")])
        # Scrap naming no step is the last step's.
        with self.assertRaisesMessage(ValidationError, "1 more than came to it"):
            self.book("0", "76", [(self.seam, "76", None)])
        self.book("75")

    def test_a_step_that_has_not_counted_bounds_nothing_until_it_does(self):
        self.book("100")
        with self.assertRaisesMessage(ValidationError, "Stitch has already taken 100"):
            report(self.step("Cut"), "50", on_date=TODAY)
        report(self.step("Cut"), "100", on_date=TODAY)
        # Print never counted: nothing can be said about what waits for Cut.
        self.assertEqual([row["waiting_before"] for row in flow(self.run_)],
                         [None, None, Decimal("0")])

    def test_the_last_step_is_the_runs_own_output(self):
        with self.assertRaisesMessage(ValidationError, "is the last step"):
            report(self.step("Stitch"), "10", on_date=TODAY)


class WithdrawnCountTests(StepsTestCase):
    def test_not_once_the_next_step_has_drawn_on_it(self):
        self.through_the_steps()
        extra = report(self.step("Print"), "40", on_date=TODAY)
        extra.void("Double counted")
        with self.assertRaisesMessage(ValidationError, "Cut has already taken 950"):
            OperationReport.objects.get(operation__name="Print", quantity_good=1000).void("x")

    def test_voiding_the_output_frees_the_count_behind_it(self):
        report(self.step("Print"), "1000", on_date=TODAY)
        cut = report(self.step("Cut"), "900", on_date=TODAY)
        entry = self.book("800")
        with self.assertRaisesMessage(ValidationError, "Stitch has already taken 800"):
            cut.void("Wrong")
        entry.void()
        cut.void("Wrong")
        self.assertEqual(flow(self.run_)[1]["good"], Decimal("0"))

    def test_a_count_is_as_it_was_taken(self):
        counted = report(self.step("Print"), "10", on_date=TODAY)
        counted.quantity_good = Decimal("11")
        with self.assertRaisesMessage(ValidationError, "Void it and report again"):
            counted.save()
        with self.assertRaisesMessage(ValidationError, "void it"):
            counted.delete()
        counted.void("Miscount")
        with self.assertRaisesMessage(ValidationError, "already void"):
            counted.void("Again")
        with self.assertRaisesMessage(ValidationError, "Say why"):
            report(self.step("Print"), "10", on_date=TODAY).void(" ")


class CountRefusalTests(StepsTestCase):
    def test_refusals(self):
        with self.assertRaisesMessage(ValidationError, "count of nothing"):
            report(self.step("Print"), "0", on_date=TODAY)
        stranger = Machine.objects.create(code="C-9", name="Cutter", work_centre=self.ct)
        with self.assertRaisesMessage(ValidationError, "belongs to CT"):
            report(self.step("Print"), "10", on_date=TODAY, machine=stranger)
        self.run_.close()
        with self.assertRaisesMessage(ValidationError, "is not running"):
            report(self.step("Print"), "10", on_date=TODAY)


class ScrapLineTests(StepsTestCase):
    def test_never_more_explained_than_was_scrapped(self):
        with self.assertRaisesMessage(ValidationError, "30 of scrap is explained and 25"):
            self.book("10", "25", [(self.seam, "20", "Stitch"), (self.miscut, "10", "Cut")])

    def test_a_plant_that_wants_every_sack_explained(self):
        ManufacturingSettings.objects.update(scrap_needs_reason=True)
        with self.assertRaisesMessage(ValidationError, "20 of 25 scrapped says why"):
            self.book("10", "25", [(self.seam, "20", None)])
        self.book("10", "25", [(self.seam, "20", None), (self.miscut, "5", "Cut")])

    def test_a_step_of_this_run_and_a_reason_in_use(self):
        other = self.order()
        other.release(TODAY)
        entry = self.produce(self.run_, "10", "5")
        with self.assertRaisesMessage(ValidationError, "not of"):
            ProductionScrap.objects.create(entry=entry, reason=self.seam, quantity=Decimal("5"),
                                           operation=other.operations.get(name="Cut"))
        retired = ScrapReason.objects.create(code="OLD", name="Old", is_active=False)
        with self.assertRaisesMessage(ValidationError, "no longer used"):
            ProductionScrap.objects.create(entry=entry, reason=retired, quantity=Decimal("5"))

    def test_booked_is_booked(self):
        entry = self.book("10", "5", [(self.seam, "5", None)])
        line = entry.scrap_lines.get()
        line.quantity = Decimal("4")
        with self.assertRaisesMessage(ValidationError, "its scrap is as it was booked"):
            line.save()
        with self.assertRaisesMessage(ValidationError, "its scrap is as it was booked"):
            line.delete()

    def test_scrap_by_reason_across_runs(self):
        self.through_the_steps()
        voided = self.book("0", "7", [(self.seam, "7", None)])
        voided.void()
        self.assertEqual(
            [(row["operation"], row["reason"], row["quantity"])
             for row in scrap_report(TODAY, TODAY)],
            [("Cut", "MISCUT", Decimal("50")), ("Stitch", "SEAM", Decimal("20")),
             ("Stitch", "unexplained", Decimal("5"))])


class StepsApiTests(StepsTestCase):
    def test_counted_booked_and_read_back(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("floor"))
        response = client.post("/api/manufacturing/operation-reports/record/", {
            "operation": self.step("Print").pk, "quantity_good": "1000",
            "reported_on": "2026-06-01"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        entry = self.produce(self.run_, "0", "30")
        response = client.post("/api/manufacturing/production-scrap/", {
            "entry": entry.pk, "reason": self.miscut.pk, "operation": self.step("Cut").pk,
            "quantity": "30"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        entry.post()
        rows = client.get(f"/api/manufacturing/run-flow/{self.run_.pk}/").json()
        self.assertEqual([(row["operation"], row["good"], row["scrap"]) for row in rows],
                         [("Print", "1000", "0"), ("Cut", "0", "30"), ("Stitch", "0", "0")])
        body = client.get("/api/manufacturing/run-flow/scrap/",
                          {"start": "2026-06-01", "end": "2026-06-01"}).json()
        self.assertEqual(body, [{"item": "TAPE-1000", "operation": "Cut", "reason": "MISCUT",
                                 "quantity": "30"}])
        response = client.post("/api/manufacturing/operation-reports/record/", {
            "operation": self.step("Cut").pk, "quantity_good": "971"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("1 more than came to it", response.content.decode())
        line = entry.scrap_lines.get()
        response = client.patch(f"/api/manufacturing/production-scrap/{line.pk}/",
                                {"quantity": "1"}, format="json")
        self.assertEqual(response.status_code, 400)
