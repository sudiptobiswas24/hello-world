"""
A posted invoice's draft-time field changed around its guards (a shell's bulk update: save() would refuse it)
is in the edit log with its old and new value, and the auditor reads it there; a rep does not.
"""

import datetime

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from .models import Invoice
from .tests_settlement import SettlementTestCase


class TheAuditorReadsAChangeToAPostedInvoiceTests(SettlementTestCase):
    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_edit_log_shows_the_old_and_new_date(self):
        call_command("setup_roles", verbosity=0)
        invoice = self.make_invoice()
        self.assertTrue(invoice.posted)
        call_command("shell", command=f"from apps.sales.models import Invoice; "
                                      f"Invoice.objects.filter(pk={invoice.pk}).update(invoice_date='2026-02-01')")
        auditor = self.as_("Auditor")
        response = auditor.get("/api/core/edit-log/", {"model": "sales.invoice", "from": "2026-01-01",
                                                       "to": "2099-12-31", "kind": "updated"})
        self.assertEqual(response.status_code, 200, response.content)
        rows = [row for row in response.json() if row["object_id"] == invoice.pk
                and any(change["field"] == "invoice_date" for change in row["changes"])]
        self.assertEqual(len(rows), 1, response.json())
        self.assertEqual((rows[0]["through"], rows[0]["who_name"]), ("shell", ""))
        self.assertIn({"field": "invoice_date", "label": "invoice date", "from": "2026-03-01", "to": "2026-02-01"},
                      rows[0]["changes"])
        self.assertEqual(Invoice.objects.get(pk=invoice.pk).invoice_date, datetime.date(2026, 2, 1))
        # Read only, and only theirs and the Controller's.
        self.assertIn(auditor.delete(f"/api/core/edit-log/{rows[0]['id']}/").status_code, (403, 405))
        self.assertIn(auditor.post("/api/core/edit-log/", {}, format="json").status_code, (403, 405))
        self.assertEqual(self.as_("Controller").get("/api/core/edit-log/").status_code, 200)
        self.assertEqual(self.as_("Sales Rep").get("/api/core/edit-log/").status_code, 403)
