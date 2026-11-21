"""
A bill that posts with tax under reverse charge from a supplier not
registered is given its self-invoice as it posts (s.31(3)(f)), dated that
day, when that is within Rule 47A's thirty days of receiving the supply.
Freight of 10,000, IGST 5%: taxable 10,000.00, tax 500.00.

  not raised: a registered supplier (its own invoice is the document); a
              bill with no reverse-charge tax; a supply received 31 days
              ago (it waits, for a person and the tax adviser).
  raised:     received today; received 30 days ago, the last day.
  reverse:    a debit note for the whole supply cancels it; one for half
              leaves it standing.
  edit, delete: refused; it is cancelled with a reason.
"""

import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.purchasing.models import Bill, BillLine

from .reverse_charge import SelfInvoice, awaiting_self_invoice
from .tests_rcm_cash import INVOICE_NUMBERS, ReverseChargeCashTestCase


class RaisedAsTheBillPostsTests(ReverseChargeCashTestCase):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()

    def standing(self, bill):
        return list(SelfInvoice.objects.filter(bill=bill, cancelled_on__isnull=True))

    def test_not_raised_for_a_registered_supplier_or_a_bill_with_no_reverse_charge(self):
        self.assertEqual(self.standing(self.freight_bill(vendor=self.gta, day=self.today)), [])
        self.assertEqual(self.standing(self.bill(self.trucker, "10000", [], self.today)), [])
        self.assertEqual(SelfInvoice.objects.count(), 0)

    def test_not_raised_past_the_thirty_days_and_the_bill_waits_on_the_list(self):
        late = self.freight_bill(day=self.today - datetime.timedelta(days=31))
        self.assertTrue(late.posted)
        self.assertEqual(self.standing(late), [])
        self.assertEqual([bill.pk for bill, _, _ in awaiting_self_invoice()], [late.pk])

    def test_raised_on_the_day_it_posts_from_the_series_with_the_bills_figures(self):
        bill = self.freight_bill(day=self.today)
        [made] = self.standing(bill)
        self.assertEqual((made.number, made.date, made.received_on, made.taxable, made.igst, made.supplier_name),
                         (f"SI-{self.today.year}-00001", self.today, self.today, D("10000.00"), D("500.00"),
                          self.trucker.name))
        self.assertEqual(awaiting_self_invoice(), [])
        with self.assertRaisesMessage(ValidationError, "has a self-invoice standing"):
            SelfInvoice.issue(bill, on_date=self.today)
        # The next from the series, for a supply received on the last of the thirty days.
        last = self.freight_bill(day=self.today - datetime.timedelta(days=30))
        [second] = self.standing(last)
        self.assertEqual((second.number, second.date, second.received_on),
                         (f"SI-{self.today.year}-00002", self.today, self.today - datetime.timedelta(days=30)))

    def test_the_whole_supply_debited_back_cancels_it_and_half_does_not(self):
        bill = self.freight_bill(day=self.today)
        [made] = self.standing(bill)
        bill.create_debit_note(quantities={bill.lines.get(): D("0.5")})
        self.assertEqual(self.standing(bill), [made])
        note = bill.create_debit_note(quantities={bill.lines.get(): D("0.5")})
        self.assertEqual(self.standing(bill), [])
        made.refresh_from_db()
        self.assertEqual(made.cancelled_on, self.today)
        self.assertIn(note.number, made.cancellation_reason)
        # A debit note is not a supply received: it raises nothing of its own.
        self.assertEqual(SelfInvoice.objects.count(), 1)

    def test_issued_it_is_not_edited_or_deleted(self):
        [made] = self.standing(self.freight_bill(day=self.today))
        made.igst = D("1.00")
        with self.assertRaisesMessage(ValidationError, "is not changed"):
            made.save()
        with self.assertRaisesMessage(ValidationError, "cancel it"):
            made.delete()
        self.assertEqual(SelfInvoice.objects.get().igst, D("500.00"))

    def test_the_ap_manager_posts_the_bill_and_the_officer_finds_and_prints_the_self_invoice(self):
        call_command("setup_roles", verbosity=0)

        def as_(role):
            user = User.objects.create_user(role.replace(" ", "_"))
            user.groups.add(Group.objects.get(name=role))
            client = APIClient()
            client.force_authenticate(user)
            return client

        bill = Bill.objects.create(vendor=self.trucker, bill_date=self.today, payable_account=self.ap,
                                   reference=f"INV/{next(INVOICE_NUMBERS)}")
        line = BillLine.objects.create(bill=bill, charge=self.freight, quantity=D("1"), unit_price=D("10000"),
                                       expense_account=self.expense)
        line.taxes.set([self.igst5])
        # Who may not post a bill raises nothing.
        self.assertEqual(as_("Warehouse Staff").post(f"/api/purchasing/bills/{bill.pk}/post_bill/").status_code, 403)
        self.assertEqual(SelfInvoice.objects.count(), 0)
        posted = as_("AP Manager").post(f"/api/purchasing/bills/{bill.pk}/post_bill/")
        self.assertEqual(posted.status_code, 200, posted.content)
        officer = as_("GST Officer")
        [row] = officer.get("/api/gst/self-invoices/", {"bill": bill.pk}).json()
        self.assertEqual((row["bill_number"], row["date"], row["taxable"], row["igst"], row["cancelled_on"]),
                         (posted.json()["number"], str(self.today), "10000.00", "500.00", None))
        self.assertEqual(officer.get("/api/gst/self-invoices/awaiting/").json(), [])
        self.assertEqual(officer.get(f"/api/gst/self-invoices/{row['id']}/pdf/")["Content-Type"], "application/pdf")
