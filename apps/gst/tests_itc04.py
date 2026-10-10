"""
ITC-04 from challans: a Maharashtra plant sending tape to a laminator in
Karnataka, so the rate is reported as integrated tax.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.accounting.gst import GstSettings, gstin_check_character
from apps.accounting.models import AccountingPeriod, FiscalPosition, PartyTaxProfile
from apps.manufacturing.jobwork import JobWorkLoss
from apps.manufacturing.tests_jobwork import JobWorkTestCase
from apps.manufacturing.tests_orders import TODAY

from .itc04 import itc04
from .models import UnitQuantityCode

H1 = (datetime.date(2026, 4, 1), datetime.date(2026, 9, 30))


class Itc04TestCase(JobWorkTestCase):
    def setUp(self):
        super().setUp()
        first = "27AABCD1234E1Z"
        GstSettings.objects.create(
            gstin=first + gstin_check_character(first),
            interstate_position=FiscalPosition.objects.create(code="INTER", name="Inter"),
        )
        UnitQuantityCode.objects.create(uom=self.kg, code="KGS")
        self.first = self.challan("600", day=TODAY - datetime.timedelta(days=2))
        self.second = self.challan("400", day=TODAY - datetime.timedelta(days=1))


class SentTests(Itc04TestCase):
    def test_each_challan_line_sent_in_the_period(self):
        sent = itc04(*H1)["sent"]
        self.assertEqual([row["challan"] for row in sent], [self.first.number, self.second.number])
        row = sent[0]
        self.assertEqual((row["gstin"], row["state"]), ("29AABCE5678F1ZD", "29"))
        self.assertEqual((row["uqc"], row["quantity"], row["taxable_value"]),
                         ("KGS", Decimal("600"), Decimal("57000")))
        self.assertEqual(row["goods"], "inputs")
        self.assertEqual(row["rates"], {"central": 0, "state": 0,
                                        "integrated": Decimal("5"), "cess": 0})

    def test_a_job_worker_in_our_own_state_splits_the_rate(self):
        PartyTaxProfile.objects.filter(party=self.laminator).update(
            gstin="27AABCD1234E2Z7", gst_state="27")
        challan = self.challan("50")
        row = next(r for r in itc04(*H1)["sent"] if r["challan"] == challan.number)
        self.assertEqual((row["rates"]["central"], row["rates"]["state"], row["rates"]["integrated"]),
                         (Decimal("2.5"), Decimal("2.5"), 0))

    def test_outside_the_period_and_withdrawn_are_not_sent(self):
        self.second.void()
        self.assertEqual(len(itc04(*H1)["sent"]), 1)
        self.assertEqual(itc04(datetime.date(2026, 10, 1), datetime.date(2027, 3, 31))["sent"], [])


class ReceivedTests(Itc04TestCase):
    def test_what_came_back_against_its_original_challan(self):
        self.back(self.lamination, "700", "1400")
        received = itc04(*H1)["received"]
        self.assertEqual([(r["original_challan"], r["quantity"]) for r in received],
                         [(self.first.number, Decimal("600")), (self.second.number, Decimal("100"))])
        self.assertEqual(received[0]["nature_of_job_work"], "Coat")
        self.assertEqual(received[0]["uqc"], "KGS")

    def test_losses_are_reported_against_their_challan(self):
        JobWorkLoss.objects.create(line=self.first.lines.get(), loss_date=TODAY,
                                   quantity=Decimal("12"))
        (row,) = itc04(*H1)["received"]
        self.assertEqual((row["original_challan"], row["quantity"], row["losses_quantity"]),
                         (self.first.number, 0, Decimal("12")))

    def test_a_voided_loss_is_not_reported(self):
        JobWorkLoss.objects.create(line=self.first.lines.get(), loss_date=TODAY,
                                   quantity=Decimal("12")).void()
        self.assertEqual(itc04(*H1)["received"], [])

    def test_receipts_and_losses_outside_the_period_are_not_in_it(self):
        self.back(self.lamination, "700", "1400")
        JobWorkLoss.objects.create(line=self.second.lines.get(), loss_date=datetime.date(2026, 10, 2),
                                   quantity=Decimal("3"))
        second_half = itc04(datetime.date(2026, 10, 1), datetime.date(2027, 3, 31))
        self.assertEqual([r["losses_quantity"] for r in second_half["received"]], [Decimal("3")])
        first_half = itc04(*H1)
        self.assertEqual([r["quantity"] for r in first_half["received"]],
                         [Decimal("600"), Decimal("100")])

    def test_rework_sent_back_without_a_challan_is_warned_not_hidden(self):
        self.back(self.lamination, "700", "1400")
        self.back(self.lamination, "50", "100", is_return=True)
        result = itc04(*H1)
        self.assertEqual(len(result["received"]), 2)
        self.assertTrue(any("for rework without a challan" in w for w in result["warnings"]))

    def test_a_unit_with_no_gst_code_is_warned(self):
        UnitQuantityCode.objects.all().delete()
        self.assertTrue(any("no GST unit code" in w for w in itc04(*H1)["warnings"]))


class RefusalAndApiTests(Itc04TestCase):
    def test_without_gst_there_is_no_return(self):
        GstSettings.objects.update(is_active=False)
        with self.assertRaisesMessage(ValidationError, "not set up"):
            itc04(*H1)

    def test_a_period_that_runs_backwards(self):
        with self.assertRaisesMessage(ValidationError, "ends before it starts"):
            itc04(H1[1], H1[0])

    def test_over_the_api(self):
        from django.contrib.auth.models import Permission

        user = User.objects.create_user("accountant")
        client = APIClient()
        client.force_authenticate(user)
        url = "/api/gst/itc04/?start=2026-04-01&end=2026-09-30"
        self.assertEqual(client.get(url).status_code, 403)
        user.user_permissions.add(Permission.objects.get(codename="compile_returns"))
        client.force_authenticate(User.objects.get(pk=user.pk))
        response = client.get(url)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.json()["sent"]), 2)
        self.assertEqual(client.get("/api/gst/itc04/?start=April").status_code, 400)


class ClosedMonthTests(Itc04TestCase):
    """
    May is closed: its books and its returns signed off. A challan posts no
    entry, so it asks the period itself, issued or withdrawn, and so does a
    loss at the job worker. The two challans of the fixture went out on 30 and
    31 May, before the close.
    """

    def close_may(self):
        from apps.accounting.models import AccountingPeriod

        may = AccountingPeriod.objects.create(name="May", start_date=datetime.date(2026, 5, 1),
                                              end_date=datetime.date(2026, 5, 31))
        may.close()
        return may

    def test_a_challan_is_not_issued_into_a_closed_month(self):
        before = len(itc04(*H1)["sent"])
        self.close_may()
        draft = self.challan("50", day=datetime.date(2026, 5, 15), post=False)
        with self.assertRaisesMessage(ValidationError, "May is closed"):
            draft.post()
        self.assertEqual((before, len(itc04(*H1)["sent"])), (2, 2))

    def test_reopened_it_is_issued(self):
        self.close_may().reopen()
        self.challan("50", day=datetime.date(2026, 5, 15))
        self.assertEqual(len(itc04(*H1)["sent"]), 3)

    def test_a_closed_months_challan_is_not_withdrawn(self):
        self.close_may()
        with self.assertRaisesMessage(ValidationError, "is not withdrawn"):
            self.second.void()
        self.assertEqual(len(itc04(*H1)["sent"]), 2)

    def test_a_loss_is_not_recorded_nor_withdrawn_in_a_closed_month(self):
        loss = JobWorkLoss.objects.create(line=self.first.lines.get(), loss_date=datetime.date(2026, 5, 31),
                                          quantity=Decimal("12"))
        self.close_may()
        with self.assertRaisesMessage(ValidationError, "a loss is not recorded on 2026-05-31"):
            JobWorkLoss.objects.create(line=self.second.lines.get(), loss_date=datetime.date(2026, 5, 31),
                                       quantity=Decimal("3"))
        with self.assertRaisesMessage(ValidationError, "is not withdrawn"):
            loss.void()
        self.assertEqual([row["losses_quantity"] for row in itc04(*H1)["received"]], [Decimal("12")])

    def test_over_the_api_as_the_stores_manager(self):
        from django.core.management import call_command
        from django.contrib.auth.models import Group

        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("stores")
        user.groups.add(Group.objects.get(name="Stores Manager"))
        client = APIClient()
        client.force_authenticate(user)
        self.close_may()
        draft = self.challan("50", day=datetime.date(2026, 5, 15), post=False)
        response = client.post(f"/api/manufacturing/job-work-challans/{draft.pk}/post/")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("May is closed", str(response.json()))
        response = client.post(f"/api/manufacturing/job-work-challans/{self.second.pk}/void/")
        self.assertEqual(response.status_code, 400, response.content)


class TheAuditAsksTheCloseTests(Itc04TestCase):
    """`audit_invariants` reports a record a GST return reads that posts no entry and asks no period."""

    def findings(self, edit=lambda text: text, where="jobwork.py"):
        from apps.core.management.commands.audit_invariants import Command, app_sources

        sources = app_sources()
        sources["manufacturing"] = {path: edit(text) if path.name == where else text
                                    for path, text in sources["manufacturing"].items()}
        return [detail.split(" ")[0] for _, detail in Command().reported_without_the_period(
            ["gst", "manufacturing", "sales", "purchasing"], sources)]

    def test_a_challan_and_a_loss_that_never_ask_are_reported(self):
        self.assertEqual(self.findings(lambda text: text.replace("_refuse_closed(", "_left_open(")),
                         ["manufacturing.JobWorkChallan", "manufacturing.JobWorkLine", "manufacturing.JobWorkLoss"])

    def test_as_they_stand_they_are_not(self):
        self.assertEqual(self.findings(), [])

    def test_a_receipt_reached_through_a_function_and_a_relation_is_reported(self):
        # O158: ITC-04 reads OutsideMovement through jobwork.allocation() and
        # operation.outside_receipts(); it has a journal entry, but at no value posts none.
        self.assertEqual(self.findings(lambda text: text.replace("_refuse_closed(", "_left_open("), "outside.py"),
                         ["manufacturing.OutsideMovement"])


class AReceiptOfNoValueAsksThePeriodTests(Itc04TestCase):
    """
    O158 (review_stat #1). Challans of 30 and 31 May stand; June is closed. 100 kg
    booked back from the laminator at value 0 posts no entry, so JournalEntry.post
    never asked June: it was accepted and ITC-04's "received" went from 0 rows to 1;
    voided after June closed it went from 1 to 0. Both are refused now.
    """

    def close_june(self):
        AccountingPeriod.objects.create(name="Jun", start_date=datetime.date(2026, 6, 1),
                                        end_date=datetime.date(2026, 6, 30)).close()

    def test_booked_at_nothing_into_a_closed_month(self):
        self.close_june()
        with self.assertRaisesMessage(ValidationError, "vendor work is not booked on 2026-06-01"):
            self.back(self.lamination, "100", "0")
        self.assertEqual(len(itc04(*H1)["received"]), 0)

    def test_not_voided_before_it_was_booked_nor_ahead(self):
        # O165: OutsideMovement.void took any day. Booked back on 1 June.
        receipt = self.back(self.lamination, "100", "1400")
        with self.assertRaisesMessage(ValidationError, "is not voided on 2026-05-31: it was booked on 2026-06-01"):
            receipt.void(on_date=datetime.date(2026, 5, 31))
        with self.assertRaisesMessage(ValidationError, "is not voided on 2099-01-01: that day has not come"):
            receipt.void(on_date=datetime.date(2099, 1, 1))
        self.assertEqual(len(itc04(*H1)["received"]), 1)

    def test_voided_after_its_month_closed(self):
        receipt = self.back(self.lamination, "100", "0")
        self.assertIsNone(receipt.journal_entry_id)
        self.assertEqual(len(itc04(*H1)["received"]), 1)
        self.close_june()
        with self.assertRaisesMessage(ValidationError, "booked on 2026-06-01, is not voided"):
            receipt.void()
        self.assertEqual(len(itc04(*H1)["received"]), 1)


class ChallansInTableThirteenTests(Itc04TestCase):
    """
    The fixture's two challans went out on 30 and 31 May; the second is withdrawn.
    GSTR-1 table 13 for May: job-work challans, nature 9, JWC-1 to JWC-2, 2 issued,
    1 cancelled, 1 net (calc_stat/o59_table13.py).
    """

    def test_issued_and_withdrawn_challans_are_counted(self):
        from .returns import gstr1, gstr1_json

        self.second.void()
        result = gstr1(datetime.date(2026, 5, 1), datetime.date(2026, 5, 31))
        (row,) = result["documents"]
        self.assertEqual((row["kind"], row["from"], row["to"], row["total"], row["cancelled"]),
                         ("job_work_challans", self.first.number, self.second.number, 2, 1))
        (detail,) = gstr1_json(result)["doc_issue"]["doc_det"]
        self.assertEqual((detail["doc_num"], detail["docs"][0]["net_issue"]), (9, 1))
