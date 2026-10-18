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


class TheAuditSeesTheUtcDayOfAnyMomentTests(SimpleTestCase):
    """O246: greenwich_dates reports .date() of an aware moment handed in, not only now().date()."""

    def findings(self, text):
        from pathlib import Path

        from apps.core.management.commands.audit_invariants import Command

        return [detail.split(" ")[0] for _shape, detail in Command().greenwich_dates(
            ["gst"], {"gst": {Path("apps/gst/example.py"): text}})]

    def test_an_acknowledgement_s_date_is_reported_and_the_plants_day_of_it_is_not(self):
        handed_in = ("def acknowledged(answer):\n"
                     "    moment = parse_datetime(answer['AckDt'])\n"
                     "    return moment.date()\n")
        self.assertEqual(self.findings(handed_in), ["gst/example.py:3"])
        self.assertEqual(self.findings("def day():\n    return timezone.now().date()\n"), ["gst/example.py:2"])
        for passes in ("def acknowledged(moment):\n    return timezone.localtime(moment).date()\n",
                       "def parsed(text):\n    return datetime.datetime.strptime(text, '%d/%m/%Y').date()\n",
                       "def day(moment):\n    if timezone.is_aware(moment):\n        moment = timezone.localtime(moment)\n"
                       "    return moment.date()\n",
                       "def said():\n    '''Never posted_at.date(): the UTC day.'''\n    return 1\n"):
            self.assertEqual(self.findings(passes), [], passes)

    def test_the_code_as_it_stands_takes_no_utc_day(self):
        from apps.core.management.commands.audit_invariants import OUR_APPS, Command, app_sources

        self.assertEqual(Command().greenwich_dates(list(OUR_APPS), app_sources()), [])


class TheAuditFollowsTheShelfAndTheRateTests(SimpleTestCase):
    """O40: per_unit_withdrawal_rates asks average_cost_at and inventory/models.py; shelf_read_before_lock
    follows calls within a module."""

    @staticmethod
    def sources(label, name, text):
        from pathlib import Path

        return {label: {Path(f"apps/{label}/{name}"): text}}

    def test_a_withdrawal_at_the_average_is_reported_in_inventory_models_too(self):
        from apps.core.management.commands.audit_invariants import Command

        text = ("class Item:\n    def write_down(self, warehouse, quantity):\n"
                "        return quantity * self.average_cost_at(warehouse)\n")
        found = Command().per_unit_withdrawal_rates(["inventory"], self.sources("inventory", "models.py", text))
        self.assertEqual([detail.split(" ")[0] for _shape, detail in found], ["inventory/models.py:3"])
        handed_on = text.replace("write_down", "removal_unit_cost")
        self.assertEqual(Command().per_unit_withdrawal_rates(
            ["inventory"], self.sources("inventory", "models.py", handed_on)), [])

    def test_a_read_then_a_helper_that_locks_is_reported(self):
        from apps.core.management.commands.audit_invariants import Command

        text = ("def hold(item):\n    lock_position(item)\n\n"
                "class Delivery:\n    def post(self, item):\n        if item.on_hand_at(1) < 10:\n"
                "            raise ValueError\n        hold(item)\n")
        found = Command().shelf_read_before_lock(["sales"], self.sources("sales", "deliveries.py", text))
        self.assertEqual([detail.split(" ")[0] for _shape, detail in found], ["sales/deliveries.py:Delivery.post"])
        locked_first = text.replace("        if item.on_hand_at(1) < 10:\n            raise ValueError\n        hold(item)\n",
                                    "        hold(item)\n        if item.on_hand_at(1) < 10:\n            raise ValueError\n")
        self.assertEqual(Command().shelf_read_before_lock(["sales"], self.sources("sales", "deliveries.py", locked_first)),
                         [])

    def test_the_tree_as_it_stands_names_every_rate_and_read_it_keeps(self):
        from apps.core.management.commands.audit_invariants import OUR_APPS, Command, app_sources

        sources = app_sources()
        self.assertEqual(Command().per_unit_withdrawal_rates(list(OUR_APPS), sources)
                         + Command().shelf_read_before_lock(list(OUR_APPS), sources), [])
