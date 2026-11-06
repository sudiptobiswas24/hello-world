"""
Closing April 2026 from the office: the controller closes it with a
note, nothing posts into it, its dates cannot move and it cannot be
deleted while closed; reopened, it can. The bookkeeper sees the periods
and closes none.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from .models import AccountingPeriod, JournalEntry, JournalLine
from .tests import AccountingTestCase

PERIODS = "/api/accounting/periods/"


class PeriodsApiCase(AccountingTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user, _ = User.objects.get_or_create(username=role.replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def entry(self, day):
        entry = JournalEntry.objects.create(date=day, memo="late")
        JournalLine.objects.create(entry=entry, account=self.cash, debit=Decimal("10"))
        JournalLine.objects.create(entry=entry, account=self.revenue, credit=Decimal("10"))
        return entry


class PeriodsApiTests(PeriodsApiCase):
    def test_closed_from_the_office_and_reopened_there(self):
        controller, books = self.as_("Controller"), self.as_("Bookkeeper")
        made = controller.post(PERIODS, {"name": "Apr 2026", "start_date": "2026-04-01", "end_date": "2026-04-30"},
                               format="json")
        self.assertEqual(made.status_code, 201, made.content)
        pk = made.json()["id"]
        self.assertEqual(books.get(PERIODS).json()[0]["name"], "Apr 2026")
        self.assertEqual(books.post(f"{PERIODS}{pk}/close/", {}, format="json").status_code, 403)

        closed = controller.post(f"{PERIODS}{pk}/close/", {"note": "Signed off by the auditors"}, format="json")
        self.assertEqual(closed.status_code, 200, closed.content)
        self.assertEqual((closed.json()["closed"], closed.json()["closed_by_name"], closed.json()["note"]),
                         (True, "Controller", "Signed off by the auditors"))
        self.assertEqual(controller.post(f"/api/accounting/journal-entries/{self.entry(datetime.date(2026, 4, 15)).pk}/post_entry/",
                                         {}, format="json").status_code, 400)
        self.assertEqual(controller.post(f"/api/accounting/journal-entries/{self.entry(datetime.date(2026, 5, 1)).pk}/post_entry/",
                                         {}, format="json").status_code, 200)
        moved = controller.patch(f"{PERIODS}{pk}/", {"end_date": "2026-05-31"}, format="json")
        self.assertEqual(moved.status_code, 400, moved.content)
        self.assertEqual(controller.delete(f"{PERIODS}{pk}/").status_code, 400)
        self.assertTrue(AccountingPeriod.objects.filter(pk=pk, closed=True).exists())

        reopened = controller.post(f"{PERIODS}{pk}/reopen/", {"note": "A late invoice to post"}, format="json")
        self.assertEqual((reopened.status_code, reopened.json()["closed"]), (200, False))
        self.assertEqual(controller.delete(f"{PERIODS}{pk}/").status_code, 204)

    def test_who_may(self):
        self.assertEqual(self.as_("Purchasing Clerk").get(PERIODS).status_code, 403)
        self.assertEqual(self.as_("Bookkeeper").post(PERIODS, {"name": "X", "start_date": "2026-04-01",
                                                                "end_date": "2026-04-30"}, format="json").status_code, 403)


class APeriodClosesWithNoDraftDatedInItTests(PeriodsApiCase):
    """
    O361: a draft dated in the period holds its close, named by type, number or id and date, until it is
    posted, moved out of the period or deleted. Reopening a closed period asks nothing of drafts.
    """

    def april(self):
        return self.as_("Controller").post(PERIODS, {"name": "Apr 2026", "start_date": "2026-04-01",
                                                     "end_date": "2026-04-30"}, format="json").json()["id"]

    def test_the_controller_is_refused_with_the_drafts_named_until_each_is_dealt_with(self):
        controller, pk = self.as_("Controller"), self.april()
        posted_later, moved, deleted = (self.entry(datetime.date(2026, 4, day)) for day in (3, 15, 30))
        self.entry(datetime.date(2026, 5, 1))  # dated in May: holds nothing in April
        refused = controller.post(f"{PERIODS}{pk}/close/", {}, format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        said = refused.content.decode()
        self.assertIn("3 draft document(s) are dated in it: "
                      f"journal entry id {posted_later.pk} dated 2026-04-03; "
                      f"journal entry id {moved.pk} dated 2026-04-15; "
                      f"journal entry id {deleted.pk} dated 2026-04-30. "
                      "Post each, move it to a date outside the period, or delete it.", said)
        self.assertFalse(AccountingPeriod.objects.get(pk=pk).closed)

        entries = "/api/accounting/journal-entries/"
        self.assertEqual(controller.post(f"{entries}{posted_later.pk}/post_entry/", {}, format="json").status_code, 200)
        self.assertEqual(controller.patch(f"{entries}{moved.pk}/", {"date": "2026-05-02"}, format="json").status_code,
                         200)
        still = controller.post(f"{PERIODS}{pk}/close/", {}, format="json")
        self.assertEqual(still.status_code, 400, still.content)
        self.assertIn(f"1 draft document(s) are dated in it: journal entry id {deleted.pk} dated 2026-04-30.",
                      still.content.decode())
        self.assertEqual(controller.delete(f"{entries}{deleted.pk}/").status_code, 204)
        closed = controller.post(f"{PERIODS}{pk}/close/", {}, format="json")
        self.assertEqual((closed.status_code, closed.json()["closed"]), (200, True))

        # Reopened, a draft dated in it is the reason it was reopened: nothing holds the reopening.
        self.assertEqual(controller.post(f"{PERIODS}{pk}/reopen/", {}, format="json").status_code, 200)
        self.entry(datetime.date(2026, 4, 20))
        self.assertEqual(controller.post(f"{PERIODS}{pk}/close/", {}, format="json").status_code, 400)

    def test_a_draft_receipt_holds_it_as_the_entry_does(self):
        from apps.core.models import Party

        from .models import Payment, PaymentDirection

        customer = Party.objects.create(code="C1", name="Raghav Polymers")
        ar = self.revenue.__class__.objects.create(code="1100", name="Debtors", account_type="asset")
        receipt = Payment.objects.create(party=customer, direction=PaymentDirection.RECEIPT,
                                         payment_date=datetime.date(2026, 4, 10), amount=Decimal("500"),
                                         bank_account=self.cash, counterpart_account=ar)
        april = AccountingPeriod.objects.get(pk=self.april())
        with self.assertRaisesMessage(Exception, f"receipt {receipt.number or f'id {receipt.pk}'} dated 2026-04-10"):
            april.close()
        receipt.payment_date = datetime.date(2026, 3, 31)
        receipt.save()
        april.close()
        self.assertTrue(AccountingPeriod.objects.get(pk=april.pk).closed)

    def test_every_dated_draft_that_posts_is_asked(self):
        from .models import DATED_DRAFTS

        self.assertEqual(sorted(f"{model._meta.label}.{field}" for model, field, _, _ in DATED_DRAFTS), [
            "accounting.JournalEntry.date", "accounting.Payment.payment_date",
            "hr.ExpenseClaim.claim_date",
            "inventory.StockAdjustment.adjustment_date", "inventory.StockCount.count_date",
            "manufacturing.JobWorkChallan.challan_date", "manufacturing.MaterialIssue.issue_date",
            "manufacturing.OutsideMovement.movement_date", "manufacturing.ProductionEntry.entry_date",
            "manufacturing.Rebatch.rebatched_on", "manufacturing.TimeBooking.booking_date",
            "purchasing.Bill.bill_date", "purchasing.BillOfEntry.be_date", "purchasing.GoodsReceipt.receipt_date",
            "sales.Delivery.delivery_date", "sales.Invoice.invoice_date",
        ])
