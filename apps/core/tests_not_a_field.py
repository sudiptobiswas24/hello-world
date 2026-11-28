"""
A key a serializer has no field for is refused beside it, not dropped (O573).

DRF drops a key its serializer does not declare without a word: an account sent `hold_money` for `holds_money` was
made, 201, as no bank; a follow-up sent `asigned_to` was planned for whoever sent it. The shared bases
(apps.core.api.ModelSerializer and Serializer) refuse it now, beside the other fields' errors. A key the view
itself reads beside the serializer is named where it is read, and taken there only.
"""

from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers, viewsets
from rest_framework.decorators import action

from apps.accounting.models import Account
from apps.accounting.serializers import AccountSerializer
from apps.core.chatter import FollowUp
from apps.core.models import Party

from .tests_roles import RolesTestCase

ACCOUNTS = "/api/accounting/accounts/"
PARTIES = "/api/core/parties/"
FOLLOW_UPS = "/api/core/follow-ups/"


class AKeyNoFieldTakesIsRefusedTests(RolesTestCase):
    def test_a_typo_on_a_create_is_refused_beside_it_and_nothing_is_made(self):
        books = self.as_("Bookkeeper")
        before = Account.objects.count()
        sent = books.post(ACCOUNTS, {"code": "1999", "name": "Petty cash", "account_type": "asset",
                                     "hold_money": True}, format="json")
        self.assertEqual(sent.status_code, 400, sent.content)
        self.assertEqual(list(sent.json()), ["hold_money"])
        self.assertIn("`hold_money` is not a field of this record, and would be dropped without a word.",
                      sent.json()["hold_money"][0])
        self.assertIn("`holds_money`", sent.json()["hold_money"][0])  # it says what it takes
        self.assertEqual(Account.objects.count(), before)
        made = books.post(ACCOUNTS, {"code": "1999", "name": "Petty cash", "account_type": "asset",
                                     "holds_money": True}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.assertTrue(Account.objects.get(code="1999").holds_money)

    def test_a_typo_on_a_change_is_refused_and_the_record_stands(self):
        books = self.as_("Bookkeeper")
        account = Account.objects.create(code="1998", name="Float", account_type="asset")
        sent = books.patch(f"{ACCOUNTS}{account.pk}/", {"nme": "Till float"}, format="json")
        self.assertEqual(sent.status_code, 400, sent.content)
        self.assertEqual(list(sent.json()), ["nme"])
        account.refresh_from_db()
        self.assertEqual(account.name, "Float")

    def test_what_was_read_sent_back_is_still_taken(self):
        # O565 kept: an id, a name read through a relation, sent back as it was read, are ignored, not refused.
        books = self.as_("Bookkeeper")
        account = Account.objects.create(code="1997", name="Float", account_type="asset")
        sent = books.patch(f"{ACCOUNTS}{account.pk}/", {"id": account.pk + 100, "name": "Till float",
                                                        "parent_name": "anything"}, format="json")
        self.assertEqual(sent.status_code, 200, sent.content)
        account.refresh_from_db()
        self.assertEqual((account.pk, account.name), (sent.json()["id"], "Till float"))

    def test_a_new_partys_role_is_taken_on_its_create_only(self):
        rep = self.as_("AR Manager")
        made = rep.post(PARTIES, {"code": "C-901", "name": "Buyer", "role": "customer"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        party = Party.objects.get(code="C-901")
        self.assertEqual(list(party.role_assignments.values_list("role", flat=True)), ["customer"])
        # On a change nothing reads it: refused, rather than taken and dropped.
        changed = rep.patch(f"{PARTIES}{party.pk}/", {"role": "vendor"}, format="json")
        self.assertEqual(changed.status_code, 400, changed.content)
        self.assertEqual(list(changed.json()), ["role"])
        self.assertEqual(list(party.role_assignments.values_list("role", flat=True)), ["customer"])

    def test_a_follow_ups_input_refuses_a_typo_and_takes_the_record_it_is_about(self):
        books = self.as_("Bookkeeper")
        account = Account.objects.create(code="1996", name="Float", account_type="asset")
        about = {"model": "accounting.account", "id": account.pk, "summary": "Count the float",
                 "due_on": "2026-11-30"}
        sent = books.post(FOLLOW_UPS, {**about, "asigned_to": 1}, format="json")
        self.assertEqual(sent.status_code, 400, sent.content)
        self.assertEqual(list(sent.json()), ["asigned_to"])
        self.assertFalse(FollowUp.objects.exists())
        made = books.post(FOLLOW_UPS, about, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        row = FollowUp.objects.get()
        self.assertEqual((row.content_type, row.object_id), (ContentType.objects.get_for_model(Account), account.pk))
        # The record it is about is read on the create only: on a change it is refused, not moved and not dropped.
        changed = books.patch(f"{FOLLOW_UPS}{row.pk}/", {"model": "core.party", "sumary": "x"}, format="json")
        self.assertEqual(changed.status_code, 400, changed.content)
        self.assertEqual(sorted(changed.json()), ["model", "sumary"])
        row.refresh_from_db()
        self.assertEqual(row.summary, "Count the float")


class TheRuleOnTheSharedBasesTests(RolesTestCase):
    def test_a_nested_lines_typo_is_refused_under_its_list(self):
        from apps.core.api import ModelSerializer

        class Line(ModelSerializer):
            class Meta:
                model = Account
                fields = ["code", "name"]

        class Holder(ModelSerializer):
            new_lines = Line(many=True, required=False, write_only=True)

            class Meta:
                model = Account
                fields = ["code", "name", "account_type", "new_lines"]

        sent = Holder(data={"code": "1", "name": "A", "account_type": "asset",
                            "new_lines": [{"code": "2", "name": "B", "nmae": "C"}]})
        self.assertFalse(sent.is_valid())
        self.assertEqual(list(sent.errors), ["new_lines"])
        self.assertEqual(list(sent.errors["new_lines"][0]), ["nmae"])

    def test_a_key_named_beside_it_is_taken_and_only_there(self):
        from apps.core.api import Serializer

        class Asked(Serializer):
            item = serializers.CharField()

        self.assertFalse(Asked(data={"item": "x", "lamination_gsm": "20"}).is_valid())
        self.assertTrue(Asked(data={"item": "x", "lamination_gsm": "20"},
                              context={"body_beside": ("lamination_gsm",)}).is_valid())
        # The browsable API's own form token is never the record's.
        self.assertTrue(Asked(data={"item": "x", "csrfmiddlewaretoken": "t"}).is_valid())

    def test_a_plain_serializer_off_the_base_is_reported(self):
        from apps.core.api import Serializer
        from apps.core.management.commands.audit_invariants import Command

        class Dropping(serializers.Serializer):
            item = serializers.CharField()

        class Refusing(Serializer):
            item = serializers.CharField()

        found = Command().serializers_off_the_shared_base(["core"], [Dropping, Refusing])
        self.assertEqual([detail.split(" is written")[0] for _shape, detail in found],
                         ["apps.core.tests_not_a_field.Dropping"])
        self.assertIn("drops a key it has no field for", found[0][1])


class Planted(viewsets.ModelViewSet):
    queryset = Account.objects.all()
    serializer_class = AccountSerializer

    def perform_create(self, serializer):
        serializer.save(name=self.request.data.get("why") or "x")

    @action(detail=False, methods=["post"])
    def make(self, request):
        given = AccountSerializer(data=request.data)
        given.is_valid(raise_exception=True)
        return given.save(name=request.data.get("label"))


class Named(Planted):
    body_beside_its_serializer = {"create": ("why",)}

    @action(detail=False, methods=["post"])
    def make(self, request):
        given = AccountSerializer(data=request.data, context={"body_beside": ("label",)})
        given.is_valid(raise_exception=True)
        return given.save(name=request.data.get("label"))


class Stale(Named):
    body_beside_its_serializer = {"create": ("why",), "update": ("why",)}


class ReadBesideItsSerializerTests(RolesTestCase):
    def found(self, view):
        from apps.core.management.commands.audit_invariants import Command

        return [detail for _shape, detail in Command().body_read_beside_its_serializer(["core"], [view])]

    def test_a_key_read_beside_the_serializer_and_not_named_is_reported(self):
        self.assertEqual(self.found(Planted), [
            "core.Planted.perform_create reads why from the body its serializer is given, and its "
            "body_beside_its_serializer does not name it for create: the serializer refuses it whenever it is sent. "
            "Name it there.",
            "core.Planted.make reads label beside AccountSerializer, which it gives the same body and which refuses "
            "it whenever it is sent. Name it in that serializer's body_beside.",
        ])

    def test_named_where_it_is_read_it_is_not(self):
        self.assertEqual(self.found(Named), [])

    def test_a_name_its_action_does_not_read_is_reported_though_another_reads_it(self):
        # `why` is read on create; named for update too, a change would take it and drop it.
        self.assertEqual(self.found(Stale), [
            "core.Stale.body_beside_its_serializer names why for update, which its update does not read: a key "
            "taken and read by nothing is dropped without a word. Take it off.",
        ])

    def test_every_view_of_ours_names_what_it_reads_beside_its_serializer(self):
        from apps.core.management.commands.audit_invariants import OUR_APPS, Command

        self.assertEqual(Command().body_read_beside_its_serializer(list(OUR_APPS)), [])
