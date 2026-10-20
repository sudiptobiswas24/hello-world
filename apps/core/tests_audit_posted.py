"""
A posted document refuses to be edited and to be deleted: the audit asks both.

  A save() that refuses and no delete(): reported as deletable. Both
  guards: not reported. A delete() that never says posted: reported. A
  document reported to its owner and guarded since: its exemption is stale.
"""

from pathlib import Path

from django.test import SimpleTestCase

from apps.core.management.commands.audit_invariants import Command

SAVE = (
    "    def save(self, *args, **kwargs):\n"
    "        if self.posted:\n"
    "            raise ValidationError('posted')\n"
    "        super().save(*args, **kwargs)\n"
)


def audit(body):
    text = "class MaterialIssue(AuditModel):\n" + body
    found = Command().mutable_posted_documents(
        ["manufacturing"], {"manufacturing": {Path("apps/manufacturing/example.py"): text}})
    return [(shape, detail) for shape, detail in found if "MaterialIssue " in detail]


class APostedDocumentIsNotDeletedTests(SimpleTestCase):
    def test_a_save_guard_alone_is_reported(self):
        self.assertEqual([shape for shape, _ in audit(SAVE)], ["deletable posted document"])

    def test_both_guards_are_not(self):
        self.assertEqual(audit(SAVE + (
            "    def delete(self, *args, **kwargs):\n"
            "        if self.posted:\n"
            "            raise ValidationError('posted')\n"
            "        return super().delete(*args, **kwargs)\n"
        )), [])

    def test_a_delete_that_never_asks_is_reported(self):
        self.assertEqual([shape for shape, _ in audit(SAVE + (
            "    def delete(self, *args, **kwargs):\n"
            "        return super().delete(*args, **kwargs)\n"
        ))], ["deletable posted document"])

    def test_an_exemption_outlives_nothing(self):
        # The list is empty since O205 guarded the last two; one put back on it
        # for the test stands in.
        from unittest import mock

        with mock.patch.object(Command, "DELETABLE_REPORTED", {"inventory.StockCount": "for the test"}):
            found = Command().mutable_posted_documents(["inventory"], {"inventory": {
                Path("apps/inventory/example.py"): "class StockCount(AuditModel):\n" + SAVE + (
                    "    def delete(self, *args, **kwargs):\n"
                    "        if self.posted:\n"
                    "            raise ValidationError('posted')\n"
                )}})
        self.assertEqual([shape for shape, detail in found if "StockCount" in detail], ["stale exemption"])


class AWeighedRecordIsNotEditedOverTheApiTests(SimpleTestCase):
    """O272: a weighed record a route writes refuses edits and deletion once weighed."""

    def audit(self, body):
        from apps.manufacturing.rolls import FabricRoll

        text = "class FabricRoll(AuditModel):\n" + body
        return [shape for shape, detail in Command().weighed_records_written_over_the_api(
            ["manufacturing"], {"manufacturing": {Path("apps/manufacturing/example.py"): text}},
            written={FabricRoll}) if "FabricRoll " in detail]

    def test_a_roll_with_no_guards_is_reported_twice(self):
        self.assertEqual(self.audit("    notes = 1\n"), ["weighed record written over the api"] * 2)

    def test_both_guards_are_not(self):
        guard = ("        if self.is_weighed():\n"
                 "            raise ValidationError('weighed')\n")
        self.assertEqual(self.audit("    def save(self):\n" + guard + "    def delete(self):\n" + guard), [])
