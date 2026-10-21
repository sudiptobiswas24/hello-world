"""Probes of the second audit, trade lane (sales). Each asserts what SHOULD be true."""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounting.models import Account, AccountType
from .models import Invoice, InvoiceLine
from .tests_base import SalesTestCase


from apps.core.tests_roles import RolesTestCase  # noqa: E402


class WhoMayCreditByReturning(RolesTestCase, SalesTestCase):
    def setUp(self):
        SalesTestCase.setUp(self)
        RolesTestCase.setUp(self)

    def test_S6_a_warehouse_hand_taking_goods_back_posts_no_credit_note(self):
        """A credit note is money: Warehouse Staff hold post_delivery, not post_invoice. Purchasing's mirror leaves
        the debit notes as drafts for anyone who may not post bills (review F8)."""
        order = self.make_order(quantity="10", price="100")
        delivery = self.ship(order, 10)
        self.bill(order)
        client = self.as_("Warehouse Staff")
        reply = client.post(f"/api/sales/deliveries/{delivery.pk}/customer_return/", {}, format="json")
        self.assertEqual(reply.status_code, 200, reply.content)
        posted_notes = Invoice.objects.filter(credits__isnull=False, posted=True).count()
        self.assertEqual(posted_notes, 0, "a credit note posted by a login holding no post_invoice")


class DunningOnlyAdvances(SalesTestCase):
    def test_S7_a_customer_who_got_the_60_day_notice_is_not_sent_the_7_day_one_after(self):
        """Levels 7, 30, 60. Run first at 70 days overdue: the 60-day notice. Run again next day: nothing
        (or a higher level), never the 30-day and then the 7-day reminder after the final notice."""
        from django.core import mail
        from .models import DunningLevel, DunningNotice, run_dunning

        for name, days in (("First", 7), ("Second", 30), ("Final", 60)):
            DunningLevel.objects.create(name=name, days_overdue=days)
        order = self.make_order(quantity="10", price="100")
        self.ship(order, 10)
        invoice = self.bill(order, on=datetime.date(2026, 3, 1))   # net 30: due 31 March
        first_day = invoice.due_date + datetime.timedelta(days=70)
        run_dunning(as_of=first_day)
        run_dunning(as_of=first_day + datetime.timedelta(days=1))
        run_dunning(as_of=first_day + datetime.timedelta(days=2))
        sent = list(DunningNotice.objects.filter(invoice=invoice).order_by("id").values_list("level__days_overdue", flat=True))
        self.assertEqual(sent, [60], f"levels sent in order: {sent}")


class CommissionOnCollectedExcludesTax(SalesTestCase):
    def test_S8_commission_on_collected_cash_is_on_the_net_sale_as_the_invoiced_basis_is(self):
        """Invoice 10 x 100 + 20% VAT = 1,200, paid in full. 5% of the net sale 1,000 is 50.00 on either basis;
        the collected basis counts the payment, tax included: 60.00."""
        from apps.accounting.models import Tax, TaxGroup
        from .models import CommissionBasis, CommissionPlan, SalesRep, commission_report

        group = TaxGroup.objects.create(code="VAT", name="VAT")
        vat_account = Account.objects.create(code="2100", name="VAT Payable", account_type=AccountType.LIABILITY)
        vat = Tax.objects.create(code="VAT20", name="VAT 20%", rate=Decimal("20"), group=group,
                                 collected_account=vat_account, paid_account=vat_account)
        plan = CommissionPlan.objects.create(code="P", name="5% collected", percent=Decimal("5"),
                                             basis=CommissionBasis.PAID)
        SalesRep.objects.create(party=self.rep, plan=plan)
        order = self.make_order("10", "100", rep=self.rep)
        for line in order.lines.all():
            line.taxes.set([vat])
        invoice = self.bill(order)
        self.assertEqual(invoice.total(), Decimal("1200.00"))
        self.allocate(self.receipt("1200"), invoice, "1200")
        self.assertEqual(commission_report()[0]["commission"], Decimal("50.00"))


class ApprovalPolicyIsInTheBaseCurrency(SalesTestCase):
    """The policy's figures and the item's cost are rupees (the base); a euro order is weighed in euros."""

    def eur_order(self, quantity, price):
        from apps.core.models import Currency, ExchangeRate
        from .models import SalesOrder, SalesOrderLine

        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(currency=eur, rate=Decimal("90"), valid_from=datetime.date(2026, 1, 1))
        order = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=eur)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal(quantity),
                                      unit_price=Decimal(price), revenue_account=self.revenue)
        return order

    def test_S9_a_euro_order_worth_4_5_million_base_asks_the_1_million_limit(self):
        """10 x 5,000 EUR at 90 = 4,500,000 base, above a 1,000,000 limit: needs approval."""
        from .models import ApprovalPolicy

        ApprovalPolicy.objects.create(code="P", name="Limit", max_order_value=Decimal("1000000"))
        order = self.eur_order("10", "5000")
        with self.assertRaises(ValidationError):
            order.confirm()

    def test_S10_a_euro_sale_at_a_95_percent_margin_is_not_held_by_a_20_percent_floor(self):
        """Cost 4 base a unit; 1 EUR a unit at 90 is 90 base: margin 95.6%. Weighed in euros against a
        base cost it reads 1 - 4 = -300%."""
        from .models import ApprovalPolicy

        ApprovalPolicy.objects.create(code="P", name="Floor", min_margin_percent=Decimal("20"))
        order = self.eur_order("10", "1")
        order.confirm()   # raises 'Gross margin is -300.00%' on the base
