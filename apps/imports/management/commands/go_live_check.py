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

    # Only this block reads the company. With none, it says so once and every other check is still
    # asked: an installer mended one line per run while the check stopped here (O422).
    company = Company.objects.first()
    if company is None:
        add(FAIL, "Company", "No company yet: python manage.py setup_indian_books makes it ready to post "
            "(rupees, the year from April, the default accounts); then name it under Settings > Company.")
    else:
        _company(add, company)

    if not Warehouse.objects.exists():
        add(FAIL, "Warehouses", "None. Make at least one before anything is received.")
    active = Warehouse.receiving_to_stock()
    if active.count() >= 2 and Warehouse.default_receiving() is None:
        # O415: a purchase or requisition line naming no warehouse belongs to the default receiving warehouse
        # only; with none, it is planned nowhere, and before that it was counted in every one.
        add(FAIL, "Default receiving warehouse",
            f"{active.count()} active warehouses receive to stock and none receives by default, so a purchase or requisition line "
            "naming no warehouse is planned in none of them: mark one under Stores > Warehouses.")

    # O403: a fresh plant had neither, and nothing said so until a count could not be booked.
    from apps.core.models import Country
    from apps.inventory.adjustments import AdjustmentReason

    if not AdjustmentReason.objects.filter(is_active=True).exists():
        add(FAIL, "Stock adjustment reasons", "None, so no adjustment and no count's difference can be booked: "
            "run python manage.py setup_stores, or make them under Stores > Configuration > Adjustment reasons.")
    if not Country.objects.exists():
        add(WARN, "Countries", "None, so no export customer or overseas vendor can be given one: "
            "python manage.py setup_indian_books adds them.")
    _customs(add)

    missing_roles = sorted(set(ROLES) - set(Group.objects.values_list("name", flat=True)))
    if missing_roles:
        add(FAIL, "Roles", f"Missing {', '.join(missing_roles)}: run python manage.py setup_roles.")
    else:
        add(OK, "Roles", f"{len(ROLES)} set up")

    _gst(add)
    _people(add)
    _statutory(add)
    _making(add)
    _books(add)
    _settings(add)
    return found


def _company(add, company):
    add(OK, "Company", company.name)
    if company.base_currency_id is None:
        add(FAIL, "Base currency", "Set the company's base currency.")

    for field, name, used_by in REQUIRED_ACCOUNTS:
        if getattr(company, f"{field}_id") is None:
            add(FAIL, f"Account: {name}", f"Not set on the company; {used_by} cannot post.")
    for field, name, used_by in OPTIONAL_ACCOUNTS:
        if getattr(company, f"{field}_id") is None:
            add(WARN, f"Account: {name}", f"Not set; {used_by} will be refused until it is.")


def _customs(add):
    """The freight and insurance a bill of entry takes when the actuals are unknown: loaded unconfirmed (L21)."""
    from django.utils import timezone

    from apps.purchasing.imports import CustomsValuationDefault

    standing = CustomsValuationDefault.objects.filter(valid_from__lte=timezone.localdate()).order_by(
        "-valid_from").first()
    if standing is not None and not standing.confirmed:
        add(WARN, "Customs valuation defaults",
            f"Freight {standing.freight_percent.normalize():f}% and insurance "
            f"{standing.insurance_percent.normalize():f}% of FOB, from {standing.valid_from:%d %b %Y}, are not "
            "confirmed: a bill of entry with no actual figures takes them. Have the customs broker confirm or "
            "correct them under Purchasing > Configuration > Customs valuation defaults.")


def _gst(add):
    from apps.accounting.gst import GstSettings
    from apps.accounting.models import Tax
    from apps.sales.models import SalesOrder

    # Every reverse-charge tax names where its credit waits until paid in cash (review_rcm F2): a bill of one
    # that does not is refused.
    waiting = sorted(Tax.objects.filter(reverse_charge=True, reverse_charge_waiting_account__isnull=True)
                     .values_list("code", flat=True))
    if waiting:
        add(FAIL, "Reverse charge: credit waiting account",
            f"{', '.join(waiting)} name no account their credit waits in; their bills will be refused. The "
            "Controller sets it in accounting setup (Settings > Taxes), or run setup_indian_books.")

    gst = GstSettings.active()
    if gst is None:
        add(WARN, "GST", "No active registration: no invoice will carry GST and no return compiles.")
        return
    add(OK, "GST", gst.gstin)
    from apps.inventory.models import Item

    # O467: an item that declares no tax is read as bearing none, and every line of it goes out with no GST
    # unless the line names one; the items import set none at all until it had the columns.
    bare = Item.objects.filter(item_type="goods", is_active=True, sales_taxes=None, purchase_taxes=None)
    if bare.exists():
        skus = list(bare.order_by("sku").values_list("sku", flat=True)[:6])
        add(WARN, "Items with no taxes",
            f"{bare.count()} goods item(s) declare no sales or purchase tax ({', '.join(skus)}"
            f"{', ...' if bare.count() > len(skus) else ''}): every order, invoice and bill line of them goes "
            "out with no GST unless the line names one. A supply that truly bears none (diesel) is right to; "
            "set the rest on each item, or bring items in with their sales_taxes and purchase_taxes.")
    from apps.accounting.gst import interstate_position_faults

    faults = interstate_position_faults(gst.interstate_position)
    if faults:
        # A rate added after the position was chosen is charged CGST and SGST across a state line until mapped.
        add(FAIL, "GST: inter-state position", f"{gst.interstate_position} maps {', '.join(faults[:4])}: a supply "
            "across a state line would be charged central and state tax. Map each CGST to the IGST at the whole "
            "rate and each SGST to no tax (Settings > Fiscal positions), or run setup_indian_books.")
    if not gst.job_work_sac and SalesOrder.objects.filter(is_job_work=True).exists():
        add(FAIL, "GST: job-work SAC", "Job-work orders exist and the SAC for conversion is not set; "
            "their invoices will be refused.")


def _statutory(add):
    """A line per statutory engine the plant uses: a confirmed figure must cover today, or it fails."""
    from django.utils import timezone

    from apps.hr.statutory_check import statutory_gaps

    # What happens meanwhile, said as it is. "Nothing under it can post" was said of every engine, and a pay
    # run posted with no minimum wage loaded: the minimum wage is read by the bonus alone, no wage is checked
    # against it yet (gap H2, L33), and PF and overtime work at the code's own figures until a row exists.
    meanwhile = {
        "Minimum wage": "until then the bonus cannot be worked out, and no wage is checked against the "
                        "minimum (that check is not built yet)",
        "PF": "with none loaded a pay run works at the code's own wage ceilings, which may not be the law; "
              "loaded and not confirmed, it is refused",
        "Overtime": "with none loaded overtime is paid at the code's own multiple, which may not be the law; "
                    "loaded and not confirmed, it is refused",
    }
    for engine, used, missing in statutory_gaps(timezone.localdate()):
        if not used:
            continue
        if missing:
            add(FAIL, f"Statutory: {engine}",
                f"No confirmed figure covers today for {', '.join(missing)}. Load them (python manage.py "
                "setup_statutory) and have the Controller confirm each; "
                + meanwhile.get(engine, "until then what reads it is refused") + ".")
        else:
            add(OK, f"Statutory: {engine}", "confirmed figures cover today")


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
    # O461: where a schedule has bands for women, a person whose gender is not said takes the general ones.
    from apps.hr.payroll import PayComponentSlab

    states = set(PayComponentSlab.objects.filter(for_women=True).values_list("state", flat=True))
    unsaid = sorted(Employee.objects.filter(employment_status="active", gender="", work_state__in=states)
                    .values_list("employee_number", flat=True))
    if unsaid:
        add(WARN, "Employees with no gender said",
            f"{', '.join(unsaid[:10])}{', ...' if len(unsaid) > 10 else ''}: they work in "
            f"{', '.join(sorted(states))}, whose professional tax has bands for women, and are taxed at the "
            "general ones. Say it on each employee's page.")
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
