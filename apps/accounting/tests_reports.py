"""
Financial statements.

Every module here posts to the ledger and nothing read it back. A
double-entry system that cannot produce a trial balance can record a
year of trading and answer nothing about it.

The tests that matter are the self-proving ones: a trial balance must
balance and a balance sheet must add up, after every kind of document
this system can produce.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.core.models import (
    Company,
    Currency,
    Party,
    PartyRole,
    PartyRoleAssignment,
    UnitOfMeasure,
)
from apps.inventory.models import Item, Warehouse

from .models import (
    Account,
    AccountingPeriod,
    AccountType,
    JournalEntry,
    JournalLine,
    Payment,
    PaymentDirection,
)
from .reports import (
    balance_sheet,
    profit_and_loss,
    retained_earnings,
    trial_balance,
)


class ReportTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        self.uom = UnitOfMeasure.objects.create(code="ea", name="Each")
        self.item = Item.objects.create(sku="W", name="Widget", uom=self.uom)
        self.warehouse = Warehouse.objects.create(code="W", name="Main")
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.bank = acc("1010", "Bank", AccountType.ASSET)
        self.ar = acc("1100", "AR", AccountType.ASSET)
        self.inventory = acc("1200", "Inventory", AccountType.ASSET)
        self.ap = acc("2000", "AP", AccountType.LIABILITY)
        self.grni = acc("2150", "GRNI", AccountType.LIABILITY)
        self.capital = acc("3000", "Share capital", AccountType.EQUITY)
        self.revenue = acc("4000", "Revenue", AccountType.INCOME)
        self.cogs = acc("5000", "Cost of sales", AccountType.EXPENSE)
        self.overheads = acc("6000", "Overheads", AccountType.EXPENSE)
        Company.objects.create(
            name="Test Co", base_currency=self.usd,
            default_inventory_account=self.inventory, default_cogs_account=self.cogs,
            grni_account=self.grni, fiscal_year_start_month=1,
        )

    def entry(self, date, *lines, memo="test"):
        entry = JournalEntry.objects.create(date=date, memo=memo)
        for account, debit, credit in lines:
            JournalLine.objects.create(
                entry=entry, account=account,
                debit=Decimal(debit), credit=Decimal(credit),
            )
        entry.post()
        return entry

    def capitalise(self, amount="100000", on=datetime.date(2026, 1, 1)):
        self.entry(on, (self.bank, amount, "0"), (self.capital, "0", amount))

    def sell(self, amount="1000", cost="600", on=datetime.date(2026, 2, 1)):
        self.entry(on, (self.ar, amount, "0"), (self.revenue, "0", amount))
        self.entry(on, (self.cogs, cost, "0"), (self.inventory, "0", cost))

    def spend(self, amount="250", on=datetime.date(2026, 2, 10)):
        self.entry(on, (self.overheads, amount, "0"), (self.bank, "0", amount))


class TrialBalanceTests(ReportTestCase):
    def test_it_balances(self):
        """If total debits do not equal total credits, the ledger is
        broken, not the report."""
        self.capitalise()
        self.sell()
        self.spend()

        result = trial_balance()

        self.assertTrue(result["balanced"])
        self.assertEqual(result["closing_debit"], result["closing_credit"])

    def test_an_empty_ledger_balances_too(self):
        self.assertTrue(trial_balance()["balanced"])
        self.assertEqual(trial_balance()["rows"], [])

    def test_each_account_carries_its_balance(self):
        self.capitalise("100000")
        self.sell("1000", "600")

        rows = {row["account"]: row for row in trial_balance()["rows"]}

        self.assertEqual(rows[self.bank]["balance"], Decimal("100000"))
        self.assertEqual(rows[self.ar]["balance"], Decimal("1000"))
        self.assertEqual(rows[self.revenue]["balance"], Decimal("-1000"))

    def test_it_reports_the_figure_a_reader_expects(self):
        """Revenue reads as a positive thousand, not a negative one."""
        self.sell("1000", "600")
        rows = {row["account"]: row for row in trial_balance()["rows"]}

        self.assertEqual(rows[self.revenue]["natural"], Decimal("1000"))
        self.assertEqual(rows[self.cogs]["natural"], Decimal("600"))

    def test_an_as_of_date_excludes_later_entries(self):
        self.capitalise(on=datetime.date(2026, 1, 1))
        self.sell(on=datetime.date(2026, 3, 1))

        result = trial_balance(as_of=datetime.date(2026, 2, 1))

        rows = {row["account"]: row for row in result["rows"]}
        self.assertNotIn(self.revenue, rows)
        self.assertTrue(result["balanced"])

    def test_a_window_separates_opening_from_movement(self):
        self.capitalise(on=datetime.date(2026, 1, 1))
        self.spend("250", on=datetime.date(2026, 2, 10))

        result = trial_balance(
            start=datetime.date(2026, 2, 1), as_of=datetime.date(2026, 2, 28)
        )
        rows = {row["account"]: row for row in result["rows"]}

        self.assertEqual(rows[self.bank]["opening"], Decimal("100000"))
        self.assertEqual(rows[self.bank]["credit"], Decimal("250"))
        self.assertEqual(rows[self.bank]["balance"], Decimal("99750"))

    def test_unposted_entries_are_not_counted(self):
        self.capitalise()
        JournalEntry.objects.create(date=datetime.date(2026, 2, 1), memo="draft")

        self.assertTrue(trial_balance()["balanced"])

    def test_dormant_accounts_are_hidden_unless_asked_for(self):
        self.capitalise()
        self.assertNotIn(
            self.revenue, [row["account"] for row in trial_balance()["rows"]]
        )
        self.assertIn(
            self.revenue,
            [row["account"] for row in trial_balance(include_zero=True)["rows"]],
        )


class ProfitAndLossTests(ReportTestCase):
    def test_it_nets_income_against_expenses(self):
        self.sell("1000", "600")
        self.spend("250")

        result = profit_and_loss(
            start=datetime.date(2026, 1, 1), end=datetime.date(2026, 12, 31)
        )

        self.assertEqual(result["income_total"], Decimal("1000"))
        self.assertEqual(result["expense_total"], Decimal("850"))
        self.assertEqual(result["net_profit"], Decimal("150"))

    def test_it_is_dated_by_period_not_cumulative(self):
        """Income and expense measure a span of time, which is the whole
        reason they do not appear on the balance sheet."""
        self.sell("1000", "600", on=datetime.date(2026, 2, 1))
        self.sell("400", "200", on=datetime.date(2026, 5, 1))

        first = profit_and_loss(
            start=datetime.date(2026, 1, 1), end=datetime.date(2026, 3, 31)
        )
        self.assertEqual(first["income_total"], Decimal("1000"))
        self.assertEqual(first["net_profit"], Decimal("400"))

    def test_balance_sheet_accounts_are_left_out(self):
        self.capitalise()
        result = profit_and_loss(
            start=datetime.date(2026, 1, 1), end=datetime.date(2026, 12, 31)
        )
        self.assertEqual(result["income_total"], Decimal("0"))
        self.assertEqual(result["expense_total"], Decimal("0"))

    def test_a_loss_reads_negative(self):
        self.spend("250")
        result = profit_and_loss(
            start=datetime.date(2026, 1, 1), end=datetime.date(2026, 12, 31)
        )
        self.assertEqual(result["net_profit"], Decimal("-250"))


class BalanceSheetTests(ReportTestCase):
    def test_it_balances(self):
        self.capitalise("100000")
        self.sell("1000", "600")
        self.spend("250")

        result = balance_sheet(as_of=datetime.date(2026, 12, 31))

        self.assertTrue(result["balanced"])
        self.assertEqual(result["asset_total"], result["total_liabilities_and_equity"])

    def test_profit_reaches_equity(self):
        """Without retained earnings the sheet cannot balance, because
        income and expense accounts carry nothing forward."""
        self.capitalise("100000")
        self.sell("1000", "600")

        result = balance_sheet(as_of=datetime.date(2026, 12, 31))

        self.assertEqual(result["profit_for_year"], Decimal("400"))
        self.assertEqual(result["equity_and_earnings"], Decimal("100400"))
        self.assertTrue(result["balanced"])

    def test_prior_years_are_brought_forward_separately(self):
        self.capitalise("100000", on=datetime.date(2025, 1, 1))
        self.sell("1000", "600", on=datetime.date(2025, 6, 1))
        self.sell("500", "300", on=datetime.date(2026, 6, 1))

        result = balance_sheet(
            as_of=datetime.date(2026, 12, 31), year_start=datetime.date(2026, 1, 1)
        )

        self.assertEqual(result["retained_brought_forward"], Decimal("400"))
        self.assertEqual(result["profit_for_year"], Decimal("200"))
        self.assertTrue(result["balanced"])

    def test_it_balances_with_nothing_in_it(self):
        result = balance_sheet(as_of=datetime.date(2026, 12, 31))
        self.assertTrue(result["balanced"])
        self.assertEqual(result["asset_total"], Decimal("0"))

    def test_the_fiscal_year_start_decides_the_split(self):
        company = Company.get()
        company.fiscal_year_start_month = 4
        company.save()
        self.sell("1000", "600", on=datetime.date(2026, 2, 1))
        self.sell("500", "300", on=datetime.date(2026, 6, 1))

        result = balance_sheet(as_of=datetime.date(2026, 12, 31))

        self.assertEqual(result["year_start"], datetime.date(2026, 4, 1))
        self.assertEqual(result["profit_for_year"], Decimal("200"))
        self.assertEqual(result["retained_brought_forward"], Decimal("400"))

    def test_retained_earnings_is_derived_not_posted(self):
        """There is no closing entry to forget, to run twice, or to
        disagree with the accounts it came from."""
        self.sell("1000", "600")
        self.assertEqual(retained_earnings(), Decimal("400"))
        self.assertFalse(
            JournalEntry.objects.filter(memo__icontains="retained").exists()
        )


class StatementsAgainstRealDocumentsTests(ReportTestCase):
    """The statements have to survive what the modules actually post,
    not only hand-written journals."""

    def test_a_full_trading_cycle_still_balances(self):
        from apps.purchasing.models import (
            Bill,
            BillLine,
            GoodsReceipt,
            GoodsReceiptLine,
            PurchaseOrder,
            PurchaseOrderLine,
        )
        from apps.sales.models import (
            Delivery,
            DeliveryLine,
            SalesOrder,
            SalesOrderLine,
        )

        vendor = Party.objects.create(code="V-1", name="Supplier", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=vendor, role=PartyRole.VENDOR)
        customer = Party.objects.create(code="C-1", name="Acme", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=customer, role=PartyRole.CUSTOMER)
        self.capitalise("100000")

        order = PurchaseOrder.objects.create(
            vendor=vendor, order_date=datetime.date(2026, 2, 1)
        )
        line = PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal("100"), unit_price=Decimal("6"),
        )
        order.confirm()
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 2, 5)
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=line, warehouse=self.warehouse,
            quantity_received=Decimal("100"),
        )
        receipt.post()
        bill = order.create_bill(self.ap, bill_date=datetime.date(2026, 2, 6))
        bill.post()

        sale = SalesOrder.objects.create(
            customer=customer, order_date=datetime.date(2026, 3, 1), currency=self.usd
        )
        sale_line = SalesOrderLine.objects.create(
            order=sale, item=self.item, uom=self.uom, quantity=Decimal("60"),
            unit_price=Decimal("15"), revenue_account=self.revenue,
        )
        sale.confirm()
        delivery = Delivery.objects.create(
            sales_order=sale, delivery_date=datetime.date(2026, 3, 2)
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=sale_line,
            warehouse=self.warehouse, quantity_shipped=Decimal("60"),
        )
        delivery.post()
        invoice = sale.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 3))
        invoice.post()

        payment = Payment.objects.create(
            party=customer, direction=PaymentDirection.RECEIPT,
            payment_date=datetime.date(2026, 3, 20), amount=Decimal("900"),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.ar,
        )
        payment.post()

        result = balance_sheet(as_of=datetime.date(2026, 12, 31))
        self.assertTrue(trial_balance()["balanced"])
        self.assertTrue(result["balanced"])

        # Sold 60 at 15 for 900, costing 6 each. Stock left is 40 at 6.
        pnl = profit_and_loss(
            start=datetime.date(2026, 1, 1), end=datetime.date(2026, 12, 31)
        )
        self.assertEqual(pnl["income_total"], Decimal("900"))
        self.assertEqual(pnl["expense_total"], Decimal("360"))
        self.assertEqual(pnl["net_profit"], Decimal("540"))
        rows = {row["account"]: row for row in trial_balance()["rows"]}
        self.assertEqual(rows[self.inventory]["balance"], Decimal("240"))


class PeriodCloseTests(ReportTestCase):
    def period(self, closed=False):
        period = AccountingPeriod.objects.create(
            name="February 2026", start_date=datetime.date(2026, 2, 1),
            end_date=datetime.date(2026, 2, 28),
        )
        if closed:
            period.close()
        return period

    def test_a_closed_period_refuses_further_postings(self):
        """Statements that can change after they are issued are not
        statements."""
        self.period(closed=True)

        with self.assertRaisesMessage(ValidationError, "is closed"):
            self.spend("250", on=datetime.date(2026, 2, 10))

    def test_an_open_period_does_not(self):
        self.period()
        self.spend("250", on=datetime.date(2026, 2, 10))
        self.assertTrue(trial_balance()["balanced"])

    def test_other_dates_are_unaffected(self):
        self.period(closed=True)
        self.spend("250", on=datetime.date(2026, 3, 10))
        self.assertTrue(trial_balance()["balanced"])

    def test_the_lock_covers_every_module(self):
        """One guard at the only chokepoint, rather than six that each
        need remembering."""
        self.period(closed=True)
        customer = Party.objects.create(code="C-1", name="Acme", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=customer, role=PartyRole.CUSTOMER)

        payment = Payment.objects.create(
            party=customer, direction=PaymentDirection.RECEIPT,
            payment_date=datetime.date(2026, 2, 15), amount=Decimal("100"),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.ar,
        )
        with self.assertRaisesMessage(ValidationError, "is closed"):
            payment.post()

    def test_reopening_lets_it_through_again(self):
        """Books do get reopened; a system that makes it impossible gets
        worked around by back-dating into the next period instead."""
        period = self.period(closed=True)
        period.reopen(note="Audit adjustment")

        self.spend("250", on=datetime.date(2026, 2, 10))

        self.assertTrue(trial_balance()["balanced"])

    def test_a_correction_can_be_dated_into_an_open_period(self):
        entry = self.entry(
            datetime.date(2026, 2, 10), (self.overheads, "250", "0"), (self.bank, "0", "250")
        )
        self.period(closed=True)

        reversal = entry.create_reversal(entry_date=datetime.date(2026, 3, 1))

        self.assertTrue(reversal.posted)
        self.assertTrue(trial_balance()["balanced"])

    def test_periods_cannot_overlap(self):
        self.period()
        overlapping = AccountingPeriod(
            name="Feb-Mar", start_date=datetime.date(2026, 2, 15),
            end_date=datetime.date(2026, 3, 15),
        )
        with self.assertRaisesMessage(ValidationError, "overlaps"):
            overlapping.clean()

    def test_a_period_cannot_end_before_it_starts(self):
        period = AccountingPeriod(
            name="Bad", start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 2, 1),
        )
        with self.assertRaisesMessage(ValidationError, "cannot end before it starts"):
            period.clean()

    def test_it_cannot_be_closed_twice(self):
        period = self.period(closed=True)
        with self.assertRaisesMessage(ValidationError, "already closed"):
            period.close()

    def test_an_open_period_cannot_be_reopened(self):
        period = self.period()
        with self.assertRaisesMessage(ValidationError, "not closed"):
            period.reopen()
