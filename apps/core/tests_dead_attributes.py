"""
A class that says one name twice keeps only the second: the audit says so.

  Two read-only lists on one serializer: reported, at the first. A
  property and its setter, one name on purpose: not reported. A model method
  nothing names outside its tests: reported (O320).
"""

from pathlib import Path

from django.test import SimpleTestCase

from apps.core.management.commands.audit_invariants import Command


def audit(text):
    return Command().dead_class_attributes(["core"], {"core": {Path("apps/core/example.py"): text}})


class DeadClassAttributeTests(SimpleTestCase):
    def test_the_first_of_two_is_reported(self):
        found = audit("class Meta:\n    read_only_fields = ['reverses']\n    read_only_fields = ['posted']\n")
        self.assertEqual([shape for shape, _ in found], ["dead class attribute"])
        self.assertIn("example.py:2 Meta.read_only_fields is said again at line 3", found[0][1])

    def test_a_property_and_its_setter_are_one_name_on_purpose(self):
        self.assertEqual(audit("class A:\n    @property\n    def x(self):\n        return 1\n"
                               "    @x.setter\n    def x(self, value):\n        pass\n"), [])


class PlantedRun:
    """A planted model: one method asked for, one asked for by nothing, one read by a serializer's string."""

    def suggestions(self):
        return []

    def fenced(self):
        return []

    @property
    def is_complete(self):
        return True

    def save(self, *args, **kwargs):
        pass


CORPUS = (
    "class Run:\n    def suggestions(self): ...\n    def fenced(self): ...\n    def is_complete(self): ...\n"
    "for order in run.suggestions():\n    pass\n"
    "fields = ['id', 'is_complete']\n"
)


def methods(corpus=CORPUS, exempt=None):
    command = Command()
    if exempt is not None:
        command.CALLED_FROM_OUTSIDE = exempt
    return command.uncalled_model_methods(["planning"], classes=[("planning", PlantedRun)], corpus=corpus)


class AMethodNothingAsksForIsReportedTests(SimpleTestCase):
    """O320: the helper check read module-level functions only, so a model's method escaped it."""

    def test_the_method_named_only_where_it_is_defined_is_reported(self):
        found = methods()
        self.assertEqual([shape for shape, _ in found], ["uncalled method"])
        self.assertIn("planning.PlantedRun.fenced() (tests_dead_attributes.py:", found[0][1])

    def test_a_property_named_in_a_field_list_and_a_hook_django_calls_are_asked_for(self):
        found = methods(corpus=CORPUS + "run.fenced()\n")
        self.assertEqual(found, [])

    def test_a_name_two_classes_define_needs_a_caller_beyond_its_definitions(self):
        found = methods(corpus=CORPUS + "class Other:\n    def fenced(self): ...\n")
        self.assertEqual([detail.split("()")[0] for _, detail in found], ["planning.PlantedRun.fenced"])

    def test_an_exemption_with_its_reason_silences_it(self):
        self.assertEqual(methods(exempt={"planning.PlantedRun.fenced": "called by name from elsewhere"}), [])

    def test_an_exemption_for_a_method_gone_is_stale(self):
        found = methods(corpus=CORPUS + "run.fenced()\n", exempt={"planning.PlantedRun.gone": "once called"})
        self.assertEqual([shape for shape, _ in found], ["stale exemption"])
        self.assertIn("planning.PlantedRun.gone", found[0][1])
