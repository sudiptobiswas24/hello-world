"""A delivery names the weighbridge slip of the truck it left on (L17), as the stores does it."""

from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.inventory.gate import GateVisit

from .tests_base import SalesTestCase


class DeliveryWeighingTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("stores")
        user.groups.add(Group.objects.get(name="Warehouse Staff"))
        self.stores = APIClient()
        self.stores.force_authenticate(user)
        self.typist = User.objects.create_user("typist")
        self.typist.user_permissions.add(Permission.objects.get(codename="type_weight"))

    def weighing(self, purpose="outward_delivery", tare="9000", gross="9400", vehicle="MH12AB1234"):
        visit = GateVisit.objects.create(vehicle_number=vehicle, driver_name="Ramesh", purpose=purpose)
        weighing = visit.start_weighing("WB-1")
        weighing.record("tare", "typed", tare, reason="power", by=self.typist)
        if gross:
            weighing.record("gross", "typed", gross, reason="power", by=self.typist)
        return weighing

    def weigh(self, delivery, weighing):
        return self.stores.post(f"/api/sales/deliveries/{delivery.pk}/weighed/",
                                {"weighing": weighing.pk if weighing else None}, format="json")

    def test_a_posted_delivery_names_its_outward_weighing_and_can_be_put_right(self):
        delivery = self.ship(self.make_order(), "10")
        self.assertEqual(self.weigh(delivery, self.weighing(purpose="inward_order")).status_code, 400,
                         "a lorry that brought goods in")
        self.assertEqual(self.weigh(delivery, self.weighing(gross=None, vehicle="MH12AB2222")).status_code, 400,
                         "half weighed")
        weighing = self.weighing(vehicle="MH12AB3333")
        named = self.weigh(delivery, weighing)
        self.assertEqual((named.status_code, named.data["weighing_number"]), (200, weighing.number), named.data)
        # Named, the weighing stands: the visit is not cancelled under it nor the weighing replaced.
        visit = GateVisit.objects.get(pk=weighing.visit_id)
        refused = self.stores.post(f"/api/inventory/gate-visits/{visit.pk}/cancel/", {"reason": "x"}, format="json")
        self.assertEqual(refused.status_code, 403, "the stores does not cancel the gate's visits")
        from django.core.exceptions import ValidationError

        with self.assertRaisesMessage(ValidationError, "named by"):
            visit.cancel("entered twice")
        # Taken off again (the wrong truck), and the visit can then be put right.
        self.assertEqual(self.weigh(delivery, None).data["weighing"], None)
        visit.cancel("entered twice")
