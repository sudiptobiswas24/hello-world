"""
review_qrecall: races on PostgreSQL for the recall (O207) and the accept (O123). Probes, not product code.
Run: DATABASE_URL=postgres://erp:erp@localhost:5432/review_qrecall ... manage.py test apps.e2e.tests_probe_review_qrecall_races
"""

import unittest
from decimal import Decimal

from django.db import connection
from django.test import TransactionTestCase

from apps.e2e.tests_races import fixture, race


@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class TheLiftAgainstANewRejection(TransactionTestCase):
    def trial(self):
        from apps.manufacturing import tests_recall
        from apps.quality.models import Inspection, Reading, ReleaseStatus
        from apps.quality.release import release_status

        made = fixture(self, tests_recall.RecallTestCase)
        first = made.reject_b1()
        plan = made.bag_spec.inspection_plan
        line = plan.lines.get()
        draft = Inspection.objects.create(lot=made.b1, plan=plan, inspected_on=tests_recall.TODAY,
                                          number="QC-RACE-R2")  # numbered: no lock on the numbering row
        for number, value in enumerate(tests_recall.OUT[:line.sample_size], start=1):
            Reading.objects.create(inspection=draft, plan_line=line, value=Decimal(value),
                                   sample_reference=f"S{number}")
        outcomes = race(Inspection,
                        lambda: Inspection.objects.get(pk=first.pk).void("typo"),
                        lambda: Inspection.objects.get(pk=draft.pk).post())
        self.assertEqual(outcomes, ["done", "done"], outcomes)
        held = [release_status(lot) for lot in (made.a, made.b)]
        recalls = [Inspection.objects.filter(lot=lot, recalled_by__isnull=False, voided_at__isnull=True).count()
                   for lot in (made.a, made.b)]
        print(f"\nRACE lift/new rejection: statuses {held}, open recalls {recalls}")
        self.assertEqual(held, [ReleaseStatus.HELD] * 2)
        self.assertEqual(recalls, [1, 1], "held by the second rejection, and no recall waits for the Quality Manager")

    def test_trial_1(self):
        self.trial()

    def test_trial_2(self):
        self.trial()

    def test_trial_3(self):
        self.trial()


@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class TheVerdictAgainstADecision(TransactionTestCase):
    def test_a_decision_on_the_recall_and_a_new_verdict_on_the_lot_at_once_leave_one_standing(self):
        from apps.manufacturing import tests_recall
        from apps.quality.models import Disposition, Inspection, Reading

        made = fixture(self, tests_recall.RecallTestCase)
        made.reject_b1()
        recall = made.recall_of(made.a)
        plan = made.bag_spec.inspection_plan
        line = plan.lines.get()
        draft = Inspection.objects.create(lot=made.a, plan=plan, inspected_on=tests_recall.TODAY,
                                          number="QC-RACE-A")
        for number, value in enumerate(tests_recall.IN[:line.sample_size], start=1):
            Reading.objects.create(inspection=draft, plan_line=line, value=Decimal(value),
                                   sample_reference=f"S{number}")
        from django.contrib.auth.models import User

        asha = User.objects.get(username="asha")
        asha_post = User.objects.get(username="asha")
        outcomes = race(Inspection,
                        lambda: Inspection.objects.get(pk=recall.pk).decide(asha, Disposition.CONCESSION, "As is"),
                        lambda: Inspection.objects.get(pk=draft.pk).post(by=asha_post))
        print(f"\nRACE decide/new verdict on the recalled lot: {outcomes}")
        self.assertEqual(sorted(o == "done" for o in outcomes), [False, True], outcomes)
