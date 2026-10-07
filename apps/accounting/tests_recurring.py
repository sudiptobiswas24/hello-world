"""
The rent accrual, every month from 31 January 2026: Dr Rent 25,000
(office) / Cr Accrued liabilities 25,000. Taken on the 31st, then the
28th of February, then the 31st of March again — the series stays
anchored to its day. Each run is a draft journal entry that knows its
schedule; a schedule that posts what it makes is refused by a closed
month like anything else, and does not move on.
"""

import datetime
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db.models import ProtectedError
from django.test import TestCase
from rest_framework.test import APIClient

from .analytic import CostCentre
from .models import Account, AccountingPeriod, AccountType, JournalEntry
from .recurring import RecurringJournal, RecurringJournalLine, generate_due_journals


class RecurringTestCase(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.rent = Account.objects.create(code="5600", name="Rent", account_type=AccountType.EXPENSE)
        self.accrued = Account.objects.create(code="2100", name="Accrued liabilities", account_type=AccountType.LIABILITY)
        self.office = CostCentre.objects.create(code="OFFICE", name="Office")

    def schedule(self, code="RENT", debit="25000", credit="25000", **extra):
        schedule = RecurringJournal.objects.create(
            code=code, memo="Rent accrual", start_date=datetime.date(2026, 1, 31), **extra)
        RecurringJournalLine.objects.create(schedule=schedule, account=self.rent, debit=Decimal(debit),
                                            cost_centre=self.office, description="Office rent")
        RecurringJournalLine.objects.create(schedule=schedule, account=self.accrued, credit=Decimal(credit))
        return schedule

    def as_(self, role):
        user, _ = User.objects.get_or_create(username=role.replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client


class RunTests(RecurringTestCase):
    def test_each_run_is_a_draft_entry_that_knows_its_schedule_and_the_series_keeps_its_day(self):
        schedule = self.schedule()
        self.assertEqual(schedule.next_run_date, datetime.date(2026, 1, 31))
        first = schedule.generate_one()
        self.assertEqual((first.date, first.reference, first.memo, first.recurring_journal, first.posted),
                         (datetime.date(2026, 1, 31), "RENT", "Rent accrual", schedule, False))
        self.assertEqual([(line.account_id, line.debit, line.credit, line.cost_centre_id, line.description)
                          for line in first.lines.order_by("pk")],
                         [(self.rent.pk, Decimal("25000.00"), Decimal("0.00"), self.office.pk, "Office rent"),
                          (self.accrued.pk, Decimal("0.00"), Decimal("25000.00"), None, "")])
        self.assertEqual(schedule.next_run_date, datetime.date(2026, 2, 28))
        schedule.generate_one()
        self.assertEqual(schedule.next_run_date, datetime.date(2026, 3, 31))
        schedule.generate_one()
        self.assertEqual(schedule.next_run_date, datetime.date(2026, 4, 30))
        self.assertEqual(schedule.entries.count(), 3)

    def test_what_is_due_is_taken_one_period_at_a_time_and_only_once(self):
        schedule = self.schedule()
        self.assertTrue(schedule.is_due(datetime.date(2026, 3, 31)))
        made, refused = generate_due_journals(as_of=datetime.date(2026, 3, 31))
        self.assertEqual(([entry.date for entry in made], refused),
                         ([datetime.date(2026, 1, 31), datetime.date(2026, 2, 28), datetime.date(2026, 3, 31)], []))
        schedule.refresh_from_db()
        self.assertEqual(schedule.next_run_date, datetime.date(2026, 4, 30))
        self.assertEqual(generate_due_journals(as_of=datetime.date(2026, 3, 31)), ([], []))
        self.assertFalse(schedule.is_due(datetime.date(2026, 3, 31)))

    def test_the_command_takes_what_is_due(self):
        self.schedule()
        out = StringIO()
        RecurringJournal.objects.create(code="EMPTY", memo="Nothing on it", start_date=datetime.date(2026, 1, 1))
        call_command("generate_recurring", as_of="2026-02-28", stdout=out)
        self.assertEqual(out.getvalue().strip().splitlines(), [
            "EMPTY not taken: This schedule has no lines to post.",
            "0 invoice(s) issued, 2 journal entries taken, 1 schedule(s) refused."])
        self.assertEqual(JournalEntry.objects.filter(recurring_journal__isnull=False).count(), 2)

    def test_a_schedule_that_posts_is_refused_by_a_closed_month_and_does_not_move_on(self):
        schedule = self.schedule(auto_post=True)
        AccountingPeriod.objects.create(name="Jan 2026", start_date=datetime.date(2026, 1, 1),
                                        end_date=datetime.date(2026, 1, 31)).close()
        with self.assertRaisesMessage(ValidationError, "Jan 2026 is closed"):
            schedule.generate_one()
        schedule.refresh_from_db()
        self.assertEqual((schedule.next_run_date, JournalEntry.objects.count()), (datetime.date(2026, 1, 31), 0))
        # Dated into an open month instead, it posts.
        entry = schedule.generate_one(on_date=datetime.date(2026, 2, 1))
        self.assertTrue(entry.posted)
        self.assertEqual(schedule.next_run_date, datetime.date(2026, 2, 28))

    def test_refusals(self):
        with self.assertRaisesMessage(ValidationError, "do not balance: debits 100.00 and credits 80.00"):
            self.schedule(code="OFF", debit="100", credit="80").generate_one()
        with self.assertRaisesMessage(ValidationError, "no lines"):
            RecurringJournal.objects.create(code="EMPTY", memo="Nothing", start_date=datetime.date(2026, 1, 1)).generate_one()
        with self.assertRaisesMessage(ValidationError, "stopped"):
            self.schedule(code="STOP", is_active=False).generate_one()
        ending = self.schedule(code="END", end_date=datetime.date(2026, 2, 15))
        ending.generate_one()
        with self.assertRaisesMessage(ValidationError, "reached its end date"):
            ending.generate_one()
        # The run takes what it can and hands back what it could not, schedule by schedule.
        made, refused = generate_due_journals(as_of=datetime.date(2026, 12, 31))
        self.assertEqual((made, refused), ([], [
            ("EMPTY", "This schedule has no lines to post."),
            ("OFF", "The schedule's lines do not balance: debits 100.00 and credits 80.00."),
        ]))
        with self.assertRaisesMessage(ValidationError, "either a debit or a credit, not both"):
            RecurringJournalLine.objects.create(schedule=ending, account=self.rent, debit=Decimal("1"), credit=Decimal("1"))
        with self.assertRaisesMessage(ValidationError, "either a debit or a credit"):
            RecurringJournalLine.objects.create(schedule=ending, account=self.rent)
        with self.assertRaisesMessage(ValidationError, "cannot end before it starts"):
            RecurringJournal.objects.create(code="BACK", memo="x", start_date=datetime.date(2026, 2, 1),
                                            end_date=datetime.date(2026, 1, 1))

    def test_a_schedule_with_entries_stays(self):
        schedule = self.schedule()
        schedule.generate_one()
        with self.assertRaises(ProtectedError):
            schedule.delete()


class ApiTests(RecurringTestCase):
    def test_the_bookkeeper_keeps_the_schedule_and_takes_a_draft_from_it(self):
        books = self.as_("Bookkeeper")
        made = books.post("/api/accounting/recurring-journals/", {
            "code": "INS", "memo": "Insurance amortised", "interval": "monthly", "start_date": "2026-04-01"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        pk = made.json()["id"]
        self.assertEqual(made.json()["next_run_date"], "2026-04-01")
        for body in ({"account": self.rent.pk, "debit": "1000.00", "cost_centre": self.office.pk},
                     {"account": self.accrued.pk, "credit": "1000.00"}):
            line = books.post("/api/accounting/recurring-journal-lines/", {"schedule": pk, **body}, format="json")
            self.assertEqual(line.status_code, 201, line.content)
        taken = books.post(f"/api/accounting/recurring-journals/{pk}/generate/", {}, format="json")
        self.assertEqual(taken.status_code, 200, taken.content)
        self.assertEqual((taken.json()["date"], taken.json()["posted"], taken.json()["recurring_journal"]),
                         ("2026-04-01", False, pk))
        listed = books.get("/api/accounting/journal-entries/", {"recurring_journal": pk}).json()
        self.assertEqual([row["id"] for row in listed], [taken.json()["id"]])
        # A schedule that posts is run by someone who may post.
        self.assertEqual(books.patch(f"/api/accounting/recurring-journals/{pk}/", {"auto_post": True},
                                     format="json").status_code, 200)
        self.assertEqual(books.post(f"/api/accounting/recurring-journals/{pk}/generate/", {}, format="json").status_code, 403)
        self.assertEqual(books.post("/api/accounting/recurring-journals/run/", {"as_of": "2026-05-01"},
                                    format="json").status_code, 403)
        controller = self.as_("Controller")
        posted = controller.post(f"/api/accounting/recurring-journals/{pk}/generate/", {}, format="json")
        self.assertEqual((posted.status_code, posted.json()["posted"], posted.json()["date"]), (200, True, "2026-05-01"))
        ran = controller.post("/api/accounting/recurring-journals/run/", {"as_of": "2026-07-01"}, format="json")
        self.assertEqual(([row["date"] for row in ran.json()["made"]], ran.json()["refused"]),
                         (["2026-06-01", "2026-07-01"], []))
        gone = controller.delete(f"/api/accounting/recurring-journals/{pk}/")
        self.assertEqual(gone.status_code, 400, gone.content)
        self.assertIn("Still used by", gone.json()["non_field_errors"][0])

    def test_who_may(self):
        self.assertEqual(self.as_("Purchasing Clerk").get("/api/accounting/recurring-journals/").status_code, 403)
        # The right to post journals is what the auto-posting run needs, over and above adding entries.
        user = User.objects.create_user("preparer")
        user.user_permissions.add(*Permission.objects.filter(codename__in=[
            "view_recurringjournal", "add_recurringjournal", "add_journalentry", "view_journalentry"]))
        client = APIClient()
        client.force_authenticate(user)
        schedule = self.schedule(auto_post=True)
        self.assertEqual(client.post(f"/api/accounting/recurring-journals/{schedule.pk}/generate/", {},
                                     format="json").status_code, 403)
        schedule.auto_post = False
        schedule.save()
        self.assertEqual(client.post(f"/api/accounting/recurring-journals/{schedule.pk}/generate/", {},
                                     format="json").status_code, 200)
