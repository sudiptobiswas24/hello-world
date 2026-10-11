"""
The morning after: an invoice 62 days overdue, a fire NOC thirty days
from lapsing, and a draft bill three days old. The controller's inbox
holds the invoice, the licence and the draft; the stores' holds none of
them. The morning mail goes once to whoever has something waiting and an
address, says nothing to the rest, and prints instead where no mail
server is set up.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType, JournalEntry, JournalLine
from apps.core.licences import Licence, LicenceKind
from apps.imports.importer import run
from apps.imports.tests import ImportTestCase
from apps.purchasing.models import Bill

from .checks import inbox


class ChecksTestCase(ImportTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()  # the books' last answer (health.py) is kept ten minutes; not across tests
        call_command("setup_roles", verbosity=0)
        self.today = timezone.localdate()
        # The fixture's 500 widgets at 4 are stock the ledger never saw;
        # booked, so the books agree and the inbox counts only what is set up here.
        opening_stock = JournalEntry.objects.create(date=self.today, memo="opening stock", posted=True, posted_at=timezone.now())
        JournalLine.objects.bulk_create([JournalLine(entry=opening_stock, account=self.inventory, debit=Decimal("2000")),
                                         JournalLine(entry=opening_stock, account=self.opening, credit=Decimal("2000"))])
        ago = lambda days: self.today - datetime.timedelta(days=days)  # noqa: E731
        report = run("open_invoices", f"customer,reference,date,amount\nC-1,OLD/1,{ago(92)},20000\n",
                     commit=True, against="3900")
        self.assertTrue(report.committed, report.errors)
        Licence.objects.create(kind=LicenceKind.FIRE, licence_number="FIRE/NOC/77", valid_from=ago(335),
                               valid_to=self.today + datetime.timedelta(days=30))
        from apps.core.models import Party, PartyRole, PartyRoleAssignment

        vendor = Party.objects.create(code="V-9", name="Granules", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=vendor, role=PartyRole.VENDOR)
        Bill.objects.create(vendor=vendor, bill_date=ago(3), currency=self.usd, payable_account=self.payable)

    def person(self, role, email=""):
        user = User.objects.create_user(role.replace(" ", "_").lower(), email=email)
        user.groups.add(Group.objects.get(name=role))
        return user

    def keys(self, user):
        return {row["key"]: row["count"] for row in inbox(user)}


class InboxTests(ChecksTestCase):
    def test_each_login_sees_what_it_may_act_on(self):
        controller = self.keys(self.person("Controller"))
        self.assertEqual((controller.get("invoices_overdue"), controller.get("licences_due"), controller.get("bills_draft")),
                         (1, 1, 1))
        stores = self.keys(self.person("Warehouse Staff"))
        self.assertNotIn("invoices_overdue", stores)
        self.assertNotIn("licences_due", stores)
        self.assertNotIn("bills_draft", stores)

    def test_what_recurs_and_is_due_is_in_the_inbox_of_whoever_runs_it(self):
        from apps.accounting.recurring import RecurringJournal
        from apps.sales.models import RecurringInvoice

        RecurringJournal.objects.create(code="RENT", memo="Rent", start_date=self.today - datetime.timedelta(days=3))
        RecurringJournal.objects.create(code="LATER", memo="Not yet", start_date=self.today + datetime.timedelta(days=3))
        RecurringInvoice.objects.create(code="AMC", customer=self.customer, receivable_account=self.ar,
                                        interval="monthly", start_date=self.today)
        controller = self.keys(self.person("Controller"))
        self.assertEqual(controller.get("recurring_journals_due"), 1)
        self.assertNotIn("recurring_invoices_due", controller)
        self.assertEqual(self.keys(self.person("AR Manager")).get("recurring_invoices_due"), 1)
        self.assertNotIn("recurring_journals_due", self.keys(self.person("Warehouse Staff")))

    def test_the_api_answers_the_login_itself(self):
        client = APIClient()
        client.force_authenticate(self.person("Controller"))
        got = client.get("/api/web/inbox/")
        self.assertEqual(got.status_code, 200, got.content)
        rows = {row["key"]: row for row in got.json()["rows"]}
        self.assertEqual((rows["invoices_overdue"]["count"], rows["invoices_overdue"]["href"]), (1, "/sales/aging"))
        self.assertEqual(APIClient().get("/api/web/inbox/").status_code in (401, 403), True)


class MorningMailTests(ChecksTestCase):
    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_one_mail_to_whoever_has_something_waiting(self):
        self.person("Controller", email="controller@example.com")
        self.person("Warehouse Staff", email="stores@example.com")
        self.person("AR Manager")  # no address: printed, not mailed
        call_command("morning_checks", verbosity=0)
        self.assertEqual([message.to for message in mail.outbox], [["controller@example.com"]])
        self.assertIn("Invoices past due: 1", mail.outbox[0].body)
        self.assertIn("Licences to renew: 1", mail.outbox[0].body)

    @override_settings(EMAIL_BACKEND="apps.core.mail.NotConfiguredBackend")
    def test_without_a_mail_server_it_prints_and_says_so(self):
        from io import StringIO

        self.person("Controller", email="controller@example.com")
        out = StringIO()
        call_command("morning_checks", stdout=out)
        self.assertIn("Email is not set up", out.getvalue())
        self.assertIn("Invoices past due: 1", out.getvalue())
        self.assertIn("0 mailed, 1 printed", out.getvalue())


class IncompleteTests(ChecksTestCase):
    """
    What was left incomplete and would carry forward unseen: each counted
    for the role that completes it, each list screen's filter giving
    exactly those rows, and each gone once it is done.
    """

    def gst(self, **fields):
        from apps.accounting.gst import GstSettings, gstin_check_character
        from apps.accounting.models import FiscalPosition

        inter = FiscalPosition.objects.create(code="INTER", name="Interstate")
        return GstSettings.objects.create(gstin="27AABCD1234E1Z" + gstin_check_character("27AABCD1234E1Z"),
                                          interstate_position=inter, **fields)

    def client_for(self, role):
        client = APIClient()
        client.force_authenticate(self.person(role))
        return client

    def test_a_customer_with_no_gst_standing_and_an_item_with_no_hsn(self):
        # Nothing to say until GST is set up at all.
        self.assertNotIn("customers_without_gst", self.keys(self.person("Controller")))
        self.gst()
        controller = self.keys(User.objects.get(username="controller"))
        self.assertEqual((controller["customers_without_gst"], controller["items_without_hsn"]), (1, 1))
        client = self.client_for("GST Officer")
        self.assertEqual([row["code"] for row in client.get("/api/core/parties/", {"gst": "unknown"}).json()], ["C-1"])
        from apps.accounting.models import PartyTaxProfile

        PartyTaxProfile.objects.create(party=self.customer, gst_registration="regular", gstin="27AABCE5555F1ZP")
        self.item.hsn_code = "3923"
        self.item.save()
        controller = self.keys(User.objects.get(username="controller"))
        self.assertNotIn("customers_without_gst", controller)
        self.assertNotIn("items_without_hsn", controller)
        self.assertEqual([row["sku"] for row in client.get("/api/inventory/items/", {"without_hsn": "true"}).json()], [])

    def employee(self, **kwargs):
        from apps.hr.models import Employee
        from apps.hr.payroll import ComponentBasis, ComponentKind, EmployeeCompensation, PayComponent, Statutory

        pf = PayComponent.objects.create(code="PF", name="Provident fund", kind=ComponentKind.DEDUCTION,
                                         basis=ComponentBasis.PERCENT_OF_GROSS, liability_account=self.payable,
                                         sequence=50, statutory=Statutory.PF)
        person = Employee.objects.create(party=self.rep, employee_number="E-1", hire_date=datetime.date(2020, 1, 1), **kwargs)
        EmployeeCompensation.objects.create(employee=person, component=pf, amount=Decimal("12"),
                                            effective_from=datetime.date(2020, 1, 1))
        return person

    def test_a_pf_member_with_no_uan(self):
        person = self.employee()
        self.assertEqual(self.keys(self.person("HR Admin"))["pf_without_uan"], 1)
        self.assertNotIn("esi_without_number", self.keys(User.objects.get(username="hr_admin")))
        client = self.client_for("Payroll Officer")
        self.assertEqual([row["employee_number"] for row in client.get("/api/hr/employees/", {"missing": "uan"}).json()], ["E-1"])
        self.assertEqual(client.get("/api/hr/employees/", {"missing": "pan"}).status_code, 400)
        person.uan = "100200300400"
        person.save()
        self.assertNotIn("pf_without_uan", self.keys(User.objects.get(username="hr_admin")))

    def test_money_received_and_applied_to_nothing_for_a_week(self):
        from apps.accounting.models import Payment, PaymentDirection
        from apps.sales.models import Invoice, InvoicePayment

        def receipt(days_ago, amount):
            payment = Payment.objects.create(party=self.customer, direction=PaymentDirection.RECEIPT,
                                             payment_date=self.today - datetime.timedelta(days=days_ago),
                                             amount=Decimal(amount), currency=self.usd, bank_account=self.bank,
                                             counterpart_account=self.ar)
            payment.post()
            return payment

        old, fresh = receipt(8, "3000"), receipt(2, "500")
        self.assertEqual(self.keys(self.person("Controller"))["receipts_unapplied"], 1)
        client = self.client_for("AR Manager")
        rows = client.get("/api/accounting/payments/", {"unapplied": "true"}).json()
        self.assertEqual(sorted(row["id"] for row in rows), sorted([old.pk, fresh.pk]))
        InvoicePayment.objects.create(invoice=Invoice.objects.get(reference="OLD/1"), payment=old, amount=Decimal("3000"))
        self.assertNotIn("receipts_unapplied", self.keys(User.objects.get(username="controller")))
        self.assertEqual([row["id"] for row in client.get("/api/accounting/payments/", {"unapplied": "true"}).json()], [fresh.pk])

    def test_a_bank_line_a_week_old_nobody_explained(self):
        from apps.accounting.models import BankStatement, BankStatementLine

        statement = BankStatement.objects.create(bank_account=self.bank, start_date=self.today - datetime.timedelta(days=30),
                                                 end_date=self.today, opening_balance=Decimal("0"),
                                                 closing_balance=Decimal("900"))
        BankStatementLine.objects.create(statement=statement, date=self.today - datetime.timedelta(days=8), amount=Decimal("900"))
        BankStatementLine.objects.create(statement=statement, date=self.today - datetime.timedelta(days=2), amount=Decimal("0.01"))
        self.assertEqual(self.keys(self.person("Controller"))["bank_lines_unexplained"], 1)

    def test_last_months_depreciation_not_charged(self):
        from apps.assets.models import AssetCategory, FixedAsset

        plant = Account.objects.create(code="1500", name="Plant", account_type=AccountType.ASSET)
        category = AssetCategory.objects.create(code="PLANT", name="Plant", asset_account=plant, accumulated_account=plant,
                                                expense_account=Account.objects.create(code="6100", name="Depreciation",
                                                                                       account_type=AccountType.EXPENSE),
                                                disposal_account=plant, default_life_months=120)
        lathe = FixedAsset.objects.create(name="Lathe", category=category, acquisition_date=datetime.date(2026, 1, 1),
                                          cost=Decimal("120000"), salvage_value=Decimal("0"), life_months=120)
        self.assertNotIn("depreciation_not_run", self.keys(self.person("Controller")))
        lathe.place_in_service(on_date=datetime.date(2026, 1, 1))
        controller = User.objects.get(username="controller")
        self.assertEqual(self.keys(controller)["depreciation_not_run"], 1)
        lathe.depreciate(through=self.today.replace(day=1) - datetime.timedelta(days=1))
        self.assertNotIn("depreciation_not_run", self.keys(controller))

    def test_last_months_wages_not_posted(self):
        from apps.hr.payroll import PayRun, PayRunStatus

        self.employee()
        payroll = self.person("Payroll Officer")
        tenth = self.today.replace(day=10)
        seventh = self.today.replace(day=7)
        counts = lambda day: {row["key"]: row["count"] for row in inbox(payroll, day)}  # noqa: E731
        self.assertNotIn("wages_not_posted", counts(seventh))  # still time
        self.assertEqual(counts(tenth)["wages_not_posted"], 1)
        end = tenth.replace(day=1) - datetime.timedelta(days=1)
        PayRun.objects.create(period_start=end.replace(day=1), period_end=end, pay_date=seventh, status=PayRunStatus.POSTED)
        self.assertNotIn("wages_not_posted", counts(tenth))

    def test_an_invoice_the_portal_never_saw(self):
        from apps.gst.einvoice import EInvoice
        from apps.sales.models import Invoice

        self.gst(einvoicing_from=datetime.date(2026, 4, 1))
        invoice = Invoice.objects.create(customer=self.customer, invoice_date=datetime.date(2026, 9, 10),
                                         receivable_account=self.ar, currency=self.usd)
        Invoice.objects.filter(pk=invoice.pk).update(posted=True, number="INV-B2B", party_gstin="29AABCK2222B1Z5",
                                                     party_registration="regular")
        officer = self.person("GST Officer")
        self.assertEqual(self.keys(officer)["invoices_without_irn"], 1)  # the opening invoice is the old system's
        client = self.client_for("AR Manager")
        self.assertEqual([row["number"] for row in client.get("/api/sales/invoices/", {"without_irn": "true"}).json()], ["INV-B2B"])
        EInvoice.objects.create(invoice=invoice, payload={}, irn="a" * 64)
        self.assertNotIn("invoices_without_irn", self.keys(officer))
