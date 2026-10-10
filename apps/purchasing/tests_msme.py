"""
Micro and small vendors paid within the Act's days, against the figures
worked by hand:

  bill 1 Jun 2026: net 30 -> due 1 Jul; paid 15 Jul -> 14 days late
  net 60 -> capped at 45 -> due 16 Jul;  no terms -> 15 days -> 16 Jun
  paid in two parts, 20 Jun and 10 Jul, net 30 -> paid on 10 Jul, 9 late
  at 31 Mar 2027: 1 Feb net 30 (due 3 Mar) unpaid 50,000 -> at risk, 28 late;
                  1 Mar net 30 (due 31 Mar) unpaid -> not yet late
  a medium enterprise is not listed
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TransactionTestCase, tag

from apps.accounting.models import Account, AccountType, PartyTaxProfile, Payment, PaymentDirection
from apps.core.models import Party, PartyRole, PartyRoleAssignment, PaymentTerms

from .models import Bill, BillLine, BillPayment
from .msme import msme_bills
from .tests_lifecycle import PurchasingLifecycleTestCase

JUNE = datetime.date(2026, 6, 1)


class MsmeTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        self.net30 = PaymentTerms.objects.create(code="N30M", name="Net 30", net_days=30)
        self.net60 = PaymentTerms.objects.create(code="N60", name="Net 60", net_days=60)
        PartyTaxProfile.objects.create(party=self.vendor, msme_category="micro", udyam_number="UDYAM-MH-26-0012345")

    def bill(self, amount="10000", day=JUNE, terms=None, vendor=None):
        bill = Bill.objects.create(vendor=vendor or self.vendor, bill_date=day, payable_account=self.payable,
                                   payment_terms=terms)
        BillLine.objects.create(bill=bill, description="Cones", quantity=Decimal("1"), unit_price=Decimal(amount),
                                expense_account=self.expense)
        bill.post()
        return Bill.objects.get(pk=bill.pk)

    def pay(self, bill, amount, day):
        payment = Payment.objects.create(
            party=bill.vendor, direction=PaymentDirection.DISBURSEMENT, payment_date=day, amount=Decimal(amount),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.payable)
        payment.post()
        BillPayment.objects.create(bill=bill, payment=payment, amount=Decimal(amount))

    def row(self, bill, as_of):
        [row] = [row for row in msme_bills(JUNE.replace(month=1), datetime.date(2027, 12, 31), as_of=as_of)
                 if row["bill"] == bill.pk]
        return row


class MsmeTests(MsmeTestCase):
    def test_late_by_the_days_past_the_acts_date(self):
        bill = self.bill(terms=self.net30)
        self.pay(bill, "10000", datetime.date(2026, 7, 15))
        row = self.row(bill, datetime.date(2026, 8, 1))
        self.assertEqual((row["due"], row["paid_on"], row["days_late"], row["at_risk"]),
                         (datetime.date(2026, 7, 1), datetime.date(2026, 7, 15), 14, Decimal("0")))

    def test_agreed_days_capped_at_45_and_15_without_agreement(self):
        self.assertEqual(self.row(self.bill(terms=self.net60), JUNE)["due"], datetime.date(2026, 7, 16))
        # The fixture's vendor brings Net 30 to every bill; one with no terms agreed.
        loose = Party.objects.create(code="V-L", name="Loose Terms")
        PartyRoleAssignment.objects.create(party=loose, role=PartyRole.VENDOR)
        PartyTaxProfile.objects.create(party=loose, msme_category="small", udyam_number="UDYAM-MH-26-0054321")
        unagreed = self.bill(vendor=loose)
        self.assertIsNone(unagreed.payment_terms_id)
        self.assertEqual(self.row(unagreed, JUNE)["due"], datetime.date(2026, 6, 16))

    def test_paid_in_parts_is_paid_on_the_last(self):
        bill = self.bill(terms=self.net30)
        self.pay(bill, "4000", datetime.date(2026, 6, 20))
        self.pay(bill, "6000", datetime.date(2026, 7, 10))
        row = self.row(bill, datetime.date(2026, 8, 1))
        self.assertEqual((row["paid_on"], row["days_late"]), (datetime.date(2026, 7, 10), 9))

    def test_what_the_year_end_adds_back(self):
        year_end = datetime.date(2027, 3, 31)
        late = self.bill("50000", day=datetime.date(2027, 2, 1), terms=self.net30)
        current = self.bill("20000", day=datetime.date(2027, 3, 1), terms=self.net30)
        self.pay(late, "50000", datetime.date(2027, 4, 10))  # after the year: not paid at its end
        late_row, current_row = self.row(late, year_end), self.row(current, year_end)
        self.assertEqual((late_row["unpaid"], late_row["at_risk"], late_row["days_late"]),
                         (Decimal("50000.00"), Decimal("50000.00"), 28))
        self.assertEqual((current_row["unpaid"], current_row["at_risk"]), (Decimal("20000.00"), Decimal("0")))

    def test_a_medium_enterprise_is_not_listed_and_a_category_needs_its_udyam(self):
        medium = Party.objects.create(code="V-M", name="Medium Mills")
        PartyRoleAssignment.objects.create(party=medium, role=PartyRole.VENDOR)
        PartyTaxProfile.objects.create(party=medium, msme_category="medium", udyam_number="UDYAM-MH-26-0099999")
        self.bill(vendor=medium)
        self.assertEqual(msme_bills(JUNE, JUNE, as_of=JUNE), [])
        other = Party.objects.create(code="V-X", name="No Udyam")
        with self.assertRaisesMessage(ValidationError, "give the number"):
            PartyTaxProfile.objects.create(party=other, msme_category="small")
        with self.assertRaisesMessage(ValidationError, "is not a Udyam number"):
            PartyTaxProfile.objects.create(party=other, msme_category="small", udyam_number="12345")

    def test_the_controller_reads_it_and_the_stores_do_not(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        bill = self.bill(terms=self.net30)
        self.pay(bill, "10000", datetime.date(2026, 7, 15))

        def as_(role):
            user = User.objects.create_user(role.replace(" ", "_").lower())
            user.groups.add(Group.objects.get(name=role))
            client = APIClient()
            client.force_authenticate(user)
            return client

        url = "/api/purchasing/purchasing-reports/msme/"
        query = {"start": "2026-06-01", "end": "2026-06-30", "as_of": "2026-08-01"}
        [row] = as_("Controller").get(url, query).json()
        self.assertEqual((row["due"], row["paid_on"], row["days_late"], row["total"]),
                         ("2026-07-01", "2026-07-15", 14, "10000.00"))
        self.assertEqual(as_("Warehouse Staff").get(url, query).status_code, 403)


class KeptOnTheBillTests(MsmeTestCase):
    """
    The category is the bill's, as its vendor's Udyam registration said when it
    posted. Booked from a micro vendor on 1 June, net 60, 1,000, unpaid at
    31 March; the vendor is medium from April. Still at risk: 1,000.00.
    """

    def test_a_bill_from_a_micro_vendor_stays_on_the_list_when_the_vendor_grows(self):
        bill = self.bill("1000", terms=self.net60)
        self.assertEqual(bill.msme_category, "micro")
        profile = PartyTaxProfile.objects.get(party=self.vendor)
        profile.msme_category = "medium"
        profile.save()
        rows = msme_bills(datetime.date(2026, 4, 1), datetime.date(2027, 3, 31), as_of=datetime.date(2027, 3, 31))
        self.assertEqual([(row["category"], row["at_risk"]) for row in rows], [("micro", Decimal("1000.00"))])

    def test_a_vendor_that_becomes_micro_later_brings_no_earlier_bill(self):
        PartyTaxProfile.objects.filter(party=self.vendor).update(msme_category="medium")
        self.bill("1000", terms=self.net60)
        PartyTaxProfile.objects.filter(party=self.vendor).update(msme_category="micro")
        self.assertEqual(msme_bills(JUNE, JUNE, as_of=datetime.date(2027, 3, 31)), [])

    def test_a_debit_note_is_its_bills(self):
        bill = self.bill("1000", terms=self.net60)
        PartyTaxProfile.objects.filter(party=self.vendor).update(msme_category="medium")
        note = bill.create_debit_note(quantities={bill.lines.get(): Decimal("0.5")})
        self.assertEqual(note.msme_category, "micro")

    def test_the_controller_reads_the_category_booked(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        self.bill("1000", terms=self.net60)
        PartyTaxProfile.objects.filter(party=self.vendor).update(msme_category="medium")
        user = User.objects.create_user("controller")
        user.groups.add(Group.objects.get(name="Controller"))
        client = APIClient()
        client.force_authenticate(user)
        [row] = client.get("/api/purchasing/purchasing-reports/msme/",
                           {"start": "2026-06-01", "end": "2026-06-30", "as_of": "2027-03-31"}).json()
        self.assertEqual((row["category"], row["at_risk"]), ("micro", "1000.00"))

    def test_the_audit_reports_the_category_read_live(self):
        from apps.core.management.commands.audit_invariants import Command, app_sources

        sources = app_sources()
        self.assertEqual(Command().kept_settings_read_live(["purchasing"], sources), [])
        sources["purchasing"] = {path: text.replace('"category": bill.msme_category',
                                                    '"category": bill.vendor.tax_profile.msme_category')
                                 if path.name == "msme.py" else text for path, text in sources["purchasing"].items()}
        (finding,) = Command().kept_settings_read_live(["purchasing"], sources)
        self.assertIn("purchasing.Bill keeps its own msme_category", finding[1])


@tag("migration")
class CategoryKeptMigrationTests(TransactionTestCase):
    """Bills posted before the bill kept a category read their vendor's; that is what they keep."""

    before = [("purchasing", "0060_allocation_dated_on_its_own_day")]
    after = [("purchasing", "0061_bill_keeps_its_msme_category")]

    def test_posted_bills_take_their_vendors_category(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        model = executor.loader.project_state(self.before).apps.get_model
        micro = model("core", "Party").objects.create(code="M", name="Micro")
        plain = model("core", "Party").objects.create(code="P", name="Plain")
        model("accounting", "PartyTaxProfile").objects.create(party=micro, msme_category="micro",
                                                              udyam_number="UDYAM-MH-26-0012345")
        payable = model("accounting", "Account").objects.create(code="2000", name="AP", account_type="liability")
        for party, posted in ((micro, True), (micro, False), (plain, True)):
            model("purchasing", "Bill").objects.create(vendor=party, bill_date=JUNE, payable_account=payable,
                                                      posted=posted)
        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        Bill = executor.loader.project_state(self.after).apps.get_model("purchasing", "Bill")
        self.assertEqual(sorted(Bill.objects.values_list("vendor__code", "posted", "msme_category")),
                         [("M", False, ""), ("M", True, "micro"), ("P", True, "")])
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


class DueByTheActTests(MsmeTestCase):
    """
    A micro vendor's bill is due by the earlier of its terms and 45 days from
    acceptance, and the payment run lists it by that day (calc_stat/o103_msme.py):
      bill 1 June, net 60, nothing received: terms 31 July, the Act 16 July;
      the same bill for goods received 10 June: the Act 25 July;
      net 30: 1 July, the terms come first.
    """

    def listed(self, day):
        from .models import payment_run

        return [entry["bill"].pk for run in payment_run(due_by=day) for entry in run["bills"]]

    def test_the_payment_run_lists_a_micro_vendors_bill_by_the_acts_45_days(self):
        from .models import payment_run

        bill = self.bill("1000", terms=self.net60)
        (row,) = msme_bills(JUNE, datetime.date(2026, 6, 30), as_of=datetime.date(2026, 7, 17))
        self.assertEqual((row["due"], bill.due_date, bill.pay_by()),
                         (datetime.date(2026, 7, 16), datetime.date(2026, 7, 31), datetime.date(2026, 7, 16)))
        self.assertNotIn(bill.pk, self.listed(datetime.date(2026, 7, 15)))
        self.assertIn(bill.pk, self.listed(datetime.date(2026, 7, 16)))
        (entry,) = [entry for run in payment_run(due_by=datetime.date(2026, 7, 17)) for entry in run["bills"]]
        self.assertEqual((entry["due_date"], entry["days_overdue"]), (datetime.date(2026, 7, 16), 1))

    def test_from_the_day_the_goods_came(self):
        from .models import GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLine

        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=JUNE)
        line = PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                                unit_price=Decimal("100"))
        order.confirm()
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=datetime.date(2026, 6, 10))
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=line, warehouse=self.warehouse,
                                        quantity_received=Decimal("10"))
        receipt.post()
        bill = Bill.objects.create(vendor=self.vendor, bill_date=JUNE, payable_account=self.payable,
                                   payment_terms=self.net60, purchase_order=order)
        BillLine.objects.create(bill=bill, order_line=line, item=self.item, quantity=Decimal("10"),
                                unit_price=Decimal("100"), expense_account=self.expense)
        bill.post()
        bill = Bill.objects.get(pk=bill.pk)
        self.assertEqual(bill.pay_by(), datetime.date(2026, 7, 25))

    def test_terms_that_come_first_stand(self):
        bill = self.bill("1000", terms=self.net30)
        self.assertEqual(bill.pay_by(), datetime.date(2026, 7, 1))

    def test_a_medium_vendors_bill_keeps_its_terms(self):
        PartyTaxProfile.objects.filter(party=self.vendor).update(msme_category="medium")
        bill = self.bill("1000", terms=self.net60)
        self.assertEqual(bill.pay_by(), datetime.date(2026, 7, 31))
        self.assertNotIn(bill.pk, self.listed(datetime.date(2026, 7, 16)))

    def test_over_the_api_as_the_ap_manager(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        bill = self.bill("1000", terms=self.net60)
        user = User.objects.create_user("ap")
        user.groups.add(Group.objects.get(name="AP Manager"))
        client = APIClient()
        client.force_authenticate(user)
        response = client.get("/api/purchasing/purchasing-reports/payment-run/", {"due_by": "2026-07-16"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn(bill.number, response.content.decode())
