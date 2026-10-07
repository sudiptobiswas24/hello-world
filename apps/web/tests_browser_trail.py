"""
A rep in the browser attaches the customer's enquiry to a lead, sees it
listed with the history line it made, opens the file, and removes it.
"""

import re
import tempfile

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from django.test import override_settings

from apps.core.attachments import Attachment
from apps.sales.crm import Lead
from apps.sales.tests_base import carries_every_customer

from .tests_browser import BrowserTestCase


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="erp-trail-"))
class AttachmentsInTheBrowserTests(BrowserTestCase):
    def test_a_file_is_kept_with_the_lead_and_taken_off_again(self):
        rep = self.person("Sales Rep")
        carries_every_customer(rep)
        lead = Lead.objects.create(company_name="Shree Cement", source="exhibition")
        page = self.sign_in(rep, f"/app/sales/leads/{lead.pk}")
        attachments = page.locator("section.related", has_text="Attachments")
        expect(attachments).to_contain_text("None yet.")
        attachments.locator("input[type=file]").set_input_files({
            "name": "enquiry.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 twenty thousand sacks a month",
        })
        expect(attachments.locator("tbody")).to_contain_text("enquiry.pdf")
        expect(page.locator("section.related", has_text="History").locator("tbody")).to_contain_text("Attached")
        kept = Attachment.objects.get()
        self.assertEqual((kept.name, kept.size, kept.created_by), ("enquiry.pdf", 38, rep))
        opened = page.request.get(f"{self.live_server_url}/api/core/attachments/{kept.pk}/download/")
        self.assertEqual((opened.status, opened.body()), (200, b"%PDF-1.4 twenty thousand sacks a month"))

        attachments.get_by_role("button", name=re.compile("Remove enquiry.pdf")).click()
        expect(attachments).to_contain_text("None yet.")
        self.assertEqual(Attachment.objects.count(), 0)
        self.assertEqual(self.problems, [])
