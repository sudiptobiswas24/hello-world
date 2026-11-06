from decimal import Decimal as D

from apps.accounting.models import Account, AccountType
from apps.core.models import Company
from apps.inventory.models import StockMovement

from .tests_subcontract import SubcontractTestCase


class Awkward(SubcontractTestCase):
    def setUp(self):
        super().setUp()
        self.variance = Account.objects.create(code="5900", name="PPV", account_type=AccountType.EXPENSE)
        Company.objects.update(purchase_price_variance_account=self.variance)

    def stock_value(self):
        return sum(i.valuation_at(w)[1] for i in (self.frame, self.motor, self.assembly) for w in (self.warehouse, self.subcontractor))

    def figs(self):
        return (self.stock_value(), self.balance(self.inventory), self.balance(self.variance))

    def run_case(self, legacy, method, std, parts, qty="7", partial_receipt=None):
        self.assembly.costing_method = method
        if std: self.assembly.standard_cost = D(std)
        self.assembly.save()
        self.stock(self.frame, "7", "3.337", warehouse=self.subcontractor)
        self.stock(self.frame, "5", "4.1119", warehouse=self.subcontractor)
        self.stock(self.motor, "20", "1.0033", warehouse=self.subcontractor)
        b = self.figs()
        order = self.subcontract_order(qty, service="0.3333")
        if partial_receipt:
            r1 = self.receive(order, partial_receipt)
            r = self.receive(order, str(D(qty) - D(partial_receipt)))
            receipts = [r1, r]
        else:
            receipts = [self.receive(order, qty)]
        if legacy:
            StockMovement.objects.filter(notes__startswith="Consumed by subcontractor").update(notes="Consumed")
        mid = self.figs()
        for receipt in receipts:
            line = receipt.lines.get()
            for p in parts[receipt.lines.get().quantity_received] if isinstance(parts, dict) else parts:
                receipt.create_return(quantities={line: D(p)}, debit_bills=False)
        a = self.figs()
        delta = tuple(x - y for x, y in zip(a, b))
        print(("legacy" if legacy else "recorded"), method, std, parts, "in:", tuple(x - y for x, y in zip(mid, b)), "after returns delta vs before:", delta)
        return delta

    def test_legacy_average(self):
        self.run_case(True, "average", None, ["3", "3", "1"])

    def test_recorded_average(self):
        self.run_case(False, "average", None, ["3", "3", "1"])

    def test_legacy_partial_receipts(self):
        self.run_case(True, "average", None, {D("3"): ["1", "2"], D("4"): ["1", "3"]}, partial_receipt="3")

    def test_standard_awkward(self):
        self.run_case(False, "standard", "3.3333", ["3", "3", "1"])

    def test_standard_awkward_high(self):
        self.run_case(False, "standard", "30.3333", ["2", "5"])

    def test_standard_legacy(self):
        self.run_case(True, "standard", "3.3333", ["3", "3", "1"])

    def test_fifo_legacy(self):
        self.run_case(True, "fifo", None, ["3", "3", "1"])
