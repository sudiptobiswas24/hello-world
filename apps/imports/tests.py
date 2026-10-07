"""
Go-live import: every file whole or not at all, every problem named by
row and column, and the opening position brought in as documents that
age, settle and reconcile. Refusals first.
"""

import datetime
import tempfile
from decimal import Decimal
from io import StringIO

from django.core.management import CommandError, call_command
from django.db.models import Sum

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import Company, Party
from apps.inventory.adjustments import AdjustmentReason
from apps.inventory.models import Item
from apps.sales.tests_base import SalesTestCase

from .importer import run

GO_LIVE = datetime.date(2026, 10, 1)


class ImportTestCase(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.opening = Account.objects.create(code="3900", name="Opening balances",
                                              account_type=AccountType.EQUITY)
        self.payable = Account.objects.create(code="2000", name="Payables",
                                              account_type=AccountType.LIABILITY)
        company = Company.get()
        company.default_receivable_account = self.ar
        company.default_payable_account = self.payable
        company.save()

    def balance(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit"))
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))

    def errors(self, report):
        return [(row, column) for row, column, _message in report.errors]


PARTIES = """code,name,roles,gstin,gst_state,gst_registration,credit_limit,address_line1,city,state,currency
C-100,Shree Cement,customer,27AABCD1234E1Z8,27,regular,500000,Plot 4 MIDC,Pune,Maharashtra,USD
V-100,Granule House,vendor,,,,,,,,USD
B-100,Both Ways Pvt Ltd,customer;vendor,,,,,,,,
"""


class PartiesTests(ImportTestCase):
    def test_a_dry_run_keeps_nothing_and_says_so(self):
        report = run("parties", PARTIES)
        self.assertEqual((report.rows, report.created, report.errors, report.committed), (3, 3, [], False))
        self.assertFalse(Party.objects.filter(code__in=["C-100", "V-100", "B-100"]).exists())

    def test_every_problem_is_named_and_one_bad_row_keeps_nothing(self):
        bad = PARTIES + "\n".join([
            ",No code,customer,,,,,,,,",
            "C-1,Already here,customer,,,,,,,,",
            "X-1,Bad role,supplier,,,,,,,,",
            "X-2,Bad GSTIN,customer,27AABCD1234E1Z9,27,regular,,,,,",
            "X-3,Limit for a vendor,vendor,,,,100,,,,",
            "X-4,No currency,customer,,,,,,,,XYZ",
        ]) + "\n"
        report = run("parties", bad, commit=True)
        self.assertEqual(self.errors(report), [
            (5, "code"), (6, "code"), (7, "roles"), (8, "gstin"), (9, "credit_limit"), (10, "currency"),
        ])
        self.assertFalse(report.committed)
        self.assertFalse(Party.objects.filter(code="C-100").exists())

    def test_committed_each_party_has_its_roles_tax_profile_limit_and_address(self):
        report = run("parties", PARTIES, commit=True)
        self.assertTrue(report.committed, report.errors)
        cement = Party.objects.get(code="C-100")
        self.assertEqual(sorted(cement.role_assignments.values_list("role", flat=True)), ["customer"])
        self.assertEqual((cement.tax_profile.gstin, cement.tax_profile.gst_state), ("27AABCD1234E1Z8", "27"))
        self.assertEqual(cement.customer_profile.credit_limit, Decimal("500000"))
        self.assertEqual(cement.addresses.get().city, "Pune")
        self.assertEqual(sorted(Party.objects.get(code="B-100").role_assignments.values_list("role", flat=True)),
                         ["customer", "vendor"])


class ItemsTests(ImportTestCase):
    def test_items_come_in_with_their_unit_and_refuse_what_the_model_refuses(self):
        report = run("items", "sku,name,uom,hsn_code,costing_method,sale_price\n"
                              "FAB-20,Fabric 20 gsm,each,5407,fifo,12.50\n"
                              "FAB-21,Fabric 21 gsm,bag,5407,,\n"
                              "FAB-22,Fabric 22 gsm,each,5407,lifo,\n"
                              "WDG-1,Duplicate,each,,,\n", commit=True)
        self.assertEqual(self.errors(report), [(3, "uom"), (4, "costing_method"), (5, "sku")])
        self.assertFalse(Item.objects.filter(sku="FAB-20").exists())
        report = run("items", "sku,name,uom,hsn_code,costing_method,sale_price\n"
                              "FAB-20,Fabric 20 gsm,each,5407,fifo,12.50\n", commit=True)
        self.assertTrue(report.committed, report.errors)
        self.assertEqual(Item.objects.get(sku="FAB-20").costing_method, "fifo")


class OpeningStockTests(ImportTestCase):
    def test_without_its_reason_nothing_moves(self):
        report = run("opening_stock", "sku,warehouse,quantity,unit_cost\nWDG-1,WH1,10,4\n",
                     commit=True, date=GO_LIVE, reason="OPENING")
        self.assertEqual(self.errors(report), [(0, "--reason")])

    def test_stock_is_posted_against_the_opening_balance_account(self):
        AdjustmentReason.objects.create(code="OPENING", name="Opening stock", account=self.opening)
        on_hand = self.item.on_hand_at(self.warehouse)
        report = run("opening_stock", "sku,warehouse,quantity,unit_cost\n"
                                      "WDG-1,WH1,250,4.2\n"
                                      "WDG-1,NOPE,1,1\n"
                                      "WDG-1,WH1,-3,1\n", commit=True, date=GO_LIVE, reason="OPENING")
        self.assertEqual(self.errors(report), [(3, "warehouse"), (4, "quantity")])
        self.assertEqual(self.item.on_hand_at(self.warehouse), on_hand)

        report = run("opening_stock", "sku,warehouse,quantity,unit_cost\nWDG-1,WH1,250,4.2\n",
                     commit=True, date=GO_LIVE, reason="OPENING")
        self.assertTrue(report.committed, report.errors)
        self.assertEqual(self.item.on_hand_at(self.warehouse), on_hand + 250)
        # 250 at 4.20: 1,050.00 into stock, from the opening-balance account.
        self.assertEqual(self.balance(self.opening), Decimal("-1050.00"))


class OpenDocumentsTests(ImportTestCase):
    def test_an_open_invoice_is_owed_ages_and_is_refused_twice(self):
        text = "customer,reference,date,amount\nC-1,OLD/0412,2026-08-20,11800\n"
        report = run("open_invoices", text, commit=True, against="3900")
        self.assertTrue(report.committed, report.errors)
        from apps.sales.models import Invoice

        invoice = Invoice.objects.get(reference="OLD/0412")
        # Net 30 from the customer's terms: 2/10 net 30 in this fixture.
        self.assertEqual((invoice.posted, invoice.amount_due(), invoice.due_date, invoice.is_opening_balance),
                         (True, Decimal("11800.00"), datetime.date(2026, 9, 19), True))
        self.assertEqual((self.balance(self.ar), self.balance(self.opening)),
                         (Decimal("11800.00"), Decimal("-11800.00")))
        again = run("open_invoices", text, commit=True, against="3900")
        self.assertEqual(self.errors(again), [(2, "reference")])

    def test_an_open_bill_is_owing_and_only_to_a_vendor(self):
        from apps.core.models import PartyRole, PartyRoleAssignment

        vendor = Party.objects.create(code="V-9", name="Granules", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=vendor, role=PartyRole.VENDOR)
        report = run("open_bills", "vendor,reference,date,amount\n"
                                   "V-9,GH-77,2026-09-02,5000\n"
                                   "C-1,X,2026-09-02,10\n"
                                   "V-9,GH-78,2026-09-02,-5\n", commit=True, against="3900")
        self.assertEqual(self.errors(report), [(3, "vendor"), (4, "amount")])
        report = run("open_bills", "vendor,reference,date,amount\nV-9,GH-77,2026-09-02,5000\n",
                     commit=True, against="3900")
        self.assertTrue(report.committed, report.errors)
        self.assertEqual((self.balance(self.payable), self.balance(self.opening)),
                         (Decimal("-5000.00"), Decimal("5000.00")))
        from apps.purchasing.models import Bill

        self.assertTrue(Bill.objects.get(reference="GH-77").is_opening_balance)


class OpeningBalancesTests(ImportTestCase):
    def test_they_must_balance_and_leave_documented_accounts_to_documents(self):
        report = run("opening_balances", "account,debit,credit\n1010,250000,\n3900,,240000\n",
                     commit=True, date=GO_LIVE)
        self.assertEqual(report.errors[0][:2], (0, ""))
        self.assertIn("must balance", report.errors[0][2])
        report = run("opening_balances", "account,debit,credit\n1100,5,\n3900,,5\n", commit=True, date=GO_LIVE)
        self.assertEqual(self.errors(report), [(2, "account")])
        report = run("opening_balances", "account,debit,credit\n1010,250000,\n3900,,250000\n",
                     commit=True, date=GO_LIVE)
        self.assertTrue(report.committed, report.errors)
        self.assertEqual(self.balance(self.bank), Decimal("250000.00"))


class TheCommandTests(ImportTestCase):
    def call(self, *args):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as handle:
            handle.write(args[1])
        out = StringIO()
        call_command("import_csv", args[0], handle.name, *args[2:], stdout=out)
        return out.getvalue()

    def test_a_dry_run_says_nothing_was_kept_and_problems_fail_the_command(self):
        self.assertIn("Dry run: nothing was kept", self.call("parties", PARTIES))
        self.assertFalse(Party.objects.filter(code="C-100").exists())
        with self.assertRaisesMessage(CommandError, "nothing was kept"):
            self.call("parties", "code,name,roles\n,Nameless,customer\n")
        self.assertIn("brought in", self.call("parties", PARTIES, "--commit"))
        self.assertTrue(Party.objects.filter(code="C-100").exists())

    def test_the_opening_position_needs_its_date_and_account(self):
        with self.assertRaisesMessage(CommandError, "needs --date"):
            self.call("opening_balances", "account,debit,credit\n")
        with self.assertRaisesMessage(CommandError, "needs --against"):
            self.call("open_invoices", "customer,reference,date,amount\n")


EMPLOYEES = """employee_number,name,hire_date,department,manager,job_title,username,roles
E-200,Ravi Kulkarni,2024-04-01,WEAVE,E-100,Loom operator,ravi,Employee Self Service
E-100,Meena Joshi,2019-06-01,WEAVE,,Weaving supervisor,meena,Line Manager;Employee Self Service
E-300,Sunil Pawar,2025-01-15,,,Helper,,
"""


class EmployeesTests(ImportTestCase):
    def setUp(self):
        super().setUp()
        from apps.hr.models import Department

        call_command("setup_roles", verbosity=0)
        Department.objects.create(code="WEAVE", name="Weaving")

    def test_people_logins_and_managers_named_later_in_the_file(self):
        from django.contrib.auth.models import User

        from apps.hr.models import Employee

        report = run("employees", EMPLOYEES, commit=True)
        self.assertTrue(report.committed, report.errors)
        ravi = Employee.objects.get(employee_number="E-200")
        self.assertEqual((ravi.manager.employee_number, ravi.department.code, ravi.user.username),
                         ("E-100", "WEAVE", "ravi"))
        self.assertTrue(ravi.party.role_assignments.filter(role="employee").exists())
        meena = User.objects.get(username="meena")
        self.assertEqual(sorted(meena.groups.values_list("name", flat=True)),
                         ["Employee Self Service", "Line Manager"])
        self.assertIsNone(Employee.objects.get(employee_number="E-300").user)
        # A first password for each new login, and it works.
        self.assertEqual(sorted(name for name, _ in report.passwords), ["meena", "ravi"])
        first = dict(report.passwords)["ravi"]
        self.assertTrue(User.objects.get(username="ravi").check_password(first))

    def test_problems_are_named_and_nothing_is_kept(self):
        from django.contrib.auth.models import User

        from apps.hr.models import Employee

        bad = EMPLOYEES + "\n".join([
            "E-400,Bad role,2025-01-01,,,,anita,Weaver",
            "E-500,No such manager,2025-01-01,,E-999,,,",
            "E-600,Roles without a login,2025-01-01,,,,,Line Manager",
            "E-700,No date,,,,,,",
            "E-800,Unknown department,2025-01-01,PACK,,,,",
        ]) + "\n"
        report = run("employees", bad, commit=True)
        self.assertEqual([(row, column) for row, column, _ in report.errors],
                         [(5, "roles"), (7, "roles"), (8, "hire_date"), (9, "department"), (6, "manager")])
        self.assertFalse(report.committed)
        self.assertEqual(report.passwords, [])
        self.assertFalse(Employee.objects.exists())
        self.assertFalse(User.objects.filter(username__in=["ravi", "meena"]).exists())

    def test_the_command_writes_first_passwords_to_a_file_only_its_owner_reads(self):
        import os
        import stat

        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "employees.csv")
            with open(source, "w") as handle:
                handle.write(EMPLOYEES)
            with self.assertRaisesMessage(CommandError, "needs --passwords-out"):
                call_command("import_csv", "employees", source, "--commit", stdout=StringIO())
            out = os.path.join(folder, "first-passwords.csv")
            said = StringIO()
            call_command("import_csv", "employees", source, "--commit", "--passwords-out", out, stdout=said)
            self.assertIn("2 new login(s)", said.getvalue())
            self.assertNotIn(dict(read_passwords(out))["ravi"], said.getvalue())
            self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)


def read_passwords(path):
    import csv

    with open(path) as handle:
        return [(row["username"], row["first_password"]) for row in csv.DictReader(handle)]


class CustomerRepsTests(ImportTestCase):
    """Reps marked in the employees file, then their customers, by code."""

    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.assertTrue(run("parties", PARTIES, commit=True).committed)
        report = run("employees", "employee_number,name,hire_date,sales_rep\n"
                     "E-1,Asha Rep,2026-01-01,yes\nE-2,Clerk,2026-01-01,no\n", commit=True)
        self.assertTrue(report.committed, report.errors)

    def test_a_customer_carried_by_someone_not_a_rep_is_refused(self):
        report = run("customer_reps", "customer,rep\nC-100,E-2\nV-100,E-1\nC-100,E-9\n")
        self.assertEqual(self.errors(report), [(2, "rep"), (3, "customer"), (4, "rep")])

    def test_customers_are_given_their_rep(self):
        from apps.hr.models import Employee
        from apps.sales.models import SalesRep

        rep = Employee.objects.get(employee_number="E-1").party
        self.assertTrue(SalesRep.objects.filter(party=rep).exists())
        self.assertFalse(SalesRep.objects.filter(party__employee_profile__employee_number="E-2").exists())
        report = run("customer_reps", "customer,rep\nC-100,E-1\nB-100,E-1\n", commit=True)
        self.assertTrue(report.committed, report.errors)
        carried = Party.objects.filter(customer_profile__sales_rep=rep)
        self.assertEqual(sorted(carried.values_list("code", flat=True)), ["B-100", "C-100"])
        # C-100's credit limit, from the parties file, is kept.
        self.assertEqual(Party.objects.get(code="C-100").customer_profile.credit_limit, Decimal("500000"))


class TemplatesTests(ImportTestCase):
    def test_each_kind_has_a_blank_file_that_reads_back_as_no_rows(self):
        import os

        from .importer import KINDS, columns_of, read

        with tempfile.TemporaryDirectory() as folder:
            for kind in KINDS:
                path = os.path.join(folder, f"{kind}.csv")
                call_command("import_csv", kind, path, "--template", stdout=StringIO())
                with open(path) as handle:
                    text = handle.read()
                self.assertEqual(text.strip().split(","), columns_of(kind))
                self.assertEqual(read(text), [])
            with self.assertRaisesMessage(CommandError, "never written over"):
                call_command("import_csv", "items", os.path.join(folder, "items.csv"), "--template",
                             stdout=StringIO())


class GoLiveCheckTests(ImportTestCase):
    def check(self):
        out = StringIO()
        try:
            call_command("go_live_check", stdout=out)
            return True, out.getvalue()
        except CommandError as error:
            return False, out.getvalue() + str(error)

    def test_what_breaks_the_first_day_fails_it_and_says_how_to_mend_it(self):
        ready, said = self.check()
        self.assertFalse(ready)
        for expected in ("FAIL  Account: bank", "FAIL  Roles", "setup_roles",
                         "WARN  GST", "Not ready"):
            self.assertIn(expected, said)

    def test_a_configured_installation_is_ready_and_names_what_to_read(self):
        from django.contrib.auth.models import Group, User

        from apps.hr.models import Employee

        call_command("setup_roles", verbosity=0)
        company = Company.get()
        company.default_bank_account = self.bank
        company.default_revenue_account = self.revenue
        company.save()
        # The fixture's 500 at 4.00 is on the shelf with no entry behind it:
        # booked here as an opening balance, as an import would.
        from apps.accounting.models import JournalEntry

        entry = JournalEntry.objects.create(date=GO_LIVE, memo="Opening stock")
        JournalLine.objects.create(entry=entry, account=self.inventory, debit=Decimal("2000"), credit=0)
        JournalLine.objects.create(entry=entry, account=self.opening, debit=0, credit=Decimal("2000"))
        entry.post()
        clerk = User.objects.create_user("clerk")
        clerk.groups.add(Group.objects.get(name="Sales Rep"))
        User.objects.create_user("idle")
        ready, said = self.check()
        self.assertTrue(ready, said)
        self.assertIn("ok    Trial balance", said)
        self.assertIn("WARN  Logins with no role: idle", said)
        self.assertIn("WARN  Logins not linked to an employee: clerk", said)
        self.assertFalse(Employee.objects.exists())

    def test_a_ledger_that_does_not_balance_fails_it(self):
        from apps.accounting.models import JournalEntry, JournalLine

        call_command("setup_roles", verbosity=0)
        company = Company.get()
        company.default_bank_account = self.bank
        company.default_revenue_account = self.revenue
        company.save()
        entry = JournalEntry.objects.create(date=GO_LIVE, memo="Broken by hand", posted=True)
        JournalLine.objects.bulk_create([JournalLine(entry=entry, account=self.bank, debit=Decimal("5"),
                                                     credit=Decimal("0"))])
        ready, said = self.check()
        self.assertFalse(ready)
        self.assertIn("FAIL  Trial balance", said)
