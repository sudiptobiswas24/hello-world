"""
A test certificate for a shipment of tape batch TAPE-A, made from polymer
batch PP-2609.

  Tape denier read 1000, 1010 and 990: mean 1000, inside 970 to 1030.
  Polymer melt flow read 3.0 and 3.2: mean 3.1, inside 2.5 to 3.5.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.sales.models import Delivery

from apps.core.models import Party, UnitOfMeasure, UnitOfMeasureCategory
from apps.quality.models import (
    Characteristic, Disposition, Evaluation, Inspection, InspectionPlan, PlanLine, Reading,
)

from .certificates import TestCertificate, issue
from .tests_orders import TODAY
from .tests_trace import TraceTestCase


class CertificateTestCase(TraceTestCase):
    def setUp(self):
        super().setUp()
        self.inspector = Party.objects.create(code="QC1", name="Meera")
        self.manager = Party.objects.create(code="MGR", name="Plant manager")
        den = UnitOfMeasure.objects.create(code="den", name="Denier",
                                           category=UnitOfMeasureCategory.OTHER)
        mfi = UnitOfMeasure.objects.create(code="g10", name="g/10 min",
                                           category=UnitOfMeasureCategory.OTHER)
        self.denier = Characteristic.objects.create(code="DEN", name="Denier", uom=den)
        self.melt = Characteristic.objects.create(code="MFI", name="Melt flow", uom=mfi)
        self.tape_plan = self.plan(self.tape, self.denier, "1000", "970", "1030", 3)
        self.polymer_plan = self.plan(self.virgin, self.melt, "3", "2.5", "3.5", 2,
                                      mandatory=False)
        self.inspect(self.polymer_plan, self.polymer_lot, ["3.0", "3.2"])
        self.a_run()

    def plan(self, item, characteristic, target, lower, upper, samples, mandatory=True):
        plan = InspectionPlan.objects.create(item=item, is_mandatory=mandatory)
        PlanLine.objects.create(plan=plan, characteristic=characteristic,
                                target=Decimal(target), lower_limit=Decimal(lower),
                                upper_limit=Decimal(upper), sample_size=samples,
                                evaluation=Evaluation.MEAN, line_number=1)
        return plan

    def inspect(self, plan, lot, values, **extra):
        inspection = Inspection.objects.create(lot=lot, plan=plan, inspected_on=TODAY,
                                               inspected_by=self.inspector, **extra)
        for index, value in enumerate(values, start=1):
            Reading.objects.create(inspection=inspection, plan_line=plan.lines.get(),
                                   value=Decimal(value), sample_reference=f"S{index}")
        inspection.post()
        return inspection

    def shipped(self):
        return self.ship("600")

    def batch(self, certificate):
        (line,) = certificate.content["lines"]
        (batch,) = line["batches"]
        return batch


class WhatItSaysTests(CertificateTestCase):
    def test_the_shipped_batch_and_what_it_was_made_from(self):
        tape = self.inspect(self.tape_plan, self.tape_lot, ["1000", "1010", "990"])
        certificate = issue(self.shipped())
        self.assertTrue(certificate.number.startswith("TC-"))
        batch = self.batch(certificate)
        self.assertEqual((batch["lot"], batch["inspection"], batch["status"], batch["quantity"]),
                         ("TAPE-A", tape.number, "passed", "600"))
        (denier,) = batch["characteristics"]
        self.assertEqual((denier["mean"], denier["lower"], denier["upper"], denier["readings"],
                          denier["passed"], denier["unit"]),
                         ("1000", "970", "1030", 3, True, "den"))
        (polymer,) = batch["made_from"]
        self.assertEqual((polymer["lot"], polymer["level"]), ("PP-2609", 1))
        self.assertEqual(polymer["characteristics"][0]["mean"], "3.1")

    def test_a_batch_taken_by_concession_says_so(self):
        self.inspect(self.tape_plan, self.tape_lot, ["1100", "1100", "1100"],
                     disposition=Disposition.CONCESSION, decided_by=self.manager,
                     decision_note="Customer agreed")
        batch = self.batch(issue(self.shipped()))
        self.assertEqual(batch["status"], "accepted by concession")
        self.assertIs(batch["characteristics"][0]["passed"], False)

    def test_an_advisory_measurement_withdrawn_is_left_off_not_shown_blank(self):
        self.inspect(self.tape_plan, self.tape_lot, ["1000", "1010", "990"])
        self.polymer_lot.inspections.get().void("Wrong sample")
        self.assertEqual(self.batch(issue(self.shipped()))["made_from"], [])

    def test_the_limits_are_the_ones_it_was_judged_against(self):
        self.inspect(self.tape_plan, self.tape_lot, ["1000", "1010", "990"])
        self.tape_plan.lines.update(lower_limit=Decimal("995"), upper_limit=Decimal("1005"))
        (denier,) = self.batch(issue(self.shipped()))["characteristics"]
        self.assertEqual((denier["lower"], denier["upper"], denier["passed"]),
                         ("970", "1030", True))

    def test_a_batch_nobody_had_to_inspect_says_it_was_not(self):
        self.tape_plan.lines.all().delete()
        InspectionPlan.objects.filter(pk=self.tape_plan.pk).delete()
        batch = self.batch(issue(self.shipped()))
        self.assertEqual((batch["status"], batch["characteristics"]), ("not inspected", []))


class WhenItIsRefusedTests(CertificateTestCase):
    def test_goods_that_should_not_have_shipped_are_not_certified(self):
        tape = self.inspect(self.tape_plan, self.tape_lot, ["1000", "1010", "990"])
        delivery = self.shipped()
        tape.void("Scale out of calibration")
        with self.assertRaisesMessage(ValidationError, "should not have been used or shipped"):
            issue(delivery)

    def test_goods_rejected_after_they_went_are_not_certified(self):
        self.inspect(self.tape_plan, self.tape_lot, ["1000", "1010", "990"])
        delivery = self.shipped()
        self.inspect(self.tape_plan, self.tape_lot, ["900", "900", "900"],
                     disposition=Disposition.REJECT)
        with self.assertRaisesMessage(ValidationError, "was not accepted"):
            issue(delivery)

    def test_nor_what_was_made_from_a_batch_nobody_measured(self):
        self.inspect(self.tape_plan, self.tape_lot, ["1000", "1010", "990"])
        delivery = self.shipped()
        self.plan(self.regrind, self.melt, "3", "2.5", "3.5", 1)
        with self.assertRaisesMessage(ValidationError, "RG-1 must be inspected"):
            issue(delivery)

    def test_nor_from_one_whose_measurement_was_withdrawn(self):
        self.inspect(self.tape_plan, self.tape_lot, ["1000", "1010", "990"])
        delivery = self.shipped()
        InspectionPlan.objects.filter(pk=self.polymer_plan.pk).update(is_mandatory=True)
        self.polymer_lot.inspections.get().void("Wrong sample")
        with self.assertRaisesMessage(ValidationError, "PP-2609 must be inspected"):
            issue(delivery)

    def test_only_a_posted_shipment(self):
        self.inspect(self.tape_plan, self.tape_lot, ["1000", "1010", "990"])
        delivery = self.shipped()
        draft = Delivery.objects.create(sales_order=delivery.sales_order,
                                        delivery_date=delivery.delivery_date)
        with self.assertRaisesMessage(ValidationError, "has not shipped"):
            issue(draft)
        back = delivery.create_return(credit_invoices=False)
        with self.assertRaisesMessage(ValidationError, "not a shipment"):
            issue(back)

    def test_once_until_withdrawn(self):
        self.inspect(self.tape_plan, self.tape_lot, ["1000", "1010", "990"])
        delivery = self.shipped()
        first = issue(delivery)
        with self.assertRaisesMessage(ValidationError, "already certified"):
            issue(delivery)
        with self.assertRaisesMessage(ValidationError, "Say why"):
            first.void("  ")
        first.void("Wrong customer reference")
        second = issue(delivery)
        self.assertNotEqual(first.number, second.number)


class FrozenTests(CertificateTestCase):
    def test_a_later_void_does_not_reach_into_an_issued_certificate(self):
        tape = self.inspect(self.tape_plan, self.tape_lot, ["1000", "1010", "990"])
        certificate = issue(self.shipped())
        tape.void("Recalibrated")
        certificate.refresh_from_db()
        self.assertEqual(self.batch(certificate)["inspection"], tape.number)

    def test_a_withdrawn_certificate_is_still_not_edited(self):
        self.inspect(self.tape_plan, self.tape_lot, ["1000", "1010", "990"])
        certificate = issue(self.shipped())
        certificate.void("Wrong customer reference")
        certificate.issued_on = TODAY + datetime.timedelta(days=1)
        with self.assertRaisesMessage(ValidationError, "Void it"):
            certificate.save()

    def test_nothing_edits_or_deletes_one(self):
        self.inspect(self.tape_plan, self.tape_lot, ["1000", "1010", "990"])
        certificate = issue(self.shipped())
        certificate.issued_on = TODAY + datetime.timedelta(days=1)
        with self.assertRaisesMessage(ValidationError, "Void it"):
            certificate.save()
        with self.assertRaisesMessage(ValidationError, "void it"):
            certificate.delete()


class CertificateApiTests(CertificateTestCase):
    def test_issued_printed_and_withdrawn(self):
        self.inspect(self.tape_plan, self.tape_lot, ["1000", "1010", "990"])
        delivery = self.shipped()
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("qa"))
        response = client.post("/api/manufacturing/test-certificates/",
                               {"delivery": delivery.pk}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        url = f"/api/manufacturing/test-certificates/{response.json()['id']}/"
        page = client.get(url + "print/").content.decode()
        self.assertIn(response.json()["number"], page)
        self.assertIn("TAPE-A", page)
        self.assertIn("PP-2609", page)
        self.assertEqual(client.patch(url, {}, format="json").status_code, 405)
        response = client.post(url + "void/", {"reason": "Reissue"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNotNone(TestCertificate.objects.get().voided_at)
