import datetime
import unittest
from decimal import Decimal

from django.db import connection
from django.test import tag

from apps.e2e.tests_races import RaceCase, fixture, race
from apps.sales import tests_base as sales_fixture


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class Probe(RaceCase):
    def build(self):
        from apps.sales.models import DunningLevel
        sales = fixture(self, sales_fixture.SalesTestCase)
        for n, d in (("A", 7), ("B", 30)):
            DunningLevel.objects.create(name=n, days_overdue=d)
        order = sales.make_order("10", "100")
        sales.ship(order, 10)
        return sales, sales.bill(order, on=datetime.date(2026, 3, 1))

    def test_different_days_at_once(self):
        from apps.sales.models import DunningNotice, run_dunning
        sales, invoice = self.build()
        d1 = invoice.due_date + datetime.timedelta(days=8)
        d2 = invoice.due_date + datetime.timedelta(days=40)
        out = race(DunningNotice, lambda: run_dunning(as_of=d1, send=False), lambda: run_dunning(as_of=d2, send=False))
        sent = sorted(DunningNotice.objects.filter(invoice=invoice).values_list("level__days_overdue", flat=True))
        print("RACE diff days", out, sent)

    def test_three_runs_with_mail(self):
        from django.core import mail
        from apps.sales.models import DunningNotice, run_dunning
        sales, invoice = self.build()
        d = invoice.due_date + datetime.timedelta(days=8)
        out = race(DunningNotice, *[lambda: run_dunning(as_of=d, send=True)] * 3)
        print("RACE three", out, DunningNotice.objects.filter(invoice=invoice).count(), len(mail.outbox))

    def test_two_hand_credits_at_once(self):
        from apps.sales.models import Invoice
        from apps.accounting.models import JournalEntry
        sales, invoice = self.build()
        line = invoice.lines.get()
        def credit():
            Invoice.objects.get(pk=invoice.pk).create_credit_note(quantities={type(line).objects.get(pk=line.pk): Decimal("6")})
        out = race(JournalEntry, credit, credit)
        invoice = Invoice.objects.get(pk=invoice.pk)
        print("RACE credits", out, invoice.amount_due())
