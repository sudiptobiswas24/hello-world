"""
A keeper adds a column to customers (credit rating, one of a list), to
items (a shelf) and to bills (a gate pass number) from Settings, with no
developer and no migration. A value is refused unless it is in the
field's shape and under a defined key; required holds for what the
office types and not for the system's own saves; a field switched off
hides and keeps its values; its key and kind never change; a posted
invoice keeps its extras frozen like the rest of it. Nothing else reads
`extra`: the ledger, stock and every report are indifferent to it.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.inventory.models import Item
from apps.sales.models import Invoice, InvoiceLine
from apps.sales.tests_base import SalesTestCase, carries_every_customer

from .customfields import CustomField, check_extra
from .models import Party

FIELDS = "/api/core/custom-fields/"


class CustomFieldTestCase(SalesTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.rating = CustomField.objects.create(kind="core.party", key="credit_rating", label="Credit rating",
                                                 field_type="choice", choices="A\nB\nC", required=True)
        self.shelf = CustomField.objects.create(kind="inventory.item", key="shelf", label="Shelf")
        self.reviewed = CustomField.objects.create(kind="core.party", key="reviewed_on", label="Reviewed on",
                                                   field_type="date")
        self.limit_days = CustomField.objects.create(kind="core.party", key="limit_days", label="Limit, days",
                                                     field_type="number")
        self.vip = CustomField.objects.create(kind="core.party", key="vip", label="VIP", field_type="yes_no")

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client


class ShapeTests(CustomFieldTestCase):
    def test_values_are_kept_in_their_shape(self):
        kept = check_extra("core.party", {"credit_rating": "B", "reviewed_on": "2026-04-01", "limit_days": "45",
                                          "vip": "yes"})
        self.assertEqual(kept, {"credit_rating": "B", "reviewed_on": "2026-04-01", "limit_days": "45", "vip": True})
        self.assertEqual(check_extra("core.party", {"limit_days": "12.5000"})["limit_days"], "12.5")
        self.assertEqual(check_extra("core.party", {"limit_days": 30})["limit_days"], "30")
        self.assertEqual(check_extra("core.party", {"limit_days": ""}), {})  # emptied: gone

    def test_what_is_refused(self):
        for extra, message in (
            ({"colour": "red"}, "Customers and vendors have no field called colour."),
            ({"credit_rating": "D"}, "Credit rating is one of A, B, C."),
            ({"reviewed_on": "soon"}, "Reviewed on 'soon' is not a date"),
            ({"limit_days": "many"}, "Limit, days 'many' is not a number."),
            ({"limit_days": "1.23456"}, "Limit, days has more than four decimal places."),
            ({"vip": "maybe"}, "VIP is yes or no."),
            ([1, 2], "a set of named values"),
        ):
            with self.assertRaisesMessage(ValidationError, message):
                check_extra("core.party", extra)

    def test_required_holds_for_the_office_not_for_the_system(self):
        self.assertEqual(check_extra("core.party", {}), {})
        with self.assertRaisesMessage(ValidationError, "Credit rating is required."):
            check_extra("core.party", {}, from_office=True)
        # An import or a posting that never knew the field still saves the customer.
        self.customer.extra = {}
        self.customer.save()
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.extra, {})

    def test_switched_off_the_field_hides_and_keeps_its_values(self):
        self.customer.extra = {"credit_rating": "A"}
        self.customer.save()
        self.rating.is_active = False
        self.rating.save()
        self.customer.refresh_from_db()
        self.customer.save()  # still saves, the value untouched and no longer checked
        self.assertEqual(Party.objects.get(pk=self.customer.pk).extra, {"credit_rating": "A"})

    def test_the_key_and_the_kind_of_value_never_change(self):
        self.rating.key = "rating"
        with self.assertRaisesMessage(ValidationError, "cannot change"):
            self.rating.save()
        self.rating.refresh_from_db()
        self.rating.field_type = "text"
        with self.assertRaisesMessage(ValidationError, "cannot change once"):
            self.rating.save()
        with self.assertRaisesMessage(ValidationError, "starting with a letter"):
            CustomField.objects.create(kind="core.party", key="2nd", label="Second")
        with self.assertRaisesMessage(ValidationError, "needs the list"):
            CustomField.objects.create(kind="core.party", key="grade", label="Grade", field_type="choice")
        with self.assertRaisesMessage(ValidationError, "Custom fields go on"):
            CustomField.objects.create(kind="accounting.journalentry", key="x", label="X")

    def test_a_posted_invoice_keeps_its_extras_frozen(self):
        CustomField.objects.create(kind="sales.invoice", key="lr_number", label="LR number")
        invoice = Invoice.objects.create(customer=self.customer, invoice_date=datetime.date(2026, 3, 1),
                                         receivable_account=self.ar, currency=self.usd, extra={"lr_number": "LR-1"})
        InvoiceLine.objects.create(invoice=invoice, item=self.item, quantity=Decimal("1"), unit_price=Decimal("10"),
                                   revenue_account=self.revenue)
        invoice.post()
        invoice.extra = {"lr_number": "LR-2"}
        with self.assertRaisesMessage(ValidationError, "posted and immutable"):
            invoice.save()
        self.assertEqual(Invoice.objects.get(pk=invoice.pk).extra, {"lr_number": "LR-1"})


class OfficeTests(CustomFieldTestCase):
    def test_the_keeper_defines_a_field_and_a_rep_fills_it(self):
        controller = self.as_("Controller")
        made = controller.post(FIELDS, {"kind": "purchasing.bill", "key": "Gate_Pass", "label": "Gate pass",
                                        "field_type": "text", "hint": "From the security register"}, format="json")
        self.assertEqual((made.status_code, made.json()["key"], made.json()["kind_label"]),
                         (201, "gate_pass", "Bills and debit notes"), made.content)
        kinds = controller.get(f"{FIELDS}kinds/").json()
        self.assertEqual(kinds[0], {"kind": "core.party", "label": "Customers and vendors"})
        self.assertEqual([row["key"] for row in controller.get(FIELDS, {"kind": "core.party", "is_active": "true"}).json()],
                         ["credit_rating", "reviewed_on", "limit_days", "vip"])

        rep = self.as_("Sales Rep")
        carries_every_customer(rep.handler._force_user)
        self.assertEqual(rep.get(FIELDS, {"kind": "core.party"}).status_code, 200)  # read, to show the boxes
        self.assertEqual(rep.post(FIELDS, {"kind": "core.party", "key": "x", "label": "X"}, format="json").status_code, 403)
        saved = rep.patch(f"/api/core/parties/{self.customer.pk}/",
                          {"extra": {"credit_rating": "B", "limit_days": "30", "vip": True}}, format="json")
        self.assertEqual((saved.status_code, saved.json()["extra"]),
                         (200, {"credit_rating": "B", "limit_days": "30", "vip": True}), saved.content)
        refused = rep.patch(f"/api/core/parties/{self.customer.pk}/", {"extra": {"credit_rating": "Z", "colour": "red"}},
                            format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json(), {"extra.credit_rating": ["Credit rating is one of A, B, C."],
                                          "extra.colour": ["Customers and vendors have no field called colour."]})
        self.assertEqual(rep.patch(f"/api/core/parties/{self.customer.pk}/", {"extra": {}}, format="json").json(),
                         {"extra.credit_rating": ["Credit rating is required."]})

    def test_a_field_with_values_is_switched_off_not_deleted(self):
        controller = self.as_("Controller")
        Item.objects.filter(pk=self.item.pk).update(extra={"shelf": "A-12"})
        gone = controller.delete(f"{FIELDS}{self.shelf.pk}/")
        self.assertEqual((gone.status_code, "deactivate the field" in gone.json()[0]), (400, True), gone.content)
        self.assertEqual(controller.patch(f"{FIELDS}{self.shelf.pk}/", {"is_active": False}, format="json").status_code, 200)
        self.assertEqual(controller.delete(f"{FIELDS}{self.vip.pk}/").status_code, 204)  # nothing holds one
