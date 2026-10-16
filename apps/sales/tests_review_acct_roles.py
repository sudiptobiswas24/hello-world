"""
review_acct probes: who may (apps/core/tests_roles.py's way, as a person in the role over the API) and
setup_indian_books. A failure is a finding. Not part of the product's suite.
"""

import datetime
from decimal import Decimal as D
from io import StringIO

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounting.models import (
    Account, AccountType, FiscalPosition, FiscalPositionTaxMapping, SeededDefault, Tax, TaxGroup, TcsRate, TcsSection,
)
from apps.core.models import Company, Currency
from apps.gst.einvoice import EInvoice
from apps.sales.models import Invoice
from apps.sales.tests_tcs import TcsTestCase

OUTSIDE = ["Sales Rep", "Stores Manager", "Warehouse Staff", "Production Supervisor", "Quality Manager",
           "Employee Self Service", "Purchasing Clerk", "AP Manager", "Bookkeeper", "Process Engineer"]


class WhoMay(TcsTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def s2(self):
        return self.invoice(self.mh, [(self.sack, "100", "150", "0", self.pair)])

    def client_as(self, role):
        self.n = getattr(self, "n", 0) + 1
        user = User.objects.create_user(f"{role.replace(' ', '_').lower()}{self.n}")
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def statuses(self, method, url, roles, data=None):
        out = {}
        for role in roles:
            client = self.client_as(role)
            out[role] = getattr(client, method)(url, data or {}, format="json").status_code if method != "get" else client.get(url).status_code
        return out

    def passed_the_door(self, statuses, allowed=()):
        return sorted(role for role, code in statuses.items() if code != 403 and role not in allowed)

    def test_nobody_outside_accounts_cancels_an_irn_confirms_a_rate_records_a_declaration_or_pays_a_challan(self):
        record = self.registered(self.s2())
        rate = TcsRate.objects.create(section=self.section, applies_from=datetime.date(2027, 4, 1),
                                      rate_percent=D("1"), no_pan_rate_percent=D("5"))
        asked = {
            "cancel the IRN": ("post", f"/api/gst/e-invoices/{record.pk}/cancel/"),
            "record the portal's cancellation": ("post", f"/api/gst/e-invoices/{record.pk}/record_cancellation/"),
            "confirm a TCS rate": ("post", f"/api/accounting/tcs-rates/{rate.pk}/confirm/"),
            "record a Form 127": ("post", "/api/accounting/tcs-declarations/"),
            "pay a TCS challan": ("post", "/api/sales/tcs-challans/pay/"),
            "void a TCS challan": ("post", "/api/sales/tcs-challans/999999/void/"),
            "change a TCS section": ("post", "/api/accounting/tcs-sections/"),
            "add a TCS rate": ("post", "/api/accounting/tcs-rates/"),
        }
        found = {}
        for what, (method, url) in asked.items():
            through = self.passed_the_door(self.statuses(method, url, OUTSIDE))
            if through:
                found[what] = through
        self.assertEqual(found, {}, "roles with no business in the books got past the door")

    def test_who_does_get_through(self):
        record = self.registered(self.s2())
        rate = TcsRate.objects.create(section=self.section, applies_from=datetime.date(2027, 4, 1),
                                      rate_percent=D("1"), no_pan_rate_percent=D("5"))
        every = ["Controller", "AR Manager", "GST Officer", "Sales Rep", "Bookkeeper"]
        matrix = {
            "cancel": self.statuses("post", f"/api/gst/e-invoices/{record.pk}/cancel/", every),
            "confirm": self.statuses("post", f"/api/accounting/tcs-rates/{rate.pk}/confirm/", every),
            "declare": self.statuses("post", "/api/accounting/tcs-declarations/", every),
            "pay": self.statuses("post", "/api/sales/tcs-challans/pay/", every),
            "void": self.statuses("post", "/api/sales/tcs-challans/999999/void/", every),
        }
        print("\nROLE MATRIX (403 = refused):", matrix)
        self.assertEqual(matrix["confirm"]["AR Manager"], 403, "the person who pays the challan also confirms the rate")
        self.assertEqual(matrix["confirm"]["Sales Rep"], 403)

    def test_a_gst_officer_does_not_post_a_credit_note_by_recording_a_portals_answer(self):
        """GST Officer is deliberately NOT given post_invoice: 'the person who files the return reports what was booked, and does not book it.'"""
        invoice = self.s2()
        record = self.registered(invoice)
        officer = self.client_as("GST Officer")
        self.assertEqual(officer.post(f"/api/gst/e-invoices/{record.pk}/cancel/", {"reason": "1", "remarks": "dup"}, format="json").status_code, 200)
        done = officer.post(f"/api/gst/e-invoices/{record.pk}/record_cancellation/",
                            {"cancelled_at": timezone.now().isoformat()}, format="json")
        self.assertEqual(Invoice.objects.filter(credits=invoice, posted=True).count(), 0,
                         f"a GST Officer ({done.status_code}) has reversed an invoice, its GST and its TCS in the ledger")

    def test_a_stores_manager_cannot_switch_the_tcs_flag_on_the_scrap_item(self):
        stores = self.client_as("Stores Manager")
        response = stores.patch(f"/api/inventory/items/{self.scrap.pk}/", {"tcs_section": None}, format="json")
        self.scrap.refresh_from_db()
        self.assertIsNotNone(self.scrap.tcs_section_id,
                             f"Stores Manager ({response.status_code}) cleared the TCS section of an item: no TCS is collected from then on")

    def test_a_sales_rep_does_not_read_tcs_collections_or_the_return(self):
        self.t1()
        rep = self.client_as("Sales Rep")
        self.assertEqual(rep.get("/api/sales/tcs-collections/").status_code, 403)
        self.assertEqual(rep.get("/api/sales/tcs-collections/quarter/?from=2026-07-01&to=2026-09-30").status_code, 403)
        self.assertEqual(rep.get("/api/sales/tcs-challans/").status_code, 403)
        self.assertEqual(rep.get("/api/accounting/tcs-declarations/").status_code, 403)

    def test_the_invoice_json_a_rep_reads_does_not_carry_buyers_pan_or_the_kept_particulars_of_others(self):
        invoice = self.t1()
        body = self.client_as("Sales Rep").get(f"/api/sales/invoices/{invoice.pk}/").json()
        pan = self.mh.tax_profile.pan_on_file()
        self.assertNotIn(pan, str(body))

    def test_the_pdf_in_three_copies_for_those_who_read_invoices_and_not_for_others(self):
        invoice = self.t1()
        for copy in ("original", "duplicate", "triplicate"):
            self.assertEqual(self.client_as("AR Manager").get(f"/api/sales/invoices/{invoice.pk}/pdf/?copy={copy}").status_code, 200)
        self.assertEqual(self.client_as("Production Supervisor").get(f"/api/sales/invoices/{invoice.pk}/pdf/").status_code, 403)
        self.assertEqual(self.client_as("AR Manager").get(f"/api/sales/invoices/{invoice.pk}/pdf/?copy=bogus").status_code, 400)


class SetupBooks(TestCase):
    def setUp(self):
        inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        Company.objects.create(name="New Co", base_currency=inr)

    def run_it(self, **kw):
        out = StringIO()
        call_command("setup_indian_books", stdout=out, **kw)
        return out.getvalue()

    def snapshot(self):
        return {m: set(m.objects.values_list("pk", flat=True)) for m in
                (Account, Tax, TaxGroup, FiscalPosition, FiscalPositionTaxMapping, TcsSection, TcsRate)}

    def test_twice_adds_nothing_and_lists_everything_it_made(self):
        before = self.snapshot()
        self.run_it()
        first = self.snapshot()
        made = {(m._meta.label_lower, pk) for m in first for pk in first[m] - before[m]}
        listed = {(s.model, s.object_id) for s in SeededDefault.objects.all()}
        self.assertEqual(made - listed, set(), "seeded and not listed for review")
        self.run_it()
        self.assertEqual(self.snapshot(), first)
        self.assertEqual(SeededDefault.objects.count(), len(listed))
        self.assertEqual(list(TcsRate.objects.exclude(confirmed_on=None)), [])
        self.assertFalse(SeededDefault.objects.exclude(reviewed_on=None).exists())

    def test_on_a_company_with_accounts_and_taxes_it_overwrites_nothing(self):
        rent = Account.objects.create(code="2201", name="Rent payable", account_type=AccountType.LIABILITY)
        stock = Account.objects.create(code="1100", name="Raw material stock", account_type=AccountType.ASSET)
        old = Tax.objects.create(code="CGST9", name="Old CGST nine", rate=D("9"), gst_head="cgst",
                                 collected_account=rent, paid_account=stock)
        self.run_it()
        for obj, name in ((rent, "Rent payable"), (stock, "Raw material stock"), (old, "Old CGST nine")):
            obj.refresh_from_db()
            self.assertEqual(obj.name, name)
        self.assertEqual((old.collected_account_id, old.paid_account_id), (rent.pk, stock.pk))

    def test_it_does_not_wire_the_new_taxes_to_accounts_that_are_something_else(self):
        """2201 is the plant's Rent payable. The seeded Output CGST 2.5 must not collect GST into it."""
        rent = Account.objects.create(code="2201", name="Rent payable", account_type=AccountType.LIABILITY)
        self.run_it()
        tax = Tax.objects.get(code="CGST2.5")
        self.assertNotEqual(tax.collected_account_id, rent.pk,
                            "the seeded CGST 2.5% collects into 'Rent payable' because the code was taken")

    def test_an_account_of_the_wrong_kind_is_not_used_for_the_tcs_payable(self):
        expense = Account.objects.create(code="2260", name="Staff welfare", account_type=AccountType.EXPENSE)
        self.run_it()
        section = TcsSection.objects.get(code="SCRAP")
        self.assertNotEqual(section.payable_account_id, expense.pk, "TCS is owed into an expense account")

    def test_a_bad_gstin_is_refused_not_stored(self):
        with self.assertRaises(Exception):
            self.run_it(gstin="not-a-gstin")
        from apps.accounting.gst import GstSettings
        self.assertEqual(GstSettings.objects.count(), 0)

    def test_it_never_marks_anything_confirmed_and_collects_nothing_until_confirmed(self):
        self.run_it()
        section = TcsSection.objects.get(code="SCRAP")
        self.assertEqual(list(section.rates.exclude(confirmed_on=None)), [])  # both seeded rates (review F13)
        for day in (datetime.date(2026, 3, 31), datetime.date(2026, 9, 10)):
            with self.assertRaises(Exception):
                section.rate_on(day)
