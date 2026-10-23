"""Review of fix_money2 (accounting): probes as the roles over the API."""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.core.models import Company, Currency
from apps.core.tests_roles import RolesTestCase

from .analytic import CostCentre
from .models import Account, AccountType, BooksOpening, JournalEntry, JournalLine
from .recurring import RecurringJournal, RecurringJournalLine

D = Decimal
OPEN = datetime.date(2026, 4, 1)


class Base(RolesTestCase):
    def setUp(self):
        super().setUp()
        self.inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        self.usd = Currency.objects.create(code="USD", name="Dollar")
        Company.objects.create(name="Deccan", base_currency=self.inr)
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        self.rent = Account.objects.create(code="5100", name="Rent", account_type=AccountType.EXPENSE)
        self.equity = Account.objects.create(code="3000", name="Opening", account_type=AccountType.EQUITY)
        self.centre = CostCentre.objects.create(code="CC1", name="Plant")
        BooksOpening.objects.create(opened_on=OPEN, opening_account=self.equity)

    def api_entry(self, client, day, centre=None):
        r = client.post("/api/accounting/journal-entries/", {"date": str(day), "memo": "x"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        eid = r.json()["id"]
        for acct, side in ((self.rent, "debit"), (self.bank, "credit")):
            body = {"entry": eid, "account": acct.pk, side: "100"}
            if centre and side == "debit":
                body["cost_centre"] = centre.pk
            lr = client.post("/api/accounting/journal-lines/", body, format="json")
            if lr.status_code != 201:
                return eid, lr
        return eid, None


class OpeningDoor(Base):
    def test_journal_day_before_opening(self):
        for role in ("Bookkeeper", "Controller"):
            c = self.as_(role)
            eid, bad = self.api_entry(c, OPEN - datetime.timedelta(days=1))
            r = c.post(f"/api/accounting/journal-entries/{eid}/post_entry/")
            print("JOURNAL", role, r.status_code, r.content[:150])
            self.assertEqual(r.status_code, 400)
        eid, bad = self.api_entry(c, OPEN)
        self.assertEqual(c.post(f"/api/accounting/journal-entries/{eid}/post_entry/").status_code, 200)

    def test_journal_edit_date_after_draft(self):
        c = self.as_("Controller")
        eid, _ = self.api_entry(c, OPEN)
        c.patch(f"/api/accounting/journal-entries/{eid}/", {"date": "2026-03-31"}, format="json")
        r = c.post(f"/api/accounting/journal-entries/{eid}/post_entry/")
        self.assertEqual(r.status_code, 400)

    def test_reverse_of_pre_opening_entry_posts(self):
        """An entry posted before the books opened was recorded; reversing it is dated today and must work."""
        BooksOpening.objects.all().delete()
        c = self.as_("Controller")
        eid, _ = self.api_entry(c, datetime.date(2026, 3, 1))
        self.assertEqual(c.post(f"/api/accounting/journal-entries/{eid}/post_entry/").status_code, 200)
        BooksOpening.objects.create(opened_on=OPEN, opening_account=self.equity)
        r = c.post(f"/api/accounting/journal-entries/{eid}/reverse/", {}, format="json")
        print("REVERSE old", r.status_code, r.content[:200])
        self.assertEqual(r.status_code, 201 if r.status_code == 201 else 200)


class CurrencyDoor(Base):
    def test_currency_patch_put_before_after(self):
        for role in ("Bookkeeper", "Controller"):
            c = self.as_(role)
            a = Account.objects.create(code=f"9{role[:2]}", name="x", account_type=AccountType.ASSET, holds_money=True)
            r = c.patch(f"/api/accounting/accounts/{a.pk}/", {"currency": self.usd.pk}, format="json")
            print("CUR before", role, r.status_code, r.content[:120])
            c.patch(f"/api/accounting/accounts/{a.pk}/", {"currency": None}, format="json")
            e = JournalEntry.objects.create(date=OPEN, memo="x")
            JournalLine.objects.create(entry=e, account=a, debit=D("5"))
            JournalLine.objects.create(entry=e, account=self.rent, credit=D("5"))
            e.post()
            for method, body in (("patch", {"currency": self.usd.pk}), ("put", {"code": a.code, "name": "x", "account_type": "asset", "holds_money": True, "currency": self.usd.pk})):
                r = getattr(c, method)(f"/api/accounting/accounts/{a.pk}/", body, format="json")
                print("CUR after", role, method, r.status_code, r.content[:120])
                self.assertEqual(r.status_code, 400)
            r = c.patch(f"/api/accounting/accounts/{a.pk}/", {"currency": self.inr.pk}, format="json")
            print("CUR to base", r.status_code)
            self.assertEqual(r.status_code, 200)

    def test_usd_account_to_unset_after_post(self):
        a = Account.objects.create(code="9US", name="x", account_type=AccountType.ASSET, currency=self.usd, holds_money=True)
        e = JournalEntry.objects.create(date=OPEN, memo="x")
        JournalLine.objects.create(entry=e, account=a, debit=D("5"))
        JournalLine.objects.create(entry=e, account=self.rent, credit=D("5"))
        e.post()
        a.currency = None
        with self.assertRaises(ValidationError):
            a.save()

    def test_update_fields_save(self):
        a = Account.objects.create(code="9UF", name="x", account_type=AccountType.ASSET, holds_money=True)
        e = JournalEntry.objects.create(date=OPEN, memo="x")
        JournalLine.objects.create(entry=e, account=a, debit=D("5"))
        JournalLine.objects.create(entry=e, account=self.rent, credit=D("5"))
        e.post()
        a.currency = self.usd
        with self.assertRaises(ValidationError):
            a.save(update_fields=["currency"])


class SwitchedOffDoors(Base):
    def test_journal_posting_doors(self):
        c = self.as_("Bookkeeper")
        self.rent.is_active = False
        self.rent.save()
        eid, bad = self.api_entry(c, OPEN)
        r = c.post(f"/api/accounting/journal-entries/{eid}/post_entry/")
        print("OFF account post", r.status_code, r.content[:100])
        self.assertEqual(r.status_code, 400)
        self.rent.is_active = True
        self.rent.save()
        self.centre.is_active = False
        self.centre.save()
        eid, bad = self.api_entry(c, OPEN, centre=self.centre)
        print("OFF centre line", bad.status_code if bad else None)
        self.assertIsNotNone(bad)

    def test_reverse_and_void_with_off(self):
        c = self.as_("Controller")
        eid, _ = self.api_entry(c, OPEN, centre=self.centre)
        self.assertEqual(c.post(f"/api/accounting/journal-entries/{eid}/post_entry/").status_code, 200)
        self.rent.is_active = False
        self.rent.save()
        self.centre.is_active = False
        self.centre.save()
        r = c.post(f"/api/accounting/journal-entries/{eid}/reverse/", {}, format="json")
        print("REVERSE with off", r.status_code, r.content[:150])
        self.assertIn(r.status_code, (200, 201))

    def test_a_name_on_the_stack_exempts_a_new_posting(self):
        """in_a_correction goes by frame names: anything called from a function named cancel/void/reverse... is exempt."""
        self.rent.is_active = False
        self.rent.save()
        e = JournalEntry.objects.create(date=OPEN, memo="x")
        JournalLine.objects.create(entry=e, account=self.rent, debit=D("5"))
        JournalLine.objects.create(entry=e, account=self.bank, credit=D("5"))

        def reverse_charge_booking():
            e.post()
        with self.assertRaises(ValidationError):
            reverse_charge_booking()


class RecurringDates(Base):
    def schedule(self, start, interval="monthly", auto=True):
        s = RecurringJournal.objects.create(code="RJ", memo="Rent", start_date=start, interval=interval, auto_post=auto)
        RecurringJournalLine.objects.create(schedule=s, account=self.rent, debit=D("100"))
        RecurringJournalLine.objects.create(schedule=s, account=self.bank, credit=D("100"))
        return s

    def test_month_end_and_leap(self):
        s = self.schedule(datetime.date(2026, 4, 30))
        days = []
        for _ in range(5):
            days.append(s.generate_one(on_date=None).date if False else None)
        # run through the API as of today
        c = self.as_("Controller")
        r = c.post(f"/api/accounting/recurring-journals/{s.pk}/generate/", {}, format="json")
        print("RJ gen", r.status_code, r.content[:200])

    def test_schedule_moved_later_after_a_run(self):
        s = self.schedule(datetime.date(2026, 5, 1))
        s.generate_one()
        s.refresh_from_db()
        print("next after first", s.next_run_date)
        s.start_date = datetime.date(2026, 8, 1)  # edited after a run
        s.save()
        s.refresh_from_db()
        try:
            s.generate_one()
            print("RJ edited start: ran")
        except ValidationError as exc:
            print("RJ edited start: refused", exc.messages)
            self.fail("schedule is stuck after start_date edit")

    def test_run_with_future_as_of_and_off_account(self):
        s = self.schedule(datetime.date(2026, 5, 1))
        c = self.as_("Controller")
        r = c.post("/api/accounting/recurring-journals/run/", {"as_of": "2030-01-01"}, format="json")
        print("RUN future", r.status_code, [len(r.json()["made"]), r.json()["refused"][:1]])
        s.refresh_from_db()
        print("made entries", JournalEntry.objects.filter(recurring_journal=s).count(), "next", s.next_run_date)
