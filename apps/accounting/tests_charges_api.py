"""Charge types through the API: the controller keeps them, a sales rep reads them to add a line."""

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Account, AccountType, ChargeType, Tax

URL = "/api/accounting/charge-types/"


class ChargeTypeApiTests(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.recharged = Account.objects.create(code="4300", name="Freight recharged", account_type=AccountType.INCOME)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_controller_adds_freight_and_the_rep_reads_it(self):
        made = self.as_("Controller").post(URL, {"code": "FRT", "name": "Freight", "hsn_code": "9965",
                                                 "revenue_account": self.recharged.pk, "taxes": []}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        rep = self.as_("Sales Rep")
        [row] = rep.get(URL, {"is_active": "true"}).json()
        self.assertEqual((row["code"], row["revenue_account"]), ("FRT", self.recharged.pk))
        refused = rep.patch(f"{URL}{row['id']}/", {"name": "Free"}, format="json")
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(ChargeType.objects.get().name, "Freight")

    def test_the_taxes_it_carries_are_kept_one_by_one(self):
        charge = ChargeType.objects.create(code="FRT", name="Freight")
        collected = Account.objects.create(code="2300", name="GST payable", account_type=AccountType.LIABILITY)
        gst = Tax.objects.create(code="GST18", name="GST 18%", rate=18, collected_account=collected,
                                 paid_account=collected)
        controller = self.as_("Controller")
        url = f"{URL}{charge.pk}/taxes/"
        added = controller.post(url, {"tax": gst.pk}, format="json")
        self.assertEqual(added.status_code, 200, added.content)
        self.assertEqual([row["id"] for row in added.json()["tax_rows"]], [gst.pk])
        self.assertEqual(controller.post(url, {"tax": gst.pk}, format="json").status_code, 400)
        self.assertEqual(self.as_("Sales Rep").delete(f"{url}?tax={gst.pk}").status_code, 403)
        taken = controller.delete(f"{url}?tax={gst.pk}")
        self.assertEqual(taken.json()["tax_rows"], [])
