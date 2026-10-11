"""
python manage.py go_live_check

Is this installation ready for people to use? Each check says what it
found and, when something is missing, what to do. A FAIL is something
that breaks the first day (an invoice that cannot post, a ledger that
does not balance); a WARN is something that will be noticed later (a
login that sees nothing, invoices nobody can email). The command fails
while any FAIL stands, so it can gate a go-live script.

Run it after the import (docs/IMPORT.md) and again on the morning of
go-live. It reads; it changes nothing.
"""

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError

FAIL, WARN, OK = "FAIL", "WARN", "ok"

# Without these, a document of that kind cannot post at all.
REQUIRED_ACCOUNTS = [
    ("default_receivable_account", "receivable", "invoices"),
    ("default_payable_account", "payable", "bills"),
    ("default_bank_account", "bank", "payments"),
    ("default_revenue_account", "revenue", "invoice lines with no account of their own"),
    ("default_inventory_account", "inventory", "stock movements"),
    ("default_cogs_account", "cost of sales", "deliveries"),
    ("grni_account", "goods received not invoiced", "goods receipts"),
]
# Without these, one flow refuses when it is first used.
OPTIONAL_ACCOUNTS = [
    ("customer_deposit_account", "customer advances", "down payments"),
    ("vendor_prepayment_account", "vendor prepayments", "prepayment bills"),
    ("fx_gain_account", "exchange gain", "foreign-currency settlements"),
    ("fx_loss_account", "exchange loss", "foreign-currency settlements"),
    ("settlement_discount_account", "discount allowed", "early-payment discounts"),
    ("bad_debt_account", "bad debts", "write-offs"),
    ("purchase_price_variance_account", "price variance", "returns at a changed cost"),
    ("net_pay_account", "net pay", "pay runs"),
]


def checks():
    """[(level, what, detail)]."""
    from apps.core.management.commands.setup_roles import ROLES
    from apps.core.models import Company
    from apps.inventory.models import Warehouse

    found = []

    def add(level, what, detail=""):
        found.append((level, what, detail))

    company = Company.objects.first()
    if company is None:
        add(FAIL, "Company", "No company profile. Create it in the admin (Core > Company).")
        return found
    add(OK, "Company", company.name)
    if company.base_currency_id is None:
        add(FAIL, "Base currency", "Set the company's base currency.")

    for field, name, used_by in REQUIRED_ACCOUNTS:
        if getattr(company, f"{field}_id") is None:
            add(FAIL, f"Account: {name}", f"Not set on the company; {used_by} cannot post.")
    for field, name, used_by in OPTIONAL_ACCOUNTS:
        if getattr(company, f"{field}_id") is None:
            add(WARN, f"Account: {name}", f"Not set; {used_by} will be refused until it is.")

    if not Warehouse.objects.exists():
        add(FAIL, "Warehouses", "None. Make at least one before anything is received.")

    missing_roles = sorted(set(ROLES) - set(Group.objects.values_list("name", flat=True)))
    if missing_roles:
        add(FAIL, "Roles", f"Missing {', '.join(missing_roles)}: run python manage.py setup_roles.")
    else:
        add(OK, "Roles", f"{len(ROLES)} set up")

    _gst(add)
    _people(add)
    _books(add)
    _settings(add)
    return found


def _gst(add):
    from apps.accounting.gst import GstSettings
    from apps.sales.models import SalesOrder

    gst = GstSettings.active()
    if gst is None:
        add(WARN, "GST", "No active registration: no invoice will carry GST and no return compiles.")
        return
    add(OK, "GST", gst.gstin)
    if not gst.job_work_sac and SalesOrder.objects.filter(is_job_work=True).exists():
        add(FAIL, "GST: job-work SAC", "Job-work orders exist and the SAC for conversion is not set; "
            "their invoices will be refused.")


def _people(add):
    from apps.hr.models import Employee

    users = User.objects.filter(is_active=True, is_superuser=False)
    roleless = sorted(users.filter(groups__isnull=True).values_list("username", flat=True))
    if roleless:
        add(WARN, "Logins with no role", f"{', '.join(roleless)}: they sign in to an empty screen.")
    unlinked = sorted(users.filter(groups__isnull=False, employee__isnull=True)
                      .distinct().values_list("username", flat=True))
    if unlinked:
        add(WARN, "Logins not linked to an employee",
            f"{', '.join(unlinked)}: they see no leave and decide none. Set the employee's login.")
    nobody = sorted(Employee.objects.filter(
        employment_status="active", manager__isnull=True, department__manager__isnull=True,
    ).values_list("employee_number", flat=True))
    if nobody:
        add(WARN, "Employees with no manager",
            f"{', '.join(nobody)}: HR decides their leave until a manager is set.")
    if not User.objects.filter(is_active=True, is_superuser=True).exists():
        add(WARN, "Administrator", "No active superuser: nobody can open the admin.")
    _reps(add, users)


def _reps(add, users):
    """Reps see their own customers only: one who is nobody's rep sees none."""
    from apps.sales.models import CustomerProfile, SalesRep
    from apps.sales.scoping import UNLIMITED, rep_limit

    limited = [user for user in users.filter(groups__name="Sales Rep").distinct()
               if rep_limit(user) is not UNLIMITED]
    if not limited:
        return
    lost = sorted(user.username for user in limited if not SalesRep.objects.filter(
        party__employee_profile__user=user, is_active=True).exists())
    if lost:
        add(WARN, "Sales reps who carry nobody",
            f"{', '.join(lost)}: no active sales rep record on their employee, so they see "
            "no customer and can make none.")
    from apps.core.models import Party, PartyRole

    loose = Party.objects.filter(role_assignments__role=PartyRole.CUSTOMER, is_active=True).exclude(
        pk__in=CustomerProfile.objects.filter(sales_rep__isnull=False).values("party"))
    if loose.exists():
        add(WARN, "Customers with no rep",
            f"{loose.count()} customer(s) are nobody's: no rep sees them until one is set "
            "on the customer's sales terms.")


def _making(add):
    from apps.inventory.models import Item
    from apps.manufacturing.bom import BillOfMaterials

    made = set(BillOfMaterials.objects.values_list("item_id", flat=True))
    without = sorted(Item.objects.filter(stock_class__in=("finished", "semi_finished"), is_active=True)
                     .exclude(pk__in=made).values_list("sku", flat=True))
    if without:
        add(WARN, "Items made here with no recipe",
            f"{', '.join(without[:12])}{', …' if len(without) > 12 else ''}: nothing can be planned or "
            "costed for them until their specification is in (bag_specs, fabric_specs, tape_specs) or a "
            "bill of materials is typed.")


def _books(add):
    # The same probes the Health screen runs every day after go-live.
    from apps.web.health import integrity

    for finding in integrity(fresh=True)["findings"]:
        if finding["ok"] is True:
            add(OK, finding["label"], finding["detail"])
        elif finding["ok"] is None:
            add(WARN, finding["label"], finding["detail"])
        else:
            add(FAIL, finding["label"], finding["detail"] + " Stop and find out why before going live.")


def _settings(add):
    production = getattr(settings, "PRODUCTION", False)
    if production and settings.DEBUG:
        add(FAIL, "Settings: DEBUG", "On in production: every error page shows the settings.")
    if settings.SECRET_KEY.startswith("django-insecure") and production:
        add(FAIL, "Settings: secret key", "The development key: set DJANGO_SECRET_KEY.")
    if settings.TIME_ZONE != "Asia/Kolkata":
        add(WARN, "Settings: time zone",
            f"{settings.TIME_ZONE}: the plant's day, shifts and backups run on this clock.")
    if production and not getattr(settings, "TRUSTED_PROXIES", 0):
        add(WARN, "Settings: proxy", "DJANGO_TRUSTED_PROXIES is 0: behind a proxy, every sign-in "
            "looks as if it came from the proxy and one address's lock is everybody's.")
    if not getattr(settings, "EMAIL_HOST", "") or settings.EMAIL_HOST == "localhost":
        add(WARN, "Email", "No mail server: invoices, statements and reminders cannot be emailed.")


class Command(BaseCommand):
    help = "Check this installation is ready for people to use. Reads; changes nothing."

    def handle(self, *args, **options):
        found = checks()
        for level, what, detail in found:
            line = f"{level:4}  {what}" + (f": {detail}" if detail else "")
            style = {FAIL: self.style.ERROR, WARN: self.style.WARNING}.get(level, str)
            self.stdout.write(style(line))
        failures = sum(1 for level, *_ in found if level == FAIL)
        warnings = sum(1 for level, *_ in found if level == WARN)
        if failures:
            raise CommandError(f"Not ready: {failures} failure(s), {warnings} warning(s).")
        self.stdout.write(self.style.SUCCESS(f"Ready, with {warnings} warning(s) to read."))
