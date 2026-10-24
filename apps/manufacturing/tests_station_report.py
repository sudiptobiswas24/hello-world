"""
The 8 AM report, worked by hand.

A shift-day on Contractor A's looms. Tape on the looms was counted at
410 kg the morning before and 395 kg this morning; 450 kg was issued in
between, so 465 kg was consumed. Four rolls of 104.4 kg net came off
(417.6 kg) and 10 kg of waste was weighed, leaving 37.4 kg — 8.04% of
consumption — unaccounted for.

Each roll supports 1,000 m. L-17's two were declared at 1,006 and
1,120 m, 2,126 against 2,000: +6.30%, over the ±2% limit. L-20's were
declared at 1,005 and 1,000: +0.25%.
"""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from rest_framework.test import APIClient

from .orders import IssueDirection, MaterialIssue, MaterialIssueLine
from .station import LoomWaste, TapeCount
from .rolls import FabricRoll
from .station_report import morning_report
from .tests_orders import TODAY
from .tests_station import StationTestCase, at

YESTERDAY = TODAY - datetime.timedelta(days=1)


class ReportTestCase(StationTestCase):
    def setUp(self):
        super().setUp()
        self.tape = self.spec.warp_tape.tape_item
        self.count(YESTERDAY, "410")
        self.count(TODAY, "395")
        self.issued = self.issue("450")
        self.weigh(declared="1006", when=at(TODAY, 9))
        self.weigh(declared="1120", when=at(TODAY, 11))
        self.weigh(declared="1005", machine=self.l20, when=at(TODAY, 12))
        self.weigh(declared="1000", machine=self.l20,
                   when=at(TODAY + datetime.timedelta(days=1), 2, 14),
                   source="manual", supervisor=self.supervisor, reason="scale_offline")
        LoomWaste.objects.create(station=self.station, contractor=self.contractor,
                                 shift_date=TODAY, kg=Decimal("10"),
                                 weighed_by=self.operator, weighed_at=at(TODAY, 19))

    def count(self, day, kg):
        return TapeCount.objects.create(
            station=self.station, contractor=self.contractor, shift_date=day,
            kg=Decimal(kg), counted_by=self.operator, counted_at=at(day, 8),
        )

    def issue(self, kg, direction=IssueDirection.ISSUE, returns=None):
        document = MaterialIssue.objects.create(
            work_order=self.run, issue_date=TODAY, warehouse=self.plant,
            contractor=self.contractor, direction=direction,
        )
        MaterialIssueLine.objects.create(
            issue=document, item=self.tape, quantity=Decimal(kg), uom=self.kg,
            line_number=1, returns_line=returns,
        )
        document.post()
        return document

    def report(self):
        return morning_report(self.station, TODAY)


class WhatCameOffTheLoomsTests(ReportTestCase):
    def test_rolls_by_shift_and_by_source(self):
        report = self.report()
        self.assertEqual(report["rolls"], 4)
        self.assertEqual(report["by_shift"], {"D": 3, "N": 1})
        self.assertEqual((report["from_scale"], report["manual"]), (3, 1))
        self.assertEqual(report["manual_percent"], Decimal("25.00"))

    def test_a_roll_voided_as_weighed_in_error_is_not_in_it(self):
        FabricRoll.objects.get(length_m=Decimal("1120")).entry.void()
        report = self.report()
        self.assertEqual(report["rolls"], 3)
        l17 = report["looms"][0]
        self.assertEqual((l17["rolls"], l17["declared"]), (1, Decimal("1006")))

    def test_metres_by_loom(self):
        l17, l20 = self.report()["looms"]
        self.assertEqual((l17["loom"], l17["rolls"]), ("L-17", 2))
        self.assertEqual((l17["from_weight"], l17["declared"]),
                         (Decimal("2000.00"), Decimal("2126")))
        self.assertEqual(l17["variance_percent"], Decimal("6.30"))
        self.assertTrue(l17["over"])
        self.assertEqual(l20["variance_percent"], Decimal("0.25"))
        self.assertFalse(l20["over"])

    def test_a_loom_is_judged_at_the_tolerance_its_rolls_were_held_to(self):
        type(self.station).objects.filter(pk=self.station.pk).update(
            metres_tolerance_percent=Decimal("10"))
        self.assertTrue(self.report()["looms"][0]["over"])

    def test_a_limit_changed_during_the_day_judges_by_the_latest(self):
        """L-17's third roll was weighed after the limit went to 10%."""
        type(self.station).objects.filter(pk=self.station.pk).update(
            metres_tolerance_percent=Decimal("10"))
        self.station.refresh_from_db()
        self.weigh(declared="1000", when=at(TODAY, 16))
        l17 = self.report()["looms"][0]
        self.assertEqual(l17["tolerance"], Decimal("10.00"))
        self.assertFalse(l17["over"])

    def test_the_overrides(self):
        (row,) = self.report()["overrides"]
        self.assertEqual(row["roll"], "FR-260601-N-L20-01")
        self.assertEqual((row["reason"], row["at"], row["approved_by"]),
                         ("Scale not connected", "02:14", "EMP-0087"))

    def test_another_day_is_another_report(self):
        self.assertEqual(morning_report(self.station, YESTERDAY)["rolls"], 0)


class TheTapeBalanceTests(ReportTestCase):
    def test_worked_by_hand(self):
        (balance,) = self.report()["balances"]
        self.assertEqual((balance["opening"], balance["issued"], balance["closing"]),
                         (Decimal("410"), Decimal("450"), Decimal("395")))
        self.assertEqual(balance["consumed"], Decimal("465"))
        self.assertEqual(balance["rolls_kg"], Decimal("417.6"))
        self.assertEqual(balance["waste"], Decimal("10"))
        self.assertEqual(balance["unaccounted"], Decimal("37.4"))
        self.assertEqual(balance["unaccounted_percent"], Decimal("8.04"))
        self.assertTrue(balance["over"])

    def test_tape_sent_back_is_not_consumed(self):
        self.issue("50", direction=IssueDirection.RETURN,
                   returns=self.issued.lines.get())
        (balance,) = self.report()["balances"]
        self.assertEqual(balance["issued"], Decimal("400"))
        self.assertEqual(balance["consumed"], Decimal("415"))

    def test_a_voided_issue_was_never_issued(self):
        self.issued.void()
        self.assertEqual(self.report()["balances"][0]["issued"], Decimal("0"))

    def test_tape_issued_to_nobody_in_particular_is_not_theirs(self):
        MaterialIssue.objects.filter(pk=self.issued.pk).update(contractor=None)
        self.assertEqual(self.report()["balances"][0]["issued"], Decimal("0"))

    def test_within_the_threshold_is_not_flagged(self):
        TapeCount.objects.filter(shift_date=TODAY).update(kg=Decimal("425"))
        (balance,) = self.report()["balances"]
        # 410 + 450 - 425 = 435 consumed; 435 - 427.6 = 7.4, 1.70%.
        self.assertEqual(balance["unaccounted_percent"], Decimal("1.70"))
        self.assertTrue(balance["over"])
        TapeCount.objects.filter(shift_date=TODAY).update(kg=Decimal("427"))
        self.assertFalse(self.report()["balances"][0]["over"])

    def test_exactly_at_the_threshold_is_not_over_it(self):
        # 410 + 450 - 420 = 440 consumed; 417.6 in rolls and 15.8 of
        # waste leave 6.6, which is 1.50% exactly.
        TapeCount.objects.filter(shift_date=TODAY).update(kg=Decimal("420"))
        LoomWaste.objects.update(kg=Decimal("15.8"))
        (balance,) = self.report()["balances"]
        self.assertEqual(balance["unaccounted_percent"], Decimal("1.50"))
        self.assertFalse(balance["over"])

    def test_a_loom_nobody_contracts_is_in_no_contractors_balance(self):
        from .machines import Machine

        own = Machine.objects.create(work_centre=self.centre, code="L-21")
        self.station.machines.add(own)
        self.weigh(machine=own, when=at(TODAY, 15))
        self.assertEqual(self.report()["balances"][0]["rolls_kg"], Decimal("417.6"))

    def test_waste_from_another_day_is_not_this_days(self):
        LoomWaste.objects.create(station=self.station, contractor=self.contractor,
                                 shift_date=YESTERDAY, kg=Decimal("99"),
                                 weighed_by=self.operator, weighed_at=at(YESTERDAY, 19))
        self.assertEqual(self.report()["balances"][0]["waste"], Decimal("10"))

    def test_a_missing_count_is_said_not_counted_as_nothing(self):
        TapeCount.objects.filter(shift_date=YESTERDAY).delete()
        (balance,) = self.report()["balances"]
        self.assertEqual(balance["missing"], ["opening"])
        self.assertIsNone(balance["unaccounted"])
        self.assertFalse(balance["over"])


class WhatToActOnTests(ReportTestCase):
    def test_the_exceptions_in_order(self):
        kinds = [row["kind"] for row in self.report()["exceptions"]]
        self.assertEqual(kinds, ["metres", "unaccounted", "overrides"])
        first, second, _ = self.report()["exceptions"]
        self.assertEqual(first["title"],
                         "L-17: declared metres 6.30% above weight-derived")
        self.assertEqual(first["detail"], "2 rolls. 2,126 m declared against 2,000 m "
                                          "from weight; limit ±2.00%.")
        self.assertEqual(second["title"],
                         "Contractor A: unaccounted tape 37.4 kg, 8.04% of consumption")

    def test_the_worst_loom_comes_first(self):
        # L-20 becomes 3,505 m declared against 3,000: +16.83%.
        self.weigh(declared="1500", machine=self.l20, when=at(TODAY, 15))
        titles = [row["title"] for row in self.report()["exceptions"]
                  if row["kind"] == "metres"]
        self.assertEqual(titles, [
            "L-20: declared metres 16.83% above weight-derived",
            "L-17: declared metres 6.30% above weight-derived",
        ])

    def test_a_loom_under_its_declared_metres_says_below(self):
        # With the two already on L-17: 3,726 m declared against 4,000.
        self.weigh(declared="800", when=at(TODAY, 13))
        self.weigh(declared="800", when=at(TODAY, 14))
        titles = [row["title"] for row in self.report()["exceptions"]]
        self.assertIn("L-17: declared metres 6.85% below weight-derived", titles)

    def test_a_missing_count_is_an_exception(self):
        TapeCount.objects.filter(shift_date=TODAY).delete()
        kinds = [row["kind"] for row in self.report()["exceptions"]]
        self.assertIn("tape_count", kinds)
        self.assertNotIn("unaccounted", kinds)

    def test_a_clean_day_has_nothing_to_act_on(self):
        from .rolls import FabricRoll

        FabricRoll.objects.filter(weight_source="manual").delete()
        FabricRoll.objects.filter(length_m=Decimal("1120")).delete()
        TapeCount.objects.filter(shift_date=TODAY).update(kg=Decimal("637.9"))
        # Two rolls are left (208.8 kg) and 10 kg of waste. 410 + 450 -
        # 637.9 = 222.1 consumed, 3.3 kg of it unaccounted: 1.49%.
        self.assertEqual(self.report()["balances"][0]["unaccounted_percent"],
                         Decimal("1.49"))
        self.assertEqual(self.report()["exceptions"], [])


class ReportApiTests(ReportTestCase):
    def setUp(self):
        super().setUp()
        self.manager = User.objects.create_user("manager")
        self.manager.user_permissions.add(Permission.objects.get(codename="view_loomstation"))
        self.client = APIClient()
        self.client.force_authenticate(self.manager)
        self.url = f"/api/manufacturing/station-reports/{self.station.code}/"

    def test_it_needs_the_permission(self):
        other = APIClient()
        other.force_authenticate(User.objects.create_user("someone"))
        self.assertEqual(other.get(self.url).status_code, 403)

    def test_the_report_as_json(self):
        body = self.client.get(self.url + "?date=2026-06-01").json()
        self.assertEqual(body["rolls"], 4)
        self.assertEqual(body["balances"][0]["unaccounted_percent"], "8.04")
        self.assertEqual(body["looms"][0]["variance_percent"], "6.30")
        self.assertEqual(body["overrides"][0]["net_kg"], "104.400")
        self.assertEqual(len(body["exceptions"]), 3)

    def test_it_defaults_to_yesterday(self):
        with patch("apps.manufacturing.station_views.timezone.localdate",
                   return_value=TODAY + datetime.timedelta(days=1)):
            self.assertEqual(self.client.get(self.url).json()["shift_date"], "2026-06-01")

    def test_a_bad_date(self):
        self.assertEqual(self.client.get(self.url + "?date=June").status_code, 400)


class CountsAndWasteAtTheStationTests(ReportTestCase):
    def setUp(self):
        super().setUp()
        device = User.objects.create_user("station-lx1")
        device.user_permissions.add(Permission.objects.get(codename="weigh_at_station"))
        self.client = APIClient()
        self.client.force_authenticate(device)
        self.base = f"/api/manufacturing/stations/{self.station.code}/"
        self.pin = self.operator.issue_pin()

    def post(self, path, data):
        return self.client.post(self.base + path, data, format="json")

    def test_a_count_needs_someone_signed_in(self):
        response = self.post("tape-count/", {"contractor": "CON-A", "kg": "400",
                                             "shift_date": "2026-06-02"})
        self.assertEqual(response.status_code, 400)

    def test_a_count_for_the_day_it_closes(self):
        self.post("sign-in/", {"pin": self.pin})
        response = self.post("tape-count/", {"contractor": "CON-A", "kg": "400",
                                             "shift_date": "2026-06-02"})
        self.assertEqual(response.status_code, 201, response.content)
        count = TapeCount.objects.get(shift_date=datetime.date(2026, 6, 2))
        self.assertEqual((count.kg, count.counted_by), (Decimal("400"), self.operator))

    def test_a_day_is_counted_once(self):
        self.post("sign-in/", {"pin": self.pin})
        response = self.post("tape-count/", {"contractor": "CON-A", "kg": "1",
                                             "shift_date": "2026-06-01"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("already counted", str(response.content))

    def test_a_count_typed_wrong_is_corrected_with_a_supervisors_pin(self):
        supervisor_pin = self.supervisor.issue_pin()
        self.post("sign-in/", {"pin": self.pin})
        data = {"contractor": "CON-A", "kg": "405", "shift_date": "2026-06-01", "correct": True}
        self.assertEqual(self.post("tape-count/", data).status_code, 400)  # no supervisor
        response = self.post("tape-count/", {**data, "supervisor_pin": self.pin})
        self.assertIn("somebody else", str(response.content))
        stranger_pin = self.employee("EMP-0999", "Not a supervisor here").issue_pin()
        response = self.post("tape-count/", {**data, "supervisor_pin": stranger_pin})
        self.assertIn("does not approve", str(response.content))
        response = self.post("tape-count/", {**data, "supervisor_pin": supervisor_pin})
        self.assertEqual(response.status_code, 201, response.content)
        old, new = TapeCount.objects.filter(shift_date=TODAY).order_by("id")
        self.assertEqual((old.kg, old.voided_by, new.kg), (Decimal("395"), self.supervisor,
                                                           Decimal("405")))
        (balance,) = self.report()["balances"]
        self.assertEqual(balance["closing"], Decimal("405"))

    def test_waste_weighed_wrong_is_withdrawn_with_a_supervisors_pin(self):
        supervisor_pin = self.supervisor.issue_pin()
        self.post("sign-in/", {"pin": self.pin})
        waste = LoomWaste.objects.get(shift_date=TODAY)
        url = f"waste/{waste.pk}/void/"
        self.assertEqual(self.post(url, {"supervisor_pin": self.pin}).status_code, 400)
        self.assertEqual(self.post(url, {"supervisor_pin": supervisor_pin}).status_code, 200)
        (balance,) = self.report()["balances"]
        self.assertEqual(balance["waste"], Decimal("0"))
        self.assertEqual(self.post(url, {"supervisor_pin": supervisor_pin}).status_code, 400)

    def test_counts_that_cannot_be(self):
        self.post("sign-in/", {"pin": self.pin})
        for data in ({"contractor": "CON-A", "kg": "-1", "shift_date": "2026-06-02"},
                     {"contractor": "CON-A", "kg": "1", "shift_date": "tomorrow"},
                     {"contractor": "CON-Z", "kg": "1", "shift_date": "2026-06-02"}):
            with self.subTest(data=data):
                self.assertEqual(self.post("tape-count/", data).status_code, 400)

    def test_waste_is_weighed_to_the_running_shift(self):
        self.post("sign-in/", {"pin": self.pin})
        with patch("apps.manufacturing.station_views.timezone.now",
                   return_value=at(TODAY + datetime.timedelta(days=1), 3)):
            response = self.post("waste/", {"contractor": "CON-A", "kg": "4.5"})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(LoomWaste.objects.filter(shift_date=TODAY).count(), 2)
        self.assertEqual(self.post("waste/", {"contractor": "CON-A", "kg": "0"}).status_code,
                         400)
