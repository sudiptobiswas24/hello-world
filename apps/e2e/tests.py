"""
Polymer in, cash out, and every clearing account back at nothing.

2,700-odd tests check each module's arithmetic. None of them checked the
seams, which is where the last two ledger defects were found: a foreign
purchase that never converted and a discount taken twice both passed
every module's own tests, and both left money in goods received not
invoiced for ever. This follows one run of tape through the whole
company and then asks the ledger the only questions that matter.

The story, all hand-checked:

    buy   970 kg polymer at €1.00        received 5 Jan at 1.2   1,164.00
          30 kg masterbatch at 200 less 10%, in rupees             5,400.00
    bill  polymer on 10 Jan at 1.3: owed 1,261.00, exchange loss       97.00
          masterbatch at the agreed net price: nothing left over
    make  1,000 kg of tape: 970 + 30 issued                        6,564.00
          two hours planned on the extruder at 300 an hour           600.00
          booked 130 minutes, so 650 charged and 50 over
          output received at the planned 7.164 a kilo             7,164.00
          closed: the 50 to conversion variance, nothing left in WIP
    sell  1,000 kg at 10.00                                       10,000.00
          30 per cent deposit taken and paid                       3,000.00
          delivered: cost of sales                                 7,164.00
          invoiced, deposit applied, the 7,000 left paid
    pay   polymer on 16 Jan at 1.25: 1,212.50 out, exchange gain      48.50
          masterbatch                                              5,400.00

Profit: 10,000 − 7,164 − 50 + 650 − 97 + 48.50 = 3,387.50, where the
650 is the extruder's time absorbed into stock (its wages and power are
somebody else's entry). The bank: 3,000 + 7,000 − 1,212.50 − 5,400 =
3,387.50. Nothing else on the balance sheet.
"""

import datetime
from decimal import Decimal

from django.db.models import Sum
from django.test import TestCase

from apps.accounting.models import (
    Account,
    AccountType,
    JournalLine,
    Payment,
    PaymentDirection,
)
from apps.core.models import (
    Company,
    Currency,
    ExchangeRate,
    Party,
    PartyRole,
    PartyRoleAssignment,
    PaymentTerms,
    UnitOfMeasure,
    UnitOfMeasureCategory,
)
from apps.inventory.models import Item, Warehouse
from apps.manufacturing.bom import BillOfMaterials, BomComponent
from apps.manufacturing.orders import (
    ManufacturingSettings,
    MaterialIssue,
    MaterialIssueLine,
    ProductionEntry,
    TimeBooking,
    WorkCentre,
    WorkOrder,
    WorkOrderStatus,
)
from apps.manufacturing.routing import Routing, RoutingOperation
from apps.purchasing.models import (
    Bill,
    BillLine,
    BillPayment,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
)
from apps.sales.models import (
    Delivery,
    DeliveryLine,
    InvoicePayment,
    SalesOrder,
    SalesOrderLine,
)

JAN = lambda day: datetime.date(2026, 1, day)


class FromPolymerToCashTests(TestCase):
    def setUp(self):
        self.inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        self.eur = Currency.objects.create(code="EUR", name="Euro")
        for day, rate in ((1, "1.2"), (8, "1.3"), (15, "1.25")):
            ExchangeRate.objects.create(
                currency=self.eur, rate=Decimal(rate), valid_from=JAN(day)
            )
        acc = lambda code, name, kind, **more: Account.objects.create(
            code=code, name=name, account_type=kind, **more
        )
        A, L, I, E = (AccountType.ASSET, AccountType.LIABILITY,
                      AccountType.INCOME, AccountType.EXPENSE)
        self.bank = acc("1010", "Bank", A, holds_money=True)
        self.ar = acc("1100", "Receivables", A)
        self.inventory = acc("1200", "Inventory", A)
        self.wip = acc("1250", "Work in progress", A)
        self.ap = acc("2000", "Payables", L)
        self.grni = acc("2150", "Goods received not invoiced", L)
        self.deposits = acc("2300", "Customer deposits", L)
        self.revenue = acc("4000", "Sales", I)
        self.fx_gain = acc("4900", "Exchange gain", I)
        self.cogs = acc("5000", "Cost of sales", E)
        self.material_variance = acc("5100", "Material variance", E)
        self.conversion_variance = acc("5110", "Conversion variance", E)
        self.ppv = acc("5150", "Price variance", E)
        self.scrap = acc("5200", "Scrap", E)
        self.absorbed = acc("5300", "Conversion absorbed", E)
        self.fx_loss = acc("5900", "Exchange loss", E)
        Company.objects.create(
            name="Deccan Polysacks", base_currency=self.inr,
            default_inventory_account=self.inventory,
            default_cogs_account=self.cogs, grni_account=self.grni,
            customer_deposit_account=self.deposits,
            purchase_price_variance_account=self.ppv,
            fx_gain_account=self.fx_gain, fx_loss_account=self.fx_loss,
        )
        ManufacturingSettings.objects.create(
            wip_account=self.wip, variance_account=self.material_variance,
            scrap_account=self.scrap, conversion_absorbed_account=self.absorbed,
            conversion_variance_account=self.conversion_variance,
        )

        terms = PaymentTerms.objects.create(code="N30", name="Net 30", net_days=30)
        self.polymer_vendor = self.party("V-EU", "Borealis", PartyRole.VENDOR,
                                         self.eur, terms)
        self.mb_vendor = self.party("V-IN", "Colourtech", PartyRole.VENDOR,
                                    self.inr, terms)
        self.customer = self.party("C-1", "UltraTech Cement", PartyRole.CUSTOMER,
                                   self.inr, terms)

        self.kg = UnitOfMeasure.objects.create(
            code="kg", name="Kilogram", category=UnitOfMeasureCategory.WEIGHT
        )
        self.plant = Warehouse.objects.create(code="P", name="Plant")
        self.polymer = Item.objects.create(sku="PP", name="PP homopolymer", uom=self.kg)
        self.masterbatch = Item.objects.create(sku="MB", name="Masterbatch", uom=self.kg)
        self.tape = Item.objects.create(sku="TAPE", name="PP tape", uom=self.kg)

        extruder = WorkCentre.objects.create(
            code="EXT-1", name="Extruder", labour_rate_per_hour=Decimal("300"),
        )
        routing = Routing.objects.create(code="R-TAPE", name="Tape")
        RoutingOperation.objects.create(
            routing=routing, sequence=10, name="Extrude", work_centre=extruder,
            units_per_hour=Decimal("500"), rate_uom=self.kg,
        )
        self.bom = BillOfMaterials.objects.create(
            item=self.tape, quantity_produced=Decimal("1000"), uom=self.kg,
            routing=routing,
        )
        for number, (item, quantity) in enumerate(
            ((self.polymer, "970"), (self.masterbatch, "30")), start=1
        ):
            BomComponent.objects.create(
                bom=self.bom, item=item, quantity=Decimal(quantity),
                uom=self.kg, line_number=number,
            )

    def party(self, code, name, role, currency, terms):
        party = Party.objects.create(
            code=code, name=name, default_currency=currency,
            payment_terms=terms,
        )
        PartyRoleAssignment.objects.create(party=party, role=role)
        return party

    def balance(self, account):
        rows = JournalLine.objects.filter(
            account=account, entry__posted=True
        ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))

    # -- the story ------------------------------------------------------

    def buy(self, vendor, currency, item, quantity, price, discount="0"):
        order = PurchaseOrder.objects.create(
            vendor=vendor, order_date=JAN(2), currency=currency,
        )
        line = PurchaseOrderLine.objects.create(
            order=order, item=item, uom=self.kg, quantity=Decimal(quantity),
            unit_price=Decimal(price), discount_percent=Decimal(discount),
        )
        order.confirm()
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=JAN(5)
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=line, warehouse=self.plant,
            quantity_received=Decimal(quantity),
        )
        receipt.post()
        bill = Bill.objects.create(
            vendor=vendor, bill_date=JAN(10), payable_account=self.ap,
            purchase_order=order, currency=currency,
        )
        BillLine.objects.create(
            bill=bill, item=item, order_line=line, quantity=Decimal(quantity),
            unit_price=Decimal(price), discount_percent=Decimal(discount),
        )
        bill.post()
        return bill

    def pay_vendor(self, bill, currency, amount, day):
        payment = Payment.objects.create(
            party=bill.vendor, direction=PaymentDirection.DISBURSEMENT,
            payment_date=JAN(day), amount=Decimal(amount), currency=currency,
            bank_account=self.bank, counterpart_account=self.ap,
        )
        payment.post()
        BillPayment.objects.create(bill=bill, payment=payment, amount=Decimal(amount))

    def collect(self, invoice, amount, day):
        payment = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.RECEIPT,
            payment_date=JAN(day), amount=Decimal(amount), currency=self.inr,
            bank_account=self.bank, counterpart_account=self.ar,
        )
        payment.post()
        InvoicePayment.objects.create(
            invoice=invoice, payment=payment, amount=Decimal(amount)
        )

    def make_tape(self):
        run = WorkOrder.objects.create(
            item=self.tape, bom=self.bom, quantity_ordered=Decimal("1000"),
            uom=self.kg, warehouse=self.plant, scheduled_end=JAN(12),
        )
        run.release(JAN(11))
        issue = MaterialIssue.objects.create(
            work_order=run, issue_date=JAN(11), warehouse=self.plant,
        )
        for number, component in enumerate(run.components.all(), start=1):
            MaterialIssueLine.objects.create(
                issue=issue, item=component.item,
                quantity=component.quantity_required, uom=component.uom,
                line_number=number,
            )
        issue.post()
        booking = TimeBooking.objects.create(
            work_order=run, operation=run.operations.get(),
            booking_date=JAN(11), minutes=Decimal("130"),
        )
        booking.post()
        ProductionEntry.objects.create(
            work_order=run, entry_date=JAN(12), warehouse=self.plant,
            quantity_produced=Decimal("1000"), uom=self.kg,
        ).post()
        run.close(JAN(12))
        return run

    def sell_tape(self):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=JAN(3), currency=self.inr,
        )
        SalesOrderLine.objects.create(
            order=order, item=self.tape, uom=self.kg, quantity=Decimal("1000"),
            unit_price=Decimal("10"), revenue_account=self.revenue,
        )
        order.confirm()
        deposit = order.create_down_payment_invoice(self.ar, percent=30)
        deposit.post()
        self.collect(deposit, "3000", 4)
        delivery = Delivery.objects.create(sales_order=order, delivery_date=JAN(20))
        DeliveryLine.objects.create(
            delivery=delivery, order_line=order.lines.get(), warehouse=self.plant,
            quantity_shipped=Decimal("1000"),
        )
        delivery.post()
        invoice = order.create_invoice(self.ar, invoice_date=JAN(21))
        invoice.post()
        self.collect(invoice, "7000", 25)
        return invoice

    def run_the_company(self):
        polymer_bill = self.buy(
            self.polymer_vendor, self.eur, self.polymer, "970", "1.00"
        )
        mb_bill = self.buy(
            self.mb_vendor, self.inr, self.masterbatch, "30", "200", discount="10"
        )
        run = self.make_tape()
        invoice = self.sell_tape()
        self.pay_vendor(polymer_bill, self.eur, "970", 16)
        self.pay_vendor(mb_bill, self.inr, "5400", 16)
        return run, invoice

    # -- what the ledger must say --------------------------------------

    def test_every_clearing_account_is_back_at_nothing(self):
        run, _invoice = self.run_the_company()
        for account in (self.grni, self.wip, self.deposits, self.ar, self.ap):
            self.assertEqual(self.balance(account), Decimal("0"), account.name)
        self.assertEqual(run.status, WorkOrderStatus.CLOSED)

    def test_the_shelf_and_the_ledger_agree_and_are_both_empty(self):
        self.run_the_company()
        for item in (self.polymer, self.masterbatch, self.tape):
            self.assertEqual(item.on_hand_at(self.plant), Decimal("0"), item.sku)
            self.assertEqual(item.stock_value_at(self.plant), Decimal("0"), item.sku)
        self.assertEqual(self.balance(self.inventory), Decimal("0"))

    def test_each_difference_landed_where_it_belongs(self):
        self.run_the_company()
        self.assertEqual(self.balance(self.fx_loss), Decimal("97.00"))
        self.assertEqual(self.balance(self.fx_gain), Decimal("-48.50"))
        self.assertEqual(self.balance(self.ppv), Decimal("0"))
        self.assertEqual(self.balance(self.conversion_variance), Decimal("50.00"))
        self.assertEqual(self.balance(self.material_variance), Decimal("0"))
        self.assertEqual(self.balance(self.absorbed), Decimal("-650.00"))
        self.assertEqual(self.balance(self.cogs), Decimal("7164.00"))
        self.assertEqual(self.balance(self.revenue), Decimal("-10000.00"))

    def test_the_profit_is_the_cash(self):
        """With nothing else left on the balance sheet, they must agree."""
        self.run_the_company()
        self.assertEqual(self.balance(self.bank), Decimal("3387.50"))
        profit = -sum(
            (self.balance(account) for account in Account.objects.filter(
                account_type__in=(AccountType.INCOME, AccountType.EXPENSE)
            )),
            Decimal("0"),
        )
        self.assertEqual(profit, Decimal("3387.50"))

    def test_the_books_balance(self):
        self.run_the_company()
        totals = JournalLine.objects.filter(entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit")
        )
        self.assertEqual(totals["debit"], totals["credit"])
