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
from django.test import SimpleTestCase, TestCase

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

    def open_books(self):
        """The books' setting every kind that posts the opening position waits for (O529)."""
        from apps.accounting.models import BooksOpening

        return BooksOpening.get() or BooksOpening.objects.create(opened_on=GO_LIVE, opening_account=self.opening)

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


class AccountsTests(ImportTestCase):
    """The plant's own chart, so every line of the old trial balance has an account to come in on."""

    def test_each_refusal_names_its_row_and_column_and_nothing_is_kept(self):
        text = """code,name,account_type,parent,currency,holds_money
3900,Opening again,equity,,,
1501,Lathe,machine,,,
1502,Lathe,asset,9999,,
1503,Lathe,asset,3900,,
4501,Interest earned,income,,,yes
1504,Euro bank,asset,,XYZ,yes
1505,,asset,,,
1506,Good row,asset,,,
"""
        report = run("accounts", text, commit=True)
        self.assertEqual(self.errors(report), [
            (2, "code"), (3, "account_type"), (4, "parent"), (5, "parent"), (6, "holds_money"),
            (7, "currency"), (8, "name")])
        self.assertIn("'3900' already exists", report.errors[0][2])
        self.assertIn("A sub-account must have the same account_type", report.errors[3][2])
        self.assertFalse(report.committed)
        self.assertFalse(Account.objects.filter(code="1506").exists())

    def test_a_chart_with_its_groups_banks_and_loans_comes_in(self):
        text = """code,name,account_type,parent,currency,holds_money
1500,Fixed assets,Asset,,,
1510,Plant and machinery,asset,1500,,
1205,HDFC Bank current account,asset,,USD,yes
2510,SBI term loan,liability,,,no
"""
        report = run("accounts", text, commit=True)
        self.assertEqual((report.created, report.errors, report.committed), (4, [], True))
        machinery = Account.objects.get(code="1510")
        self.assertEqual((machinery.parent.code, machinery.account_type), ("1500", AccountType.ASSET))
        bank = Account.objects.get(code="1205")
        self.assertEqual((bank.holds_money, bank.currency.code), (True, "USD"))
        self.assertFalse(Account.objects.get(code="2510").holds_money)

    def test_the_opening_balances_then_land_on_them(self):
        from apps.accounting.models import BooksOpening

        BooksOpening.objects.create(opened_on=GO_LIVE, opening_account=self.opening)
        run("accounts", "code,name,account_type,holds_money\n1205,HDFC Bank,asset,yes\n", commit=True)
        report = run("opening_balances", "account,debit,credit\n1205,250000,\n3900,,250000\n", commit=True)
        self.assertEqual(report.errors, [])
        self.assertEqual(self.balance(Account.objects.get(code="1205")), Decimal("250000"))


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


class ItemTaxesTests(ImportTestCase):
    """O467: an item that declares no tax is read as bearing none; the import could set none."""

    def setUp(self):
        super().setUp()
        from apps.accounting.models import Tax, TaxGroup

        payable = Account.objects.create(code="2201", name="Output GST", account_type=AccountType.LIABILITY)
        tax = lambda code, rate, scope="both", group=None: Tax.objects.create(  # noqa: E731
            code=code, name=code, rate=Decimal(rate), scope=scope, group=group,
            collected_account=payable, paid_account=payable)
        group = TaxGroup.objects.create(code="GST18", name="GST 18%")
        tax("SGST9", "9", group=group)
        tax("CGST9", "9", group=group)
        tax("RCM5", "5", scope="purchase")

    def test_an_item_declares_its_taxes_by_code_or_by_group(self):
        report = run("items", "sku,name,uom,sales_taxes,purchase_taxes\n"
                              "BAG-1,Cement bag,each,GST18,CGST9;SGST9\n", commit=True)
        self.assertTrue(report.committed, report.errors)
        bag = Item.objects.get(sku="BAG-1")
        self.assertEqual(sorted(bag.sales_taxes.values_list("code", flat=True)), ["CGST9", "SGST9"])
        self.assertEqual(sorted(bag.purchase_taxes.values_list("code", flat=True)), ["CGST9", "SGST9"])

    def test_a_tax_of_the_other_side_or_none_such_is_refused(self):
        report = run("items", "sku,name,uom,sales_taxes,purchase_taxes\n"
                              "BAG-2,Bag,each,RCM5,\n"
                              "BAG-3,Bag,each,,GST12\n", commit=True)
        self.assertEqual(self.errors(report), [(2, "sales_taxes"), (3, "purchase_taxes")])
        self.assertIn("RCM5 is not taxes on a sale", report.errors[0][2])
        self.assertIn("no tax or tax group GST12", report.errors[1][2])
        self.assertFalse(Item.objects.filter(sku__in=["BAG-2", "BAG-3"]).exists())

    def test_go_live_check_names_goods_that_declare_no_tax(self):
        from apps.accounting.gst import GstSettings
        from apps.gst.tests import gstin

        from apps.accounting.models import FiscalPosition

        GstSettings.objects.create(gstin=gstin("27AABCD1234E1Z"),
                                   interstate_position=FiscalPosition.objects.create(code="INTER", name="Inter-state"))
        run("items", "sku,name,uom\nBAG-4,Bag,each\n", commit=True)
        out = StringIO()
        try:
            call_command("go_live_check", stdout=out)
        except CommandError:
            pass
        self.assertIn("Items with no taxes", out.getvalue())
        self.assertIn("BAG-4", out.getvalue())
        from apps.accounting.models import Tax

        Item.objects.get(sku="BAG-4").sales_taxes.set(Tax.objects.filter(code="CGST9"))
        out = StringIO()
        try:
            call_command("go_live_check", stdout=out)
        except CommandError:
            pass
        self.assertNotIn("BAG-4", out.getvalue())


class OpeningStockTests(ImportTestCase):
    def setUp(self):
        super().setUp()
        self.open_books()

    def test_a_cost_with_more_places_than_the_line_keeps_is_refused(self):
        # O4: read at eight places into a four-place field. Refused already by the
        # line's own field (clean()); kept so a reader that stops asking it is seen.
        AdjustmentReason.objects.create(code="OPENING", name="Opening stock", account=self.opening)
        on_hand = self.item.on_hand_at(self.warehouse)
        report = run("opening_stock", "sku,warehouse,quantity,unit_cost\nWDG-1,WH1,1000,3.33333333\n",
                     commit=True, date=GO_LIVE, reason="OPENING")
        self.assertEqual(self.errors(report), [(2, "unit_cost")])
        self.assertIn("more than 4 decimal places", str(report.errors))
        self.assertEqual(self.item.on_hand_at(self.warehouse), on_hand)

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
    def setUp(self):
        super().setUp()
        self.open_books()

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
    def test_they_wait_for_the_books_to_open_and_keep_to_the_day(self):
        from apps.accounting.models import BooksOpening

        report = run("opening_balances", "account,debit,credit\n1010,5,\n3900,,5\n", commit=True)
        self.assertEqual(report.errors[0][:2], (0, "--date"))
        BooksOpening.objects.create(opened_on=GO_LIVE, opening_account=self.opening)
        report = run("opening_balances", "account,debit,credit\n1010,5,\n3900,,5\n", commit=True,
                     date=GO_LIVE + datetime.timedelta(days=1))
        self.assertFalse(report.committed)
        self.assertIn("is not the day the books opened", str(report.errors))
        report = run("opening_balances", "account,debit,credit\n1010,5,\n3900,,5\n", commit=True)
        self.assertTrue(report.committed, report.errors)

    def test_they_must_balance_and_leave_documented_accounts_to_documents(self):
        from apps.accounting.models import BooksOpening

        BooksOpening.objects.create(opened_on=GO_LIVE, opening_account=self.opening)
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

    FILES = (("opening_balances", "account,debit,credit\n1010,5,\n3900,,5\n"),
             ("opening_stock", "sku,warehouse,quantity,unit_cost\nWDG-1,WH1,250,4.2\n"),
             ("open_invoices", "customer,reference,date,amount\nC-1,OLD/0412,2026-08-20,11800\n"))

    def test_the_opening_position_takes_its_day_and_account_from_the_books_setting(self):
        """O531: the command demanded --date and --against, which the setting alone decides."""
        AdjustmentReason.objects.create(code="OPENING", name="Opening stock", account=self.opening)
        self.open_books()
        for kind, text in self.FILES:
            with self.subTest(kind=kind):
                self.assertIn(f"{text.count(chr(10)) - 1} row(s) are clean", self.call(kind, text))

    def test_before_the_books_open_the_command_says_where_to_set_the_day(self):
        AdjustmentReason.objects.create(code="OPENING", name="Opening stock", account=self.opening)
        for kind, text in self.FILES:
            with self.subTest(kind=kind):
                out = StringIO()
                with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as handle:
                    handle.write(text)
                with self.assertRaisesMessage(CommandError, "1 problem(s) in "):
                    call_command("import_csv", kind, handle.name, stdout=out)
                self.assertIn("et the day the books opened (Settings, Books opening) first", out.getvalue())

    def test_a_date_or_account_given_all_the_same_must_agree(self):
        self.open_books()
        out = StringIO()
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as handle:
            handle.write(self.FILES[0][1])
        with self.assertRaises(CommandError):
            call_command("import_csv", "opening_balances", handle.name, "--date", "2026-10-02", stdout=out)
        self.assertIn("file, --date: 2026-10-02 is not the day the books opened (2026-10-01)", out.getvalue())


EMPLOYEES = """employee_number,name,hire_date,department,manager,job_title,username
E-200,Ravi Kulkarni,2024-04-01,WEAVE,E-100,Loom operator,ravi
E-100,Meena Joshi,2019-06-01,WEAVE,,Weaving supervisor,meena
E-300,Sunil Pawar,2025-01-15,,,Helper,
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
        # Logins, with no roles: those are given on the Logins screen by someone who holds them.
        meena = User.objects.get(username="meena")
        self.assertEqual(list(meena.groups.all()), [])
        self.assertIsNone(Employee.objects.get(employee_number="E-300").user)
        # A first password for each new login, and it works.
        self.assertEqual(sorted(name for name, _ in report.passwords), ["meena", "ravi"])
        first = dict(report.passwords)["ravi"]
        self.assertTrue(User.objects.get(username="ravi").check_password(first))

    def test_problems_are_named_and_nothing_is_kept(self):
        from django.contrib.auth.models import User

        from apps.hr.models import Employee

        # An old file, with the roles column the template no longer has.
        old_file = "\n".join(f"{line},roles" if number == 0 else f"{line},"
                             for number, line in enumerate(EMPLOYEES.strip().split("\n")))
        bad = old_file + "\n" + "\n".join([
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


    def test_a_passwords_file_that_cannot_be_made_refuses_before_anyone_is_kept(self):
        """O465: the file was opened after the commit; a folder it could not write to kept logins nobody could open."""
        import os

        from django.contrib.auth.models import User

        from apps.hr.models import Employee

        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "employees.csv")
            with open(source, "w") as handle:
                handle.write(EMPLOYEES)
            nowhere = os.path.join(folder, "no-such-folder", "first-passwords.csv")
            with self.assertRaisesMessage(CommandError, "cannot be made"):
                call_command("import_csv", "employees", source, "--commit", "--passwords-out", nowhere,
                             stdout=StringIO())
            taken = os.path.join(folder, "taken.csv")
            with open(taken, "w") as handle:
                handle.write("kept\n")
            with self.assertRaisesMessage(CommandError, "exists; first passwords are never written over"):
                call_command("import_csv", "employees", source, "--commit", "--passwords-out", taken,
                             stdout=StringIO())
            with open(taken) as handle:
                self.assertEqual(handle.read(), "kept\n")
            self.assertFalse(Employee.objects.exists())
            self.assertFalse(User.objects.filter(username__in=["ravi", "meena"]).exists())
            # A file refused for its rows leaves no empty passwords file behind, and can be run again.
            out = os.path.join(folder, "first-passwords.csv")
            with open(os.path.join(folder, "bad.csv"), "w") as handle:
                handle.write(EMPLOYEES.replace("2025-01-15", "not a date", 1))
            with self.assertRaises(CommandError):
                call_command("import_csv", "employees", os.path.join(folder, "bad.csv"), "--commit",
                             "--passwords-out", out, stdout=StringIO())
            self.assertFalse(os.path.exists(out))


class ImportGivesNoRolesTests(ImportTestCase):
    """
    O86: the import takes core.import_records alone, and its employees
    file gave roles, to a new login or to any not yet an employee's: a
    Controller gave their own login HR Admin. It gives none now, and links
    only a login that holds none.
    """

    HEADING = "employee_number,name,hire_date,username,roles\n"

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group, User

        call_command("setup_roles", verbosity=0)
        self.controller = User.objects.create_user("ctl")
        self.controller.groups.add(Group.objects.get(name="Controller"))

    def test_a_role_in_the_file_is_refused_and_nothing_kept(self):
        from django.contrib.auth.models import User

        from apps.hr.models import Employee

        report = run("employees", self.HEADING + "E-9,Asha,2024-04-01,asha,Line Manager\n", commit=True)
        self.assertEqual((self.errors(report), report.committed), ([(2, "roles")], False))
        self.assertFalse(User.objects.filter(username="asha").exists())
        self.assertFalse(Employee.objects.exists())

    def test_a_login_that_holds_a_role_is_not_linked(self):
        from apps.hr.models import Employee

        report = run("employees", self.HEADING + "E-9,Controller,2024-04-01,ctl,\n", commit=True)
        self.assertEqual((self.errors(report), report.committed), ([(2, "username")], False))
        self.assertFalse(Employee.objects.exists())
        self.assertEqual(list(self.controller.groups.values_list("name", flat=True)), ["Controller"])

    def test_a_login_holding_only_an_employees_own_role_is_linked(self):
        from django.contrib.auth.models import Group, User

        from apps.hr.models import Employee

        ess = User.objects.create_user("ess")
        ess.groups.add(Group.objects.get(name="Employee Self Service"))
        report = run("employees", "employee_number,name,hire_date,username\nE-9,Ess,2024-04-01,ess\n", commit=True)
        self.assertTrue(report.committed, report.errors)
        self.assertEqual(Employee.objects.get().user, ess)

    def test_a_login_that_holds_nothing_is_linked_and_given_nothing(self):
        from django.contrib.auth.models import User

        from apps.hr.models import Employee

        idle = User.objects.create_user("idle")
        report = run("employees", "employee_number,name,hire_date,username\nE-9,Idle,2024-04-01,idle\n",
                     commit=True)
        self.assertTrue(report.committed, report.errors)
        self.assertEqual((Employee.objects.get().user, list(idle.groups.all()), report.passwords), (idle, [], []))


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

    def unconfirmed_customs_defaults(self):
        """As migration 0087 loads them; made here, since a run without migrations has none."""
        from apps.purchasing.imports import CustomsValuationDefault

        CustomsValuationDefault.objects.get_or_create(valid_from=datetime.date(2017, 9, 26), defaults={
            "freight_percent": Decimal("20"), "insurance_percent": Decimal("1.125"),
            "citation": "Customs Valuation Rules 2007, r.10(2)", "confirmed": False})

    def test_what_breaks_the_first_day_fails_it_and_says_how_to_mend_it(self):
        self.unconfirmed_customs_defaults()
        ready, said = self.check()
        self.assertFalse(ready)
        for expected in ("FAIL  Account: bank", "FAIL  Roles", "setup_roles",
                         "WARN  GST", "Not ready",
                         # O403: neither existed on a fresh plant, and nothing said so.
                         "FAIL  Stock adjustment reasons", "setup_stores", "WARN  Countries",
                         # L21: loaded unconfirmed, and nothing said so or offered a screen to confirm them on.
                         "WARN  Customs valuation defaults", "Freight 20% and insurance 1.125% of FOB"):
            self.assertIn(expected, said)

    def test_customs_valuation_defaults_once_confirmed_are_not_mentioned(self):
        from apps.purchasing.imports import CustomsValuationDefault

        self.unconfirmed_customs_defaults()
        CustomsValuationDefault.objects.update(confirmed=True)
        _ready, said = self.check()
        self.assertNotIn("Customs valuation defaults", said)

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
        # A configured plant can book a count's difference (O403).
        from apps.inventory.adjustments import AdjustmentReason

        shrink = Account.objects.create(code="5300", name="Stock written off", account_type=AccountType.EXPENSE)
        AdjustmentReason.objects.create(code="SHORT", name="Short on count", account=shrink, direction="decrease")
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


    def test_an_item_made_here_with_no_recipe_is_named(self):
        """_making() was written and never asked (O439): a sack with no specification went unmentioned."""
        from apps.manufacturing.bom import BillOfMaterials

        sack = Item.objects.create(sku="SACK-50", name="50 kg sack", uom=self.uom, stock_class="finished")
        fabric = Item.objects.create(sku="FAB-90", name="Fabric 90 cm", uom=self.uom, stock_class="semi_finished")
        BillOfMaterials.objects.create(item=fabric, name="Woven", quantity_produced=Decimal("1"), uom=self.uom)
        _ready, said = self.check()
        self.assertIn("WARN  Items made here with no recipe", said)
        self.assertIn("SACK-50", said)
        self.assertNotIn("FAB-90", said)

    NOON = datetime.datetime(2026, 11, 5, 12, 0, tzinfo=datetime.timezone.utc)

    def old_todo_rows(self):
        """
        O457. The rule reached this database at noon (UTC) on 5 Nov 2026. Six to-dos about one party:

            made 4 Nov by ravi for asha, open                    counted: the old rule governs it
            made 4 Nov by asha for herself, open                 not: its maker is its addressee
            made 4 Nov by ravi for asha, done                    not: nothing is left to decide
            made 4 Nov by ravi for asha, a call                  not: tell() raises to-dos only
            made 6 Nov by ravi for asha, open                    not: made under the rule
            made 6 Nov by the application for asha (ravi's act)  not: marked as raised by a rule
        """
        from django.contrib.auth.models import User
        from django.contrib.contenttypes.models import ContentType

        from apps.core.chatter import FollowUp, FollowUpKind

        ravi, asha = (User.objects.create_superuser(name) for name in ("ravi", "asha"))
        party = Party.objects.create(code="C-OLD", name="Old Cement")
        day = datetime.timedelta(days=1)

        def todo(summary, by, made, **more):
            row = FollowUp.objects.create(
                content_type=ContentType.objects.get_for_model(Party), object_id=party.pk, summary=summary,
                due_on=datetime.date(2026, 11, 12), assigned_to=asha, created_by=by, updated_by=by, **more)
            FollowUp.objects.filter(pk=row.pk).update(created_at=made)

        todo("Decide on the failed lot", ravi, self.NOON - day)
        todo("Ring the transporter", asha, self.NOON - day)
        todo("Old and finished", ravi, self.NOON - day, done_on=datetime.date(2026, 11, 4), done_by=asha)
        todo("Call about the claim", ravi, self.NOON - day, kind=FollowUpKind.CALL)
        todo("Planned after the rule", ravi, self.NOON + day)
        todo("Raised after the rule", ravi, self.NOON + day, raised_by_a_rule=True)

    def test_with_no_to_do_from_before_the_rule_nothing_is_said(self):
        from unittest import mock

        with mock.patch("apps.core.chatter.rule_arrived", return_value=self.NOON):
            _ready, said = self.check()
        self.assertNotIn("To-dos from before", said)

    def test_open_to_dos_from_before_the_rule_are_counted_and_named_and_none_is_changed(self):
        from unittest import mock

        from apps.core.chatter import FollowUp

        self.old_todo_rows()
        before = list(FollowUp.objects.order_by("pk").values_list("pk", "raised_by_a_rule", "done_on", "updated_at"))
        with mock.patch("apps.core.chatter.rule_arrived", return_value=self.NOON):
            _ready, said = self.check()
        self.assertIn("WARN  To-dos from before the application marked its own: 1 open, made before 05 Nov 2026",
                      said)
        self.assertIn("'Decide on the failed lot' for asha, made by ravi, due 12 Nov 2026.", said)
        for other in ("Ring the transporter", "Old and finished", "Call about the claim", "Planned after the rule",
                      "Raised after the rule"):
            self.assertNotIn(other, said)
        self.assertEqual(list(FollowUp.objects.order_by("pk").values_list(
            "pk", "raised_by_a_rule", "done_on", "updated_at")), before)

    def test_a_database_that_never_recorded_the_migration_is_not_guessed_at(self):
        from unittest import mock

        self.old_todo_rows()
        with mock.patch("apps.core.chatter.rule_arrived", return_value=None):
            _ready, said = self.check()
        self.assertNotIn("To-dos from before", said)

    def test_when_the_rule_arrived_is_read_off_the_migrations_this_database_recorded(self):
        from django.db import connection
        from django.db.migrations.recorder import MigrationRecorder

        from apps.core.chatter import rule_arrived, todos_from_before_the_rule

        self.old_todo_rows()
        if not MigrationRecorder(connection).has_table():  # a run without migrations
            self.assertIsNone(rule_arrived())
            return
        self.assertEqual(rule_arrived(), MigrationRecorder.Migration.objects.get(
            app="core", name="0047_followup_raised_by_a_rule").applied)
        # This database was built after 4 Nov 2026, so the one row the old rule governs is before it;
        # the two "after the rule" are dated 6 Nov and count as before it too, being open and unmarked.
        self.assertEqual(sorted(todos_from_before_the_rule()[1].values_list("summary", flat=True)),
                         ["Decide on the failed lot", "Planned after the rule"])


class GoLiveCheckBeforeTheCompanyTests(TestCase):
    """
    A first installation, nothing yet set up: the check stopped at "No company
    yet" and listed nothing else, though warehouses, roles and the statutory
    figures do not read the company, so an installer mended one line per run (O422).
    """

    def test_every_check_that_does_not_read_the_company_is_still_asked(self):
        self.assertFalse(Company.objects.exists())
        out = StringIO()
        with self.assertRaises(CommandError) as refused:
            call_command("go_live_check", stdout=out)
        said = out.getvalue()
        for expected in ("FAIL  Company: No company yet", "FAIL  Warehouses", "FAIL  Stock adjustment reasons",
                         "FAIL  Roles", "WARN  GST", "WARN  Administrator"):
            self.assertIn(expected, said)
        # What reads the company is not asked of one that is not there: one line says so.
        self.assertNotIn("Account: ", said)
        self.assertNotIn("Base currency", said)
        self.assertIn("Not ready", str(refused.exception))


class TheOpeningPositionComesInOnceTests(ImportTestCase):
    """O280, O313: every opening kind refuses a second run of what it brought in, before it writes."""

    BALANCES = "account,debit,credit\n1010,250000,\n3900,,250000\n"
    STOCK = "sku,warehouse,quantity,unit_cost\nWDG-1,WH1,1000,3.50\n"

    def on_hand(self):
        from apps.inventory.models import StockMovement

        return StockMovement.objects.filter(item__sku="WDG-1").aggregate(t=Sum("quantity"))["t"]

    def test_the_same_opening_balances_brought_in_twice_are_refused_the_second_time(self):
        from apps.accounting.models import BooksOpening, JournalEntry

        bank = Account.objects.get(code="1010")
        BooksOpening.objects.create(opened_on=GO_LIVE, opening_account=self.opening)
        first = run("opening_balances", self.BALANCES, commit=True, date=GO_LIVE)
        self.assertTrue(first.committed, first.errors)
        entries = JournalEntry.objects.count()
        second = run("opening_balances", self.BALANCES, commit=True, date=GO_LIVE)
        self.assertFalse(second.committed)
        self.assertIn("already came in", second.errors[0][2])
        self.assertEqual((self.balance(bank), JournalEntry.objects.count()), (250000, entries))
        # The reverse path: the first entry reversed, a corrected file comes in.
        JournalEntry.objects.get(reference="Opening balances").reverse_by_hand(memo="wrong file")
        self.assertEqual(self.balance(bank), 0)
        third = run("opening_balances", self.BALANCES, commit=True, date=GO_LIVE)
        self.assertTrue(third.committed, third.errors)
        self.assertEqual(self.balance(bank), 250000)

    def test_opening_stock_file_run_twice_does_not_double_the_stock(self):
        from apps.inventory.adjustments import StockAdjustment

        AdjustmentReason.objects.create(code="OPENING", name="Opening stock", account=self.opening)
        self.open_books()
        first = run("opening_stock", self.STOCK, commit=True, date=GO_LIVE, reason="OPENING")
        self.assertEqual(first.errors, [])
        self.assertEqual((self.on_hand(), self.balance(self.opening)), (1500, Decimal("-3500.00")))
        # Under another reason posting to the same account it is still the same opening stock.
        AdjustmentReason.objects.create(code="OPEN2", name="Opening stock again", account=self.opening)
        for reason in ("OPENING", "OPEN2"):
            second = run("opening_stock", self.STOCK, commit=True, date=GO_LIVE, reason=reason)
            self.assertEqual((second.committed, [e[1] for e in second.errors]), (False, ["sku"]))
        self.assertEqual((self.on_hand(), self.balance(self.opening)), (1500, Decimal("-3500.00")))
        # The reverse path: the adjustment voided, the figure comes in again.
        StockAdjustment.objects.get(reason__code="OPENING").void(memo="wrong count")
        self.assertEqual(self.on_hand(), 500)
        third = run("opening_stock", self.STOCK, commit=True, date=GO_LIVE, reason="OPENING")
        self.assertEqual((third.committed, third.errors), (True, []))
        self.assertEqual(self.on_hand(), 1500)

    def test_an_opening_kind_that_does_not_ask_once_only_is_reported(self):
        from apps.core.management.commands.audit_invariants import Command

        def _opening_loans(rows, report, options):
            return None

        found = Command().opening_kinds_brought_in_twice({"opening_loans": _opening_loans})
        self.assertEqual([shape for shape, _ in found], ["opening kind brought in twice"])
        self.assertEqual(Command().opening_kinds_brought_in_twice(), [])


class WhatEachFileSaysItTakesTests(SimpleTestCase):
    """O263: the screen's line for a file names roles only where the file has a roles column."""

    def test_roles_named_only_where_a_roles_column_is(self):
        from .api import WHAT
        from .importer import columns_of
        for kind, words in WHAT.items():
            with self.subTest(kind=kind):
                if "roles" in words.lower() and "logins screen" not in words.lower():
                    self.assertIn("roles", columns_of(kind), words)


class TheOpeningPositionStandsOnOneDayTests(ImportTestCase):
    """
    O529: opening stock took any --date. Books opened on 1 October; stock dated the 10th came in, so the
    trial balance at the opening day lacked it. And with no books setting at all it came in on a day the
    books could then never be opened on.
    """

    STOCK = "sku,warehouse,quantity,unit_cost\nWDG-1,WH1,250,4.2\n"

    def setUp(self):
        super().setUp()
        AdjustmentReason.objects.create(code="OPENING", name="Opening stock", account=self.opening)
        self.on_hand = self.item.on_hand_at(self.warehouse)

    def test_stock_dated_any_other_day_than_the_books_opened_is_refused(self):
        self.open_books()
        for day in (GO_LIVE + datetime.timedelta(days=9), GO_LIVE - datetime.timedelta(days=1)):
            with self.subTest(day=day):
                report = run("opening_stock", self.STOCK, commit=True, date=day, reason="OPENING")
                self.assertEqual((report.committed, self.errors(report)), (False, [(0, "--date")]))
                self.assertIn(f"{day} is not the day the books opened (2026-10-01)", report.errors[0][2])
        self.assertEqual((self.item.on_hand_at(self.warehouse), self.balance(self.opening)),
                         (self.on_hand, Decimal("0")))

    def test_stock_with_no_date_is_dated_the_day_the_books_opened(self):
        from apps.inventory.adjustments import StockAdjustment

        self.open_books()
        report = run("opening_stock", self.STOCK, commit=True, reason="OPENING")
        self.assertTrue(report.committed, report.errors)
        self.assertEqual(StockAdjustment.objects.get(reason__code="OPENING").adjustment_date, GO_LIVE)

    def test_every_kind_that_posts_the_opening_position_waits_for_the_books_to_open(self):
        from apps.core.models import PartyRole, PartyRoleAssignment

        from .importer import POSTS_THE_OPENING_POSITION

        vendor = Party.objects.create(code="V-9", name="Granules", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=vendor, role=PartyRole.VENDOR)
        files = {
            "opening_stock": (self.STOCK, 0),
            "open_invoices": ("customer,reference,date,amount\nC-1,OLD/0412,2026-08-20,11800\n", 2),
            "open_bills": ("vendor,reference,date,amount\nV-9,GH-77,2026-09-02,5000\n", 2),
            "opening_balances": ("account,debit,credit\n1010,5,\n3900,,5\n", 0),
            "fixed_assets": ("category,name,cost,acquisition_date\nNONE,Loom,100,2020-04-01\n", 2),
        }
        self.assertEqual(set(files), set(POSTS_THE_OPENING_POSITION))
        for kind, (text, row) in files.items():
            with self.subTest(kind=kind):
                report = run(kind, text, commit=True, date=GO_LIVE, reason="OPENING", against="3900")
                self.assertEqual((report.committed, self.errors(report)), (False, [(row, "--date")]))
                self.assertIn("et the day the books opened (Settings, Books opening) first", report.errors[0][2])
        # What letting it through cost: the stock posted on 1 October, and the books could not open on it.
        self.assertEqual(self.balance(self.opening), Decimal("0"))
        self.open_books()

    def test_a_handler_reading_the_date_or_the_account_from_its_flag_is_reported(self):
        from apps.core.management.commands.audit_invariants import Command

        planted = 'def _opening_loans(rows, report, options):\n    on = options["date"]\n'
        found = Command().opening_kinds_read_their_own_flags(planted)
        self.assertEqual([shape for shape, _ in found], ["opening kind reading its own flag"])
        also = 'def _open_loans(row, options):\n    against = options.get("against")\n'
        self.assertEqual(len(Command().opening_kinds_read_their_own_flags(also)), 1)
        self.assertEqual(Command().opening_kinds_read_their_own_flags(), [])


class TheOpeningAccountClearsTests(ImportTestCase):
    """
    O530: --against took any account. The old trial balance below (worked in
    calc_g37_imports/opening_account.py): bank 2,50,000.00, receivables 11,800.00, stock 250 at 4.20 =
    1,050.00; payables 5,000.00, capital 2,57,850.00. Everything in, the opening-balance account is nothing.
    """

    STOCK = "sku,warehouse,quantity,unit_cost\nWDG-1,WH1,250,4.2\n"
    INVOICES = "customer,reference,date,amount\nC-1,OLD/0412,2026-08-20,11800\n"
    BILLS = "vendor,reference,date,amount\nV-9,GH-77,2026-09-02,5000\n"
    BALANCES = "account,debit,credit\n1010,250000,\n3950,,257850\n3900,7850,\n"

    def setUp(self):
        from apps.accounting.models import BooksOpening
        from apps.core.models import PartyRole, PartyRoleAssignment

        super().setUp()
        self.capital = Account.objects.create(code="3950", name="Share capital", account_type=AccountType.EQUITY)
        vendor = Party.objects.create(code="V-9", name="Granules", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=vendor, role=PartyRole.VENDOR)
        BooksOpening.objects.create(opened_on=GO_LIVE, opening_account=self.opening)

    def test_an_invoice_or_bill_against_any_other_account_is_refused(self):
        for kind, text in (("open_invoices", self.INVOICES), ("open_bills", self.BILLS)):
            with self.subTest(kind=kind):
                report = run(kind, text, commit=True, against="3950")
                self.assertEqual((report.committed, self.errors(report)), (False, [(2, "--against")]))
                self.assertIn("3950 is not the opening-balance account (3900)", report.errors[0][2])
        self.assertEqual((self.balance(self.capital), self.balance(self.ar), self.balance(self.payable)),
                         (Decimal("0"), Decimal("0"), Decimal("0")))

    def test_stock_through_a_reason_that_posts_elsewhere_is_refused(self):
        AdjustmentReason.objects.create(code="OPENCAP", name="Opening stock", account=self.capital)
        on_hand = self.item.on_hand_at(self.warehouse)
        report = run("opening_stock", self.STOCK, commit=True, reason="OPENCAP")
        self.assertEqual((report.committed, self.errors(report)), (False, [(0, "--reason")]))
        self.assertIn("posts to 3950, not to the opening-balance account (3900)", report.errors[0][2])
        self.assertEqual((self.item.on_hand_at(self.warehouse), self.balance(self.capital)), (on_hand, Decimal("0")))

    def test_with_everything_in_the_opening_account_is_nothing(self):
        AdjustmentReason.objects.create(code="OPENING", name="Opening stock", account=self.opening)
        # No --against at all: the books' setting says which account.
        for kind, text, options in (("opening_stock", self.STOCK, {"reason": "OPENING"}),
                                    ("open_invoices", self.INVOICES, {}),
                                    ("open_bills", self.BILLS, {"against": "3900"})):
            report = run(kind, text, commit=True, **options)
            self.assertTrue(report.committed, (kind, report.errors))
        # Stock 1,050.00 and the invoice 11,800.00 credited, the bill 5,000.00 debited.
        self.assertEqual(self.balance(self.opening), Decimal("-7850.00"))
        report = run("opening_balances", self.BALANCES, commit=True)
        self.assertTrue(report.committed, report.errors)
        self.assertEqual(self.balance(self.opening), Decimal("0.00"))
        self.assertEqual(
            (self.balance(self.bank), self.balance(self.ar), self.balance(self.inventory),
             self.balance(self.payable), self.balance(self.capital)),
            (Decimal("250000.00"), Decimal("11800.00"), Decimal("1050.00"), Decimal("-5000.00"),
             Decimal("-257850.00")))
