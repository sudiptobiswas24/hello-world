"""
Money moves only through a bank, a cash box, a card or an overdraft.

The account says which it is (holds_money). A payment, a bank statement
and the company's default bank name one; nothing else is one. And the
other side: an invoice's receivable, a bill's payable, the company's
settings and the tax accounts never name one, or what is owed and the
money it was paid with would share one balance.

1010 Bank and 2300 Company card hold money; 1100 AR, 2000 AP, 1500
Deposits paid (an asset), 6200 Travel and 4000 Sales do not. A receipt
of 500 posts Dr 1010 500.00 / Cr 1100 500.00; a vendor paid 300 by card,
Dr 2000 300.00 / Cr 2300 300.00.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase, tag
from rest_framework.test import APIClient

from apps.core.models import Company, Currency, Party, PartyRole, PartyRoleAssignment
from apps.purchasing.models import Bill
from apps.sales.models import Invoice, RecurringInvoice

from .models import Account, AccountType, BankStatement, JournalLine, Payment, PaymentDirection, Tax
from .tds import TdsSection

DAY = datetime.date(2026, 6, 1)


class MoneyTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        self.card = Account.objects.create(code="2300", name="Company card", account_type=AccountType.LIABILITY,
                                           holds_money=True)
        self.ar = Account.objects.create(code="1100", name="AR", account_type=AccountType.ASSET)
        self.ap = Account.objects.create(code="2000", name="AP", account_type=AccountType.LIABILITY)
        self.deposits_paid = Account.objects.create(code="1500", name="Deposits paid", account_type=AccountType.ASSET)
        self.travel = Account.objects.create(code="6200", name="Travel", account_type=AccountType.EXPENSE)
        self.sales = Account.objects.create(code="4000", name="Sales", account_type=AccountType.INCOME)
        Company.objects.create(name="Test Co", base_currency=self.usd, default_bank_account=self.bank,
                               default_receivable_account=self.ar, default_payable_account=self.ap)
        self.customer = self.party("C-1", "Acme", PartyRole.CUSTOMER)
        self.vendor = self.party("V-1", "Polymers Ltd", PartyRole.VENDOR)

    def party(self, code, name, role):
        party = Party.objects.create(code=code, name=name, default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=party, role=role)
        return party

    def receipt(self, through=None, against=None, amount="500"):
        fields = {"bank_account": through} if through is not None else {}
        if against is not None:
            fields["counterpart_account"] = against
        return Payment.objects.create(party=self.customer, direction=PaymentDirection.RECEIPT, payment_date=DAY,
                                      amount=Decimal(amount), currency=self.usd, **fields)

    def closed_cash(self):
        return Account.objects.create(code="1020", name="Old cash box", account_type=AccountType.ASSET,
                                      holds_money=True, is_active=False)

    @staticmethod
    def posted_lines(payment):
        return {(row.account.code, row.debit, row.credit)
                for row in JournalLine.objects.filter(entry=payment.journal_entry)}


class PaidThroughMoneyTests(MoneyTestCase):
    def test_not_through_an_expense_account(self):
        with self.assertRaisesMessage(ValidationError, "6200 - Travel is not a bank, cash or card account"):
            self.receipt(through=self.travel)
        self.assertFalse(Payment.objects.exists())

    def test_not_through_the_receivable(self):
        with self.assertRaisesMessage(ValidationError, "1100 - AR is not a bank, cash or card account"):
            self.receipt(through=self.ar, against=self.sales)
        self.assertFalse(Payment.objects.exists())

    def test_not_through_a_closed_account(self):
        with self.assertRaisesMessage(ValidationError, "1020 - Old cash box is no longer in use"):
            self.receipt(through=self.closed_cash())

    def test_not_out_of_the_account_it_settles(self):
        other = Account.objects.create(code="1030", name="Second bank", account_type=AccountType.ASSET,
                                       holds_money=True)
        with self.assertRaisesMessage(ValidationError, "is the account this payment settles as well"):
            self.receipt(through=other, against=other)

    def test_a_draft_is_not_moved_onto_an_expense(self):
        payment = self.receipt()
        payment.bank_account = self.travel
        with self.assertRaisesMessage(ValidationError, "is not a bank, cash or card account"):
            payment.save()
        self.assertEqual(Payment.objects.get(pk=payment.pk).bank_account, self.bank)

    def test_a_draft_through_an_account_closed_since_is_not_posted(self):
        cash = Account.objects.create(code="1020", name="Cash box", account_type=AccountType.ASSET, holds_money=True)
        payment = self.receipt(through=cash)
        cash.is_active = False
        cash.save()
        with self.assertRaisesMessage(ValidationError, "1020 - Cash box is no longer in use"):
            payment.post()
        payment.refresh_from_db()
        self.assertEqual((payment.posted, payment.journal_entry), (False, None))

    def test_a_receipt_into_the_bank(self):
        payment = self.receipt()
        payment.post()
        self.assertEqual(self.posted_lines(payment), {("1010", Decimal("500.00"), Decimal("0")),
                                                      ("1100", Decimal("0"), Decimal("500.00"))})

    def test_a_vendor_paid_by_card(self):
        payment = Payment.objects.create(party=self.vendor, direction=PaymentDirection.DISBURSEMENT, payment_date=DAY,
                                         amount=Decimal("300"), currency=self.usd, bank_account=self.card)
        payment.post()
        self.assertEqual(self.posted_lines(payment), {("2000", Decimal("300.00"), Decimal("0")),
                                                      ("2300", Decimal("0"), Decimal("300.00"))})

    def test_a_statement_is_of_an_account_money_moves_through(self):
        with self.assertRaisesMessage(ValidationError, "6200 - Travel is not a bank, cash or card account"):
            BankStatement.objects.create(bank_account=self.travel, start_date=DAY, end_date=DAY,
                                         opening_balance=Decimal("0"), closing_balance=Decimal("0"))
        BankStatement.objects.create(bank_account=self.bank, start_date=DAY, end_date=DAY,
                                     opening_balance=Decimal("0"), closing_balance=Decimal("0"))


class WhatHoldsMoneyTests(MoneyTestCase):
    def test_an_expense_does_not(self):
        with self.assertRaisesMessage(ValidationError, "is an asset, or a liability"):
            Account.objects.create(code="6300", name="Fuel", account_type=AccountType.EXPENSE, holds_money=True)

    def test_a_new_overdraft_does(self):
        overdraft = Account.objects.create(code="2310", name="Cash credit", account_type=AccountType.LIABILITY,
                                           holds_money=True)
        self.assertTrue(Account.objects.get(pk=overdraft.pk).holds_money)

    def test_a_bank_posted_through_stays_a_bank(self):
        self.receipt().post()
        self.bank.holds_money = False
        with self.assertRaisesMessage(ValidationError, "1010 - Bank has posted entries as a bank, cash or card account"):
            self.bank.save()
        self.assertTrue(Account.objects.get(pk=self.bank.pk).holds_money)

    def test_an_account_posted_to_does_not_become_one(self):
        self.receipt().post()
        self.ar.holds_money = True
        with self.assertRaisesMessage(ValidationError, "1100 - AR has posted entries as not one"):
            self.ar.save()

    def test_a_bank_posted_through_is_renamed(self):
        self.receipt().post()
        self.bank.name = "HDFC current account"
        self.bank.save()

    def test_not_one_the_company_keeps_what_customers_owe_on(self):
        debtors = Account.objects.create(code="1150", name="Debtors", account_type=AccountType.ASSET)
        company = Company.get()
        company.default_receivable_account = debtors
        company.save()
        debtors.holds_money = True
        with self.assertRaisesMessage(ValidationError, "1150 - Debtors is the default receivable account of Test Co"):
            debtors.save()


class WhatIsOwedIsNotWhereTheMoneyIsTests(MoneyTestCase):
    def test_an_invoice_is_not_receivable_in_the_bank(self):
        with self.assertRaisesMessage(ValidationError, "1010 - Bank is a bank, cash or card account; the receivable account"):
            Invoice.objects.create(customer=self.customer, invoice_date=DAY, currency=self.usd,
                                   receivable_account=self.bank)
        self.assertFalse(Invoice.objects.exists())

    def test_an_invoice_does_not_post_to_an_account_made_a_bank_since(self):
        invoice = Invoice.objects.create(customer=self.customer, invoice_date=DAY, currency=self.usd,
                                         receivable_account=self.deposits_paid)
        self.deposits_paid.holds_money = True
        self.deposits_paid.save()
        with self.assertRaisesMessage(ValidationError, "1500 - Deposits paid is a bank, cash or card account"):
            invoice.post()
        self.assertFalse(Invoice.objects.get(pk=invoice.pk).posted)

    def test_a_bill_is_not_payable_by_card(self):
        with self.assertRaisesMessage(ValidationError, "2300 - Company card is a bank, cash or card account; the payable account"):
            Bill.objects.create(vendor=self.vendor, bill_date=DAY, currency=self.usd, payable_account=self.card)
        self.assertFalse(Bill.objects.exists())

    def test_a_bill_does_not_post_to_an_account_made_a_bank_since(self):
        bill = Bill.objects.create(vendor=self.vendor, bill_date=DAY, currency=self.usd,
                                   payable_account=self.deposits_paid)
        self.deposits_paid.holds_money = True
        self.deposits_paid.save()
        with self.assertRaisesMessage(ValidationError, "1500 - Deposits paid is a bank, cash or card account"):
            bill.post()
        self.assertFalse(Bill.objects.get(pk=bill.pk).posted)

    def test_a_recurring_invoice_is_not_receivable_in_the_bank(self):
        with self.assertRaisesMessage(ValidationError, "the receivable account is an account of its own"):
            RecurringInvoice.objects.create(code="RENT", customer=self.customer, start_date=DAY, currency=self.usd,
                                            receivable_account=self.bank)

    def test_the_company_keeps_no_receivable_in_the_bank(self):
        company = Company.get()
        company.default_receivable_account = self.bank
        with self.assertRaisesMessage(ValidationError, "the default receivable account is an account of its own"):
            company.save()
        self.assertEqual(Company.get().default_receivable_account, self.ar)

    def test_nor_any_other_setting(self):
        company = Company.get()
        company.grni_account = self.card
        with self.assertRaisesMessage(ValidationError, "2300 - Company card is a bank, cash or card account"):
            company.save()

    def test_the_companys_bank_holds_money(self):
        company = Company.get()
        company.default_bank_account = self.ar
        with self.assertRaisesMessage(ValidationError, "1100 - AR is not a bank, cash or card account"):
            company.save()
        self.assertEqual(Company.get().default_bank_account, self.bank)

    def test_a_tds_section_owes_nothing_in_the_bank(self):
        with self.assertRaisesMessage(ValidationError, "the payable account is an account of its own"):
            TdsSection.objects.create(code="194C", name="Contractors", rate_percent=Decimal("1"),
                                      no_pan_rate_percent=Decimal("20"), payable_account=self.bank)

    def test_a_tax_is_not_collected_into_the_bank(self):
        with self.assertRaisesMessage(ValidationError, "the collected account is an account of its own"):
            Tax.objects.create(code="GST18", name="GST 18%", rate=Decimal("18"), scope="sales",
                               collected_account=self.bank)


class FromTheOfficeTests(MoneyTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.closed_cash()
        # Whoever records what customers pay.
        user = User.objects.create_user("receivables")
        user.groups.add(Group.objects.get(name="AR Manager"))
        self.api = APIClient()
        self.api.force_authenticate(user)

    def test_the_picker_offers_the_open_banks_cash_and_cards(self):
        response = self.api.get("/api/accounting/accounts/", {"holds_money": "true", "is_active": "true"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([row["code"] for row in response.json()], ["1010", "2300"])

    def test_a_receipt_through_travel_is_refused_beside_the_field(self):
        response = self.api.post("/api/accounting/payments/", {
            "party": self.customer.pk, "direction": "receipt", "payment_date": "2026-06-01", "amount": "500",
            "currency": self.usd.pk, "bank_account": self.travel.pk}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("is not a bank, cash or card account", " ".join(response.json()["bank_account"]))
        self.assertFalse(Payment.objects.exists())

    def test_a_receipt_into_the_card_is_recorded(self):
        response = self.api.post("/api/accounting/payments/", {
            "party": self.customer.pk, "direction": "receipt", "payment_date": "2026-06-01", "amount": "500",
            "currency": self.usd.pk, "bank_account": self.card.pk}, format="json")
        self.assertEqual((response.status_code, response.json().get("bank_account_name")), (201, "Company card"),
                         response.content)


@tag("migration")
class MoneyAlreadyMovedMigrationTests(TransactionTestCase):
    """
    Upgraded, the accounts money already went through hold it: a payment's
    bank, a statement's, the company's, a claim's and a challan's. Not the
    receivable a payment was once put through, not an expense, and not an
    account nothing moved through.
    """

    before = [("accounting", "0022_statement_line_returns_a_payment"), ("hr", "0018_people"),
              ("purchasing", "0057_returned_payment_releases_fx")]
    after = [("accounting", "0023_account_holds_money"), ("hr", "0019_claims_paid_from_hold_money"),
             ("purchasing", "0058_challans_paid_from_hold_money")]

    def test_where_money_went_is_marked_and_nothing_else(self):
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        apps = executor.loader.project_state(self.before).apps
        Account_ = apps.get_model("accounting", "Account")

        def account(code, kind):
            return Account_.objects.create(code=code, name=code, account_type=kind)

        bank, statement_bank, challan_bank = (account(code, "asset") for code in ("1010", "1020", "1030"))
        account("1040", "asset")  # nothing moves through it
        card, ar, ap, travel = account("2300", "liability"), account("1100", "asset"), account("2000", "liability"), \
            account("6200", "expense")
        usd = apps.get_model("core", "Currency").objects.create(code="USD", name="USD", is_base=True)
        apps.get_model("core", "Company").objects.create(name="Test Co", base_currency=usd, default_bank_account=bank,
                                                          default_receivable_account=ar, default_payable_account=ap)
        Party_ = apps.get_model("core", "Party")
        customer = Party_.objects.create(code="C-1", name="Acme")
        for through, against in ((bank, ar), (ar, bank), (travel, ar)):
            apps.get_model("accounting", "Payment").objects.create(
                party=customer, direction="receipt", payment_date=DAY, amount=Decimal("1"), bank_account=through,
                counterpart_account=against)
        apps.get_model("accounting", "BankStatement").objects.create(
            bank_account=statement_bank, start_date=DAY, end_date=DAY, opening_balance=0, closing_balance=0)
        employee = apps.get_model("hr", "Employee").objects.create(
            party=Party_.objects.create(code="E-1", name="Riley"), employee_number="E1", hire_date=DAY)
        apps.get_model("hr", "ExpenseClaim").objects.create(employee=employee, claim_date=DAY, purpose="Visit",
                                                             status="paid", paid_from=card)
        section = apps.get_model("accounting", "TdsSection").objects.create(
            code="194C", name="Contractors", rate_percent=1, no_pan_rate_percent=20)
        apps.get_model("purchasing", "TdsChallan").objects.create(
            section=section, month=DAY, date=DAY, bank_account=challan_bank, challan_number="1", bsr_code="0510308",
            amount=Decimal("1"), journal_entry=apps.get_model("accounting", "JournalEntry").objects.create(date=DAY))

        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        apps = executor.loader.project_state(self.after).apps
        marked = apps.get_model("accounting", "Account").objects.filter(holds_money=True)
        self.assertEqual(sorted(marked.values_list("code", flat=True)), ["1010", "1020", "1030", "2300"])
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
