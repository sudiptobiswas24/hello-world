"""
A body read by hand names every key it takes (O574, O577). A create or action that reads request.data itself
read the keys it knew and dropped the rest without a word; apps.core.api.takes_only refuses one beside it, and
audit_invariants reports a create that reads its body by hand without it.
"""

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient


class ACreateReadingItsBodyByHandIsReportedTests(TestCase):
    """audit_invariants creates_reading_the_body_by_hand, against planted viewsets and against the routes."""

    @staticmethod
    def planted(body):
        from rest_framework import viewsets
        from rest_framework.response import Response

        from apps.core.api import takes_only  # noqa: F401  (named in the planted sources below)

        if body == "by hand":
            class Planted(viewsets.ViewSet):
                def create(self, request):
                    return Response({"bill": request.data.get("bill")}, status=201)
        elif body == "named":
            class Planted(viewsets.ViewSet):
                def create(self, request):
                    takes_only(request.data, "bill")
                    return Response({"bill": request.data.get("bill")}, status=201)
        else:
            class Planted(viewsets.GenericViewSet):
                def create(self, request):
                    serializer = self.get_serializer(data=request.data)
                    serializer.is_valid(raise_exception=True)
                    return Response(serializer.data, status=201)
        Planted.__module__ = "apps.purchasing.views"
        return Planted

    def found(self, view):
        from apps.core.management.commands.audit_invariants import Command

        return Command().creates_reading_the_body_by_hand(["purchasing"], [view])

    def test_a_planted_create_reading_its_body_by_hand_is_reported(self):
        [(shape, detail)] = self.found(self.planted("by hand"))
        self.assertEqual(shape, "create reading its body by hand")
        self.assertIn("purchasing.Planted.create", detail)

    def test_one_naming_its_keys_or_reading_through_a_serializer_is_not(self):
        self.assertEqual(self.found(self.planted("named")), [])
        self.assertEqual(self.found(self.planted("serializer")), [])

    def test_no_create_here_reads_its_body_by_hand(self):
        from apps.core.management.commands.audit_invariants import OUR_APPS, Command

        self.assertEqual(Command().creates_reading_the_body_by_hand(list(OUR_APPS)), [])


class EveryBodyReadByHandRefusesAKeyItDoesNotTakeTests(TestCase):
    """
    O577: the five creates O574 left (a note, a file, a new customer or vendor, a price variation bill, a BOM
    change order, a scale reading) and the record, cancel and void actions of O574's six viewsets, each asked
    by a login in the role that does it, with a key it does not take. takes_only runs before anything is looked
    up, so the refusal stands beside the key whatever else was sent.
    """

    ROUTES = (
        ("AR Manager", "/api/core/notes/", "body"),
        ("AR Manager", "/api/core/attachments/", "purpose"),
        ("AR Manager", "/api/sales/customers/", "terms"),
        ("Purchasing Clerk", "/api/purchasing/vendors/", "terms"),
        ("AR Manager", "/api/sales/price-variation-bills/", "invoice_date"),
        ("Process Engineer", "/api/manufacturing/bom-change-orders/", "reason"),
        ("Process Engineer", "/api/manufacturing/bom-change-orders/0/apply/", "note"),
        ("Process Engineer", "/api/manufacturing/bom-change-orders/0/reject/", "note"),
        ("Scale bridge", "/api/manufacturing/scale-readings/", "key"),
        ("GST Officer", "/api/gst/e-invoices/0/record/", "signed_qr"),
        ("GST Officer", "/api/gst/e-invoices/0/cancel/", "remarks"),
        ("AR Manager", "/api/gst/e-invoices/0/record_cancellation/", "cancelled_at"),
        ("GST Officer", "/api/gst/eway-bills/0/record/", "valid_until"),
        ("GST Officer", "/api/gst/eway-bills/0/cancel/", "remarks"),
        ("GST Officer", "/api/gst/eway-bills/record-where-it-went/", "port_address"),
        ("GST Officer", "/api/gst/eway-bills/withdraw-where-it-went/", "invoice"),
        ("GST Officer", "/api/gst/self-invoices/0/cancel/", "date"),
        ("AR Manager", "/api/manufacturing/cost-sheets/0/quote/", "taxes"),
        ("Quality Manager", "/api/manufacturing/test-certificates/0/void/", "reason"),
    )

    def test_each_refuses_a_key_it_does_not_take_beside_it(self):
        call_command("setup_roles", verbosity=0)
        clients = {}
        for role, url, one_it_takes in self.ROUTES:
            if role not in clients:
                user = User.objects.create_user(role.replace(" ", "_").lower())
                user.groups.add(Group.objects.get(name=role))
                clients[role] = APIClient()
                clients[role].force_authenticate(user)
            with self.subTest(url=url, role=role):
                answer = clients[role].post(url, {"nothing_reads_this": "1"}, format="json")
                self.assertEqual((answer.status_code, list(answer.json())), (400, ["nothing_reads_this"]),
                                 answer.content)
                said = answer.json()["nothing_reads_this"][0]
                self.assertTrue(said.startswith("This does not take `nothing_reads_this`"), said)
                self.assertIn(f"`{one_it_takes}`", said)
