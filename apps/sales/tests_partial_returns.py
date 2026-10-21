"""
A customer sends back part of what came: twenty of fifty bags torn in
transit, and later the rest. Purchasing could always return part of a
receipt; a delivery could only be returned whole, and once, so twenty
torn bags could not be recorded at all. Found testing the return paths
end to end.

Fifty shipped at 25.00 and invoiced, 1,250.00. Twenty back: a credit
note of 500.00 and 750.00 still due. The other thirty: 750.00 more, and
nothing due.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.inventory.reports import reconcile_to_ledger

from .tests_base import SalesTestCase


class PartOfADeliveryComesBackTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.gap = reconcile_to_ledger()["difference"]
        self.order = self.make_order("50", "25")
        self.shipment = self.ship(self.order, "50")
        self.invoice = self.bill(self.order)
        self.line = self.shipment.lines.get()

    def test_refused_beyond_what_is_left(self):
        with self.assertRaisesMessage(ValidationError, "Only 50"):
            self.shipment.create_return(quantities={self.line: Decimal("51")})
        self.shipment.create_return(quantities={self.line: Decimal("20")})
        with self.assertRaisesMessage(ValidationError, "Only 30"):
            self.shipment.create_return(quantities={self.line: Decimal("31")})

    def test_refused_for_a_line_of_another_delivery(self):
        other = self.ship(self.make_order("5", "25"), "5")
        with self.assertRaisesMessage(ValidationError, "different delivery"):
            self.shipment.create_return(quantities={other.lines.get(): Decimal("1")})

    def test_part_then_the_rest(self):
        back = self.shipment.create_return(quantities={self.line: Decimal("20")})
        (note,) = back.credit_notes_created
        self.assertEqual((note.total(), self.invoice.amount_due()),
                         (Decimal("500.00"), Decimal("750.00")))
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("470"))
        rest = self.shipment.create_return()
        self.assertEqual(rest.lines.get().quantity_shipped, Decimal("30"))
        self.assertEqual(self.invoice.amount_due(), Decimal("0.00"))
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("500"))
        self.assertEqual(reconcile_to_ledger()["difference"], self.gap)
        with self.assertRaisesMessage(ValidationError, "nothing left on this delivery"):
            self.shipment.create_return()

    def test_over_the_api_too(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("store"))
        response = client.post(f"/api/sales/deliveries/{self.shipment.pk}/customer_return/",
                               {"quantities": {str(self.line.pk): "20"}}, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response.json()["credit_notes"][0]["total"], "500.00")
        self.assertEqual(client.post(
            f"/api/sales/deliveries/{self.shipment.pk}/customer_return/",
            {"quantities": {str(self.line.pk): "31"}}, format="json").status_code, 400)


class BatchesGoHomeTests(SalesTestCase):
    """
    Thirty from batch A and twenty from batch B went out on one line.
    Twenty-five back, then twenty-five more: A gets its thirty and B its
    twenty, not fifty into A because each return began again at the top.
    """

    def test_each_batch_gets_back_what_left_it(self):
        from django.utils import timezone

        from apps.inventory.models import Item, Lot, MovementType, StockMovement

        Item.objects.filter(pk=self.item.pk).update(tracking="lot")
        self.item.refresh_from_db()
        a = Lot.objects.create(item=self.item, code="A")
        b = Lot.objects.create(item=self.item, code="B")
        for lot, quantity in ((a, "30"), (b, "20")):
            StockMovement.objects.create(item=self.item, warehouse=self.warehouse, lot=lot,
                                         movement_type=MovementType.RECEIPT, uom=self.uom,
                                         quantity=Decimal(quantity), unit_cost=Decimal("4"),
                                         occurred_at=timezone.now())
        from .models import Delivery, DeliveryAllocation, DeliveryLine

        order = self.make_order("50", "25")
        shipment = Delivery.objects.create(sales_order=order, delivery_date=datetime.date(2026, 3, 3))
        line = DeliveryLine.objects.create(delivery=shipment, order_line=order.lines.get(),
                                           warehouse=self.warehouse,
                                           quantity_shipped=Decimal("50"))
        shipment.post()
        sent = {row.lot.code: row.quantity for row in DeliveryAllocation.objects.filter(line=line)}
        before = {lot.code: lot.on_hand_at(self.warehouse) for lot in (a, b)}
        shipment.create_return(quantities={line: Decimal("25")}, credit_invoices=False)
        shipment.create_return(quantities={line: Decimal("25")}, credit_invoices=False)
        after = {lot.code: lot.on_hand_at(self.warehouse) for lot in (a, b)}
        self.assertEqual({code: after[code] - before[code] for code in after}, sent)

    def test_a_batch_shipped_on_a_posted_delivery_is_not_deleted(self):
        """O1 in docs/RISKS.md: the refusal read a field the allocation does not have."""
        from django.core.exceptions import ValidationError
        from django.utils import timezone

        from apps.inventory.models import Item, Lot, MovementType, StockMovement

        from .models import Delivery, DeliveryAllocation, DeliveryLine

        Item.objects.filter(pk=self.item.pk).update(tracking="lot")
        self.item.refresh_from_db()
        lot = Lot.objects.create(item=self.item, code="A")
        StockMovement.objects.create(item=self.item, warehouse=self.warehouse, lot=lot,
                                     movement_type=MovementType.RECEIPT, uom=self.uom,
                                     quantity=Decimal("30"), unit_cost=Decimal("4"), occurred_at=timezone.now())
        order = self.make_order("30", "25")
        shipment = Delivery.objects.create(sales_order=order, delivery_date=datetime.date(2026, 3, 3))
        line = DeliveryLine.objects.create(delivery=shipment, order_line=order.lines.get(),
                                           warehouse=self.warehouse, quantity_shipped=Decimal("30"))
        shipment.post()
        allocation = DeliveryAllocation.objects.get(line=line)

        with self.assertRaisesMessage(ValidationError, "Create a customer return instead"):
            allocation.delete()
        self.assertTrue(DeliveryAllocation.objects.filter(pk=allocation.pk).exists())


class ReturnedAtWhatTheShipmentTookTests(SalesTestCase):
    """
    A thousand at 4 and two thousand at 3: three thousand worth 10,000,
    averaging 3.3333... Shipped whole, cost of sales took 10,000.00.
    Returned at the line's four-place rate, 3,000 at 3.3333 put back
    9,999.90, and 0.10 of a shipment that came back entirely stayed in
    cost of sales. The total goes back, not a rate multiplied up.
    """

    def setUp(self):
        super().setUp()
        from django.utils import timezone

        from apps.inventory.models import Item, MovementType, StockMovement

        from .models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine

        self.plain = Item.objects.create(sku="PLAIN", name="Plain", uom=self.uom)
        for quantity, cost in (("1000", "4"), ("2000", "3")):
            StockMovement.objects.create(item=self.plain, warehouse=self.warehouse,
                                         movement_type=MovementType.RECEIPT, uom=self.uom,
                                         quantity=Decimal(quantity), unit_cost=Decimal(cost),
                                         occurred_at=timezone.now())
        order = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 3, 1),
                                          currency=self.usd)
        order_line = SalesOrderLine.objects.create(order=order, item=self.plain, uom=self.uom,
                                                   quantity=Decimal("3000"), unit_price=Decimal("5"),
                                                   revenue_account=self.revenue)
        order.confirm()
        self.shipment = Delivery.objects.create(sales_order=order, delivery_date=datetime.date(2026, 3, 3))
        self.line = DeliveryLine.objects.create(delivery=self.shipment, order_line=order_line,
                                                warehouse=self.warehouse,
                                                quantity_shipped=Decimal("3000"))
        self.shipment.post()

    def test_returned_whole_nothing_stays_in_cost_of_sales(self):
        self.shipment.create_return(credit_invoices=False)
        self.assertEqual(self.balance(self.cogs), Decimal("0.00"))

    def test_returned_whole_the_shelf_is_worth_what_it_was(self):
        self.shipment.create_return(credit_invoices=False)
        self.assertEqual(self.plain.stock_value_at(self.warehouse), Decimal("10000.00"))

    def test_returned_in_two_parts_it_still_comes_to_nothing(self):
        self.shipment.create_return(quantities={self.line: Decimal("1000")}, credit_invoices=False)
        self.assertEqual(self.balance(self.cogs), Decimal("6666.67"))
        self.shipment.create_return(quantities={self.line: Decimal("2000")}, credit_invoices=False)
        self.assertEqual(self.balance(self.cogs), Decimal("0.00"))
        self.assertEqual(self.plain.stock_value_at(self.warehouse), Decimal("10000.00"))

    def test_returned_in_three_parts_the_last_takes_what_is_left(self):
        """Each part put back 3,333.33 on its own reckoning, and 0.01 stayed in cost of sales."""
        balances = []
        for _part in range(3):
            self.shipment.create_return(quantities={self.line: Decimal("1000")}, credit_invoices=False)
            balances.append(self.balance(self.cogs))
        self.assertEqual(balances, [Decimal("6666.67"), Decimal("3333.33"), Decimal("0.00")])
        self.assertEqual(self.plain.stock_value_at(self.warehouse), Decimal("10000.00"))

    def test_taken_back_by_the_store_through_the_api(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        store = User.objects.create_user("store")
        store.groups.add(Group.objects.get(name="Warehouse Staff"))
        client = APIClient()
        client.force_authenticate(store)
        response = client.post(f"/api/sales/deliveries/{self.shipment.pk}/customer_return/",
                               {"credit_invoices": False}, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(self.balance(self.cogs), Decimal("0.00"))


from apps.core.tests_roles import RolesTestCase  # noqa: E402

from .models import Invoice, InvoiceLine, InvoicePolicy  # noqa: E402


class WhoMayCreditByReturningTests(RolesTestCase, SalesTestCase):
    """O292: a return's credit notes post only for someone who may post invoices, as purchasing's debit notes."""

    def setUp(self):
        SalesTestCase.setUp(self)
        RolesTestCase.setUp(self)

    def take_back(self, client, delivery):
        reply = client.post(f"/api/sales/deliveries/{delivery.pk}/customer_return/", {}, format="json")
        self.assertEqual(reply.status_code, 200, reply.content)
        return reply.json()["credit_notes"]

    def test_a_warehouse_hand_taking_goods_back_posts_no_credit_note(self):
        # 10 sacks at 100 shipped and invoiced; a store hand takes them back: the note waits for the AR side.
        order = self.make_order(quantity="10", price="100")
        delivery = self.ship(order, 10)
        invoice = self.bill(order)
        due = invoice.amount_due()
        (note,) = self.take_back(self.as_("Warehouse Staff"), delivery)
        self.assertEqual(Invoice.objects.filter(credits__isnull=False, posted=True).count(), 0)
        self.assertEqual(invoice.amount_due(), due)
        # The AR side posts it, and only then is the receivable cut.
        draft = Invoice.objects.get(pk=note["id"])
        draft.post()
        self.assertEqual(invoice.amount_due(), due - draft.total())

    def test_one_who_may_post_invoices_posts_the_return_notes(self):
        order = self.make_order(quantity="10", price="100")
        delivery = self.ship(order, 10)
        self.bill(order)
        (note,) = self.take_back(self.as_("Warehouse Staff", "AR Manager"), delivery)
        self.assertTrue(Invoice.objects.get(pk=note["id"]).posted)

    def test_a_second_return_credits_the_next_invoice_while_the_first_note_waits(self):
        # Two shipments of 5, each invoiced: the first return's draft takes invoice A; the second takes B, not A again.
        order = self.make_order(quantity="10", price="100", policy=InvoicePolicy.DELIVERED)
        first = self.ship(order, 5)
        a = self.bill(order)
        second = self.ship(order, 5)
        b = self.bill(order)
        hand = self.as_("Warehouse Staff")
        (one,) = self.take_back(hand, first)
        (two,) = self.take_back(hand, second)
        credited = sorted(Invoice.objects.get(pk=note["id"]).credits_id for note in (one, two))
        self.assertEqual(credited, sorted([a.pk, b.pk]))


class AReturnWaitingAsADraftHoldsItsInvoiceLineTests(SalesTestCase):
    """O336: a store hand's return waits as a draft; AR cannot credit the same goods by hand meanwhile."""

    def shipped_and_invoiced(self):
        order = self.make_order(quantity="10", price="100")
        delivery = self.ship(order, 10)
        invoice = self.bill(order)
        return delivery, invoice, invoice.lines.get()

    def test_ar_credits_only_what_the_draft_leaves(self):
        # 10 at 100 invoiced; 4 taken back by someone who may not post invoices: 6 are left to credit by hand.
        delivery, invoice, line = self.shipped_and_invoiced()
        delivery.create_return(quantities={delivery.lines.get(): Decimal("4")}, post_notes=False)
        self.assertEqual(line.quantity_creditable(), Decimal("6"))
        with self.assertRaises(ValidationError):
            invoice.create_credit_note(quantities={line: Decimal("7")})
        invoice.create_credit_note(quantities={line: Decimal("6")})
        # The draft then posts: 1,000 credited in all, nothing over.
        Invoice.objects.get(credits=invoice, posted=False).post()
        self.assertEqual(invoice.amount_due(), Decimal("0.00"))

    def test_a_whole_return_waiting_leaves_nothing_to_credit_by_hand(self):
        delivery, invoice, line = self.shipped_and_invoiced()
        delivery.create_return(post_notes=False)
        self.assertEqual(line.quantity_creditable(), Decimal("0"))
        with self.assertRaises(ValidationError):
            invoice.create_credit_note()
        with self.assertRaises(ValidationError):
            invoice.create_credit_note(quantities={line: Decimal("10")})

    def test_the_draft_deleted_gives_the_room_back(self):
        delivery, invoice, line = self.shipped_and_invoiced()
        delivery.create_return(post_notes=False)
        Invoice.objects.get(credits=invoice, posted=False).delete()
        self.assertEqual(line.quantity_creditable(), Decimal("10"))

    def test_a_draft_line_typed_past_what_is_left_is_refused_at_save(self):
        delivery, invoice, line = self.shipped_and_invoiced()
        delivery.create_return(quantities={delivery.lines.get(): Decimal("4")}, post_notes=False)
        note = Invoice.objects.create(customer=invoice.customer, invoice_date=invoice.invoice_date,
                                      receivable_account=invoice.receivable_account, currency=invoice.currency,
                                      credits=invoice)
        with self.assertRaises(ValidationError):
            InvoiceLine.objects.create(invoice=note, credits_line=line, item=line.item, quantity=Decimal("7"),
                                       unit_price=line.unit_price, revenue_account=line.revenue_account)


class ANoteMadeInCodeKeepsToItsInvoiceTests(SalesTestCase):
    """O298 (probes S1, S2, S2b, S5): a credit note keyed in code is refused as it is made, and again as it posts."""

    def posted_invoice(self, day=datetime.date(2026, 3, 1)):
        order = self.make_order(quantity="10", price="100")
        self.ship(order, 10)
        return self.bill(order, on=day)

    def note(self, invoice, **extra):
        return Invoice.objects.create(
            customer=invoice.customer, invoice_date=extra.pop("invoice_date", invoice.invoice_date),
            receivable_account=extra.pop("receivable_account", invoice.receivable_account),
            currency=invoice.currency, credits=invoice, **extra)

    def line(self, note, invoice, quantity="2"):
        line = invoice.lines.first()
        return InvoiceLine.objects.create(invoice=note, credits_line=line, item=line.item, quantity=Decimal(quantity),
                                          unit_price=line.unit_price, revenue_account=line.revenue_account)

    def test_a_note_on_another_control_account_is_refused(self):
        from apps.accounting.models import Account, AccountType

        invoice = self.posted_invoice()
        other = Account.objects.create(code="1101", name="AR 2", account_type=AccountType.ASSET)
        with self.assertRaisesMessage(ValidationError, "a note on it is booked there too"):
            self.note(invoice, receivable_account=other)
        # Moved after it was made, it is refused as it posts.
        note = self.note(invoice)
        self.line(note, invoice)
        Invoice.objects.filter(pk=note.pk).update(receivable_account=other)
        with self.assertRaisesMessage(ValidationError, "a note on it is booked there too"):
            Invoice.objects.get(pk=note.pk).post()

    def test_a_note_dated_before_its_invoice_is_refused(self):
        invoice = self.posted_invoice(datetime.date(2026, 3, 10))
        with self.assertRaisesMessage(ValidationError, "it was invoiced on 2026-03-10"):
            self.note(invoice, invoice_date=datetime.date(2026, 3, 1))
        note = self.note(invoice)
        self.line(note, invoice)
        Invoice.objects.filter(pk=note.pk).update(invoice_date=datetime.date(2026, 3, 1))
        with self.assertRaisesMessage(ValidationError, "it was invoiced on 2026-03-10"):
            Invoice.objects.get(pk=note.pk).post()

    def test_a_note_dated_a_year_ahead_is_refused(self):
        invoice = self.posted_invoice()
        with self.assertRaisesMessage(ValidationError, "that day has not come"):
            self.note(invoice, invoice_date=timezone.localdate() + datetime.timedelta(days=400))

    def test_a_typed_credit_line_cannot_credit_more_than_the_invoice(self):
        # 10 x 100: 1,000 to give back. A typed line of 1 x 5,000 is refused as it is saved.
        invoice = self.posted_invoice()
        note = self.note(invoice)
        with self.assertRaisesMessage(ValidationError, "has 1000 left to give back"):
            InvoiceLine.objects.create(invoice=note, description="oops", quantity=Decimal("1"),
                                       unit_price=Decimal("5000"), revenue_account=self.revenue)

    def test_a_typed_line_is_held_to_what_posted_notes_leave(self):
        # 2 of the 10 credited (200): a typed goodwill credit may give back 800, not 800.01.
        invoice = self.posted_invoice()
        invoice.create_credit_note(quantities={invoice.lines.first(): Decimal("2")})
        note = self.note(invoice)
        with self.assertRaisesMessage(ValidationError, "has 800 left to give back (200 of 1000"):
            InvoiceLine.objects.create(invoice=note, description="goodwill", quantity=Decimal("1"),
                                       unit_price=Decimal("800.01"), revenue_account=self.revenue)
        line = InvoiceLine.objects.create(invoice=note, description="goodwill", quantity=Decimal("1"),
                                          unit_price=Decimal("800"), revenue_account=self.revenue)
        # Raised past it behind save()'s back, the posting refuses it.
        InvoiceLine.objects.filter(pk=line.pk).update(unit_price=Decimal("900"))
        with self.assertRaisesMessage(ValidationError, "this note gives back 900"):
            Invoice.objects.get(pk=note.pk).post()
        InvoiceLine.objects.filter(pk=line.pk).update(unit_price=Decimal("800"))
        Invoice.objects.get(pk=note.pk).post()
        self.assertEqual(invoice.amount_due(), Decimal("0.00"))
