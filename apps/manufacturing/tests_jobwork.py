"""
Goods out to a job worker on a challan.

The outside fixture's run is 1,000 kg of tape going out to be coated.
Two challans send it: 600 kg on the first, 400 kg on the second.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.accounting.models import PartyTaxProfile
from apps.core.models import Party, UnitOfMeasure, UnitOfMeasureCategory

from .jobwork import (
    JobWorkChallan,
    JobWorkLine,
    JobWorkLoss,
    allocation,
    check_back_was_sent,
    still_out,
)
from .orders import WorkOrder
from .tests_orders import TODAY
from .tests_outside import OutsideTestCase

DAY = datetime.timedelta(days=1)


class JobWorkTestCase(OutsideTestCase):
    def setUp(self):
        super().setUp()
        self.laminator = Party.objects.create(code="LAM", name="Laminator")
        PartyTaxProfile.objects.create(party=self.laminator, gstin="29AABCE5678F1ZD")
        self.lamination = self.released()
        self.coat = self.lamination.operations.get(is_outside=True)

    def challan(self, quantity, day=TODAY, post=True, capital=False, operation=None):
        challan = JobWorkChallan.objects.create(job_worker=self.laminator, challan_date=day)
        JobWorkLine.objects.create(
            challan=challan, operation=operation or self.coat,
            description="Woven fabric for coating", hsn_code="63053300",
            quantity=Decimal(quantity), value=Decimal(quantity) * 95,
            tax_rate=Decimal("5"), is_capital_goods=capital,
        )
        if post:
            challan.post()
        return challan

    def state(self, challan):
        return allocation(self.coat)[challan.lines.get().pk]


class IssuingAChallanTests(JobWorkTestCase):
    def test_it_is_numbered_and_freezes_the_job_workers_registration(self):
        challan = self.challan("600")
        self.assertTrue(challan.number.startswith("JWC-"))
        self.assertEqual((challan.job_worker_gstin, challan.job_worker_state),
                         ("29AABCE5678F1ZD", "29"))

    def test_an_issued_challan_cannot_be_edited(self):
        challan = self.challan("600")
        challan.vehicle = "KA-01-1234"
        with self.assertRaisesMessage(ValidationError, "is issued"):
            challan.save()
        with self.assertRaisesMessage(ValidationError, "lines are fixed"):
            line = challan.lines.get()
            line.quantity = Decimal("1")
            line.save()

    def test_it_cannot_be_issued_twice(self):
        challan = self.challan("600")
        with self.assertRaisesMessage(ValidationError, "already issued"):
            challan.post()

    def test_an_empty_challan_sends_nothing(self):
        challan = JobWorkChallan.objects.create(job_worker=self.laminator, challan_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "nothing on it"):
            challan.post()

    def test_nothing_goes_out_for_one_of_our_own_steps(self):
        inside = self.lamination.operations.get(is_outside=False)
        with self.assertRaisesMessage(ValidationError, "our own machines"):
            self.challan("100", operation=inside, post=False)

    def test_a_line_needs_a_real_hsn(self):
        challan = JobWorkChallan.objects.create(job_worker=self.laminator, challan_date=TODAY)
        for code in ("", "63053"):
            with self.subTest(code=code), self.assertRaises(ValidationError):
                JobWorkLine.objects.create(
                    challan=challan, operation=self.coat, description="Fabric",
                    hsn_code=code, quantity=Decimal("1"), value=Decimal("1"),
                    tax_rate=Decimal("5"),
                )

    def test_no_more_goes_out_than_the_run_holds(self):
        # 1,000 kg with the run's 10% over-production allowance is 1,100.
        self.assertEqual(self.lamination.maximum_output(), Decimal("1100"))
        self.challan("600")
        self.challan("500")
        with self.assertRaisesMessage(ValidationError, "would be out on challans"):
            self.challan("1")

    def test_a_run_that_is_not_running_sends_nothing(self):
        challan = self.challan("600", post=False)
        self.lamination.cancel()
        with self.assertRaisesMessage(ValidationError, "not running"):
            challan.post()

    def test_a_line_that_cannot_be(self):
        challan = JobWorkChallan.objects.create(job_worker=self.laminator, challan_date=TODAY)
        with self.assertRaises(IntegrityError), transaction.atomic():
            JobWorkLine.objects.create(
                challan=challan, operation=self.coat, description="Fabric",
                hsn_code="6305", quantity=Decimal("0"), value=Decimal("1"),
                tax_rate=Decimal("5"),
            )


class WhatCameBackTests(JobWorkTestCase):
    def setUp(self):
        super().setUp()
        self.first = self.challan("600", day=TODAY - 2 * DAY)
        self.second = self.challan("400", day=TODAY - DAY)

    def test_what_comes_back_fills_the_oldest_challan_first(self):
        self.back(self.lamination, "700", "1400")
        self.assertEqual((self.state(self.first)["back"], self.state(self.first)["outstanding"]),
                         (Decimal("600"), Decimal("0")))
        self.assertEqual((self.state(self.second)["back"], self.state(self.second)["outstanding"]),
                         (Decimal("100"), Decimal("300")))

    def test_work_sent_back_for_rework_reopens_the_latest_it_filled(self):
        self.back(self.lamination, "700", "1400")
        self.back(self.lamination, "50", "100", is_return=True)
        self.assertEqual(self.state(self.second)["outstanding"], Decimal("350"))
        self.assertEqual(self.state(self.first)["outstanding"], Decimal("0"))
        self.assertEqual([quantity for _, quantity in self.state(self.second)["events"]],
                         [Decimal("100"), Decimal("-50")])

    def test_rework_larger_than_the_latest_fill_reaches_back(self):
        self.back(self.lamination, "700", "1400")
        self.back(self.lamination, "150", "300", is_return=True)
        self.assertEqual(self.state(self.second)["outstanding"], Decimal("400"))
        self.assertEqual(self.state(self.first)["outstanding"], Decimal("50"))

    def test_what_was_lost_is_not_filled_by_what_comes_back(self):
        JobWorkLoss.objects.create(line=self.first.lines.get(), loss_date=TODAY,
                                   quantity=Decimal("12"))
        self.back(self.lamination, "700", "1400")
        self.assertEqual(self.state(self.first)["back"], Decimal("588"))
        self.assertEqual(self.state(self.second)["back"], Decimal("112"))
        self.assertEqual(self.state(self.second)["outstanding"], Decimal("288"))

    def test_a_full_challan_takes_nothing_from_later_receipts(self):
        self.back(self.lamination, "700", "1400")
        self.back(self.lamination, "200", "400")
        self.assertEqual([q for _, q in self.state(self.first)["events"]], [Decimal("600")])

    def test_a_voided_receipt_came_back_as_nothing(self):
        movement = self.back(self.lamination, "700", "1400")
        movement.void()
        self.assertEqual(self.state(self.first)["outstanding"], Decimal("600"))

    def test_nothing_comes_back_that_was_not_sent(self):
        self.back(self.lamination, "1000", "2000")
        with self.assertRaisesMessage(ValidationError, "still out on challans"):
            check_back_was_sent(self.coat, Decimal("1"))

    def test_the_receipt_itself_is_refused(self):
        self.second.void()
        with self.assertRaisesMessage(ValidationError, "still out on challans"):
            self.back(self.lamination, "700", "1400")

    def test_a_step_without_challans_is_left_alone(self):
        other = self.released("500")
        self.back(other, "500", "1000")
        self.assertEqual(other.operations.get(is_outside=True).quantity_back(), Decimal("500"))


class LossesAtTheJobWorkerTests(JobWorkTestCase):
    def setUp(self):
        super().setUp()
        self.first = self.challan("600")

    def loss(self, quantity, challan=None):
        return JobWorkLoss.objects.create(
            line=(challan or self.first).lines.get(), loss_date=TODAY,
            quantity=Decimal(quantity), note="Edge trim",
        )

    def test_a_loss_is_no_longer_out(self):
        self.loss("12")
        self.assertEqual(self.state(self.first)["outstanding"], Decimal("588"))

    def test_nor_can_it_come_back(self):
        self.loss("12")
        with self.assertRaisesMessage(ValidationError, "still out on challans"):
            self.back(self.lamination, "590", "1180")

    def test_no_more_lost_than_is_out(self):
        self.back(self.lamination, "590", "1180")
        with self.assertRaisesMessage(ValidationError, "Only 10"):
            self.loss("11")

    def test_a_recorded_loss_stays_recorded(self):
        loss = self.loss("12")
        loss.quantity = Decimal("1")
        with self.assertRaisesMessage(ValidationError, "is a fact"):
            loss.save()

    def test_a_loss_recorded_in_error_is_voided_not_deleted(self):
        loss = self.loss("12")
        with self.assertRaisesMessage(ValidationError, "void it rather than delete it"):
            loss.delete()
        loss.void()
        self.assertEqual(self.state(self.first)["outstanding"], Decimal("600"))
        self.back(self.lamination, "600", "1200")  # all of it can come back now
        with self.assertRaisesMessage(ValidationError, "already withdrawn"):
            loss.void()

    def test_a_voided_loss_is_still_not_edited(self):
        loss = self.loss("12")
        loss.void()
        loss.quantity = Decimal("1")
        with self.assertRaisesMessage(ValidationError, "is a fact"):
            loss.save()

    def test_a_voided_loss_does_not_hold_the_challan(self):
        self.loss("12").void()
        self.first.void()
        self.assertIsNotNone(self.first.voided_at)

    def test_not_before_the_goods_went_out(self):
        with self.assertRaisesMessage(ValidationError, "nothing on it was lost"):
            JobWorkLoss.objects.create(line=self.first.lines.get(), quantity=Decimal("1"),
                                       loss_date=TODAY - datetime.timedelta(days=1))

    def test_not_against_a_withdrawn_challan(self):
        second = self.challan("100")
        second.void()
        with self.assertRaisesMessage(ValidationError, "not an issued challan"):
            self.loss("1", challan=second)


class AChallansOwnLinesCountTogetherTests(JobWorkTestCase):
    def line(self, challan, quantity):
        return JobWorkLine.objects.create(
            challan=challan, operation=self.coat, description="Woven fabric for coating",
            hsn_code="63053300", quantity=Decimal(quantity),
            value=Decimal(quantity) * 95, tax_rate=Decimal("5"),
        )

    def test_two_lines_past_what_the_run_holds_are_refused(self):
        # The run holds 1,100 with its allowance; each 600 found only the
        # other challans, of which there were none.
        challan = JobWorkChallan.objects.create(job_worker=self.laminator, challan_date=TODAY)
        self.line(challan, "600")
        self.line(challan, "600")
        with self.assertRaisesMessage(ValidationError, "and 1200.0000 of it would be out"):
            challan.post()

    def test_two_lines_within_it_go(self):
        challan = JobWorkChallan.objects.create(job_worker=self.laminator, challan_date=TODAY)
        self.line(challan, "500")
        self.line(challan, "500")
        challan.post()
        self.assertTrue(challan.posted)


class ARunInTonnesTests(JobWorkTestCase):
    """
    Found in review: a challan's lines are in the run's unit and the
    ceiling in the item's stocking unit, and the two were compared as
    they stood. Two challans of 0.6 t on a run of 1 t of tape kept in
    kilogrammes read as 1.2 against 1,100 and went out; the vendor's
    1.2 t back would then have been refused against the same 1,100 kg.
    """

    def setUp(self):
        super().setUp()
        tonne = UnitOfMeasure.objects.create(
            code="t", name="Tonne", category=UnitOfMeasureCategory.WEIGHT,
            base_unit=self.kg, conversion_factor=Decimal("1000"),
        )
        self.in_tonnes = WorkOrder.objects.create(
            item=self.tape, bom=self.bom, quantity_ordered=Decimal("1"), uom=tonne,
            warehouse=self.plant, work_centre=self.loom,
        )
        self.in_tonnes.release(TODAY)
        self.coat_in_tonnes = self.in_tonnes.operations.get(is_outside=True)

    def test_past_the_run_is_refused(self):
        self.assertEqual(self.in_tonnes.maximum_output(), Decimal("1100"))
        self.challan("0.6", operation=self.coat_in_tonnes)
        with self.assertRaisesMessage(ValidationError, "and 1200"):
            self.challan("0.6", operation=self.coat_in_tonnes)

    def test_up_to_it_they_go(self):
        self.challan("0.6", operation=self.coat_in_tonnes)
        self.assertTrue(self.challan("0.5", operation=self.coat_in_tonnes).posted)


class WithdrawingAChallanTests(JobWorkTestCase):
    def test_one_nothing_came_back_against(self):
        challan = self.challan("600")
        challan.void()
        self.assertIsNotNone(challan.voided_at)
        self.assertEqual(allocation(self.coat), {})

    def test_not_once_work_came_back_against_it(self):
        challan = self.challan("600")
        self.back(self.lamination, "100", "200")
        with self.assertRaisesMessage(ValidationError, "has come back"):
            challan.void()

    def test_unless_another_challan_covers_it(self):
        first = self.challan("600")
        self.challan("400")
        self.back(self.lamination, "300", "600")
        first.void()
        self.assertIsNotNone(first.voided_at)

    def test_not_when_the_others_lost_what_would_cover_it(self):
        # 600 and 400 out, 100 of the 600 lost, 550 back: the 600 accounts
        # for 500 at most, so the 400 is needed.
        first = self.challan("600", day=TODAY - datetime.timedelta(days=2))
        second = self.challan("400", day=TODAY - datetime.timedelta(days=1))
        JobWorkLoss.objects.create(line=first.lines.get(), loss_date=TODAY,
                                   quantity=Decimal("100"))
        self.back(self.lamination, "550", "1100")
        with self.assertRaisesMessage(ValidationError, "has come back"):
            second.void()
        self.assertIsNone(JobWorkChallan.objects.get(pk=second.pk).voided_at)

    def test_but_when_they_still_cover_it(self):
        first = self.challan("600", day=TODAY - datetime.timedelta(days=2))
        second = self.challan("400", day=TODAY - datetime.timedelta(days=1))
        JobWorkLoss.objects.create(line=first.lines.get(), loss_date=TODAY,
                                   quantity=Decimal("100"))
        self.back(self.lamination, "500", "1000")
        second.void()
        self.assertIsNotNone(second.voided_at)

    def test_not_with_losses_recorded(self):
        challan = self.challan("600")
        JobWorkLoss.objects.create(line=challan.lines.get(), loss_date=TODAY,
                                   quantity=Decimal("1"))
        with self.assertRaisesMessage(ValidationError, "Losses are recorded"):
            challan.void()

    def test_an_issued_challan_is_never_deleted(self):
        challan = self.challan("600")
        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            challan.delete()
        with self.assertRaisesMessage(ValidationError, "its lines are fixed"):
            challan.lines.get().delete()
        challan.void()
        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            challan.delete()
        draft = self.challan("100", post=False)
        draft.delete()
        self.assertFalse(JobWorkChallan.objects.filter(pk=draft.pk).exists())

    def test_a_line_is_not_moved_off_an_issued_challan(self):
        # O82: the line asked only the challan it joined, so an issued
        # challan's line could be moved onto a draft and ITC-04 lose it.
        issued = self.challan("600")
        draft = self.challan("100", post=False)
        line = issued.lines.get()
        line.challan = draft
        with self.assertRaisesMessage(ValidationError, "its lines are fixed"):
            line.save()
        self.assertEqual(JobWorkChallan.objects.get(pk=issued.pk).lines.count(), 1)

    def test_not_twice(self):
        challan = self.challan("600")
        challan.void()
        with self.assertRaisesMessage(ValidationError, "not an issued challan"):
            challan.void()


class DueBackTests(JobWorkTestCase):
    def test_inputs_have_a_year_and_capital_goods_three(self):
        inputs = self.challan("300", day=datetime.date(2025, 6, 1))
        capital = self.challan("300", day=datetime.date(2025, 6, 1), capital=True)
        rows = {row["challan"].pk: row for row in still_out(as_of=datetime.date(2026, 6, 2))}
        self.assertEqual(rows[inputs.pk]["due_back_by"], datetime.date(2026, 6, 1))
        self.assertTrue(rows[inputs.pk]["overdue"])
        self.assertEqual(rows[capital.pk]["due_back_by"], datetime.date(2028, 5, 31))
        self.assertFalse(rows[capital.pk]["overdue"])
        self.assertFalse(rows[capital.pk]["due_soon"])

    def test_as_of_a_day_only_what_had_happened_by_then(self):
        self.challan("300", day=datetime.date(2026, 5, 1))
        self.back(self.lamination, "300", "600")  # dated TODAY, 1 June
        later = self.challan("50", day=datetime.date(2026, 6, 10))
        (row,) = still_out(as_of=datetime.date(2026, 5, 31))
        self.assertEqual(row["outstanding"], Decimal("300"))
        (row,) = still_out(as_of=datetime.date(2026, 6, 10))
        self.assertEqual(row["challan"], later)

    def test_the_last_day_is_still_in_time(self):
        challan = self.challan("300", day=datetime.date(2025, 6, 1))
        (row,) = still_out(as_of=datetime.date(2026, 6, 1))
        self.assertEqual((row["challan"], row["overdue"]), (challan, False))

    def test_due_soon_and_what_is_back_is_not_listed(self):
        soon = self.challan("300", day=datetime.date(2025, 6, 20))
        done = self.challan("300", day=datetime.date(2025, 6, 10))
        self.back(self.lamination, "300", "600")  # fills the older one, `done`
        rows = still_out(as_of=datetime.date(2026, 6, 1), within_days=30)
        self.assertEqual([row["challan"].pk for row in rows], [soon.pk])
        self.assertTrue(rows[0]["due_soon"])
        self.assertFalse(rows[0]["overdue"])
        self.assertNotIn(done.pk, [row["challan"].pk for row in rows])


class JobWorkApiTests(JobWorkTestCase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_superuser("planner"))
        self.base = "/api/manufacturing/"

    def draft(self):
        challan = self.client.post(self.base + "job-work-challans/", {
            "job_worker": self.laminator.pk, "challan_date": "2026-06-01",
        }, format="json").json()
        line = self.client.post(self.base + "job-work-lines/", {
            "challan": challan["id"], "operation": self.coat.pk,
            "description": "Woven fabric for coating", "hsn_code": "63053300",
            "quantity": "600", "value": "57000", "tax_rate": "5",
        }, format="json")
        self.assertEqual(line.status_code, 201, line.content)
        return challan

    def test_draft_issue_and_the_line_fixed_after(self):
        challan = self.draft()
        issued = self.client.post(f"{self.base}job-work-challans/{challan['id']}/post/")
        self.assertEqual(issued.status_code, 200, issued.content)
        self.assertEqual(issued.json()["job_worker_gstin"], "29AABCE5678F1ZD")
        line_id = issued.json()["lines"][0]["id"]
        response = self.client.patch(f"{self.base}job-work-lines/{line_id}/",
                                     {"quantity": "1"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self.client.delete(f"{self.base}job-work-challans/{challan['id']}/").status_code, 400)

    def test_what_is_still_out(self):
        challan = self.draft()
        self.client.post(f"{self.base}job-work-challans/{challan['id']}/post/")
        rows = self.client.get(self.base + "job-work-challans/still-out/?as_of=2026-06-02").json()
        self.assertEqual(rows[0]["outstanding"], "600.0000")
        self.assertEqual(rows[0]["due_back_by"], "2027-06-01")
        self.assertEqual(self.client.get(
            self.base + "job-work-challans/still-out/?as_of=June").status_code, 400)

    def test_a_loss_is_recorded_and_not_edited(self):
        challan = self.draft()
        issued = self.client.post(f"{self.base}job-work-challans/{challan['id']}/post/").json()
        loss = self.client.post(self.base + "job-work-losses/", {
            "line": issued["lines"][0]["id"], "loss_date": "2026-06-05", "quantity": "4",
        }, format="json")
        self.assertEqual(loss.status_code, 201, loss.content)
        self.assertEqual(self.client.patch(f"{self.base}job-work-losses/{loss.json()['id']}/",
                                           {"quantity": "1"}, format="json").status_code, 405)
        too_much = self.client.post(self.base + "job-work-losses/", {
            "line": issued["lines"][0]["id"], "loss_date": "2026-06-05", "quantity": "700",
        }, format="json")
        self.assertEqual(too_much.status_code, 400)


class ChallanPdfTests(JobWorkTestCase):
    """The rule 55 delivery challan that goes with the goods, printed from the record."""

    def test_an_issued_and_a_voided_challan_both_print(self):
        challan = self.challan("10")
        self.assertTrue(challan.render_pdf().startswith(b"%PDF-"))
        from rest_framework.test import APIClient
        from django.contrib.auth.models import User

        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("printer"))
        response = client.get(f"/api/manufacturing/job-work-challans/{challan.pk}/pdf/")
        self.assertEqual((response.status_code, response["Content-Type"]), (200, "application/pdf"))
        self.assertIn(challan.number, response["Content-Disposition"])

    def test_an_issued_challan_goes_to_the_job_worker_by_mail(self):
        from django.contrib.auth.models import User
        from django.core import mail
        from django.test import override_settings
        from rest_framework.test import APIClient

        challan = self.challan("10")
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("sender"))
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            self.assertEqual(client.post(f"/api/manufacturing/job-work-challans/{challan.pk}/send/").status_code, 400)
            sent = client.post(f"/api/manufacturing/job-work-challans/{challan.pk}/send/", {"to": "lam@example.com"},
                               format="json")
        self.assertEqual((sent.status_code, sent.json()), (200, {"sent_to": "lam@example.com"}))
        self.assertEqual(mail.outbox[0].attachments[0][0], f"{challan.number}.pdf")
        rows = client.get("/api/core/history/", {"model": "manufacturing.jobworkchallan", "id": challan.pk}).json()
        self.assertEqual((rows[0]["label"], rows[0]["summary"]), ("Sent", "Job-work challan to lam@example.com"))

    def test_sending_a_challan_takes_the_right_to_change_it_not_to_add_one(self):
        from django.contrib.auth.models import Permission, User
        from django.test import override_settings
        from rest_framework.test import APIClient

        challan = self.challan("10")
        for codename, answered in (("add_jobworkchallan", 403), ("change_jobworkchallan", 200)):
            with self.subTest(holds=codename):
                person = User.objects.create_user(codename)
                person.user_permissions.add(*Permission.objects.filter(
                    content_type__app_label="manufacturing", codename__in=("view_jobworkchallan", codename)))
                client = APIClient()
                client.force_authenticate(person)
                with override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
                    sent = client.post(f"/api/manufacturing/job-work-challans/{challan.pk}/send/",
                                       {"to": "lam@example.com"}, format="json")
                self.assertEqual(sent.status_code, answered, sent.content)
