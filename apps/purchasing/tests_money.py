"""
Money that came out wrong on the purchasing side (g20, from the 9 October
audit's probes): a prepayment drawn off the account it was held in,
pricing in the line's unit, a release at the agreed discount. Each
expected figure was worked in a separate plain-Python script
(calc_fix_money/).
"""

import datetime
from decimal import Decimal

from django.db.models import Sum

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import Company, Currency, ExchangeRate

from .models import Bill, BillPolicy, PurchaseOrder, PurchaseOrderLine
from .tests_lifecycle import PurchasingLifecycleTestCase

JAN = lambda day: datetime.date(2026, 1, day)  # noqa: E731


class MoneyCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        acc = lambda code, name, kind: Account.objects.create(code=code, name=name, account_type=kind)  # noqa: E731
        self.ppv = acc("5900", "Purchase price variance", AccountType.EXPENSE)
        self.fx_loss = acc("7100", "FX loss", AccountType.EXPENSE)
        self.fx_gain = acc("7000", "FX gain", AccountType.INCOME)
        company = Company.get()
        company.purchase_price_variance_account = self.ppv
        company.default_purchase_expense_account = self.expense
        company.fx_loss_account = self.fx_loss
        company.fx_gain_account = self.fx_gain
        company.purchase_price_tolerance_percent = Decimal("0")
        company.save()

    def balance(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            d=Sum("debit"), c=Sum("credit"))
        return (rows["d"] or Decimal("0")) - (rows["c"] or Decimal("0"))

    def order(self, quantity="10", price="5", discount="0", policy=BillPolicy.RECEIVED):
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=JAN(1), bill_policy=policy)
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom,
                                         quantity=Decimal(quantity), unit_price=Decimal(price),
                                         discount_percent=Decimal(discount))
        order.confirm()
        return order


class PrepaymentHeldWhereItWasPaidTests(MoneyCase):
    def test_a_drawdown_takes_the_prepayment_from_where_it_was_held(self):
        """O105. 15 paid up front into 1400; the setting then moved to 1401. Billed 50: 1400 and 1401 both 0."""
        held = Account.objects.create(code="1400", name="Vendor prepayments", account_type=AccountType.ASSET)
        Company.objects.update(vendor_prepayment_account=held)
        order = self.order()
        order.create_prepayment_bill(self.payable, percent=30).post()
        moved = Account.objects.create(code="1401", name="Vendor advances", account_type=AccountType.ASSET)
        Company.objects.update(vendor_prepayment_account=moved)
        self.receive(order, "10")
        order.create_bill(self.payable, bill_date=JAN(10)).post()
        self.assertEqual((self.balance(held), self.balance(moved)), (Decimal("0"), Decimal("0")),
                         "(1400, 1401)")

    def test_a_euro_prepayment_debited_in_part_is_drawn_down_to_nothing(self):
        """
        O51 with a note. EUR order 10 @ 5. 15 paid up front at 90 (1,350); 5 debited back
        (450, at 90); received at 90, billed at 95: the last 10 drawn takes the 900 left.
        Held 0; the bill owes 40 EUR.
        """
        eur = Currency.objects.create(code="EUR", name="Euro")
        for day, rate in ((1, "90"), (10, "95")):
            ExchangeRate.objects.create(currency=eur, rate=Decimal(rate), valid_from=JAN(day))
        held = Account.objects.create(code="1400", name="Vendor prepayments", account_type=AccountType.ASSET)
        Company.objects.update(vendor_prepayment_account=held)
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=JAN(1), currency=eur)
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                         unit_price=Decimal("5"))
        order.confirm()
        prepayment = order.create_prepayment_bill(self.payable, amount=Decimal("15"), bill_date=JAN(2))
        prepayment.post()
        Bill.objects.get(pk=prepayment.pk).create_debit_note(amount=Decimal("5"))
        self.receive(order, "10")
        bill = order.create_bill(self.payable, bill_date=JAN(10))
        bill.post()
        bill = Bill.objects.get(pk=bill.pk)
        self.assertEqual((self.balance(held), bill.amount_due()), (Decimal("0"), Decimal("40.00")),
                         "(held, bill due)")


class BouncedRefundTests(MoneyCase):
    """
    O53 (DebitNoteRefundProbe). A bill of 1,000 paid in full; a debit note
    of 300; the vendor's 300 refund, applied to the note, bounces. The
    vendor owes the 300 again, as the payable says: the note counted every
    allocation, the sales mirror only those that stand (settlement.standing_paid).
    """

    def test_a_vendor_refund_returned_unpaid_is_owed_again(self):
        from apps.accounting.models import Payment
        from .models import BillLine, BillPayment

        bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)

        def pay(direction, amount, day):
            payment = Payment.objects.create(party=self.vendor, direction=direction, payment_date=day,
                                             amount=Decimal(amount), currency=self.usd, bank_account=bank,
                                             counterpart_account=self.payable)
            payment.post()
            return payment

        bill = Bill.objects.create(vendor=self.vendor, bill_date=JAN(10), payable_account=self.payable,
                                   currency=self.usd)
        BillLine.objects.create(bill=bill, description="Spares", quantity=Decimal("10"), unit_price=Decimal("100"),
                                expense_account=self.expense)
        bill.post()
        BillPayment.objects.create(bill=bill, payment=pay("disbursement", "1000", JAN(12)), amount=Decimal("1000"))
        note = bill.create_debit_note(quantities={bill.lines.get(): Decimal("3")})
        self.assertEqual(note.refund_due(), Decimal("300"))
        refund = pay("receipt", "300", JAN(20))
        BillPayment.objects.create(bill=note, payment=refund, amount=Decimal("300"))
        self.assertEqual(Bill.objects.get(pk=note.pk).refund_due(), Decimal("0"))
        refund.void(memo="Bounced", on_date=JAN(25))
        note = Bill.objects.get(pk=note.pk)
        self.assertEqual((note.refund_due(), self.balance(self.payable)), (Decimal("300"), Decimal("300")),
                         "(the note's refund due, the payable) after the refund bounced")


class RepointedBillAllocationTests(MoneyCase):
    """O54, the purchasing side: 1,000 paid on X (1,000) re-pointed to Y (500) is refused."""

    def test_a_bill_allocation_re_pointed_to_a_smaller_bill_is_refused(self):
        from django.core.exceptions import ValidationError

        from apps.accounting.models import Payment
        from .models import BillLine, BillPayment

        bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)

        def bill(price):
            made = Bill.objects.create(vendor=self.vendor, bill_date=JAN(10), payable_account=self.payable,
                                       currency=self.usd)
            BillLine.objects.create(bill=made, description="Spares", quantity=Decimal("1"),
                                    unit_price=Decimal(price), expense_account=self.expense)
            made.post()
            return made

        x, y = bill("1000"), bill("500")
        paying = Payment.objects.create(party=self.vendor, direction="disbursement", payment_date=JAN(12),
                                        amount=Decimal("1000"), currency=self.usd, bank_account=bank,
                                        counterpart_account=self.payable)
        paying.post()
        allocation = BillPayment.objects.create(bill=x, payment=paying, amount=Decimal("1000"))
        allocation.bill = y
        with self.assertRaisesMessage(ValidationError, "only has 500.00 outstanding"):
            allocation.save()
        self.assertEqual(Bill.objects.get(pk=y.pk).amount_due(), Decimal("500.00"))


class PricedInTheLinesTermsTests(MoneyCase):
    """
    O97, O98 (audit_pur AgreementTests). A blanket 10 @ 100 less 5% released
    in full comes to 950.00. Agreed 100 a kg: 2 tonnes ordered with no price
    typed are 100,000 a tonne, 200,000.00; a break at 1,000 kg is reached by
    2 tonnes and not by half of one (calc_fix_money/pricing.py).
    """

    def setUp(self):
        super().setUp()
        from apps.core.models import UnitOfMeasure
        from apps.inventory.models import Item

        self.kg = UnitOfMeasure.objects.create(code="kg", name="Kilogramme")
        self.tonne = UnitOfMeasure.objects.create(code="t", name="Tonne", base_unit=self.kg,
                                                  conversion_factor=Decimal("1000"))
        self.granule = Item.objects.create(sku="PP-GRAN", name="PP granules", uom=self.kg)

    def ordered(self, quantity, uom):
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=JAN(1))
        return PurchaseOrderLine.objects.create(order=order, item=self.granule, uom=uom, quantity=Decimal(quantity))

    def test_a_release_keeps_the_discount_agreed(self):
        from .models import BlanketOrder, BlanketOrderLine

        blanket = BlanketOrder.objects.create(vendor=self.vendor, start_date=JAN(1),
                                              end_date=datetime.date(2026, 12, 31))
        line = BlanketOrderLine.objects.create(blanket=blanket, item=self.item, uom=self.uom,
                                               quantity=Decimal("10"), unit_price=Decimal("100"),
                                               discount_percent=Decimal("5"))
        blanket.confirm()
        order = blanket.release({line: Decimal("10")}, order_date=JAN(5))
        self.assertEqual(order.lines.get().net_amount(), Decimal("950.00"))

    def test_an_agreed_price_per_kg_prices_an_order_in_tonnes(self):
        from .models import VendorPrice

        VendorPrice.objects.create(vendor=self.vendor, item=self.granule, currency=self.usd, unit_price=Decimal("100"))
        line = self.ordered("2", self.tonne)
        self.assertEqual((line.unit_price, line.net_amount()), (Decimal("100000.00"), Decimal("200000.00")))

    def test_a_price_per_tonne_and_its_break_are_in_tonnes(self):
        """The same agreed per tonne: 100,000 a tonne, and 90,000 from 1 tonne; ordered in kg."""
        from .models import VendorPrice

        VendorPrice.objects.create(vendor=self.vendor, item=self.granule, currency=self.usd, uom=self.tonne,
                                   unit_price=Decimal("100000"))
        VendorPrice.objects.create(vendor=self.vendor, item=self.granule, currency=self.usd, uom=self.tonne,
                                   unit_price=Decimal("90000"), min_quantity=Decimal("1"))
        self.assertEqual(self.ordered("2000", self.kg).unit_price, Decimal("90.00"), "2,000 kg reach the 1 t break")
        self.assertEqual(self.ordered("500", self.kg).unit_price, Decimal("100.00"), "500 kg do not")

    def test_agreed_per_kg_over_the_api_by_the_ap_manager(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("ap")
        user.groups.add(Group.objects.get(name="AP Manager"))
        client = APIClient()
        client.force_authenticate(user)
        response = client.post("/api/purchasing/vendor-prices/", {
            "vendor": self.vendor.pk, "item": self.granule.pk, "currency": self.usd.pk, "unit_price": "100",
            "uom": self.kg.pk, "min_quantity": "0"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["uom"], self.kg.pk)
        self.assertEqual(self.ordered("2", self.tonne).unit_price, Decimal("100000.00"))
        response = client.post("/api/purchasing/vendor-prices/", {
            "vendor": self.vendor.pk, "item": self.granule.pk, "currency": self.usd.pk, "unit_price": "5",
            "uom": self.uom.pk}, format="json")
        self.assertEqual(response.status_code, 400, response.content)


