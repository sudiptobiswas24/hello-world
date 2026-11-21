"""
The signed copy of a delivery, kept as a file (apps/sales/proof.py), asked
of the API as the people who do it: a dispatch clerk (Warehouse Staff)
keeps it, a Quality Inspector only reads it, a Stores Manager may remove
anyone's, a Sales Rep reads no delivery at all, and a login limited to its
own customers reads only theirs.

Refused, before anything is kept:
    a rep (no right to read deliveries)             403, reading and keeping
    a reader who may not record the receipt         403 keeping; reads it
    a delivery not yet shipped                      400 "went out"
    a return                                        400 "went out"
    shipped, receipt not recorded                   400 "Record the receipt first"
    a spreadsheet, a text file                      400 "kept as .jpeg, .jpg, .pdf, .png"
    a .pdf that is not one inside                   400 "is not one inside"
    an empty file; one over 20 MB                   400
    a purpose nobody registered; on a party         400
    changing one (PUT, PATCH)                       405
    another customer's, for a login limited to its own   404, list and download
    removed by a clerk who did not add it           403
Then: kept with who and when, the second one current and the first under
it, removed by its own clerk and the first current again.
"""

import tempfile

from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps.core.attachments import MAX_BYTES, Attachment, check_file

from django.core.management import call_command

from apps.core.models import PartyRole

from .models import CustomerProfile, Delivery, DeliveryLine, InvoicePolicy, SalesOrder, SalesOrderLine
from .proof import PROOF_OF_DELIVERY
from .tests_base import SalesTestCase
from .tests_reps import RepTestCase, party

FILES = "/api/core/attachments/"
PDF = b"%PDF-1.4 signed at the gate"            # 27 bytes
JPEG = b"\xff\xd8\xff\xe0 a photograph of it"   # 23 bytes
PNG = b"\x89PNG\r\n\x1a\n a scan"


def upload(name, body):
    return SimpleUploadedFile(name, body)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="erp-pod-"))
class ProofOfDeliveryTests(SalesTestCase):
    a_rep = RepTestCase.rep
    as_user = RepTestCase.as_user

    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.rep_a, self.rep_b = self.a_rep("rep-a"), self.a_rep("rep-b")
        self.acme, self.beta = self.customer, party("BETA", PartyRole.CUSTOMER)
        for customer, rep in ((self.acme, self.rep_a), (self.beta, self.rep_b)):
            CustomerProfile.objects.update_or_create(party=customer, defaults={"sales_rep": rep.employee.party})
        self.delivery = self.shipped_to(self.acme)
        self.where = {"model": "sales.delivery", "id": self.delivery.pk}
        self.clerk = self.person("clerk", "Warehouse Staff")
        self.as_clerk = self.as_user(self.clerk)

    def person(self, name, role):
        user = User.objects.create_user(name)
        user.groups.add(Group.objects.get(name=role))
        return user

    def shipped_to(self, customer, post=True):
        order = SalesOrder.objects.create(customer=customer, order_date="2026-03-01", currency=self.usd,
                                          invoice_policy=InvoicePolicy.ORDERED)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=10, unit_price=100,
                                      revenue_account=self.revenue)
        order.confirm()
        delivery = Delivery.objects.create(sales_order=order, delivery_date="2026-03-03")
        DeliveryLine.objects.create(delivery=delivery, order_line=order.lines.first(), warehouse=self.warehouse,
                                    quantity_shipped=10)
        if post:
            delivery.post()
        return delivery

    def receive(self, delivery=None, client=None):
        delivery = delivery or self.delivery
        done = (client or self.as_clerk).post(f"/api/sales/deliveries/{delivery.pk}/received/", {
            "received_on": "2026-03-05", "received_by": "R. Sharma, stores", "reference": "GRN-771"}, format="json")
        self.assertEqual(done.status_code, 200, done.content)

    def keep(self, client, name="pod.pdf", body=PDF, where=None, purpose=PROOF_OF_DELIVERY):
        data = {**(where or self.where), "file": upload(name, body)}
        if purpose is not None:
            data["purpose"] = purpose
        return client.post(FILES, data, format="multipart")

    def proofs(self, client, where=None):
        return client.get(FILES, {**(where or self.where), "purpose": PROOF_OF_DELIVERY})

    def refused(self, response, status, words):
        self.assertEqual(response.status_code, status, response.content)
        self.assertIn(words, response.content.decode())
        self.assertEqual(Attachment.objects.count(), 0)

    # -- refused ----------------------------------------------------------------------------------

    def test_a_rep_reads_no_delivery_and_so_no_proof_of_one(self):
        self.receive()
        rep = self.as_user(self.rep_a)
        self.assertEqual(self.proofs(rep).status_code, 403)
        self.refused(self.keep(rep), 403, "")

    def test_reading_the_delivery_is_not_keeping_its_proof(self):
        self.receive()
        inspector = self.as_user(self.person("inspector", "Quality Inspector"))
        self.refused(self.keep(inspector), 403, "what recording its receipt takes")
        kept = self.keep(self.as_clerk).json()
        self.assertEqual([row["name"] for row in self.proofs(inspector).json()], ["pod.pdf"])
        opened = inspector.get(f"{FILES}{kept['id']}/download/")
        self.assertEqual((opened.status_code, b"".join(opened.streaming_content)), (200, PDF))

    def test_only_a_delivery_that_went_out_and_was_received_has_one(self):
        draft = self.shipped_to(self.acme, post=False)
        self.refused(self.keep(self.as_clerk, where={"model": "sales.delivery", "id": draft.pk}), 400,
                     "Only a delivery that went out has a proof of delivery.")
        self.refused(self.keep(self.as_clerk), 400, "Record the receipt first")
        back = self.shipped_to(self.acme)
        Delivery.objects.filter(pk=back.pk).update(reverses=self.delivery)  # as a customer return is
        self.refused(self.keep(self.as_clerk, where={"model": "sales.delivery", "id": back.pk}), 400,
                     "Only a delivery that went out has a proof of delivery.")

    def test_the_wrong_kind_of_file_is_refused_by_name_and_by_what_is_inside(self):
        self.receive()
        for name in ("pod.xlsx", "pod.txt", "pod"):
            self.refused(self.keep(self.as_clerk, name, PDF), 400,
                         "A proof of delivery is kept as .jpeg, .jpg, .pdf, .png")
        self.refused(self.keep(self.as_clerk, "pod.pdf", b"MZ\x90 not a paper"), 400, "is not one inside")
        self.refused(self.keep(self.as_clerk, "pod.jpg", PDF), 400, "is not one inside")
        self.refused(self.keep(self.as_clerk, "pod.pdf", b""), 400, "")
        with self.assertRaisesMessage(ValidationError, "up to 20 MB is kept"):
            check_file("pod.jpg", MAX_BYTES + 1, PROOF_OF_DELIVERY)
        self.assertEqual(check_file("pod.jpg", MAX_BYTES, PROOF_OF_DELIVERY), "image/jpeg")

    def test_a_purpose_nobody_set_up_or_on_another_kind_of_record_is_refused(self):
        self.receive()
        self.refused(self.keep(self.as_clerk, purpose="signed_copy"), 400, "is not a purpose a file is kept for")
        self.assertEqual(self.as_clerk.get(FILES, {**self.where, "purpose": "signed_copy"}).status_code, 400)
        manager = self.as_user(self.person("ar", "AR Manager"))
        self.refused(self.keep(manager, where={"model": "core.party", "id": self.acme.pk}), 400,
                     "is kept on a sales.delivery, not a core.party")

    def test_what_a_file_was_kept_as_is_never_changed(self):
        self.receive()
        kept = self.keep(self.as_clerk).json()
        for method in (self.as_clerk.put, self.as_clerk.patch):
            self.assertEqual(method(f"{FILES}{kept['id']}/", {"purpose": ""}, format="json").status_code, 405)
        row = Attachment.objects.get()
        row.purpose = ""
        with self.assertRaisesMessage(ValidationError, "What a file was kept as is not changed"):
            row.save()
        plain = self.keep(self.as_clerk, "lr.pdf", purpose=None).json()
        row = Attachment.objects.get(pk=plain["id"])
        row.purpose = PROOF_OF_DELIVERY
        with self.assertRaisesMessage(ValidationError, "What a file was kept as is not changed"):
            row.save()
        self.assertEqual(sorted(Attachment.objects.values_list("name", "purpose")),
                         [("lr.pdf", ""), ("pod.pdf", PROOF_OF_DELIVERY)])

    def test_a_login_limited_to_its_own_customers_reads_only_their_proofs(self):
        theirs = self.shipped_to(self.beta)
        self.receive()
        self.receive(theirs)
        mine = self.keep(self.as_clerk).json()
        other = self.keep(self.as_clerk, "beta.pdf", where={"model": "sales.delivery", "id": theirs.pk}).json()
        # The Sales Rep role reads no delivery; this rep is also given that one right, as a plant might.
        self.rep_a.user_permissions.add(Permission.objects.get(content_type__app_label="sales",
                                                               codename="view_delivery"))
        rep = self.as_user(User.objects.get(pk=self.rep_a.pk))
        self.assertEqual([row["name"] for row in self.proofs(rep).json()], ["pod.pdf"])
        self.assertEqual(rep.get(f"{FILES}{mine['id']}/download/").status_code, 200)
        self.assertEqual(self.proofs(rep, {"model": "sales.delivery", "id": theirs.pk}).status_code, 404)
        self.assertEqual(rep.get(f"{FILES}{other['id']}/download/").status_code, 404)
        self.assertEqual(rep.delete(f"{FILES}{other['id']}/").status_code, 404)
        # Reading is not keeping: the rep may not record a receipt, so adds no proof either.
        self.assertEqual(self.keep(rep, "mine.pdf").status_code, 403)
        self.assertEqual(Attachment.objects.count(), 2)

    # -- kept, replaced by adding, removed --------------------------------------------------------

    def test_kept_with_who_and_when_replaced_by_adding_and_removed_as_any_file_is(self):
        self.receive()
        first = self.keep(self.as_clerk)
        self.assertEqual(first.status_code, 201, first.content)
        first = first.json()
        self.assertEqual((first["name"], first["size"], first["media_type"], first["by"], first["mine"],
                          first["purpose"], first["purpose_label"], first["current"]),
                         ("pod.pdf", 27, "application/pdf", "clerk", True, PROOF_OF_DELIVERY, "Proof of delivery",
                          True))
        self.assertEqual(Attachment.objects.get().created_by, self.clerk)
        self.assertIsNotNone(first["at"])
        # A plain file on the same delivery is not its proof.
        self.assertEqual(self.keep(self.as_clerk, "lr.pdf", purpose=None).status_code, 201)

        # A better copy is added by a second clerk; the first stays under it.
        second_clerk = self.as_user(self.person("clerk-2", "Warehouse Staff"))
        second = self.keep(second_clerk, "pod-photo.jpg", JPEG).json()
        self.assertEqual([(row["name"], row["size"], row["by"], row["current"])
                          for row in self.proofs(self.as_clerk).json()],
                         [("pod-photo.jpg", 23, "clerk-2", True), ("pod.pdf", 27, "clerk", False)])
        everything = {row["name"]: (row["purpose"], row["current"])
                      for row in self.as_clerk.get(FILES, self.where).json()}
        self.assertEqual(everything, {"pod-photo.jpg": (PROOF_OF_DELIVERY, True), "pod.pdf": (PROOF_OF_DELIVERY, False),
                                      "lr.pdf": ("", False)})
        history = self.as_clerk.get("/api/core/history/", self.where).json()
        self.assertIn(("Attached", "Proof of delivery: pod-photo.jpg"),
                      [(row["label"], row["summary"]) for row in history])

        # Removing: not another clerk's; one's own; and anyone's by a manager who may remove files.
        self.assertEqual(self.as_clerk.delete(f"{FILES}{second['id']}/").status_code, 403)
        self.assertEqual(second_clerk.delete(f"{FILES}{second['id']}/").status_code, 204)
        self.assertEqual([(row["name"], row["current"]) for row in self.proofs(self.as_clerk).json()],
                         [("pod.pdf", True)])
        history = self.as_clerk.get("/api/core/history/", self.where).json()
        self.assertEqual((history[0]["label"], history[0]["summary"]),
                         ("Removed", "Proof of delivery: pod-photo.jpg"))
        manager = self.as_user(self.person("stores", "Stores Manager"))
        self.assertEqual(manager.delete(f"{FILES}{first['id']}/").status_code, 204)
        self.assertEqual(self.proofs(self.as_clerk).json(), [])
        self.assertEqual(list(Attachment.objects.values_list("name", flat=True)), ["lr.pdf"])
        # The receipt it was the paper of is untouched.
        self.delivery.refresh_from_db()
        self.assertEqual((str(self.delivery.received_on), self.delivery.receipt_reference), ("2026-03-05", "GRN-771"))

    def test_a_scan_as_a_picture_and_a_receipt_recorded_again_keeps_its_proof(self):
        self.receive()
        self.assertEqual(self.keep(self.as_clerk, "pod.PNG", PNG).json()["media_type"], "image/png")
        # The receipt's edit path: recorded again with the right GRN, the paper stays.
        again = self.as_clerk.post(f"/api/sales/deliveries/{self.delivery.pk}/received/", {
            "received_on": "2026-03-06", "received_by": "R. Sharma", "reference": "GRN-772"}, format="json")
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual([(row["name"], row["current"]) for row in self.proofs(self.as_clerk).json()],
                         [("pod.PNG", True)])
