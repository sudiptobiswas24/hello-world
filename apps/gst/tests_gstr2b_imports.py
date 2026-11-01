"""GSTR-2B's IMPG table matched with the bills of entry booked (L21), on the import of apps/purchasing/tests_imports."""

import datetime
import json
from decimal import Decimal

from apps.accounting.gst import GstSettings
from apps.accounting.models import FiscalPosition
from apps.purchasing.imports import BillOfEntry
from apps.purchasing.tests_imports import ImportPlantTestCase

from .gstr2b import keep, reconcile

D = Decimal


class ImpgMatchTests(ImportPlantTestCase):
    def setUp(self):
        super().setUp()
        GstSettings.objects.create(gstin="19AABCP1234E1ZT", state="19",
                                   interstate_position=FiscalPosition.objects.create(code="INTER", name="Inter"))
        self.import_order()

    def entry(self, number):
        entry = BillOfEntry.objects.create(
            be_number=number, be_date=datetime.date(2026, 10, 6), port_code="INCCU1", currency=self.usd,
            customs_rate=D("84.1000"), freight=D("1500.00"), customs_party=self.customs,
            payable_account=self.duty_payable, duty_account=self.duty, igst_account=self.igst_in)
        return entry

    def file(self, rows):
        return json.dumps({"data": {"gstin": "19AABCP1234E1ZT", "rtnprd": "102026", "gendt": "14-11-2026",
                                    "docdata": {"impg": rows}}})

    def test_matched_by_port_number_and_date_and_the_igst_compared(self):
        entry = self.entry("4417823")
        entry.clear_receipt(self.receipt_a, self.bill)
        entry.clear_receipt(self.receipt_b, self.bill)
        entry.post()
        keep("2026-10", self.file([
            {"refdt": "10-10-2026", "recdt": "12-10-2026", "portcode": "INCCU1", "benum": "4417823",
             "bedt": "06-10-2026", "txval": 4378456.25, "igst": 853142.00, "cess": 0, "isamd": "N"},
            {"refdt": "10-10-2026", "recdt": "12-10-2026", "portcode": "INHAL1", "benum": "5500001",
             "bedt": "08-10-2026", "txval": 100000, "igst": 20160, "cess": 0, "isamd": "N"}]))
        imports = reconcile("2026-10")["imports"]
        self.assertEqual([row["entry"]["be_number"] for row in imports["matched"]], ["4417823"])
        self.assertEqual(imports["matched"][0]["difference"], D("0.00"))
        self.assertEqual([row["line"]["be_number"] for row in imports["not_booked"]], ["5500001"])
        self.assertEqual(imports["totals"]["not_booked"], D("20160.00"))
        self.assertEqual(imports["at_risk"], [])

    def test_credit_on_a_bill_of_entry_absent_from_2b_is_at_risk(self):
        entry = self.entry("4417823")
        entry.clear_receipt(self.receipt_a, self.bill)
        entry.post()
        keep("2026-10", self.file([{"portcode": "INCCU1", "benum": "4417823", "bedt": "06-10-2026",
                                    "txval": 1, "igst": 400000, "cess": 0}]))
        differs = reconcile("2026-10")["imports"]["differs"]
        self.assertEqual(len(differs), 1)
        self.assertEqual(differs[0]["difference"], D("400000.00") - entry.totals()["igst"])
        other = self.entry("4417824")
        other.clear_receipt(self.receipt_b, self.bill)
        other.post()
        imports = reconcile("2026-10")["imports"]
        self.assertEqual([row["entry"]["be_number"] for row in imports["at_risk"]], ["4417824"])
        self.assertEqual(imports["totals"]["at_risk"], other.totals()["igst"])
