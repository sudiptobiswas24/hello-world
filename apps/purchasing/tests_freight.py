"""
Transport on the dispatch, and the freight bill matched to it: the lorry
receipt and vehicle recorded after the truck left, carried onto the
e-way bill, and a delivery charged for once.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.sales.tests_base import SalesTestCase

from .freight import carry, unbilled_freight
from .models import Bill, BillLine


class FreightTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.carrier = Party.objects.create(code="T-1", name="Deccan Roadways")
        PartyRoleAssignment.objects.create(party=self.carrier, role=PartyRole.VENDOR)
        order = self.make_order("10", "100")
        self.delivery = self.ship(order, "10")
        self.delivery.record_transport(self.carrier, "LR-5521", datetime.date(2026, 3, 2), "mh 12 ab 1234")

    def freight_bill(self, vendor=None):
        bill = Bill.objects.create(vendor=vendor or self.carrier, bill_date=datetime.date(2026, 3, 31),
                                   payable_account=self.ar)
        BillLine.objects.create(bill=bill, description="Freight, March", quantity=Decimal("1"),
                                unit_price=Decimal("4500"), expense_account=self.cogs)
        return bill

    def test_recorded_after_shipping_and_listed_until_billed(self):
        self.delivery.refresh_from_db()
        self.assertEqual((self.delivery.posted, self.delivery.lr_number, self.delivery.vehicle_number),
                         (True, "LR-5521", "MH12AB1234"))
        self.assertEqual([row["number"] for row in unbilled_freight(self.carrier)], [self.delivery.number])
        carry(self.freight_bill(), self.delivery)
        self.assertEqual(unbilled_freight(self.carrier), [])

    def test_charged_for_once_and_only_by_its_carrier(self):
        carry(self.freight_bill(), self.delivery)
        with self.assertRaisesMessage(ValidationError, "charged for on"):
            carry(self.freight_bill(), self.delivery)
        other = Party.objects.create(code="T-2", name="Other Lines")
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.VENDOR)
        with self.assertRaisesMessage(ValidationError, "went with T-1 - Deccan Roadways"):
            carry(self.freight_bill(vendor=other), self.delivery)

    def test_over_the_api_as_dispatch_and_accounts(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)

        def as_(role):
            user = User.objects.create_user("api-" + role.replace(" ", "-").lower())
            user.groups.add(Group.objects.get(name=role))
            client = APIClient()
            client.force_authenticate(user)
            return client

        dispatch = as_("Warehouse Staff")
        recorded = dispatch.post(f"/api/sales/deliveries/{self.delivery.pk}/transport/", {
            "transporter": self.carrier.pk, "lr_number": "LR-9", "lr_date": "2026-03-03", "vehicle_number": "MH12CD5678"},
            format="json")
        self.assertEqual(recorded.status_code, 200, recorded.content)
        self.assertEqual((recorded.json()["lr_number"], recorded.json()["transporter_name"]), ("LR-9", "Deccan Roadways"))
        accounts = as_("AP Manager")
        [row] = accounts.get("/api/purchasing/purchasing-reports/unbilled-freight/").json()
        self.assertEqual(row["lr_number"], "LR-9")
        bill = self.freight_bill()
        carried = accounts.post(f"/api/purchasing/bills/{bill.pk}/carried/", {"delivery": self.delivery.pk}, format="json")
        self.assertEqual(carried.status_code, 200, carried.content)
        self.assertEqual([row["number"] for row in carried.json()["carried"]], [self.delivery.number])
        self.assertEqual(accounts.get("/api/purchasing/purchasing-reports/unbilled-freight/").json(), [])
        off = accounts.delete(f"/api/purchasing/bills/{bill.pk}/carried/?delivery={self.delivery.pk}")
        self.assertEqual(off.json()["carried"], [])

    def test_signed_for_after_it_arrived_and_listed_until_then(self):
        with self.assertRaisesMessage(ValidationError, "arrived after it left"):
            self.delivery.record_receipt(datetime.date(2026, 2, 1), "Stores, Acme")
        self.assertEqual(self.delivery.sales_order.deliveries.filter(received_on__isnull=True).count(), 1)
        self.delivery.record_receipt(datetime.date(2026, 3, 4), "Stores, Acme", "GRN-881")
        self.delivery.refresh_from_db()
        self.assertEqual((self.delivery.received_on, self.delivery.receipt_reference),
                         (datetime.date(2026, 3, 4), "GRN-881"))
