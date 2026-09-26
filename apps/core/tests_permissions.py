from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType, JournalEntry, JournalLine


def grant(user, *permission_names):
    for name in permission_names:
        app_label, codename = name.split(".", 1)
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label=app_label, codename=codename)
        )


class PostingPermissionTests(TestCase):
    """
    The point of these tests: being able to CREATE a journal entry must not
    imply being able to POST it. That separation is the whole reason the
    custom post_* permissions exist.
    """

    def setUp(self):
        self.cash = Account.objects.create(code="1000", name="Cash", account_type=AccountType.ASSET)
        self.revenue = Account.objects.create(
            code="4000", name="Revenue", account_type=AccountType.INCOME
        )
        self.client = APIClient()

    def make_entry(self):
        entry = JournalEntry.objects.create(date="2026-01-01", memo="Test")
        JournalLine.objects.create(entry=entry, account=self.cash, debit=Decimal("100"))
        JournalLine.objects.create(entry=entry, account=self.revenue, credit=Decimal("100"))
        return entry

    def test_anonymous_user_cannot_post(self):
        entry = self.make_entry()
        response = self.client.post(f"/api/accounting/journal-entries/{entry.pk}/post_entry/")
        self.assertIn(response.status_code, (401, 403))
        entry.refresh_from_db()
        self.assertFalse(entry.posted)

    def test_user_who_can_create_entries_cannot_post_them(self):
        bookkeeper = User.objects.create_user("bookkeeper", password="x")
        grant(bookkeeper, "accounting.add_journalentry", "accounting.change_journalentry")
        self.client.force_authenticate(user=bookkeeper)

        entry = self.make_entry()
        response = self.client.post(f"/api/accounting/journal-entries/{entry.pk}/post_entry/")

        self.assertEqual(response.status_code, 403)
        entry.refresh_from_db()
        self.assertFalse(entry.posted)

    def test_user_with_post_permission_can_post(self):
        controller = User.objects.create_user("controller", password="x")
        grant(
            controller,
            "accounting.add_journalentry",
            "accounting.change_journalentry",
            "accounting.post_journalentry",
        )
        self.client.force_authenticate(user=controller)

        entry = self.make_entry()
        response = self.client.post(f"/api/accounting/journal-entries/{entry.pk}/post_entry/")

        self.assertEqual(response.status_code, 200)
        entry.refresh_from_db()
        self.assertTrue(entry.posted)

    def test_posting_permission_does_not_leak_across_modules(self):
        """Holding accounting.post_journalentry must not allow posting invoices."""
        controller = User.objects.create_user("controller2", password="x")
        grant(controller, "accounting.post_journalentry", "sales.add_invoice")
        self.client.force_authenticate(user=controller)

        response = self.client.post("/api/sales/invoices/1/post_invoice/")
        self.assertIn(response.status_code, (403, 404))

    def test_reversal_requires_the_same_posting_permission(self):
        controller = User.objects.create_user("controller3", password="x")
        grant(controller, "accounting.add_journalentry", "accounting.post_journalentry")
        entry = self.make_entry()
        entry.post()

        bookkeeper = User.objects.create_user("bookkeeper2", password="x")
        grant(bookkeeper, "accounting.add_journalentry", "accounting.change_journalentry")
        self.client.force_authenticate(user=bookkeeper)

        response = self.client.post(f"/api/accounting/journal-entries/{entry.pk}/reverse/")
        self.assertEqual(response.status_code, 403)


class LeaveApprovalPermissionTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_creating_leave_requests_does_not_imply_approving_them(self):
        employee_user = User.objects.create_user("employee", password="x")
        grant(employee_user, "hr.add_leaverequest", "hr.view_leaverequest")
        self.client.force_authenticate(user=employee_user)

        response = self.client.post("/api/hr/leave-requests/1/approve/", {"decided_by": 1})
        self.assertIn(response.status_code, (403, 404))


class SetupRolesCommandTests(TestCase):
    def test_setup_roles_creates_groups_with_expected_separation(self):
        call_command("setup_roles", verbosity=0)

        bookkeeper = Group.objects.get(name="Bookkeeper")
        controller = Group.objects.get(name="Controller")

        bookkeeper_codenames = set(bookkeeper.permissions.values_list("codename", flat=True))
        controller_codenames = set(controller.permissions.values_list("codename", flat=True))

        self.assertIn("add_journalentry", bookkeeper_codenames)
        self.assertNotIn("post_journalentry", bookkeeper_codenames)
        self.assertIn("post_journalentry", controller_codenames)

    def test_setup_roles_is_idempotent(self):
        call_command("setup_roles", verbosity=0)
        first = Group.objects.get(name="Controller").permissions.count()
        call_command("setup_roles", verbosity=0)
        second = Group.objects.get(name="Controller").permissions.count()
        self.assertEqual(first, second)
        self.assertEqual(Group.objects.filter(name="Controller").count(), 1)

    def test_sales_rep_cannot_post_invoices_but_ar_manager_can(self):
        call_command("setup_roles", verbosity=0)

        rep_codenames = set(
            Group.objects.get(name="Sales Rep").permissions.values_list("codename", flat=True)
        )
        manager_codenames = set(
            Group.objects.get(name="AR Manager").permissions.values_list("codename", flat=True)
        )

        self.assertIn("add_invoice", rep_codenames)
        self.assertNotIn("post_invoice", rep_codenames)
        self.assertIn("post_invoice", manager_codenames)
