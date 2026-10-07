"""
Teams and targets. Rep A bills Acme 1,000 and 500 in September and
credits 200 back; rep B bills Beta 700. Worked by hand:

    rep A, September      1,000 + 500 - 200 = 1,300   target 2,000 → 65.0%, short 700
    team West (A and B)   1,300 + 700       = 2,000   target 1,500 → 133.3%, short 0

A dollar invoice counts in rupees at the rate it posted at: 123 sacks at
1.00 USD, 7 credited back, at 83.2567 is 116 x 83.2567 = 9,657.7772, so
rep A reads 10,957.78 (547.9%) and West 11,657.78 (777.2%); a rate that
starts later changes nothing. Counted in its own dollars it was 1,416.

A target is a team's or a rep's, never both or neither; it ends after
it starts; a second for the same start is refused. The AR Manager keeps
them, a rep reads them.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.core.models import Currency, ExchangeRate

from .models import Invoice, InvoiceLine, revenue_report
from .teams import SalesTarget, SalesTeam, net_by_rep, targets_report
from .tests_reps import RepTestCase

SEPT = (datetime.date(2026, 9, 1), datetime.date(2026, 9, 30))


class TeamTestCase(RepTestCase):
    def setUp(self):
        super().setUp()
        self.a, self.b = self.rep_a.employee.party.sales_rep_profile, self.rep_b.employee.party.sales_rep_profile
        self.west = SalesTeam.objects.create(code="WEST", name="West", leader=self.a)
        self.a.team = self.west
        self.a.save()
        self.b.team = self.west
        self.b.save()

    def sell(self, customer, price, day, credit=None, currency=None):
        """An invoice to `customer` of `price` units at 1.00, posted on `day`; `credit` units credited back the same day."""
        invoice = Invoice.objects.create(customer=customer, invoice_date=day, receivable_account=self.receivable,
                                         currency=currency)
        line = InvoiceLine.objects.create(invoice=invoice, item=self.item, description="Sacks", quantity=Decimal(price),
                                          unit_price=Decimal("1"), revenue_account=self.revenue)
        invoice.post()
        if credit:
            note = invoice.create_credit_note(quantities={line: Decimal(credit)})
            Invoice.objects.filter(pk=note.pk).update(invoice_date=day)
        return invoice


class ReportTests(TeamTestCase):
    def setUp(self):
        super().setUp()
        self.sell(self.acme, "1000", datetime.date(2026, 9, 5), credit="200")
        self.sell(self.acme, "500", datetime.date(2026, 9, 20))
        self.sell(self.beta, "700", datetime.date(2026, 9, 12))
        self.sell(self.acme, "9000", datetime.date(2026, 10, 2))  # October: not September's
        SalesTarget.objects.create(rep=self.a, period_start=SEPT[0], period_end=SEPT[1], amount=Decimal("2000"))
        SalesTarget.objects.create(team=self.west, period_start=SEPT[0], period_end=SEPT[1], amount=Decimal("1500"))
        SalesTarget.objects.create(rep=self.b, period_start=datetime.date(2026, 11, 1),
                                   period_end=datetime.date(2026, 11, 30), amount=Decimal("100"))

    def test_net_by_rep_is_invoices_less_credit_notes_in_the_span(self):
        self.assertEqual(net_by_rep(*SEPT), {self.a.party_id: Decimal("1300.00"), self.b.party_id: Decimal("700.00")})
        by_rep = {row["key"]: row["net"] for row in revenue_report(*SEPT, group_by="rep")}
        self.assertEqual(by_rep, {str(self.a.party): Decimal("1300.00"), str(self.b.party): Decimal("700.00")})

    def test_each_target_against_its_own_span(self):
        rows = targets_report(*SEPT)
        self.assertEqual([(row["who"], row["kind"], row["target"], row["actual"], row["percent"], row["shortfall"])
                          for row in rows],
                         [("West", "team", Decimal("1500.00"), Decimal("2000.00"), Decimal("133.3"), Decimal("0")),
                          (str(self.a.party), "rep", Decimal("2000.00"), Decimal("1300.00"), Decimal("65.0"), Decimal("700.00"))])
        # November's target is outside September's window; asked for, it has nothing posted yet.
        november = targets_report("2026-11-01", "2026-11-30")
        self.assertEqual([(row["who"], row["actual"], row["percent"]) for row in november],
                         [(str(self.b.party), Decimal("0.00"), Decimal("0.0"))])

    def test_a_dollar_invoice_counts_in_rupees_at_the_rate_it_posted_at(self):
        usd = Currency.objects.create(code="USD", name="US Dollar")
        ExchangeRate.objects.create(currency=usd, rate=Decimal("83.2567"), valid_from=datetime.date(2026, 9, 1))
        export = self.sell(self.acme, "123", datetime.date(2026, 9, 10), credit="7", currency=usd)
        self.assertEqual(export.exchange_rate, Decimal("83.2567"))
        # A rate from later on is not the one either document posted at.
        ExchangeRate.objects.create(currency=usd, rate=Decimal("90"), valid_from=datetime.date(2026, 9, 11))
        rows = {row["who"]: (row["actual"], row["percent"], row["shortfall"]) for row in targets_report(*SEPT)}
        self.assertEqual(rows, {"West": (Decimal("11657.78"), Decimal("777.2"), Decimal("0")),
                                str(self.a.party): (Decimal("10957.78"), Decimal("547.9"), Decimal("0"))})


class GuardTests(TeamTestCase):
    def test_a_target_is_a_teams_or_a_reps_and_ends_after_it_starts(self):
        with self.assertRaisesMessage(ValidationError, "not both and not neither"):
            SalesTarget.objects.create(team=self.west, rep=self.a, period_start=SEPT[0], period_end=SEPT[1], amount=1)
        with self.assertRaisesMessage(ValidationError, "not both and not neither"):
            SalesTarget.objects.create(period_start=SEPT[0], period_end=SEPT[1], amount=1)
        with self.assertRaisesMessage(ValidationError, "ends before it starts"):
            SalesTarget.objects.create(rep=self.a, period_start=SEPT[1], period_end=SEPT[0], amount=1)
        with self.assertRaisesMessage(ValidationError, "nothing or more"):
            SalesTarget.objects.create(rep=self.a, period_start=SEPT[0], period_end=SEPT[1], amount=Decimal("-5"))
        SalesTarget.objects.create(rep=self.a, period_start=SEPT[0], period_end=SEPT[1], amount=Decimal("10"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesTarget.objects.create(rep=self.a, period_start=SEPT[0], period_end=SEPT[1], amount=Decimal("20"))

    def test_a_zero_target_has_no_percent(self):
        SalesTarget.objects.create(team=self.west, period_start=SEPT[0], period_end=SEPT[1], amount=Decimal("0"))
        self.assertEqual([(row["percent"], row["shortfall"]) for row in targets_report(*SEPT)], [(None, Decimal("0.00"))])


class OfficeTests(TeamTestCase):
    def test_the_manager_keeps_them_and_a_rep_reads_them(self):
        manager = self.as_role("AR Manager")
        made = manager.post("/api/sales/sales-teams/", {"code": "EAST", "name": "East"}, format="json")
        self.assertEqual((made.status_code, made.json()["member_count"]), (201, 0), made.content)
        target = manager.post("/api/sales/sales-targets/", {"team": self.west.pk, "period_start": "2026-09-01",
                                                            "period_end": "2026-09-30", "amount": "1500"}, format="json")
        self.assertEqual((target.status_code, target.json()["who"]), (201, "West"), target.content)
        both = manager.post("/api/sales/sales-targets/", {"team": self.west.pk, "rep": self.a.pk, "period_start": "2026-10-01",
                                                          "period_end": "2026-10-31", "amount": "1"}, format="json")
        self.assertEqual((both.status_code, "not both" in both.json()["team"][0]), (400, True), both.content)
        teams = manager.get("/api/sales/sales-teams/").json()
        self.assertEqual({row["code"]: (row["member_count"], row["leader_name"]) for row in teams},
                         {"EAST": (0, ""), "WEST": (2, self.a.party.name)})
        self.assertEqual({row["name"] for row in manager.get("/api/sales/sales-reps/", {"team": self.west.pk}).json()},
                         {self.a.party.name, self.b.party.name})

        # A team with reps in it stays; an empty one goes.
        kept = manager.delete(f"/api/sales/sales-teams/{self.west.pk}/")
        self.assertEqual((kept.status_code, SalesTeam.objects.filter(pk=self.west.pk).exists()), (400, True), kept.content)
        self.assertEqual(manager.delete(f"/api/sales/sales-teams/{made.json()['id']}/").status_code, 204)

        rep = self.as_user(self.rep_a)
        report = rep.get("/api/sales/sales-targets/report/", {"from": "2026-09-01", "to": "2026-09-30"})
        self.assertEqual((report.status_code, [row["who"] for row in report.json()]), (200, ["West"]), report.content)
        self.assertEqual(rep.post("/api/sales/sales-targets/", {"rep": self.a.pk, "period_start": "2026-09-01",
                                                                "period_end": "2026-09-30", "amount": "1"},
                                  format="json").status_code, 403)
