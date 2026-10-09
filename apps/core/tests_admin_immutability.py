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

        self.cash = Account.objects.create(code="1000", name="Cash", account_type=AccountType.ASSET, holds_money=True)
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


class TheAdminsBulkDeleteAsksEachRowTests(TestCase):
    """
    O87: the changelist's "delete selected" ran one QuerySet.delete(),
    which never calls a model's delete(). A staff HR Admin deleted approved
    leave the API refuses. It goes through each row's delete() now, all or
    nothing.
    """

    def setUp(self):
        import datetime

        from django.contrib.auth.models import Group
        from django.core.management import call_command

        from apps.hr import tests_leave

        call_command("setup_roles", verbosity=0)
        case = type("Leave", (tests_leave.LeaveTestCase,), {"runTest": lambda self: None})()
        case.setUp()
        self.approved = case.request(case.employee("A1"), datetime.date(2026, 7, 1), datetime.date(2026, 7, 3))
        self.approved.approve(by=case.boss)
        self.pending = case.request(case.employee("A2"), datetime.date(2026, 7, 6), datetime.date(2026, 7, 7))
        self.hr = User.objects.create_user("hr-staff", password="pw", is_staff=True)
        self.hr.groups.add(Group.objects.get(name="HR Admin"))
        self.client.force_login(self.hr)

    def delete_selected(self, *rows):
        with override_settings(ALLOWED_HOSTS=["testserver"]):
            return self.client.post("/admin/hr/leaverequest/", {
                "action": "delete_selected", "_selected_action": [row.pk for row in rows], "post": "yes"},
                follow=True)

    def test_approved_leave_the_api_refuses_is_not_deleted_in_bulk(self):
        from apps.hr.models import LeaveRequest

        response = self.delete_selected(self.approved, self.pending)
        self.assertEqual(response.status_code, 200)
        self.assertIn("cancel it rather than delete it", response.content.decode())
        # All or nothing: the pending one beside it stays too.
        self.assertEqual(set(LeaveRequest.objects.values_list("pk", flat=True)), {self.approved.pk, self.pending.pk})

    def test_what_may_be_deleted_still_is(self):
        from apps.hr.models import LeaveRequest

        self.delete_selected(self.pending)
        self.assertEqual(list(LeaveRequest.objects.values_list("pk", flat=True)), [self.approved.pk])

    def test_every_changelist_has_this_action(self):
        from django.contrib import admin

        from apps.core.admin_mixins import delete_selected

        self.assertIs(admin.site.get_action("delete_selected"), delete_selected)
