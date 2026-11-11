"""
The demo plant: loaded into an empty database through the application's
own posting paths, its books agree with themselves, its logins work and
see what their roles allow, and a second load is refused, so it can
never mix invented records into real books.
"""

import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.web.health import integrity


def load(**options):
    """
    load_demo, with its migration guard told the schema is complete: a test
    database built without running the migrations (TEST MIGRATE False) reads
    to it as one still being built. The guard has its own test below.
    """
    with mock.patch("apps.web.management.commands.load_demo.MigrationExecutor") as executor:
        executor.return_value.migration_plan.return_value = []
        call_command("load_demo", **options)


class LoadDemoTests(TestCase):
    def test_a_whole_plant_loads_and_its_books_agree(self):
        out = StringIO()
        load(password="Trial-2026", stdout=out, stderr=StringIO())
        self.assertIn("The demo plant is loaded.", out.getvalue())
        self.assertEqual([finding["label"] for finding in integrity(fresh=True)["findings"] if finding["ok"] is False], [])
        self.assert_the_agings_foot_with_a_foreign_document_open()

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
        tax = Decimal("3900") if run.period_start.month == 2 else Decimal("2600")
        booked = {row["account__code"]: row["debit"] - row["credit"] for row in run.journal_entry.lines.values(
            "account__code").annotate(debit=Sum("debit"), credit=Sum("credit"))}
        self.assertEqual(booked, {"6000": Decimal("575500"), "6100": Decimal("38527"), "2410": Decimal("-75720"),
                                  "2420": Decimal("-821"), "2430": -tax, "2400": tax - Decimal("537486")})

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
