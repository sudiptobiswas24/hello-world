"""
Manufacturing settings through the API, kept by the controller: there is
one record, and the work-in-progress account does not move while a run
holds material on it.
"""

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType

from .orders import ManufacturingSettings
from .tests_orders import TODAY, RunTestCase

URL = "/api/manufacturing/manufacturing-settings/"


class ManufacturingSettingsApiTests(RunTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.settings = ManufacturingSettings.objects.get()
        self.new_wip = Account.objects.create(code="1251", name="WIP, second", account_type=AccountType.ASSET)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_controller_reads_and_changes_the_one_record(self):
        controller = self.as_("Controller")
        [row] = controller.get(URL).json()
        self.assertEqual(row["wip_account"], self.wip.pk)
        changed = controller.patch(f"{URL}{row['id']}/", {"wip_account": self.new_wip.pk}, format="json")
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(controller.post(URL, {}, format="json").status_code, 403)
        self.assertEqual(ManufacturingSettings.objects.count(), 1)

    def test_a_second_record_is_refused(self):
        with self.assertRaisesMessage(Exception, "one set of manufacturing settings"):
            ManufacturingSettings.objects.create()

    def test_the_wip_account_stays_while_a_run_is_open(self):
        order = self.order()
        order.release(TODAY)
        refused = self.as_("Controller").patch(f"{URL}{self.settings.pk}/", {"wip_account": self.new_wip.pk},
                                               format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("close them before moving it", refused.content.decode())
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.wip_account, self.wip)

    def test_the_planner_does_not_keep_the_ledger_accounts(self):
        refused = self.as_("Production Planner").patch(f"{URL}{self.settings.pk}/",
                                                       {"wip_account": self.new_wip.pk}, format="json")
        self.assertEqual(refused.status_code, 403)
