"""
BAL-1, the lab balance: 0 to 200 g, calibrated every 180 days.

  Calibrated 10 January 2026, found in tolerance: due 9 July 2026. A GSM
  reading on 9 July relies on it; on 10 July the balance is overdue.
  Calibrated 1 July 2025 and 10 January 2026 in tolerance, then on 15
  June 2026 found out and adjusted: the inspections of 1 March and 1
  June measured on it are suspect, the one of 1 December 2025 (under the
  previous good calibration's watch, before the 10 January check) is not.

  Found failed on 15 March, on a certificate that only arrives in May:
  the inspections of 1 March and 1 April both relied on the January
  check and both are suspect. Repaired and passed on 20 April, the one
  of 1 May relied on that and is not. Adjusted on 15 March instead, it
  was right again from that day, and only 1 March is suspect.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from .calibration import Calibration, CalibrationResult, Instrument, due
from .models import Characteristic, Inspection, Reading
from .tests import QualityTestCase

D = datetime.date


class CalibrationTestCase(QualityTestCase):
    def setUp(self):
        super().setUp()
        self.balance = Instrument.objects.create(code="BAL-1", name="Lab balance",
                                                 interval_days=180, range_low=Decimal("0"),
                                                 range_high=Decimal("200"))
        self.balance.measures.add(self.gsm_check)
        self.gsm_plan = self.plan()

    def calibrate(self, on, result=CalibrationResult.PASS, instrument=None, **extra):
        calibration = Calibration.objects.create(
            instrument=instrument or self.balance, calibrated_on=on, result=result,
            performed_by="Metro Calibration Lab", **extra)
        calibration.post()
        return calibration

    def measured(self, on, values=("87", "88", "87.5"), instrument="balance", lot=None):
        inspection = Inspection.objects.create(lot=lot or self.roll, plan=self.gsm_plan,
                                               inspected_on=on, inspected_by=self.inspector)
        line = self.gsm_plan.lines.get()
        for index, value in enumerate(values, start=1):
            Reading.objects.create(
                inspection=inspection, plan_line=line, value=Decimal(value),
                sample_reference=f"S{index}",
                instrument=self.balance if instrument == "balance" else instrument)
        return inspection


class InCalibrationTests(CalibrationTestCase):
    def test_a_reading_relies_on_the_calibration_in_force_and_says_so(self):
        calibration = self.calibrate(D(2026, 1, 10))
        self.assertEqual(calibration.due_on, D(2026, 7, 9))
        inspection = self.measured(D(2026, 7, 9)).post()
        self.assertEqual({reading.calibration for reading in inspection.readings.all()},
                         {calibration})

    def test_overdue_never_and_failed_are_refused(self):
        with self.assertRaisesMessage(ValidationError, "was never calibrated on 2026-06-01"):
            self.measured(D(2026, 6, 1)).post()
        self.calibrate(D(2026, 1, 1)).void("Wrong balance")
        with self.assertRaisesMessage(ValidationError, "was never calibrated on 2026-06-01"):
            self.measured(D(2026, 6, 1)).post()
        self.calibrate(D(2026, 1, 10))
        with self.assertRaisesMessage(ValidationError, "was overdue on 2026-07-10"):
            self.measured(D(2026, 7, 10)).post()
        self.calibrate(D(2026, 7, 20), CalibrationResult.FAIL)
        with self.assertRaisesMessage(ValidationError, "was out of service on 2026-07-21"):
            self.measured(D(2026, 7, 21)).post()
        self.calibrate(D(2026, 7, 22))
        self.measured(D(2026, 7, 22)).post()

    def test_only_what_it_measures_and_only_across_its_range(self):
        self.calibrate(D(2026, 1, 10))
        tenacity = Characteristic.objects.create(code="TEN", name="Tenacity", uom=self.kg)
        self.balance.measures.set([tenacity])
        with self.assertRaisesMessage(ValidationError, "does not measure GSM"):
            self.measured(D(2026, 6, 1)).post()
        self.balance.measures.set([])
        Instrument.objects.filter(pk=self.balance.pk).update(range_high=Decimal("87.6"))
        self.balance.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "88 is outside what BAL-1"):
            self.measured(D(2026, 6, 1)).post()
        Instrument.objects.filter(pk=self.balance.pk).update(range_high=None,
                                                             range_low=Decimal("87.1"))
        self.balance.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "87 is outside what BAL-1"):
            self.measured(D(2026, 6, 1)).post()

    def test_a_retired_instrument_measures_nothing(self):
        self.calibrate(D(2026, 1, 10))
        Instrument.objects.filter(pk=self.balance.pk).update(is_active=False)
        self.balance.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "is retired"):
            self.measured(D(2026, 6, 1)).post()
        with self.assertRaisesMessage(ValidationError, "is retired"):
            self.calibrate(D(2026, 6, 2))

    def test_a_characteristic_that_demands_an_instrument(self):
        Characteristic.objects.filter(pk=self.gsm_check.pk).update(
            needs_calibrated_instrument=True)
        with self.assertRaisesMessage(ValidationError, "reading S1 names none"):
            self.measured(D(2026, 6, 1), instrument=None).post()
        Characteristic.objects.filter(pk=self.gsm_check.pk).update(
            needs_calibrated_instrument=False)
        self.measured(D(2026, 6, 1), instrument=None).post()

    def test_a_refusal_half_way_leaves_no_reading_claiming_a_calibration(self):
        calibration = self.calibrate(D(2026, 1, 10))
        Instrument.objects.filter(pk=self.balance.pk).update(range_high=Decimal("87.9"))
        self.balance.refresh_from_db()
        inspection = self.measured(D(2026, 6, 1))
        with self.assertRaisesMessage(ValidationError, "outside"):
            inspection.post()
        self.assertFalse(Reading.objects.filter(calibration__isnull=False).exists())
        calibration.void("Entered against the wrong balance")


class FoundOutTests(CalibrationTestCase):
    def test_every_inspection_since_the_last_good_check_is_suspect(self):
        self.calibrate(D(2025, 7, 1))
        before = self.measured(D(2025, 12, 1)).post()
        self.calibrate(D(2026, 1, 10))
        march = self.measured(D(2026, 3, 1)).post()
        june = self.measured(D(2026, 6, 1)).post()
        withdrawn = self.measured(D(2026, 6, 2)).post()
        withdrawn.void("Wrong roll")
        same_day = self.measured(D(2026, 6, 15)).post()
        other = Instrument.objects.create(code="BAL-2", name="Other", interval_days=180)
        self.calibrate(D(2026, 1, 10), instrument=other)
        self.measured(D(2026, 6, 3), instrument=other).post()
        found = self.calibrate(D(2026, 6, 15), CalibrationResult.ADJUSTED)
        self.assertEqual(found.suspect_inspections(), [march, june, same_day])
        self.assertNotIn(before, found.suspect_inspections())
        self.assertEqual(Calibration.objects.get(calibrated_on=D(2026, 1, 10),
                                                 instrument=self.balance)
                         .suspect_inspections(), [])

    def test_an_adjustment_is_the_last_good_check_for_the_next(self):
        self.calibrate(D(2026, 1, 10))
        february = self.measured(D(2026, 2, 1)).post()
        adjusted = self.calibrate(D(2026, 3, 15), CalibrationResult.ADJUSTED)
        april = self.measured(D(2026, 4, 1)).post()
        failed = self.calibrate(D(2026, 6, 15), CalibrationResult.FAIL)
        self.assertEqual(adjusted.suspect_inspections(), [february])
        self.assertEqual(failed.suspect_inspections(), [april])

    def test_a_failure_recorded_late_casts_doubt_forward_on_what_relied_on_the_last_good(self):
        self.calibrate(D(2026, 1, 10))
        march = self.measured(D(2026, 3, 1)).post()
        april = self.measured(D(2026, 4, 1)).post()
        failed = self.calibrate(D(2026, 3, 15), CalibrationResult.FAIL)
        self.calibrate(D(2026, 4, 20))
        may = self.measured(D(2026, 5, 1)).post()
        self.assertEqual(failed.suspect_inspections(), [march, april])
        self.assertNotIn(may, failed.suspect_inspections())

    def test_an_adjustment_recorded_late_was_right_again_from_its_day(self):
        self.calibrate(D(2026, 1, 10))
        march = self.measured(D(2026, 3, 1)).post()
        self.measured(D(2026, 4, 1)).post()
        adjusted = self.calibrate(D(2026, 3, 15), CalibrationResult.ADJUSTED)
        self.assertEqual(adjusted.suspect_inspections(), [march])

    def test_found_out_at_its_first_check_has_nothing_behind_it(self):
        # A reading can only name an instrument in calibration, so a good
        # calibration always stands behind anything it measured.
        Instrument.objects.filter(pk=self.balance.pk).update(interval_days=400)
        self.balance.refresh_from_db()
        first = self.calibrate(D(2025, 7, 1), CalibrationResult.FAIL)
        self.assertEqual(first.suspect_inspections(), [])
        self.calibrate(D(2025, 7, 2))
        june = self.measured(D(2026, 6, 1)).post()
        failed = self.calibrate(D(2026, 6, 15), CalibrationResult.FAIL)
        self.assertEqual(failed.suspect_inspections(), [june])


class TheRecordTests(CalibrationTestCase):
    def test_what_a_calibration_must_say(self):
        with self.assertRaisesMessage(ValidationError, "falls due after"):
            self.calibrate(D(2026, 1, 10), due_on=D(2026, 1, 10))
        with self.assertRaisesMessage(ValidationError, "after today"):
            self.calibrate(D(2099, 1, 1))
        draft = Calibration.objects.create(instrument=self.balance, calibrated_on=D(2026, 1, 10),
                                           result=CalibrationResult.PASS, performed_by=" ")
        with self.assertRaisesMessage(ValidationError, "Say who"):
            draft.post()
        stated = self.calibrate(D(2026, 1, 10), due_on=D(2026, 4, 1))
        self.assertEqual(stated.due_on, D(2026, 4, 1))

    def test_posted_is_voided_never_edited_and_not_once_relied_on(self):
        calibration = self.calibrate(D(2026, 1, 10))
        calibration.notes = "x"
        with self.assertRaisesMessage(ValidationError, "is posted. Void it"):
            calibration.save()
        with self.assertRaisesMessage(ValidationError, "is posted; void it"):
            calibration.delete()
        with self.assertRaisesMessage(ValidationError, "already posted"):
            calibration.post()
        self.measured(D(2026, 6, 1)).post()
        with self.assertRaisesMessage(ValidationError, "it stands"):
            calibration.void("Wrong")
        spare = self.calibrate(D(2026, 1, 11))
        with self.assertRaisesMessage(ValidationError, "Say why"):
            spare.void(" ")
        spare.void("Entered twice")
        with self.assertRaisesMessage(ValidationError, "not a standing calibration"):
            spare.void("Again")

    def test_what_is_due(self):
        self.calibrate(D(2026, 1, 10))
        never = Instrument.objects.create(code="TEN-1", name="Tensile tester", interval_days=365)
        Instrument.objects.create(code="OLD", name="Retired", interval_days=365, is_active=False)
        self.assertEqual([(row["instrument"], row["status"], row["due_on"])
                          for row in due(30, D(2026, 6, 20))],
                         [(never, "never calibrated", None),
                          (self.balance, "in calibration", D(2026, 7, 9))])
        self.assertEqual([row["instrument"] for row in due(10, D(2026, 6, 20))], [never])


class CalibrationApiTests(CalibrationTestCase):
    def test_recorded_posted_and_asked_about(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("lab"))
        response = client.post("/api/quality/calibrations/", {
            "instrument": self.balance.pk, "calibrated_on": "2026-01-10", "result": "pass",
            "performed_by": "Metro Calibration Lab"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        pk = response.json()["id"]
        body = client.post(f"/api/quality/calibrations/{pk}/post/").json()
        self.assertEqual((body["due_on"], body["number"][:4]), ("2026-07-09", "CAL-"))
        response = client.patch(f"/api/quality/calibrations/{pk}/", {"notes": "x"},
                                format="json")
        self.assertEqual(response.status_code, 400)
        june = self.measured(D(2026, 6, 1)).post()
        response = client.post("/api/quality/calibrations/", {
            "instrument": self.balance.pk, "calibrated_on": "2026-06-15",
            "result": "adjusted", "performed_by": "Metro"}, format="json")
        found = response.json()["id"]
        client.post(f"/api/quality/calibrations/{found}/post/")
        self.assertEqual(client.get(f"/api/quality/calibrations/{found}/suspects/").json(),
                         [{"inspection": june.number, "lot": "R-001",
                           "inspected_on": "2026-06-01"}])
        response = client.post(f"/api/quality/calibrations/{pk}/void/", {"reason": "x"},
                                format="json")
        self.assertEqual(response.status_code, 400)
        rows = client.get("/api/quality/instruments/due/",
                          {"within": "400", "on": "2026-06-20"}).json()
        self.assertEqual(rows, [{"instrument": "BAL-1", "status": "in calibration",
                                 "due_on": "2026-12-12"}])
        self.assertEqual(client.get("/api/quality/instruments/due/",
                                    {"within": "soon"}).status_code, 400)


class WhoDecidesACalibrationTests(CalibrationTestCase):
    """
    O83: post and void named no permission and took add_calibration. The
    Inspector, who holds add, voided the failed calibration that had made
    their own inspection suspect. Both take change now, the manager's.
    """

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group
        from django.core.management import call_command

        D = datetime.date
        self.calibrate(D(2026, 1, 10))
        self.measured(D(2026, 3, 1)).post()
        self.found = self.calibrate(D(2026, 6, 15), CalibrationResult.FAIL)
        call_command("setup_roles", verbosity=0)
        self.people = {}
        for role in ("Quality Inspector", "Quality Manager"):
            user = User.objects.create_user(role)
            user.groups.add(Group.objects.get(name=role))
            self.people[role] = APIClient()
            self.people[role].force_authenticate(user)

    def test_the_inspector_does_not_withdraw_the_calibration_that_found_their_balance_out(self):
        self.assertEqual(len(self.found.suspect_inspections()), 1)
        response = self.people["Quality Inspector"].post(f"/api/quality/calibrations/{self.found.pk}/void/",
                                                         {"reason": "Lab error"}, format="json")
        self.found.refresh_from_db()
        self.assertEqual((response.status_code, self.found.voided_at), (403, None), response.content)

    def test_the_manager_does(self):
        response = self.people["Quality Manager"].post(f"/api/quality/calibrations/{self.found.pk}/void/",
                                                       {"reason": "Lab error"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.found.refresh_from_db()
        self.assertIsNotNone(self.found.voided_at)
