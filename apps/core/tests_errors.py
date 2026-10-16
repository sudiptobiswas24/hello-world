"""
A request that fails on the server leaves a reference the person is
shown (E-7K3QM), a row with who, where and the traceback for whoever
keeps the system, and a mail to DJANGO_ADMINS; what Django answers
itself (a 404) is not one. The Controller and the HR Admin read the
rows and say what was done; a bookkeeper may not.
"""

import re

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.management import call_command
from django.http import Http404
from django.test import Client, TestCase, override_settings
from django.urls import include, path
from rest_framework.test import APIClient

from .errors import ServerError

REF = re.compile(r"^E-[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{5}$")


def boom(request):
    raise RuntimeError("the loom caught fire")


def missing(request):
    raise Http404("no such thing")


urlpatterns = [
    path("api/boom/", boom),
    path("boom/", boom),
    path("api/missing/", missing),
    path("", include("config.urls")),
]


@override_settings(ROOT_URLCONF=__name__)
class CaptureTests(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.asha = User.objects.create_user("asha", password="pw-for-tests-77")
        self.asha.groups.add(Group.objects.get(name="Bookkeeper"))
        self.client = Client()
        self.client.force_login(self.asha)

    @override_settings(ADMINS=[("Ops", "ops@example.com")])
    def test_a_failure_is_recorded_told_and_mailed(self):
        answer = self.client.get("/api/boom/")
        self.assertEqual(answer.status_code, 500)
        body = answer.json()
        self.assertRegex(body["error_id"], REF)
        self.assertIn(body["error_id"], body["detail"])
        row = ServerError.objects.get(ref=body["error_id"])
        self.assertEqual((row.kind, row.message, row.path, row.method, row.user, row.resolved_at),
                         ("RuntimeError", "the loom caught fire", "/api/boom/", "GET", self.asha, None))
        self.assertIn("raise RuntimeError", row.traceback)
        self.assertEqual([message.to for message in mail.outbox], [["ops@example.com"]])
        self.assertIn(body["error_id"], mail.outbox[0].subject)
        self.assertIn("the loom caught fire", mail.outbox[0].body)

    def test_a_page_outside_the_api_says_the_reference_too(self):
        answer = self.client.get("/boom/")
        self.assertEqual((answer.status_code, answer["Content-Type"]), (500, "text/html"))
        ref = ServerError.objects.get().ref
        self.assertIn(ref, answer.content.decode())

    def test_what_django_answers_itself_is_not_an_error(self):
        self.assertEqual(self.client.get("/api/missing/").status_code, 404)
        self.assertEqual(self.client.get("/api/no-such-collection/").status_code, 404)
        self.assertEqual(ServerError.objects.count(), 0)

    @override_settings(DEBUG=True)
    def test_the_developer_keeps_the_technical_page_and_the_row_is_still_written(self):
        answer = Client(raise_request_exception=False).get("/api/boom/")
        self.assertEqual(answer.status_code, 500)
        self.assertEqual(ServerError.objects.get().kind, "RuntimeError")


class ProblemsApiTests(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.row = ServerError.objects.create(ref="E-TEST1", path="/api/sales/invoices/7/post_invoice/",
                                              method="POST", kind="ZeroDivisionError", message="division by zero",
                                              traceback="Traceback ...")

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        client.user = user
        return client

    def test_who_reads_them_and_marks_them_dealt_with(self):
        controller = self.as_("Controller")
        rows = controller.get("/api/core/errors/", {"resolved_at__isnull": "true"}).json()
        self.assertEqual([(row["ref"], row["kind"], row["traceback"]) for row in rows],
                         [("E-TEST1", "ZeroDivisionError", "Traceback ...")])
        done = controller.post(f"/api/core/errors/{self.row.pk}/resolve/", {"note": "Fixed in 2.3"}, format="json")
        self.assertEqual((done.status_code, done.json()["note"], done.json()["resolved_by_name"]),
                         (200, "Fixed in 2.3", "controller"), done.content)
        self.assertEqual(controller.get("/api/core/errors/", {"resolved_at__isnull": "true"}).json(), [])
        back = controller.post(f"/api/core/errors/{self.row.pk}/reopen/")
        self.assertEqual((back.status_code, back.json()["resolved_at"]), (200, None))
        self.assertEqual(self.as_("HR Admin").get("/api/core/errors/").status_code, 200)
        self.assertEqual(self.as_("Bookkeeper").get("/api/core/errors/").status_code, 403)
        # A fact of what happened: not written, not deleted.
        self.assertEqual(controller.patch(f"/api/core/errors/{self.row.pk}/", {"note": "x"}, format="json").status_code, 405)
        self.assertIn(controller.delete(f"/api/core/errors/{self.row.pk}/").status_code, (403, 405))
