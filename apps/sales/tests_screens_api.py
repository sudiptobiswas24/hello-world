"""
What the sales screens ask the server, asked as the people who use them.

Each endpoint here exists for a screen: Ship on an order, the "to ship"
and "still owed" lists, the names a list shows without a second call,
and a customer made in one step with its role. The refusals come first.
"""

import datetime
from decimal import Decimal

from django.test import override_settings

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.sales.models import Delivery, SalesOrder, SalesOrderLine
from apps.sales.tests_base import SalesTestCase, carries_every_customer

DAY = datetime.date(2026, 3, 5)


class ScreensTestCase(SalesTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user, _ = User.objects.get_or_create(username=role.replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        if role == "Sales Rep":
            carries_every_customer(user)
        client = APIClient()
        client.force_authenticate(user)
        return client

    def ship_order(self, order, **body):
        return self.as_("Warehouse Staff").post(
            f"/api/sales/sales-orders/{order.pk}/ship/", body, format="json")


class ShipRefusalTests(ScreensTestCase):
    def test_a_draft_order_is_not_shipped(self):
        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY, currency=self.usd)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("5"),
                                      unit_price=Decimal("10"), revenue_account=self.revenue)
        response = self.ship_order(order, warehouse=self.warehouse.pk)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("confirmed", str(response.json()))
        self.assertEqual(Delivery.objects.count(), 0)

    def test_a_line_from_no_warehouse_is_refused_by_the_field(self):
        order = self.make_order()
        response = self.ship_order(order)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("warehouse", response.json())
        self.assertEqual(Delivery.objects.count(), 0)

    def test_an_unknown_warehouse_is_not_found(self):
        order = self.make_order()
        response = self.ship_order(order, warehouse=999999)
        self.assertEqual(response.status_code, 404, response.content)
        self.assertEqual(Delivery.objects.count(), 0)

    def test_one_draft_waits_at_a_time(self):
        order = self.make_order()
        self.assertEqual(self.ship_order(order, warehouse=self.warehouse.pk).status_code, 201)
        response = self.ship_order(order, warehouse=self.warehouse.pk)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("already waiting", str(response.json()))
        self.assertEqual(order.deliveries.count(), 1)

    def test_nothing_left_is_said_so(self):
        order = self.make_order()
        self.ship(order, "10")
        response = self.ship_order(order, warehouse=self.warehouse.pk)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("Nothing is left", str(response.json()))

    def test_a_rep_who_may_not_ship_is_refused(self):
        order = self.make_order()
        response = self.as_("Sales Rep").post(
            f"/api/sales/sales-orders/{order.pk}/ship/", {"warehouse": self.warehouse.pk}, format="json")
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(Delivery.objects.count(), 0)


class DatesInWordsTests(ScreensTestCase):
    """A mistyped date is refused in a sentence beside nothing broken; it was
    a server error on every action that read one."""

    def test_to_date(self):
        from django.core.exceptions import ValidationError

        from apps.core.models import to_date

        self.assertEqual(to_date("2026-03-31"), datetime.date(2026, 3, 31))
        self.assertIsNone(to_date(""))
        self.assertIsNone(to_date("  "))
        for typed in ["31/03/2026", "2026-02-30", "tomorrow"]:
            with self.subTest(typed=typed), self.assertRaisesMessage(ValidationError, "is not a date"):
                to_date(typed)

    def test_a_shipment_on_no_such_day(self):
        order = self.make_order()
        response = self.ship_order(order, warehouse=self.warehouse.pk, delivery_date="31/03/2026")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("is not a date", str(response.json()))
        self.assertEqual(Delivery.objects.count(), 0)

    def test_an_empty_date_box_means_today(self):
        from django.utils import timezone

        response = self.ship_order(self.make_order(), warehouse=self.warehouse.pk, delivery_date="")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["delivery_date"], timezone.localdate().isoformat())

    def test_an_invoice_on_no_such_day(self):
        from apps.core.models import Company

        company = Company.get()
        company.default_receivable_account = self.ar
        company.save()
        order = self.make_order()
        response = self.as_("AR Manager").post(f"/api/sales/sales-orders/{order.pk}/create_invoice/",
                                               {"invoice_date": "2026-02-30"}, format="json")
        # Read by the model's date field rather than to_date: Django's own
        # sentence, but a refusal either way, and no invoice.
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("invalid date", str(response.json()))
        self.assertFalse(order.invoices.exists())

    def test_a_list_filtered_from_no_such_day(self):
        response = self.as_("AR Manager").get("/api/sales/invoices/", {"from": "yesterday"})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("is not a date", str(response.json()))
        self.assertNotIn("['", str(response.json()))


class DatabaseRulesInWordsTests(ScreensTestCase):
    """A rule only the database holds (a check constraint no serializer
    runs) was a 500. It is a 400 beside the field it is about."""

    def test_a_negative_quantity_on_a_delivery_line(self):
        order = self.make_order()
        line = self.ship_order(order, warehouse=self.warehouse.pk).json()["lines"][0]
        response = self.as_("Warehouse Staff").patch(
            f"/api/sales/delivery-lines/{line['id']}/", {"quantity_shipped": "-2"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json(), {"quantity_shipped": ["Quantity shipped must be more than 0."]})
        self.assertEqual(order.deliveries.get().lines.get().quantity_shipped, Decimal("10"))


class DeletingWhatIsUsedTests(ScreensTestCase):
    def test_a_customer_with_orders_is_refused_in_words(self):
        from django.contrib.auth.models import Permission

        self.make_order()
        user = User.objects.create_user("tidier")
        # Not a rep: someone who reads parties sees every customer only
        # when told so, or they would see none (tests_reps).
        user.user_permissions.set(Permission.objects.filter(
            codename__in=["delete_party", "view_party", "view_every_customer"]))
        client = APIClient()
        client.force_authenticate(user)
        response = client.delete(f"/api/core/parties/{self.customer.pk}/")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("Still used by", str(response.json()))
        self.assertTrue(Party.objects.filter(pk=self.customer.pk).exists())


class ShipTests(ScreensTestCase):
    def test_drafts_what_is_owed_and_moves_nothing(self):
        order = self.make_order(quantity="10")
        on_hand = self.item.on_hand_at(self.warehouse)
        response = self.ship_order(order, warehouse=self.warehouse.pk, delivery_date="2026-03-06")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertFalse(body["posted"])
        self.assertEqual(body["delivery_date"], "2026-03-06")
        self.assertEqual(body["order_number"], order.number)
        self.assertEqual(body["customer_name"], self.customer.name)
        [line] = body["lines"]
        self.assertEqual(Decimal(line["quantity_shipped"]), Decimal("10"))
        self.assertEqual(line["warehouse"], self.warehouse.pk)
        self.assertTrue(line["description"])
        self.assertEqual(self.item.on_hand_at(self.warehouse), on_hand)

    def test_after_part_shipped_drafts_the_rest(self):
        order = self.make_order(quantity="10")
        self.ship(order, "4")
        response = self.ship_order(order, warehouse=self.warehouse.pk)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Decimal(response.json()["lines"][0]["quantity_shipped"]), Decimal("6"))

    def test_the_lines_own_warehouse_wins(self):
        from apps.inventory.models import Warehouse

        other = Warehouse.objects.create(code="WH2", name="Godown")
        order = self.make_order()
        order.lines.update(warehouse=other)
        response = self.ship_order(order, warehouse=self.warehouse.pk)
        self.assertEqual(response.json()["lines"][0]["warehouse"], other.pk)
        response = self.ship_order(self.make_order())  # none given, none named
        self.assertEqual(response.status_code, 400)

    def test_charges_and_closed_lines_are_not_drafted(self):
        from apps.accounting.models import ChargeType

        order = self.make_order(quantity="10")
        order.status = "draft"
        order.save()
        freight = ChargeType.objects.create(code="FRT", name="Freight", revenue_account=self.revenue)
        SalesOrderLine.objects.create(order=order, charge=freight, quantity=Decimal("1"),
                                      unit_price=Decimal("50"), revenue_account=self.revenue)
        closing = SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom,
                                                quantity=Decimal("3"), unit_price=Decimal("10"),
                                                revenue_account=self.revenue)
        order.confirm()
        closing.close_short("Customer cancelled the rest")
        response = self.ship_order(order, warehouse=self.warehouse.pk)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual([Decimal(line["quantity_shipped"]) for line in response.json()["lines"]],
                         [Decimal("10")])

    def test_an_unposted_return_is_not_a_shipment_waiting(self):
        order = self.make_order(quantity="10")
        delivery = self.ship(order, "10")
        Delivery.objects.create(sales_order=order, reverses=delivery, delivery_date=DAY)
        response = self.ship_order(order, warehouse=self.warehouse.pk)
        # Refused because all ten went, not because a draft is waiting.
        self.assertEqual(response.status_code, 400)
        self.assertIn("Nothing is left", str(response.json()))

    def test_what_came_back_is_owed_again(self):
        order = self.make_order(quantity="10")
        delivery = self.ship(order, "10")
        delivery.create_return(credit_invoices=False, quantities={delivery.lines.get(): Decimal("4")})
        response = self.ship_order(order, warehouse=self.warehouse.pk)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Decimal(response.json()["lines"][0]["quantity_shipped"]), Decimal("4"))

    def test_the_draft_posts(self):
        order = self.make_order(quantity="10")
        delivery_id = self.ship_order(order, warehouse=self.warehouse.pk).json()["id"]
        on_hand = self.item.on_hand_at(self.warehouse)
        response = self.as_("Warehouse Staff").post(f"/api/sales/deliveries/{delivery_id}/post_delivery/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.item.on_hand_at(self.warehouse), on_hand - Decimal("10"))
        self.assertEqual(order.lines.get().quantity_open(), Decimal("0"))


class ToShipTests(ScreensTestCase):
    def ids(self, **params):
        response = self.as_("Warehouse Staff").get("/api/sales/sales-orders/", {"to_ship": "true", **params})
        self.assertEqual(response.status_code, 200, response.content)
        return {row["id"] for row in response.json()}, int(response["X-Total-Count"])

    def test_only_confirmed_orders_with_goods_owed(self):
        owed = self.make_order(quantity="10")
        part = self.make_order(quantity="10")
        self.ship(part, "3")
        done = self.make_order(quantity="10")
        self.ship(done, "10")
        draft = SalesOrder.objects.create(customer=self.customer, order_date=DAY, currency=self.usd)
        SalesOrderLine.objects.create(order=draft, item=self.item, uom=self.uom, quantity=Decimal("5"),
                                      unit_price=Decimal("10"), revenue_account=self.revenue)
        ids, total = self.ids()
        self.assertEqual(ids, {owed.pk, part.pk})
        self.assertEqual(total, 2)

    def test_false_lists_every_order(self):
        self.make_order()
        done = self.make_order()
        self.ship(done, "10")
        response = self.as_("Warehouse Staff").get("/api/sales/sales-orders/", {"to_ship": "false"})
        self.assertEqual(len(response.json()), 2)

    def test_a_word_that_is_not_yes_or_no_is_refused(self):
        response = self.as_("Warehouse Staff").get("/api/sales/sales-orders/", {"to_ship": "maybe"})
        self.assertEqual(response.status_code, 400, response.content)


class StillOwedTests(ScreensTestCase):
    def ids(self, **params):
        response = self.as_("AR Manager").get("/api/sales/invoices/", {"open": "true", **params})
        self.assertEqual(response.status_code, 200, response.content)
        return {row["id"] for row in response.json()}

    def test_what_is_owed_and_nothing_else(self):
        unpaid = self.bill(self.make_order(quantity="10", price="100"))
        part = self.bill(self.make_order(quantity="10", price="100"))
        self.allocate(self.receipt("400"), part, "400")
        paid = self.bill(self.make_order(quantity="10", price="100"))
        self.allocate(self.receipt("1000"), paid, "1000")
        credited = self.bill(self.make_order(quantity="10", price="100"))
        note = credited.create_credit_note(memo="Wrong goods")
        draft = self.make_order(quantity="10", price="100").create_invoice(self.ar, invoice_date=DAY)

        self.assertEqual(self.ids(), {unpaid.pk, part.pk})
        self.assertNotIn(note.pk, self.ids())
        self.assertNotIn(draft.pk, self.ids())
        part.refresh_from_db()
        self.assertEqual(part.amount_due(), Decimal("600.00"))

    def test_a_written_off_invoice_is_owed_no_longer(self):
        invoice = self.bill(self.make_order(quantity="10", price="100"))
        invoice.write_off(reason="Customer gone")
        self.assertEqual(self.ids(), set())

    def test_a_void_payment_makes_it_owed_again(self):
        invoice = self.bill(self.make_order(quantity="10", price="100"))
        payment = self.receipt("1000")
        self.allocate(payment, invoice, "1000")
        self.assertEqual(self.ids(), set())
        response = self.as_("AR Manager").post(f"/api/accounting/payments/{payment.pk}/void/",
                                               {"memo": "Cheque bounced"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["voided"])
        self.assertEqual(self.ids(), {invoice.pk})

    def test_with_the_customer_filter(self):
        from apps.core.models import Party as P

        other = P.objects.create(code="C-2", name="Other", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.CUSTOMER)
        mine = self.bill(self.make_order())
        self.assertEqual(self.ids(customer=self.customer.pk), {mine.pk})
        self.assertEqual(self.ids(customer=other.pk), set())


class StillOwedAgreesWithAmountDueTests(ScreensTestCase):
    """
    still_owed() decides in SQL what amount_due() decides in Python. Every
    way an invoice is settled, alone and together, so the two cannot part
    without this failing.
    """

    def test_every_way_of_settling(self):
        from apps.sales.models import Invoice, still_owed

        def invoice():
            return self.bill(self.make_order(quantity="10", price="100"))  # 1,000.00

        owed, settled = {}, {}
        owed["unpaid"] = invoice()
        owed["part paid"] = invoice()
        self.allocate(self.receipt("400"), owed["part paid"], "400")
        settled["paid"] = invoice()
        self.allocate(self.receipt("1000"), settled["paid"], "1000")
        settled["paid less discount"] = invoice()
        self.allocate(self.receipt("980"), settled["paid less discount"], "980")
        settled["paid less discount"].apply_settlement_discount(force=True)
        settled["credited"] = invoice()
        settled["credited"].create_credit_note(memo="Wrong goods")
        # The credit only takes it to nothing: 600 paid, 1,000 credited.
        settled["part paid then credited"] = invoice()
        self.allocate(self.receipt("600"), settled["part paid then credited"], "600")
        settled["part paid then credited"].create_credit_note(memo="Returned")
        owed["part credited"] = invoice()
        owed["part credited"].create_credit_note(quantities={owed["part credited"].lines.get(): Decimal("3")})
        settled["written off"] = invoice()
        settled["written off"].write_off(reason="Gone")
        owed["part written off"] = invoice()
        owed["part written off"].write_off(amount=Decimal("200"), reason="Disputed")
        owed["paid by a bounced cheque"] = invoice()
        bounced = self.receipt("1000")
        self.allocate(bounced, owed["paid by a bounced cheque"], "1000")
        bounced.void(memo="Bounced")
        order = self.make_order(quantity="10", price="100")
        order.create_down_payment_invoice(self.ar, percent=30).post()
        owed["part met from a deposit"] = self.bill(order)
        order = self.make_order(quantity="10", price="100")
        order.create_down_payment_invoice(self.ar, percent=100).post()
        settled["met from a deposit"] = self.bill(order)
        order = self.make_order(quantity="10", price="100")
        order.create_down_payment_invoice(self.ar, percent=50).post()
        settled["deposit and cash"] = self.bill(order)
        self.allocate(self.receipt("500"), settled["deposit and cash"], "500")

        from apps.accounting.models import Account, AccountType, TdsSection

        goods = TdsSection.objects.create(
            code="194Q", name="Purchase of goods", rate_percent=Decimal("0.1"), no_pan_rate_percent=Decimal("5"),
            mode="excess", annual_threshold=Decimal("5000000"), receivable_account=Account.objects.create(
                code="1450", name="TDS receivable", account_type=AccountType.ASSET))
        settled["paid less tax withheld"] = invoice()
        settled["paid less tax withheld"].record_tds(goods, "10")
        self.allocate(self.receipt("990"), settled["paid less tax withheld"], "990")
        owed["tax withheld and reversed"] = invoice()
        reversed_later = owed["tax withheld and reversed"]
        withheld = reversed_later.record_tds(goods, "10")
        self.allocate(self.receipt("990"), reversed_later, "990")
        withheld.reverse()

        # Paid again less the discount once the first payment came back: the 20 is owed.
        lost = owed["discounted, returned, paid again less the discount"] = invoice()
        returned = self.receipt("980")
        self.allocate(returned, lost, "980")
        lost.apply_settlement_discount(force=True)
        returned.void(memo="Returned unpaid")
        self.allocate(self.receipt("980"), lost, "980")

        answer = set(still_owed(Invoice.objects.all()).values_list("pk", flat=True))
        for name, document in owed.items():
            self.assertIn(document.pk, answer, f"{name}: due {document.amount_due()}")
        for name, document in settled.items():
            self.assertNotIn(document.pk, answer, f"{name}: due {document.amount_due()}")
        # And over everything, the deposit invoices and notes included.
        everything = Invoice.objects.all()
        self.assertEqual(answer, {each.pk for each in everything
                                  if each.posted and not each.credits_id and each.amount_due() > 0})
        self.assertEqual(owed["part credited"].amount_due(), Decimal("700.00"))
        self.assertEqual(lost.amount_due(), Decimal("20.00"))
        self.assertEqual(owed["part met from a deposit"].amount_due(), Decimal("700.00"))


class NamesOnListsTests(ScreensTestCase):
    def test_a_receipt_names_its_payer_and_what_it_paid(self):
        invoice = self.bill(self.make_order())
        payment = self.receipt("250")
        self.allocate(payment, invoice, "250")
        client = self.as_("AR Manager")
        [row] = client.get("/api/accounting/payments/", {"direction": "receipt"}).json()
        self.assertEqual(row["party_name"], self.customer.name)
        self.assertFalse(row["voided"])
        [applied] = client.get("/api/sales/invoice-payments/", {"payment": payment.pk}).json()
        self.assertEqual(applied["invoice_number"], invoice.number)
        self.assertEqual(Decimal(applied["amount"]), Decimal("250"))
        self.assertEqual(client.get("/api/sales/invoice-payments/", {"invoice": invoice.pk + 1000}).json(), [])

    def test_a_line_added_with_only_an_item_is_called_by_the_item(self):
        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY, currency=self.usd)
        client = self.as_("Sales Rep")
        response = client.post("/api/sales/sales-order-lines/", {
            "order": order.pk, "item": self.item.pk, "uom": self.uom.pk, "quantity": "3"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["description"], "")
        self.assertEqual(response.json()["label"], "WDG-1 - Widget")
        [line] = client.get(f"/api/sales/sales-orders/{order.pk}/").json()["lines"]
        self.assertEqual(line["label"], "WDG-1 - Widget")

    def test_the_lists_do_not_ask_once_per_row(self):
        for _ in range(3):
            self.ship(self.make_order(quantity="10"), "2")
        client = self.as_("Warehouse Staff")
        self.queries_for(client, "/api/sales/deliveries/")  # warm the permission cache
        before = self.queries_for(client, "/api/sales/deliveries/")
        for _ in range(3):
            self.ship(self.make_order(quantity="10"), "2")
        self.assertEqual(self.queries_for(client, "/api/sales/deliveries/"), before)

    def queries_for(self, client, url):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(client.get(url).status_code, 200)
        return len(queries)


class CustomerWithItsRoleTests(ScreensTestCase):
    def create(self, as_="Sales Rep", **body):
        return self.as_(as_).post("/api/core/parties/", {"code": "C-9", "name": "Kisan Feeds", **body},
                                  format="json")

    def test_a_role_that_is_not_trading_is_refused_and_nothing_is_made(self):
        response = self.create(role=PartyRole.EMPLOYEE)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("role", response.json())
        self.assertFalse(Party.objects.filter(code="C-9").exists())

    def test_a_new_customer_is_in_the_customer_list(self):
        response = self.create(role=PartyRole.CUSTOMER)
        self.assertEqual(response.status_code, 201, response.content)
        party = Party.objects.get(code="C-9")
        self.assertTrue(party.role_assignments.filter(role=PartyRole.CUSTOMER).exists())
        listed = self.as_("Sales Rep").get("/api/core/parties/", {"role_assignments__role": "customer"}).json()
        self.assertIn(party.pk, [row["id"] for row in listed])

    def test_a_refused_party_leaves_no_role_behind(self):
        response = self.create(role=PartyRole.CUSTOMER, code="")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(PartyRoleAssignment.objects.filter(party__name="Kisan Feeds").count(), 0)

    def test_without_a_role_none_is_given(self):
        # Not by a rep, who makes customers only (tests_reps).
        self.assertEqual(self.create(as_="Purchasing Clerk").status_code, 201)
        self.assertFalse(Party.objects.get(code="C-9").role_assignments.exists())


class VoidedSinceItWasReadTests(ScreensTestCase):
    def test_a_payment_whose_entry_was_reversed_by_hand_meanwhile(self):
        from django.core.exceptions import ValidationError

        from apps.accounting.models import JournalEntry, Payment

        payment = self.receipt("100")
        stale = Payment.objects.select_related("journal_entry").prefetch_related(
            "journal_entry__reversed_by").get(pk=payment.pk)
        self.assertFalse(stale.voided_entry_id)
        JournalEntry.objects.get(pk=payment.journal_entry_id).create_reversal(memo="By hand")
        with self.assertRaisesMessage(ValidationError, "already been voided"):
            stale.void(memo="Bounced")
        self.assertEqual(JournalEntry.objects.filter(reverses_id=payment.journal_entry_id).count(), 1)
        self.assertEqual(self.balance(self.bank), Decimal("0.00"))  # in once, out once


class ReviewFindingsTests(ScreensTestCase):
    def test_a_party_made_in_the_office_says_who_made_it(self):
        response = self.as_("Sales Rep").post("/api/core/parties/", {"code": "C-50", "name": "Raj Cement",
                                                                    "role": "customer"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        party = Party.objects.get(code="C-50")
        rep = User.objects.get(username="Sales_Rep")
        self.assertEqual((party.created_by, party.updated_by), (rep, rep))
        self.assertEqual(party.role_assignments.get().created_by, rep)

    def test_an_id_that_is_not_a_number_is_refused_by_its_field(self):
        order = self.make_order()
        response = self.ship_order(order, warehouse="MAIN")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("warehouse", response.json())
        response = self.as_("AR Manager").post(f"/api/sales/sales-orders/{order.pk}/create_invoice/",
                                               {"receivable_account": "1100"}, format="json")
        self.assertEqual(response.status_code, 404, response.content)  # a number, but no such account
        response = self.as_("AR Manager").post(f"/api/sales/sales-orders/{order.pk}/create_invoice/",
                                               {"receivable_account": "AR"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("receivable_account", response.json())


class WhatIsLeftToApplyTests(ScreensTestCase):
    def test_the_server_says_what_is_left_on_a_receipt(self):
        first = self.bill(self.make_order(quantity="10", price="100"))
        second = self.bill(self.make_order(quantity="10", price="100"))
        payment = self.receipt("1500")
        self.allocate(payment, first, "1000")
        self.allocate(payment, second, "300")
        row = self.as_("AR Manager").get(f"/api/accounting/payments/{payment.pk}/").json()
        self.assertEqual(Decimal(row["unallocated"]), Decimal("200"))  # 1500 - 1000 - 300


class PartOfADepositThroughTheApiTests(ScreensTestCase):
    def test_an_amount_is_read_to_the_paisa_and_refused_beside_its_field(self):
        order = self.make_order("10", "100")
        deposit = order.create_down_payment_invoice(self.ar, percent=30)
        deposit.post()
        url = f"/api/sales/invoices/{deposit.pk}/credit_note/"
        ar = self.as_("AR Manager")
        for typed in ["ninety", "0", "-1", "10.005"]:
            with self.subTest(typed=typed):
                refused = ar.post(url, {"amount": typed}, format="json")
                self.assertEqual(refused.status_code, 400, refused.content)
                self.assertIn("amount", refused.json())
        self.assertEqual(self.as_("Sales Rep").post(url, {"amount": "90"}, format="json").status_code, 403)
        given = ar.post(url, {"amount": "90", "memo": "Order cut"}, format="json")
        self.assertEqual(given.status_code, 200, given.content)
        self.assertEqual(given.json()["total"], "90.00")
        deposit.refresh_from_db()
        self.assertEqual(deposit.deposit_unapplied(), Decimal("210.00"))



class ACancelThatHoldsADepositThroughTheApiTests(ScreensTestCase):
    def test_refused_in_words_until_the_deposit_is_credited_back(self):
        order = self.make_order("10", "100")
        deposit = order.create_down_payment_invoice(self.ar, amount=Decimal("300"))
        deposit.post()
        ar = self.as_("AR Manager")
        cancel = f"/api/sales/sales-orders/{order.pk}/cancel/"
        refused = ar.post(cancel, {}, format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn(f"Down payment {deposit.number} still holds 300.00", str(refused.json()))
        given = ar.post(f"/api/sales/invoices/{deposit.pk}/credit_note/", {"memo": "Order called off"}, format="json")
        self.assertEqual((given.status_code, given.json()["total"]), (200, "300.00"), given.content)
        cancelled = ar.post(cancel, {}, format="json")
        self.assertEqual((cancelled.status_code, cancelled.json()["status"]), (200, "cancelled"), cancelled.content)


class DeliveryChallanPdfTests(ScreensTestCase):
    """The challan the lorry carries, read by whoever may read the delivery and nobody else."""

    def test_the_challan_prints_for_the_warehouse_and_not_for_payroll(self):
        delivery = self.ship(self.make_order(), "10")
        response = self.as_("Warehouse Staff").get(f"/api/sales/deliveries/{delivery.pk}/pdf/")
        self.assertEqual((response.status_code, response["Content-Type"]), (200, "application/pdf"))
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.assertIn(delivery.number, response["Content-Disposition"])
        self.assertEqual(self.as_("Payroll Officer").get(f"/api/sales/deliveries/{delivery.pk}/pdf/").status_code, 403)

    def test_a_draft_with_no_lines_prints_too(self):
        from .models import Delivery

        draft = Delivery.objects.create(sales_order=self.make_order(), delivery_date=datetime.date(2026, 3, 3))
        self.assertTrue(draft.render_pdf().startswith(b"%PDF-"))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class DeliveryMailAndHistoryTests(ScreensTestCase):
    """The challan goes to the customer by mail once shipped, and the delivery's page says what happened to it."""

    def test_shipping_and_sending_are_the_deliverys_history(self):
        from django.core import mail

        from .models import Delivery, DeliveryLine

        order = self.make_order()
        delivery = Delivery.objects.create(sales_order=order, delivery_date=datetime.date(2026, 3, 3))
        DeliveryLine.objects.create(delivery=delivery, order_line=order.lines.filter(charge__isnull=True).first(),
                                    warehouse=self.warehouse, quantity_shipped=Decimal("10"))
        warehouse = self.as_("Warehouse Staff")
        self.assertEqual(warehouse.post(f"/api/sales/deliveries/{delivery.pk}/send/").status_code, 400)
        self.assertEqual(warehouse.post(f"/api/sales/deliveries/{delivery.pk}/post_delivery/").status_code, 200)
        sent = warehouse.post(f"/api/sales/deliveries/{delivery.pk}/send/")
        self.assertEqual((sent.status_code, sent.json()), (200, {"sent_to": "ap@acme.example"}))
        delivery.refresh_from_db()
        self.assertEqual([message.attachments[0][0] for message in mail.outbox], [f"{delivery.number}.pdf"])
        self.assertEqual(mail.outbox[0].subject, f"Delivery challan {delivery.number} from Test Co")
        rows = warehouse.get("/api/core/history/", {"model": "sales.delivery", "id": delivery.pk}).json()
        self.assertEqual([(row["label"], row["summary"]) for row in rows],
                         [("Sent", "Delivery challan to ap@acme.example"), ("Posted delivery", "")])
        self.assertEqual(self.as_("Payroll Officer").post(f"/api/sales/deliveries/{delivery.pk}/send/").status_code, 403)
