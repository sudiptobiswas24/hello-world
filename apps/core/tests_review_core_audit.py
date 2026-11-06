"""
Review probes for fix_core's widened audit checks (g22 review_core): O246, O40, O46, O212. For each, a planted
instance the check must report and a near-miss it must not. A check that reports nothing proves nothing, so every
test plants first.
"""
from pathlib import Path

from django.test import SimpleTestCase

from apps.core.management.commands.audit_invariants import Command


def where(found):
    return [detail.split(" ")[0] for _shape, detail in found]


class Greenwich(SimpleTestCase):
    def found(self, text):
        return where(Command().greenwich_dates(["gst"], {"gst": {Path("apps/gst/x.py"): text}}))

    def test_planted_spellings_are_reported(self):
        for text in (
            "def f(a):\n    return a.astimezone(timezone.utc).date()\n",
            "def f(t):\n    return datetime.datetime.fromtimestamp(t, tz=timezone.utc).date()\n",
            "def f(row):\n    return row.posted_on_moment.date()\n",
            "def f(answer):\n    return getattr(answer, 'ack_dt').date()\n",
            "X = parse_datetime('2026-10-14T01:00:00+00:00').date()\n",
            "def f(a):\n    return (a or b).date()\n",
        ):
            self.assertEqual(len(self.found(text)), 1, text)

    def test_near_misses_are_not_reported(self):
        for text in (
            "def f(a):\n    return timezone.localtime(a).date()\n",
            "def f(s):\n    return datetime.datetime.strptime(s, '%Y').date()\n",
            "def f(a):\n    return timezone.make_naive(a).date()\n",
            "def f(a):\n    '''a.date() is the UTC day'''\n    return 1\n",
        ):
            self.assertEqual(self.found(text), [], text)

    def test_an_is_aware_question_about_one_moment_does_not_excuse_the_date_of_another(self):
        # f asks is_aware() of `a` and then takes the UTC day of `b`, a moment it never asked about.
        text = ("def f(a, b):\n    if timezone.is_aware(a):\n        a = timezone.localtime(a)\n"
                "    return a.date(), b.date()\n")
        self.assertEqual(len(self.found(text)), 1, "b.date() is the UTC day of an aware moment and goes unreported")


class Shelf(SimpleTestCase):
    def found(self, text, name="deliveries.py"):
        return where(Command().shelf_read_before_lock(["sales"], {"sales": {Path(f"apps/sales/{name}"): text}}))

    def test_a_read_two_calls_before_the_lock_is_reported(self):
        text = ("def deep(item):\n    lock_position(item)\n\ndef mid(item):\n    deep(item)\n\n"
                "class D:\n    def post(self, item):\n        if item.on_hand_at(1) < 3:\n            raise ValueError\n"
                "        mid(item)\n")
        self.assertEqual(self.found(text), ["sales/deliveries.py:D.post"])

    def test_a_read_made_by_a_helper_before_the_lock_is_reported(self):
        text = ("def enough(item):\n    return item.on_hand_at(1) >= 3\n\n"
                "class D:\n    def post(self, item):\n        if not enough(item):\n            raise ValueError\n"
                "        lock_position(item)\n")
        self.assertEqual(self.found(text), ["sales/deliveries.py:D.post"])

    def test_a_lock_then_a_read_is_not_reported(self):
        text = ("def enough(item):\n    return item.on_hand_at(1) >= 3\n\n"
                "class D:\n    def post(self, item):\n        lock_position(item)\n        enough(item)\n")
        self.assertEqual(self.found(text), [])

    def test_a_helper_that_locks_then_reads_is_clean_and_so_is_its_caller(self):
        text = ("def hold_and_read(item):\n    lock_position(item)\n    return item.on_hand_at(1)\n\n"
                "class D:\n    def post(self, item):\n        hold_and_read(item)\n")
        self.assertEqual(self.found(text), [])

    def test_a_read_then_a_lock_taken_through_lock_positions_with_a_keyword_is_reported(self):
        text = "def post(item, wh):\n    item.available_at(wh)\n    lock_positions([(item, wh)])\n"
        self.assertEqual(self.found(text), ["sales/deliveries.py:post"])

    def test_the_rate_is_reported_in_a_function_that_is_not_the_named_one_of_the_same_file(self):
        text = ("class Item:\n    def removal_unit_cost(self, w):\n        return self.average_cost_at(w)\n"
                "    def write_down(self, w, q):\n        return q * self.average_cost_at(w)\n"
                "    def margin(self, w):\n        return self.removal_unit_cost(w)\n")
        found = Command().per_unit_withdrawal_rates(["inventory"], {"inventory": {Path("apps/inventory/models.py"): text}})
        self.assertEqual(sorted(where(found)), ["inventory/models.py:5", "inventory/models.py:7"])


class OpenRuns(SimpleTestCase):
    def found(self, text):
        return where(Command().unlocked_open_runs(["manufacturing"], {"manufacturing": {Path("apps/manufacturing/x.py"): text}}))

    def test_released_asked_of_an_order_or_run_unheld_is_reported(self):
        for receiver in ("order", "run", "self.work_order", "op.work_order", "plan.production_run"):
            text = f"def f(x):\n    if {receiver}.status == WorkOrderStatus.RELEASED:\n        return 1\n"
            self.assertEqual(self.found(text), ["manufacturing/x.py:2"], receiver)

    def test_released_in_a_tuple_is_reported(self):
        text = "def f(order):\n    return order.status in (WorkOrderStatus.RELEASED, WorkOrderStatus.DRAFT)\n"
        self.assertEqual(self.found(text), ["manufacturing/x.py:2"])

    def test_released_asked_while_holding_it_is_not_reported(self):
        text = "def f(order):\n    lock_rows(order)\n    return order.status == WorkOrderStatus.RELEASED\n"
        self.assertEqual(self.found(text), [])

    def test_released_asked_of_an_order_kept_under_another_name_is_reported(self):
        # `wo`, `job` and `jwo` are how this code names a work order or a job-work order; none ends in order or run.
        for receiver in ("wo", "job", "jwo", "o"):
            text = f"def f(x):\n    if {receiver}.status == WorkOrderStatus.RELEASED:\n        return 1\n"
            self.assertEqual(self.found(text), ["manufacturing/x.py:2"], receiver)

    def test_a_status_other_than_released_is_not_reported(self):
        self.assertEqual(self.found("def f(order):\n    return order.status == WorkOrderStatus.CLOSED\n"), [])


class CodeNotWords(SimpleTestCase):
    def found(self, body):
        sources = {"gst": {Path("apps/gst/example.py"): "from apps.manufacturing.example import tally\n"},
                   "manufacturing": {Path("apps/manufacturing/example.py"): body}}
        return [detail for _shape, detail in Command().reported_without_the_period(["gst", "manufacturing"], sources)]

    def test_a_comment_and_a_string_name_no_model_and_an_attribute_does(self):
        self.assertEqual(self.found('def tally(roll):\n    # made by the loom\n    return "made"\n'), [])
        self.assertTrue(any("ProcessRoll" in d for d in self.found("def tally(roll):\n    return roll.made\n")))
        self.assertTrue(any("ProcessRoll" in d for d in self.found("def tally(roll):\n    return made\n")))
