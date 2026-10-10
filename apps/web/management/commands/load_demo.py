"""
A demo plant to try the application on: Deccan Polysacks at Chakan, a
woven-sack maker, with a quarter of sales and purchases, production
runs, quality, maintenance, payroll, people, assets and the CRM, all
made through the application's own posting paths.

For a fresh installation only. It refuses a database that already holds
a company, an account, a party or an item, so it can never mix invented
records into a plant's books.

    docker compose run --rm web python manage.py load_demo
    python manage.py load_demo --password <password for every demo login>

The story ends on the day it is loaded: its months move forward to end
in the current one, and nothing in it is dated after today. Every name,
GSTIN and figure in it is invented.
"""

import calendar
import contextlib
import datetime
import secrets
import traceback
from decimal import Decimal as D

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from apps.core.models import lock_rows

M = apps.get_model
# The day the story was written for; loaded later, it moves with the calendar.
WRITTEN_FOR = datetime.date(2026, 10, 7)
LOGINS = [
    ("rohit", "Sales manager", "AR Manager, Line Manager"),
    ("imran", "Sales executive", "Sales Rep: sees only their own customers"),
    ("priya", "Sales executive, south and export", "Sales Rep"),
    ("ravi", "Purchase officer", "Purchasing Clerk, AP Manager"),
    ("sunita", "Stores keeper", "Stores Manager, Warehouse Staff"),
    ("suresh", "Production supervisor", "Production Supervisor, Planner, Process Engineer"),
    ("meena", "Quality inspector", "Quality Inspector, Quality Manager"),
    ("vikas", "Maintenance technician", "Maintenance"),
    ("anita", "Accountant", "Controller, Bookkeeper, GST Officer"),
    ("kiran", "HR and payroll", "HR Admin, Payroll Officer"),
]


class Story:
    """The story's calendar, moved by whole months to end in the current one."""

    def __init__(self, today):
        self.today = today
        self.shift = (today.year - WRITTEN_FOR.year) * 12 + today.month - WRITTEN_FOR.month

    def month(self, year, month, offset=0):
        """(year, month) of a story month, moved."""
        index = year * 12 + month - 1 + self.shift + offset
        return index // 12, index % 12 + 1

    def on(self, year, month, day):
        """A story date, moved; never after today."""
        y, m = self.month(year, month)
        return min(datetime.date(y, m, min(day, calendar.monthrange(y, m)[1])), self.today)

    def month_span(self, year, month, offset=0):
        y, m = self.month(year, month, offset)
        return datetime.date(y, m, 1), datetime.date(y, m, calendar.monthrange(y, m)[1])

    def day(self, offset):
        return self.today + datetime.timedelta(days=offset)


class Command(BaseCommand):
    help = "Load a demo woven-sack plant into an empty database, to try the application on."

    def add_arguments(self, parser):
        parser.add_argument("--password", help="The password for every demo login. Left out, a random one is made and printed.")

    def handle(self, *args, **options):
        # Run while the first start is still building the database, the roles would refuse permissions that
        # do not exist yet, in words that say nothing about waiting.
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if pending:
            raise CommandError(f"The database is still being built: {len(pending)} migration(s) to go. Wait until "
                               "the web process says it is listening (docker compose logs -f web), then run this again.")
        held = [label for label in ("core.Company", "accounting.Account", "core.Party", "inventory.Item")
                if M(label).objects.exists()]
        if held:
            raise CommandError(
                "This database already holds records (" + ", ".join(str(M(label)._meta.verbose_name_plural) for label in held)
                + "). The demo is for an empty installation only; it never mixes into real books.")
        call_command("setup_roles", verbosity=0)
        password = options.get("password") or secrets.token_urlsafe(9)
        failed = []
        self.load(Story(timezone.localdate()), password, failed)
        if failed:
            raise CommandError(f"The demo loaded only in part; these sections failed: {', '.join(failed)}. "
                               "Each is reported above.")
        self.stdout.write("\nThe demo plant is loaded. Sign in at /app/ as any of these, with the password "
                          f"{'you gave' if options.get('password') else password}:")
        self.stdout.write(f"  {'admin':8}  plant administrator, sees everything")
        for login, title, roles in LOGINS:
            self.stdout.write(f"  {login:8}  {title}: {roles}")

    @contextlib.contextmanager
    def step(self, name, failed):
        """One section in its own transaction: a failure is reported, rolled back and named at the end."""
        self.stdout.write(f"  {name}")
        try:
            with transaction.atomic():
                yield
        except Exception as exc:  # reported here and refused at the end, never swallowed
            failed.append(name)
            self.stderr.write(f"    FAILED: {type(exc).__name__}: {exc}")
            self.stderr.write("".join(traceback.format_exc(limit=4)))

    def load(self, s, password, failed):
        """The plant, told in order: each section uses what the ones before it made."""
        from apps.accounting.gst import gstin_check_character
        from apps.manufacturing.changes import raise_change

        def step(name):
            return self.step(name, failed)

        on, day, TODAY = s.on, s.day, s.today

        def gstin(prefix14):
            return prefix14 + gstin_check_character(prefix14)

        def stamp(date, hour=9):
            return timezone.make_aware(datetime.datetime(date.year, date.month, date.day, hour))

        # ---------------------------------------------------------- kernel
        with step("Company, currencies, units and the chart of accounts"):
            inr = M("core.Currency").objects.create(code="INR", name="Indian Rupee", symbol="₹", is_base=True)
            usd = M("core.Currency").objects.create(code="USD", name="US Dollar", symbol="$")
            M("core.ExchangeRate").objects.create(currency=usd, rate=D("83.4500"), valid_from=on(2026, 4, 1))
            M("core.ExchangeRate").objects.create(currency=usd, rate=D("84.1200"), valid_from=on(2026, 9, 1))
            UOM = M("core.UnitOfMeasure")
            kg = UOM.objects.create(code="kg", name="Kilogram", category="weight")
            nos = UOM.objects.create(code="nos", name="Numbers", category="count")
            M("gst.UnitQuantityCode").objects.create(uom=kg, code="KGS")
            M("gst.UnitQuantityCode").objects.create(uom=nos, code="NOS")
            Terms = M("core.PaymentTerms")
            net15 = Terms.objects.create(code="N15", name="Net 15 days", net_days=15)
            net30 = Terms.objects.create(code="N30", name="Net 30 days", net_days=30)
            net45 = Terms.objects.create(code="N45", name="Net 45 days", net_days=45)

            def acc(code, name, kind, holds_money=False):
                return M("accounting.Account").objects.create(code=code, name=name, account_type=kind,
                                                              holds_money=holds_money)

            bank = acc("1010", "HDFC Bank current account", "asset", holds_money=True)
            acc("1020", "Cash in hand", "asset", holds_money=True)
            ar = acc("1100", "Sundry debtors", "asset")
            stock_acc = acc("1200", "Stock in trade", "asset")
            wip = acc("1250", "Work in progress", "asset")
            vendor_adv = acc("1280", "Advances to suppliers", "asset")
            cgst_in, sgst_in, igst_in = (acc("1311", "CGST input", "asset"), acc("1312", "SGST input", "asset"),
                                         acc("1313", "IGST input", "asset"))
            plant_acc = acc("1500", "Plant and machinery", "asset")
            office_acc = acc("1510", "Office equipment", "asset")
            accumulated = acc("1590", "Accumulated depreciation", "asset")
            ap = acc("2000", "Sundry creditors", "liability")
            grni = acc("2150", "Goods received, not billed", "liability")
            deposits = acc("2200", "Customer advances", "liability")
            cgst_out, sgst_out, igst_out = (acc("2201", "CGST output", "liability"), acc("2202", "SGST output", "liability"),
                                            acc("2203", "IGST output", "liability"))
            igst0_out = acc("2204", "IGST output, zero-rated", "liability")
            pf_payable = acc("2420", "PF payable", "liability")
            pt_payable = acc("2430", "Professional tax payable", "liability")
            net_pay = acc("2440", "Salaries payable", "liability")
            acc("3000", "Share capital", "equity")
            opening_eq = acc("3900", "Opening balances", "equity")
            sales_sacks = acc("4000", "Sales: woven sacks", "income")
            acc("4010", "Sales: fabric and tape", "income")
            export_sales = acc("4020", "Export sales", "income")
            fx_gain = acc("4910", "Exchange gain", "income")
            cogs = acc("5000", "Cost of goods sold", "expense")
            variance = acc("5100", "Production variance", "expense")
            ppv = acc("5150", "Purchase price variance", "expense")
            scrap_acc = acc("5200", "Production scrap", "expense")
            power = acc("5300", "Power and fuel", "expense")
            repairs = acc("5400", "Repairs and maintenance", "expense")
            wages = acc("5500", "Salaries and wages", "expense")
            employer_pf = acc("5510", "Employer PF", "expense")
            freight = acc("5600", "Freight outward", "expense")
            travel = acc("5700", "Travel and conveyance", "expense")
            rent = acc("5750", "Rent", "expense")
            bank_charges = acc("5800", "Bank charges", "expense")
            discounts = acc("5810", "Settlement discounts", "expense")
            bad_debts = acc("5820", "Bad debts", "expense")
            fx_loss = acc("5830", "Exchange loss", "expense")
            depreciation = acc("5900", "Depreciation", "expense")
            M("core.Company").objects.create(
                name="Deccan Polysacks Pvt Ltd", legal_name="Deccan Polysacks Private Limited",
                email="accounts@deccanpolysacks.example", phone="+91 20 6712 4400", website="deccanpolysacks.example",
                bank_name="HDFC Bank, Chakan", bank_account_number="50200012345678", bank_ifsc="HDFC0001234",
                base_currency=inr, fiscal_year_start_month=4,
                default_inventory_account=stock_acc, default_cogs_account=cogs, grni_account=grni,
                settlement_discount_account=discounts, bad_debt_account=bad_debts, default_revenue_account=sales_sacks,
                default_receivable_account=ar, default_payable_account=ap, default_bank_account=bank,
                fx_gain_account=fx_gain, fx_loss_account=fx_loss, vendor_prepayment_account=vendor_adv,
                net_pay_account=net_pay, customer_deposit_account=deposits, purchase_price_variance_account=ppv,
            )
            M("manufacturing.ManufacturingSettings").objects.create(wip_account=wip, variance_account=variance,
                                                                    scrap_account=scrap_acc)

        with step("GST: rates, the inter-state and export positions, the registration"):
            Tax, Position, Mapping = M("accounting.Tax"), M("accounting.FiscalPosition"), M("accounting.FiscalPositionTaxMapping")
            cgst = Tax.objects.create(code="CGST9", name="CGST", rate=D("9"), gst_head="cgst",
                                      collected_account=cgst_out, paid_account=cgst_in)
            sgst = Tax.objects.create(code="SGST9", name="SGST", rate=D("9"), gst_head="sgst",
                                      collected_account=sgst_out, paid_account=sgst_in)
            igst = Tax.objects.create(code="IGST18", name="IGST", rate=D("18"), gst_head="igst",
                                      collected_account=igst_out, paid_account=igst_in)
            igst0 = Tax.objects.create(code="IGST0", name="IGST, zero-rated", rate=D("0"), gst_head="igst",
                                       collected_account=igst0_out, paid_account=igst_in)
            gst18 = [cgst, sgst]

            def position(code, name, cgst_to):
                fiscal = Position.objects.create(code=code, name=name)
                Mapping.objects.create(fiscal_position=fiscal, source_tax=cgst, target_tax=cgst_to)
                Mapping.objects.create(fiscal_position=fiscal, source_tax=sgst, target_tax=None)
                return fiscal

            inter = position("INTER", "Inter-state supply", igst)
            lut = position("LUT", "Export under LUT", igst0)
            M("accounting.GstSettings").objects.create(gstin=gstin("27AAECD4821K1Z"), interstate_position=inter)

        with step("Warehouses, bins, items and the opening stock"):
            Warehouse, Bin, Item = M("inventory.Warehouse"), M("inventory.StorageBin"), M("inventory.Item")
            plant = Warehouse.objects.create(code="PLANT", name="Chakan plant")
            godown = Warehouse.objects.create(code="FG", name="Finished goods godown", requires_bins=True)
            bins = {code: Bin.objects.create(warehouse=godown, code=code, name=name, sequence=n)
                    for n, (code, name) in enumerate([("A-01", "Aisle A, bay 1"), ("A-02", "Aisle A, bay 2"),
                                                      ("B-01", "Aisle B, bay 1"), ("B-02", "Aisle B, bay 2")], start=1)}

            def item(sku, name, uom, cost, price=None, hsn=""):
                return Item.objects.create(sku=sku, name=name, uom=uom, standard_cost=D(cost),
                                           sale_price=D(price) if price else None, hsn_code=hsn)

            virgin = item("PP-RAFFIA", "PP raffia granule, homopolymer", kg, "112", hsn="39021000")
            filler = item("CACO3-MB", "Calcium carbonate filler masterbatch", kg, "38", hsn="38249900")
            uv = item("UV-MB", "UV stabiliser masterbatch", kg, "310", hsn="38123990")
            regrind = item("REGRIND", "Reprocessed PP (own trim)", kg, "60", hsn="39159090")
            liner_film = item("LDPE-FILM", "LDPE liner film, 40 micron", kg, "125", hsn="39201019")
            thread = item("THREAD-PE", "Polyester stitching thread", kg, "260", hsn="54023300")
            ink = item("INK-BLUE", "Flexo printing ink, reflex blue", kg, "420", hsn="32151190")
            tape = item("TAPE-900", "PP tape, 900 denier", kg, "128", hsn="54062000")
            fabric = item("FAB-60", "Circular woven fabric, 60 cm", kg, "139", hsn="54074200")
            cement = item("SACK-CEM50", "Cement sack 50 kg, 2-colour print", nos, "7.10", "9.80", "63053300")
            fert = item("SACK-FERT50", "Fertiliser sack 50 kg with liner", nos, "10.60", "14.50", "63053300")
            rice = item("SACK-RICE25", "Rice bag 25 kg, 4-colour print", nos, "8.30", "11.20", "63053300")
            bearing = item("SP-6205", "Bearing 6205 (loom)", nos, "240", hsn="84821011")
            heater = item("SP-HEATER", "Extruder heater band", nos, "1850", hsn="85168000")

            Movement = M("inventory.StockMovement")
            opened = on(2026, 7, 1)
            shelf = [(virgin, "18000", "112", plant, None), (filler, "6000", "38", plant, None),
                     (uv, "250", "310", plant, None), (regrind, "1500", "60", plant, None),
                     (liner_film, "3500", "125", plant, None), (thread, "400", "260", plant, None),
                     (ink, "300", "420", plant, None), (tape, "6000", "128", plant, None),
                     (fabric, "9000", "139", plant, None), (cement, "760000", "7.10", plant, None),
                     (fert, "300000", "10.60", plant, None), (rice, "150000", "8.30", plant, None),
                     (bearing, "40", "240", plant, None), (heater, "12", "1850", plant, None),
                     (cement, "80000", "7.10", godown, "A-01"), (cement, "60000", "7.10", godown, "B-02"),
                     (fert, "45000", "10.60", godown, "A-02"), (rice, "20000", "8.30", godown, "B-01")]
            for it, qty, cost, where, bin_code in shelf:
                Movement.objects.create(item=it, warehouse=where, bin=bins.get(bin_code), movement_type="receipt",
                                        uom=it.uom, quantity=D(qty), unit_cost=D(cost), occurred_at=stamp(opened, 8),
                                        notes="Opening stock")
            # The shelves' value into the books, as a cutover does it.
            value = sum((D(qty) * D(cost) for _, qty, cost, _, _ in shelf), D("0"))
            entry = M("accounting.JournalEntry").objects.create(date=opened, memo="Opening stock at cutover")
            M("accounting.JournalLine").objects.create(entry=entry, account=stock_acc, debit=value, credit=D("0"))
            M("accounting.JournalLine").objects.create(entry=entry, account=opening_eq, debit=D("0"), credit=value)
            entry.post()

        # --------------------------------------------------------- parties
        with step("Customers and vendors, with GST registrations and agreed prices"):
            Party, Role = M("core.Party"), M("core.PartyRoleAssignment")

            def party(code, name, role, city, pin, gst, terms, contact, email, currency=None, **tax):
                p = Party.objects.create(code=code, name=name, legal_name=name, email=email,
                                         default_currency=currency or inr, payment_terms=terms)
                Role.objects.create(party=p, role=role)
                M("core.Address").objects.create(party=p, line1="Plot 14, MIDC industrial area", city=city,
                                                 postal_code=pin, is_primary=True)
                first, last, title = contact
                M("core.Contact").objects.create(party=p, first_name=first, last_name=last, job_title=title,
                                                 email=email, is_primary=True)
                M("accounting.PartyTaxProfile").objects.create(party=p, gstin=gstin(gst) if gst else "", **tax)
                return p

            sahyadri = party("C-SAHY", "Sahyadri Cement Works", "customer", "Pune", "411019", "27AAHCS4410M1Z", net45,
                             ("Rahul", "Mehta", "Purchase manager"), "purchase@sahyadricement.example")
            godavari = party("C-GODA", "Godavari Fertilisers Ltd", "customer", "Kakinada", "533003", "37AACCG7731P1Z", net30,
                             ("K.", "Srinivas", "Stores head"), "stores@godavarifert.example")
            konkan = party("C-KONK", "Konkan Agro Foods", "customer", "Ratnagiri", "415612", "27AAFCK2290Q1Z", net30,
                           ("Sneha", "Sawant", "Owner"), "sneha@konkanagro.example")
            narmada = party("C-NARM", "Narmada Sugar Mills", "customer", "Bharuch", "392001", "24AABCN5512R1Z", net30,
                            ("Hitesh", "Patel", "Purchase"), "buy@narmadasugar.example")
            malwa = party("C-MALW", "Malwa Seeds and Grains", "customer", "Indore", "452001", "23AAGFM8814S1Z", net15,
                          ("Arjun", "Tiwari", "Partner"), "arjun@malwaseeds.example")
            kaveri = party("C-KAVR", "Kaveri Feeds", "customer", "Mysuru", "570016", "29AAFCK6627T1Z", net30,
                           ("Deepa", "Gowda", "Accounts"), "accounts@kaverifeeds.example")
            gulf = party("C-GULF", "Gulf Bulk Packaging Trading LLC", "customer", "Dubai", "", None, net45,
                         ("Omar", "Haddad", "Buyer"), "omar@gulfbulk.example", currency=usd,
                         gst_registration="overseas", fiscal_position=lut)
            granule = party("V-GRAN", "Granule House Polymers", "vendor", "Vadodara", "390010", "24AACCG1180A1Z", net30,
                            ("Nilesh", "Shah", "Sales"), "sales@granulehouse.example")
            western = party("V-WMB", "Western Masterbatch Pvt Ltd", "vendor", "Thane", "400601", "27AABCW3391B1Z", net30,
                            ("Farhan", "Qureshi", "Sales"), "orders@westernmb.example", msme_category="small",
                            udyam_number="UDYAM-MH-33-0012345")
            inks = party("V-INK", "Bharat Printing Inks", "vendor", "Mumbai", "400093", "27AAFFB5562C1Z", net30,
                         ("Ketan", "Desai", "Sales"), "ketan@bharatinks.example")
            liner = party("V-LINER", "Deccan Liner Films", "vendor", "Pune", "411026", "27AADCD7720D1Z", net15,
                          ("Vaibhav", "Kale", "Sales"), "vk@deccanliner.example")
            threads = party("V-THRD", "Shakti Thread Mills", "vendor", "Surat", "395003", "24AAJFS9935E1Z", net30,
                            ("Bhavesh", "Joshi", "Sales"), "sales@shaktithread.example")
            spares = party("V-SPAR", "Loomtech Spares", "vendor", "Coimbatore", "641018", "33AAKFL4407F1Z", net30,
                           ("Senthil", "Kumar", "Service"), "service@loomtech.example")
            roadways = party("V-SRW", "Sharma Roadways", "vendor", "Pune", "411019", "27AAKFS5521G1Z", net15,
                             ("Manoj", "Sharma", "Bookings"), "bookings@sharmaroadways.example")
            for vendor, it, price, lead in ((granule, virgin, "112", 7), (western, filler, "38", 5), (western, uv, "310", 5),
                                            (inks, ink, "420", 4), (liner, liner_film, "125", 3), (threads, thread, "260", 6),
                                            (spares, bearing, "240", 10), (spares, heater, "1850", 10)):
                M("purchasing.VendorPrice").objects.create(vendor=vendor, item=it, unit_price=D(price), currency=inr,
                                                           lead_time_days=lead, is_preferred=True, valid_from=on(2026, 4, 1))

        with step("Departments, cost centres, staff and their logins"):
            centres = {code: M("accounting.CostCentre").objects.create(code=code, name=name) for code, name in (
                ("EXT", "Tape extrusion"), ("LOOM", "Loom shed"), ("PRINT", "Printing"),
                ("CONV", "Conversion and baling"), ("OFFICE", "Office and sales"))}
            depts = {code: M("hr.Department").objects.create(code=code, name=name, centre=centres[centre])
                     for code, name, centre in (("SALES", "Sales", "OFFICE"), ("PURCH", "Purchase and stores", "OFFICE"),
                                                ("PROD", "Production", "LOOM"), ("QA", "Quality", "OFFICE"),
                                                ("MAINT", "Maintenance", "LOOM"), ("ACCT", "Accounts and HR", "OFFICE"))}
            groups = {g.name: g for g in Group.objects.all()}
            User = get_user_model()
            people = {}

            def person(number, name, dept, title, hired, login=None, roles=()):
                p = Party.objects.create(code=f"E-{number}", name=name,
                                         email=f"{name.split()[0].lower()}@deccanpolysacks.example")
                Role.objects.create(party=p, role="employee")
                user = None
                if login and not User.objects.filter(username=login).exists():
                    user = User.objects.create_user(login, password=password, email=p.email,
                                                    first_name=name.split()[0], last_name=name.split()[-1])
                    user.groups.add(*[groups[role] for role in roles if role in groups])
                employee = M("hr.Employee").objects.create(party=p, employee_number=number, hire_date=hired,
                                                           department=depts[dept], job_title=title, user=user)
                people[login or number] = employee
                return employee

            d = datetime.date
            rohit = person("101", "Rohit Bhosale", "SALES", "Sales manager", d(2019, 6, 1), "rohit", ["AR Manager", "Line Manager"])
            imran = person("102", "Imran Shaikh", "SALES", "Sales executive", d(2022, 1, 10), "imran",
                           ["Sales Rep", "Employee Self Service"])
            priya = person("103", "Priya Nair", "SALES", "Sales executive, south and export", d(2023, 3, 1), "priya",
                           ["Sales Rep", "Employee Self Service"])
            ravi = person("201", "Ravi Kulkarni", "PURCH", "Purchase officer", d(2020, 8, 1), "ravi",
                          ["Purchasing Clerk", "AP Manager"])
            sunita = person("202", "Sunita Pawar", "PURCH", "Stores keeper", d(2021, 2, 15), "sunita",
                            ["Stores Manager", "Warehouse Staff"])
            suresh = person("301", "Suresh Patil", "PROD", "Production supervisor", d(2018, 4, 1), "suresh",
                            ["Production Supervisor", "Production Planner", "Process Engineer", "Line Manager"])
            mahesh = person("302", "Mahesh Jadhav", "PROD", "Extrusion operator", d(2021, 7, 1))
            lakshmi = person("303", "Lakshmi Rao", "PROD", "Loom operator", d(2022, 9, 1))
            ganesh = person("304", "Ganesh More", "PROD", "Stitching operator", d(2023, 5, 1))
            meena = person("401", "Meena Joshi", "QA", "Quality inspector", d(2020, 11, 1), "meena",
                           ["Quality Inspector", "Quality Manager"])
            vikas = person("501", "Vikas Pawar", "MAINT", "Maintenance technician", d(2019, 1, 7), "vikas", ["Maintenance"])
            anita = person("601", "Anita Deshmukh", "ACCT", "Accountant", d(2017, 10, 1), "anita",
                           ["Controller", "Bookkeeper", "GST Officer"])
            kiran = person("602", "Kiran Shetty", "ACCT", "HR and payroll", d(2021, 6, 1), "kiran", ["HR Admin", "Payroll Officer"])
            for employee, boss in ((imran, rohit), (priya, rohit), (mahesh, suresh), (lakshmi, suresh), (ganesh, suresh),
                                   (sunita, ravi), (ravi, anita), (suresh, anita), (meena, suresh), (vikas, suresh),
                                   (kiran, anita), (rohit, anita)):
                employee.manager = boss
                employee.save()
            admin = User.objects.filter(username="admin").first() or User.objects.create_superuser(
                "admin", "admin@deccanpolysacks.example", password, first_name="Plant", last_name="Administrator")

        with step("Sales reps, teams, targets and what each customer is allowed"):
            Rep, Team = M("sales.SalesRep"), M("sales.SalesTeam")
            rep_rohit, rep_imran, rep_priya = (Rep.objects.create(party=e.party) for e in (rohit, imran, priya))
            west = Team.objects.create(code="WEST", name="West zone", leader=rep_rohit)
            south = Team.objects.create(code="SOUTH", name="South and export", leader=rep_priya)
            for rep, team in ((rep_rohit, west), (rep_imran, west), (rep_priya, south)):
                rep.team = team
                rep.save()
            # What they fill, how the goods travel and how they are packed: each new order records them.
            for customer, rep, limit, trade, carriage, bale, extra in (
                    (sahyadri, imran, "12000000", "cement", "for_destination", 500,
                     {"transporter": roadways, "marking": "Brand and batch on the front; month of manufacture on the back"}),
                    (konkan, imran, "2500000", "food_grain", "ex_works", 250, {}),
                    (narmada, rohit, "5000000", "sugar", "for_destination", 500, {"transporter": roadways}),
                    (malwa, imran, "1500000", "food_grain", "ex_works", 250, {}),
                    (godavari, priya, "6000000", "fertiliser", "to_pay", 500, {"marking": "Batch number on the gusset"}),
                    (kaveri, priya, "3000000", "feed", "for_destination", 500, {}),
                    (gulf, priya, "12000000", "other", "", 1000, {"incoterm": "CIF", "port_of_discharge": "Jebel Ali"})):
                M("sales.CustomerProfile").objects.create(party=customer, sales_rep=rep.party, credit_limit=D(limit),
                                                          industry=trade, freight_terms=carriage, sacks_per_bale=bale,
                                                          **extra)
            quarter = (s.month_span(2026, 7)[0], s.month_span(2026, 9)[1])
            this_month = s.month_span(2026, 10)
            for who, span, amount in (({"team": west}, quarter, "7500000"), ({"team": south}, quarter, "5500000"),
                                      ({"rep": rep_imran}, quarter, "5500000"), ({"rep": rep_priya}, quarter, "5000000"),
                                      ({"team": west}, this_month, "3000000"), ({"rep": rep_imran}, this_month, "1800000")):
                M("sales.SalesTarget").objects.create(period_start=span[0], period_end=span[1], amount=D(amount), **who)

        # ----------------------------------------------------------- sales
        with step("Three months of orders, deliveries, invoices and money received"):
            Order, OrderLine = M("sales.SalesOrder"), M("sales.SalesOrderLine")
            Delivery, DeliveryLine = M("sales.Delivery"), M("sales.DeliveryLine")
            Payment, Allocation = M("accounting.Payment"), M("sales.InvoicePayment")
            invoices = []
            # One transaction confirms every customer's orders in date order; held first, in key
            # order, each confirm finds its customer already held (O183).
            lock_rows(sahyadri, konkan, narmada, malwa, godavari, kaveri, gulf, refresh=False)

            def order(customer, when, lines, currency=None):
                o = Order.objects.create(customer=customer, order_date=when, currency=currency or inr)
                for it, qty, price in lines:
                    line = OrderLine.objects.create(
                        order=o, item=it, uom=it.uom, quantity=D(qty), unit_price=D(price), warehouse=plant,
                        revenue_account=export_sales if customer == gulf else sales_sacks,
                        delivery_date=when + datetime.timedelta(days=7))
                    line.taxes.set(gst18)
                o.confirm()
                return o

            def sell(customer, when, lines, paid=None, currency=None):
                o = order(customer, when, lines, currency)
                shipped = min(when + datetime.timedelta(days=3), TODAY)
                delivery = Delivery.objects.create(sales_order=o, delivery_date=shipped)
                for line in o.lines.all():
                    DeliveryLine.objects.create(delivery=delivery, order_line=line, warehouse=plant,
                                                quantity_shipped=line.quantity)
                delivery.post()
                invoice = o.create_invoice(ar, invoice_date=shipped)
                invoice.post()
                invoices.append(invoice)
                received = min(invoice.invoice_date + datetime.timedelta(days=28), TODAY)
                if paid and received < TODAY:
                    amount = (invoice.total() * D(paid)).quantize(D("0.01"))
                    payment = Payment.objects.create(
                        party=customer, direction="receipt", amount=amount, payment_date=received,
                        currency=currency or inr, bank_account=bank, counterpart_account=ar,
                        reference=f"NEFT {customer.code[-4:]}{invoice.pk:05d}")
                    payment.post()
                    Allocation.objects.create(invoice=invoice, payment=payment, amount=amount)
                return invoice

            sell(sahyadri, on(2026, 7, 6), [(cement, "180000", "9.80")], paid="1")
            sell(godavari, on(2026, 7, 9), [(fert, "75000", "14.50")], paid="1")
            sell(konkan, on(2026, 7, 14), [(rice, "36000", "11.20")], paid="1")
            short = sell(narmada, on(2026, 7, 21), [(cement, "45000", "9.60"), (rice, "15000", "11.00")], paid="1")
            sell(sahyadri, on(2026, 8, 3), [(cement, "165000", "9.80")], paid="1")
            sell(kaveri, on(2026, 8, 6), [(fert, "30000", "14.20"), (rice, "18000", "11.20")], paid="1")
            sell(gulf, on(2026, 8, 10), [(fert, "90000", "0.19")], paid="1", currency=usd)
            sell(malwa, on(2026, 8, 18), [(rice, "24000", "11.40")], paid="0.5")
            sell(godavari, on(2026, 8, 25), [(fert, "60000", "14.50")], paid="1")
            sell(sahyadri, on(2026, 9, 2), [(cement, "150000", "9.90")], paid="1")
            sell(narmada, on(2026, 9, 8), [(cement, "54000", "9.60")])
            sell(konkan, on(2026, 9, 15), [(rice, "27000", "11.20")], paid="1")
            sell(kaveri, on(2026, 9, 22), [(fert, "36000", "14.20")])
            sell(sahyadri, on(2026, 9, 28), [(cement, "135000", "9.90")])
            sell(malwa, on(2026, 10, 1), [(rice, "18000", "11.40")])
            short.create_credit_note(quantities={short.lines.first(): D("2400")}, memo="2,400 sacks short-stitched, credited")
            # Open orders: to ship this week, two of them drafted from the godown's bins.
            open_cement = order(sahyadri, day(-2), [(cement, "120000", "9.90")])
            order(godavari, day(-1), [(fert, "54000", "14.50")])
            open_rice = order(konkan, TODAY, [(rice, "45000", "11.20")])
            for o, qty in ((open_cement, "120000"), (open_rice, "45000")):
                draft = Delivery.objects.create(sales_order=o, delivery_date=TODAY)
                DeliveryLine.objects.create(delivery=draft, order_line=o.lines.first(), warehouse=godown, quantity_shipped=D(qty))
            unconfirmed = Order.objects.create(customer=narmada, order_date=TODAY, currency=inr)
            OrderLine.objects.create(order=unconfirmed, item=cement, uom=nos, quantity=D("60000"), unit_price=D("9.60"),
                                     revenue_account=sales_sacks, warehouse=plant, delivery_date=day(14)).taxes.set(gst18)

        with step("Quotations"):
            for customer, when, it, qty, price in ((kaveri, day(-6), fert, "45000", "14.20"),
                                                   (narmada, day(-3), cement, "90000", "9.55"),
                                                   (gulf, day(-1), fert, "120000", "0.185")):
                quote = M("sales.Quotation").objects.create(customer=customer, quotation_date=when,
                                                            valid_until=when + datetime.timedelta(days=15),
                                                            currency=usd if customer == gulf else inr)
                M("sales.QuotationLine").objects.create(
                    quotation=quote, item=it, uom=it.uom, quantity=D(qty), unit_price=D(price),
                    revenue_account=export_sales if customer == gulf else sales_sacks).taxes.set(gst18)

        with step("The CRM: a campaign, leads, opportunities and calls"):
            Lead, Opportunity, Activity = M("sales.Lead"), M("sales.Opportunity"), M("sales.Activity")
            expo = M("sales.Campaign").objects.create(code="AGRI26", name="Agri packaging expo, Nagpur",
                                                      channel="exhibition", starts_on=on(2026, 9, 12),
                                                      ends_on=on(2026, 9, 14), budget=D("180000"))
            leads = [Lead.objects.create(company_name=company, contact_name=contact, phone=phone, email=email, city=city,
                                         source=source, interest=interest, owner=owner.party if owner else None,
                                         campaign=campaign)
                     for company, contact, phone, email, city, source, interest, owner, campaign in (
                         ("Vidarbha Rice Exports", "Sanjay Wankhede", "98230 41122", "sanjay@vidarbharice.example", "Nagpur",
                          "exhibition", "25 kg BOPP rice bags, 40,000 a month", imran, expo),
                         ("Satpura Pulses", "Neha Agrawal", "98260 77310", "", "Jabalpur", "exhibition",
                          "50 kg plain sacks for dal", imran, expo),
                         ("Coastal Salt Works", "Joseph Fernandes", "", "joseph@coastalsalt.example", "Thoothukudi", "web",
                          "Laminated 25 kg salt bags", priya, None),
                         ("Sangli Turmeric Traders", "", "97300 11245", "", "Sangli", "phone", "", None, None),
                         ("Bharat Cement Kurnool", "Ravi Teja", "99490 55678", "procure@bharatcementknl.example", "Kurnool",
                          "referral", "Cement sacks, a lakh a month, valve type", priya, None),
                         ("Shree Ganesh Feeds", "Amol Kadam", "98500 33421", "amol@ganeshfeeds.example", "Kolhapur", "walk_in",
                          "Feed bags with liner", imran, None))]
            leads[0].status = "working"
            leads[0].save()
            for lead, kind, summary, done in ((leads[0], "visit", "Visited the stall; sample bags sent", on(2026, 9, 13)),
                                              (leads[0], "call", "Wants a quote for two sizes", on(2026, 9, 20)),
                                              (leads[4], "visit", "Plant visit with the purchase head", on(2026, 9, 25))):
                Activity.objects.create(kind=kind, lead=lead, owner=lead.owner, summary=summary, done_on=done)
            Activity.objects.create(kind="call", lead=leads[1], owner=imran.party, summary="Follow up after the expo",
                                    due_on=day(1))
            Lead.objects.filter(pk=leads[3].pk).update(created_at=timezone.now() - datetime.timedelta(days=120))
            leads[3].refresh_from_db()
            leads[3].lose("Buys jute; no interest in woven PP")
            opportunities = [Opportunity.objects.create(customer=customer, title=title, owner=owner.party, stage=stage,
                                                        value=D(value), expected_on=expected)
                             for customer, title, owner, stage, value, expected in (
                                 (sahyadri, "Valve sacks for the new Satara grinding unit", imran, "qualified", "4200000", day(40)),
                                 (godavari, "Rabi season: 1.5 lakh fertiliser sacks", priya, "quoted", "2175000", day(20)),
                                 (narmada, "Laminated sugar bags trial", rohit, "new", "650000", day(60)),
                                 (kaveri, "Feed bags with liner, annual contract", priya, "qualified", "1800000", day(35)),
                                 (gulf, "FIBC liners, export trial", priya, "new", "1200000", day(75)),
                                 (konkan, "Mango pulp carton liners", imran, "quoted", "320000", day(10)))]
            Opportunity.objects.create(customer=malwa, title="Seed bags, kharif repeat", owner=imran.party,
                                       value=D("380000"), stage="quoted").win(on_date=on(2026, 9, 30))
            Opportunity.objects.create(customer=narmada, title="Jumbo bags", owner=rohit.party,
                                       value=D("900000")).lose("Price: a Gujarat mill quoted 6% lower", on_date=on(2026, 9, 18))
            Activity.objects.create(kind="call", opportunity=opportunities[1], owner=priya.party,
                                    summary="Confirm print artwork for the rabi bags", due_on=day(2))
            Activity.objects.create(kind="visit", opportunity=opportunities[0], owner=imran.party,
                                    summary="Sample trial at Satara", due_on=day(5))

        # ------------------------------------------------------ purchasing
        with step("Purchase orders, goods in, bills and money paid"):
            PO, POLine = M("purchasing.PurchaseOrder"), M("purchasing.PurchaseOrderLine")

            def buy(vendor, when, lines, receive=True, bill=True, pay=False, part=None):
                po = PO.objects.create(vendor=vendor, order_date=when, currency=inr)
                for it, qty, price in lines:
                    POLine.objects.create(order=po, item=it, uom=it.uom, quantity=D(qty), unit_price=D(price),
                                          warehouse=plant, expected_date=when + datetime.timedelta(days=7)).taxes.set(gst18)
                po.confirm()
                if not receive:
                    return
                receipt = po.create_receipt(receipt_date=min(when + datetime.timedelta(days=5), TODAY), warehouse=plant)
                if part:
                    for row in receipt.lines.all():
                        row.quantity_received = (row.quantity_received * D(part)).quantize(D("1"))
                        row.save()
                receipt.post()
                if not bill:
                    return
                b = po.create_bill(ap, bill_date=min(when + datetime.timedelta(days=6), TODAY),
                                   reference=f"{vendor.code[-4:]}/{when:%y%m}/{po.pk:03d}")
                b.post()
                paid_on = b.bill_date + datetime.timedelta(days=25)
                if pay and paid_on < TODAY:
                    payment = Payment.objects.create(party=vendor, direction="disbursement", amount=b.total(),
                                                     payment_date=paid_on, currency=inr, bank_account=bank,
                                                     counterpart_account=ap, reference=f"RTGS {po.pk:04d}")
                    payment.post()
                    M("purchasing.BillPayment").objects.create(bill=b, payment=payment, amount=b.total())

            buy(granule, on(2026, 7, 3), [(virgin, "15000", "112")], pay=True)
            buy(western, on(2026, 7, 8), [(filler, "4000", "38"), (uv, "150", "310")], pay=True)
            buy(threads, on(2026, 7, 15), [(thread, "250", "260")], pay=True)
            buy(granule, on(2026, 8, 4), [(virgin, "16000", "113.50")], pay=True)
            buy(inks, on(2026, 8, 11), [(ink, "200", "420")], pay=True)
            buy(liner, on(2026, 8, 19), [(liner_film, "2500", "125")], pay=True)
            buy(granule, on(2026, 9, 3), [(virgin, "18000", "115")])
            buy(western, on(2026, 9, 10), [(filler, "5000", "38.50")])
            buy(spares, on(2026, 9, 16), [(bearing, "24", "240"), (heater, "4", "1850")])
            buy(granule, on(2026, 9, 29), [(virgin, "12000", "116")], bill=False, part="0.5")
            buy(threads, day(-3), [(thread, "300", "262")], receive=False)
            buy(liner, day(-1), [(liner_film, "3000", "124")], receive=False)

        with step("Requisitions, and one out for quotes with three answers"):
            Requisition, RequisitionLine = M("purchasing.PurchaseRequisition"), M("purchasing.PurchaseRequisitionLine")

            def requisition(when, needed, rows, why, approve=True):
                r = Requisition.objects.create(requested_by=sunita.party, request_date=when, needed_by=needed, justification=why)
                for it, qty, estimate, vendor in rows:
                    RequisitionLine.objects.create(requisition=r, item=it, uom=it.uom, quantity=D(qty),
                                                   estimated_price=D(estimate), suggested_vendor=vendor)
                r.submit()
                if approve:
                    r.approve(by=admin, note="Within the month's budget")
                return r

            asked = requisition(day(-8), day(10), [(uv, "200", "305", western), (ink, "150", "415", inks)],
                                "UV for the export order; ink for the rabi print run")
            rfq = asked.create_rfq(response_due=day(3))
            M("purchasing.RfqInvitation").objects.create(rfq=rfq, vendor=granule)
            rfq.issue()
            for vendor, prices in ((western, {"UV-MB": "298"}), (inks, {"INK-BLUE": "409"}),
                                   (granule, {"UV-MB": "312", "INK-BLUE": "430"})):
                invitation = rfq.invited.get(vendor=vendor)
                for line in rfq.lines.all():
                    if line.item.sku in prices:
                        invitation.quote(line, D(prices[line.item.sku]), lead_time_days=6)
            requisition(day(-4), day(14), [(bearing, "30", "240", spares), (heater, "6", "1850", None)],
                        "Loom shed spares for the quarter")
            requisition(day(-1), day(20), [(liner_film, "4000", "123", liner)], "Liner for 60,000 fertiliser sacks",
                        approve=False)

        # ------------------------------------------------------ production
        with step("Work centres, machines, routings, bills of materials, shifts and reasons"):
            WorkCentre, Machine = M("manufacturing.WorkCentre"), M("manufacturing.Machine")
            ext = WorkCentre.objects.create(code="EXT", name="Tape extrusion", capacity_per_hour=D("450"),
                                            capacity_uom=kg, available_hours_per_day=D("24"))
            looms = WorkCentre.objects.create(code="LOOM", name="Circular looms", capacity_per_hour=D("360"),
                                              capacity_uom=kg, available_hours_per_day=D("24"))
            press = WorkCentre.objects.create(code="PRINT", name="Flexo printing", capacity_per_hour=D("6000"),
                                              capacity_uom=nos, available_hours_per_day=D("16"))
            conv = WorkCentre.objects.create(code="CONV", name="Cutting and stitching", capacity_per_hour=D("5000"),
                                             capacity_uom=nos, available_hours_per_day=D("16"))
            machines = {"EXT-1": Machine.objects.create(work_centre=ext, code="EXT-1", name="Tape line, 120 mm"),
                        "PRN-1": Machine.objects.create(work_centre=press, code="PRN-1", name="4-colour flexo press")}
            for n in range(1, 7):
                machines[f"LOOM-{n:02d}"] = Machine.objects.create(work_centre=looms, code=f"LOOM-{n:02d}",
                                                                   name=f"6-shuttle circular loom {n}")
            for n in (1, 2):
                machines[f"STITCH-{n}"] = Machine.objects.create(work_centre=conv, code=f"STITCH-{n}",
                                                                 name=f"Cut-and-stitch line {n}")

            def routing(code, name, operations):
                r = M("manufacturing.Routing").objects.create(code=code, name=name)
                for n, (operation, centre, setup) in enumerate(operations, start=1):
                    M("manufacturing.RoutingOperation").objects.create(routing=r, sequence=n * 10, name=operation,
                                                                       work_centre=centre, setup_minutes=D(setup))
                return r

            def bom(it, name, per, r, rows, byproduct=None):
                b = M("manufacturing.BillOfMaterials").objects.create(item=it, name=name, quantity_produced=D(per),
                                                                      uom=it.uom, routing=r)
                for n, (component, qty, waste) in enumerate(rows, start=1):
                    M("manufacturing.BomComponent").objects.create(bom=b, item=component, quantity=D(qty), uom=component.uom,
                                                                   waste_percent=D(waste), line_number=n)
                if byproduct:
                    M("manufacturing.BomByproduct").objects.create(bom=b, item=byproduct[0], quantity=D(byproduct[1]),
                                                                   uom=byproduct[0].uom, valuation="standard")
                return b

            tape_bom = bom(tape, "Tape 900 den, 16% filler", "100", routing("R-TAPE", "Extrude tape", [("Extrude and stretch", ext, "90")]),
                           [(virgin, "78", "3"), (filler, "16", "3"), (uv, "1", "3"), (regrind, "5", "3")], (regrind, "2.4742"))
            fabric_bom = bom(fabric, "Fabric 60 cm, 10x10", "100", routing("R-FAB", "Weave fabric", [("Weave", looms, "30")]),
                             [(tape, "100", "2")])
            sack_route = routing("R-SACK", "Print, cut and stitch", [("Print", press, "45"), ("Cut and stitch", conv, "20")])
            cement_bom = bom(cement, "Cement 50 kg, 2-colour", "1000", sack_route,
                             [(fabric, "74", "1.5"), (thread, "1.2", "0"), (ink, "0.5", "0")])
            fert_bom = bom(fert, "Fertiliser 50 kg with liner", "1000", sack_route,
                           [(fabric, "92", "1.5"), (liner_film, "38", "1"), (thread, "1.4", "0"), (ink, "0.6", "0")])
            rice_bom = bom(rice, "Rice 25 kg, 4-colour", "1000", sack_route,
                           [(fabric, "48", "1.5"), (thread, "0.9", "0"), (ink, "0.8", "0")])
            shifts = [M("manufacturing.Shift").objects.create(code=code, name=f"Shift {code}", starts_at=starts, hours=D("8"))
                      for code, starts in (("A", datetime.time(6)), ("B", datetime.time(14)), ("C", datetime.time(22)))]
            reasons = {code: M("manufacturing.DowntimeReason").objects.create(code=code, name=name, is_planned=planned)
                       for code, name, planned in (("BRK", "Breakdown", False), ("NOMAT", "Waiting for material", False),
                                                   ("PM", "Planned maintenance", True), ("CHG", "Size or print changeover", True))}
            for code, name in (("EDGE", "Edge trim"), ("PRINT", "Misprint"), ("STITCH", "Stitching fault"),
                               ("TAPE", "Tape breaks")):
                M("manufacturing.ScrapReason").objects.create(code=code, name=name)

        with step("Production runs: one closed, one weaving, one waiting, two not released, one to close"):
            WorkOrder, Issue, IssueLine = (M("manufacturing.WorkOrder"), M("manufacturing.MaterialIssue"),
                                           M("manufacturing.MaterialIssueLine"))

            def start_run(it, b, qty, centre, start, hours, release=True):
                w = WorkOrder.objects.create(item=it, bom=b, quantity_ordered=D(qty), uom=it.uom, warehouse=plant,
                                             work_centre=centre, routing=b.routing, scheduled_start=stamp(start, 6),
                                             scheduled_end=stamp(start, 6) + datetime.timedelta(hours=hours))
                if release:
                    w.release(on_date=min(start, TODAY))
                return w

            def issue_all(w, when):
                document = Issue.objects.create(work_order=w, issue_date=when, warehouse=plant)
                for n, component in enumerate(w.components.all(), start=1):
                    IssueLine.objects.create(issue=document, item=component.item, quantity=component.quantity_required,
                                             uom=component.uom, line_number=n)
                document.post()

            def produce(w, when, qty):
                M("manufacturing.ProductionEntry").objects.create(
                    work_order=w, entry_date=when, warehouse=plant, quantity_produced=D(qty), quantity_scrapped=D("0"),
                    uom=w.uom, work_centre=w.work_centre).post()

            closed = start_run(tape, tape_bom, "6000", ext, on(2026, 9, 21), 16)
            issue_all(closed, on(2026, 9, 21))
            produce(closed, on(2026, 9, 22), "5890")
            closed.close(on_date=on(2026, 9, 23))
            weaving = start_run(fabric, fabric_bom, "4000", looms, day(-2), 12)
            issue_all(weaving, day(-2))
            produce(weaving, day(-1), "1650")
            produce(weaving, TODAY, "820")
            start_run(cement, cement_bom, "40000", press, day(1), 10)
            start_run(fert, fert_bom, "20000", press, day(4), 8, release=False)
            start_run(rice, rice_bom, "15000", press, day(6), 6, release=False)
            done = start_run(rice, rice_bom, "10000", conv, day(-4), 6)
            issue_all(done, day(-4))
            produce(done, day(-3), "10000")

        with step("A change order on the cement sack's bill of materials"):
            raise_change(cement_bom, day(14), "Thread to 1,000 denier polyester after the seam complaint", by=admin)

        with step("Stoppages"):
            for code, when, reason, minutes, shift, note in (
                    ("LOOM-03", day(-6), "BRK", 95, 0, "Shuttle wheel bearing seized"),
                    ("EXT-1", day(-5), "CHG", 60, 1, "Changed to the 900 denier die"),
                    ("LOOM-05", day(-3), "NOMAT", 40, 2, "Tape bobbins late from extrusion"),
                    ("PRN-1", day(-2), "CHG", 75, 0, "Cylinder change to the Sahyadri artwork")):
                machine = machines[code]
                M("manufacturing.Downtime").objects.create(work_centre=machine.work_centre, machine=machine, shift_date=when,
                                                           reason=reasons[reason], minutes=minutes, shift=shifts[shift],
                                                           notes=note)

        with step("Quality: alerts, a complaint, corrective actions, instruments"):
            Alert, Action = M("manufacturing.QualityAlert"), M("manufacturing.CorrectiveAction")
            breaks = Alert.objects.create(raised_on=day(-1), raised_by=meena, title="Tape breaks above norm on LOOM-03",
                                          description="Weft breaks 14 an hour against a norm of 5; fabric GSM drifting low.",
                                          severity="high", work_centre=looms, machine=machines["LOOM-03"], item=fabric,
                                          work_order=weaving, quantity_affected=D("180"), owner=suresh)
            Action.objects.create(alert=breaks, kind="containment", description="Hold LOOM-03 fabric for a full GSM check",
                                  owner=meena, due_on=day(1))
            Action.objects.create(alert=breaks, kind="corrective", description="Replace heald wires and shuttle wheels",
                                  owner=vikas, due_on=day(3))
            Alert.objects.create(raised_on=TODAY, raised_by=ganesh, title="Print smudge on cement sacks", severity="medium",
                                 description="Blue smudging on the back panel after stitching.", work_centre=press,
                                 machine=machines["PRN-1"], item=cement, owner=meena)
            Alert.objects.create(raised_on=day(-12), raised_by=meena, title="Bag weight 4 g under on STITCH-2", severity="low",
                                 work_centre=conv, machine=machines["STITCH-2"], item=cement, owner=suresh).close(
                "Cutter set 6 mm short after a blade change; re-set and locked.", by=suresh, on_date=day(-10))
            complaint = M("manufacturing.Complaint").objects.create(
                customer=sahyadri, received_on=day(-9), category="seam", quantity_affected=D("650"),
                description="Bags opening at the bottom seam during filling, about 1 in 400.")
            Action.objects.create(complaint=complaint, kind="corrective", owner=suresh, due_on=day(14),
                                  description="Move to 1,000 denier thread; the change order is raised")
            Action.objects.create(complaint=complaint, kind="preventive", owner=meena, due_on=day(5), done_on=day(-2),
                                  description="Seam pull test every two hours on both lines",
                                  done_note="Added to the shift checklist")
            for code, name, interval, last, result in (("SCALE-01", "Platform scale, 300 kg", 180, day(-150), "pass"),
                                                       ("TENS-01", "Tensile tester, 500 N", 365, day(-30), "adjusted"),
                                                       ("GSM-01", "GSM cutter and balance", 90, day(-95), "pass")):
                instrument = M("quality.Instrument").objects.create(code=code, name=name, interval_days=interval,
                                                                    location="QA lab")
                calibration = M("quality.Calibration").objects.create(
                    instrument=instrument, calibrated_on=last, result=result, performed_by="Precision Cal Labs, Pune",
                    certificate_reference=f"PCL/{last:%y%m}/{code}")
                calibration.post()

        with step("Maintenance: schedules, planned jobs and breakdowns"):
            for code, name, minutes, every in (("EXT-1", "Screen changer and die cleaning", 120, 30),
                                               ("LOOM-01", "Shuttle and cam lubrication", 45, 14),
                                               ("PRN-1", "Anilox roll cleaning", 60, 7)):
                M("manufacturing.MaintenanceSchedule").objects.create(
                    work_centre=machines[code].work_centre, machine=machines[code], name=name, duration_minutes=minutes,
                    every_days=every, last_done_on=day(3 - every))
            for code, due, minutes, fault, done in (("EXT-1", day(3), 120, "", None), ("LOOM-01", day(8), 45, "", None),
                                                    ("PRN-1", day(1), 60, "", None), ("LOOM-04", day(12), 90, "", None),
                                                    ("LOOM-03", day(-1), 60, "Weft breaks, heald wires worn", None),
                                                    ("STITCH-2", day(-8), 30, "Needle bar knocking", day(-8))):
                machine = machines[code]
                # A breakdown rests on the stoppage it caused.
                stoppage = M("manufacturing.Downtime").objects.create(
                    work_centre=machine.work_centre, machine=machine, shift_date=due, reason=reasons["BRK"],
                    minutes=minutes, shift=shifts[0], notes=fault) if fault else None
                job = M("manufacturing.MaintenanceJob").objects.create(
                    work_centre=machine.work_centre, machine=machine, due_on=due, planned_minutes=minutes,
                    is_breakdown=bool(fault), fault=fault, technician=vikas, downtime=stoppage)
                if done:
                    job.complete(on_date=done, cause="Worn needle bar bush", action="Bush replaced")

        with step("Fixed assets in service"):
            machinery = M("assets.AssetCategory").objects.create(
                code="PM", name="Plant and machinery", asset_account=plant_acc, accumulated_account=accumulated,
                expense_account=depreciation, default_life_months=120)
            office = M("assets.AssetCategory").objects.create(
                code="OE", name="Office equipment", asset_account=office_acc, accumulated_account=accumulated,
                expense_account=depreciation, default_life_months=60)
            for name, category, bought, cost, months in (
                    ("Tape line, 120 mm (EXT-1)", machinery, on(2026, 4, 10), "9500000", 120),
                    ("Circular looms, six (LOOM-01 to 06)", machinery, on(2026, 4, 20), "11100000", 120),
                    ("4-colour flexo press (PRN-1)", machinery, on(2026, 5, 2), "3850000", 120),
                    ("Laptops for the office, four", office, on(2026, 6, 15), "260000", 36)):
                asset = M("assets.FixedAsset").objects.create(name=name, category=category, acquisition_date=bought,
                                                              cost=D(cost), life_months=months)
                asset.place_in_service(on_date=bought)
                # Charged to the end of the last month the books step closes, before it closes them:
                # a month left uncharged under a close holds up every asset's month-end run.
                asset.depreciate(through=s.month_span(2026, 7, -1)[1])

        # ----------------------------------------------------------- books
        with step("Journals, cost centres, a budget, the accounting periods and a recurring rent"):
            Entry, Line = M("accounting.JournalEntry"), M("accounting.JournalLine")

            def journal(when, memo, rows):
                entry = Entry.objects.create(date=when, memo=memo)
                for account, debit, credit, centre in rows:
                    Line.objects.create(entry=entry, account=account, debit=D(debit), credit=D(credit), cost_centre=centre)
                entry.post()

            journal(on(2026, 7, 1), "Opening bank balance", [(bank, "4500000", "0", None), (opening_eq, "0", "4500000", None)])
            for month, units in ((7, ("185400", "124300", "28100", "23800")), (8, ("191200", "128900", "29400", "24600")),
                                 (9, ("196700", "131500", "30200", "25100"))):
                total = sum((D(x) for x in units), D("0"))
                journal(on(2026, month, 5), f"MSEDCL power bill, {s.month_span(2026, month)[0]:%B}",
                        [(power, units[0], "0", centres["EXT"]), (power, units[1], "0", centres["LOOM"]),
                         (power, units[2], "0", centres["PRINT"]), (power, units[3], "0", centres["CONV"]),
                         (bank, "0", str(total), None)])
                journal(on(2026, month, 7), "Godown rent", [(rent, "85000", "0", centres["OFFICE"]), (bank, "0", "85000", None)])
                journal(on(2026, month, 28), "Bank charges",
                        [(bank_charges, "1180", "0", centres["OFFICE"]), (bank, "0", "1180", None)])
            journal(on(2026, 9, 12), "Agri expo stall and travel",
                    [(travel, "142000", "0", centres["OFFICE"]), (bank, "0", "142000", None)])
            year_start = datetime.date(TODAY.year if TODAY.month >= 4 else TODAY.year - 1, 4, 1)
            budget = M("accounting.Budget").objects.create(code=f"FY{year_start.year % 100 + 1:02d}",
                                                           name=f"Budget {year_start.year}-{(year_start.year + 1) % 100:02d}",
                                                           start_date=year_start,
                                                           end_date=datetime.date(year_start.year + 1, 3, 31))
            for account, amount, centre in ((power, "8400000", None), (rent, "1020000", centres["OFFICE"]),
                                            (repairs, "900000", None), (travel, "450000", centres["OFFICE"]),
                                            (wages, "9600000", None), (freight, "1800000", None)):
                M("accounting.BudgetLine").objects.create(budget=budget, account=account, amount=D(amount), cost_centre=centre)
            # Monthly periods from three months before the story's first to three after this one; the
            # three before it are closed.
            for offset in range(-3, 7):
                start, end = s.month_span(2026, 7, offset)
                M("accounting.AccountingPeriod").objects.create(name=f"{start:%B %Y}", start_date=start, end_date=end,
                                                                closed=offset < 0)
            rent_due = M("accounting.RecurringJournal").objects.create(code="RENT-GODOWN", memo="Godown rent, Chakan",
                                                                       start_date=TODAY, interval="monthly",
                                                                       next_run_date=TODAY)
            M("accounting.RecurringJournalLine").objects.create(schedule=rent_due, account=rent, debit=D("85000"),
                                                                cost_centre=centres["OFFICE"])
            M("accounting.RecurringJournalLine").objects.create(schedule=rent_due, account=bank, credit=D("85000"))

        with step("Payroll: pay components, salaries, one month posted and one calculated"):
            Component, Pay, PayRun = M("hr.PayComponent"), M("hr.EmployeeCompensation"), M("hr.PayRun")
            basic = Component.objects.create(code="BASIC", name="Basic salary", kind="earning", basis="fixed",
                                             expense_account=wages, sequence=10, reduces_for_unpaid_leave=True)
            hra = Component.objects.create(code="HRA", name="House rent allowance", kind="earning", basis="fixed",
                                           expense_account=wages, sequence=20)
            pf = Component.objects.create(code="PF", name="Provident fund (employee)", kind="deduction", basis="percent",
                                          liability_account=pf_payable, sequence=50)
            pt = Component.objects.create(code="PT", name="Professional tax", kind="deduction", basis="fixed",
                                          liability_account=pt_payable, sequence=60)
            epf = Component.objects.create(code="EPF", name="Provident fund (employer)", kind="employer_cost", basis="percent",
                                           expense_account=employer_pf, liability_account=pf_payable, sequence=70)
            for key, (basic_pay, rent_allowance) in {
                    "rohit": ("62000", "18000"), "imran": ("32000", "9000"), "priya": ("34000", "9500"),
                    "ravi": ("38000", "11000"), "sunita": ("21000", "6000"), "suresh": ("48000", "14000"),
                    "302": ("18500", "5000"), "303": ("17800", "5000"), "304": ("17200", "5000"),
                    "meena": ("29000", "8500"), "vikas": ("26000", "7500"), "anita": ("52000", "15000"),
                    "kiran": ("36000", "10000")}.items():
                for component, amount in ((basic, basic_pay), (hra, rent_allowance), (pf, "12"), (pt, "200"), (epf, "12")):
                    Pay.objects.create(employee=people[key], component=component, amount=D(amount),
                                       effective_from=datetime.date(2026, 4, 1))
            for offset, post in ((-2, True), (-1, False)):
                start, end = s.month_span(2026, 10, offset)  # the story's last month is this one
                run = PayRun.objects.create(name=f"{start:%B %Y}", period_start=start, period_end=end, pay_date=end)
                run.calculate()
                if post:
                    run.post()

        with step("People: expense claims, appraisals, openings and applicants, leave"):
            Claim, ClaimLine = M("hr.ExpenseClaim"), M("hr.ExpenseLine")

            def claim(employee, when, purpose, rows):
                c = Claim.objects.create(employee=employee, claim_date=when, purpose=purpose)
                for spent, account, text, amount in rows:
                    ClaimLine.objects.create(claim=c, spent_on=spent, expense_account=account, description=text,
                                             amount=D(amount))
                return c

            paid = claim(imran, day(-20), "Customer visits, Satara and Kolhapur",
                         [(day(-24), travel, "Fuel, 640 km", "5120"), (day(-23), travel, "Lodging, Kolhapur", "2400")])
            paid.submit()
            paid.approve(by=rohit, note="Visits logged in the CRM")
            paid.pay(bank, on_date=day(-15))
            approved = claim(priya, day(-6), "Agri expo, Nagpur", [(on(2026, 9, 12), travel, "Train fare", "2860"),
                                                                    (on(2026, 9, 13), travel, "Hotel, two nights", "6400")])
            approved.submit()
            approved.approve(by=rohit)
            claim(vikas, day(-1), "Emergency spares run to Bhosari",
                  [(day(-2), repairs, "Heald wires, local purchase", "3450"), (day(-2), travel, "Auto fare", "380")]).submit()
            claim(imran, TODAY, "Sample delivery, Ratnagiri", [(TODAY, travel, "Courier and fuel", "1850")])
            for employee, reviewer, rating, strengths, improve, submit in (
                    (mahesh, suresh, 4, "Keeps the line at 98% uptime; trains new operators.", "Logs changeovers late.", True),
                    (lakshmi, suresh, 3, "Careful with GSM; low waste.", "Needs to run two looms at once.", True),
                    (imran, rohit, 4, "Opened Konkan and Malwa; collections on time.", "Pipeline notes are thin.", False)):
                appraisal = M("hr.Appraisal").objects.create(
                    employee=employee, reviewer=reviewer, period_start=s.month_span(2026, 4)[0],
                    period_end=s.month_span(2026, 9)[1], rating=rating, strengths=strengths, improvements=improve,
                    goals="Hold waste under 2.5% and finish the tape line training.")
                if submit:
                    appraisal.submit()
            opening = M("hr.JobOpening").objects.create(title="Circular loom operator", department=depts["PROD"],
                                                        openings=2, opened_on=day(-21),
                                                        description="Two shifts; experience on 6-shuttle looms.")
            M("hr.JobOpening").objects.create(title="Quality chemist", department=depts["QA"], openings=1,
                                              opened_on=day(-40)).hold()
            stages = ["applied", "screening", "interview", "offered"]
            for name, source, stage, phone in (("Ramesh Gaikwad", "referral", "interview", "98901 22314"),
                                               ("Pooja Shinde", "walk_in", "screening", "97654 10982"),
                                               ("Anil Yadav", "portal", "offered", "99220 45671"),
                                               ("Sachin Lokhande", "agency", "applied", "98812 30457")):
                applicant = M("hr.Applicant").objects.create(opening=opening, name=name, applied_on=day(-15), source=source,
                                                             phone=phone)
                for next_stage in stages[1:stages.index(stage) + 1]:
                    applicant.advance(next_stage)
            M("hr.LeaveRequest").objects.create(employee=lakshmi, leave_type="vacation", start_date=day(5), end_date=day(7),
                                                reason="Family function, Solapur")
            M("hr.LeaveRequest").objects.create(employee=ravi, leave_type="sick", start_date=day(-9), end_date=day(-8),
                                                reason="Fever")

