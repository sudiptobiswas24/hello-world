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
from apps.accounting.models import FiscalPosition, PartyTaxProfile
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
