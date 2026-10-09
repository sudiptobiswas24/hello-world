"""
The release gate, asked of the code.

A held lot reached production by doors that each answered for
themselves: planning read today's plan beside the verdict, and a tape
load and a roll mount asked only through an issue a backflushed run never
makes. Planted, each is reported; asked through the gate, neither is.
"""

from pathlib import Path

from django.test import SimpleTestCase

from apps.core.management.commands.audit_invariants import Command


def asked(text, label="planning", name="example.py"):
    return Command().release_past_the_gate([label], {label: {Path(f"apps/{label}") / name: text}})


DECIDED_HERE = '''
def unusable(item, lot):
    plan = plan_for(item)
    return plan is not None and plan.is_mandatory and release_status(lot) != "released"
'''

LOADED_UNASKED = '''
def load(run, lot, kg):
    if not run.backflush:
        issue = MaterialIssue.objects.create(work_order=run)
    return TapeLoad.objects.create(work_order=run, lot=lot, kg=kg)
'''


class ReleaseDecidedOutsideTheGateTests(SimpleTestCase):
    def test_a_plan_and_a_verdict_read_together_are_reported(self):
        (finding,) = asked(DECIDED_HERE)
        self.assertEqual(finding[0], "release decided outside the gate")
        self.assertIn("planning/example.py:2 unusable()", finding[1])

    def test_the_gate_itself_is_not(self):
        self.assertEqual(asked(DECIDED_HERE, label="quality", name="release.py"), [])

    def test_a_verdict_shown_is_not(self):
        self.assertEqual(asked("def status(lot):\n    return release_status(lot)\n"), [])


class AskedOnlyByTheIssueTests(SimpleTestCase):
    def test_a_door_that_issues_unless_backflushed_and_never_asks_is_reported(self):
        (finding,) = asked(LOADED_UNASKED, label="manufacturing")
        self.assertEqual(finding[0], "release asked only by the issue")
        self.assertIn("manufacturing/example.py:2 load()", finding[1])

    def test_one_that_asks_the_gate_is_not(self):
        asking = LOADED_UNASKED.replace("    if not run.backflush:",
                                        "    check_released(lot.item, lot)\n    if not run.backflush:")
        self.assertEqual(asked(asking, label="manufacturing"), [])
