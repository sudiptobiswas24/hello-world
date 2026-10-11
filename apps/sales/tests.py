from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.models import Account, AccountType
from apps.core.models import Party, PartyRole, PartyRoleAssignment, UnitOfMeasure
from apps.inventory.models import Item

from .models import Invoice, InvoiceLine, SalesOrder, SalesOrderLine


class SalesTestCase(TestCase):
    def setUp(self):
        self.uom = UnitOfMeasure.objects.create(code="each", name="Each")
        self.item = Item.objects.create(sku="WIDGET-1", name="Widget", uom=self.uom)

        self.customer = Party.objects.create(code="CUST-1", name="Acme Co")
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)

        self.receivable = Account.objects.create(
            code="1100", name="Accounts Receivable", account_type=AccountType.ASSET
        )
        self.revenue = Account.objects.create(
            code="4000", name="Sales Revenue", account_type=AccountType.INCOME
        )

    def make_invoice(self, quantity=Decimal("2"), unit_price=Decimal("50")):
        invoice = Invoice.objects.create(
            customer=self.customer,
            invoice_date="2026-01-01",
            receivable_account=self.receivable,
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            item=self.item,
            description="Widgets",
            quantity=quantity,
            unit_price=unit_price,
            revenue_account=self.revenue,
        )
        return invoice


class CustomerRoleTests(SalesTestCase):
    def test_party_without_customer_role_is_rejected(self):
        vendor = Party.objects.create(code="VEND-1", name="Not A Customer")
        invoice = Invoice(
            customer=vendor, invoice_date="2026-01-01", receivable_account=self.receivable
        )
        with self.assertRaises(ValidationError):
            invoice.full_clean()

    def test_sales_order_requires_customer_role(self):
        vendor = Party.objects.create(code="VEND-2", name="Not A Customer Either")
        order = SalesOrder(customer=vendor, order_date="2026-01-01")
        with self.assertRaises(ValidationError):
            order.full_clean()


class SalesOrderTests(SalesTestCase):
    def test_total_sums_line_subtotals(self):
        order = SalesOrder.objects.create(customer=self.customer, order_date="2026-01-01")
        SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("3"), unit_price=Decimal("10")
        )
        SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("1"), unit_price=Decimal("5")
        )
        self.assertEqual(order.total(), Decimal("35"))


class InvoicePostingTests(SalesTestCase):
    def test_posting_creates_balanced_journal_entry(self):
        invoice = self.make_invoice(Decimal("2"), Decimal("50"))
        invoice.post()
        invoice.refresh_from_db()

        self.assertTrue(invoice.posted)
        entry = invoice.journal_entry
        self.assertTrue(entry.posted)
        self.assertEqual(entry.total_debit(), Decimal("100"))
        self.assertEqual(entry.total_credit(), Decimal("100"))

        ar_line = entry.lines.get(account=self.receivable)
        revenue_line = entry.lines.get(account=self.revenue)
        self.assertEqual(ar_line.debit, Decimal("100"))
        self.assertEqual(revenue_line.credit, Decimal("100"))

    def test_cannot_post_invoice_with_no_lines(self):
        invoice = Invoice.objects.create(
            customer=self.customer, invoice_date="2026-01-01", receivable_account=self.receivable
        )
        with self.assertRaises(ValidationError):
            invoice.post()

    def test_cannot_post_twice(self):
        invoice = self.make_invoice()
        invoice.post()
        with self.assertRaises(ValidationError):
            invoice.post()


class InvoiceImmutabilityTests(SalesTestCase):
    def test_posted_invoice_cannot_be_edited(self):
        invoice = self.make_invoice()
        invoice.post()
        invoice.reference = "changed"
        with self.assertRaises(ValidationError):
            invoice.save()

    def test_posted_invoice_cannot_be_deleted(self):
        invoice = self.make_invoice()
        invoice.post()
        with self.assertRaises(ValidationError):
            invoice.delete()

    def test_line_on_posted_invoice_cannot_be_edited(self):
        invoice = self.make_invoice()
        invoice.post()
        line = invoice.lines.first()
        line.quantity = Decimal("999")
        with self.assertRaises(ValidationError):
            line.save()


class CreditNoteTests(SalesTestCase):
    def test_credit_note_reverses_original_journal_entry(self):
        invoice = self.make_invoice(Decimal("2"), Decimal("50"))
        invoice.post()

        credit_note = invoice.create_credit_note(memo="Customer returned goods")

        self.assertEqual(credit_note.credits, invoice)
        self.assertTrue(credit_note.posted)
        self.assertEqual(credit_note.journal_entry.reverses, invoice.journal_entry)

        cn_ar_line = credit_note.journal_entry.lines.get(account=self.receivable)
        cn_revenue_line = credit_note.journal_entry.lines.get(account=self.revenue)
        self.assertEqual(cn_ar_line.credit, Decimal("100"))
        self.assertEqual(cn_revenue_line.debit, Decimal("100"))

    def test_original_invoice_is_untouched_by_credit_note(self):
        invoice = self.make_invoice(Decimal("2"), Decimal("50"))
        invoice.post()
        original_journal_entry_id = invoice.journal_entry_id

        invoice.create_credit_note()
        invoice.refresh_from_db()

        self.assertEqual(invoice.journal_entry_id, original_journal_entry_id)
        self.assertTrue(invoice.journal_entry.posted)
        self.assertEqual(invoice.total(), Decimal("100"))

    def test_net_receivable_impact_is_zero_after_credit_note(self):
        invoice = self.make_invoice(Decimal("2"), Decimal("50"))
        invoice.post()
        invoice.create_credit_note()

        ar_lines = self.receivable.lines.all()
        debit_total = sum(line.debit for line in ar_lines)
        credit_total = sum(line.credit for line in ar_lines)
        self.assertEqual(debit_total, credit_total)

    def test_cannot_credit_an_unposted_invoice(self):
        invoice = self.make_invoice()
        with self.assertRaises(ValidationError):
            invoice.create_credit_note()

    def test_cannot_credit_a_credit_note(self):
        invoice = self.make_invoice()
        invoice.post()
        credit_note = invoice.create_credit_note()
        with self.assertRaises(ValidationError):
            credit_note.create_credit_note()


# The second trading review (O169-O172, O174, O175): docs/handoff/reports/review_trade2.md.
import datetime  # noqa: E402

from django.contrib.auth.models import User as _User  # noqa: E402
from rest_framework.test import APIClient as _APIClient  # noqa: E402

from .models import Delivery as _Delivery  # noqa: E402
from .models import Invoice as _Invoice  # noqa: E402
from .models import InvoiceLine as _InvoiceLine  # noqa: E402
from .models import SalesOrder as _SalesOrder  # noqa: E402
from .models import SalesOrderLine as _SalesOrderLine  # noqa: E402
from .tests_base import SalesTestCase as _SalesTestCase  # noqa: E402

_REVIEW_DAY = datetime.date(2026, 3, 1)


def _refused(call):
    try:
        call()
    except ValidationError as exc:
        return "; ".join(exc.messages)
    return ""


class ACreditNoteGivesBackOnlyWhatItsLineHoldsTests(_SalesTestCase):
    def note_on(self, invoice, **line):
        note = _Invoice.objects.create(customer=self.customer, invoice_date=_REVIEW_DAY, currency=self.usd,
                                       receivable_account=self.ar, credits=invoice)
        return note, _InvoiceLine.objects.create(invoice=note, credits_line=invoice.lines.get(), item=self.item,
                                                 revenue_account=self.revenue, **line)

    def test_a_note_against_a_credit_note_is_refused(self):  # O170
        invoice = self.bill(self.make_order("10", "100"))
        note = invoice.create_credit_note(quantities={invoice.lines.get(): Decimal("5")})
        second = _Invoice(customer=self.customer, invoice_date=_REVIEW_DAY, currency=self.usd,
                          receivable_account=self.ar, credits=note)
        self.assertIn("is itself a note", _refused(second.save))

    def test_a_typed_note_at_ten_times_the_price_is_refused(self):  # O171
        invoice = self.bill(self.make_order("10", "100"))
        note, _ = self.note_on(invoice, quantity=Decimal("10"), unit_price=Decimal("1000"))
        before = self.balance(self.ar)
        said = _refused(lambda: _Invoice.objects.get(pk=note.pk).post())
        self.assertIn("1000.00 left to give back; this note gives back 10000.00", said)
        self.assertEqual(self.balance(self.ar), before)

    def test_a_typed_note_line_takes_the_order_line_it_gives_back(self):  # O172
        order = self.make_order("10", "100")
        invoice = self.bill(order)
        note, line = self.note_on(invoice, quantity=Decimal("10"), unit_price=Decimal("100"))
        self.assertEqual(line.order_line_id, invoice.lines.get().order_line_id)
        _Invoice.objects.get(pk=note.pk).post()
        self.assertEqual(_SalesOrderLine.objects.get(order=order).quantity_invoiced(), Decimal("0"))

    def test_a_note_line_naming_no_order_line_does_not_post(self):  # O172, a line written before the fix
        invoice = self.bill(self.make_order("10", "100"))
        note, line = self.note_on(invoice, quantity=Decimal("10"), unit_price=Decimal("100"))
        _InvoiceLine.objects.filter(pk=line.pk).update(order_line=None)
        self.assertIn("this line names None", _refused(lambda: _Invoice.objects.get(pk=note.pk).post()))


class LocksTakenInTheWrittenOrderTests(_SalesTestCase):
    """Each failed under the lock-order sentinel before its fix: a deadlock path with two people."""

    def test_a_return_on_the_second_order_of_a_consolidated_invoice(self):  # O169
        first, second = self.make_order("5", "100"), self.make_order("5", "100")
        self.ship(first, "5")
        delivery = self.ship(second, "5")
        invoice = _Invoice.objects.create(customer=self.customer, invoice_date=_REVIEW_DAY, currency=self.usd,
                                          receivable_account=self.ar)
        for order in (first, second):
            _InvoiceLine.objects.create(invoice=invoice, order_line=order.lines.get(), quantity=Decimal("5"),
                                        unit_price=Decimal("100"), revenue_account=self.revenue)
        invoice.post()
        delivery = _Delivery.objects.get(pk=delivery.pk)
        delivery.create_return(quantities={delivery.lines.get(): Decimal("2")})
        self.assertEqual([o.pk for o in delivery.locked_before_returning()], [first.pk, second.pk])
        self.assertEqual(_SalesOrderLine.objects.get(order=second).quantity_invoiced(), Decimal("3"))

    def test_an_invoice_takes_the_older_deposit_it_draws_before_itself(self):  # O175
        order = self.make_order("10", "100")
        deposit = order.create_down_payment_invoice(self.ar, amount=Decimal("800"), invoice_date=_REVIEW_DAY)
        deposit.post()
        invoice = self.bill(order)
        self.assertEqual(_Invoice.objects.get(pk=invoice.pk).locked_before_it()[-1].pk, deposit.pk)
        self.assertEqual(_Invoice.objects.get(pk=deposit.pk).deposit_unapplied(), Decimal("0"))

    def test_a_line_moved_between_draft_orders_holds_both_orders_first(self):  # O175
        here, there = (_SalesOrder.objects.create(customer=self.customer, order_date=_REVIEW_DAY, currency=self.usd)
                       for _ in range(2))
        line = _SalesOrderLine.objects.create(order=there, item=self.item, uom=self.uom, quantity=Decimal("1"),
                                              unit_price=Decimal("1"), revenue_account=self.revenue)
        client = _APIClient()
        client.force_authenticate(_User.objects.create_superuser("mover"))
        response = client.patch(f"/api/sales/sales-order-lines/{line.pk}/", {"order": here.pk}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(_SalesOrderLine.objects.get(pk=line.pk).order_id, here.pk)


class AReturnAfterAClaimGivesBackWhatIsLeftTests(_SalesTestCase):  # O185
    def typed_note(self, invoice, line, quantity, price):
        note = _Invoice.objects.create(customer=self.customer, invoice_date=_REVIEW_DAY, currency=self.usd,
                                       receivable_account=self.ar, credits=invoice)
        _InvoiceLine.objects.create(invoice=note, credits_line=line, item=self.item, quantity=Decimal(quantity),
                                    unit_price=Decimal(price), revenue_account=self.revenue)
        return _Invoice.objects.get(pk=note.pk)

    def test_a_claim_then_the_whole_return_credits_what_is_left(self):
        order = self.make_order("10", "100")
        delivery = self.ship(order, "10")
        invoice = self.bill(order)
        invoice.credit_claim(Decimal("100"), "rate", on_date=_REVIEW_DAY)
        on_hand = self.item.on_hand_at(self.warehouse)
        returned = _Delivery.objects.get(pk=delivery.pk).create_return()
        note = _Invoice.objects.get(credits=invoice, claim_reason="")
        self.assertEqual(note.subtotal(), Decimal("900.00"))
        self.assertEqual(self.balance(self.ar), Decimal("0"))
        self.assertEqual(self.item.on_hand_at(self.warehouse), on_hand + Decimal("10"))
        self.assertTrue(returned.posted)

    def test_a_claim_then_two_part_returns_credit_450_each(self):
        order = self.make_order("10", "100")
        delivery = self.ship(order, "10")
        invoice = self.bill(order)
        invoice.credit_claim(Decimal("100"), "rate", on_date=_REVIEW_DAY)
        for _ in range(2):
            delivery = _Delivery.objects.get(pk=delivery.pk)
            delivery.create_return(quantities={delivery.lines.get(): Decimal("5")})
        notes = _Invoice.objects.filter(credits=invoice, claim_reason="").order_by("pk")
        self.assertEqual([note.subtotal() for note in notes], [Decimal("450.00"), Decimal("450.00")])
        self.assertEqual(self.balance(self.ar), Decimal("0"))

    def test_a_share_the_paisa_does_not_divide_comes_back_whole(self):
        invoice = self.bill(self.make_order("3", "100"))
        invoice.credit_claim(Decimal("100"), "rate", on_date=_REVIEW_DAY)
        note = _Invoice.objects.get(pk=invoice.pk).create_credit_note()
        self.assertEqual(sorted((line.quantity, line.unit_price) for line in note.lines.all()),
                         [(Decimal("1"), Decimal("66.68")), (Decimal("2"), Decimal("66.66"))])
        self.assertEqual(self.balance(self.ar), Decimal("0"))

    def test_one_unit_at_ten_times_the_price_is_refused_and_the_rest_still_credits(self):
        invoice = self.bill(self.make_order("10", "100"))
        note = self.typed_note(invoice, invoice.lines.get(), "1", "1000")
        said = _refused(note.post)
        self.assertIn("1 of 10 left: 100.00); this note gives back 1000.00", said)
        _Invoice.objects.get(pk=invoice.pk).create_credit_note()
        self.assertEqual(self.balance(self.ar), Decimal("0"))

    def test_five_units_at_150_on_a_line_at_100_are_refused(self):
        invoice = self.bill(self.make_order("10", "100"))
        said = _refused(self.typed_note(invoice, invoice.lines.get(), "5", "150").post)
        self.assertIn("5 of 10 left: 500.00); this note gives back 750.00", said)

    def test_a_claim_is_not_spread_onto_a_line_given_back_whole(self):
        order = self.make_order("10", "100")
        _SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                       unit_price=Decimal("100"), revenue_account=self.revenue)
        invoice = _SalesOrder.objects.get(pk=order.pk).create_invoice(self.ar, invoice_date=_REVIEW_DAY)
        invoice.post()
        first, second = invoice.lines.order_by("pk")
        self.typed_note(invoice, first, "10", "100").post()
        _Invoice.objects.get(pk=invoice.pk).credit_claim(Decimal("1000"), "rate", on_date=_REVIEW_DAY)
        given = {line.pk: sum((note.quantity * note.unit_price for note in
                               _InvoiceLine.objects.filter(credits_line=line, invoice__posted=True)), Decimal("0"))
                 for line in (first, second)}
        self.assertEqual(given, {first.pk: Decimal("1000"), second.pk: Decimal("1000")})
        self.assertEqual(self.balance(self.ar), Decimal("0"))
