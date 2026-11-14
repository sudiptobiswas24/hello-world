"""
Fabric GSM, three samples a roll, limits 83.125 to 91.875.

  Ten rolls, 1 to 10 March, the baseline: centre 87.4700, mean range
  0.5400, sigma 0.3190. X-bar limits 86.9175 to 88.0225, range limits 0
  to 1.3897. Cp 4.5721, Cpk 4.5408.
  A roll at 89.5/89.8/89.6 (mean 89.6333) is beyond three sigma.
  Nine at 87.6 signal on the 8th: the last baseline roll was above too.
  Six rising, 87.2 to 87.7, signal on the 6th.
  87.9, 87.5, 87.95: the third is the second of three beyond 2 sigma.
  One reading a roll, 87.5 ... 87.6: centre 87.5000, moving range
  0.3000, sigma 0.2660, UCL 88.2979, moving-range limit 0.9801.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.inventory.models import Item, Lot, TrackingMode

from .models import Inspection, Reading
from .spc import chart
from .tests import QualityTestCase

BASELINE = [["87.2", "87.8", "87.5"], ["87.6", "87.1", "87.4"], ["87.9", "87.3", "87.6"],
            ["87.0", "87.5", "87.3"], ["87.4", "87.9", "87.7"], ["87.3", "87.6", "87.0"],
            ["87.8", "87.2", "87.5"], ["87.5", "87.4", "87.9"], ["87.1", "87.6", "87.3"],
            ["87.6", "87.3", "87.8"]]
END = datetime.date(2026, 3, 10)


def four(value):
    return round(value, 4)


class ChartTestCase(QualityTestCase):
    def setUp(self):
        super().setUp()
        self.gsm_plan = self.plan()
        self.rolls = 0

    def rolls_of(self, groups, plan=None, item=None):
        made = []
        for values in groups:
            self.rolls += 1
            day = datetime.date(2026, 3, 1) + datetime.timedelta(days=self.rolls - 1)
            lot = Lot.objects.create(item=item or self.fabric, code=f"C-{self.rolls:03d}")
            inspection = Inspection.objects.create(lot=lot, plan=plan or self.gsm_plan,
                                                   inspected_on=day, inspected_by=self.inspector)
            line = (plan or self.gsm_plan).lines.get()
            for index, value in enumerate(values, start=1):
                Reading.objects.create(inspection=inspection, plan_line=line,
                                       value=Decimal(value), sample_reference=f"S{index}")
            made.append(inspection.post())
        return made

    def signals(self, groups):
        self.rolls_of(BASELINE)
        self.rolls_of(groups)
        found = chart(self.fabric, self.gsm_check, baseline_end=END)
        return [(index - 10, point["signals"]) for index, point in enumerate(found["points"])
                if point["signals"]]


class LimitsTests(ChartTestCase):
    def test_set_from_the_baseline_within_the_subgroups(self):
        self.rolls_of(BASELINE)
        found = chart(self.fabric, self.gsm_check)
        first = found["points"][0]
        self.assertEqual((found["chart"], four(found["centre"]), four(found["sigma"])),
                         ("X-bar and R", 87.47, 0.319))
        self.assertEqual((four(first["ucl"]), four(first["lcl"]), four(first["r_ucl"]),
                          first["r_lcl"]), (88.0225, 86.9175, 1.3897, 0.0))
        self.assertEqual((four(found["capability"]["cp"]), four(found["capability"]["cpk"])),
                         (4.5721, 4.5408))
        self.assertEqual(found["capability"]["lower"], Decimal("83.125000"))
        self.assertEqual(found["notes"][0][:31], "Limits are set from every subgr")
        self.assertIn("10 baseline subgroups; limits are provisional", found["notes"][1])

    def test_later_points_are_judged_not_absorbed(self):
        self.rolls_of(BASELINE)
        self.rolls_of([["89.5", "89.8", "89.6"]])
        judged = chart(self.fabric, self.gsm_check, baseline_end=END)
        self.assertEqual((four(judged["centre"]), judged["baseline_subgroups"]), (87.47, 10))
        self.assertEqual(judged["points"][-1]["in_baseline"], False)
        absorbed = chart(self.fabric, self.gsm_check)
        self.assertNotEqual(four(absorbed["centre"]), 87.47)
        self.assertIn("capability is meaningless", absorbed["notes"][-1])

    def test_one_reading_a_roll_is_an_individuals_chart(self):
        other = Item.objects.create(sku="FAB-2", name="Lighter fabric", uom=self.kg,
                                    tracking=TrackingMode.LOT)
        single = self.plan(samples=1, item=other)
        self.rolls_of([[value] for value in ("87.5", "87.2", "87.8", "87.4", "87.6", "87.3",
                                             "87.7", "87.5", "87.4", "87.6")], single, other)
        self.rolls_of([["89.0"]], single, other)
        found = chart(other, self.gsm_check, baseline_end=datetime.date(2026, 3, 10))
        last = found["points"][-1]
        self.assertEqual((found["chart"], four(found["centre"]), four(found["sigma"])),
                         ("individuals and moving range", 87.5, 0.266))
        self.assertEqual((four(last["ucl"]), four(last["r_ucl"]), four(last["moving_range"])),
                         (88.2979, 0.9801, 1.4))
        self.assertEqual(last["signals"], ["beyond 3 sigma", "moving range beyond its limit"])
        self.assertIsNone(found["points"][0]["moving_range"])


class SignalTests(ChartTestCase):
    def test_beyond_three_sigma(self):
        self.assertEqual(self.signals([["89.5", "89.8", "89.6"]]), [(0, ["beyond 3 sigma"])])

    def test_beyond_three_sigma_though_not_four(self):
        # 88.1 against a limit of 88.0225; four sigma would be 88.2066.
        self.assertEqual(self.signals([["88.0", "88.1", "88.2"]]), [(0, ["beyond 3 sigma"])])

    def test_one_beyond_two_sigma_with_one_short_of_it_is_not_a_signal(self):
        # 87.7 is past one sigma (87.6542) and short of two (87.8383).
        self.assertEqual(self.signals([["87.6", "87.7", "87.8"], ["87.4", "87.5", "87.6"],
                                       ["87.8", "87.9", "88.0"]]), [])

    def test_runs_are_seen_from_the_first_point(self):
        self.rolls_of([["87.2", "87.6", "88.0"]] * 9 + [["86.8", "87.2", "87.6"]] * 9)
        found = chart(self.fabric, self.gsm_check)
        self.assertEqual([(index, point["signals"]) for index, point in enumerate(found["points"])
                          if point["signals"]],
                         [(8, ["9 in a row on one side"]), (17, ["9 in a row on one side"])])

    def test_a_trend_from_the_first_point(self):
        self.rolls_of([["86.6", "87.0", "87.4"], ["86.7", "87.1", "87.5"],
                       ["86.8", "87.2", "87.6"], ["86.9", "87.3", "87.7"],
                       ["87.0", "87.4", "87.8"], ["87.1", "87.5", "87.9"],
                       ["86.8", "87.2", "87.6"], ["86.9", "87.3", "87.7"]])
        found = chart(self.fabric, self.gsm_check)
        self.assertEqual([(index, point["signals"]) for index, point in enumerate(found["points"])
                          if point["signals"]], [(5, ["6 in a row rising or falling"])])

    def test_nine_in_a_row_on_one_side(self):
        self.assertEqual(self.signals([["87.6", "87.5", "87.7"]] * 9),
                         [(7, ["9 in a row on one side"]), (8, ["9 in a row on one side"])])

    def test_six_rising(self):
        self.assertEqual(self.signals([["87.2", "87.1", "87.3"], ["87.3", "87.2", "87.4"],
                                       ["87.4", "87.3", "87.5"], ["87.5", "87.4", "87.6"],
                                       ["87.6", "87.5", "87.7"], ["87.7", "87.6", "87.8"]]),
                         [(5, ["6 in a row rising or falling"])])

    def test_six_falling(self):
        self.assertEqual(self.signals([["87.7", "87.6", "87.8"], ["87.6", "87.5", "87.7"],
                                       ["87.5", "87.4", "87.6"], ["87.4", "87.3", "87.5"],
                                       ["87.3", "87.2", "87.4"], ["87.2", "87.1", "87.3"]]),
                         [(5, ["6 in a row rising or falling"])])

    def test_two_of_three_beyond_two_sigma(self):
        self.assertEqual(self.signals([["87.9", "87.8", "88.0"], ["87.5", "87.4", "87.6"],
                                       ["87.95", "87.85", "88.05"]]),
                         [(2, ["2 of 3 beyond 2 sigma"])])

    def test_a_range_too_wide(self):
        self.assertEqual(self.signals([["86.8", "87.5", "88.3"]]),
                         [(0, ["range beyond its limits"])])


class WhatIsChartedTests(ChartTestCase):
    def test_only_standing_inspections_and_enough_of_them(self):
        (first,) = self.rolls_of([BASELINE[0]])
        with self.assertRaisesMessage(ValidationError, "at least two subgroups"):
            chart(self.fabric, self.gsm_check)
        first.void("Wrong roll")
        with self.assertRaisesMessage(ValidationError, "No standing inspection"):
            chart(self.fabric, self.gsm_check)

    def test_a_window_of_dates(self):
        self.rolls_of(BASELINE)
        found = chart(self.fabric, self.gsm_check, start=datetime.date(2026, 3, 3),
                      end=datetime.date(2026, 3, 6))
        self.assertEqual([point["lot"] for point in found["points"]],
                         ["C-003", "C-004", "C-005", "C-006"])


class ChartApiTests(ChartTestCase):
    def test_asked_for_by_item_and_characteristic(self):
        self.rolls_of(BASELINE)
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("qa"))
        body = client.get("/api/quality/spc/", {"item": self.fabric.pk, "characteristic": "GSM",
                                                "baseline_end": "2026-03-10"}).json()
        self.assertEqual((body["centre"], body["sigma"], body["points"][0]["ucl"],
                          body["capability"]["cpk"]), ("87.4700", "0.3190", "88.0225", "4.5408"))
        self.assertEqual(body["points"][0]["inspected_on"], "2026-03-01")
        self.assertEqual(client.get("/api/quality/spc/", {
            "item": self.fabric.pk, "characteristic": "GSM", "end": "2026-02-01"}).status_code,
            400)
