"""Review probes (trade3): the note-line check (O170-O172) and the O174 backfill. Each prints what happened."""
import datetime
from decimal import Decimal as D

from django.core.exceptions import ValidationError

from apps.accounting.models import Account, AccountType, Tax

from .models import Delivery, Invoice, InvoiceLine, SalesOrder, SalesOrderLine
from .tests_base import SalesTestCase

DAY = datetime.date(2026, 3, 1)


def attempt(call):
    try:
        call()
    except ValidationError as exc:
        return "REFUSED: " + "; ".join(exc.messages)[:200]
    return "POSTED"


class NoteLineProbes(SalesTestCase):
    def note(self, invoice, line, quantity, price, taxes=(), discount=None):
        note = Invoice.objects.create(customer=self.customer, invoice_date=DAY, currency=self.usd,
                                      receivable_account=self.ar, credits=invoice)
        made = InvoiceLine.objects.create(invoice=note, credits_line=line, item=self.item, quantity=D(quantity),
                                          unit_price=D(price), revenue_account=self.revenue,
                                          **({"discount_percent": D(discount)} if discount else {}))
        if taxes:
            made.taxes.set(taxes)
        return note

    def test_P1_claim_then_return_of_all_the_goods(self):
        order = self.make_order("10", "100")
        delivery = self.ship(order, "10")
        invoice = self.bill(order)
        invoice.credit_claim(D("100"), "rate", on_date=DAY)
        out = attempt(lambda: Delivery.objects.get(pk=delivery.pk).create_return())
        print(f"\nP1 claim 100 then return all 10: {out}; AR {self.balance(self.ar)}; "
              f"on hand after: {self.item.on_hand_at(self.warehouse)}")

    def test_P2_claim_then_two_part_returns(self):
        order = self.make_order("10", "100")
        delivery = self.ship(order, "10")
        invoice = self.bill(order)
        invoice.credit_claim(D("100"), "rate", on_date=DAY)
        d = Delivery.objects.get(pk=delivery.pk)
        first = attempt(lambda: d.create_return(quantities={d.lines.get(): D("5")}))
        second = attempt(lambda: Delivery.objects.get(pk=delivery.pk).create_return(
            quantities={d.lines.get(): D("5")}))
        print(f"\nP2 claim 100; return 5 -> {first}; return 5 more -> {second}; AR {self.balance(self.ar)}")

    def test_P3_one_unit_at_ten_times_the_price(self):
        order = self.make_order("10", "100")
        invoice = self.bill(order)
        line = invoice.lines.get()
        n = self.note(invoice, line, "1", "1000")
        out = attempt(lambda: Invoice.objects.get(pk=n.pk).post())
        rest = attempt(lambda: Invoice.objects.get(pk=invoice.pk).create_credit_note())
        print(f"\nP3 1 unit at 1000 on a 10x100 line: {out}; AR {self.balance(self.ar)}; "
              f"then credit of the other 9: {rest}")

    def test_P4_typed_note_carries_a_tax_the_invoice_never_charged(self):
        pay = Account.objects.create(code="2199", name="Tax payable", account_type=AccountType.LIABILITY)
        vat = Tax.objects.create(code="V20", name="VAT 20", rate=D("20"), collected_account=pay, paid_account=pay)
        order = self.make_order("10", "100")
        invoice = self.bill(order)
        n = self.note(invoice, invoice.lines.get(), "10", "100", taxes=[vat])
        out = attempt(lambda: Invoice.objects.get(pk=n.pk).post())
        print(f"\nP4 invoice 1000 untaxed, note 10x100 + 20% VAT: {out}; AR {self.balance(self.ar)}; "
              f"tax payable {self.balance(pay)}")

    def test_P5_partial_then_full_default_and_by_value(self):
        order = self.make_order("10", "100")
        invoice = self.bill(order)
        line = invoice.lines.get()
        n1 = self.note(invoice, line, "4", "100", discount="0")
        a = attempt(lambda: Invoice.objects.get(pk=n1.pk).post())
        b = attempt(lambda: Invoice.objects.get(pk=invoice.pk).create_credit_note())
        print(f"\nP5 partial 4 then whole: {a}, {b}; AR {self.balance(self.ar)}")

    def test_P6_partial_at_a_higher_price_then_the_rest_at_its_own(self):
        order = self.make_order("10", "100")
        invoice = self.bill(order)
        n1 = self.note(invoice, invoice.lines.get(), "5", "150")
        a = attempt(lambda: Invoice.objects.get(pk=n1.pk).post())
        b = attempt(lambda: Invoice.objects.get(pk=invoice.pk).create_credit_note())
        print(f"\nP6 5 units at 150 (750) then the rest by default: {a}; {b}; AR {self.balance(self.ar)}")

    def test_P7_a_claim_spread_onto_a_fully_credited_line(self):
        order = self.make_order("10", "100")
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("10"),
                                      unit_price=D("100"), revenue_account=self.revenue)
        order = SalesOrder.objects.get(pk=order.pk)
        invoice = order.create_invoice(self.ar, invoice_date=DAY)
        invoice.post()
        first = invoice.lines.order_by("pk").first()
        n1 = self.note(invoice, first, "10", "100")
        Invoice.objects.get(pk=n1.pk).post()
        claim = attempt(lambda: Invoice.objects.get(pk=invoice.pk).credit_claim(D("1000"), "rate", on_date=DAY))
        credited = sum(l.quantity * l.unit_price for l in InvoiceLine.objects.filter(credits_line=first, invoice__posted=True))
        print(f"\nP7 line 1 fully credited (1000), claim 1000 on the 2000 invoice: {claim}; "
              f"credited on line 1 = {credited} of 1000; AR {self.balance(self.ar)}")

    def test_P8_type_a_note_line_naming_another_item(self):
        order = self.make_order("10", "100")
        invoice = self.bill(order)
        note = Invoice.objects.create(customer=self.customer, invoice_date=DAY, currency=self.usd,
                                      receivable_account=self.ar, credits=invoice)
        other = Account.objects.create(code="4999", name="Other income", account_type=AccountType.INCOME)
        InvoiceLine.objects.create(invoice=note, credits_line=invoice.lines.get(), description="x", quantity=D("10"),
                                   unit_price=D("100"), revenue_account=other)
        out = attempt(lambda: Invoice.objects.get(pk=note.pk).post())
        print(f"\nP8 note line crediting the other income account: {out}")


class SeedEdges(SalesTestCase):
    def test_P9_seed_of_an_order_with_no_lines_and_of_a_cancelled_one(self):
        from django.contrib.auth.models import User
        from apps.sales.models import ApprovalPolicy
        ApprovalPolicy.objects.create(code="STD", name="Standard", max_discount_percent=D("15"))
        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY, currency=self.usd)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("10"),
                                      unit_price=D("100"), discount_percent=D("20"), revenue_account=self.revenue)
        order.approve(by=User.objects.create_user("a1"))
        order.confirm()
        kept = SalesOrder.objects.get(pk=order.pk).approved_figures
        # Cut to 10 before the seed runs (as an order cut between 0063 and 0064, or before 0063 from its old approval)
        line = order.lines.get()
        line.discount_percent = D("10")
        line.save()
        SalesOrder.objects.filter(pk=order.pk).update(approved_figures=None)
        from importlib import import_module
        mod = import_module("apps.sales.migrations.0064_seed_approved_figures")
        from django.apps import apps as live
        mod.seed(live, None)
        seeded = SalesOrder.objects.get(pk=order.pk).approved_figures
        print(f"\nP9 approved at 20 (kept {kept['discounts']}), cut to 10, then the seed: {seeded['discounts']}")
        line = order.lines.get()
        line.discount_percent = D("20")
        out = attempt(lambda: line.save())
        print(f"P9 put back to 20 after the seed: {out} (status {SalesOrder.objects.get(pk=order.pk).status})")
