"""
The demo plant: loaded into an empty database through the application's
own posting paths, its books agree with themselves, its logins work and
see what their roles allow, and a second load is refused, so it can
never mix invented records into real books.
"""

import calendar
import datetime
import json
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.imports.tests import TwoYearsOn
from apps.web.health import integrity


def load(**options):
    """
    load_demo, with its migration guard told the schema is complete: a test
    database built without running the migrations (TEST MIGRATE False) reads
    to it as one still being built. The guard has its own test below.
    """
    with mock.patch("apps.web.management.commands.load_demo.MigrationExecutor") as executor:
        executor.return_value.migration_plan.return_value = []
        # A section that fails writes why to stderr; a test that hands in a StringIO would hide it, and a failure
        # read "these sections failed" with nothing to say why (g39's pg-c). Said in the error instead.
        told = options.get("stderr")
        try:
            call_command("load_demo", **options)
        except CommandError as refused:
            why = told.getvalue() if isinstance(told, StringIO) else ""
            raise CommandError(f"{refused}\n{why}") from refused


class LoadDemoTests(TestCase):
    def test_a_whole_plant_loads_and_its_books_agree(self):
        out = StringIO()
        load(password="Trial-2026", stdout=out, stderr=StringIO())
        self.assertIn("The demo plant is loaded.", out.getvalue())
        self.assertEqual([finding["label"] for finding in integrity(fresh=True)["findings"] if finding["ok"] is False], [])
        self.assert_the_agings_foot_with_a_foreign_document_open()
        self.assert_every_export_went_under_the_undertaking_for_its_year()

        # A freight charge to put on a document from its add row (O495), taxed and booked on both sides.
        from apps.accounting.models import ChargeType

        freight = ChargeType.objects.get(code="FREIGHT")
        self.assertEqual((freight.revenue_account.code, freight.expense_account.code, freight.is_active),
                         ("4300", "5610", True))
        self.assertEqual(sorted(freight.taxes.values_list("code", flat=True)), ["CGST9", "SGST9"])

        # A rep signs in and sees only the customers they carry.
        self.assertTrue(self.client.login(username="imran", password="Trial-2026"))
        rows = self.client.get("/api/sales/sales-orders/", {"page_size": 100}).json()
        self.assertEqual({row["customer_name"] for row in rows},
                         {"Sahyadri Cement Works", "Konkan Agro Foods", "Malwa Seeds and Grains"})

        # Loaded once, the database holds records, and a second load touches nothing.
        parties = Party.objects.count()
        with self.assertRaisesMessage(CommandError, "already holds records"):
            load(stdout=StringIO())
        self.assertEqual(Party.objects.count(), parties)

    def assert_the_agings_foot_with_a_foreign_document_open(self):
        """
        O416: each aging, in rupees, is what its control accounts hold, with a dollar document open on each
        side: the export of 6,000 sacks at USD 0.19 and the second container of filler, 16,000 kg at USD 1.25.
        """
        from django.db.models import Sum
        from django.utils import timezone

        from apps.accounting.models import JournalLine
        from apps.purchasing.models import ap_aging, payable_accounts
        from apps.sales.models import ar_aging, receivable_accounts

        def held(accounts):
            rows = JournalLine.objects.filter(account__in=accounts, entry__posted=True).aggregate(
                debit=Sum("debit"), credit=Sum("credit"))
            return (rows["debit"] or 0) - (rows["credit"] or 0)

        today = timezone.localdate()
        for report, accounts, rows, sign in ((ar_aging(as_of=today), receivable_accounts(), "invoices", 1),
                                             (ap_aging(as_of=today), payable_accounts(), "bills", -1)):
            foreign = [(row["currency"], row["due_in_currency"]) for bucket in report.values() for row in bucket[rows]
                       if row["currency"] == "USD"]
            self.assertTrue(foreign, rows)
            self.assertEqual(sum(bucket["total"] for bucket in report.values()), sign * held(accounts), rows)

    def assert_every_export_went_under_the_undertaking_for_its_year(self):
        """
        O569: one LUT for each financial year from the story's first month to today, each for the whole year its
        dates fall in and filed before it opens, and each export under the one for its own day's year.
        """
        from django.utils import timezone

        from apps.accounting.gst import LetterOfUndertaking
        from apps.accounting.tds import financial_year
        from apps.sales.models import Invoice
        from apps.web.management.commands.load_demo import Story

        today = timezone.localdate()
        years, opens = [], financial_year(Story(today).on(2026, 7, 1))[0]
        while opens <= today:
            years.append(financial_year(opens))
            opens = years[-1][1] + datetime.timedelta(days=1)
        luts = LetterOfUndertaking.objects.order_by("valid_from")
        self.assertEqual([(lut.valid_from, lut.valid_to) for lut in luts], years)
        for lut in luts:
            self.assertEqual(lut.financial_year, f"{lut.valid_from.year}-{str(lut.valid_to.year)[2:]}")
            self.assertLess(lut.filed_on, lut.valid_from)
        exports = Invoice.objects.filter(customer__code="C-GULF", posted=True)
        self.assertEqual(exports.count(), 2)
        for export in exports:
            under = LetterOfUndertaking.in_force(export.invoice_date)
            self.assertEqual(financial_year(export.invoice_date), (under.valid_from, under.valid_to))
            self.assertEqual(export.issued_as["lut"]["arn"], under.arn)

    def test_a_database_with_anything_in_it_is_refused(self):
        customer = Party.objects.create(code="C-1", name="A real customer")
        PartyRoleAssignment.objects.create(party=customer, role=PartyRole.CUSTOMER)
        with self.assertRaisesMessage(CommandError, "The demo is for an empty installation only"):
            load(stdout=StringIO())
        self.assertEqual(list(Party.objects.values_list("code", flat=True)), ["C-1"])

    def test_a_database_still_being_built_is_told_to_wait(self):
        """Run during the first start, before the migrations finish: refused in words, nothing made."""
        with mock.patch("apps.web.management.commands.load_demo.MigrationExecutor") as executor:
            executor.return_value.migration_plan.return_value = [("hr.0018_people", False)]
            with self.assertRaisesMessage(CommandError, "still being built: 1 migration(s) to go"):
                call_command("load_demo", stdout=StringIO())
        self.assertFalse(Party.objects.exists())


class LoadDemoTestsTwoYearsOn(TwoYearsOn, LoadDemoTests):
    """
    The plant loaded two years on (O569). The story's dates move with the calendar; anything in it written down
    as a year does not. Its letter of undertaking kept "2026-27" while its dates moved, and every load from April
    2027 was refused: worked month by month in /home/user/work/calc_g41_kept/o569_lut.py.
    """


class LoadDemoTestsInMay(LoadDemoTestsTwoYearsOn):
    """Loaded on 15 May two calendar years on: the story's two exports fall either side of 1 April (O569)."""

    DAYS_ON = (datetime.date(datetime.date.today().year + 2, 5, 15) - datetime.date.today()).days


class WhatTheDemoShowsTests(TestCase):
    """O405: the plant's own features the demo could not show, each with the figure it should come to."""

    @classmethod
    def setUpTestData(cls):
        load(password="Trial-2026", stdout=StringIO(), stderr=StringIO())

    def test_professional_tax_is_maharashtras_and_a_womans_salary_up_to_25000_pays_none(self):
        from apps.hr.payroll import Payslip

        slips = Payslip.objects.filter(run__status="posted")
        posted = {slip.employee.employee_number: [line.amount for line in slip.lines.all() if line.component.code == "PT"]
                  for slip in slips}
        # The story's months move with today (Story): the posted run is two months back, and February's band is 300.
        due = [Decimal("300.00") if slips.first().run.period_start.month == 2 else Decimal("200.00")]
        # Lakshmi: 17,800 basic and 5,000 HRA is 22,800, under a woman's 25,000. Sunita: 27,000, over it.
        self.assertEqual(posted["303"], [])
        self.assertEqual(posted["202"], due)
        # A man over 10,000 pays it; the security guard's 20,500 is over it.
        self.assertEqual(posted["203"], due)

    def test_provident_fund_is_taken_on_basic_up_to_the_ceiling_as_the_plants_own_setup_takes_it(self):
        """
        O532, worked in /home/user/work/calc_g37_demo/o532.py. The demo took 12% of basic and HRA with no ceiling:
        Rohit's 62,000 basic and 18,000 HRA paid 9,600 a side, and the plant 69,060. As setup_payroll seeds it, PF is
        12% of basic up to the EPF wage ceiling standing on the run's last day: 25,000 from 17 September 2026
        (statutory/provident_fund.csv), which every run the demo can now post ends after. Nine people are over
        the ceiling at 3,000 each and five under it on their basic (2,520, 1,920, 2,220, 2,136, 2,064): 37,860.
        """
        from django.db.models import Sum

        from apps.hr.models import PayRun
        from apps.hr.payroll import PayComponent, PayslipLine

        self.assertEqual(sorted(PayComponent.objects.filter(statutory__in=["pf", "pf_employer"]).values_list(
            "code", "ceiling_figure", "base_components__code")),
            [("PF", "epf_ceiling", "BASIC"), ("PF", "epf_ceiling", "DA"),
             ("PF-ER", "epf_ceiling", "BASIC"), ("PF-ER", "epf_ceiling", "DA")])
        run = PayRun.objects.get(status="posted")
        self.assertGreaterEqual(run.period_end, datetime.date(2026, 9, 30))
        share = {line.payslip.employee.employee_number: line.amount
                 for line in PayslipLine.objects.filter(payslip__run=run, component__code="PF")}
        self.assertEqual(share, {
            "101": Decimal("3000.00"), "102": Decimal("3000.00"), "103": Decimal("3000.00"), "201": Decimal("3000.00"),
            "202": Decimal("2520.00"), "203": Decimal("1920.00"), "301": Decimal("3000.00"), "302": Decimal("2220.00"),
            "303": Decimal("2136.00"), "304": Decimal("2064.00"), "401": Decimal("3000.00"), "501": Decimal("3000.00"),
            "601": Decimal("3000.00"), "602": Decimal("3000.00")})
        totals = dict(PayslipLine.objects.filter(payslip__run=run).values_list("component__code").annotate(Sum("amount")))
        self.assertEqual((totals["PF"], totals["PF-ER"]), (Decimal("37860.00"), Decimal("37860.00")))
        self.assertEqual(run.payslips.get(employee__employee_number="101").net(), Decimal("76800.00"))
        # The run's journal: wages 575,500 and the employer's 37,860 of PF with 667 of ESI, against what is owed.
        # Professional tax is 13 at 200, and February's band is 300 (the story's months move with today).
        # The gratuity provision, 21,524.75 (O560), is the employer's own cost: net pay is as it was.
        tax = Decimal("3900") if run.period_start.month == 2 else Decimal("2600")
        booked = {row["account__code"]: row["debit"] - row["credit"] for row in run.journal_entry.lines.values(
            "account__code").annotate(debit=Sum("debit"), credit=Sum("credit"))}
        self.assertEqual(booked, {"6000": Decimal("575500"), "6100": Decimal("38527"), "2410": Decimal("-75720"),
                                  "2420": Decimal("-821"), "2430": -tax, "2400": tax - Decimal("537486"),
                                  "6120": Decimal("21524.75"), "2470": Decimal("-21524.75")})

    def test_everyone_is_given_the_gratuity_provision_and_the_posted_run_accrues_it(self):
        """
        O560, worked in /home/user/work/calc_g40_people/o560.py. The demo seeded GRATUITY and gave it to nobody:
        go_live_check's Gratuity line named all fourteen people and no run accrued a rupee. Given as setup_payroll
        gives it, 4.81% of each basic (the demo pays no DA), at the paisa: 2,982.20 on Rohit's 62,000 and 856.18
        on Lakshmi's 17,800; 4.81% of the 447,500 of basic is 21,524.75, into 2470 against 6120.
        """
        from django.db.models import Sum

        from apps.hr.models import PayRun
        from apps.hr.payroll import PayslipLine
        from apps.imports.management.commands.go_live_check import OK, checks

        run = PayRun.objects.get(status="posted")
        accrued = {line.payslip.employee.employee_number: line.amount
                   for line in PayslipLine.objects.filter(payslip__run=run, component__code="GRATUITY")}
        self.assertEqual(len(accrued), 14)
        self.assertEqual((accrued["101"], accrued["303"], accrued["302"]),
                         (Decimal("2982.20"), Decimal("856.18"), Decimal("889.85")))
        self.assertEqual(sum(accrued.values()), Decimal("21524.75"))
        held = run.journal_entry.lines.filter(account__code="2470").aggregate(debit=Sum("debit"), credit=Sum("credit"))
        self.assertEqual((held["debit"], held["credit"]), (Decimal("0"), Decimal("21524.75")))
        # go_live_check's line, which named all fourteen.
        lines = {what: (level, detail) for level, what, detail in checks()}
        self.assertEqual(lines["Statutory: Gratuity"], (OK, "confirmed figures cover today"))

    def test_the_specifications_build_the_recipes_and_the_tapes_lots_are_inspected_and_released(self):
        from apps.manufacturing.models import BillOfMaterials
        from apps.quality.models import Disposition, Inspection
        from apps.quality.release import release_status

        tape = BillOfMaterials.objects.get(item__sku="TAPE-1000")
        self.assertTrue(tape.is_computed)
        self.assertEqual({row.item.sku: row.quantity for row in tape.components.all()},
                         {"PP-RAFFIA": Decimal("78"), "REGRIND": Decimal("5"), "CACO3-MB": Decimal("16"),
                          "UV-MB": Decimal("1")})
        for sku, made_of in (("FAB-50-87", "TAPE-1000"), ("SACK-ATTA25", "FAB-50-87"), ("FILM-LF40", "LDPE-GRAN")):
            bom = BillOfMaterials.objects.get(item__sku=sku)
            self.assertTrue(bom.is_computed, sku)
            self.assertIn(made_of, {row.item.sku for row in bom.components.all()}, sku)
        inspections = Inspection.objects.filter(lot__item__sku="TAPE-1000", posted=True).order_by("lot__code")
        self.assertEqual([(i.lot.code, i.disposition, release_status(i.lot)) for i in inspections],
                         [("TP-1000-01", Disposition.ACCEPT, "released"), ("TP-1000-02", Disposition.ACCEPT, "released")])

    def test_the_executives_earn_commission_the_meters_are_read_and_leave_carries(self):
        from django.utils import timezone

        from apps.hr.models import Employee, LeavePolicy
        from apps.manufacturing.energy import MeterReading
        from apps.sales.models import SalesRep

        self.assertEqual({rep.party.name: rep.plan and rep.plan.percent for rep in SalesRep.objects.all()},
                         {"Rohit Bhosale": None, "Imran Shaikh": Decimal("1.50"), "Priya Nair": Decimal("1.50")})
        self.assertEqual(MeterReading.objects.filter(meter__code="EM-LOOMS").count(), 7)
        # Earned leave on 1 July: six months of 18 a year is 9, and the 6 carried from last year.
        year = timezone.localdate().year
        lakshmi = Employee.objects.get(employee_number="303")
        self.assertEqual(LeavePolicy.objects.get(code="EL").entitlement_for(lakshmi, year, as_of=datetime.date(year, 7, 1)),
                         Decimal("15.00"))


class SignsInAsTheDemo:
    def as_(self, username):
        """The demo person, signed in by the demo password, with the second factor their role is asked for."""
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        from apps.core.tests_second_factor import for_tests

        client = APIClient()
        self.assertTrue(client.login(username=username, password="Trial-2026"), username)
        for_tests(client, get_user_model().objects.get(username=username))
        return client


class TheStatutoryOutputsComeOutTests(SignsInAsTheDemo, TestCase):
    """
    O537: every statutory output comes out of the demo as loaded, by its API route, for the person whose work it
    is. Until g37 six of them could not: no UAN or date of birth for the ECR; no e-invoicing date, company address,
    state or country for an e-invoice or e-way bill; no section, rate or payable account for TDS; no challan for
    ITC-04 and no statement for the 2B match. Each figure is worked in /home/user/work/calc_g37_demo/o537.py
    (and o532.py for the ECR), from the story's own quantities and prices.
    """

    @classmethod
    def setUpTestData(cls):
        load(password="Trial-2026", stdout=StringIO(), stderr=StringIO())

    def invoice_of(self, customer, taxable):
        from apps.sales.models import Invoice

        found = [invoice for invoice in Invoice.objects.filter(customer__code=customer, posted=True, credits=None)
                 if invoice.subtotal() == Decimal(taxable)]
        self.assertEqual(len(found), 1, (customer, taxable))
        return found[0]

    def test_the_pf_ecr_names_all_fourteen_members_at_the_ceiling(self):
        from apps.hr.models import PayRun

        run = PayRun.objects.get(status="posted")
        self.assertGreaterEqual(run.period_end, datetime.date(2026, 9, 30))  # the 25,000 ceiling, as o532.py
        answer = self.as_("kiran").get(f"/api/hr/pay-runs/{run.pk}/ecr/")
        self.assertEqual(answer.status_code, 200, answer.content[:300])
        rows = [row.split("#~#") for row in answer.content.decode().splitlines() if row]
        self.assertEqual(len(rows), 14)
        # Rohit: 80,000 gross; EPF, EPS and EDLI wages 25,000; 3,000; 8.33% is 2,082.50, 2,083; the rest 917.
        self.assertEqual(rows[0], ["100900000101", "Rohit Bhosale", "80000", "25000", "25000", "25000", "3000",
                                   "2083", "917", "0", "0"])
        self.assertEqual([sum(int(row[column]) for row in rows) for column in (2, 3, 4, 6, 7, 8)],
                         [575500, 315500, 315500, 37860, 26286, 11574])

    def test_an_e_invoice_is_built_for_a_sale_in_another_state_and_for_an_export(self):
        accountant = self.as_("anita")
        # Kaveri Feeds at Mysuru: 30,000 at 14.20 and 18,000 at 11.20, integrated tax at 18%.
        answer = accountant.post("/api/gst/e-invoices/", {"invoice": self.invoice_of("C-KAVR", "627600").pk}, format="json")
        self.assertEqual(answer.status_code, 201, answer.content[:300])
        payload = json.loads(answer.content, parse_float=Decimal)["payload"]
        self.assertEqual((payload["SellerDtls"]["Stcd"], payload["SellerDtls"]["Pin"], payload["BuyerDtls"]["Stcd"],
                          payload["BuyerDtls"]["Pin"]), ("27", 410501, "29", 570016))
        self.assertEqual([payload["ValDtls"][key] for key in ("AssVal", "IgstVal", "CgstVal", "TotInvVal")],
                         [Decimal("627600"), Decimal("112968"), Decimal("0"), Decimal("740568")])
        # Gulf Bulk at Dubai, under LUT: USD 17,100 at 83.45, no tax, and the country the goods leave for.
        answer = accountant.post("/api/gst/e-invoices/", {"invoice": self.invoice_of("C-GULF", "17100").pk}, format="json")
        self.assertEqual(answer.status_code, 201, answer.content[:300])
        payload = json.loads(answer.content, parse_float=Decimal)["payload"]
        self.assertEqual((payload["TranDtls"]["SupTyp"], payload["ExpDtls"]), ("EXPWOP", {"ForCur": "USD", "CntCode": "AE"}))
        self.assertEqual([payload["ValDtls"][key] for key in ("AssVal", "IgstVal", "TotInvVal")],
                         [Decimal("1426995"), Decimal("0"), Decimal("1426995")])

    def test_an_e_way_bill_is_built_within_the_state_and_across_a_state_line(self):
        accountant = self.as_("anita")
        moving = {"mode": "1", "distance_km": 840, "vehicle_number": "MH12AB1234"}
        # Sahyadri Cement at Pune: 165,000 sacks at 9.80; central and state tax at 9% each.
        answer = accountant.post("/api/gst/eway-bills/", {"invoice": self.invoice_of("C-SAHY", "1617000").pk, **moving},
                                 format="json")
        self.assertEqual(answer.status_code, 201, answer.content[:300])
        payload = json.loads(answer.content, parse_float=Decimal)["payload"]
        self.assertEqual([payload[key] for key in ("fromStateCode", "fromPincode", "toStateCode", "toPincode", "totalValue",
                                                   "cgstValue", "sgstValue", "igstValue", "totInvValue")],
                         [27, 410501, 27, 411019, Decimal("1617000"), Decimal("145530"), Decimal("145530"), Decimal("0"),
                          Decimal("1908060")])
        answer = accountant.post("/api/gst/eway-bills/", {"invoice": self.invoice_of("C-KAVR", "627600").pk, **moving},
                                 format="json")
        self.assertEqual(answer.status_code, 201, answer.content[:300])
        payload = json.loads(answer.content, parse_float=Decimal)["payload"]
        self.assertEqual([payload[key] for key in ("toStateCode", "totalValue", "igstValue", "totInvValue")],
                         [29, Decimal("627600"), Decimal("112968"), Decimal("740568")])

    def test_tax_deducted_from_the_contractor_is_on_the_quarters_list_with_its_challan(self):
        from apps.purchasing.tds import TdsDeduction

        deduction = TdsDeduction.objects.get()
        # 60,000 of lamination and 10,800 of GST; 2% of the 60,000 is 1,200, and 69,600 is left to pay.
        self.assertEqual((deduction.bill.total(), deduction.bill.amount_due()), (Decimal("70800.00"), Decimal("69600.00")))
        start = deduction.date.replace(month=(deduction.date.month - 1) // 3 * 3 + 1, day=1)
        end = start.replace(month=start.month + 2, day=calendar.monthrange(start.year, start.month + 2)[1])
        buyer = self.as_("ravi")
        answer = buyer.get("/api/purchasing/tds-deductions/quarter/", {"from": start.isoformat(), "to": end.isoformat()})
        self.assertEqual(answer.status_code, 200, answer.content[:300])
        self.assertEqual([(row["vendor"], row["pan"], row["base"], row["rate_percent"], row["amount"], row["challan"])
                          for row in answer.json()["rows"]],
                         [("Shree Lamination Works", "AAQFS6141H", "60000.00", "2.0000", "1200.00", "0510308/00417")])
        answer = buyer.get("/api/purchasing/tds-challans/")
        self.assertEqual(answer.status_code, 200, answer.content[:300])
        self.assertEqual([(row["amount"], row["challan_number"], row["voided"]) for row in answer.json()],
                         [("1200.00", "00417", None)])

    def test_itc_04_lists_the_fabric_out_at_the_job_worker(self):
        from apps.manufacturing.jobwork import JobWorkChallan

        day = JobWorkChallan.objects.get().challan_date
        year = day.year if day.month >= 4 else day.year - 1
        start, end = ((datetime.date(year, 4, 1), datetime.date(year, 9, 30)) if 4 <= day.month <= 9
                      else (datetime.date(year, 10, 1), datetime.date(year + 1, 3, 31)))
        answer = self.as_("anita").get("/api/gst/itc04/", {"start": start.isoformat(), "end": end.isoformat()})
        self.assertEqual(answer.status_code, 200, answer.content[:300])
        # 600 kg at the fabric's cost of 139 a kg, to a job worker in Maharashtra: 9% and 9% had it been a supply.
        self.assertEqual([(row["name"], row["state"], row["hsn"], row["uqc"], row["quantity"], row["taxable_value"],
                           row["rates"]["central"], row["rates"]["state"]) for row in answer.json()["sent"]],
                         [("Shree Lamination Works", "27", "54074200", "KGS", "600.0000", "83400.00", "9.00", "9.00")])

    def test_the_2b_match_shows_two_bills_matched_and_the_rest_not_filed(self):
        from apps.gst.gstr2b import Gstr2bStatement

        period = Gstr2bStatement.objects.get().period
        answer = self.as_("anita").get("/api/gst/gstr2b/match/", {"period": period})
        self.assertEqual(answer.status_code, 200, answer.content[:300])
        found = answer.json()
        # In the statement and matched to the paisa: the polymer (1,816,000) and the inks (84,000).
        self.assertEqual([(row["bill"]["vendor"], row["bill"]["taxable"], row["difference"]["value"])
                          for row in found["matched"]],
                         [("V-GRAN - Granule House Polymers", "1816000.00", "0.00"),
                          ("V-INK - Bharat Printing Inks", "84000.00", "0.00")])
        self.assertEqual(found["differs"], [])
        # Not filed by the supplier: the month's liner film (312,500) and the three bills of the month before.
        self.assertEqual(sorted(Decimal(row["bill"]["taxable"]) for row in found["not_in_2b"]),
                         [Decimal("65000"), Decimal("198500"), Decimal("312500"), Decimal("1680000")])
        self.assertEqual((found["totals"]["filed"], found["totals"]["matched"], found["totals"]["waiting"]),
                         ("342000.00", "342000.00", "406080.00"))


class TheFloorScreensShowTheDemoTests(SignsInAsTheDemo, TestCase):
    """
    O605: the floor's screens had nothing on the demo, so a person trying them saw them do nothing. The demo now
    weaves F-50 on LOOM-05 and weighs five rolls at the loom exit yesterday, one typed while the scale was being
    calibrated; cuts two of them into atta sacks, the press counting 1,050 and the stitching tables two bundles of
    500 and 30 spoiled; bales the bundles; books the crews' hours; and keeps the floor's attendance for the week.
    Each screen is read by its route as the person who reads it. Figures worked in
    /home/user/work/calc_g53/o605.py.
    """

    @classmethod
    def setUpTestData(cls):
        load(password="Trial-2026", stdout=StringIO(), stderr=StringIO())

    def window(self):
        from django.utils import timezone

        today = timezone.localdate()
        return f"?start={today - datetime.timedelta(days=7)}&end={today}"

    def read(self, username, path):
        answer = self.as_(username).get(path)
        self.assertEqual(answer.status_code, 200, (path, answer.content[:300]))
        return json.loads(answer.content)

    def test_the_morning_report_has_yesterdays_five_rolls_and_the_typed_one(self):
        report = self.read("suresh", "/api/manufacturing/station-reports/LOOM-EXIT/")
        self.assertEqual((report["rolls"], report["by_shift"], report["from_scale"], report["manual"]),
                         (5, {"A": 5}, 4, 1))
        self.assertEqual(report["looms"], [{"loom": "LOOM-05", "rolls": 5, "from_weight": "4997.72",
                                            "declared": "5070.00", "variance_percent": "1.45",
                                            "tolerance_percent": "2.00", "over": False}])
        self.assertEqual([(row["reason"], row["net_kg"]) for row in report["overrides"]],
                         [("Scale under calibration", "88.100")])
        rolls = self.read("suresh", "/api/manufacturing/fabric-rolls/")
        self.assertEqual(sorted((row["length_m"], row["net_weight_kg"], row["within_tolerance"]) for row in rolls),
                         [("1000.00", "87.600", True), ("1005.00", "88.100", True), ("1010.00", "88.900", True),
                          ("1060.00", "86.000", False), ("995.00", "86.700", True)])
        # F-50 has a code and no name: the Fabric column reads the code rather than nothing.
        self.assertEqual({row["specification_name"] for row in rolls}, {"F-50"})

    def test_the_sacks_were_counted_at_each_step_scrapped_by_reason_and_baled(self):
        steps = self.read("suresh", "/api/manufacturing/operation-reports/")
        self.assertEqual([(row["step"].split(" · ")[1], row["quantity_good"]) for row in steps],
                         [("10 Print", "1050.0000")])
        scrap = self.read("suresh", "/api/manufacturing/run-flow/scrap/" + self.window())
        self.assertEqual([(row["item"], row["operation"], row["reason"], row["quantity"]) for row in scrap],
                         [("SACK-ATTA25", "Cut and stitch", "EDGE", "12"),
                          ("SACK-ATTA25", "Cut and stitch", "STITCH", "18")])
        bales = self.read("suresh", "/api/manufacturing/bales/")
        self.assertEqual([(bale["item"], bale["status"], bale["bags"], [line["bags"] for line in bale["lines"]])
                          for bale in bales], [("SACK-ATTA25", "sealed", "1000", ["500", "500"])])

    def test_the_crews_hours_read_as_time_booked_and_operator_yield(self):
        self.assertEqual(len(self.read("suresh", "/api/manufacturing/time-bookings/")), 4)
        rows = self.read("suresh", "/api/manufacturing/operator-yield/" + self.window())
        self.assertEqual(sorted((row["operator"], row["work_centre"], row["minutes"], row["ideal_minutes"],
                                 row["performance"]) for row in rows),
                         [("302", "EXT", "300.00", "258.67", "0.8622"), ("302", "PRINT", "13.00", "10.50", "0.8077"),
                          ("303", "LOOM", "86.00", "72.88", "0.8475"), ("304", "CONV", "14.00", "12.00", "0.8571")])

    def test_the_floor_kept_its_attendance_for_the_week(self):
        from django.utils import timezone

        today = timezone.localdate()
        worked = [today - datetime.timedelta(days=back) for back in range(7, 0, -1)
                  if (today - datetime.timedelta(days=back)).weekday() != 6]
        rows = self.read("kiran", "/api/hr/attendance/?page_size=100")
        self.assertEqual(sorted({(row["employee_number"], row["on"]) for row in rows}),
                         sorted((number, str(on)) for on in worked for number in ("301", "302", "303", "304")))
        self.assertEqual({row["status"] for row in rows}, {"present"})
        self.assertEqual([(row["employee_number"], row["late_minutes"]) for row in rows if row["late_minutes"]],
                         [("304", 25)] if today - datetime.timedelta(days=3) in worked else [])


class TheStoresAndPlanningShowTheDemoTests(SignsInAsTheDemo, TestCase):
    """
    O613 (stores and planning): the stores' documents, the gate and planning answered nothing on the demo. The demo
    now counts the spares cupboard two bearings short, writes off 150 rice sacks spoilt by a leak, moves 10,000
    cement sacks to the godown's first bay, weighs a lorry of granule in and out, sends a laptop out on a returnable
    pass with a visitor still on site, and plans the plant once from a year of shipments, a forecast and a build
    ahead. Each is read by its route as the person whose screen it is.
    """

    @classmethod
    def setUpTestData(cls):
        load(password="Trial-2026", stdout=StringIO(), stderr=StringIO())

    def read(self, username, path):
        answer = self.as_(username).get(path)
        self.assertEqual(answer.status_code, 200, (path, answer.content[:300]))
        return json.loads(answer.content)

    def test_the_store_counted_wrote_off_and_moved(self):
        (count,) = self.read("sunita", "/api/inventory/stock-counts/")
        self.assertEqual((count["posted"], count["reason_name"], [(line["item_label"].split(" ")[0], line["variance"])
                                                                   for line in count["lines"]]),
                         (True, "Short on count", [("SP-6205", "-2.0000")]))
        spoilt = [row for row in self.read("sunita", "/api/inventory/stock-adjustments/")
                  if row["reason_name"] == "Damaged or spoilt"]
        self.assertEqual([(row["posted"], [(line["item_label"].split(" ")[0], line["quantity"]) for line in row["lines"]])
                          for row in spoilt], [(True, [("SACK-RICE25", "-150.0000")])])
        (move,) = self.read("sunita", "/api/inventory/stock-transfers/")
        self.assertEqual((move["status"], move["to_name"], [(line["item_label"].split(" ")[0], line["quantity"])
                                                            for line in move["lines"]]),
                         ("received", "Finished goods godown", [("SACK-CEM50", "10000.0000")]))

    def test_the_gate_weighed_a_lorry_passed_a_laptop_out_and_has_a_visitor_in(self):
        visits = self.read("raju", "/api/inventory/gate-visits/")
        self.assertEqual(sorted((visit["purpose"], visit["status"]) for visit in visits),
                         [("gate_pass", "out"), ("inward_order", "out"), ("visitor", "in")])
        (weighing,) = self.read("sunita", "/api/inventory/weighings/")
        self.assertEqual((weighing["gross_kg"], weighing["tare_kg"], weighing["net_kg"], weighing["state"]),
                         ("27460.000", "9380.000", "18080.000", "weighed"))
        (laptop,) = self.read("sunita", "/api/inventory/gate-passes/")
        self.assertEqual((laptop["kind"], laptop["status"], laptop["approved_by_name"],
                          [line["outstanding"] for line in laptop["lines"]]),
                         ("returnable", "out", "sunita", ["1.0000"]))

    def test_the_plan_ran_on_a_years_shipments_a_forecast_and_a_build_ahead(self):
        self.assertEqual(len(self.read("suresh", "/api/planning/shipment-history/")), 12)
        (forecast,) = self.read("suresh", "/api/planning/forecasts/")
        self.assertEqual((forecast["item_label"].split(" ")[0], forecast["quantity"]), ("SACK-CEM50", "200000.0000"))
        (ahead,) = self.read("suresh", "/api/planning/master-schedule/")
        self.assertEqual((ahead["item_label"].split(" ")[0], ahead["quantity"]), ("SACK-FERT50", "30000.0000"))
        (run,) = self.read("suresh", "/api/planning/runs/")
        self.assertTrue(run["is_complete"])
        orders = self.read("suresh", "/api/planning/planned-orders/")
        # The cement sacks the open sales orders want, made: the plan answers from the orders the demo holds.
        self.assertIn(("SACK-CEM50", "make", "sales"),
                      {(order["item_sku"], order["kind"], demand["source"]) for order in orders
                       for demand in order["demands"]})
        self.assertTrue(self.read("suresh", "/api/planning/actions/"))
        self.assertEqual(len(self.read("suresh", "/api/planning/settings/")), 1)


class TheTradingListsShowTheDemoTests(SignsInAsTheDemo, TestCase):
    """
    O613 (prices, dunning, recurring invoices, blanket agreements, reorder rules): each answered nothing on the demo.
    The dealers' price list is kept and given to nobody, so no order's price moves under it; the reminders run once
    for post, so nothing is emailed; the monthly charge for Sahyadri's printing cylinders starts next month. The
    year's raffia granule is on a blanket agreement with the first 18,000 kg called off as a draft, and the UV
    masterbatch has a reorder rule. The latest invoice in rupees has its e-invoice and e-way bill built, waiting for
    the portals. An atta sack is costed for a quote from the month's rates, and next year's standards are drafted.
    The imported container's bill of entry is entered as a draft for the accountant to post. Read by the AR Manager,
    the buyer and the accountant.
    """

    @classmethod
    def setUpTestData(cls):
        load(password="Trial-2026", stdout=StringIO(), stderr=StringIO())

    def read(self, path, username="rohit"):
        answer = self.as_(username).get(path)
        self.assertEqual(answer.status_code, 200, (path, answer.content[:300]))
        return json.loads(answer.content)

    def test_the_dealers_list_is_kept_and_is_nobodys_default(self):
        (dealers,) = self.read("/api/sales/price-lists/")
        self.assertEqual((dealers["code"], dealers["is_default"]), ("DEALER-26", False))
        self.assertEqual(sorted((entry["item_label"].split(" ")[0], entry["min_quantity"], entry["unit_price"])
                                for entry in dealers["entries"]),
                         [("SACK-CEM50", "1.0000", "7.95"), ("SACK-CEM50", "50000.0000", "7.80"),
                          ("SACK-FERT50", "1.0000", "11.40"), ("SACK-RICE25", "1.0000", "9.10")])

    def test_what_is_overdue_has_its_reminder_recorded_for_post_not_emailed(self):
        self.assertEqual([level["days_overdue"] for level in self.read("/api/sales/dunning-levels/")], [7, 30, 60])
        notices = self.read("/api/sales/dunning-notices/")
        self.assertTrue(notices)
        for notice in notices:
            self.assertEqual((notice["sent_to"], notice["sent_at"]), ("", None), notice)
            self.assertGreaterEqual(notice["days_overdue"], 7, notice)
            self.assertGreater(Decimal(notice["amount_due"]), 0, notice)

    def test_the_monthly_charge_waits_for_next_month(self):
        from django.utils import timezone

        today = timezone.localdate()
        (cylinders,) = self.read("/api/sales/recurring-invoices/")
        self.assertEqual((cylinders["code"], cylinders["next_run_date"], cylinders["auto_post"]),
                         ("RI-CYL", str((today.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)), False))
        self.assertEqual([(line["total"], line["charged_taxes"]) for line in cylinders["lines"]],
                         [("2950.00", ["CGST9", "SGST9"])])

    def test_the_years_granule_is_on_a_blanket_agreement_and_its_first_lorry_called_off(self):
        (agreement,) = self.read("/api/purchasing/blanket-orders/", "ravi")
        self.assertEqual((agreement["vendor_name"], agreement["status"]), ("Granule House Polymers", "confirmed"))
        # 120,000 kg at 109: 13,080,000; from Gujarat, so integrated tax at 18%.
        self.assertEqual([(line["quantity"], line["quantity_released"], line["quantity_remaining"], line["net_amount"],
                           line["charged_taxes"]) for line in agreement["lines"]],
                         [("120000.0000", "18000.0000", "102000.0000", "13080000.00", ["IGST18"])])
        called = self.read("/api/purchasing/purchase-orders/?status=draft", "ravi")
        self.assertEqual([(order["vendor_name"], [line["quantity"] for line in order["lines"]]) for order in called],
                         [("Granule House Polymers", ["18000.0000"])])
        (rule,) = self.read("/api/purchasing/reorder-rules/", "ravi")
        self.assertEqual((rule["item_label"].split(" ")[0], rule["minimum"], rule["target"], rule["vendor_name"]),
                         ("UV-MB", "150.0000", "400.0000", "Western Masterbatch Pvt Ltd"))

    def test_the_latest_invoice_has_its_e_invoice_and_e_way_bill_built_and_waiting_for_the_portals(self):
        from apps.sales.models import Invoice

        latest = Invoice.objects.filter(posted=True, credits__isnull=True, currency__is_base=True).order_by(
            "-invoice_date", "-pk").first()
        (built,) = self.read("/api/gst/e-invoices/", "anita")
        # Built and kept, with no IRN: registering it is the portal's answer, and nothing it could not be refused.
        self.assertEqual((built["invoice_number"], built["irn"], built["warnings"]), (latest.number, "", []))
        self.assertEqual(Decimal(str(built["payload"]["ValDtls"]["TotInvVal"])), latest.total())
        (bill,) = self.read("/api/gst/eway-bills/", "anita")
        self.assertEqual((bill["document_number"], bill["number"], bill["required"], bill["vehicle_number"]),
                         (latest.number, "", True, "MH12RT5508"))

    def test_an_atta_sack_is_costed_for_a_quote_and_next_years_standards_drafted(self):
        (sheet,) = self.read("/api/manufacturing/cost-sheets/")
        # Material 8.560159 (the rate sheet over the walked recipe), conversion 2.798376 (four stages a kilogramme
        # and packing a sack), less 0.258835 of regrind back: 11.099700; 8% overhead 0.887976; cost 11.987676;
        # 12% margin, 13.426197, quoted at 13.43.
        self.assertEqual([sheet[key] for key in ("specification_code", "quantity", "material", "conversion", "credit",
                                                 "overhead", "cost", "price", "quoted_price")],
                         ["B-ATTA25", "100000.00", "8.560159", "2.798376", "0.258835", "0.887976", "11.987676",
                          "13.426197", "13.43"])
        (version,) = self.read("/api/manufacturing/cost-versions/", "anita")
        self.assertEqual((version["is_published"], version["effective_from"][5:]), (False, "04-01"))
        self.assertGreater(version["items"], 0)
        self.assertEqual(len(self.read("/api/manufacturing/standard-costs/", "anita")), version["items"])

    def test_the_imported_containers_bill_of_entry_is_entered_for_the_accountant_to_post(self):
        (entry,) = self.read("/api/purchasing/bills-of-entry/", "anita")
        # USD 50,000 FOB, 1,150 freight and 52 insurance at 84.10: 4,306,088.20. Duty to the rupee, line by line
        # (calc_g53/o613_boe.py): BCD 322,956, social welfare surcharge 32,296, IGST 839,041.
        self.assertEqual([entry[key] for key in ("status", "fob_total", "assessable_total", "bcd_total", "sws_total",
                                                 "duty_total", "igst_total", "duty_bill")],
                         ["draft", "50000.00", "4306088.20", "322956.00", "32296.00", "355252.00", "839041.00", None])
