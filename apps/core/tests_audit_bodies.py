"""
A body read by hand names every key it takes (O574, O577). A create or action that reads request.data itself
read the keys it knew and dropped the rest without a word; apps.core.api.takes_only refuses one beside it, and
audit_invariants reports a create that reads its body by hand without it, an action that does (O579), and a
list short of a key the action or a helper it hands the body to reads.
"""

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient


def _role_of(request):
    """A helper handed the request, reading its body: as UserViewSet._role and the assets' _date do."""
    return request.data.get("role")


class AnActionReadingItsBodyByHandIsReportedTests(TestCase):
    """audit_invariants actions_reading_the_body_by_hand (O579), the creates' mirror over a viewset's actions."""

    @staticmethod
    def planted(body):
        from django.db import transaction
        from rest_framework import viewsets
        from rest_framework.decorators import action
        from rest_framework.response import Response

        from apps.core.api import takes_only  # noqa: F401  (named in the planted sources below)

        if body == "by hand":
            class Planted(viewsets.ViewSet):
                @action(detail=True, methods=["post"])
                def settle(self, request, pk=None):
                    return Response({"amount": request.data.get("amount")})
        elif body == "by hand, held":
            class Planted(viewsets.ViewSet):
                @action(detail=True, methods=["post"])
                @transaction.atomic
                def settle(self, request, pk=None):
                    return Response({"amount": request.data.get("amount")})
        elif body == "through a helper":
            class Planted(viewsets.ViewSet):
                @action(detail=True, methods=["post"])
                def settle(self, request, pk=None):
                    return Response({"role": _role_of(request)})
        elif body == "named":
            class Planted(viewsets.ViewSet):
                @action(detail=True, methods=["post"])
                def settle(self, request, pk=None):
                    takes_only(request.data, "amount")
                    return Response({"amount": request.data.get("amount")})
        else:
            class Planted(viewsets.ViewSet):
                @action(detail=True, methods=["get"])
                def settle(self, request, pk=None):
                    return Response({"amount": request.data.get("amount")})
        Planted.__module__ = "apps.purchasing.views"
        return Planted

    def found(self, view):
        from apps.core.management.commands.audit_invariants import Command

        return Command().actions_reading_the_body_by_hand(["purchasing"], [view])

    def test_a_planted_action_reading_its_body_by_hand_is_reported(self):
        # Through a helper handed the request: granting a role, placing an asset in service and five more
        # read their body only so, and the first check, which looked for request.data in the action, missed them.
        for body in ("by hand", "by hand, held", "through a helper"):
            with self.subTest(body):
                [(shape, detail)] = self.found(self.planted(body))
                self.assertEqual(shape, "action reading its body by hand")
                self.assertIn("purchasing.Planted.settle", detail)

    def test_one_naming_its_keys_or_answering_a_get_is_not(self):
        self.assertEqual(self.found(self.planted("named")), [])
        self.assertEqual(self.found(self.planted("a get")), [])

    def test_no_action_here_reads_its_body_by_hand(self):
        from apps.core.management.commands.audit_invariants import OUR_APPS, Command

        self.assertEqual(Command().actions_reading_the_body_by_hand(list(OUR_APPS)), [])


def _core_in_use(data):
    """A helper reading a key of the body the action hands it, as the station's does."""
    return data.get("core")


def _figure(data, name):
    """A helper reading the key it is told, as number() and money_amount() do."""
    return data.get(name)


class ATakesOnlyShortOfItsReadsIsReportedTests(TestCase):
    """
    audit_invariants takes_only_short_of_its_reads (O579): the station's four weighing actions read `core` in a
    helper and did not name it, so every roll the tablet weighed was refused, and only the suite saw it.
    """

    @staticmethod
    def planted(shape):
        from rest_framework import viewsets
        from rest_framework.decorators import action
        from rest_framework.response import Response

        from apps.core.api import takes_only  # noqa: F401  (named in the planted sources below)

        if shape == "a helper's key unnamed":
            class Planted(viewsets.ViewSet):
                @action(detail=True, methods=["post"])
                def weigh(self, request, pk=None):
                    takes_only(request.data, "gross_kg")
                    return Response({"core": _core_in_use(request.data), "kg": request.data.get("gross_kg")})
        elif shape == "a key handed in unnamed":
            class Planted(viewsets.ViewSet):
                @action(detail=True, methods=["post"])
                def weigh(self, request, pk=None):
                    takes_only(request.data, "gross_kg")
                    data = request.data
                    return Response({"kg": _figure(data, "net_kg")})
        elif shape == "a helper imported in the method, its key unnamed":
            class Planted(viewsets.ViewSet):
                @action(detail=True, methods=["post"])
                def weigh(self, request, pk=None):
                    from apps.core.tests_audit_bodies import _figure as read

                    takes_only(request.data, "gross_kg")
                    return Response({"memo": read(request.data, "memo")})
        elif shape == "two actions, one helper, both short":
            class Planted(viewsets.ViewSet):
                @action(detail=True, methods=["post"])
                def tare(self, request, pk=None):
                    takes_only(request.data, "gross_kg")
                    return Response({"core": _core_in_use(request.data)})

                @action(detail=True, methods=["post"])
                def weigh(self, request, pk=None):
                    takes_only(request.data, "gross_kg")
                    return Response({"core": _core_in_use(request.data)})
        elif shape == "a method's key unnamed":
            class Planted(viewsets.ViewSet):
                def _note(self, body):
                    return body["note"]

                @action(detail=True, methods=["post"])
                def weigh(self, request, pk=None):
                    takes_only(request.data, "gross_kg")
                    return Response({"note": self._note(request.data)})
        else:
            class Planted(viewsets.ViewSet):
                @action(detail=True, methods=["post"])
                def weigh(self, request, pk=None):
                    takes_only(request.data, "core", "gross_kg", "net_kg")
                    return Response({"core": _core_in_use(request.data), "kg": _figure(request.data, "net_kg")})
        Planted.__module__ = "apps.manufacturing.station_views"
        return Planted

    def found(self, view):
        from apps.core.management.commands.audit_invariants import Command

        return Command().takes_only_short_of_its_reads(["manufacturing"], [view])

    def test_a_key_read_past_the_list_is_reported_wherever_it_is_read(self):
        for shape, key in (("a helper's key unnamed", "core"), ("a key handed in unnamed", "net_kg"),
                           ("a helper imported in the method, its key unnamed", "memo"),
                           ("a method's key unnamed", "note")):
            with self.subTest(shape):
                [(kind, detail)] = self.found(self.planted(shape))
                self.assertEqual(kind, "takes_only short of its reads")
                self.assertIn("manufacturing.Planted.weigh reads " + key, detail)

    def test_two_actions_reaching_one_helper_are_each_reported(self):
        # What a helper reads is kept for the next action that reaches it: a set of helpers seen once stopped
        # recursion, and every action after the first that reached a shared helper read nothing from it.
        found = self.found(self.planted("two actions, one helper, both short"))
        self.assertEqual([detail.split(" reads ")[0] for _, detail in found],
                         ["manufacturing.Planted.tare", "manufacturing.Planted.weigh"])

    def test_a_list_naming_every_key_read_is_not(self):
        self.assertEqual(self.found(self.planted("every key named")), [])

    def test_no_action_here_reads_a_key_its_list_does_not_name(self):
        from apps.core.management.commands.audit_invariants import OUR_APPS, Command

        self.assertEqual(Command().takes_only_short_of_its_reads(list(OUR_APPS)), [])


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


class EveryActionRefusesAKeyItDoesNotTakeTests(TestCase):
    """
    O579: about 250 actions read request.data by hand and dropped a key they did not read without a word. One
    in each module, asked by a login in the role that does it, refuses one beside it now. The down payment read
    `amount` or `percent` through a loop, which the first list of its keys missed; it is here so it stays taken.
    Two of the suite's own calls sent a key nothing read: a work order's close was sent `date` for `on_date`, and
    a pay run's void a `reason`.
    """

    ROUTES = (
        ("AR Manager", "/api/accounting/payments/0/void/", "memo"),
        ("Bookkeeper", "/api/accounting/recurring-journals/0/generate/", "on_date"),
        ("AR Manager", "/api/sales/sales-orders/0/down_payment/", "percent"),
        ("Controller", "/api/sales/customer-tds/0/confirm/", "certificate"),
        ("AP Manager", "/api/purchasing/bills/0/debit_old_supply/", "lines"),
        ("Quality Inspector", "/api/purchasing/goods-receipts/0/accept/", "quantities"),
        ("AR Manager", "/api/manufacturing/complaints/0/settle/", "net"),
        ("Production Planner", "/api/planning/planned-orders/0/firm/", "requested_by"),
        ("Controller", "/api/hr/pay-runs/0/void/", "on_date"),
        # Three of the seven that read their body only through a helper handed the request.
        ("HR Admin", "/api/core/users/0/grant/", "role"),
        ("Line Manager", "/api/hr/leave-requests/0/approve/", "decided_by"),
        ("Controller", "/api/assets/assets/0/place-in-service/", "on_date"),
    )

    def test_each_refuses_a_key_it_does_not_take_beside_it(self):
        EveryBodyReadByHandRefusesAKeyItDoesNotTakeTests.ask(self, self.ROUTES)


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
        self.ask(self, self.ROUTES)

    @staticmethod
    def ask(case, routes):
        call_command("setup_roles", verbosity=0)
        clients = {}
        for role, url, one_it_takes in routes:
            if role not in clients:
                user = User.objects.create_user(role.replace(" ", "_").lower())
                user.groups.add(Group.objects.get(name=role))
                clients[role] = APIClient()
                clients[role].force_authenticate(user)
            with case.subTest(url=url, role=role):
                answer = clients[role].post(url, {"nothing_reads_this": "1"}, format="json")
                case.assertEqual((answer.status_code, list(answer.json())), (400, ["nothing_reads_this"]),
                                 answer.content)
                said = answer.json()["nothing_reads_this"][0]
                case.assertTrue(said.startswith("This does not take `nothing_reads_this`"), said)
                case.assertIn(f"`{one_it_takes}`", said)
