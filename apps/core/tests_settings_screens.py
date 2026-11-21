"""
The settings screens and the party page, server side, asked as the
people who use them.

The controller keeps the reference data every role reads; a rep reads
it and changes none of it. On a customer's page the rep keeps the
addresses and contacts, and ticking one primary unticks the one before
it; the GST registration is kept by the GST officer and read by the
rest. An address a posted invoice prints is archived and replaced, never
rewritten.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType, PartyTaxProfile
from apps.sales.models import Invoice, InvoiceLine
from apps.sales.tests_base import carries_every_customer

from .models import (
    Address, Contact, Currency, Party, PartyBankAccount, PartyRole, PartyRoleAssignment, UnitOfMeasure,
)


def as_(role):
    user = User.objects.create_user(role.replace(" ", "_").lower())
    user.groups.add(Group.objects.get(name=role))
    if role == "Sales Rep":
        carries_every_customer(user)
    client = APIClient()
    client.force_authenticate(user)
    return client


class SettingsTestCase(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.inr = Currency.objects.create(code="INR", name="Indian rupee", is_base=True)
        self.customer = Party.objects.create(code="SUN", name="Sunrise Cement")
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)


class ReferenceDataTests(SettingsTestCase):
    def test_the_controller_keeps_it_and_a_rep_only_reads_it(self):
        controller = as_("Controller")
        for url, body in (
            ("/api/core/countries/", {"code": "IN", "name": "India"}),
            ("/api/core/party-tags/", {"name": "Cement"}),
            ("/api/core/units-of-measure/", {"code": "bale", "name": "Bale", "category": "count"}),
        ):
            made = controller.post(url, body, format="json")
            self.assertEqual(made.status_code, 201, (url, made.content))
        rep = as_("Sales Rep")
        self.assertEqual(rep.get("/api/core/currencies/").status_code, 200)
        self.assertEqual(rep.post("/api/core/currencies/", {"code": "EUR", "name": "Euro"},
                                  format="json").status_code, 403)
        self.assertEqual(rep.post("/api/core/units-of-measure/", {"code": "m", "name": "Metre"},
                                  format="json").status_code, 403)

    def test_an_exchange_rate_is_named_and_found_by_currency_and_date(self):
        usd = Currency.objects.create(code="USD", name="US dollar")
        controller = as_("Controller")
        made = controller.post("/api/core/exchange-rates/", {"currency": usd.pk, "rate": "83.25",
                                                             "valid_from": "2026-06-01"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        [row] = controller.get("/api/core/exchange-rates/", {"currency": usd.pk, "from": "2026-06-01"}).json()
        self.assertEqual((row["currency_code"], row["rate"]), ("USD", "83.25000000"))
        self.assertEqual(controller.get("/api/core/exchange-rates/", {"to": "2026-05-31"}).json(), [])

    def test_a_unit_names_its_base(self):
        kg = UnitOfMeasure.objects.create(code="kg", name="Kilogram", category="weight")
        UnitOfMeasure.objects.create(code="t", name="Tonne", category="weight", base_unit=kg,
                                     conversion_factor=Decimal("1000"))
        [row] = as_("Controller").get("/api/core/units-of-measure/", {"search": "tonne"}).json()
        self.assertEqual(row["base_unit_code"], "kg")


class PartyPageTests(SettingsTestCase):
    def address(self, **values):
        return Address.objects.create(party=self.customer, line1="Plot 4, MIDC", city="Nagpur", **values)

    def test_the_rep_adds_an_address_and_a_contact_to_their_customer(self):
        rep = as_("Sales Rep")
        made = rep.post("/api/core/addresses/", {"party": self.customer.pk, "address_type": "shipping",
                                                 "line1": "Godown 2, Wadi", "city": "Nagpur"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        [row] = rep.get("/api/core/addresses/", {"party": self.customer.pk}).json()
        self.assertEqual(row["one_line"], "Godown 2, Wadi, Nagpur")
        contact = rep.post("/api/core/contacts/", {"party": self.customer.pk, "first_name": "Meera",
                                                   "job_title": "Purchase manager"}, format="json")
        self.assertEqual(contact.status_code, 201, contact.content)
        self.assertEqual([found["full_name"] for found in
                          rep.get("/api/core/contacts/", {"party": self.customer.pk}).json()], ["Meera"])

    def test_making_one_primary_makes_it_the_only_one(self):
        first = self.address(is_primary=True)
        second = self.address(line2="Gate 2")
        response = as_("Sales Rep").patch(f"/api/core/addresses/{second.pk}/", {"is_primary": True}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.is_primary, second.is_primary), (False, True))

    def test_the_same_for_a_contact(self):
        first = Contact.objects.create(party=self.customer, first_name="Meera", is_primary=True)
        second = Contact.objects.create(party=self.customer, first_name="Arun")
        response = as_("Sales Rep").patch(f"/api/core/contacts/{second.pk}/", {"is_primary": True}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        first.refresh_from_db()
        self.assertFalse(first.is_primary)

    def test_and_for_a_bank_account_made_primary_as_it_is_added(self):
        first = PartyBankAccount.objects.create(party=self.customer, account_name="Current",
                                                account_number="001", is_primary=True)
        second = PartyBankAccount.objects.create(party=self.customer, account_name="Cash credit",
                                                 account_number="002", is_primary=True)
        first.refresh_from_db()
        self.assertEqual((first.is_primary, second.is_primary), (False, True))

    def test_only_another_of_the_same_party_and_kind_gives_way(self):
        billing = self.address(is_primary=True)
        other = Party.objects.create(code="ULT", name="Ultratech")
        theirs = Address.objects.create(party=other, line1="Kalamna", city="Nagpur", is_primary=True)
        self.address(address_type="shipping", is_primary=True)
        billing.refresh_from_db()
        theirs.refresh_from_db()
        self.assertEqual((billing.is_primary, theirs.is_primary), (True, True))

    def test_an_address_belonging_to_no_party_takes_nothing_from_another(self):
        ours = Address.objects.create(line1="Plot 9, Butibori", city="Nagpur", is_primary=True)
        second = Address.objects.create(line1="Godown, Hingna", city="Nagpur", is_primary=True)
        ours.refresh_from_db()
        self.assertEqual((ours.is_primary, second.is_primary), (True, True))


class AnAddressAPostedInvoicePrintsTests(SettingsTestCase):
    def setUp(self):
        super().setUp()
        self.billed = Address.objects.create(party=self.customer, line1="Plot 4, MIDC", city="Nagpur",
                                             is_primary=True)
        receivable = Account.objects.create(code="1100", name="Receivable", account_type=AccountType.ASSET)
        revenue = Account.objects.create(code="4000", name="Sales", account_type=AccountType.INCOME)
        from apps.inventory.models import Item

        item = Item.objects.create(sku="BAG-1", name="Sack", uom=UnitOfMeasure.objects.create(code="pcs", name="Pieces"))
        self.invoice = Invoice.objects.create(customer=self.customer, invoice_date=datetime.date(2026, 6, 1),
                                              receivable_account=receivable, billing_address=self.billed)
        InvoiceLine.objects.create(invoice=self.invoice, item=item, quantity=Decimal("1"),
                                   unit_price=Decimal("100"), revenue_account=revenue)
        self.rep = as_("Sales Rep")

    def test_while_the_invoice_is_a_draft_it_may_change(self):
        response = self.rep.patch(f"/api/core/addresses/{self.billed.pk}/", {"line1": "Plot 5, MIDC"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)

    def test_once_posted_it_is_archived_and_replaced_not_rewritten(self):
        self.invoice.post()
        response = self.rep.patch(f"/api/core/addresses/{self.billed.pk}/", {"line1": "Plot 5, MIDC"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Add the new address and archive this one", response.content.decode())
        self.billed.refresh_from_db()
        self.assertEqual(self.billed.line1, "Plot 4, MIDC")
        # What prints nowhere may still change, and archiving is the way out.
        archived = self.rep.patch(f"/api/core/addresses/{self.billed.pk}/",
                                  {"label": "Old office", "is_active": False, "is_primary": False}, format="json")
        self.assertEqual(archived.status_code, 200, archived.content)


class GstRegistrationTests(SettingsTestCase):
    def test_the_gst_officer_keeps_it_and_gst_reads_it(self):
        officer = as_("GST Officer")
        made = officer.post("/api/accounting/party-tax-profiles/", {"party": self.customer.pk,
                                                                    "gstin": "27AABCD1234E1Z8"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        [row] = officer.get("/api/accounting/party-tax-profiles/", {"party": self.customer.pk}).json()
        self.assertEqual((row["gst_state"], row["gst_registration"]), ("27", "regular"))
        self.assertEqual(self.customer.tax_profile.gstin, "27AABCD1234E1Z8")

    def test_a_rep_reads_it_and_does_not_set_it(self):
        rep = as_("Sales Rep")
        self.assertEqual(rep.post("/api/accounting/party-tax-profiles/", {"party": self.customer.pk,
                                                                          "gstin": "27AABCD1234E1Z8"},
                                  format="json").status_code, 403)
        PartyTaxProfile.objects.create(party=self.customer, gstin="27AABCD1234E1Z8")
        self.assertEqual(len(rep.get("/api/accounting/party-tax-profiles/", {"party": self.customer.pk}).json()), 1)
