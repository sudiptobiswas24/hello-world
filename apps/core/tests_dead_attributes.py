"""
A class that says one name twice keeps only the second: the audit says so.

  Two read-only lists on one serializer: reported, at the first. A
  property and its setter, one name on purpose: not reported.
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
