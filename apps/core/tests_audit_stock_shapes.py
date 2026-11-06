"""
The stock shapes the stores audit found more than once, asked of the code.

A shelf read before it is held, a withdrawal priced at a rate multiplied
back up, and movements written around the checks their save() makes:
each was a defect in more than one place before it was a rule.
"""

from pathlib import Path

from django.test import SimpleTestCase

from apps.core.management.commands.audit_invariants import Command


def asked(check, text, name="example.py"):
    return getattr(Command(), check)(["sales"], {"sales": {Path("apps/sales") / name: text}})


READ_THEN_LOCK = '''
def post(self):
    if item.on_hand_at(shelf) < wanted:
        raise ValidationError("short")
    lock_positions([(item, shelf)])
'''

LOCK_THEN_READ = '''
def post(self):
    lock_positions([(item, shelf)])
    if item.on_hand_at(shelf) < wanted:
        raise ValidationError("short")
'''


class ShelfReadBeforeItsLockTests(SimpleTestCase):
    def test_a_read_before_the_lock_is_reported(self):
        (finding,) = asked("shelf_read_before_lock", READ_THEN_LOCK)
        self.assertEqual(finding[0], "shelf read before its lock")
        self.assertIn("sales/example.py:post", finding[1])

    def test_the_lock_first_is_not(self):
        self.assertEqual(asked("shelf_read_before_lock", LOCK_THEN_READ), [])

    def test_a_read_with_no_lock_in_the_function_is_not_this_shape(self):
        self.assertEqual(asked("shelf_read_before_lock", "def f():\n    return item.on_hand_at(shelf)\n"), [])


class PerUnitWithdrawalRateTests(SimpleTestCase):
    def test_a_rate_asked_for_a_withdrawal_is_reported(self):
        (finding,) = asked("per_unit_withdrawal_rates",
                           "def f():\n    cost = item.removal_unit_cost(shelf, quantity)\n")
        self.assertEqual(finding[0], "per-unit withdrawal rate")
        self.assertIn("sales/example.py:2", finding[1])

    def test_the_total_is_not(self):
        self.assertEqual(asked("per_unit_withdrawal_rates",
                               "def f():\n    cost = item.cost_of_removing(shelf, quantity)\n"), [])


class MovementsWrittenAroundSaveTests(SimpleTestCase):
    def test_a_bulk_insert_is_reported(self):
        (finding,) = asked("movements_written_around_save",
                           "def f():\n    StockMovement.objects.bulk_create(rows)\n")
        self.assertIn("bulk_create", finding[1])

    def test_a_queryset_update_is_reported(self):
        (finding,) = asked("movements_written_around_save",
                           "def f():\n    StockMovement.objects.filter(pk=1).update(quantity=0)\n")
        self.assertIn("update", finding[1])

    def test_one_by_one_is_not(self):
        self.assertEqual(asked("movements_written_around_save",
                               "def f():\n    StockMovement.objects.create(item=item)\n"), [])


class ArrivalsWithoutTheirDepartureTests(SimpleTestCase):
    # O210: an arrival that does not name the leg it came from is not followed.
    def test_a_transfer_in_naming_no_departure_is_reported(self):
        (finding,) = asked("arrivals_without_their_departure",
                           "def f():\n    StockMovement.objects.create(item=item,\n"
                           "        movement_type=MovementType.TRANSFER_IN, quantity=q)\n")
        self.assertEqual(finding[0], "arrival without its departure")
        self.assertIn("sales/example.py:2", finding[1])

    def test_one_naming_it_is_not(self):
        self.assertEqual(asked("arrivals_without_their_departure",
                               "def f():\n    StockMovement.objects.create(item=item, moved_from=out,\n"
                               "        movement_type=MovementType.TRANSFER_IN, quantity=q)\n"), [])

    def test_a_filter_on_arrivals_is_not(self):
        self.assertEqual(asked("arrivals_without_their_departure",
                               "def f():\n    StockMovement.objects.filter(movement_type=MovementType.TRANSFER_IN)\n"),
                         [])


class OnePostedDocumentAdminRuleTests(SimpleTestCase):
    # O45: three admin mixins refused the same thing, one of them answering yes unasked.
    COPY = "class A:\n    def has_delete_permission(self, request, obj=None):\n        return not obj.posted\n"

    def test_a_copy_is_reported(self):
        (finding,) = asked("posted_admin_rule_copied", self.COPY, name="admin.py")
        self.assertIn("sales/admin.py:2", finding[1])

    def test_the_rule_itself_is_not(self):
        self.assertEqual(asked("posted_admin_rule_copied", self.COPY, name="admin_mixins.py"), [])

    def test_every_admin_of_a_posted_document_keeps_it_read_only(self):
        from django.contrib import admin
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        from apps.core.admin_mixins import PostedImmutableAdminMixin, PostedImmutableInlineMixin

        request = RequestFactory().get("/")
        request.user = type("Root", (AnonymousUser,), {"is_active": True, "is_staff": True, "is_superuser": True,
                                                        "has_perm": lambda self, perm, obj=None: True})()
        posted = type("Posted", (), {"posted": True})()
        kept = []
        for model, model_admin in admin.site._registry.items():
            if isinstance(model_admin, PostedImmutableAdminMixin):
                kept.append(model.__name__)
                self.assertFalse(model_admin.has_change_permission(request, posted), model)
                self.assertFalse(model_admin.has_delete_permission(request, posted), model)
                for inline in model_admin.get_inline_instances(request):
                    if isinstance(inline, PostedImmutableInlineMixin):
                        self.assertFalse(inline.has_add_permission(request, posted), inline)
        for name in ("MaterialIssue", "ProductionEntry", "TimeBooking", "StockAdjustment", "StockCount",
                     "Invoice", "Bill", "JournalEntry", "Inspection"):
            self.assertIn(name, kept)
