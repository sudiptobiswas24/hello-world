"""
Foreign balances restated at the closing rate (accounting.restatement, g24).

Every figure is worked in /home/user/work/calc_fix_yearend/calc.py: a USD invoice booked at 83.3367,
restated at 84.1 on 31 March, reversed on 1 April and paid at 84.55 on 15 April.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import Company, Currency, ExchangeRate, Party, PartyRole, PartyRoleAssignment, UnitOfMeasure
from apps.inventory.models import Item

from .models import Account, AccountingPeriod, AccountType, JournalLine, Payment, PaymentDirection
from .restatement import Restatement

D = datetime.date
YEAR_END = D(2026, 3, 31)


class RestatementTestCase(TestCase):
    def setUp(self):
        self.inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        self.usd = Currency.objects.create(code="USD", name="Dollar")
        for day, rate in ((D(2026, 3, 5), "83.3367"), (D(2026, 3, 10), "83.9"), (YEAR_END, "84.1"),
                          (D(2026, 4, 15), "84.55")):
            ExchangeRate.objects.create(currency=self.usd, rate=Decimal(rate), valid_from=day)
        self.uom = UnitOfMeasure.objects.create(code="ea", name="Each")
        self.item = Item.objects.create(sku="W", name="Widget", uom=self.uom)
        acc = lambda c, n, t, **more: Account.objects.create(code=c, name=n, account_type=t, **more)
        self.ar = acc("1100", "AR", AccountType.ASSET)
        self.ap = acc("2000", "AP", AccountType.LIABILITY)
        self.bank = acc("1010", "Bank", AccountType.ASSET, holds_money=True)
        self.eefc = acc("1020", "EEFC USD", AccountType.ASSET, holds_money=True, currency=self.usd)
        self.revenue = acc("4000", "Revenue", AccountType.INCOME)
        self.gain = acc("4900", "FX Gain", AccountType.INCOME)
        self.loss = acc("5900", "FX Loss", AccountType.EXPENSE)
        Company.objects.create(name="Plant", base_currency=self.inr, fx_gain_account=self.gain,
                               fx_loss_account=self.loss)
        self.customer = Party.objects.create(code="C-1", name="Buyer Inc", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)
        self.vendor = Party.objects.create(code="V-1", name="Seller LLC", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)

    def march(self):
        return AccountingPeriod.objects.create(name="March 2026", start_date=D(2026, 3, 1), end_date=YEAR_END)

    def balance(self, account, through=None):
        rows = JournalLine.objects.filter(account=account, entry__posted=True)
        if through:
            rows = rows.filter(entry__date__lte=through)
        rows = rows.aggregate(debit=Sum("debit"), credit=Sum("credit"))
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))

    def invoice(self, amount="1000"):
        from apps.sales.models import SalesOrder, SalesOrderLine

        order = SalesOrder.objects.create(customer=self.customer, order_date=D(2026, 3, 5), currency=self.usd)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
                                      unit_price=Decimal(amount), revenue_account=self.revenue)
        order.confirm()
        invoice = order.create_invoice(self.ar, invoice_date=D(2026, 3, 5))
        invoice.post()
        return invoice

    def receipt(self, amount, on, bank=None, party=None, direction=PaymentDirection.RECEIPT):
        receiving = direction == PaymentDirection.RECEIPT
        payment = Payment.objects.create(
            party=party or (self.customer if receiving else self.vendor), direction=direction, payment_date=on,
            amount=Decimal(amount), currency=self.usd, bank_account=bank or self.bank,
            counterpart_account=self.ar if receiving else self.ap)
        payment.post()
        return payment

    def apply(self, payment, invoice, amount, on):
        from apps.sales.models import InvoicePayment

        return InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal(amount), date=on)

    def gains(self, through=None):
        return -self.balance(self.gain, through) - self.balance(self.loss, through)


class AnOpenInvoiceTests(RestatementTestCase):
    def test_restated_reversed_and_paid_against_its_booked_base(self):
        invoice = self.invoice()
        self.assertEqual(self.balance(self.ar), Decimal("83336.70"))
        AccountingPeriod.objects.create(name="Q4", start_date=D(2026, 1, 1), end_date=YEAR_END)
        made = Restatement.make(YEAR_END)
        line = made.lines.get()
        self.assertEqual((line.label, line.open_amount, line.rate, line.carried_base, line.restated_base,
                          line.difference), (invoice.number, Decimal("1000.00"), Decimal("84.1"),
                                             Decimal("83336.70"), Decimal("84100.00"), Decimal("763.30")))
        self.assertEqual(list(made.rates.values_list("currency__code", "rate", "rate_from")),
                         [("USD", Decimal("84.1"), YEAR_END)])
        # The year closes at the closing rate; the next period opens at the booked one.
        self.assertEqual(self.balance(self.ar, YEAR_END), Decimal("84100.00"))
        self.assertEqual(self.gains(YEAR_END), Decimal("763.30"))
        self.assertEqual(made.reverses_on, D(2026, 4, 1))
        self.assertEqual(self.balance(self.ar, D(2026, 4, 1)), Decimal("83336.70"))
        # Paid at 84.55: realised against the 83,336.70 it booked, not the 84,100 restated.
        self.apply(self.receipt("1000", D(2026, 4, 15)), invoice, "1000", D(2026, 4, 15))
        self.assertEqual(self.balance(self.ar), Decimal("0"))
        self.assertEqual(self.gains() - self.gains(YEAR_END), Decimal("450.00"))  # -763.30 reversed + 1,213.30
        self.assertEqual(self.gains(), Decimal("1213.30"))

    def test_restated_as_it_stood_though_paid_since(self):
        invoice = self.invoice()
        self.apply(self.receipt("1000", D(2026, 4, 15)), invoice, "1000", D(2026, 4, 15))
        line = Restatement.make(YEAR_END).lines.get()
        self.assertEqual((line.open_amount, line.difference), (Decimal("1000.00"), Decimal("763.30")))
        self.assertEqual(self.balance(self.ar), Decimal("0"))
        self.assertEqual(self.gains(), Decimal("1213.30"))

    def test_paid_before_the_year_end_leaves_nothing_to_restate(self):
        invoice = self.invoice()
        self.apply(self.receipt("1000", D(2026, 3, 20)), invoice, "1000", D(2026, 3, 20))
        self.assertEqual(self.gains(), Decimal("563.30"))
        with self.assertRaisesMessage(ValidationError, "Nothing in a foreign currency was open on 31-03-2026"):
            Restatement.make(YEAR_END)

    def test_a_credit_note_after_the_day_is_replayed_to_it(self):
        # A full credit note dated 2 April, the year end restated after it: 1,000 was open on 31 March.
        invoice = self.invoice()
        invoice.create_credit_note(on_date=D(2026, 4, 2))
        line = Restatement.make(YEAR_END).lines.get(item_type="sales.invoice", item_id=invoice.pk)
        self.assertEqual((line.open_amount, line.carried_base, line.difference),
                         (Decimal("1000.00"), Decimal("83336.70"), Decimal("763.30")))
        self.assertEqual(self.balance(self.ar, YEAR_END), Decimal("84100.00"))
        self.assertEqual(self.balance(self.ar), Decimal("0"))
        self.assertEqual(self.gains(), Decimal("0"))

    def test_a_part_paid_invoice_restates_what_is_left(self):
        invoice = self.invoice()
        self.apply(self.receipt("400", D(2026, 3, 10)), invoice, "400", D(2026, 3, 10))
        self.assertEqual(self.gains(), Decimal("225.32"))
        line = Restatement.make(YEAR_END).lines.get()
        self.assertEqual((line.open_amount, line.carried_base, line.restated_base, line.difference),
                         (Decimal("600.00"), Decimal("50002.02"), Decimal("50460.00"), Decimal("457.98")))
        self.apply(self.receipt("600", D(2026, 4, 15)), invoice, "600", D(2026, 4, 15))
        self.assertEqual(self.balance(self.ar), Decimal("0"))
        self.assertEqual(self.gains(), Decimal("225.32") + Decimal("727.98"))


class AnOpenBillTests(RestatementTestCase):
    def test_a_bill_owed_in_dollars_is_restated_as_a_loss(self):
        from apps.purchasing.models import Bill, BillLine

        expense = Account.objects.create(code="5000", name="Purchases", account_type=AccountType.EXPENSE)
        bill = Bill.objects.create(vendor=self.vendor, bill_date=D(2026, 3, 5), payable_account=self.ap,
                                   currency=self.usd)
        BillLine.objects.create(bill=bill, description="Parts", quantity=Decimal("1"), unit_price=Decimal("1000"),
                                expense_account=expense)
        bill.post()
        line = Restatement.make(YEAR_END).lines.get()
        self.assertEqual((line.label, line.side, line.carried_base, line.restated_base, line.difference),
                         (bill.number, -1, Decimal("-83336.70"), Decimal("-84100.00"), Decimal("-763.30")))
        self.assertEqual(self.balance(self.ap, YEAR_END), Decimal("-84100.00"))
        self.assertEqual(self.gains(YEAR_END), Decimal("-763.30"))
        self.assertEqual(self.balance(self.ap), Decimal("-83336.70"))


    def test_a_bill_dated_into_a_restated_day_is_refused(self):
        from apps.purchasing.models import Bill, BillLine

        self.invoice()
        made = Restatement.make(YEAR_END)
        expense = Account.objects.create(code="5001", name="Freight", account_type=AccountType.EXPENSE)
        bill = Bill.objects.create(vendor=self.vendor, bill_date=D(2026, 3, 30), payable_account=self.ap,
                                   currency=self.usd)
        BillLine.objects.create(bill=bill, description="Freight", quantity=Decimal("1"), unit_price=Decimal("10"),
                                expense_account=expense)
        with self.assertRaisesMessage(ValidationError, f"Void {made.number} and restate once this is posted"):
            bill.post()
        made.void(on_date=YEAR_END)
        bill.post()


class AForeignBankTests(RestatementTestCase):
    def test_the_eefc_balance_and_the_money_on_account(self):
        self.receipt("1000", D(2026, 3, 10), bank=self.eefc)
        self.receipt("300", D(2026, 3, 20), bank=self.eefc, direction=PaymentDirection.DISBURSEMENT)
        made = Restatement.make(YEAR_END)
        lines = {line.item_type: line for line in made.lines.all() if line.item_type != "accounting.payment"}
        bank = lines["accounting.account"]
        self.assertEqual((bank.open_amount, bank.carried_base, bank.restated_base, bank.difference),
                         (Decimal("700.00"), Decimal("58730.00"), Decimal("58870.00"), Decimal("140.00")))
        on_account = sorted((line.side, line.difference) for line in made.lines.filter(item_type="accounting.payment"))
        self.assertEqual(on_account, [(-1, Decimal("-200.00")), (1, Decimal("60.00"))])
        self.assertEqual(self.balance(self.eefc, YEAR_END), Decimal("58870.00"))
        self.assertEqual(self.gains(YEAR_END), Decimal("0.00"))
        self.assertEqual(self.balance(self.eefc), Decimal("58730.00"))


class WhatARestatementRefusesTests(RestatementTestCase):
    def test_a_second_for_the_same_day(self):
        self.invoice()
        made = Restatement.make(YEAR_END)
        with self.assertRaisesMessage(ValidationError, f"{made.number} already restates 31-03-2026"):
            Restatement.make(YEAR_END)

    def test_settling_it_on_the_day_it_restated_until_voided(self):
        invoice = self.invoice()
        payment = self.receipt("1000", D(2026, 3, 20))
        made = Restatement.make(YEAR_END)
        with self.assertRaisesMessage(ValidationError, f"{made.number} restated"):
            self.apply(payment, invoice, "1000", YEAR_END)
        with self.assertRaisesMessage(ValidationError, f"{made.number} restated"):
            payment.void(on_date=YEAR_END)
        made.void(on_date=YEAR_END)
        self.assertEqual(self.balance(self.ar, YEAR_END), Decimal("83336.70") - Decimal("83900.00"))
        self.assertEqual(self.gains(), Decimal("0"))
        self.apply(payment, invoice, "1000", YEAR_END)
        self.assertEqual(self.gains(), Decimal("563.30"))
        with self.assertRaisesMessage(ValidationError, "Nothing in a foreign currency was open"):
            Restatement.make(YEAR_END)

    def test_a_void_after_it_reversed_itself(self):
        self.invoice()
        made = Restatement.make(YEAR_END)
        with self.assertRaisesMessage(ValidationError, "it reversed itself on 01-04-2026"):
            made.void(on_date=D(2026, 4, 2))
        with self.assertRaisesMessage(ValidationError, "is not voided on 2026-03-30"):
            made.void(on_date=D(2026, 3, 30))

    def test_a_closed_period(self):
        self.invoice()
        self.march().close()
        with self.assertRaisesMessage(ValidationError, "March 2026 is closed"):
            Restatement.make(YEAR_END)

    def test_an_advance_is_not_restated(self):
        from apps.sales.models import Invoice

        invoice = self.invoice()
        Invoice.objects.filter(pk=invoice.pk).update(is_down_payment=True)
        with self.assertRaisesMessage(ValidationError, "Nothing in a foreign currency was open"):
            Restatement.make(YEAR_END)


class ThroughTheApiTests(RestatementTestCase):
    def as_(self, role):
        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user(role.replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_controller_restates_and_voids(self):
        self.invoice()
        client = self.as_("Controller")
        preview = client.get("/api/accounting/restatements/preview/", {"as_of": "2026-03-31"})
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertEqual(preview.json()["items"][0]["carried_base"], "83336.70")
        made = client.post("/api/accounting/restatements/restate/", {"as_of": "2026-03-31"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual(made.json()["lines"][0]["difference"], "763.30")
        again = client.post("/api/accounting/restatements/restate/", {"as_of": "2026-03-31"}, format="json")
        self.assertEqual(again.status_code, 400)
        voided = client.post(f"/api/accounting/restatements/{made.json()['id']}/void/", {"date": "2026-03-31"},
                             format="json")
        self.assertEqual(voided.status_code, 200, voided.content)
        self.assertEqual(voided.json()["voided_on"], "2026-03-31")

    def test_refused_to_a_bookkeeper(self):
        self.invoice()
        client = self.as_("Bookkeeper")
        self.assertEqual(client.post("/api/accounting/restatements/restate/", {"as_of": "2026-03-31"},
                                     format="json").status_code, 403)
        self.assertEqual(client.get("/api/accounting/restatements/").status_code, 403)
        self.assertFalse(Restatement.objects.exists())
