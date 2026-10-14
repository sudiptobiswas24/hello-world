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
        asking = LOADED_UNASKED.replace(
            "    if not run.backflush:",
            "    check_released(lot.item, lot, warehouse=run.warehouse)\n    if not run.backflush:")
        self.assertEqual(asked(asking, label="manufacturing"), [])


BALED_UNASKED = '''
def pack(warehouse, rows):
    for lot, bags in rows:
        check_released(lot.item, lot, action="be baled")
'''


class AskedWithoutTheWarehouseTests(SimpleTestCase):
    """O147: a gate asked of the batch alone let a quarantine bay's bundles be baled."""

    def test_a_question_that_names_no_warehouse_is_reported(self):
        (finding,) = asked(BALED_UNASKED, label="manufacturing")
        self.assertEqual(finding[0], "release asked without the warehouse")
        self.assertIn("manufacturing/example.py:2 pack()", finding[1])

    def test_a_warehouse_of_none_is_no_warehouse(self):
        planted = BALED_UNASKED.replace('action="be baled"', 'action="be baled", warehouse=None')
        self.assertEqual(len(asked(planted, label="manufacturing")), 1)
        self.assertEqual(len(asked("def held(item, lot):\n    return lot_held(item, lot, None)\n")), 1)

    def test_one_that_names_it_is_not(self):
        named = BALED_UNASKED.replace('action="be baled"', 'action="be baled", warehouse=warehouse')
        self.assertEqual(asked(named, label="manufacturing"), [])
        self.assertEqual(asked("def held(item, lot, place):\n    return lot_held(item, lot, place)\n"), [])

    def test_the_gate_itself_is_not(self):
        self.assertEqual(asked(BALED_UNASKED, label="quality", name="release.py"), [])
