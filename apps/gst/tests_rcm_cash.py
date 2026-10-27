"""
Reverse charge after the books: the self-invoice (s.31(3)(f), Rule 47A), the payment voucher
(s.31(3)(g)), the tax paid in cash (s.2(82), s.49(4)) and GSTR-3B 6.1. Figures worked in
/home/user/work/calc_fix_rcm/rcm.py:

  unregistered trucker, freight 10,000 on 10 Sep 2026, IGST 5% under reverse charge = 500
  self-invoice due within 30 days of receipt: 10 Oct 2026 is the last day, 11 Oct refused
  local unregistered supplier 4,000 at CGST 2.5% + SGST 2.5% = 100 + 100
  September challan: deposit 700 (IGST 500, CGST 100, SGST 100), set off 700; bank -700, payable 0
  3.1(d) 14,000 and 500/100/100; 4(A)(3) 0 before the challan, 500/100/100 after; 6.1 cash 500/100/100
  half the freight debited back in September first: IGST 250, challan 450
  voucher on a payment of 4,000 of the 10,000 bill: IGST 200; of 3,333: 166.65
"""

import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType, Payment, PaymentDirection, Tax, TaxScope
from apps.core.models import PartyRole
from apps.purchasing.models import Bill, BillLine, BillPayment
from apps.web.health import _gst_cash_ledger, _payment_vouchers, _self_invoices

from .returns import gstr3b
from .reverse_charge import GstCashPayment, PaymentVoucher, SelfInvoice, awaiting_self_invoice, cash_ledger
from .tests import DAY, GstReturnTestCase, gstin

SEPTEMBER = (DAY.replace(day=1), DAY.replace(day=30))
OCTOBER = (datetime.date(2026, 10, 1), datetime.date(2026, 10, 31))


class ReverseChargeCashTestCase(GstReturnTestCase):
    def setUp(self):
        super().setUp()

        def account(code, kind, **more):
            return Account.objects.create(code=code, name=code, account_type=kind, **more)

        self.rcm_igst = account("2291", AccountType.LIABILITY)
        self.rcm_local = account("2292", AccountType.LIABILITY)
        self.itc = account("1390", AccountType.ASSET)
        self.bank = account("1010", AccountType.ASSET, holds_money=True)
        self.ecl = account("1395", AccountType.ASSET)

        def rcm(code, rate, head, owed):
            return Tax.objects.create(code=code, name=code, rate=D(rate), gst_head=head, scope=TaxScope.PURCHASE,
                                      paid_account=self.itc, reverse_charge=True, reverse_charge_account=owed)

        self.igst5 = rcm("IGST5RC", "5", "igst", self.rcm_igst)
        self.cgst25 = rcm("CGST25RC", "2.5", "cgst", self.rcm_local)
        self.sgst25 = rcm("SGST25RC", "2.5", "sgst", self.rcm_local)
        self.trucker = self.party("TRUCKER", role=PartyRole.VENDOR, gst_state="29", gst_registration="unregistered")
        self.local = self.party("LOCAL", role=PartyRole.VENDOR, gst_state="27", gst_registration="unregistered")
        self.gta = self.party("GTA", role=PartyRole.VENDOR, gstin=gstin("29AABCV4444D1Z"))

    def bill(self, vendor, amount, taxes, day=DAY):
        bill = Bill.objects.create(vendor=vendor, bill_date=day, payable_account=self.ap)
        line = BillLine.objects.create(bill=bill, charge=self.freight, quantity=D("1"), unit_price=D(amount),
                                       expense_account=self.expense)
        line.taxes.set(taxes)
        bill.post()
        return Bill.objects.get(pk=bill.pk)

    def freight_bill(self, vendor=None, day=DAY):
        return self.bill(vendor or self.trucker, "10000", [self.igst5], day)

    def local_bill(self):
        return self.bill(self.local, "4000", [self.cgst25, self.sgst25])

    def pay(self, cpin="12345678901234", day=datetime.date(2026, 10, 15), month="2026-09"):
        return GstCashPayment.pay(month, day, self.bank, self.ecl, cpin)

    def paid_supplier(self, bill, amount, day=datetime.date(2026, 9, 15)):
        payment = Payment.objects.create(party=bill.vendor, direction=PaymentDirection.DISBURSEMENT,
                                         payment_date=day, amount=D(amount), bank_account=self.bank,
                                         counterpart_account=self.ap)
        payment.post()
        BillPayment.objects.create(bill=bill, payment=payment, amount=D(amount))
        return Payment.objects.get(pk=payment.pk)


class SelfInvoiceTests(ReverseChargeCashTestCase):
    def test_issued_from_the_bill_in_its_own_series(self):
        bill = self.freight_bill()
        made = SelfInvoice.issue(bill, on_date=DAY)
        self.assertEqual((made.number, made.received_on, made.taxable, made.igst, made.cgst),
                         ("SI-2026-00001", DAY, D("10000.00"), D("500.00"), D("0.00")))
        second = SelfInvoice.issue(self.local_bill(), on_date=DAY)
        self.assertEqual((second.number, second.cgst, second.sgst), ("SI-2026-00002", D("100.00"), D("100.00")))

    def test_the_thirtieth_day_is_the_last(self):
        bill = self.freight_bill()
        with self.assertRaisesMessage(ValidationError, "by 10 Oct 2026 (CGST Rule 47A)"):
            SelfInvoice.issue(bill, on_date=datetime.date(2026, 10, 11))
        self.assertEqual(SelfInvoice.issue(bill, on_date=datetime.date(2026, 10, 10)).number, "SI-2026-00001")

    def test_refusals(self):
        with self.assertRaisesMessage(ValidationError, "the supplier's own tax invoice is the document"):
            SelfInvoice.issue(self.freight_bill(vendor=self.gta), on_date=DAY)
        bill = self.freight_bill()
        with self.assertRaisesMessage(ValidationError, "the supply was received on 2026-09-10"):
            SelfInvoice.issue(bill, on_date=datetime.date(2026, 9, 9))
        SelfInvoice.issue(bill, on_date=DAY)
        with self.assertRaisesMessage(ValidationError, "has a self-invoice standing"):
            SelfInvoice.issue(bill, on_date=DAY)
        plain = self.bill(self.trucker, "500", [])
        with self.assertRaisesMessage(ValidationError, "bears no tax under reverse charge"):
            SelfInvoice.issue(plain, on_date=DAY)

    def test_cancelled_never_edited_or_deleted(self):
        made = SelfInvoice.issue(self.freight_bill(), on_date=DAY)
        made.igst = D("1")
        with self.assertRaisesMessage(ValidationError, "cancel it and issue another"):
            made.save()
        with self.assertRaisesMessage(ValidationError, "cancel it"):
            SelfInvoice.objects.get(pk=made.pk).delete()
        made = SelfInvoice.objects.get(pk=made.pk)
        with self.assertRaisesMessage(ValidationError, "is not cancelled on 2026-09-09: it was issued on 2026-09-10"):
            made.cancel("wrong supplier", on_date=datetime.date(2026, 9, 9))
        with self.assertRaisesMessage(ValidationError, "Say why"):
            made.cancel("", on_date=DAY)
        made.cancel("wrong figures", on_date=datetime.date(2026, 9, 12))
        self.assertEqual(SelfInvoice.objects.get(pk=made.pk).cancelled_on, datetime.date(2026, 9, 12))
        again = SelfInvoice.issue(made.bill, on_date=datetime.date(2026, 9, 12))
        self.assertEqual(again.number, "SI-2026-00002")

    def test_the_health_line_lists_every_supply_without_one(self):
        bill = self.freight_bill()
        self.freight_bill(vendor=self.gta)  # registered: its own invoice is the document
        self.assertEqual([(found.number, last) for found, _, last in awaiting_self_invoice()],
                         [(bill.number, datetime.date(2026, 10, 10))])
        finding = _self_invoices(datetime.date(2026, 10, 26))
        self.assertEqual((finding["ok"], finding["count"]), (False, 1))
        self.assertIn("(overdue)", finding["rows"][0]["label"])
        SelfInvoice.issue(bill, on_date=DAY)
        self.assertEqual(_self_invoices(DAY)["ok"], True)

    def test_a_whole_debit_note_cancels_it_and_a_part_one_does_not(self):
        bill = self.freight_bill()
        made = SelfInvoice.issue(bill, on_date=DAY)
        half = bill.create_debit_note(quantities={bill.lines.get(): D("0.5")})
        Bill.objects.filter(pk=half.pk).update(bill_date=datetime.date(2026, 9, 20))
        self.assertIsNone(SelfInvoice.objects.get(pk=made.pk).cancelled_on)
        rest = bill.create_debit_note(quantities={bill.lines.get(): D("0.5")})
        made = SelfInvoice.objects.get(pk=made.pk)
        self.assertEqual(made.cancelled_on, max(rest.bill_date, DAY))
        self.assertIn(rest.number, made.cancellation_reason)
        self.assertEqual(awaiting_self_invoice(), [])
        with self.assertRaisesMessage(ValidationError, "took back all of"):
            SelfInvoice.issue(bill, on_date=DAY)

    def test_a_supplier_who_registers_mid_month(self):
        before = self.freight_bill(day=datetime.date(2026, 9, 5))
        profile = self.trucker.tax_profile
        profile.gstin, profile.gst_registration = gstin("29AABCT5555E1Z"), "regular"
        profile.save()
        after = self.freight_bill(day=datetime.date(2026, 9, 20))
        self.assertEqual([bill.pk for bill, _, _ in awaiting_self_invoice()], [before.pk])
        with self.assertRaisesMessage(ValidationError, "was registered"):
            SelfInvoice.issue(after, on_date=datetime.date(2026, 9, 20))
        self.assertEqual(SelfInvoice.issue(before, on_date=datetime.date(2026, 9, 20)).igst, D("500.00"))


class CashPaymentTests(ReverseChargeCashTestCase):
    def test_paid_in_cash_through_the_cash_ledger_and_then_claimed(self):
        self.freight_bill()
        self.local_bill()
        before = gstr3b(*SEPTEMBER)
        self.assertEqual(before["4A3"], {"igst": D("0"), "cgst": D("0"), "sgst": D("0"), "cess": D("0")})
        self.assertEqual(len([w for w in before["warnings"] if w.startswith("Reverse charge")]), 3)
        payment = self.pay()
        self.assertEqual((payment.igst, payment.cgst, payment.sgst, payment.total()),
                         (D("500.00"), D("100.00"), D("100.00"), D("700.00")))
        self.assertEqual((self.ledger("2291"), self.ledger("2292"), self.ledger("1010"), self.ledger("1395")),
                         (D("0.00"), D("0.00"), D("700.00"), D("0.00")))
        result = gstr3b(*SEPTEMBER)
        self.assertEqual(result["3.1d"], {"taxable": D("14000.00"), "igst": D("500.00"), "cgst": D("100.00"),
                                          "sgst": D("100.00"), "cess": D("0")})
        paid = {"igst": D("500.00"), "cgst": D("100.00"), "sgst": D("100.00"), "cess": D("0")}
        self.assertEqual(result["4A3"], paid)
        self.assertEqual(result["6.1"]["reverse_charge"]["paid_in_cash"], paid)
        self.assertEqual(result["6.1"]["reverse_charge"]["paid_through_itc"]["igst"], D("0"))
        self.assertEqual([w for w in result["warnings"] if w.startswith("Reverse charge")], [])
        ledger = cash_ledger(self.ecl)
        self.assertEqual((ledger["heads"]["igst"]["deposited"], ledger["heads"]["igst"]["set_off"],
                          ledger["ledger"], ledger["difference"]), (D("500.00"), D("500.00"), D("0.00"), D("0.00")))
        self.assertTrue(_gst_cash_ledger(DAY)["ok"])
        with self.assertRaisesMessage(ValidationError, "No reverse-charge tax of September 2026 is waiting"):
            self.pay(cpin="12345678901235")

    def test_refusals(self):
        self.freight_bill()
        with self.assertRaisesMessage(ValidationError, "14 digits"):
            self.pay(cpin="1234")
        with self.assertRaisesMessage(ValidationError, "not a bank, cash or card account"):
            GstCashPayment.pay("2026-09", datetime.date(2026, 10, 15), self.ecl, self.itc, "12345678901234")
        with self.assertRaisesMessage(ValidationError, "an asset account of its own"):
            GstCashPayment.pay("2026-09", datetime.date(2026, 10, 15), self.bank, self.rcm_igst, "12345678901234")
        with self.assertRaisesMessage(ValidationError, "the last of it is dated 10 Sep 2026"):
            self.pay(day=datetime.date(2026, 9, 9))
        with self.assertRaisesMessage(ValidationError, "No reverse-charge tax of October 2026"):
            self.pay(month="2026-10")

    def test_voided_the_tax_is_owed_again_and_the_credit_goes(self):
        self.freight_bill()
        payment = self.pay()
        with self.assertRaisesMessage(ValidationError, "it was paid on 2026-10-15"):
            payment.void(on_date=datetime.date(2026, 10, 14))
        payment.void(on_date=datetime.date(2026, 10, 20))
        self.assertEqual((self.ledger("2291"), self.ledger("1010"), self.ledger("1395")),
                         (D("500.00"), D("0.00"), D("0.00")))
        result = gstr3b(*SEPTEMBER)
        self.assertEqual((result["4A3"]["igst"], result["6.1"]["reverse_charge"]["paid_in_cash"]["igst"]),
                         (D("0"), D("0")))
        with self.assertRaisesMessage(ValidationError, "already been voided"):
            GstCashPayment.objects.get(pk=payment.pk).void()
        again = self.pay(cpin="12345678901299", day=datetime.date(2026, 10, 21))
        self.assertEqual(again.igst, D("500.00"))
        self.assertTrue(_gst_cash_ledger(DAY)["ok"])
        payment.cpin = "99999999999999"
        with self.assertRaisesMessage(ValidationError, "void it"):
            payment.save()

    def test_a_debit_note_before_the_challan_pays_less_and_after_it_is_not_paid_back(self):
        bill = self.freight_bill()
        self.local_bill()
        note = bill.create_debit_note(quantities={bill.lines.get(): D("0.5")})
        Bill.objects.filter(pk=note.pk).update(bill_date=datetime.date(2026, 9, 20))
        payment = self.pay()
        self.assertEqual((payment.igst, payment.total()), (D("250.00"), D("450.00")))
        self.assertEqual((self.ledger("2291"), self.ledger("2292")), (D("0.00"), D("0.00")))
        later = self.freight_bill(day=datetime.date(2026, 9, 25))
        self.pay(cpin="12345678901236", day=datetime.date(2026, 10, 15))
        back = later.create_debit_note(quantities={later.lines.get(): D("1")})
        Bill.objects.filter(pk=back.pk).update(bill_date=datetime.date(2026, 10, 5))
        with self.assertRaisesMessage(ValidationError, "nets to IGST -500.00"):
            self.pay(cpin="12345678901237", day=datetime.date(2026, 10, 20), month="2026-10")
        self.assertEqual(gstr3b(*OCTOBER)["3.1d"]["igst"], D("-500.00"))

    def test_it_clears_the_account_the_tax_was_owed_into_not_the_one_named_now(self):
        self.freight_bill()
        moved = Account.objects.create(code="2299", name="moved", account_type=AccountType.LIABILITY)
        self.igst5.reverse_charge_account = moved
        self.igst5.save()
        self.pay()
        self.assertEqual((self.ledger("2291"), self.ledger("2299")), (D("0.00"), D("0.00")))


class PaymentVoucherTests(ReverseChargeCashTestCase):
    def test_issued_with_the_payment_at_its_share_of_the_tax_and_cancelled_with_its_void(self):
        bill = self.freight_bill(vendor=self.gta)
        payment = self.paid_supplier(bill, "4000")
        self.assertEqual(_payment_vouchers(DAY)["count"], 1)
        [voucher] = PaymentVoucher.issue_for(payment)
        self.assertEqual((voucher.number, voucher.date, voucher.amount, voucher.igst),
                         ("PV-2026-00001", datetime.date(2026, 9, 15), D("4000.00"), D("200.00")))
        self.assertTrue(_payment_vouchers(DAY)["ok"])
        with self.assertRaisesMessage(ValidationError, "without a voucher"):
            PaymentVoucher.issue_for(payment)
        other = self.paid_supplier(bill, "3333")
        self.assertEqual(PaymentVoucher.issue_for(other)[0].igst, D("166.65"))
        payment.void(on_date=datetime.date(2026, 9, 18))
        self.assertEqual(PaymentVoucher.objects.get(pk=voucher.pk).cancelled_on, datetime.date(2026, 9, 18))
        self.assertTrue(_payment_vouchers(DAY)["ok"])
        with self.assertRaisesMessage(ValidationError, "not a posted payment standing"):
            PaymentVoucher.issue_for(Payment.objects.get(pk=payment.pk))


class ReverseChargeApiTests(ReverseChargeCashTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_officer_issues_and_cancels_the_ap_manager_pays(self):
        bill = self.freight_bill()
        officer, ap, clerk = self.as_("GST Officer"), self.as_("AP Manager"), self.as_("Purchasing Clerk")
        self.assertEqual(clerk.post("/api/gst/self-invoices/", {"bill": bill.pk}, format="json").status_code, 403)
        response = officer.post("/api/gst/self-invoices/", {"bill": bill.pk, "date": "2026-09-10"}, format="json")
        self.assertEqual((response.status_code, response.data["number"], response.data["igst"]),
                         (201, "SI-2026-00001", "500.00"))
        late = officer.post("/api/gst/self-invoices/", {"bill": self.freight_bill().pk, "date": "2026-10-11"},
                            format="json")
        self.assertEqual(late.status_code, 400)
        self.assertIn("Rule 47A", str(late.data))
        self.assertEqual(len(officer.get("/api/gst/self-invoices/awaiting/").data), 1)
        cancel = officer.post(f"/api/gst/self-invoices/{response.data['id']}/cancel/",
                              {"reason": "typed twice", "date": "2026-09-11"}, format="json")
        self.assertEqual((cancel.status_code, cancel.data["cancelled_on"]), (200, "2026-09-11"))
        data = {"month": "2026-09", "date": "2026-10-15", "bank_account": self.bank.pk,
                "cash_ledger_account": self.ecl.pk, "cpin": "12345678901234"}
        self.assertEqual(officer.post("/api/gst/cash-payments/pay/", data, format="json").status_code, 403)
        paid = ap.post("/api/gst/cash-payments/pay/", data, format="json")
        self.assertEqual((paid.status_code, paid.data["igst"]), (201, "1000.00"))
        self.assertEqual(officer.get("/api/gst/gstr3b/?period=2026-09").data["6.1"]["reverse_charge"]
                         ["paid_in_cash"]["igst"], D("1000.00"))
        ledger = officer.get(f"/api/gst/cash-payments/cash_ledger/?account={self.ecl.pk}")
        self.assertEqual((ledger.status_code, ledger.data["difference"]), (200, D("0.00")))
        voided = ap.post(f"/api/gst/cash-payments/{paid.data['id']}/void/", {"date": "2026-10-16"}, format="json")
        self.assertEqual((voided.status_code, voided.data["voided"]), (200, True))


class KeptAccountAndRoundingTests(ReverseChargeCashTestCase):
    """
    The debit note gives back into the account its bill owed into, and every side books each tax in base,
    rounded, as the challan pays it (/home/user/work/calc_fix_rcm/fx_drift.py):
      0.37 + 0.37 USD at 83.3367: rows 30.83 + 30.83 = 61.66; summed first, 61.67, and a paisa left for good.
    """

    def test_a_debit_note_gives_back_into_the_bills_account_after_the_tax_moved(self):
        bill = self.freight_bill()
        moved = Account.objects.create(code="2299", name="moved", account_type=AccountType.LIABILITY)
        self.igst5.reverse_charge_account = moved
        self.igst5.save()
        note = bill.create_debit_note(quantities={bill.lines.get(): D("0.5")})
        Bill.objects.filter(pk=note.pk).update(bill_date=datetime.date(2026, 9, 20))
        self.assertEqual((self.ledger("2291"), self.ledger("2299")), (D("250.00"), D("0.00")))
        self.pay()
        self.assertEqual((self.ledger("2291"), self.ledger("2299"), self.ledger("1395")),
                         (D("0.00"), D("0.00"), D("0.00")))

    def test_a_rate_that_does_not_come_out_even_foots_to_the_paisa(self):
        from apps.core.models import ExchangeRate

        ExchangeRate.objects.create(currency=self.usd, rate=D("83.3367"), valid_from=datetime.date(2026, 9, 1))
        abroad = self.party("ABROAD", role=PartyRole.VENDOR, currency=self.usd, gst_registration="overseas")
        bill = Bill.objects.create(vendor=abroad, bill_date=DAY, payable_account=self.ap, currency=self.usd)
        for _ in range(2):
            line = BillLine.objects.create(bill=bill, charge=self.freight, quantity=D("1"), unit_price=D("7.40"),
                                           expense_account=self.expense)
            line.taxes.set([self.igst5])
        bill.post()
        self.assertEqual((self.ledger("2291"), self.ledger("2000")), (D("61.66"), D("1233.38")))
        self.assertEqual(gstr3b(*SEPTEMBER)["3.1d"]["igst"], D("61.66"))
        payment = self.pay()
        self.assertEqual(payment.igst, D("61.66"))
        self.assertEqual((self.ledger("2291"), self.ledger("1395"), self.ledger("1010")),
                         (D("0.00"), D("0.00"), D("61.66")))
        self.assertTrue(_gst_cash_ledger(DAY)["ok"])

    def test_the_audit_sees_the_account_read_through_the_tax(self):
        from apps.core.management.commands.audit_invariants import Command, app_sources

        def findings(old, new):
            sources = app_sources()
            sources["purchasing"] = {path: text.replace(old, new) if path.name == "models.py" else text
                                     for path, text in sources["purchasing"].items()}
            return [text for _, text in Command().kept_settings_read_live(["purchasing"], sources)
                    if "reverse_charge_account" in text]

        self.assertEqual(findings("", ""), [])
        planted = findings("reverse_totals[owed_account_at_posting(line, tax)] -= base",
                           "reverse_totals[tax.reverse_charge_account] -= base")
        self.assertEqual(len(planted), 1)
        self.assertIn("_build_journal_entry reads `tax.reverse_charge_account`", planted[0])
        keyed = findings("self.owed_account = owed_account_at_posting(self.line, self.tax)",
                         "self.owed_account_id = self.tax.reverse_charge_account_id")
        self.assertTrue(any("self.tax.reverse_charge_account_id" in text for text in keyed), keyed)


class CashLedgerReadTests(ReverseChargeApiTests):
    """review_rcm F1: the cash-ledger view answers for the cash ledger and the reverse-charge payables only."""

    def test_only_its_own_accounts_and_only_for_those_who_may(self):
        self.freight_bill()
        self.pay()
        officer, clerk = self.as_("GST Officer"), self.as_("Purchasing Clerk")
        url = "/api/gst/cash-payments/cash_ledger/?account="
        self.assertEqual(officer.get(f"{url}{self.ecl.pk}").status_code, 200)
        self.assertEqual(officer.get(f"{url}{self.rcm_igst.pk}").status_code, 200)
        refused = officer.get(f"{url}{self.ap.pk}")
        self.assertEqual(refused.status_code, 400)
        self.assertNotIn("ledger", refused.data)
        self.assertEqual(officer.get(f"{url}{self.bank.pk}").status_code, 400)
        self.assertEqual(clerk.get(f"{url}{self.ecl.pk}").status_code, 403)


class ReceivedOnTests(ReverseChargeCashTestCase):
    """review_rcm F3: a service with no receipt document is received on its bill's date."""

    def test_a_service_is_received_on_its_bills_date(self):
        from .reverse_charge import received_on

        bill = self.freight_bill(day=datetime.date(2026, 9, 3))
        self.assertEqual(received_on(bill), datetime.date(2026, 9, 3))
        self.assertEqual(SelfInvoice.issue(bill, on_date=datetime.date(2026, 10, 3)).received_on,
                         datetime.date(2026, 9, 3))


class VoucherRemainderTests(ReverseChargeCashTestCase):
    """review_rcm F4: 3333.33 + 3333.33 + 3333.34 of 10,000 at IGST 500: 166.67 + 166.67 + 166.66 = 500.00."""

    def test_the_payment_that_settles_the_bill_takes_the_remainder(self):
        bill = self.freight_bill(vendor=self.gta)
        made = [PaymentVoucher.issue_for(self.paid_supplier(bill, part))[0].igst
                for part in ("3333.33", "3333.33", "3333.34")]
        self.assertEqual(made, [D("166.67"), D("166.67"), D("166.66")])
