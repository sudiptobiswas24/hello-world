"""Review probes for GST reverse charge (review_rcm): what SHOULD be true. Numbers: /home/user/work/calc_review_rcm/calc.py."""

import datetime
from decimal import Decimal as D
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APIClient

from apps.accounting.models import Payment, PaymentDirection
from apps.core.models import ExchangeRate, PartyRole
from apps.purchasing.models import Bill, BillLine, BillPayment
from apps.web.health import _self_invoices

from .returns import gstr3b
from .reverse_charge import GstCashPayment, PaymentVoucher, SelfInvoice, cash_ledger
from .tests import DAY
from .tests_rcm_cash import SEPTEMBER, ReverseChargeCashTestCase

SEP12, SEP20 = datetime.date(2026, 9, 12), datetime.date(2026, 9, 20)


class ReviewRcm(ReverseChargeCashTestCase):
    def test_1a_full_cycle_gta_unregistered_local_returns_every_control_account_to_zero(self):
        gta, truck, local = self.freight_bill(vendor=self.gta), self.freight_bill(), self.local_bill()
        SelfInvoice.issue(truck, on_date=DAY)
        SelfInvoice.issue(local, on_date=DAY)
        self.paid_supplier(gta, "10000")
        PaymentVoucher.issue_for(Payment.objects.get())
        self.pay()
        self.assertEqual([self.ledger(c) for c in ("2291", "2292", "1395")], [D("0.00")] * 3)
        paid = gstr3b(*SEPTEMBER)
        # input credit held in the books == what 4(A)(3) claims
        self.assertEqual(-self.ledger("1390"), sum(paid["4A3"][h] for h in ("igst", "cgst", "sgst", "cess")))
        self.assertEqual(self.ledger("1010"), D("11200.00"))  # credit-positive: 1,200 tax + 10,000 supplier

    def test_1b_part_note_before_the_challan_returns_to_zero(self):
        bill = self.freight_bill()
        SelfInvoice.issue(bill, on_date=DAY)
        bill.create_debit_note(quantities={bill.lines.get(): D("0.5")}, on_date=SEP12)
        self.pay()
        self.assertEqual([self.ledger(c) for c in ("2291", "1395")], [D("0.00")] * 2)
        self.assertEqual((-self.ledger("1390"), gstr3b(*SEPTEMBER)["4A3"]["igst"]), (D("250.00"), D("250.00")))

    def test_1c_fx_part_note_at_a_later_rate_returns_to_zero(self):
        ExchangeRate.objects.create(currency=self.usd, rate=D("83.3367"), valid_from=datetime.date(2026, 9, 1))
        ExchangeRate.objects.create(currency=self.usd, rate=D("84.1234"), valid_from=SEP12)
        abroad = self.party("ABROAD", role=PartyRole.VENDOR, currency=self.usd, gst_registration="overseas")
        bill = Bill.objects.create(vendor=abroad, bill_date=DAY, payable_account=self.ap, currency=self.usd)
        for _ in range(2):
            line = BillLine.objects.create(bill=bill, charge=self.freight, quantity=D("1"), unit_price=D("7.40"),
                                           expense_account=self.expense)
            line.taxes.set([self.igst5])
        bill.post()
        bill = Bill.objects.get(pk=bill.pk)
        bill.create_debit_note(quantities={ln: D("0.5") for ln in bill.lines.all()}, on_date=SEP20)
        self.pay()
        self.assertEqual([self.ledger(c) for c in ("2291", "1395")], [D("0.00")] * 2)
        self.assertEqual(-self.ledger("1390"), gstr3b(*SEPTEMBER)["4A3"]["igst"])

    def test_1d_whole_note_before_the_challan_leaves_nothing_owed_and_nothing_stuck(self):
        a, b = self.freight_bill(), self.freight_bill()
        SelfInvoice.issue(a, on_date=DAY)
        a.create_debit_note(on_date=SEP12)
        self.assertIsNotNone(SelfInvoice.objects.get().cancelled_on)
        self.pay()
        self.assertEqual([self.ledger("2291"), self.ledger("1395"), self.ledger("1390")],
                         [D("0.00"), D("0.00"), D("-500.00")])

    def test_2_thirty_day_limit_in_kolkata_at_29_30_31(self):
        bill = self.freight_bill()  # received 10 Sep: day 29 = 9 Oct, 30 = 10 Oct, 31 = 11 Oct
        ist = override_settings(TIME_ZONE="Asia/Kolkata")
        with ist:
            for utc, expect in (("2026-10-08 19:00", "ok"), ("2026-10-09 19:00", "ok"), ("2026-10-10 19:00", "late")):
                now = datetime.datetime.fromisoformat(utc).replace(tzinfo=datetime.timezone.utc)
                with mock.patch("django.utils.timezone.now", return_value=now):
                    if expect == "late":
                        with self.assertRaisesMessage(ValidationError, "Rule 47A"):
                            SelfInvoice.issue(bill)
                    else:
                        made = SelfInvoice.issue(bill)
                        made.cancel("again", on_date=made.date)
        with ist, mock.patch("django.utils.timezone.now", return_value=datetime.datetime(
                2026, 10, 10, 19, 0, tzinfo=datetime.timezone.utc)):  # 11 Oct 00:30 IST
            finding = _self_invoices(datetime.date(2026, 10, 11))
            self.assertIn("(overdue)", finding["rows"][0]["label"])

    def test_3_vouchers_of_a_fully_paid_bill_add_up_to_its_tax(self):
        bill = self.freight_bill(vendor=self.gta)
        for part in ("3333.33", "3333.33", "3333.34"):  # 500 x 3333.33 / 10,000 = 166.6665 -> 166.67 twice
            PaymentVoucher.issue_for(self.paid_supplier(bill, part))
        self.assertEqual(sum(v.igst for v in PaymentVoucher.objects.all()), D("500.00"))

    def test_4_credit_is_not_claimed_on_a_supply_with_no_self_invoice(self):
        self.freight_bill()  # unregistered trucker, nobody issued the self-invoice
        self.pay()
        self.assertEqual(gstr3b(*SEPTEMBER)["4A3"]["igst"], D("0.00"))

    def test_5_a_part_debit_note_leaves_the_self_invoice_at_the_supply_it_no_longer_is(self):
        bill = self.freight_bill()
        made = SelfInvoice.issue(bill, on_date=DAY)
        bill.create_debit_note(quantities={bill.lines.get(): D("0.5")}, on_date=SEP12)
        self.assertEqual(made.igst, gstr3b(*SEPTEMBER)["3.1d"]["igst"])

    def test_6_roles(self):
        call_command("setup_roles", verbosity=0)

        clients = {}

        def as_(role):
            if role in clients:
                return clients[role]
            user = User.objects.create_user(role.replace(" ", "_"))
            user.groups.add(Group.objects.get(name=role))
            client = APIClient()
            client.force_authenticate(user)
            clients[role] = client
            return client

        bill, gta = self.freight_bill(), self.freight_bill(vendor=self.gta)
        payment = self.paid_supplier(gta, "4000")
        data = {"month": "2026-09", "date": "2026-10-15", "bank_account": self.bank.pk,
                "cash_ledger_account": self.ecl.pk, "cpin": "12345678901234"}
        outcome = {}
        for role in ("Sales Rep", "Warehouse Staff", "Purchasing Clerk", "AR Manager", "HR Admin"):
            c = as_(role)
            outcome[role] = [c.post("/api/gst/self-invoices/", {"bill": bill.pk, "date": "2026-09-10"}, format="json").status_code,
                             c.post("/api/gst/payment-vouchers/", {"payment": payment.pk}, format="json").status_code,
                             c.post("/api/gst/cash-payments/pay/", data, format="json").status_code,
                             c.get("/api/gst/cash-payments/cash_ledger/").status_code,
                             c.get("/api/gst/self-invoices/").status_code,
                             c.get("/api/gst/payment-vouchers/").status_code,
                             c.get("/api/gst/cash-payments/").status_code,
                             c.get("/api/gst/self-invoices/awaiting/").status_code]
        print("ROLE MATRIX (issue SI, voucher, pay, ledger, list SI, list PV, list challans, awaiting)", outcome)
        for role, codes in outcome.items():
            self.assertEqual(codes, [403] * 8, role)
        officer, ap = as_("GST Officer"), as_("AP Manager")
        made = officer.post("/api/gst/self-invoices/", {"bill": bill.pk, "date": "2026-09-10"}, format="json")
        self.assertEqual(made.status_code, 201)
        self.assertEqual(ap.post(f"/api/gst/self-invoices/{made.data['id']}/cancel/", {"reason": "x"}, format="json").status_code, 403)
        self.assertEqual(ap.post("/api/gst/self-invoices/", {"bill": bill.pk, "date": "2026-09-10"}, format="json").status_code, 403)
        self.assertEqual(officer.post("/api/gst/payment-vouchers/", {"payment": payment.pk}, format="json").status_code, 403)
        paid = ap.post("/api/gst/cash-payments/pay/", data, format="json")
        self.assertEqual(paid.status_code, 201)
        self.assertEqual(officer.post(f"/api/gst/cash-payments/{paid.data['id']}/void/", {}, format="json").status_code, 403)
        for role in ("Sales Rep", "Purchasing Clerk"):
            self.assertEqual(as_(role).post(f"/api/gst/cash-payments/{paid.data['id']}/void/", {}, format="json").status_code, 403)
        self.assertEqual(ap.post(f"/api/gst/cash-payments/{paid.data['id']}/void/", {}, format="json").status_code, 200)

    def test_7_the_cash_ledger_and_books_agree_after_a_void_and_a_second_challan(self):
        self.freight_bill()
        first = self.pay()
        first.void(on_date=datetime.date(2026, 10, 20))
        self.pay(cpin="12345678901299", day=datetime.date(2026, 10, 21))
        ledger = cash_ledger(self.ecl)
        self.assertEqual((ledger["difference"], self.ledger("2291"), self.ledger("1010")), (D("0"), D("0.00"), D("500.00")))
        self.assertEqual(GstCashPayment.objects.filter(voided_entry__isnull=True).count(), 1)


class ReviewRcmLedgerLeak(ReverseChargeCashTestCase):
    def test_8_the_cash_ledger_endpoint_reads_only_a_cash_ledger_account(self):
        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("officer8")
        user.groups.add(Group.objects.get(name="GST Officer"))
        client = APIClient()
        client.force_authenticate(user)
        self.freight_bill()  # puts 500 on the bank-side account 1010 as a credit once paid
        self.pay()
        reply = client.get(f"/api/gst/cash-payments/cash_ledger/?account={self.ap.pk}")  # a payables control account
        print("LEDGER LEAK status", reply.status_code, getattr(reply, "data", None) and reply.data.get("ledger"))
        self.assertIn(reply.status_code, (400, 403, 404))


class ReviewRcmReceiptDay(ReverseChargeCashTestCase):
    def test_9_the_day_of_receipt_is_the_goods_receipt_not_an_earlier_bill_date(self):
        """Bill dated 1 Sep, goods received 20 Sep (its docstring: 'the first goods receipt ..., else the bill's own date')."""
        from types import SimpleNamespace

        from .reverse_charge import received_on

        line = SimpleNamespace(order_line_id=1, order_line=object())
        bill = SimpleNamespace(bill_date=datetime.date(2026, 9, 1), lines=SimpleNamespace(all=lambda: [line]))
        with mock.patch("apps.purchasing.models._receipt_dates", return_value=[(datetime.date(2026, 9, 20), D("1"))]):
            self.assertEqual(received_on(bill), datetime.date(2026, 9, 20))


class ReviewRcmCancelledAfterPaid(ReverseChargeCashTestCase):
    def test_4b_a_self_invoice_cancelled_after_the_tax_is_paid_takes_its_credit_back(self):
        made = SelfInvoice.issue(self.freight_bill(), on_date=DAY)
        self.pay()
        made.cancel("wrong supplier", on_date=datetime.date(2026, 10, 16))
        self.assertEqual(gstr3b(*SEPTEMBER)["4A3"]["igst"], D("0.00"))
