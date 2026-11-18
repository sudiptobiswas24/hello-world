"""
O250: purchasing 0093 makes each discount for paying early taken on a bill before it a row of its own, with its
entry, its day and its withdrawal, as sales 0086 does an invoice's; no payment is guessed.
"""

import datetime
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag

from apps.e2e.tests_races import fixture

BEFORE, AFTER = ("purchasing", "0092_a_deduction_keeps_its_certificate"), ("purchasing", "0093_a_discount_a_row")


@tag("migration")
class EachBillDiscountBecomesItsRowTests(TransactionTestCase):
    # Not serialized_rollback, as the other migration tests: the schema is put back to the latest instead.

    def setUp(self):
        from .tests_audit import VendorSettlementDiscountTests

        self.made = fixture(self, VendorSettlementDiscountTests)

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_a_discount_and_its_withdrawal_come_through_as_its_row(self):
        from apps.accounting.models import Account, AccountType, Payment, PaymentDirection

        from .models import BillDiscount, BillPayment

        made = self.made
        bill = made.discounted_bill()
        bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        paying = Payment.objects.create(party=made.vendor, direction=PaymentDirection.DISBURSEMENT,
                                        payment_date=datetime.date(2026, 1, 15), amount=Decimal("49"),
                                        currency=made.usd, bank_account=bank, counterpart_account=made.payable)
        paying.post()
        BillPayment.objects.create(bill=bill, payment=paying, amount=Decimal("49"))
        bill.take_settlement_discount(on_date=datetime.date(2026, 1, 15))
        paying.void(memo="Recalled", on_date=datetime.date(2026, 1, 25))
        kept = BillDiscount.objects.get(bill=bill)

        executor = MigrationExecutor(connection)
        executor.migrate([BEFORE])
        Bill = executor.loader.project_state([BEFORE]).apps.get_model("purchasing", "Bill")
        self.assertEqual(Bill.objects.filter(pk=bill.pk).values_list(
            "settlement_discount_amount", "settlement_discount_entry_id", "settlement_discount_withdrawn",
            "settlement_discount_withdrawal_entry_id").get(),
            (Decimal("1.00"), kept.journal_entry_id, Decimal("1.00"), kept.withdrawal_entry_id))
        MigrationExecutor(connection).migrate([AFTER])
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

        rows = [(row.payment_id, row.date, row.amount, row.journal_entry_id, row.withdrawn_amount,
                 row.withdrawal_entry_id) for row in BillDiscount.objects.all()]
        self.assertEqual(rows, [(None, datetime.date(2026, 1, 15), Decimal("1.00"), kept.journal_entry_id,
                                 Decimal("1.00"), kept.withdrawal_entry_id)])
