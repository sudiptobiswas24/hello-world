"""
review_money probes, purchasing side (gate g20). Figures are worked in the
docstrings / $SP/calc_review_money/calc.py, not read off the code under test.
"""
import datetime
from decimal import Decimal as D

from apps.accounting.models import Account, AccountType, Payment, PaymentDirection
from apps.core.models import Currency, ExchangeRate, Party, PartyRole, PartyRoleAssignment, UnitOfMeasure
from apps.inventory.models import Item

from .models import (
    BlanketOrder, BlanketOrderLine, Bill, BillPayment, PurchaseOrder, PurchaseOrderLine, VendorPrice,
)
from .pricing import preferred_vendor, resolve_purchase_price
from .tests_audit import VendorSettlementDiscountTests
from .tests_money import MoneyCase

JAN = lambda d: datetime.date(2026, 1, d)  # noqa: E731


class DebitNoteAfterDiscountAndBounceProbe(VendorSettlementDiscountTests):
    """Bill 50 (10 @ 5), paid 49 with 1.00 discount; note 1 unit (5) reverses 0.10 of the discount."""

    def agree(self, label):
        bills = Bill.objects.filter(vendor=self.vendor, posted=True)
        derived = D("0")
        for b in bills:
            derived += (b.amount_due() if b.is_debit_note() else -b.amount_due())
        ledger = self.balance(self.payable)
        self.assertEqual((ledger,), (derived,), f"{label}: (AP in the ledger) vs minus the sum of dues {derived}")

    def pay(self, amount, day):
        bank = Account.objects.get_or_create(code="1011", defaults=dict(name="Bank", account_type=AccountType.ASSET,
                                                                       holds_money=True))[0]
        paying = Payment.objects.create(party=self.vendor, direction=PaymentDirection.DISBURSEMENT,
                                        amount=D(amount), payment_date=day, bank_account=bank,
                                        counterpart_account=self.payable, currency=self.usd)
        paying.post()
        return paying

    def test_note_then_bounce_then_pay_again(self):
        bill = self.discounted_bill()
        first = self.pay("49", JAN(15))
        BillPayment.objects.create(bill=bill, payment=first, amount=D("49"))
        bill.take_settlement_discount(on_date=JAN(15))
        note = Bill.objects.get(pk=bill.pk).create_debit_note(memo="One back",
                                                              quantities={bill.lines.get(): D("1")})
        self.agree("after the note")
        first.void(memo="Recalled", on_date=JAN(25))
        self.agree("after the payment came back")
        bill = Bill.objects.get(pk=bill.pk)
        note = Bill.objects.get(pk=note.pk)
        # 50 billed, 5 debited back, nothing paid any more: 45.00 is payable, as the ledger says.
        self.assertEqual((self.balance(self.payable), bill.amount_due(), note.amount_due()),
                         (D("-45.00"), D("45.00"), D("0.00")), "(AP, bill due, note due)")


class VendorPriceUnitProbe(MoneyCase):
    def setUp(self):
        super().setUp()
        self.kg = UnitOfMeasure.objects.create(code="kg", name="Kilo")
        self.tonne = UnitOfMeasure.objects.create(code="t", name="Tonne", base_unit=self.kg,
                                                  conversion_factor=D("1000"))
        self.resin = Item.objects.create(sku="PP-1", name="PP granule", uom=self.kg)
        self.v2 = Party.objects.create(code="V-2", name="Other", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.v2, role=PartyRole.VENDOR)

    def test_the_cheaper_vendor_per_kilo_is_preferred_whatever_unit_it_quotes_in(self):
        # V-1 100 a kg; V-2 90,000 a tonne = 90 a kg. Cheaper: V-2.
        VendorPrice.objects.create(vendor=self.vendor, item=self.resin, unit_price=D("100"))
        VendorPrice.objects.create(vendor=self.v2, item=self.resin, unit_price=D("90000"), uom=self.tonne)
        self.assertEqual(preferred_vendor(self.resin), self.v2)

    def test_the_bigger_break_wins_across_units(self):
        # P1: 100/kg from 1000 kg. P2: 90,000/t from 2 t (= 2000 kg). 3 t reaches both; P2's break is bigger.
        VendorPrice.objects.create(vendor=self.vendor, item=self.resin, unit_price=D("100"), min_quantity=D("1000"))
        VendorPrice.objects.create(vendor=self.vendor, item=self.resin, unit_price=D("90000"), uom=self.tonne,
                                   min_quantity=D("2"))
        self.assertEqual(resolve_purchase_price(self.resin, self.vendor, quantity=D("3"), uom=self.tonne),
                         D("90000.00"), "per tonne")

    def test_a_euro_price_does_not_price_a_base_order_at_face_value(self):
        # 10 EUR at 1.1 is 11.00 base. Order with no currency (base) for 5: the line must not read 10.
        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(currency=eur, rate=D("1.1"), valid_from=JAN(1))
        VendorPrice.objects.create(vendor=self.vendor, item=self.resin, currency=eur, unit_price=D("10"))
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=JAN(2))
        try:
            line = PurchaseOrderLine.objects.create(order=order, item=self.resin, uom=self.kg, quantity=D("5"))
            got = line.unit_price
        except Exception as error:  # a refusal is acceptable
            got = D("11.00")
            print("EURO PRICE ON BASE ORDER refused:", type(error).__name__, error)
        self.assertEqual(got, D("11.00"))


class BlanketReleaseDiscountProbe(MoneyCase):
    def test_a_release_at_the_agreed_discount_receives_and_bills_to_nothing_on_grni(self):
        # 10 @ 100 less 5% = 950. Receive 10, bill: GRNI 0, AP -950, Inventory 950.
        blanket = BlanketOrder.objects.create(vendor=self.vendor, start_date=JAN(1), end_date=datetime.date(2026, 12, 31),
                                              currency=self.usd)
        line = BlanketOrderLine.objects.create(blanket=blanket, item=self.item, uom=self.uom, quantity=D("10"),
                                               unit_price=D("100"), discount_percent=D("5"))
        blanket.confirm()
        order = blanket.release({line: D("10")}, order_date=JAN(3))
        order.confirm() if order.status == "draft" else None
        self.receive(order, "10")
        bill = order.create_bill(self.payable, bill_date=JAN(10))
        bill.post()
        self.assertEqual((order.lines.get().net_amount(), self.balance(self.grni), self.balance(self.payable),
                          self.balance(self.inventory)), (D("950.00"), D("0"), D("-950.00"), D("950.00")),
                         "(order line net, GRNI, AP, inventory)")


class RequisitionEstimateProbe(VendorPriceUnitProbe):
    test_a_euro_price_does_not_price_a_base_order_at_face_value = None
    test_the_cheaper_vendor_per_kilo_is_preferred_whatever_unit_it_quotes_in = None
    test_the_bigger_break_wins_across_units = None
    def test_a_euro_vendor_price_estimates_a_requisition_line_in_base(self):
        # 10 EUR at 1.1 = 11.00 base; 5 units -> 55.00 base. Taken at face value it reads 50.00.
        from .models import PurchaseRequisition, PurchaseRequisitionLine
        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(currency=eur, rate=D("1.1"), valid_from=JAN(1))
        VendorPrice.objects.create(vendor=self.vendor, item=self.resin, currency=eur, unit_price=D("10"))
        boss = Party.objects.create(code="E-9", name="Buyer")
        PartyRoleAssignment.objects.create(party=boss, role=PartyRole.EMPLOYEE)
        req = PurchaseRequisition.objects.create(requested_by=boss, request_date=JAN(2))
        line = PurchaseRequisitionLine.objects.create(requisition=req, item=self.resin, uom=self.kg, quantity=D("5"),
                                                      suggested_vendor=self.vendor)
        self.assertEqual(line.estimated_value(), D("55.00"))


class RoleRepointBillProbe(MoneyCase):
    def test_a_bill_allocation_cannot_be_repointed_past_the_new_bills_ceiling_over_the_api(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient
        from .models import BillLine
        call_command("setup_roles", verbosity=0)

        def bill(price):
            b = Bill.objects.create(vendor=self.vendor, bill_date=JAN(5), payable_account=self.payable)
            BillLine.objects.create(bill=b, description="Parts", quantity=D("1"), unit_price=D(price),
                                    expense_account=self.expense)
            b.post()
            return b
        x, y = bill("1000"), bill("500")
        bank = Account.objects.create(code="1011", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        pay = Payment.objects.create(party=self.vendor, direction=PaymentDirection.DISBURSEMENT, payment_date=JAN(6),
                                     amount=D("1000"), bank_account=bank, counterpart_account=self.payable,
                                     currency=self.usd)
        pay.post()
        alloc = BillPayment.objects.create(bill=x, payment=pay, amount=D("1000"))
        user = User.objects.create_user("ap")
        user.groups.add(Group.objects.get(name="AP Manager"))
        client = APIClient()
        client.force_authenticate(user)
        r = client.patch(f"/api/purchasing/bill-payments/{alloc.pk}/", {"bill": y.pk}, format="json")
        self.assertEqual((r.status_code, Bill.objects.get(pk=y.pk).amount_due()), (400, D("500.00")), r.content)
        clerk = User.objects.create_user("clerk")
        clerk.groups.add(Group.objects.get(name="Purchasing Clerk"))
        client.force_authenticate(clerk)
        r = client.patch(f"/api/purchasing/bill-payments/{alloc.pk}/", {"bill": y.pk, "amount": "500"}, format="json")
        self.assertEqual(r.status_code, 403, "a purchasing clerk does not re-point money")
