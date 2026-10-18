"""
O15: every step that reverses or posts an entry on a day it is given asks correction_date(), outside hr and
assets as well. What stays on DATED_ELSEWHERE corrects nothing (a schedule's run) or is dated by a caller that
asks the rule (the exchange difference a returned payment releases).
"""
from django.test import SimpleTestCase


class EveryCorrectionAsksTheRuleTests(SimpleTestCase):
    def test_no_step_is_dated_without_the_rule_and_only_three_are_excepted(self):
        from apps.core.management.commands.audit_invariants import OUR_APPS, Command, app_sources

        self.assertEqual(Command().corrections_dated_without_the_rule(list(OUR_APPS), app_sources()), [])
        self.assertEqual(sorted(Command.DATED_ELSEWHERE), [
            "accounting.RealisedOnItsOwnDay.release_exchange_difference",
            "accounting.RecurringJournal.generate_one",
            "sales.RecurringInvoice.generate_one",
        ])
