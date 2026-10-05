"""
Finding a document in a list: ?search=, ?<field>=, ?from=&to=, ?ordering=.

Asked as people in their roles, never as a superuser: what a list lets
someone narrow to is only worth testing as someone who may read it.
"""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.api import Search
from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.inventory.models import MovementType, StockMovement
from apps.sales.models import Invoice, InvoiceLine
from apps.sales.tests_base import SalesTestCase, carries_every_customer

INVOICES = "/api/sales/invoices/"


class ListSearchTestCase(SalesTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.bolt = Party.objects.create(code="C-2", name="Bolt Traders",
                                         default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.bolt, role=PartyRole.CUSTOMER)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        if role == "Sales Rep":
            carries_every_customer(user)
        client = APIClient()
        client.force_authenticate(user)
        return client

    def invoice(self, customer, day, price="10", post=True):
        invoice = Invoice.objects.create(customer=customer, invoice_date=day,
                                         receivable_account=self.ar, currency=self.usd)
        InvoiceLine.objects.create(invoice=invoice, item=self.item, quantity=Decimal("1"),
                                   unit_price=Decimal(price), revenue_account=self.revenue)
        if post:
            invoice.post()
        return invoice

    def numbers(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        return [row["id"] for row in response.json()]


class InvoiceListTests(ListSearchTestCase):
    def setUp(self):
        super().setUp()
        self.march = self.invoice(self.customer, datetime.date(2026, 3, 1))
        self.april = self.invoice(self.bolt, datetime.date(2026, 4, 1))
        self.may = self.invoice(self.customer, datetime.date(2026, 5, 1))
        self.draft = self.invoice(self.bolt, datetime.date(2026, 5, 2), post=False)
        self.client = self.as_("AR Manager")

    def test_search_by_the_customers_name_in_any_case(self):
        self.assertEqual(self.numbers(self.client.get(INVOICES, {"search": "bolt"})),
                         [self.draft.pk, self.april.pk])

    def test_search_by_part_of_a_number(self):
        self.assertEqual(self.numbers(self.client.get(INVOICES, {"search": self.april.number[-3:]})),
                         [self.april.pk])

    def test_a_word_too_common_to_list_finds_the_same(self):
        """Past Search.COMMON matches the list is narrowed in one condition
        instead of by the rows found; the answer must not change."""
        with patch.object(Search, "COMMON", 1):
            self.assertEqual(self.numbers(self.client.get(INVOICES, {"search": "bolt"})),
                             [self.draft.pk, self.april.pk])
            self.assertEqual(self.numbers(self.client.get(INVOICES, {"search": "bolt acme"})), [])

    def test_every_word_must_match_somewhere(self):
        self.assertEqual(self.numbers(self.client.get(INVOICES, {"search": "bolt acme"})), [])

    def test_by_customer_and_by_posted(self):
        self.assertEqual(self.numbers(self.client.get(INVOICES, {"customer": self.bolt.pk})),
                         [self.draft.pk, self.april.pk])
        self.assertEqual(self.numbers(self.client.get(INVOICES, {"posted": "false"})),
                         [self.draft.pk])

    def test_a_date_range_includes_both_ends(self):
        response = self.client.get(INVOICES, {"from": "2026-04-01", "to": "2026-05-01"})
        self.assertEqual(self.numbers(response), [self.may.pk, self.april.pk])
        self.assertEqual(response["X-Total-Count"], "2")

    def test_ordering_by_a_named_field_and_the_default_otherwise(self):
        self.assertEqual(self.numbers(self.client.get(INVOICES, {"ordering": "invoice_date"})),
                         [self.march.pk, self.april.pk, self.may.pk, self.draft.pk])
        newest_first = [self.draft.pk, self.may.pk, self.april.pk, self.march.pk]
        # The customer is a field the serializer shows but the list does
        # not offer to sort by; sorting by it would read Acme's first.
        self.assertEqual(self.numbers(self.client.get(INVOICES, {"ordering": "customer"})),
                         newest_first)
        self.assertEqual(self.numbers(self.client.get(INVOICES)), newest_first)

    def test_a_list_that_offers_no_sorting_ignores_a_sort(self):
        response = self.client.get("/api/sales/invoice-lines/", {"ordering": "-id"})
        ids = self.numbers(response)
        self.assertEqual(ids, sorted(ids))

    def test_a_value_the_field_cannot_hold_is_a_400_not_a_500(self):
        for params in ({"customer": "acme"}, {"posted": "maybe"}, {"from": "1 March"}):
            response = self.client.get(INVOICES, params)
            self.assertEqual(response.status_code, 400, params)

    def test_a_field_the_list_does_not_name_is_ignored(self):
        self.assertEqual(len(self.numbers(self.client.get(INVOICES, {"exchange_rate": "9"}))), 4)

    def test_a_list_with_nothing_to_search_ignores_the_search(self):
        response = self.client.get("/api/sales/invoice-lines/", {"search": "no such thing"})
        self.assertEqual(len(self.numbers(response)), 4)

    def test_searching_does_not_open_a_list_to_a_role_that_cannot_read_it(self):
        self.assertEqual(self.as_("Warehouse Staff").get(INVOICES, {"search": "bolt"}).status_code,
                         403)


class LedgerListTests(ListSearchTestCase):
    def test_an_accounts_posted_lines_in_a_period_newest_first(self):
        march = self.invoice(self.customer, datetime.date(2026, 3, 1), price="10")
        april = self.invoice(self.customer, datetime.date(2026, 4, 1), price="20")
        self.invoice(self.customer, datetime.date(2026, 5, 1), price="40")
        self.invoice(self.bolt, datetime.date(2026, 4, 2), price="80", post=False)
        response = self.as_("Controller").get("/api/accounting/journal-lines/", {
            "account": self.revenue.pk, "entry__posted": "true",
            "from": "2026-03-01", "to": "2026-04-30"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([(row["entry"], row["credit"]) for row in response.json()], [
            (april.journal_entry_id, "20.00"), (march.journal_entry_id, "10.00")])
        self.assertEqual(self.as_("Sales Rep").get("/api/accounting/journal-lines/").status_code,
                         403)


class PartyAndStockListTests(ListSearchTestCase):
    def test_parties_by_role(self):
        vendor = Party.objects.create(code="V-9", name="Granule Co")
        PartyRoleAssignment.objects.create(party=vendor, role=PartyRole.VENDOR)
        client = self.as_("AR Manager")
        response = client.get("/api/core/parties/", {"role_assignments__role": "vendor"})
        self.assertEqual(self.numbers(response), [vendor.pk])

    def test_a_movement_falls_on_the_plants_day(self):
        """20:00 in Greenwich on 1 March is 01:30 on 2 March in Kolkata,
        and a store keeper asking for 2 March means the plant's day."""
        late = StockMovement.objects.create(
            item=self.item, warehouse=self.warehouse, movement_type=MovementType.RECEIPT,
            uom=self.uom, quantity=Decimal("1"), unit_cost=Decimal("4"),
            occurred_at=datetime.datetime(2026, 3, 1, 20, 0, tzinfo=datetime.timezone.utc))
        client = self.as_("Warehouse Staff")
        with timezone.override("Asia/Kolkata"):
            response = client.get("/api/inventory/stock-movements/",
                                  {"from": "2026-03-02", "to": "2026-03-02"})
        self.assertEqual(self.numbers(response), [late.pk])
