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
        found = Command().mutable_posted_documents(["quality"], {"quality": {
            Path("apps/quality/example.py"): "class Inspection(AuditModel):\n" + SAVE + (
                "    def delete(self, *args, **kwargs):\n"
                "        if self.posted:\n"
                "            raise ValidationError('posted')\n"
            )}})
        self.assertEqual([shape for shape, _ in found], ["stale exemption"])
