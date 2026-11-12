"""
GSTR-3B table 6.1's working: the tax payable split into what credit may
set off and what must be paid in cash, reverse charge always cash (CGST
Act s.49(4), s.2(82)), the cash footed to the ledger. September 2026
(calc_g38_purch/msme_interest.py, its last line):

  sold 1,00,000 within the state at 9% + 9%:      output CGST 9,000, SGST 9,000
  bought 50,000 from a registered supplier:       credit CGST 4,500, SGST 4,500
  freight 10,000 under reverse charge, IGST 5%:   500 owed into 2291
  local 4,000 under reverse charge, 2.5% + 2.5%:  100 + 100 owed into 2292

  payable 500 / 9,100 / 9,100; credit may set off 0 / 9,000 / 9,000;
  cash only 500 / 100 / 100; owed in the ledger 700.
  Paid by challan: nothing still to pay, the ledger at nothing, and the
  credit of the month 500 / 4,600 / 4,600. The challan voided: 700 again.
"""

import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.accounting.models import JournalEntry, JournalLine
from apps.core.models import PartyRole

from .returns import _moved, gstr3b
from .reverse_charge import SelfInvoice, owed_against_the_ledger
from .tests import DAY, gstin
from .tests_rcm_cash import SEPTEMBER, ReverseChargeCashTestCase


def heads(igst, cgst, sgst):
    return {"igst": D(igst), "cgst": D(cgst), "sgst": D(sgst), "cess": D("0")}


class PaymentOfTaxWorkingTests(ReverseChargeCashTestCase):
    def setUp(self):
        super().setUp()
        self.sell(self.party("BUYER", gstin=gstin("27AAACB2222E1Z")), "100000")
        self.buy(self.party("SUPPLIER", role=PartyRole.VENDOR, gstin=gstin("27AAACS3333F1Z")), "50000")
        self.freight_one, self.local_one = self.freight_bill(), self.local_bill()

    def working(self):
        return gstr3b(*SEPTEMBER)["6.1_working"]

    def footing(self):
        found = owed_against_the_ledger()
        return found["owed"], found["paid"], found["to_pay"], found["ledger"], found["agrees"]

    def test_reverse_charge_is_cash_and_the_rest_is_what_credit_may_set_off(self):
        found = self.working()
        self.assertEqual(found["payable"], heads("500.00", "9100.00", "9100.00"))
        self.assertEqual(found["credit_may_set_off"], heads("0", "9000.00", "9000.00"))
        self.assertEqual(found["credit_of_the_period"], heads("0", "4500.00", "4500.00"))
        self.assertEqual(found["cash_only"], heads("500.00", "100.00", "100.00"))
        self.assertEqual(found["paid_in_cash"], heads("0", "0", "0"))
        self.assertEqual(found["still_to_pay_in_cash"], heads("500.00", "100.00", "100.00"))
        # The two parts are the whole, head by head.
        for head in ("igst", "cgst", "sgst", "cess"):
            self.assertEqual(found["credit_may_set_off"][head] + found["cash_only"][head], found["payable"][head])
        self.assertEqual(self.footing(), (D("700.00"), D("0"), D("700.00"), D("700.00"), True))
        self.assertEqual([(row["code"], row["to_pay"], row["ledger"]) for row in found["ledger"]["accounts"]],
                         [("2291", D("500.00"), D("500.00")), ("2292", D("200.00"), D("200.00"))])

    def test_the_challan_pays_it_and_its_void_owes_it_again(self):
        SelfInvoice.issue(self.freight_one, on_date=DAY)
        SelfInvoice.issue(self.local_one, on_date=DAY)
        challan = self.pay()
        found = self.working()
        self.assertEqual(found["paid_in_cash"], heads("500.00", "100.00", "100.00"))
        self.assertEqual(found["still_to_pay_in_cash"], heads("0.00", "0.00", "0.00"))
        self.assertEqual(found["credit_of_the_period"], heads("500.00", "4600.00", "4600.00"))
        self.assertEqual(found["cash_only"], heads("500.00", "100.00", "100.00"))  # owed is owed, paid or not
        self.assertEqual(self.footing(), (D("700.00"), D("700.00"), D("0.00"), D("0.00"), True))
        challan.void(on_date=datetime.date(2026, 10, 16))
        found = self.working()
        self.assertEqual(found["still_to_pay_in_cash"], heads("500.00", "100.00", "100.00"))
        self.assertEqual(found["credit_of_the_period"], heads("0", "4500.00", "4500.00"))
        self.assertEqual(self.footing(), (D("700.00"), D("0"), D("700.00"), D("700.00"), True))

    def test_a_debit_note_takes_its_tax_off_what_is_owed_and_off_the_ledger(self):
        self.freight_one.create_debit_note(quantities={self.freight_one.lines.get(): D("0.5")})
        self.assertEqual(self.footing(), (D("450.00"), D("0"), D("450.00"), D("450.00"), True))

    def test_a_journal_made_by_hand_on_the_account_is_shown_as_the_difference(self):
        entry = JournalEntry.objects.create(date=DAY, memo="by hand")
        JournalLine.objects.create(entry=entry, account=self.rcm_igst, credit=D("50"))
        JournalLine.objects.create(entry=entry, account=self.expense, debit=D("50"))
        entry.post()
        found = owed_against_the_ledger()
        self.assertEqual((found["to_pay"], found["ledger"], found["difference"], found["agrees"]),
                         (D("700.00"), D("750.00"), D("50.00"), False))
        [warning] = [w for w in gstr3b(*SEPTEMBER)["warnings"] if "something else moved them" in w]
        self.assertIn("700.00 by the bills and challans, and 750.00 in the ledger", warning)

    def test_the_working_is_not_a_filed_figure(self):
        was = gstr3b(*SEPTEMBER)
        self.pay()
        self.assertNotIn("6.1_working", _moved(was, gstr3b(*SEPTEMBER)))

    def test_the_officer_reads_it_and_the_warehouse_does_not(self):
        call_command("setup_roles", verbosity=0)

        def as_(role):
            user = User.objects.create_user(role.replace(" ", "_"))
            user.groups.add(Group.objects.get(name=role))
            client = APIClient()
            client.force_authenticate(user)
            return client

        self.assertEqual(as_("Warehouse Staff").get("/api/gst/gstr3b/?period=2026-09").status_code, 403)
        found = as_("GST Officer").get("/api/gst/gstr3b/?period=2026-09").json()["6.1_working"]
        self.assertEqual((found["payable"]["cgst"], found["credit_may_set_off"]["cgst"], found["cash_only"]["cgst"],
                          found["cash_only"]["igst"], found["ledger"]["to_pay"], found["ledger"]["ledger"],
                          found["ledger"]["agrees"]),
                         ("9100.00", "9000.00", "100.00", "500.00", "700.00", "700.00", True))
