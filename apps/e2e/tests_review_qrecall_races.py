"""
Races on PostgreSQL for the recall (O207, review F3) and a decision against a new verdict, kept as
regression tests. Run: DATABASE_URL=postgres://... manage.py test apps.e2e.tests_review_qrecall_races
"""

import unittest
from decimal import Decimal

from django.db import connection
from django.test import TransactionTestCase

from apps.e2e.tests_races import fixture, race


@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class TheLiftAgainstANewRejection(TransactionTestCase):
    def trial(self):
        from django.contrib.auth.models import User

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
                        # The Quality Manager's void: since rereview 3 a void naming nobody lifts no hold.
                        lambda: Inspection.objects.get(pk=first.pk).void("typo", by=User.objects.get(username="asha")),
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


@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class TheLiftDecidesUnderTheLocksTests(TransactionTestCase):
    """
    Review F3, made to happen every time: B1's rejection voided (its lift not yet run), the lift
    and a second rejection of B1 at once, each held at its first write after its checks: the
    lift at voiding RB-A's recall, the rejection at saving itself. Without the lift taking the
    post's locks, both pass their checks on what the other has not written: the lift voids the
    recalls the rejection has just found open and told on, and no recall waits. With them, one
    waits for the other, whichever comes first, and each lot has its one recall.
    """

    def test_a_lift_and_a_new_rejection_at_once_leave_each_lot_its_recall(self):
        from django.utils import timezone

        from apps.manufacturing import tests_recall
        from apps.quality.models import Inspection, Reading, ReleaseStatus
        from apps.quality.recall import lift
        from apps.quality.release import release_status

        made = fixture(self, tests_recall.RecallTestCase)
        first = made.reject_b1()
        # Voided as void() writes it, its lift held back so that the race can run it.
        Inspection.objects.filter(pk=first.pk).update(voided_at=timezone.now(), voided_reason="typo")
        plan = made.bag_spec.inspection_plan
        line = plan.lines.get()
        draft = Inspection.objects.create(lot=made.b1, plan=plan, inspected_on=tests_recall.TODAY,
                                          number="QC-RACE-R3")  # numbered: no lock on the numbering row
        for number, value in enumerate(tests_recall.OUT[:line.sample_size], start=1):
            Reading.objects.create(inspection=draft, plan_line=line, value=Decimal(value),
                                   sample_reference=f"S{number}")
        outcomes = race(Inspection,
                        lambda: lift(made.b1, f"{first.number} was voided: typo"),
                        lambda: Inspection.objects.get(pk=draft.pk).post())
        self.assertEqual(outcomes, ["done", "done"], outcomes)
        held = [release_status(lot) for lot in (made.a, made.b)]
        recalls = [Inspection.objects.filter(lot=lot, recalled_by__isnull=False, voided_at__isnull=True).count()
                   for lot in (made.a, made.b)]
        print(f"\nRACE lift under the locks / new rejection: statuses {held}, open recalls {recalls}")
        self.assertEqual(held, [ReleaseStatus.HELD] * 2)
        self.assertEqual(recalls, [1, 1], "held by the second rejection, and no recall waits for the Quality Manager")
