"""
What the plant owes vendors, and how late, in rupees (O416).

ap_aging and vendor_balance added each bill's amount due in its own
currency: an unpaid USD 50,000 bill counted 50,000 beside rupees where the
payable held 41,62,500.00. Each figure is now the document's due at the rate
it posted at, as the payable holds it, through accounting.balances, which
sales asks too. Every figure is worked in calc_fix_balances/o416.py:

  - USD 50,000 on 10 Jan at 83.25 is 41,62,500.00; 25,000 paid on 20 Jan at
    84.00 clears 20,81,250.00 of it and realises 18,750.00 of loss; the
    25,000 left reads 20,81,250.00, the payable's balance;
  - a debit note of 10,000 USD at the bill's rate takes 8,32,500.00 off:
    15,000 left reads 12,48,750.00;
  - paid 1,200 USD at 84.00 against a 1,000 USD bill at 83.25: 200 USD on
    account reads -16,800.00, the payable's debit;
  - restated at 84.12 on 31 Jan, the open 50,000 reads 41,62,500.00 and the
    restatement's 43,500.00 beside it, 42,06,000.00 as the payable holds that
    day; on 1 Feb it has reversed and the aging is back to 41,62,500.00.
"""

import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.db.models import Sum
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType, JournalLine, Payment, PaymentDirection
from apps.core.models import Company, Currency, ExchangeRate

from .models import Bill, BillLine, BillPayment, ap_aging, vendor_balance
from .tests_lifecycle import PurchasingLifecycleTestCase

JAN = lambda day: datetime.date(2026, 1, day)


class BalancesInBaseCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        # The fixture's base currency, called what the plant's is.
        self.inr = self.usd
        self.inr.code, self.inr.name = "INR", "Indian Rupee"
        self.inr.save()
        self.dollar = Currency.objects.create(code="USD", name="US Dollar")
        ExchangeRate.objects.create(currency=self.dollar, rate=D("83.25"), valid_from=JAN(1))
        ExchangeRate.objects.create(currency=self.dollar, rate=D("84.00"), valid_from=JAN(15))
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        self.fx_gain = Account.objects.create(code="4900", name="Exchange gain", account_type=AccountType.INCOME)
        self.fx_loss = Account.objects.create(code="5900", name="Exchange loss", account_type=AccountType.EXPENSE)
        company = Company.get()
        company.fx_gain_account, company.fx_loss_account = self.fx_gain, self.fx_loss
        company.save()

    def dollar_bill(self, quantity="10000", price="5"):
        bill = Bill.objects.create(vendor=self.vendor, bill_date=JAN(10), payable_account=self.payable,
                                   currency=self.dollar)
        BillLine.objects.create(bill=bill, item=self.item, quantity=D(quantity), unit_price=D(price),
                                expense_account=self.expense)
        bill.post()
        return bill

    def pay(self, amount, bill=None, applied=None, on=JAN(20)):
        payment = Payment.objects.create(party=self.vendor, direction=PaymentDirection.DISBURSEMENT, payment_date=on,
                                         amount=D(amount), currency=self.dollar, bank_account=self.bank,
                                         counterpart_account=self.payable)
        payment.post()
        if bill is not None:
            BillPayment.objects.create(bill=bill, payment=payment, amount=D(applied or amount), date=on)
        return payment

    def owed(self, account, by=None):
        """What the account holds as owed by the plant (credit less debit), by a day."""
        lines = JournalLine.objects.filter(account=account, entry__posted=True)
        if by is not None:
            lines = lines.filter(entry__date__lte=by)
        rows = lines.aggregate(debit=Sum("debit"), credit=Sum("credit"))
        return (rows["credit"] or D("0")) - (rows["debit"] or D("0"))

    def total(self, aging):
        return sum((bucket["total"] for bucket in aging.values()), D("0"))


class TheAgingReadsWhatThePayableHoldsTests(BalancesInBaseCase):
    def test_half_paid_the_half_left_is_at_the_rate_it_was_booked(self):
        bill = self.dollar_bill()
        self.assertEqual(self.owed(self.payable), D("4162500.00"))
        self.pay("25000", bill)
        aging = ap_aging(as_of=datetime.date(2026, 3, 1))  # due 9 Feb: 20 days late
        [row] = aging["1-30"]["bills"]
        self.assertEqual((row["currency"], row["due_in_currency"], row["amount_due"]),
                         ("USD", D("25000.00"), D("2081250.00")))
        self.assertEqual((self.total(aging), vendor_balance(self.vendor), self.owed(self.payable)),
                         (D("2081250.00"), D("2081250.00"), D("2081250.00")))
        # The difference was realised when the half was paid, not left on the payable.
        self.assertEqual(-self.owed(self.fx_loss), D("18750.00"))

    def test_a_multi_line_bill_reads_the_base_its_payable_holds(self):
        """
        O423, the payable's mirror (calc_fix_logic/o423.py): three lines of USD 10.01 at 83.25 book 833.33
        each, 2,499.99, where 30.03 x 83.25 reads 2,500.00. Paid 10.00 at 84.00, its slice 832.50 clears and
        1,667.49 stays held, where 20.03 x 83.25 reads 1,667.50.
        """
        bill = Bill.objects.create(vendor=self.vendor, bill_date=JAN(10), payable_account=self.payable,
                                   currency=self.dollar)
        for _ in range(3):
            BillLine.objects.create(bill=bill, item=self.item, quantity=D("1"), unit_price=D("10.01"),
                                    expense_account=self.expense)
        bill.post()
        on = datetime.date(2026, 3, 1)
        self.assertEqual((bill.total(), self.owed(self.payable)), (D("30.03"), D("2499.99")))
        self.assertEqual((self.total(ap_aging(as_of=on)), vendor_balance(self.vendor)), (D("2499.99"), D("2499.99")))
        self.pay("10.00", bill)
        [row] = ap_aging(as_of=on)["1-30"]["bills"]
        self.assertEqual((row["due_in_currency"], row["amount_due"], vendor_balance(self.vendor),
                          self.owed(self.payable)), (D("20.03"), D("1667.49"), D("1667.49"), D("1667.49")))

    def test_a_debit_note_comes_off_at_the_rate_it_was_booked(self):
        bill = self.dollar_bill()
        self.pay("25000", bill)
        bill.create_debit_note(quantities={bill.lines.get(): D("2000")}, on_date=JAN(25))
        aging = ap_aging(as_of=datetime.date(2026, 3, 1))
        self.assertEqual((self.total(aging), vendor_balance(self.vendor), self.owed(self.payable)),
                         (D("1248750.00"), D("1248750.00"), D("1248750.00")))

    def test_a_debit_note_partly_absorbed_is_restated_once(self):
        """
        O424, the payable's mirror (calc_fix_logic/o424.py): USD 1,000 at 83.25 (83,250.00), 600 paid
        (clearing 49,950.00), a debit note of 700 (58,275.00) absorbs the 400 left and is owed 300 back: the
        payable holds -24,975.00, and restated at 84.12 the 300 is -25,236.00. The note was carried at all it
        booked, and the payable read 8,064.00 on the restatement's day.
        """
        from apps.accounting.restatement import Restatement

        bill = self.dollar_bill("200", "5")
        self.pay("600", bill)
        bill.create_debit_note(quantities={bill.lines.get(): D("140")}, on_date=JAN(25))
        self.assertEqual(self.owed(self.payable), D("-24975.00"))
        Restatement.make(JAN(31), rates={"USD": "84.12"})
        self.assertEqual((self.owed(self.payable, by=JAN(31)), self.total(ap_aging(as_of=JAN(31)))),
                         (D("-25236.00"), D("-25236.00")))

    def test_a_vendor_overpaid_in_dollars_reads_what_the_payment_booked(self):
        bill = self.dollar_bill("200", "5")  # 1,000 USD
        self.pay("1200", bill, applied="1000")
        aging = ap_aging(as_of=datetime.date(2026, 3, 1))
        [row] = aging["credits"]["bills"]
        self.assertEqual((row["currency"], row["due_in_currency"], row["amount_due"]),
                         ("USD", D("-200.00"), D("-16800.00")))
        self.assertEqual((self.total(aging), vendor_balance(self.vendor), self.owed(self.payable)),
                         (D("-16800.00"), D("-16800.00"), D("-16800.00")))

    def test_on_a_restatement_s_day_it_is_one_more_line_and_the_next_day_none(self):
        from apps.accounting.restatement import Restatement

        self.dollar_bill()
        restatement = Restatement.make(JAN(31), rates={"USD": "84.12"})
        aging = ap_aging(as_of=JAN(31))
        [line] = aging["restated"]["bills"]
        self.assertEqual((line["restatement"], line["amount_due"]), (restatement, D("43500.00")))
        self.assertEqual((self.total(aging), self.owed(self.payable, by=JAN(31))), (D("4206000.00"), D("4206000.00")))
        after = ap_aging(as_of=datetime.date(2026, 2, 1))
        self.assertNotIn("restated", after)
        self.assertEqual((self.total(after), self.owed(self.payable, by=datetime.date(2026, 2, 1))),
                         (D("4162500.00"), D("4162500.00")))

    def test_the_ap_manager_reads_both_figures(self):
        bill = self.dollar_bill()
        self.pay("25000", bill)
        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("ap-manager")
        user.groups.add(Group.objects.get(name="AP Manager"))
        client = APIClient()
        client.force_authenticate(user)
        response = client.get("/api/purchasing/purchasing-reports/aging/", {"as_of": "2026-03-01"})
        self.assertEqual(response.status_code, 200, response.content)
        [row] = response.json()["1-30"]["bills"]
        self.assertEqual((row["currency"], row["due_in_currency"], row["amount_due"], response.json()["1-30"]["total"]),
                         ("USD", "25000.00", "2081250.00", "2081250.00"))
        balance = client.get("/api/purchasing/purchasing-reports/vendor-balance/", {"vendor": self.vendor.pk})
        self.assertEqual((balance.status_code, balance.json().get("balance")), (200, "2081250.00"), balance.content)
