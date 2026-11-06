"""
Purchasing audit probes, 9 October 2026. Each test states one claim about
money or state and fails with the observed and expected figures. Expected
figures were worked in a separate plain-Python script first.

Not regression tests yet: a failing test here is a finding.
"""

import datetime
import unittest
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import Sum
from django.test import TransactionTestCase, tag

from apps.accounting.models import Account, AccountType, JournalLine, PartyTaxProfile, TdsSection
from apps.core.models import (
    Company, Currency, ExchangeRate, Party, PartyRole, PartyRoleAssignment, PaymentTerms, UnitOfMeasure,
)
from apps.inventory.models import Item

from .models import (
    Bill, BillLine, BillPolicy, BlanketOrder, BlanketOrderLine, GoodsReceipt, GoodsReceiptLine,
    PurchaseOrder, PurchaseOrderLine, VendorPrice, payment_run,
)
from .tests_lifecycle import PurchasingLifecycleTestCase

JAN = lambda day: datetime.date(2026, 1, day)  # noqa: E731


def probe_accounts(self):
    """What every probe adds to the lifecycle fixture: variance and exchange accounts, tolerance 0."""
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


class ProbeCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        probe_accounts(self)

    def balance(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            d=Sum("debit"), c=Sum("credit"))
        return (rows["d"] or Decimal("0")) - (rows["c"] or Decimal("0"))

    def order(self, quantity="10", price="5", discount="0", policy=BillPolicy.RECEIVED, vendor=None,
              item=None, uom=None, confirm=True):
        order = PurchaseOrder.objects.create(vendor=vendor or self.vendor, order_date=JAN(1),
                                             bill_policy=policy)
        PurchaseOrderLine.objects.create(order=order, item=item or self.item, uom=uom or self.uom,
                                         quantity=Decimal(quantity), unit_price=Decimal(price),
                                         discount_percent=Decimal(discount))
        if confirm:
            order.confirm()
        return order

    def hand_bill(self, line, quantity, price, discount="0", vendor=None, order=None, currency=None,
                  reference="", item=None, link=True, day=None):
        bill = Bill.objects.create(vendor=vendor or self.vendor, bill_date=day or JAN(10), purchase_order=order,
                                   payable_account=self.payable, currency=currency or self.usd,
                                   reference=reference)
        BillLine.objects.create(bill=bill, order_line=line if link else None, item=item or self.item,
                                quantity=Decimal(quantity), unit_price=Decimal(price),
                                discount_percent=Decimal(discount), expense_account=self.expense)
        return bill

    def receive_on(self, order, quantity, day):
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=day)
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=order.lines.get(), warehouse=self.warehouse,
                                        quantity_received=Decimal(quantity))
        receipt.post()
        return receipt

    def other_vendor(self):
        other = Party.objects.create(code="V-2", name="Another supplier", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.VENDOR)
        return other


class GrniClearsTests(ProbeCase):
    def test_billed_ahead_of_its_goods_grni_clears_when_they_come(self):
        """Billed as ordered, 10 @ 5, then the 10 arrive: GRNI 0, purchases 0, inventory 50."""
        order = self.order(policy=BillPolicy.ORDERED)
        order.create_bill(self.payable, bill_date=JAN(3)).post()
        self.receive(order, "10")
        observed = (self.balance(self.grni), self.balance(self.expense), self.balance(self.inventory))
        self.assertEqual(observed, (Decimal("0"), Decimal("0"), Decimal("50.00")),
                         "(GRNI, purchases, inventory)")

    def test_billed_one_at_a_time_grni_clears_to_the_paisa(self):
        """3 @ 10.10 less 2.5% received (accrued 29.54), billed 1+1+1 at 9.85: GRNI 0.00."""
        order = self.order("3", "10.10", discount="2.5")
        self.receive(order, "3")
        line = order.lines.get()
        for _ in range(3):
            self.hand_bill(line, "1", "10.10", discount="2.5", order=order).post()
        self.assertEqual(self.balance(self.grni), Decimal("0.00"))

    def test_a_hand_bill_with_no_link_and_a_generated_bill_do_not_both_pay_one_receipt(self):
        """
        10 @ 5 received (GRNI -50). A bill typed for the item with no order line clears it.
        The order then still bills all 10. Expected: refused, or GRNI 0 and payable -50.
        """
        order = self.order()
        self.receive(order, "10")
        self.hand_bill(None, "10", "5", link=False).post()
        try:
            order.create_bill(self.payable, bill_date=JAN(12)).post()
        except ValidationError:
            return
        self.assertEqual((self.balance(self.grni), self.balance(self.payable)),
                         (Decimal("0"), Decimal("-50.00")), "(GRNI, payable) after both bills")


    def test_every_variation_at_once_grni_clears(self):
        """
        EUR 10 @ 5 less 10%: 6 in at 90, 4 in at 92 (2,430 + 1,656); billed 10 @ 5.20 less 10% at 95;
        3 of the first receipt sent back, debiting the bill. GRNI 0; the shelf gives up 3 at its
        average 408.60 (1,225.80, 10.80 of it price variance), leaving inventory 2,860.20.
        """
        eur = Currency.objects.create(code="EUR", name="Euro")
        for day, rate in ((1, "90"), (6, "92"), (10, "95")):
            ExchangeRate.objects.create(currency=eur, rate=Decimal(rate), valid_from=JAN(day))
        company = Company.get()
        company.purchase_price_tolerance_percent = Decimal("10")
        company.save()
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=JAN(1), currency=eur)
        line = PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                                unit_price=Decimal("5"), discount_percent=Decimal("10"))
        order.confirm()
        receipts = []
        for quantity, day in (("6", 5), ("4", 7)):
            receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=JAN(day))
            GoodsReceiptLine.objects.create(receipt=receipt, order_line=line, warehouse=self.warehouse,
                                            quantity_received=Decimal(quantity))
            receipt.post()
            receipts.append(receipt)
        self.hand_bill(line, "10", "5.20", discount="10", order=order, currency=eur).post()
        receipts[0].create_return({receipts[0].lines.get(): Decimal("3")})
        self.assertEqual((self.balance(self.grni), self.balance(self.inventory)),
                         (Decimal("0"), Decimal("2860.20")), "(GRNI, inventory)")
    def test_goods_sent_back_are_debited_on_the_bill_that_paid_for_them(self):
        """
        20 ordered @ 5 (tolerance 10%). 10 in on 5 Jan, billed 10 Jan at 5.00; 10 in on 11 Jan, billed
        12 Jan at 5.40 (4.00 variance). 3 of the second receipt sent back: the bill for them gave 16.20,
        so the vendor is debited 16.20 and the variance kept is 2.80.
        """
        company = Company.get()
        company.purchase_price_tolerance_percent = Decimal("10")
        company.save()
        order = self.order("20", "5")
        line = order.lines.get()
        self.receive_on(order, "10", JAN(5))
        self.hand_bill(line, "10", "5.00", order=order, day=JAN(10)).post()
        second = self.receive_on(order, "10", JAN(11))
        self.hand_bill(line, "10", "5.40", order=order, day=JAN(12)).post()
        returned = second.create_return({second.lines.get(): Decimal("3")})
        debited = sum((note.total() for note in returned.debit_notes_created), Decimal("0"))
        self.assertEqual((debited, self.balance(self.ppv), self.balance(self.grni)),
                         (Decimal("16.20"), Decimal("2.80"), Decimal("0")), "(debited, PPV, GRNI)")


class MatchTests(ProbeCase):
    def test_the_price_leg_reads_the_discount_agreed(self):
        """Agreed 10 @ 100 less 10% (900). Billed 10 @ 100 with no discount (1,000), tolerance 0: refused."""
        order = self.order("10", "100", discount="10")
        self.receive(order, "10")
        bill = self.hand_bill(order.lines.get(), "10", "100", order=order)
        try:
            bill.post()
        except ValidationError:
            return
        self.fail(f"Posted: payable {self.balance(self.payable)}, price variance {self.balance(self.ppv)}; "
                  "expected refused (agreed net 900.00)")

    def test_a_bill_for_one_vendor_cannot_bill_anothers_order(self):
        """V-1's order 10 @ 5 received. A bill from V-2 naming that order line: refused."""
        order = self.order()
        self.receive(order, "10")
        other = self.other_vendor()
        bill = self.hand_bill(order.lines.get(), "10", "5", vendor=other, order=order)
        try:
            bill.post()
        except ValidationError:
            return
        self.fail(f"Posted: V-2 owed {-self.balance(self.payable)}, V-1's line billed "
                  f"{order.lines.get().quantity_billed()}, GRNI {self.balance(self.grni)}; expected refused")

    def test_a_bill_in_another_currency_cannot_bill_the_order(self):
        """A USD order 10 @ 5 received (50). A EUR bill (rate 90) of 10 @ 5 on its line: refused."""
        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(currency=eur, rate=Decimal("90"), valid_from=JAN(1))
        order = self.order()
        self.receive(order, "10")
        bill = self.hand_bill(order.lines.get(), "10", "5", order=order, currency=eur)
        try:
            bill.post()
        except ValidationError:
            return
        self.fail(f"Posted: payable {self.balance(self.payable)}, FX loss {self.balance(self.fx_loss)}, "
                  f"GRNI {self.balance(self.grni)}; expected refused")

    def test_a_typed_bill_naming_no_order_cannot_bill_a_cancelled_one(self):
        """Ordered 10 @ 5, billed as ordered, cancelled. A bill naming its line but not the order: refused."""
        order = self.order(policy=BillPolicy.ORDERED)
        order.cancel()
        bill = self.hand_bill(order.lines.get(), "10", "5")
        try:
            bill.post()
        except ValidationError:
            return
        self.fail(f"Posted against {order.status} order: payable {self.balance(self.payable)}, "
                  f"line billed {order.lines.get().quantity_billed()}; expected refused")

    def test_a_bill_line_names_the_item_its_order_line_ordered(self):
        """10 of WDG-1 received. A bill line for WDG-2 naming WDG-1's order line: refused."""
        order = self.order()
        self.receive(order, "10")
        other = Item.objects.create(sku="WDG-2", name="Other widget", uom=self.uom)
        bill = self.hand_bill(order.lines.get(), "10", "5", order=order, item=other)
        try:
            bill.post()
        except ValidationError:
            return
        self.fail(f"Posted: billed WDG-2, cleared WDG-1's accrual (GRNI {self.balance(self.grni)}, "
                  f"WDG-1 line billed {order.lines.get().quantity_billed()}); expected refused")

    def test_the_same_invoice_number_in_other_case_is_refused(self):
        """INV-77 on file for V-1; inv-77 from V-1: refused."""
        order = self.order()
        self.receive(order, "10")
        self.hand_bill(order.lines.get(), "4", "5", order=order, reference="INV-77").post()
        try:
            self.hand_bill(order.lines.get(), "4", "5", order=order, reference="inv-77").post()
        except ValidationError:
            return
        self.fail(f"Both posted: payable {self.balance(self.payable)}; expected the second refused")


class OrderEditTests(ProbeCase):
    def test_a_received_lines_item_cannot_change(self):
        """10 of WDG-1 received; the line changed to another item: refused."""
        order = self.order()
        self.receive(order, "10")
        other = Item.objects.create(sku="WDG-2", name="Other widget", uom=self.uom)
        line = order.lines.get()
        line.item = other
        try:
            line.save()
        except ValidationError:
            return
        line = PurchaseOrderLine.objects.get(pk=line.pk)
        self.fail(f"Saved: line now {line.item.sku} with {line.quantity_received()} received; "
                  f"shelf WDG-1 {self.item.on_hand_at(self.warehouse)}, WDG-2 "
                  f"{other.on_hand_at(self.warehouse)}; expected refused")

    def test_a_billed_lines_discount_cannot_change(self):
        """Billed 10 @ 100 net 1,000; the order line then given 10% off: refused, as its price is."""
        order = self.order("10", "100")
        self.receive(order, "10")
        order.create_bill(self.payable, bill_date=JAN(10)).post()
        line = order.lines.get()
        line.discount_percent = Decimal("10")
        try:
            line.save()
        except ValidationError:
            return
        self.fail(f"Saved: order line net {PurchaseOrderLine.objects.get(pk=line.pk).net_amount()} "
                  "against 1,000.00 billed; expected refused")

    def test_a_received_orders_vendor_cannot_change(self):
        """V-1 delivered 10 @ 5; the order changed to V-2: refused."""
        order = self.order()
        self.receive(order, "10")
        order.vendor = self.other_vendor()
        try:
            order.save()
        except ValidationError:
            return
        bill = PurchaseOrder.objects.get(pk=order.pk).create_bill(self.payable, bill_date=JAN(10))
        self.fail(f"Saved: its bill is drafted to {bill.vendor.code} for goods V-1 delivered; expected refused")

    def test_a_received_orders_currency_cannot_change(self):
        """Received at USD 50; the order changed to EUR: refused."""
        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(currency=eur, rate=Decimal("90"), valid_from=JAN(1))
        order = self.order()
        self.receive(order, "10")
        order.currency = eur
        try:
            order.save()
        except ValidationError:
            return
        PurchaseOrder.objects.get(pk=order.pk).create_bill(self.payable, bill_date=JAN(10)).post()
        self.fail(f"Saved; its bill posted: payable {self.balance(self.payable)}, FX loss "
                  f"{self.balance(self.fx_loss)}, against goods accrued at 50.00; expected refused")


class AgreementTests(ProbeCase):
    def test_a_release_keeps_the_discount_agreed(self):
        """Blanket 10 @ 100 less 5% (950). Released in full: the order comes to 950.00."""
        blanket = BlanketOrder.objects.create(vendor=self.vendor, start_date=JAN(1),
                                              end_date=datetime.date(2026, 12, 31))
        line = BlanketOrderLine.objects.create(blanket=blanket, item=self.item, uom=self.uom,
                                               quantity=Decimal("10"), unit_price=Decimal("100"),
                                               discount_percent=Decimal("5"))
        blanket.confirm()
        order = blanket.release({line: Decimal("10")}, order_date=JAN(5))
        self.assertEqual(order.lines.get().net_amount(), Decimal("950.00"))

    def test_an_agreed_price_per_kg_prices_an_order_in_tonnes(self):
        """Agreed 100 a kg; 2 tonnes ordered with no price typed: 100,000 a tonne, 200,000.00."""
        kg = UnitOfMeasure.objects.create(code="kg", name="Kilogramme")
        tonne = UnitOfMeasure.objects.create(code="t", name="Tonne", base_unit=kg,
                                             conversion_factor=Decimal("1000"))
        granule = Item.objects.create(sku="PP-GRAN", name="PP granules", uom=kg)
        VendorPrice.objects.create(vendor=self.vendor, item=granule, currency=self.usd,
                                   unit_price=Decimal("100"))
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=JAN(1))
        line = PurchaseOrderLine.objects.create(order=order, item=granule, uom=tonne, quantity=Decimal("2"))
        self.assertEqual((line.unit_price, line.net_amount()), (Decimal("100000"), Decimal("200000.00")))

    def test_a_price_typed_over_the_agreed_one_needs_approval(self):
        """Agreed 5.00; the policy asks approval for a price typed by hand. Typed at 50.00: not confirmed unapproved."""
        from .models import PurchaseApprovalPolicy

        VendorPrice.objects.create(vendor=self.vendor, item=self.item, currency=self.usd, unit_price=Decimal("5"))
        PurchaseApprovalPolicy.objects.create(code="STD", name="Standard", require_approval_without_vendor_price=True)
        order = self.order("10", "50", confirm=False)
        try:
            order.confirm()
        except ValidationError:
            return
        self.fail(f"Confirmed unapproved at 10 x 50.00 = {order.total()} against 5.00 agreed; "
                  f"reasons {order.approval_reasons()}")

    def test_the_cheapest_vendor_is_chosen_in_one_currency(self):
        """V-1 agrees 300.00 in base; V-2 agrees EUR 4 at 90, which is 360.00. V-1 is the cheaper."""
        from .pricing import preferred_vendor

        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(currency=eur, rate=Decimal("90"), valid_from=JAN(1))
        other = self.other_vendor()
        VendorPrice.objects.create(vendor=self.vendor, item=self.item, currency=self.usd, unit_price=Decimal("300"))
        VendorPrice.objects.create(vendor=other, item=self.item, currency=eur, unit_price=Decimal("4"))
        self.assertEqual(preferred_vendor(self.item, JAN(5)).code, "V-1")


class CapitaliseTests(ProbeCase):
    def test_capitalising_a_received_machine_leaves_grni_and_stock_clear(self):
        """A stocked machine 1 @ 12,000 received and billed, then capitalised: GRNI 0, inventory 0, plant 12,000."""
        from apps.assets.models import AssetCategory

        acc = lambda code, name, kind: Account.objects.create(code=code, name=name, account_type=kind)  # noqa: E731
        plant = acc("1500", "Plant", AccountType.ASSET)
        category = AssetCategory.objects.create(
            code="PLANT", name="Plant", asset_account=plant,
            accumulated_account=acc("1590", "Accumulated", AccountType.ASSET),
            expense_account=acc("6100", "Depreciation", AccountType.EXPENSE),
            disposal_account=acc("6200", "Disposal", AccountType.EXPENSE), default_life_months=60)
        order = self.order("1", "12000")
        self.receive(order, "1")
        bill = order.create_bill(self.payable, bill_date=JAN(10))
        bill.post()
        try:
            bill.lines.get().capitalise_as_asset(category)
        except ValidationError:
            return
        observed = (self.balance(self.grni), self.balance(self.inventory), self.balance(plant))
        self.assertEqual(observed, (Decimal("0"), Decimal("0"), Decimal("12000.00")), "(GRNI, inventory, plant)")


class TdsProbeTests(ProbeCase):
    def test_a_bill_past_the_single_limit_is_taxed_alone(self):
        """
        194C at 2%: 20,000 (under 30,000, the year under 1 lakh: never taxed), then 35,000.
        Only the 35,000 is past a limit: base 35,000, tax 700.
        """
        tds_payable = Account.objects.create(code="2250", name="TDS payable", account_type=AccountType.LIABILITY)
        section = TdsSection.objects.create(
            code="194C", name="Contractors", rate_percent=Decimal("2"), no_pan_rate_percent=Decimal("20"),
            mode="whole", single_threshold=Decimal("30000"), annual_threshold=Decimal("100000"),
            payable_account=tds_payable)
        PartyTaxProfile.objects.create(party=self.vendor, pan="AAAPL1234C", tds_section=section)
        day = datetime.date(2026, 6, 10)
        bills = []
        for amount in ("20000", "35000"):
            bill = Bill.objects.create(vendor=self.vendor, bill_date=day, payable_account=self.payable)
            BillLine.objects.create(bill=bill, description="Loom fitting", quantity=Decimal("1"),
                                    unit_price=Decimal(amount), expense_account=self.expense)
            bill.post()
            bills.append(Bill.objects.get(pk=bill.pk))
        deduction = bills[1].deduct_tds()
        self.assertEqual((deduction.base, deduction.amount), (Decimal("35000.00"), Decimal("700.00")))


class MsmeTests(ProbeCase):
    def micro_bill(self, net_days=60):
        terms = PaymentTerms.objects.create(code=f"N{net_days}", name=f"Net {net_days}", net_days=net_days)
        PartyTaxProfile.objects.create(party=self.vendor, msme_category="micro", udyam_number="UDYAM-MH-26-0012345")
        bill = Bill.objects.create(vendor=self.vendor, bill_date=datetime.date(2026, 6, 1),
                                   payable_account=self.payable, payment_terms=terms)
        BillLine.objects.create(bill=bill, description="Bobbins", quantity=Decimal("1"),
                                unit_price=Decimal("1000"), expense_account=self.expense)
        bill.post()
        return Bill.objects.get(pk=bill.pk)

    def test_the_payment_run_lists_a_micro_vendors_bill_by_the_acts_45_days(self):
        """Micro vendor, net 60 from 1 June: the Act's day is 16 July. The run for 17 July lists it."""
        from .msme import msme_bills

        bill = self.micro_bill()
        (row,) = msme_bills(datetime.date(2026, 6, 1), datetime.date(2026, 6, 30), as_of=datetime.date(2026, 7, 17))
        self.assertEqual(row["due"], datetime.date(2026, 7, 16))
        listed = [entry["bill"].pk for run in payment_run(due_by=datetime.date(2026, 7, 17)) for entry in run["bills"]]
        self.assertIn(bill.pk, listed, f"run for 17 July lists {listed}; bill due_date {bill.due_date}")

    def test_a_bill_from_a_micro_vendor_stays_on_the_list_when_the_vendor_grows(self):
        """Booked from a micro vendor, unpaid at 31 March; the vendor is medium from April. Still at risk: 1,000."""
        from .msme import msme_bills

        self.micro_bill()
        profile = PartyTaxProfile.objects.get(party=self.vendor)
        profile.msme_category = "medium"
        profile.save()
        rows = msme_bills(datetime.date(2026, 4, 1), datetime.date(2027, 3, 31), as_of=datetime.date(2027, 3, 31))
        self.assertEqual([row["at_risk"] for row in rows], [Decimal("1000.00")])


class PrepaymentTests(ProbeCase):
    def test_a_drawdown_takes_the_prepayment_from_where_it_was_held(self):
        """15 paid up front into 1400; the setting then moved to 1401. Billed 50: 1400 and 1401 both 0."""
        held = Account.objects.create(code="1400", name="Vendor prepayments", account_type=AccountType.ASSET)
        company = Company.get()
        company.vendor_prepayment_account = held
        company.save()
        order = self.order()
        order.create_prepayment_bill(self.payable, percent=30).post()
        moved = Account.objects.create(code="1401", name="Vendor advances", account_type=AccountType.ASSET)
        company = Company.get()
        company.vendor_prepayment_account = moved
        company.save()
        self.receive(order, "10")
        order.create_bill(self.payable, bill_date=JAN(10)).post()
        self.assertEqual((self.balance(held), self.balance(moved)), (Decimal("0"), Decimal("0")),
                         "(1400, 1401)")


class TwoAtOnceScenarios:
    """Each builds a scenario and returns two calls that must not both succeed."""

    def typed_bills_on_one_line(self, name_the_order=False):
        order = self.order()
        self.receive(order, "10")
        line = order.lines.get()
        drafts = [self.hand_bill(line, "10", "5", order=order if name_the_order else None) for _ in range(2)]
        return [lambda pk=draft.pk: Bill.objects.get(pk=pk).post() for draft in drafts]

    def generated_bills_on_one_receipt(self):
        order = self.order()
        self.receive(order, "10")
        drafts = [order.create_bill(self.payable, bill_date=JAN(10)) for _ in range(2)]
        return [lambda pk=draft.pk: Bill.objects.get(pk=pk).post() for draft in drafts]

    def receipts_for_the_last_of_a_line(self):
        order = self.order()
        self.receive(order, "6")
        drafts = []
        for _ in range(2):
            receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=JAN(6))
            GoodsReceiptLine.objects.create(receipt=receipt, order_line=order.lines.get(),
                                            warehouse=self.warehouse, quantity_received=Decimal("4"))
            drafts.append(receipt)
        return [lambda pk=draft.pk: GoodsReceipt.objects.get(pk=pk).post() for draft in drafts]

    def award_and_order_one_request(self):
        from django.contrib.auth import get_user_model

        from .models import PurchaseRequisition, PurchaseRequisitionLine, RequestForQuotation

        employee = Party.objects.create(code="E-1", name="Dana")
        PartyRoleAssignment.objects.create(party=employee, role=PartyRole.EMPLOYEE)
        manager = get_user_model().objects.create_user(username="budget", password="x")
        requisition = PurchaseRequisition.objects.create(
            requested_by=employee, request_date=JAN(1), needed_by=datetime.date(2026, 2, 1))
        line = PurchaseRequisitionLine.objects.create(requisition=requisition, item=self.item, uom=self.uom,
                                                      quantity=Decimal("10"), estimated_price=Decimal("5"),
                                                      suggested_vendor=self.vendor)
        requisition.submit()
        requisition.approve(by=manager)
        rfq = requisition.create_rfq()
        rfq.issue()
        invitation = rfq.invited.get()
        invitation.quote(rfq.lines.get(), Decimal("4"))
        second = self.other_vendor()
        self.request_line = line
        return [
            lambda: PurchaseRequisition.objects.get(pk=requisition.pk).create_order(
                second, lines=[PurchaseRequisitionLine.objects.get(pk=line.pk)]),
            lambda: RequestForQuotation.objects.get(pk=rfq.pk).award(invitation),
        ]


    def bill_posted_as_a_line_is_added(self):
        order = self.order("20", "5")
        self.receive(order, "20")
        line = order.lines.get()
        draft = self.hand_bill(line, "10", "5", order=order)
        self.draft = draft

        def add_line():
            BillLine.objects.create(bill=Bill.objects.get(pk=draft.pk), order_line=line, item=self.item,
                                    quantity=Decimal("10"), unit_price=Decimal("5"), expense_account=self.expense)
        return [lambda: Bill.objects.get(pk=draft.pk).post(), add_line]

    def receipt_posted_as_a_line_is_added(self):
        order = self.order("20", "5")
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=JAN(5))
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=order.lines.get(), warehouse=self.warehouse,
                                        quantity_received=Decimal("10"))
        self.draft_receipt = receipt

        def add_line():
            GoodsReceiptLine.objects.create(receipt=GoodsReceipt.objects.get(pk=receipt.pk),
                                            order_line=order.lines.get(), warehouse=self.warehouse,
                                            quantity_received=Decimal("10"))
        return [lambda: GoodsReceipt.objects.get(pk=receipt.pk).post(), add_line]


class OneAfterTheOtherTests(TwoAtOnceScenarios, ProbeCase):
    """The same scenarios one after the other: the second is refused, so a race is all that lets it by."""

    def second_refused(self, calls):
        calls[0]()
        with self.assertRaises(ValidationError):
            calls[1]()

    def test_typed_bills(self):
        self.second_refused(self.typed_bills_on_one_line())

    def test_award_after_order(self):
        self.second_refused(self.award_and_order_one_request())


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class PurchasingProbeRaces(TwoAtOnceScenarios, TransactionTestCase):
    order, hand_bill, other_vendor, balance = (
        ProbeCase.order, ProbeCase.hand_bill, ProbeCase.other_vendor, ProbeCase.balance)
    receive = PurchasingLifecycleTestCase.receive

    def setUp(self):
        PurchasingLifecycleTestCase.setUp(self)
        probe_accounts(self)

    def outcome(self, sender, calls):
        from apps.e2e.tests_races import race

        return race(sender, *calls)

    def once(self, outcomes, *figures):
        self.assertEqual(sorted(o == "done" for o in outcomes), [False, True], (outcomes, figures))

    def test_two_typed_bills_naming_no_order_do_not_both_bill_the_last_of_a_line(self):
        from apps.accounting.models import JournalEntry

        outcomes = self.outcome(JournalEntry, self.typed_bills_on_one_line())
        self.once(outcomes, "payable", self.balance(self.payable), "GRNI", self.balance(self.grni))

    def test_two_typed_bills_naming_the_order_do_not_both_bill_the_last_of_a_line(self):
        from apps.accounting.models import JournalEntry

        outcomes = self.outcome(JournalEntry, self.typed_bills_on_one_line(name_the_order=True))
        self.once(outcomes, "payable", self.balance(self.payable))

    def test_two_generated_bills_do_not_both_bill_one_receipt(self):
        from apps.accounting.models import JournalEntry

        outcomes = self.outcome(JournalEntry, self.generated_bills_on_one_receipt())
        self.once(outcomes, "payable", self.balance(self.payable))

    def test_two_receipts_do_not_both_take_the_last_of_a_line(self):
        from apps.inventory.models import StockMovement

        outcomes = self.outcome(StockMovement, self.receipts_for_the_last_of_a_line())
        self.once(outcomes, "inventory", self.balance(self.inventory))

    def test_an_award_and_an_order_do_not_both_order_one_request(self):
        outcomes = self.outcome(PurchaseOrder, self.award_and_order_one_request())
        self.once(outcomes, "ordered", self.request_line.quantity_ordered())


    def test_a_line_is_not_added_to_a_bill_as_it_posts(self):
        from apps.accounting.models import JournalEntry

        outcomes = self.outcome((JournalEntry, BillLine), self.bill_posted_as_a_line_is_added())
        bill = Bill.objects.get(pk=self.draft.pk)
        self.once(outcomes, "lines", bill.lines.count(), "total", bill.total(), "payable", self.balance(self.payable))

    def test_a_line_is_not_added_to_a_receipt_as_it_posts(self):
        from apps.inventory.models import StockMovement

        outcomes = self.outcome((StockMovement, GoodsReceiptLine), self.receipt_posted_as_a_line_is_added())
        receipt = GoodsReceipt.objects.get(pk=self.draft_receipt.pk)
        self.once(outcomes, "lines", receipt.lines.count(), "received",
                  receipt.purchase_order.lines.get().quantity_received(), "inventory", self.balance(self.inventory))


class ForeignPrepaymentTests(ProbeCase):
    def test_a_euro_prepayment_debited_in_part_is_drawn_down_to_nothing(self):
        """
        EUR order 10 @ 5. 15 paid up front at 90 (1,350); 5 debited back (450, at 90); received at 90,
        billed at 95: 10 drawn down (900 off the held account). Held 0; the bill owes 40 EUR.
        """
        eur = Currency.objects.create(code="EUR", name="Euro")
        for day, rate in ((1, "90"), (10, "95")):
            ExchangeRate.objects.create(currency=eur, rate=Decimal(rate), valid_from=JAN(day))
        held = Account.objects.create(code="1400", name="Vendor prepayments", account_type=AccountType.ASSET)
        company = Company.get()
        company.vendor_prepayment_account = held
        company.save()
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


class PurchasingProbeRacesDryRun(PurchasingProbeRaces):
    """The race class's own fixture, its calls made one after the other on any database: proves the setup."""
    __unittest_skip__ = False

    def outcome(self, sender, calls):
        outcomes = []
        for call in calls:
            try:
                call()
                outcomes.append("done")
            except Exception as exc:  # noqa: BLE001 - recorded as the race helper records it
                outcomes.append(f"refused: {exc}")
        return outcomes
