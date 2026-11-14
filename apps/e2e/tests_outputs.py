"""
What the demo plant prints and files, generated through the API and read back (1 November outputs battery).

O409: the tax invoice printed a black box for the rupee sign and 1,764,000.00 under words saying "seventeen lakh".
O411: the PF ECR came back an empty file, as done, for a run of 13 that all had PF deducted.
O405: the demo had no floor station, so the shop-floor terminal could not be shown; no bank details, so no salary
file; no disposal account, so no asset could be sold.
O452: no demo run produced an ESI file: the demo had no ESI component, figures or numbers.
"""

import re
from decimal import Decimal as D
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.hr.models import PayRun
from apps.hr.payroll import PayComponent, PayslipLine
from apps.sales.models import Invoice
from apps.web.tests_load_demo import load

User = get_user_model()


def text_of(pdf):
    found = re.findall(rb"\(((?:\\.|[^\\)])*)\) Tj", pdf)
    return " ".join(re.sub(rb"\\(.)", rb"\1", t).decode("cp1252", "replace") for t in found)


class WhatThePlantPrintsAndFiles(TestCase):
    @classmethod
    def setUpTestData(cls):
        load(password="Trial-2026", stdout=StringIO(), stderr=StringIO())

    def as_(self, username):
        client = APIClient()
        client.force_authenticate(User.objects.get(username=username))
        return client

    def test_the_tax_invoice_prints_lakhs_and_no_rupee_box(self):
        invoice = Invoice.objects.filter(posted=True, credits=None, is_down_payment=False).order_by("pk").first()
        with mock.patch("reportlab.rl_config.pageCompression", 0):
            pdf = self.as_("rohit").get(f"/api/sales/invoices/{invoice.pk}/pdf/").content
        text = text_of(pdf)
        from apps.core.documents import grouped

        self.assertIn(grouped(invoice.total()), text)
        self.assertRegex(grouped(invoice.total()), r"^\d{1,2},\d{2},\d{3}\.\d{2}$")  # a lakh or more, in lakhs
        self.assertNotIn("₹", pdf.decode("latin-1"))

    def test_the_ecr_names_who_has_no_uan_rather_than_filing_nobody(self):
        # The demo's people carry a UAN each since O537; one without is still named, not left out of the file.
        from apps.hr.models import Employee

        Employee.objects.filter(employee_number="302").update(uan="")
        run = PayRun.objects.filter(status="posted").order_by("pk").first()
        answer = self.as_("anita").get(f"/api/hr/pay-runs/{run.pk}/ecr/")
        self.assertEqual(answer.status_code, 400, answer.content[:200])
        self.assertIn("No UAN on 302", str(answer.json()))

    def test_the_posted_run_files_esi_for_the_guard_under_the_ceiling(self):
        """
        O452, worked in /home/user/work/calc_fix_demo/o452.py: Raju's 16,000 basic and 4,500 HRA, 20,500, are
        within the 21,000 ceiling: 0.75% is 153.75, collected as 154; 3.25% is 666.25, 667. Nobody else is.
        """
        run = PayRun.objects.filter(status="posted").order_by("pk").first()
        slip = run.payslips.get(employee__employee_number="203")
        self.assertEqual({line.component.code: line.amount for line in slip.lines.filter(component__code__in=["ESI", "ESI-ER"])},
                         {"ESI": D("154.00"), "ESI-ER": D("667.00")})
        self.assertFalse(PayslipLine.objects.filter(payslip__run=run, component__code="ESI").exclude(
            payslip=slip).exclude(amount=0).exists())
        answer = self.as_("kiran").get(f"/api/hr/pay-runs/{run.pk}/esi/")
        self.assertEqual(answer.status_code, 200, answer.content[:300])
        rows = [row for row in answer.content.decode().splitlines() if row]
        self.assertEqual(len(rows), 2, rows)
        self.assertIn("3110045672", rows[1])
        self.assertIn("20500", rows[1])

    def test_the_floor_tablet_opens_each_station_and_an_operator_s_pin_is_known(self):
        from apps.hr.models import Employee
        from apps.manufacturing.station import LoomStation

        self.assertEqual(sorted(LoomStation.objects.values_list("code", flat=True)), ["CONV-1", "LOOM-EXIT", "TAPE-1"])
        self.assertEqual(LoomStation.objects.get(code="LOOM-EXIT").machines.count(), 6)
        self.assertTrue(self.client.login(username="floor", password="Trial-2026"))
        for code in ("LOOM-EXIT", "TAPE-1", "CONV-1"):
            page = self.client.get(f"/station/{code}/")
            self.assertEqual(page.status_code, 200, code)
        self.assertEqual(self.client.get("/api/sales/sales-orders/").status_code, 403)  # the tablet does nothing else
        self.assertEqual(Employee.objects.exclude(pin_digest=None).count(), 4)

    def test_the_posted_run_writes_a_salary_file_paying_everyone(self):
        from apps.hr.bank_file import SalaryTransferFile

        run = PayRun.objects.filter(status="posted").order_by("pk").first()
        made = SalaryTransferFile.prepare(run)
        slips = [slip for slip in run.payslips.all() if slip.net() > 0]
        self.assertEqual(made.rows.count(), len(slips))
        self.assertEqual(len(made.text.splitlines()), len(slips) + 2)  # a heading and a total
        self.assertEqual(made.total, sum(slip.net() for slip in slips))

    def test_every_asset_category_can_dispose(self):
        from apps.assets.models import AssetCategory

        self.assertFalse(AssetCategory.objects.filter(disposal_account=None).exists())

    def test_a_run_whose_pf_is_not_marked_is_refused_in_words_not_filed_empty(self):
        # As the demo's runs were worked out before its component was marked: the run keeps the marker it read.
        run = PayRun.objects.filter(status="posted").order_by("pk").first()
        PayslipLine.objects.filter(payslip__run=run).update(kept_statutory="")
        PayComponent.objects.filter(statutory__in=["pf", "pf_employer"]).update(statutory="")
        answer = self.as_("anita").get(f"/api/hr/pay-runs/{run.pk}/ecr/")
        self.assertEqual(answer.status_code, 400, answer.content[:200])
        self.assertIn("marked as provident fund", str(answer.json()))


class TheAgingsFootToTheLedger(TestCase):
    """O412: receivables by age left out an open credit note, 27,187.20 above the ledger."""

    @classmethod
    def setUpTestData(cls):
        load(password="Trial-2026", stdout=StringIO(), stderr=StringIO())

    def ledger(self, code, sign):
        from decimal import Decimal

        from django.db.models import Sum

        from apps.accounting.models import JournalLine

        totals = JournalLine.objects.filter(account__code=code, entry__posted=True).aggregate(d=Sum("debit"), c=Sum("credit"))
        return ((totals["d"] or 0) - (totals["c"] or 0)) * sign + Decimal("0")

    def report_total(self, body):
        from decimal import Decimal

        return sum((Decimal(str(bucket["total"])) for bucket in body.values()), Decimal("0"))

    def test_receivables_by_age_foot_to_the_receivables_account_with_the_open_credit_note(self):
        client = APIClient()
        client.force_authenticate(User.objects.get(username="rohit"))
        body = client.get("/api/sales/invoices/aging/").json()
        self.assertEqual(self.report_total(body), self.ledger("1100", 1).quantize(self.report_total(body)))
        notes = [row for row in body["credits"]["invoices"] if row["kind"] == "credit_note"]
        self.assertTrue(notes, body["credits"])
        self.assertTrue(all(row["amount_due"].startswith("-") for row in body["credits"]["invoices"]))

    def test_payables_by_age_foot_to_the_payables_account(self):
        client = APIClient()
        client.force_authenticate(User.objects.get(username="anita"))
        body = client.get("/api/purchasing/purchasing-reports/aging/").json()
        self.assertIn("credits", body)
        self.assertEqual(self.report_total(body), self.ledger("2000", -1).quantize(self.report_total(body)))
