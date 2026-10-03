"""
Regression tests for a bug found by previewing the admin: posted
documents rendered an editable form with a SAVE button. The model
correctly refused the write, but that surfaced as an uncaught
ValidationError (HTTP 500) rather than a read-only page.
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from apps.accounting.models import Account, AccountType, JournalEntry, JournalLine


@override_settings(ALLOWED_HOSTS=["testserver"])
class PostedDocumentAdminTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser("root", "root@example.com", "pw")
        self.client.force_login(self.admin_user)

        self.cash = Account.objects.create(code="1000", name="Cash", account_type=AccountType.ASSET)
        self.revenue = Account.objects.create(
            code="4000", name="Revenue", account_type=AccountType.INCOME
        )

    def make_posted_entry(self):
        entry = JournalEntry.objects.create(date="2026-01-01", memo="original memo")
        JournalLine.objects.create(entry=entry, account=self.cash, debit=Decimal("100"))
        JournalLine.objects.create(entry=entry, account=self.revenue, credit=Decimal("100"))
        entry.post()
        return entry

    def test_posted_entry_admin_page_has_no_save_button(self):
        entry = self.make_posted_entry()
        response = self.client.get(f"/admin/accounting/journalentry/{entry.pk}/change/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="_save"')

    def test_draft_entry_admin_page_still_editable(self):
        entry = JournalEntry.objects.create(date="2026-01-01", memo="draft")
        response = self.client.get(f"/admin/accounting/journalentry/{entry.pk}/change/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="_save"')

    def test_saving_a_posted_entry_is_refused_without_a_server_error(self):
        entry = self.make_posted_entry()
        response = self.client.post(
            f"/admin/accounting/journalentry/{entry.pk}/change/",
            {
                "date": "2026-01-01",
                "reference": "TAMPERED",
                "memo": "tampered after posting",
                "lines-TOTAL_FORMS": "0",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
            },
        )
        self.assertEqual(response.status_code, 403)
        entry.refresh_from_db()
        self.assertEqual(entry.memo, "original memo")

    def test_posted_entry_cannot_be_deleted_via_admin(self):
        entry = self.make_posted_entry()
        response = self.client.post(f"/admin/accounting/journalentry/{entry.pk}/delete/", {"post": "yes"})
        self.assertIn(response.status_code, (403, 302))
        self.assertTrue(JournalEntry.objects.filter(pk=entry.pk).exists())
