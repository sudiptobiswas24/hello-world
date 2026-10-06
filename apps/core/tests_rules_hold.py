"""
Rules a model wrote in clean(), asked where the office writes.

Django's admin calls full_clean(); DRF's ModelSerializer never does, and
neither does code that builds a document. A probe found 35 of 41 such
rules held only in the admin: an invoice to a party that is no customer,
a credit note against another customer's invoice, a delivery line off
another order, an account under itself. Each test here is one of them,
asked through the API where there is one and through save() where the
office has none yet.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountingPeriod, AccountType, BankStatement, JournalEntry, JournalLine, Tax
from apps.core.models import Company, Currency, Party, PartyRole, PartyRoleAssignment, UnitOfMeasure
from apps.inventory.models import Item, Warehouse
from apps.purchasing.models import (
    BlanketOrder,
    Budget,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseRequisition,
    ReorderRule,
    RequestForQuotation,
    RfqInvitation,
    VendorPrice,
)
from apps.sales.models import Delivery, Invoice, SalesOrder, SalesOrderLine

DAY = datetime.date(2026, 6, 1)
BEFORE = DAY - datetime.timedelta(days=1)


class RulesTestCase(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(User.objects.create_superuser("rules"))
        self.inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        self.usd = Currency.objects.create(code="USD", name="Dollar")
        self.each = UnitOfMeasure.objects.create(code="each", name="Each")
        self.kg = UnitOfMeasure.objects.create(code="kg", name="Kilogram", category="weight")
        self.item = Item.objects.create(sku="P-1", name="Part", uom=self.each)
        self.store = Warehouse.objects.create(code="WH", name="Store")
        self.nobody = Party.objects.create(code="NOBODY", name="Holds no role")
        self.customer, self.customer2 = self.party("C1", PartyRole.CUSTOMER), self.party("C2", PartyRole.CUSTOMER)
        self.vendor, self.vendor2 = self.party("V1", PartyRole.VENDOR), self.party("V2", PartyRole.VENDOR)
        self.bank = Account.objects.create(code="1000", name="Bank", account_type=AccountType.ASSET)
        self.ar = Account.objects.create(code="1100", name="AR", account_type=AccountType.ASSET)
        self.ap = Account.objects.create(code="2000", name="AP", account_type=AccountType.LIABILITY)

    def party(self, code, role):
        made = Party.objects.create(code=code, name=code)
        PartyRoleAssignment.objects.create(party=made, role=role)
        return made

    def refused(self, method, url, data, says):
        response = getattr(self.api, method)(url, data, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn(says, response.content.decode())

    def refused_on_save(self, build, says):
        with self.assertRaisesMessage(ValidationError, says):
            build()


class AccountingRulesTests(RulesTestCase):
    def test_an_account_is_not_its_own_parent(self):
        self.refused("patch", f"/api/accounting/accounts/{self.bank.pk}/", {"parent": self.bank.pk},
                     "cannot be its own parent")

    def test_nor_its_own_parent_at_one_remove(self):
        petty = Account.objects.create(code="1001", name="Petty cash", account_type=AccountType.ASSET, parent=self.bank)
        self.refused("patch", f"/api/accounting/accounts/{self.bank.pk}/", {"parent": petty.pk}, "cannot also be above it")

    def test_a_sub_account_is_of_its_parents_type(self):
        self.refused("post", "/api/accounting/accounts/", {"code": "4001", "name": "Odd", "account_type": "income",
                                                           "parent": self.bank.pk}, "same account_type as its parent")

    def test_an_account_with_posted_entries_keeps_its_type(self):
        entry = JournalEntry.objects.create(date=DAY, memo="Opening")
        JournalLine.objects.create(entry=entry, account=self.bank, debit=Decimal("100"))
        JournalLine.objects.create(entry=entry, account=self.ap, credit=Decimal("100"))
        entry.post()
        self.refused("patch", f"/api/accounting/accounts/{self.bank.pk}/", {"account_type": "expense"},
                     "open a new account instead")

    def test_a_sales_tax_names_where_it_is_collected(self):
        self.refused("post", "/api/accounting/taxes/", {"code": "G18", "name": "GST 18", "rate": "18", "scope": "sales"},
                     "needs a collected_account")

    def test_a_tax_is_not_mapped_to_itself(self):
        tax = Tax.objects.create(code="T5", name="GST 5", rate=Decimal("5"), scope="sales", collected_account=self.ap)
        position = self.api.post("/api/accounting/fiscal-positions/", {"code": "EXP", "name": "Export"},
                                 format="json").json()
        self.refused("post", "/api/accounting/fiscal-position-tax-mappings/",
                     {"fiscal_position": position["id"], "source_tax": tax.pk, "target_tax": tax.pk}, "has no effect")

    def test_a_period_does_not_end_before_it_starts(self):
        self.refused_on_save(lambda: AccountingPeriod.objects.create(name="Bad", start_date=DAY, end_date=BEFORE),
                             "cannot end before it starts")

    def test_periods_do_not_overlap(self):
        AccountingPeriod.objects.create(name="June", start_date=DAY, end_date=datetime.date(2026, 6, 30))
        self.refused_on_save(lambda: AccountingPeriod.objects.create(
            name="Mid", start_date=datetime.date(2026, 6, 15), end_date=datetime.date(2026, 7, 14)), "This overlaps June")

    def test_a_closed_period_keeps_its_dates(self):
        june = AccountingPeriod.objects.create(name="June", start_date=DAY, end_date=datetime.date(2026, 6, 30))
        june.close()
        june.end_date = datetime.date(2026, 6, 15)
        self.refused_on_save(june.save, "reopen it before moving its dates")

    def test_a_statement_does_not_end_before_it_starts(self):
        self.refused_on_save(lambda: BankStatement.objects.create(
            bank_account=self.bank, start_date=DAY, end_date=BEFORE, opening_balance=0, closing_balance=0),
            "cannot end before it starts")


class CoreRulesTests(RulesTestCase):
    def test_the_base_currency_is_worth_one_of_itself(self):
        self.refused("post", "/api/core/exchange-rates/", {"currency": self.inr.pk, "rate": "2", "valid_from": "2026-06-01"},
                     "must be 1")

    def test_a_unit_is_not_its_own_base(self):
        self.refused("patch", f"/api/core/units-of-measure/{self.kg.pk}/", {"base_unit": self.kg.pk},
                     "cannot be its own base unit")

    def test_a_unit_counts_in_a_base_of_its_own_kind(self):
        self.refused("post", "/api/core/units-of-measure/", {"code": "g", "name": "Gram", "category": "weight",
                                                             "base_unit": self.each.pk, "conversion_factor": "0.001"},
                     "same category")

    def test_a_bank_account_has_a_number_or_an_iban(self):
        self.refused("post", "/api/core/bank-accounts/", {"party": self.vendor.pk, "account_name": "Main"},
                     "either an account number or an IBAN")

    def test_a_discount_has_a_window(self):
        self.refused("post", "/api/core/payment-terms/", {"code": "2N", "name": "2% net 30", "net_days": 30,
                                                          "discount_percent": "2"}, "needs a discount window")

    def test_the_window_is_inside_the_net_term(self):
        self.refused("post", "/api/core/payment-terms/", {"code": "2X", "name": "2% 40", "net_days": 30,
                                                          "discount_percent": "2", "discount_days": 40},
                     "cannot be longer than the net term")

    def test_the_company_names_the_flagged_base_currency(self):
        company = Company.get()
        self.refused("patch", f"/api/core/company/{company.pk}/", {"base_currency": self.usd.pk},
                     "is not flagged as the base currency")

    def test_the_currency_the_company_names_stays_flagged(self):
        company = Company.get()
        company.base_currency = self.inr
        company.save()
        self.refused("patch", f"/api/core/currencies/{self.inr.pk}/", {"is_base": False}, "Point the company at another")

    def test_and_moves_before_anything_is_booked_not_after(self):
        self.inr.is_base = False
        self.inr.save()
        self.usd.is_base = True
        self.usd.save()  # nothing posted yet: setting up is still setting up
        entry = JournalEntry.objects.create(date=DAY, memo="Opening")
        JournalLine.objects.create(entry=entry, account=self.bank, debit=Decimal("100"))
        JournalLine.objects.create(entry=entry, account=self.ap, credit=Decimal("100"))
        entry.post()
        self.usd.is_base = False
        self.refused_on_save(self.usd.save, "would restate every posted amount")


class PurchasingRulesTests(RulesTestCase):
    def test_a_vendor_price_is_a_vendors(self):
        self.refused_on_save(lambda: VendorPrice.objects.create(vendor=self.nobody, item=self.item, unit_price=1),
                             "does not have the Vendor role")

    def test_a_vendor_price_does_not_end_before_it_starts(self):
        self.refused_on_save(lambda: VendorPrice.objects.create(vendor=self.vendor, item=self.item, unit_price=1,
                                                                valid_from=DAY, valid_to=BEFORE),
                             "valid_to cannot be before valid_from")

    def test_an_rfq_invites_vendors(self):
        rfq = RequestForQuotation.objects.create(issue_date=DAY)
        self.refused_on_save(lambda: RfqInvitation.objects.create(rfq=rfq, vendor=self.nobody),
                             "does not have the Vendor role")

    def test_a_requisition_is_raised_by_an_employee(self):
        self.refused_on_save(lambda: PurchaseRequisition.objects.create(requested_by=self.nobody, request_date=DAY),
                             "does not have the Employee role")

    def test_a_blanket_order_is_with_a_vendor(self):
        self.refused_on_save(lambda: BlanketOrder.objects.create(vendor=self.nobody, start_date=DAY, end_date=DAY),
                             "does not have the Vendor role")

    def test_a_blanket_order_does_not_end_before_it_starts(self):
        self.refused_on_save(lambda: BlanketOrder.objects.create(vendor=self.vendor, start_date=DAY, end_date=BEFORE),
                             "cannot end before it starts")

    def test_a_budget_does_not_end_before_it_starts(self):
        self.refused_on_save(lambda: Budget.objects.create(code="B", name="B", account=self.ap, start_date=DAY,
                                                           end_date=BEFORE, amount=1), "cannot end before it starts")

    def test_a_reorder_target_is_not_below_its_minimum(self):
        self.refused_on_save(lambda: ReorderRule.objects.create(item=self.item, warehouse=self.store,
                                                                minimum=Decimal("10"), target=Decimal("5")),
                             "cannot be below the minimum")

    def test_a_purchase_order_is_to_a_vendor(self):
        self.refused("post", "/api/purchasing/purchase-orders/", {"vendor": self.nobody.pk, "order_date": "2026-06-01"},
                     "does not have the Vendor role")

    def test_a_receipt_line_is_off_the_receipts_own_order(self):
        order_a = PurchaseOrder.objects.create(vendor=self.vendor, order_date=DAY)
        line_a = PurchaseOrderLine.objects.create(order=order_a, item=self.item, quantity=Decimal("5"),
                                                  unit_price=Decimal("1"), uom=self.each)
        order_b = PurchaseOrder.objects.create(vendor=self.vendor, order_date=DAY)
        receipt = self.api.post("/api/purchasing/goods-receipts/", {"purchase_order": order_b.pk,
                                                                     "receipt_date": "2026-06-01"}, format="json").json()
        self.refused("post", "/api/purchasing/goods-receipt-lines/",
                     {"receipt": receipt["id"], "order_line": line_a.pk, "warehouse": self.store.pk,
                      "quantity_received": "1"}, "must belong to the receipt's purchase_order")

    def test_a_vendor_who_lost_the_role_keeps_their_old_orders(self):
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=DAY)
        self.vendor.role_assignments.all().delete()
        order.reference = "Their PO 7"
        order.save()  # the role is asked when the order is made for them, not on every save after


class SalesRulesTests(RulesTestCase):
    def test_a_price_list_does_not_end_before_it_starts(self):
        self.refused("post", "/api/sales/price-lists/", {"code": "PL", "name": "PL", "currency": self.inr.pk,
                                                         "valid_from": "2026-06-01", "valid_to": "2026-05-01"},
                     "valid_to cannot be before valid_from")

    def test_an_order_is_for_a_customer(self):
        self.refused("post", "/api/sales/sales-orders/", {"customer": self.nobody.pk, "order_date": "2026-06-01"},
                     "does not have the Customer role")

    def test_an_invoice_is_to_a_customer(self):
        self.refused("post", "/api/sales/invoices/", {"customer": self.nobody.pk, "invoice_date": "2026-06-01",
                                                      "receivable_account": self.ar.pk}, "does not have the Customer role")

    # The API takes neither field (credit notes and down payments are each
    # their own action), so these two are asked of save(): code builds them.
    def test_a_down_payment_is_against_an_order(self):
        self.refused_on_save(lambda: Invoice.objects.create(customer=self.customer, invoice_date=DAY,
                                                            receivable_account=self.ar, is_down_payment=True),
                             "must be against a sales order")

    def test_a_credit_note_is_for_the_customer_it_credits(self):
        invoice = Invoice.objects.create(customer=self.customer, invoice_date=DAY, receivable_account=self.ar)
        self.refused_on_save(lambda: Invoice.objects.create(customer=self.customer2, invoice_date=DAY,
                                                            receivable_account=self.ar, credits=invoice),
                             "same customer as the invoice it credits")

    def test_a_delivery_line_is_off_the_deliverys_own_order(self):
        order_a = SalesOrder.objects.create(customer=self.customer, order_date=DAY)
        line_a = SalesOrderLine.objects.create(order=order_a, item=self.item, quantity=Decimal("5"),
                                               unit_price=Decimal("1"), uom=self.each, warehouse=self.store)
        order_b = SalesOrder.objects.create(customer=self.customer, order_date=DAY)
        delivery = Delivery.objects.create(sales_order=order_b, delivery_date=DAY)
        self.refused("post", "/api/sales/delivery-lines/", {"delivery": delivery.pk, "order_line": line_a.pk,
                                                            "warehouse": self.store.pk, "quantity_shipped": "1"},
                     "must belong to the delivery's sales_order")

    def test_a_quotation_is_to_a_customer(self):
        self.refused("post", "/api/sales/quotations/", {"customer": self.nobody.pk, "quotation_date": "2026-06-01"},
                     "does not have the Customer role")

    def test_a_quotation_is_not_valid_until_before_it_was_made(self):
        self.refused("post", "/api/sales/quotations/", {"customer": self.customer.pk, "quotation_date": "2026-06-01",
                                                        "valid_until": "2026-05-01"},
                     "valid_until cannot be before the quotation date")

    def test_a_sales_rep_is_an_employee(self):
        self.refused("post", "/api/sales/sales-reps/", {"party": self.nobody.pk}, "does not have the Employee role")

    def test_a_recurring_invoice_is_to_a_customer(self):
        self.refused("post", "/api/sales/recurring-invoices/", {"code": "R1", "customer": self.nobody.pk,
                                                                "receivable_account": self.ar.pk, "interval": "monthly",
                                                                "start_date": "2026-06-01"},
                     "does not have the Customer role")

    def test_a_recurring_invoice_does_not_end_before_it_starts(self):
        self.refused("post", "/api/sales/recurring-invoices/", {"code": "R2", "customer": self.customer.pk,
                                                                "receivable_account": self.ar.pk, "interval": "monthly",
                                                                "start_date": "2026-06-01", "end_date": "2026-05-01"},
                     "end_date cannot be before start_date")
