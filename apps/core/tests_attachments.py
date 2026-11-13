"""
Files kept with a record: added and read under the record's own view
permission, served through the API and never by URL, removed by whoever
added one or may remove any; the wrong kind or size refused in words.
"""

import tempfile

from django.contrib.auth.models import Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .attachments import MAX_BYTES, Attachment
from .models import Party

MEDIA = tempfile.mkdtemp(prefix="erp-attachments-")


def login(username, *codenames):
    user = User.objects.create_user(username)
    for codename in codenames:
        user.user_permissions.add(Permission.objects.get(content_type__app_label="core", codename=codename))
    client = APIClient()
    client.force_authenticate(user)
    return client


@override_settings(MEDIA_ROOT=MEDIA)
class AttachmentTests(TestCase):
    def setUp(self):
        self.party = Party.objects.create(code="V-1", name="Granule Traders")
        self.where = {"model": "core.party", "id": self.party.pk}

    def pdf(self, name="po.pdf", body=b"%PDF-1.4 the vendor's order copy"):
        return SimpleUploadedFile(name, body, content_type="application/pdf")

    def test_attached_listed_opened_and_in_the_history(self):
        clerk = login("clerk", "view_party", "add_attachment")
        kept = clerk.post("/api/core/attachments/", {**self.where, "file": self.pdf()}, format="multipart")
        self.assertEqual(kept.status_code, 201, kept.content)
        self.assertEqual((kept.json()["name"], kept.json()["size"], kept.json()["mine"]), ("po.pdf", 32, True))
        [row] = clerk.get("/api/core/attachments/", self.where).json()
        opened = clerk.get(f"/api/core/attachments/{row['id']}/download/")
        self.assertEqual((opened.status_code, opened["Content-Type"]), (200, "application/pdf"))
        self.assertEqual(b"".join(opened.streaming_content), b"%PDF-1.4 the vendor's order copy")
        self.assertIn('filename="po.pdf"', opened["Content-Disposition"])
        history = clerk.get("/api/core/history/", self.where).json()
        self.assertEqual((history[0]["label"], history[0]["summary"]), ("Attached", "po.pdf"))
        self.assertTrue(Attachment.objects.get().file.name.startswith("attachments/core/party/"))

    def test_the_wrong_kind_an_empty_file_and_too_big_are_refused(self):
        from django.core.exceptions import ValidationError

        from .attachments import check_file

        clerk = login("clerk", "view_party", "add_attachment")
        refused = clerk.post("/api/core/attachments/", {**self.where, "file": SimpleUploadedFile("run.exe", b"MZ")},
                             format="multipart")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("not a kind of file that is kept", refused.json()["file"][0])
        self.assertEqual(clerk.post("/api/core/attachments/", {**self.where, "file": self.pdf(body=b"")},
                                    format="multipart").status_code, 400)
        self.assertEqual(clerk.post("/api/core/attachments/", self.where, format="multipart").status_code, 400)
        # The size rule, on the figure the server reads off the upload.
        self.assertEqual(check_file("big.pdf", MAX_BYTES), "application/pdf")
        with self.assertRaisesMessage(ValidationError, "up to 20 MB is kept"):
            check_file("big.pdf", MAX_BYTES + 1)
        self.assertEqual(Attachment.objects.count(), 0)

    def test_reading_the_record_is_what_reading_its_files_takes(self):
        login("clerk", "view_party", "add_attachment").post("/api/core/attachments/", {**self.where, "file": self.pdf()},
                                                           format="multipart")
        row = Attachment.objects.get()
        stranger = login("stranger", "add_attachment")
        self.assertEqual(stranger.get("/api/core/attachments/", self.where).status_code, 403)
        self.assertEqual(stranger.get(f"/api/core/attachments/{row.pk}/download/").status_code, 403)
        self.assertEqual(stranger.post("/api/core/attachments/", {**self.where, "file": self.pdf()},
                                       format="multipart").status_code, 403)
        reader = login("reader", "view_party")
        self.assertEqual(reader.get(f"/api/core/attachments/{row.pk}/download/").status_code, 200)
        self.assertEqual(reader.post("/api/core/attachments/", {**self.where, "file": self.pdf()},
                                     format="multipart").status_code, 403)
        self.assertEqual(reader.get("/api/core/attachments/", {"model": "core.party", "id": 999}).status_code, 404)

    def test_removed_by_whoever_attached_it_or_may_remove_any(self):
        clerk = login("clerk", "view_party", "add_attachment")
        mine = clerk.post("/api/core/attachments/", {**self.where, "file": self.pdf("mine.pdf")}, format="multipart").json()
        other = login("other", "view_party", "add_attachment")
        theirs = other.post("/api/core/attachments/", {**self.where, "file": self.pdf("theirs.pdf")}, format="multipart").json()
        self.assertEqual(clerk.delete(f"/api/core/attachments/{theirs['id']}/").status_code, 403)
        self.assertEqual(clerk.delete(f"/api/core/attachments/{mine['id']}/").status_code, 204)
        manager = login("manager", "view_party", "delete_attachment")
        path = Attachment.objects.get().file.path
        self.assertEqual(manager.delete(f"/api/core/attachments/{theirs['id']}/").status_code, 204)
        self.assertEqual(Attachment.objects.count(), 0)
        import os
        self.assertFalse(os.path.exists(path))
        history = manager.get("/api/core/history/", self.where).json()
        self.assertEqual([(row["label"], row["summary"]) for row in history],
                         [("Removed", "theirs.pdf"), ("Removed", "mine.pdf"), ("Attached", "theirs.pdf"), ("Attached", "mine.pdf"),
                          # The party's own making, logged by the model layer (apps/core/history.py).
                          ("Made", "")])


@override_settings(MEDIA_ROOT=MEDIA)
class AFileKeptForAPurposeTests(TestCase):
    """
    The facility's half of a purpose, with one registered here for a party
    (the module owning a record registers its own: sales, the proof of
    delivery, tested as its people in apps/sales/tests_proof_of_delivery.py).
    """

    def setUp(self):
        from . import attachments

        self.party = Party.objects.create(code="V-1", name="Granule Traders")
        self.where = {"model": "core.party", "id": self.party.pk, "purpose": "signed_terms"}
        self.asked = []
        attachments.register_purpose("signed_terms", label="Signed terms", model="core.Party",
                                     extensions=(".pdf", ".png"), check=lambda party, user: self.asked.append(party))
        self.addCleanup(attachments.PURPOSES.pop, "signed_terms")

    def test_a_purpose_takes_only_kinds_whose_content_is_read(self):
        from .attachments import PURPOSES, register_purpose

        with self.assertRaisesMessage(ValueError, "no rule here reads the content of .txt"):
            register_purpose("notes", label="Notes", model="core.party", extensions=(".txt",), check=None)
        self.assertNotIn("notes", PURPOSES)

    def test_kept_by_name_and_content_the_owners_rule_asked_and_the_latest_current(self):
        clerk = login("clerk", "view_party", "add_attachment")
        for name, body, words in (("terms.jpg", b"\xff\xd8\xff", "A signed terms is kept as .pdf, .png"),
                                  ("terms.png", b"%PDF-1.4", "is named .png and is not one inside")):
            refused = clerk.post("/api/core/attachments/", {**self.where, "file": SimpleUploadedFile(name, body)},
                                 format="multipart")
            self.assertEqual(refused.status_code, 400, refused.content)
            self.assertIn(words, refused.json()["file"][0])
        self.assertEqual(Attachment.objects.count(), 0)
        for name in ("terms.pdf", "terms-2.pdf"):
            kept = clerk.post("/api/core/attachments/", {**self.where, "file": SimpleUploadedFile(name, b"%PDF-1.4 x")},
                              format="multipart")
            self.assertEqual(kept.status_code, 201, kept.content)
        self.assertEqual(self.asked, [self.party] * 4)  # asked before the file is looked at, each time
        plain = clerk.post("/api/core/attachments/", {"model": "core.party", "id": self.party.pk,
                                                      "file": SimpleUploadedFile("po.txt", b"anything")},
                           format="multipart")
        self.assertEqual((plain.status_code, plain.json()["purpose"], plain.json()["current"]), (201, "", False))
        self.assertEqual([(row["name"], row["purpose_label"], row["current"])
                          for row in clerk.get("/api/core/attachments/", self.where).json()],
                         [("terms-2.pdf", "Signed terms", True), ("terms.pdf", "Signed terms", False)])
        self.assertEqual(len(clerk.get("/api/core/attachments/", {"model": "core.party", "id": self.party.pk}).json()), 3)
