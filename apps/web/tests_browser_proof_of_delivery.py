"""
The signed copy of a delivery in the browser, as the people who handle it.

The dispatch clerk (Warehouse Staff) opens a delivery that went out: no
signed copy can be kept until the receipt is saved; then the scan is
added, a wrong file is refused in words, a phone photograph is added over
it and the scan stays under it as the earlier copy, and taking the
photograph off leaves the scan in force again.

A Sales Rep reads no delivery, so their order page asks for none of this.
A rep a plant has also let read deliveries sees the signed copy of their
own customer's, opens it, and is offered nothing to add or remove; another
rep's customer's delivery and its paper are not there for them.
"""

import re
import tempfile

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from django.contrib.auth.models import Permission
from django.test import override_settings

from apps.core.attachments import Attachment, attach
from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.sales.models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine
from apps.sales.proof import PROOF_OF_DELIVERY

from .tests_browser import BrowserTestCase

PDF = b"%PDF-1.4 signed at the gate"            # 27 bytes
JPEG = b"\xff\xd8\xff\xe0 a photograph of it"   # 23 bytes


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="erp-pod-browser-"))
class ProofOfDeliveryInTheBrowserTests(BrowserTestCase):
    def setUp(self):
        super().setUp()
        self.sale = self.make_order("10")
        self.delivery = self.ship(self.sale, "10")

    def test_the_dispatch_clerk_keeps_the_signed_copy_replaces_it_and_takes_one_off(self):
        clerk = self.person("Warehouse Staff")
        page = self.sign_in(clerk, f"/app/sales/deliveries/{self.delivery.pk}")
        proof = page.get_by_role("region", name="Proof of delivery")
        expect(proof).to_contain_text("Save the receipt first")
        expect(proof.locator("input[type=file]")).to_have_count(0)

        received = page.get_by_role("region", name="Received")
        received.get_by_label("Received on").fill("2026-03-05")
        received.get_by_label("Received by").fill("R. Sharma, stores")
        received.get_by_label("Their GRN").fill("GRN-771")
        received.get_by_role("button", name="Save receipt").click()
        expect(proof).to_contain_text("No signed copy is kept yet.")
        # The receipt's section and the transport's shared a key ("1-") while both were empty; saving the
        # receipt changed one and React left a second Transport section on the page.
        expect(page.get_by_role("region", name="Transport")).to_have_count(1)

        # Refused in words, and nothing kept: a spreadsheet, then a .pdf that is not one inside.
        proof.locator("input[type=file]").set_input_files({
            "name": "pod.xlsx", "mimeType": "application/octet-stream", "buffer": b"PK not a paper"})
        expect(proof.get_by_role("alert")).to_contain_text("A proof of delivery is kept as .jpeg, .jpg, .pdf, .png")
        proof.locator("input[type=file]").set_input_files({
            "name": "pod.pdf", "mimeType": "application/pdf", "buffer": b"MZ not a paper"})
        expect(proof.get_by_role("alert")).to_contain_text("is not one inside")
        self.assertEqual(Attachment.objects.count(), 0)
        self.assertEqual(len(self.problems), 2, self.problems)  # the two refusals, as the console reports a 400
        self.problems.clear()

        proof.locator("input[type=file]").set_input_files({
            "name": "pod.pdf", "mimeType": "application/pdf", "buffer": PDF})
        rows = proof.locator("tbody tr")
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text("pod.pdf")
        expect(rows.first).to_contain_text("The signed copy")
        expect(rows.first).to_contain_text(clerk.get_username())
        expect(proof.get_by_role("alert")).to_have_count(0)
        expect(page.get_by_role("region", name="History")).to_contain_text("Proof of delivery: pod.pdf")
        expect(page.get_by_role("region", name="Attachments")).to_contain_text("pod.pdf · Proof of delivery")
        kept = Attachment.objects.get()
        self.assertEqual((kept.name, kept.size, kept.purpose, kept.created_by), ("pod.pdf", 27, PROOF_OF_DELIVERY, clerk))
        opened = page.request.get(f"{self.live_server_url}/api/core/attachments/{kept.pk}/download/")
        self.assertEqual((opened.status, opened.body()), (200, PDF))

        # A better copy is added; the scan stays under it.
        expect(proof).to_contain_text("Add a newer copy")
        proof.locator("input[type=file]").set_input_files({
            "name": "pod-photo.jpg", "mimeType": "image/jpeg", "buffer": JPEG})
        expect(rows).to_have_count(2)
        expect(rows.nth(0)).to_contain_text("pod-photo.jpg")
        expect(rows.nth(0)).to_contain_text("The signed copy")
        expect(rows.nth(1)).to_contain_text("pod.pdf")
        expect(rows.nth(1)).to_contain_text("An earlier copy")

        # Taken off again by whoever added it: the scan is the signed copy once more.
        proof.get_by_role("button", name=re.compile("Remove pod-photo.jpg")).click()
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text("pod.pdf")
        expect(rows.first).to_contain_text("The signed copy")
        expect(page.get_by_role("region", name="History")).to_contain_text("Removed")
        self.assertEqual(list(Attachment.objects.values_list("name", "purpose")), [("pod.pdf", PROOF_OF_DELIVERY)])
        self.delivery.refresh_from_db()
        self.assertEqual((str(self.delivery.received_on), self.delivery.received_by, self.delivery.receipt_reference),
                         ("2026-03-05", "R. Sharma, stores", "GRN-771"))
        self.assertEqual(self.problems, [])

    def received_with_its_paper(self, delivery, name):
        from django.core.files.uploadedfile import SimpleUploadedFile

        delivery.record_receipt("2026-03-05", "R. Sharma", "GRN-771")
        clerk = self.login_for("Warehouse Staff") if not hasattr(self, "_clerk") else self._clerk
        self._clerk = clerk
        return attach(delivery, SimpleUploadedFile(name, PDF), clerk, PROOF_OF_DELIVERY)

    def test_a_rep_reads_their_own_customers_signed_copy_and_nobody_elses(self):
        mine = self.received_with_its_paper(self.delivery, "pod.pdf")
        rep = self.person("Sales Rep")  # carries every customer there is now: Acme
        # The role reads no delivery: the rep's order page asks for no delivery and no paper.
        page = self.sign_in(rep, f"/app/sales/orders/{self.sale.pk}")
        expect(page.locator(".doc-head, .doc").first).to_contain_text(self.sale.number)
        page.wait_for_load_state("networkidle")
        self.assertEqual(self.problems, [])
        refused = page.request.get(f"{self.live_server_url}/api/core/attachments/{mine.pk}/download/")
        self.assertEqual(refused.status, 403)

        # A customer of another rep's, made after: this rep does not carry it.
        bolt = Party.objects.create(code="C-2", name="Bolt Traders", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=bolt, role=PartyRole.CUSTOMER)
        order = SalesOrder.objects.create(customer=bolt, order_date="2026-03-01", currency=self.usd)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=5, unit_price=100,
                                      revenue_account=self.revenue)
        order.confirm()
        other = Delivery.objects.create(sales_order=order, delivery_date="2026-03-03")
        DeliveryLine.objects.create(delivery=other, order_line=order.lines.first(), warehouse=self.warehouse,
                                    quantity_shipped=5)
        other.post()
        theirs = self.received_with_its_paper(other, "bolt.pdf")

        # The plant lets this rep read deliveries too.
        rep.user_permissions.add(Permission.objects.get(content_type__app_label="sales", codename="view_delivery"))
        page.goto(f"{self.live_server_url}/app/sales/deliveries/{self.delivery.pk}")
        proof = page.get_by_role("region", name="Proof of delivery")
        expect(proof.locator("tbody tr")).to_have_count(1)
        expect(proof.locator("tbody tr").first).to_contain_text("pod.pdf")
        expect(proof.locator("tbody tr").first).to_contain_text("The signed copy")
        expect(proof.locator("input[type=file]")).to_have_count(0)
        expect(proof.get_by_role("button", name=re.compile("Remove"))).to_have_count(0)
        opened = page.request.get(f"{self.live_server_url}/api/core/attachments/{mine.pk}/download/")
        self.assertEqual((opened.status, opened.body()), (200, PDF))
        page.wait_for_load_state("networkidle")
        self.assertEqual(self.problems, [])

        for address in (f"/api/core/attachments/{theirs.pk}/download/",
                        f"/api/core/attachments/?model=sales.delivery&id={other.pk}&purpose={PROOF_OF_DELIVERY}",
                        f"/api/sales/deliveries/{other.pk}/"):
            self.assertEqual(page.request.get(f"{self.live_server_url}{address}").status, 404, address)
        self.assertEqual(Attachment.objects.count(), 2)
