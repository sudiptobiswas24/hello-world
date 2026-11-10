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

from .models import (Bill, BillLine, BillPayment, GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLine,
                     ReceiptInspection)
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


class TheBillSaysTheActsDayTests(MsmeTestCase):
    """
    O483: the bill read through the API says the Act's day beside the terms'. Bill of 1 June, net 60, a
    micro vendor, nothing received: the terms say 31 July, the Act 16 July (45 days from 1 June).
    """

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("ap-manager")
        user.groups.add(Group.objects.get(name="AP Manager"))
        self.ap = APIClient()
        self.ap.force_authenticate(user)

    def read(self, bill):
        response = self.ap.get(f"/api/purchasing/bills/{bill.pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_a_micro_vendors_posted_bill_carries_the_acts_day(self):
        bill = self.read(self.bill("1000", terms=self.net60))
        # O498: the day it is due is the Act's; the terms' is kept beside it.
        self.assertEqual((bill["due_date"], bill["terms_due_date"], bill["act_pay_by"]),
                         ("2026-07-16", "2026-07-31", "2026-07-16"))

    def test_a_medium_vendors_bill_and_a_draft_carry_none(self):
        medium = Party.objects.create(code="V-M", name="Medium Mills")
        PartyRoleAssignment.objects.create(party=medium, role=PartyRole.VENDOR)
        PartyTaxProfile.objects.create(party=medium, msme_category="medium", udyam_number="UDYAM-MH-26-0099999")
        self.assertIsNone(self.read(self.bill("1000", terms=self.net60, vendor=medium))["act_pay_by"])
        draft = Bill.objects.create(vendor=self.vendor, bill_date=JUNE, payable_account=self.payable)
        self.assertIsNone(self.read(draft)["act_pay_by"])


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
        # The terms' 31 July is kept as the terms'; the bill is due on the Act's day (O498).
        self.assertEqual((row["due"], bill.terms_due_date, bill.due_date, bill.pay_by()),
                         (datetime.date(2026, 7, 16), datetime.date(2026, 7, 31), datetime.date(2026, 7, 16),
                          datetime.date(2026, 7, 16)))
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


class AcceptedOnDeliveryTests(MsmeTestCase):
    """
    O160 (review_stat #3, #4). MSMED Act s.2(b): goods are accepted on the day
    of delivery unless the company objects in writing within 15 days, and
    then on the day the objection is removed; each delivery on its own day.
    Net 60, 10 x 100 (calc_fix_stat/o160_msme.py):
      A received 10 Jun, a routine QC pass 20 Jun, billed 25 Jun: 25 Jul, not 4 Aug;
      B 6 received 1 Jun and 4 on 20 Jun, one bill on 25 Jun: 600 by 16 Jul, 400 by 4 Aug;
      C received 10 Jun, objected to in writing 18 Jun, removed 30 Jun: 14 Aug;
        an objection on 26 Jun (day 16) moves nothing; while one stands, the terms' 24 Aug.
    """

    def order(self):
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=JUNE)
        line = PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                                unit_price=Decimal("100"))
        order.confirm()
        return order, line

    def receive(self, order, line, day, quantity):
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=day)
        received = GoodsReceiptLine.objects.create(receipt=receipt, order_line=line, warehouse=self.warehouse,
                                                   quantity_received=Decimal(quantity))
        receipt.post()
        return GoodsReceipt.objects.get(pk=receipt.pk), received

    def bill_for(self, order, line, day, quantity):
        bill = Bill.objects.create(vendor=self.vendor, bill_date=day, payable_account=self.payable,
                                   payment_terms=self.net60, purchase_order=order)
        BillLine.objects.create(bill=bill, order_line=line, item=self.item, quantity=Decimal(quantity),
                                unit_price=Decimal("100"), expense_account=self.expense)
        bill.post()
        return Bill.objects.get(pk=bill.pk)

    def test_a_routine_inspection_is_no_objection(self):
        order, line = self.order()
        _, received = self.receive(order, line, datetime.date(2026, 6, 10), "10")
        ReceiptInspection.objects.create(receipt_line=received, quantity=Decimal("10"), accepted=True,
                                         inspected_on=datetime.date(2026, 6, 20))
        self.assertEqual(self.bill_for(order, line, datetime.date(2026, 6, 25), "10").pay_by(),
                         datetime.date(2026, 7, 25))

    def test_each_delivery_a_bill_covers_is_due_on_its_own_day(self):
        from .models import payment_run

        order, line = self.order()
        self.receive(order, line, JUNE, "6")
        self.receive(order, line, datetime.date(2026, 6, 20), "4")
        bill = self.bill_for(order, line, datetime.date(2026, 6, 25), "10")
        self.assertEqual([(row["due_date"], row["amount"]) for row in bill.installments()],
                         [(datetime.date(2026, 7, 16), Decimal("600.00")), (datetime.date(2026, 8, 4), Decimal("400.00"))])
        self.assertEqual(bill.pay_by(), datetime.date(2026, 8, 4))
        (entry,) = [entry for run in payment_run(due_by=datetime.date(2026, 7, 16)) for entry in run["bills"]]
        self.assertEqual(entry["amount_due"], Decimal("600.00"))
        rows = msme_bills(JUNE, datetime.date(2026, 6, 30), as_of=datetime.date(2026, 7, 20))
        self.assertEqual([(row["amount"], row["due"], row["days_late"], row["at_risk"]) for row in rows],
                         [(Decimal("600.00"), datetime.date(2026, 7, 16), 4, Decimal("600.00")),
                          (Decimal("400.00"), datetime.date(2026, 8, 4), 0, Decimal("0"))])

    def report(self, as_of):
        return [(row["amount"], row["due"], row["due_pending"], row["paid_on"], row["days_late"], row["unpaid"],
                 row["at_risk"]) for row in msme_bills(JUNE, datetime.date(2026, 6, 30), as_of=as_of)]

    def test_each_delivery_is_late_from_its_own_day_in_the_report(self):
        # O180 (review_stat2 #6): 600 due 16 Jul and 400 due 4 Aug, paid together on
        # 10 Aug, read as one bill due 4 Aug, 6 days late; the 600 was 25 days late.
        order, line = self.order()
        self.receive(order, line, JUNE, "6")
        self.receive(order, line, datetime.date(2026, 6, 20), "4")
        bill = self.bill_for(order, line, datetime.date(2026, 6, 25), "10")
        self.pay(bill, "700", datetime.date(2026, 8, 1))
        self.assertEqual(self.report(datetime.date(2026, 8, 10)), [
            (Decimal("600.00"), datetime.date(2026, 7, 16), False, datetime.date(2026, 8, 1), 16, Decimal("0.00"),
             Decimal("0")),
            (Decimal("400.00"), datetime.date(2026, 8, 4), False, None, 6, Decimal("300.00"), Decimal("300.00"))])
        self.pay(bill, "300", datetime.date(2026, 8, 10))
        self.assertEqual([row[4] for row in self.report(datetime.date(2026, 8, 10))], [16, 6])

    def test_a_standing_objection_is_due_pending_not_on_time(self):
        # O180: an objection never removed read due none, 0 days late, 0 at risk.
        order, line = self.order()
        receipt, _ = self.receive(order, line, datetime.date(2026, 6, 10), "10")
        self.bill_for(order, line, datetime.date(2026, 6, 25), "10")
        receipt.record_objection(datetime.date(2026, 6, 12), "Letter PUR/14: short count")
        self.assertEqual(self.report(datetime.date(2026, 12, 31)),
                         [(Decimal("1000.00"), None, True, None, None, Decimal("1000.00"), Decimal("0"))])

    def test_an_objection_is_keyed_by_a_login_on_record_from_stores_or_accounts(self):
        """
        O179 (review_stat2 #5): the dates are the letter's, so they can be keyed
        after the event; each keying now leaves who did it and what was keyed.
        The AP Manager, who gets the letter, may key one as well as the stores.
        """
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        from apps.core.history import RecordEvent

        call_command("setup_roles", verbosity=0)
        clients = {}
        for role in ("AP Manager", "Warehouse Staff", "Purchasing Clerk"):
            user = User.objects.create_user(role.replace(" ", "").lower())
            user.groups.add(Group.objects.get(name=role))
            clients[role] = APIClient()
            clients[role].force_authenticate(user)
        order, line = self.order()
        receipt, _ = self.receive(order, line, datetime.date(2026, 6, 10), "10")
        url = f"/api/purchasing/goods-receipts/{receipt.pk}/objection/"
        ap, stores = clients["AP Manager"], clients["Warehouse Staff"]
        written = {"objected_on": "2026-06-12", "objection": "Letter PUR/14: short count"}
        self.assertEqual(clients["Purchasing Clerk"].post(url, written, format="json").status_code, 403)
        for body in ({**written, "objected_on": "2026-06-09"}, {**written, "objected_on": "2099-01-01"},
                     {**written, "objection": ["a"]}, {**written, "objection": "y" * 256}):
            self.assertEqual(ap.post(url, body, format="json").status_code, 400, body)
        self.assertEqual(ap.post(url, written, format="json").status_code, 200)
        self.assertEqual(GoodsReceipt.objects.get(pk=receipt.pk).updated_by.username, "apmanager")
        self.assertEqual(stores.post(url, {"objection_removed_on": "2099-01-01"}, format="json").status_code, 400)
        self.assertEqual(stores.post(url, {"objection_removed_on": "2026-06-20"}, format="json").status_code, 200)
        self.assertEqual(GoodsReceipt.objects.get(pk=receipt.pk).updated_by.username, "warehousestaff")
        self.assertEqual(ap.delete(url).status_code, 200)
        self.assertEqual(
            [(event.who.username, event.summary)
             # The actions' own lines; the fields each changed are the model layer's, named by the action too.
             for event in RecordEvent.objects.filter(object_id=receipt.pk, action="objection", kind="action").order_by("pk")],
            [("apmanager", "objection made 2026-06-12"),
             ("warehousestaff", "objection of 2026-06-12 removed 2026-06-20"),
             ("apmanager", "objection withdrawn: was made 2026-06-12, removed 2026-06-20")])

    def test_two_bills_for_two_deliveries(self):
        order, line = self.order()
        self.receive(order, line, JUNE, "6")
        self.receive(order, line, datetime.date(2026, 6, 20), "4")
        first = self.bill_for(order, line, datetime.date(2026, 6, 5), "6")
        second = self.bill_for(order, line, datetime.date(2026, 6, 25), "4")
        self.assertEqual((first.pay_by(), second.pay_by()), (datetime.date(2026, 7, 16), datetime.date(2026, 8, 4)))

    def test_an_objection_in_writing_moves_it_to_the_day_it_is_removed(self):
        from django.contrib.auth.models import Group, Permission, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        stores = User.objects.create_user("stores")
        stores.groups.add(Group.objects.get(name="Warehouse Staff"))
        client = APIClient()
        client.force_authenticate(stores)
        reader = User.objects.create_user("reader")
        reader.user_permissions.add(Permission.objects.get(content_type__app_label="purchasing",
                                                           codename="view_goodsreceipt"))
        reading = APIClient()
        reading.force_authenticate(reader)
        order, line = self.order()
        receipt, _ = self.receive(order, line, datetime.date(2026, 6, 10), "10")
        bill = self.bill_for(order, line, datetime.date(2026, 6, 25), "10")
        url = f"/api/purchasing/goods-receipts/{receipt.pk}/objection/"
        written = {"objected_on": "2026-06-18", "objection": "Letter PUR/14: bags 2 g under the agreed GSM"}
        self.assertEqual(reading.post(url, written, format="json").status_code, 403)
        late = client.post(url, {**written, "objected_on": "2026-06-26"}, format="json")
        self.assertEqual(late.status_code, 400, late.content)
        self.assertEqual(client.post(url, written, format="json").status_code, 200)
        self.assertEqual(Bill.objects.get(pk=bill.pk).pay_by(), datetime.date(2026, 8, 24))
        removed = client.post(url, {"objection_removed_on": "2026-06-30"}, format="json")
        self.assertEqual(removed.status_code, 200, removed.content)
        self.assertEqual(Bill.objects.get(pk=bill.pk).pay_by(), datetime.date(2026, 8, 14))
        self.assertEqual(client.delete(url).status_code, 200)
        self.assertEqual(Bill.objects.get(pk=bill.pk).pay_by(), datetime.date(2026, 7, 25))


class PaidOnTheDayOfWhatSettledItTests(MsmeTestCase):
    """
    O188. 1,000 billed 1 June, net 60, micro: due 16 July (45 days). Debited
    in full on 10 June, as of 31 December: paid on 10 June, 0 days late,
    nothing unpaid (calc_fix_purch/o188_o189.py). It read 168 days late.
    """

    def test_a_part_settled_wholly_by_a_debit_note_is_paid_on_the_notes_day(self):
        bill = self.bill("1000", terms=self.net60)
        note = bill.create_debit_note()
        # The note is dated the day it is made; the scenario's is 10 June.
        Bill.objects.filter(pk=note.pk).update(bill_date=datetime.date(2026, 6, 10))
        row = self.row(bill, datetime.date(2026, 12, 31))
        self.assertEqual((row["due"], row["paid_on"], row["days_late"], row["unpaid"], row["at_risk"]),
                         (datetime.date(2026, 7, 16), datetime.date(2026, 6, 10), 0, Decimal("0"), Decimal("0")))

    def test_a_part_debited_and_the_rest_paid_late_is_paid_on_the_later_day(self):
        bill = self.bill("1000", terms=self.net60)
        note = bill.create_debit_note(quantities={bill.lines.get(): Decimal("0.4")})
        Bill.objects.filter(pk=note.pk).update(bill_date=datetime.date(2026, 6, 10))
        self.pay(bill, "600", datetime.date(2026, 7, 20))
        row = self.row(bill, datetime.date(2026, 12, 31))
        self.assertEqual((row["paid_on"], row["days_late"], row["unpaid"]), (datetime.date(2026, 7, 20), 4, Decimal("0")))


class DueDeliveryByDeliveryTests(MsmeTestCase):
    """
    O189. 6 received 1 June, 4 on 20 June objected to in writing on 25 June
    (standing); one bill of 1,000 on 25 June, net 60 (24 Aug): 600 due 16 July
    by the Act, 400 by the terms while the objection stands. The payment run
    to 16 July lists 600 beside 16 July, not beside 24 Aug.
    """

    order, receive, bill_for = AcceptedOnDeliveryTests.order, AcceptedOnDeliveryTests.receive, AcceptedOnDeliveryTests.bill_for

    def test_the_run_and_the_bill_read_one_schedule(self):
        from .models import payment_run

        order, line = self.order()
        self.receive(order, line, JUNE, "6")
        second, _ = self.receive(order, line, datetime.date(2026, 6, 20), "4")
        second.record_objection(datetime.date(2026, 6, 25), "Letter PUR/15: bags 2 g under the agreed GSM")
        bill = self.bill_for(order, line, datetime.date(2026, 6, 25), "10")
        self.assertEqual([(row["due_date"], row["amount"]) for row in bill.installments()],
                         [(datetime.date(2026, 7, 16), Decimal("600.00")), (datetime.date(2026, 8, 24), Decimal("400.00"))])
        (entry,) = [entry for run in payment_run(due_by=datetime.date(2026, 7, 16)) for entry in run["bills"]]
        self.assertEqual((entry["due_date"], entry["amount_due"]), (datetime.date(2026, 7, 16), Decimal("600.00")))
        self.assertEqual((bill.pay_by(), bill.pay_by(by=datetime.date(2026, 7, 16))),
                         (datetime.date(2026, 8, 24), datetime.date(2026, 7, 16)))


class DueOnTheDayItMustBePaidTests(MsmeTestCase):
    """
    O498: a bill's due_date is the day it must be paid by, the earlier of
    the terms' and the MSMED Act's (s.15) for a vendor micro or small as the
    bill posted; every list sorted by due date reads it
    (calc_g34_o498/due.py). Accepted 1 June:
      micro, net 60: 16 July (terms 31 July);  micro, net 30: 1 July;
      medium, net 60: 31 July;  small, no terms: 16 June, not 1 June;
      goods received 10 June after the bill: 25 July;
      then objected to (standing): 31 July; removed 12 June: 27 July; withdrawn: 25 July;
      part paid, debited, vendor reclassified after: still 16 July.
    """

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        self.clients = {}
        for role in ("AP Manager", "Controller"):
            user = User.objects.create_user(role.lower().replace(" ", "-"))
            user.groups.add(Group.objects.get(name=role))
            self.clients[role] = APIClient()
            self.clients[role].force_authenticate(user)

    def vendor_of(self, code, category):
        party = Party.objects.create(code=code, name=f"{category.title()} Mills {code}")
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.VENDOR)
        PartyTaxProfile.objects.create(party=party, msme_category=category, udyam_number=f"UDYAM-MH-26-00{code[-5:]}")
        return party

    def get(self, role, url, params=None):
        response = self.clients[role].get(url, params or {})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_the_bill_is_due_on_the_earlier_day(self):
        medium = self.vendor_of("V-11111", "medium")
        small = self.vendor_of("V-22222", "small")
        cases = [
            (self.bill("1000", terms=self.net60), "2026-07-16", "2026-07-31"),
            (self.bill("1000", terms=self.net30), "2026-07-01", "2026-07-01"),
            (self.bill("1000", terms=self.net60, vendor=medium), "2026-07-31", "2026-07-31"),
            (self.bill("1000", vendor=small), "2026-06-16", "2026-06-01"),
        ]
        for bill, due, terms in cases:
            read = self.get("AP Manager", f"/api/purchasing/bills/{bill.pk}/")
            self.assertEqual((read["due_date"], read["terms_due_date"]), (due, terms), bill.vendor.code)

    def test_every_list_sorted_by_due_date_reads_the_acts_day(self):
        medium = self.vendor_of("V-33333", "medium")
        net50 = PaymentTerms.objects.create(code="N50", name="Net 50", net_days=50)
        micro = self.bill("1000", terms=self.net60)  # due 16 July, terms 31 July
        later = self.bill("1000", terms=net50, vendor=medium)  # due 21 July
        for role in ("AP Manager", "Controller"):
            rows = self.get(role, "/api/purchasing/bills/", {"ordering": "due_date", "open": "true"})
            self.assertEqual([(row["id"], row["due_date"]) for row in rows],
                             [(micro.pk, "2026-07-16"), (later.pk, "2026-07-21")], role)
            [run] = [run for run in self.get(role, "/api/purchasing/purchasing-reports/payment-run/",
                                             {"due_by": "2026-07-16"}) if run["vendor_id"] == self.vendor.pk]
            self.assertEqual([(row["number"], row["due_date"]) for row in run["bills"]],
                             [(micro.number, "2026-07-16")], role)
        aging = self.get("AP Manager", "/api/purchasing/purchasing-reports/aging/", {"as_of": "2026-07-17"})
        self.assertIn("2026-07-16", str(aging))
        self.assertNotIn("2026-07-31", str(aging))

    def test_no_terms_is_the_acts_fifteen_days(self):
        from .models import payment_run

        bill = self.bill("1000", vendor=self.vendor_of("V-44444", "small"))
        self.assertEqual((bill.due_date, bill.pay_by()), (datetime.date(2026, 6, 16),) * 2)
        self.assertFalse(bill.is_overdue(datetime.date(2026, 6, 16)))
        self.assertEqual(bill.days_overdue(datetime.date(2026, 6, 17)), 1)
        self.assertEqual([entry["bill"].pk for run in payment_run(due_by=datetime.date(2026, 6, 15))
                          for entry in run["bills"]], [])

    def test_paid_in_part_and_debited_it_keeps_its_day(self):
        from .models import payment_run

        bill = self.bill("10000", terms=self.net60)
        self.pay(bill, "5000", datetime.date(2026, 6, 20))
        bill.create_debit_note(quantities={bill.lines.get(): Decimal("0.2")})
        bill = Bill.objects.get(pk=bill.pk)
        self.assertEqual((bill.due_date, bill.amount_due()), (datetime.date(2026, 7, 16), Decimal("3000.00")))
        (entry,) = [entry for run in payment_run(due_by=datetime.date(2026, 7, 16)) for entry in run["bills"]]
        self.assertEqual((entry["due_date"], entry["amount_due"]), (datetime.date(2026, 7, 16), Decimal("3000.00")))

    def test_a_vendor_reclassified_after_moves_no_bill(self):
        bill = self.bill("1000", terms=self.net60)
        PartyTaxProfile.objects.filter(party=self.vendor).update(msme_category="medium")
        self.assertEqual(Bill.objects.get(pk=bill.pk).due_date, datetime.date(2026, 7, 16))
        self.assertEqual(self.bill("1000", terms=self.net60).due_date, datetime.date(2026, 7, 31))

    def test_goods_received_after_the_bill_and_objected_to_move_its_day(self):
        order, line = AcceptedOnDeliveryTests.order(self)
        PurchaseOrder.objects.filter(pk=order.pk).update(bill_policy="ordered")  # billed ahead of the goods
        order.refresh_from_db()
        bill = AcceptedOnDeliveryTests.bill_for(self, order, line, JUNE, "10")
        self.assertEqual(bill.due_date, datetime.date(2026, 7, 16))
        receipt, _ = AcceptedOnDeliveryTests.receive(self, order, line, datetime.date(2026, 6, 10), "10")
        due = lambda: Bill.objects.get(pk=bill.pk).due_date  # noqa: E731
        self.assertEqual(due(), datetime.date(2026, 7, 25))
        receipt.record_objection(datetime.date(2026, 6, 10), "Letter PUR/16: bags 2 g under the agreed GSM")
        self.assertEqual(due(), datetime.date(2026, 7, 31))
        receipt.remove_objection(datetime.date(2026, 6, 12))
        self.assertEqual(due(), datetime.date(2026, 7, 27))
        receipt.withdraw_objection()
        self.assertEqual(due(), datetime.date(2026, 7, 25))
        self.assertEqual(due(), Bill.objects.get(pk=bill.pk).pay_by())


class PostedOutOfDateOrderTests(MsmeTestCase):
    """
    O499: an order line's deliveries go to its bills oldest bill first, by
    bill date (msme.lots), so a bill dated before one already posted takes
    the deliveries that one was read against, and that one's stored due day
    is written again (calc_g35_a/o499_split.py). Net 60, micro vendor, 6
    received 1 June and 4 on 20 June; the bill of 25 June for 4 posted first
    is due 16 July (from 1 June); then the bill of 5 June posts:
      for 6: it is due 16 July, the 25 June bill 4 August (from 20 June);
      for 3: the 25 June bill takes 3 of 1 June and 1 of 20 June: 4 August;
      for 2: the 25 June bill still takes 4 of 1 June: 16 July.
    The reverse, a debit note on the 5 June bill for all of it, moves no
    split (lots() nets neither notes nor returns): 4 August stands.
    """

    def setUp(self):
        super().setUp()
        self.order_, self.line = AcceptedOnDeliveryTests.order(self)
        AcceptedOnDeliveryTests.receive(self, self.order_, self.line, JUNE, "6")
        AcceptedOnDeliveryTests.receive(self, self.order_, self.line, datetime.date(2026, 6, 20), "4")
        self.late = AcceptedOnDeliveryTests.bill_for(self, self.order_, self.line, datetime.date(2026, 6, 25), "4")
        self.assertEqual(self.late.due_date, datetime.date(2026, 7, 16))

    def early(self, quantity):
        return AcceptedOnDeliveryTests.bill_for(self, self.order_, self.line, datetime.date(2026, 6, 5), quantity)

    def due(self, bill):
        bill = Bill.objects.get(pk=bill.pk)
        self.assertEqual(bill.due_date, bill.pay_by())
        return bill.due_date

    def test_a_bill_dated_before_one_posted_moves_that_ones_day(self):
        early = self.early("6")
        self.assertEqual((self.due(early), self.due(self.late)),
                         (datetime.date(2026, 7, 16), datetime.date(2026, 8, 4)))

    def test_drafted_before_the_bill_it_moves(self):
        """The bill it moves made after it: held once it is, by the lock order, and written again alike."""
        early = Bill.objects.create(vendor=self.vendor, bill_date=datetime.date(2026, 6, 5), payable_account=self.payable,
                                    payment_terms=self.net60, purchase_order=self.order_)
        BillLine.objects.create(bill=early, order_line=self.line, item=self.item, quantity=Decimal("4"),
                                unit_price=Decimal("100"), expense_account=self.expense)
        later = AcceptedOnDeliveryTests.bill_for(self, self.order_, self.line, datetime.date(2026, 6, 26), "2")
        self.assertGreater(later.pk, early.pk)
        self.assertEqual(later.due_date, datetime.date(2026, 7, 16))  # 4 + 2 of the 6 of 1 June
        early.post()
        # The 5 June bill takes 4 of 1 June; 25 June the last 2 of 1 June and 2 of 20 June; 26 June 2 of 20 June.
        self.assertEqual((self.due(early), self.due(self.late), self.due(later)),
                         (datetime.date(2026, 7, 16), datetime.date(2026, 8, 4), datetime.date(2026, 8, 4)))

    def test_only_as_far_as_its_split_moves(self):
        for quantity, due in (("3", datetime.date(2026, 8, 4)), ("2", datetime.date(2026, 7, 16))):
            with self.subTest(quantity=quantity):
                from django.db import transaction

                with transaction.atomic():
                    self.early(quantity)
                    self.assertEqual(self.due(self.late), due)
                    transaction.set_rollback(True)

    def test_read_through_the_api_as_the_ap_manager(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("ap-manager")
        user.groups.add(Group.objects.get(name="AP Manager"))
        client = APIClient()
        client.force_authenticate(user)
        early = Bill.objects.create(vendor=self.vendor, bill_date=datetime.date(2026, 6, 5), payable_account=self.payable,
                                    payment_terms=self.net60, purchase_order=self.order_)
        BillLine.objects.create(bill=early, order_line=self.line, item=self.item, quantity=Decimal("6"),
                                unit_price=Decimal("100"), expense_account=self.expense)
        response = client.post(f"/api/purchasing/bills/{early.pk}/post_bill/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        read = client.get(f"/api/purchasing/bills/{self.late.pk}/").json()
        self.assertEqual((read["due_date"], read["terms_due_date"]), ("2026-08-04", "2026-08-24"))

    def test_the_list_says_what_each_bill_alone_says(self):
        """O501: the page's Act days, read in one pass, are the bill's own, out of date order and noted."""
        early = self.early("3")
        early.create_debit_note(quantities={early.lines.get(): Decimal("1")})
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("ap-manager")
        user.groups.add(Group.objects.get(name="AP Manager"))
        client = APIClient()
        client.force_authenticate(user)
        listed = {row["id"]: row["act_pay_by"] for row in client.get("/api/purchasing/bills/").json()}
        alone = {pk: client.get(f"/api/purchasing/bills/{pk}/").json()["act_pay_by"] for pk in listed}
        model = {pk: (lambda day: day and day.isoformat())(Bill.objects.get(pk=pk).act_pay_by()) for pk in listed}
        self.assertEqual(listed, alone)
        self.assertEqual(listed, model)
        self.assertEqual((listed[early.pk], listed[self.late.pk]), ("2026-07-16", "2026-08-04"))

    def test_a_debit_note_on_the_earlier_bill_moves_nothing(self):
        early = self.early("6")
        early.create_debit_note(quantities={early.lines.get(): Decimal("6")})
        self.assertEqual((self.due(early), self.due(self.late)),
                         (datetime.date(2026, 7, 16), datetime.date(2026, 8, 4)))


class ActsDayListedTests(MsmeTestCase):
    """
    O501: the bills list reads each MSME bill's Act day (act_pay_by) for the
    whole page in one pass, not by msme.lots() asking a bill's order line
    for its earlier bills and receipts a bill at a time; and the list says
    what the bill alone says. Net 60, micro vendor, one order line of 20:
    10 received 1 June, 10 on 20 June; bills of 5 on 2, 3, 22 and 23 June
    take 1 June, 1 June, 20 June, 20 June: due 16, 16 July, 4, 4 August.
    """

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("ap-manager")
        user.groups.add(Group.objects.get(name="AP Manager"))
        self.client_ = APIClient()
        self.client_.force_authenticate(user)
        self.order_ = PurchaseOrder.objects.create(vendor=self.vendor, order_date=JUNE)
        self.line = PurchaseOrderLine.objects.create(order=self.order_, item=self.item, uom=self.uom,
                                                     quantity=Decimal("20"), unit_price=Decimal("100"))
        self.order_.confirm()
        AcceptedOnDeliveryTests.receive(self, self.order_, self.line, JUNE, "10")
        AcceptedOnDeliveryTests.receive(self, self.order_, self.line, datetime.date(2026, 6, 20), "10")

    def listed(self):
        response = self.client_.get("/api/purchasing/bills/")
        self.assertEqual(response.status_code, 200, response.content)
        return {row["id"]: row["act_pay_by"] for row in response.json()}

    def test_the_list_asks_no_more_for_more_bills(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        bills = [AcceptedOnDeliveryTests.bill_for(self, self.order_, self.line, datetime.date(2026, 6, day), "5")
                 for day in (2, 3)]
        self.listed()  # the first request of a test fills the caches every later one reads
        with CaptureQueriesContext(connection) as two:
            self.assertEqual(self.listed(), {bills[0].pk: "2026-07-16", bills[1].pk: "2026-07-16"})
        bills += [AcceptedOnDeliveryTests.bill_for(self, self.order_, self.line, datetime.date(2026, 6, day), "5")
                  for day in (22, 23)]
        with self.assertNumQueries(len(two.captured_queries)):
            self.assertEqual(self.listed(), dict(zip([bill.pk for bill in bills],
                                                     ["2026-07-16", "2026-07-16", "2026-08-04", "2026-08-04"])))


@tag("migration")
class TermsDayKeptMigrationTests(TransactionTestCase):
    """
    O498: bills posted before 0090 kept the terms' day as due_date. Each keeps it as its terms' day; a micro
    vendor's posted bill of 1 June on net 60 is then due on 16 July (after migrate), a medium vendor's stays
    on 31 July, and a draft has neither.
    """

    before = [("purchasing", "0089_taxes_taken_under")]
    after = [("purchasing", "0090_bill_keeps_its_terms_due_date")]

    def test_posted_bills_keep_the_terms_day_and_fall_due_by_the_act(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        from .msme import keep_unkept_due_dates

        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        model = executor.loader.project_state(self.before).apps.get_model
        micro = model("core", "Party").objects.create(code="M", name="Micro")
        medium = model("core", "Party").objects.create(code="D", name="Medium")
        payable = model("accounting", "Account").objects.create(code="2000", name="AP", account_type="liability")
        expense = model("accounting", "Account").objects.create(code="5000", name="Cones", account_type="expense")
        net60 = model("core", "PaymentTerms").objects.create(code="N60", name="Net 60", net_days=60)
        july = datetime.date(2026, 7, 31)
        for party, category, posted in ((micro, "micro", True), (medium, "medium", True), (micro, "", False)):
            bill = model("purchasing", "Bill").objects.create(
                vendor=party, bill_date=JUNE, payable_account=payable, payment_terms=net60, posted=posted,
                msme_category=category, due_date=july if posted else None)
            model("purchasing", "BillLine").objects.create(
                bill=bill, description="Cones", quantity=Decimal("1"), unit_price=Decimal("1000"),
                expense_account=expense)
        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        Bill = executor.loader.project_state(self.after).apps.get_model("purchasing", "Bill")
        days = lambda: sorted(Bill.objects.values_list("vendor__code", "posted", "terms_due_date", "due_date"))  # noqa: E731
        # The migration copies the terms' day; a micro vendor's posted bill waits for the code that reads the Act.
        self.assertEqual(days(), [("D", True, july, july), ("M", False, None, None), ("M", True, None, july)])
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        keep_unkept_due_dates()
        self.assertEqual(days(), [("D", True, july, july), ("M", False, None, None),
                                  ("M", True, july, datetime.date(2026, 7, 16))])
        keep_unkept_due_dates()  # nothing left to do
