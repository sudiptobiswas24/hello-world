"""The loom exit station over HTTP, and the label it prints."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.test import SimpleTestCase
from rest_framework.test import APIClient

from apps.inventory.models import Lot

from . import barcode
from .orders import MaterialIssue, MaterialIssueLine
from .tests_orders import TODAY
from .tests_station import StationTestCase, at

GOLDEN = (
    "1101001000010001100010110001011101001101110011001110010110011101001001110011"
    "0100111011001001110110011101101110100110111001011000100010011011100100011011"
    "101001110011011101101110100110111001001110110011101101110100010001101100011"
    "101011"
)


class BarcodeTests(SimpleTestCase):
    def test_a_roll_code_encodes_to_what_an_independent_encoder_agrees(self):
        """Checked against python-barcode's symbol table when written."""
        self.assertEqual(barcode.modules("FR-261007-D-L17-07"), GOLDEN)

    def test_the_checksum(self):
        # 104 + the sum of position x value, modulo 103, worked by hand.
        self.assertEqual(barcode.values("FR-261007-D-L17-07")[-2], 35)

    def test_every_symbol_is_eleven_modules_and_distinct(self):
        self.assertTrue(all(sum(map(int, w)) == 11 for w in barcode.WIDTHS[:106]))
        self.assertEqual(sum(map(int, barcode.WIDTHS[106])), 13)
        self.assertEqual(len(set(barcode.WIDTHS)), 107)

    def test_a_character_set_b_cannot_hold_is_refused(self):
        with self.assertRaises(ValueError):
            barcode.values("FR-é")

    def test_the_drawing_has_its_quiet_zones(self):
        drawn = barcode.svg("FR-1")
        width = len(barcode.modules("FR-1")) + 2 * barcode.QUIET
        self.assertIn(f'viewBox="0 0 {width} 60"', drawn)
        self.assertIn('<rect x="10" ', drawn)


class StationApiTestCase(StationTestCase):
    def setUp(self):
        super().setUp()
        self.device = User.objects.create_user("station-lx1")
        self.device.user_permissions.add(Permission.objects.get(codename="weigh_at_station"))
        self.client = APIClient()
        self.client.force_authenticate(self.device)
        self.pin = self.operator.issue_pin()
        self.supervisor_pin = self.supervisor.issue_pin()
        self.base = f"/api/manufacturing/stations/{self.station.code}/"
        clock = patch("apps.manufacturing.station_views.timezone.now",
                      return_value=at(TODAY, 10, 42))
        clock.start()
        self.addCleanup(clock.stop)
        stamp = patch("apps.manufacturing.station.timezone.now",
                      return_value=at(TODAY, 10, 42))
        stamp.start()
        self.addCleanup(stamp.stop)

    def post(self, path, data):
        return self.client.post(self.base + path, data, format="json")

    def sign_in(self, pin=None):
        return self.post("sign-in/", {"pin": pin or self.pin})

    def roll(self, **data):
        data = {"loom": "L-17", "core": "C-76", "gross_kg": "106.8",
                "declared_m": "1006", **data}
        return self.post("rolls/", data)


class StationApiTests(StationApiTestCase):
    def test_a_device_without_the_permission_is_refused(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_user("clerk"))
        self.assertEqual(client.get(self.base).status_code, 403)

    def test_the_station_says_what_it_serves(self):
        body = self.client.get(self.base).json()
        self.assertEqual([loom["code"] for loom in body["looms"]], ["L-17", "L-20"])
        self.assertEqual(body["looms"][0]["contractor"], "Contractor A")
        self.assertEqual(body["cores"], [{"code": "C-76", "tare_kg": "2.400"}])
        self.assertEqual((body["shift"], body["shift_date"]), ("D", "2026-06-01"))
        self.assertIsNone(body["operator"])

    def test_signing_in_names_the_person(self):
        wrong = "000001" if self.pin != "000001" else "000002"
        self.assertEqual(self.sign_in(wrong).status_code, 400)
        response = self.sign_in()
        self.assertEqual(response.json()["operator"]["number"], "EMP-0142")
        self.assertEqual(self.client.get(self.base).json()["operator"]["number"], "EMP-0142")

    def test_nothing_is_weighed_before_signing_in(self):
        response = self.roll()
        self.assertEqual(response.status_code, 400)
        self.assertIn("Sign in", str(response.content))
        self.assertEqual(self.client.get(self.base + "looms/L-17/").status_code, 400)

    def test_the_loom_says_what_it_is_making(self):
        tape = self.spec.warp_tape.tape_item
        tape.tracking = "lot"
        tape.save()
        batch = Lot.objects.create(item=tape, code="TB-260601-D-03")
        self.stock(tape, "500", "120", lot=batch)
        issue = MaterialIssue.objects.create(work_order=self.run, issue_date=TODAY,
                                             warehouse=self.plant)
        MaterialIssueLine.objects.create(issue=issue, item=tape, quantity=Decimal("100"),
                                         uom=self.kg, lot=batch, line_number=1)
        issue.post()
        self.sign_in()
        self.roll()

        body = self.client.get(self.base + "looms/L-17/").json()
        self.assertEqual((body["run"], body["contractor"]), (self.run.number, "Contractor A"))
        self.assertEqual(body["specification"]["code"], "F-60")
        self.assertEqual(body["tape_batch"], "TB-260601-D-03")
        self.assertEqual(body["previous_roll"]["code"], "FR-260601-D-L17-01")

    def test_a_loom_the_station_does_not_serve(self):
        self.sign_in()
        self.assertEqual(self.client.get(self.base + "looms/L-99/").status_code, 400)

    def test_weighing_a_roll_and_printing_its_label(self):
        self.sign_in()
        response = self.roll()
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["code"], "FR-260601-D-L17-01")
        self.assertEqual(Decimal(body["variance_percent"]), Decimal("0.60"))
        self.assertFalse(body["is_exception"])

        label = self.client.get(self.base + body["label"])
        self.assertEqual(label.status_code, 200)
        page = label.content.decode()
        self.assertIn("FR-260601-D-L17-01", page)
        self.assertIn("size: 100mm 75mm", page)
        self.assertIn(barcode.svg("FR-260601-D-L17-01"), page)

    def test_a_label_for_no_weighed_roll_is_not_found(self):
        self.assertEqual(self.client.get(self.base + "label/FR-NOPE/").status_code, 404)

    def test_bad_numbers_and_cores_are_sentences(self):
        self.sign_in()
        self.assertEqual(self.roll(gross_kg="heavy").status_code, 400)
        self.assertEqual(self.roll(core="C-00").status_code, 400)
        self.assertEqual(self.roll(loom="L-99").status_code, 400)

    def test_a_typed_weight_with_a_supervisors_pin(self):
        self.sign_in()
        body = self.roll(source="manual", supervisor_pin=self.supervisor_pin,
                         reason="scale_offline").json()
        self.assertEqual(body["source"], "manual")
        self.assertEqual(body["approved_by"]["number"], "EMP-0087")

    def test_a_wrong_supervisor_pin_counts_towards_the_lock(self):
        self.sign_in()
        wrong = next(p for p in ("000001", "000002", "000003")
                     if p not in (self.pin, self.supervisor_pin))
        for _ in range(5):
            self.assertEqual(self.roll(source="manual", supervisor_pin=wrong,
                                       reason="scale_offline").status_code, 400)
        self.assertTrue(self.client.get(self.base).json()["locked"])

    def test_signing_out_ends_it(self):
        self.sign_in()
        self.post("sign-out/", {})
        self.assertEqual(self.roll().status_code, 400)

    def test_a_sign_in_lapses_after_twelve_hours(self):
        self.sign_in()
        later = at(TODAY, 10, 42) + datetime.timedelta(hours=12, minutes=1)
        with patch("apps.manufacturing.station_views.timezone.now", return_value=later):
            self.assertIsNone(self.client.get(self.base).json()["operator"])
