"""
Bringing the old system's records in, from CSV files, once, at go-live.

Six kinds, in the order they depend on each other: parties, items, then
the opening position (stock on hand, invoices still owed, bills still
owing, and every other balance on the trial balance). docs/IMPORT.md
gives each file's columns.

Every row is built through the models, so it meets the rules a record
typed in would: a GSTIN is checked, a unit of measure must exist, an
opening invoice posts its journal entry. A file goes in whole or not at
all. Nothing is kept unless every row is clean and the run is told to
commit; a dry run (the default) reports every problem by row and column
and leaves the database as it found it.

The opening position is brought in as documents, not as bare balances,
so it ages, settles and reconciles like anything else: an open invoice
is an invoice against the opening-balance account, stock is a posted
adjustment. Receivables, payables and stock may therefore not appear in
the balances file; their control accounts are refused there.
"""

import csv
import datetime
import io
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

KINDS = ("parties", "employees", "customer_reps", "items", "opening_stock", "open_invoices",
         "open_bills", "opening_balances")


# Each kind's columns, in the order a template gives them; docs/IMPORT.md
# says which are required and what each takes.
COLUMNS = {
    "parties": ["code", "name", "roles", "legal_name", "email", "phone", "currency", "payment_terms",
                "gstin", "gst_state", "gst_registration", "credit_limit", "address_line1",
                "address_line2", "city", "state", "postal_code", "country"],
    "employees": ["employee_number", "name", "party_code", "hire_date", "department", "manager",
                  "job_title", "email", "username", "roles", "sales_rep"],
    "customer_reps": ["customer", "rep"],
    "items": ["sku", "name", "uom", "item_type", "hsn_code", "track_inventory", "costing_method",
              "tracking", "sale_price", "standard_cost"],
    "opening_stock": ["sku", "warehouse", "quantity", "unit_cost", "lot"],
    "open_invoices": ["customer", "reference", "date", "amount", "payment_terms", "currency"],
    "open_bills": ["vendor", "reference", "date", "amount", "payment_terms", "currency"],
    "opening_balances": ["account", "debit", "credit", "description"],
}


def template(kind):
    """The header row of a blank file of `kind`."""
    return ",".join(COLUMNS[kind]) + "\n"


@dataclass
class Report:
    kind: str
    rows: int = 0
    created: int = 0
    errors: list = field(default_factory=list)  # (row number, column, message)
    committed: bool = False
    # (user name, first password) for each login an employees file made:
    # handed out once, and changed at the first sign-in.
    passwords: list = field(default_factory=list)

    def refuse(self, row, column, message):
        self.errors.append((row, column, str(message)))


class RowError(Exception):
    def __init__(self, column, message):
        super().__init__(message)
        self.column, self.message = column, message


def read(text):
    """Rows of a CSV, as dicts with trimmed keys and values; the header names the columns."""
    if text.startswith("﻿"):  # saved from a spreadsheet
        text = text[1:]
    reader = csv.DictReader(io.StringIO(text))
    return [
        {(key or "").strip().lower(): (value or "").strip() for key, value in row.items()}
        for row in reader
    ]


# -- reading a cell ------------------------------------------------------


def required(row, column):
    value = row.get(column, "")
    if not value:
        raise RowError(column, "is required.")
    return value


def decimal(row, column, places=None, required_=False):
    value = row.get(column, "")
    if not value:
        if required_:
            raise RowError(column, "is required.")
        return None
    try:
        number = Decimal(value.replace(",", ""))
    except InvalidOperation:
        raise RowError(column, f"{value!r} is not a number.") from None
    if not number.is_finite():
        raise RowError(column, f"{value!r} is not a number.")
    if places is not None and number != number.quantize(Decimal(1).scaleb(-places)):
        raise RowError(column, f"{value!r} has more than {places} decimal places.")
    return number


def date(row, column, required_=True):
    value = row.get(column, "")
    if not value:
        if required_:
            raise RowError(column, "is required.")
        return None
    for shape in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(value, shape).date()
        except ValueError:
            continue
    raise RowError(column, f"{value!r} is not a date; give it as YYYY-MM-DD or DD-MM-YYYY.")


def yes_no(row, column, default):
    value = row.get(column, "").lower()
    if not value:
        return default
    if value in ("yes", "y", "true", "1"):
        return True
    if value in ("no", "n", "false", "0"):
        return False
    raise RowError(column, f"{value!r} is not yes or no.")


def by_code(model, row, column, field_name="code", required_=True, **extra):
    value = row.get(column, "")
    if not value:
        if required_:
            raise RowError(column, "is required.")
        return None
    found = model.objects.filter(**{field_name: value}, **extra).first()
    if found is None:
        raise RowError(column, f"no {model._meta.verbose_name} {value!r}.")
    return found


def clean(instance, columns=None):
    """full_clean(), its complaints put on the columns they came from."""
    try:
        instance.full_clean()
    except ValidationError as error:
        if hasattr(error, "error_dict"):
            name, messages = next(iter(error.error_dict.items()))
            column = (columns or {}).get(name, name if name != "__all__" else "")
            raise RowError(column, " ".join(m for e in messages for m in e.messages)) from None
        raise RowError("", " ".join(error.messages)) from None


# -- the kinds -----------------------------------------------------------


def _party(row, options):
    from apps.accounting.models import PartyTaxProfile
    from apps.core.models import (
        Address,
        AddressType,
        Country,
        Currency,
        Party,
        PartyRole,
        PartyRoleAssignment,
        PaymentTerms,
    )
    from apps.sales.models import CustomerProfile

    code = required(row, "code")
    if Party.objects.filter(code=code).exists():
        raise RowError("code", f"{code!r} already exists.")
    roles = [role.strip().lower() for role in required(row, "roles").replace(",", ";").split(";") if role.strip()]
    allowed = {PartyRole.CUSTOMER, PartyRole.VENDOR, PartyRole.EMPLOYEE, PartyRole.OTHER}
    for role in roles:
        if role not in allowed:
            raise RowError("roles", f"{role!r} is not one of {', '.join(sorted(allowed))}.")
    party = Party(
        code=code, name=required(row, "name"), legal_name=row.get("legal_name", ""),
        email=row.get("email", ""), phone=row.get("phone", ""),
        default_currency=by_code(Currency, row, "currency", required_=False),
        payment_terms=by_code(PaymentTerms, row, "payment_terms", required_=False),
    )
    clean(party)
    party.save()
    for role in roles:
        PartyRoleAssignment.objects.create(party=party, role=role)
    if row.get("gstin") or row.get("gst_state") or row.get("gst_registration"):
        profile = PartyTaxProfile(
            party=party, gstin=row.get("gstin", "").upper(), gst_state=row.get("gst_state", ""),
            gst_registration=row.get("gst_registration", "").lower(),
        )
        # Its checks are of the GSTIN and the state it names.
        clean(profile, {"__all__": "gstin"})
        profile.save()
    limit = decimal(row, "credit_limit", places=2)
    if limit is not None:
        if PartyRole.CUSTOMER not in roles:
            raise RowError("credit_limit", "is a customer's; this party is not one.")
        profile = CustomerProfile(party=party, credit_limit=limit)
        clean(profile)
        profile.save()
    if row.get("address_line1"):
        address = Address(
            party=party, address_type=AddressType.BILLING, line1=row["address_line1"],
            line2=row.get("address_line2", ""), city=required(row, "city"),
            state=row.get("state", ""), postal_code=row.get("postal_code", ""),
            country=by_code(Country, row, "country", required_=False),
        )
        clean(address, {"line1": "address_line1", "line2": "address_line2"})
        address.save()


def _item(row, options):
    from apps.core.models import UnitOfMeasure
    from apps.inventory.models import Item

    sku = required(row, "sku")
    if Item.objects.filter(sku=sku).exists():
        raise RowError("sku", f"{sku!r} already exists.")
    item = Item(
        sku=sku, name=required(row, "name"),
        uom=by_code(UnitOfMeasure, row, "uom"),
        hsn_code=row.get("hsn_code", ""),
        track_inventory=yes_no(row, "track_inventory", True),
        sale_price=decimal(row, "sale_price", places=4),
        standard_cost=decimal(row, "standard_cost", places=4),
    )
    for column in ("item_type", "costing_method", "tracking"):
        if row.get(column):
            setattr(item, column, row[column].lower())
    clean(item)
    item.save()


def _employees(rows, report, options):
    """
    The people, each an employee of a party with the employee role, with
    their department, their manager and their login. Managers are named by
    employee number and may come later in the file: they are set once
    everyone is in. A login is linked to its employee from the start, so
    leave and self service work on the first day; a new one gets a random
    first password, reported once, never shown on the screen.
    """
    import secrets

    from django.contrib.auth.models import Group, User

    from apps.core.models import Party, PartyRole, PartyRoleAssignment
    from apps.hr.models import Department, Employee

    made = {}
    managers = []
    for number, row in rows:
        try:
            with transaction.atomic():
                employee_number = required(row, "employee_number")
                if Employee.objects.filter(employee_number=employee_number).exists():
                    raise RowError("employee_number", f"{employee_number!r} already exists.")
                code = row.get("party_code") or employee_number
                party = Party.objects.filter(code=code).first()
                if party is None:
                    party = Party(code=code, name=required(row, "name"), email=row.get("email", ""))
                    clean(party, {"code": "party_code"})
                    party.save()
                elif hasattr(party, "employee_profile"):
                    raise RowError("party_code", f"{code!r} is already an employee.")
                if not party.role_assignments.filter(role=PartyRole.EMPLOYEE).exists():
                    PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
                employee = Employee(
                    party=party, employee_number=employee_number, hire_date=date(row, "hire_date"),
                    department=by_code(Department, row, "department", required_=False),
                    job_title=row.get("job_title", ""),
                )
                clean(employee, {"hire_date": "hire_date"})
                username = row.get("username", "")
                password = None
                if username:
                    user = User.objects.filter(username=username).first()
                    if user is None:
                        password = secrets.token_urlsafe(9)
                        user = User.objects.create_user(username, email=row.get("email", ""),
                                                        password=password,
                                                        first_name=party.name[:150])
                    elif Employee.objects.filter(user=user).exists():
                        raise RowError("username", f"{username!r} is already another employee's login.")
                    names = [name.strip() for name in row.get("roles", "").split(";") if name.strip()]
                    groups = list(Group.objects.filter(name__in=names))
                    missing = sorted(set(names) - {group.name for group in groups})
                    if missing:
                        raise RowError("roles", f"no role {', '.join(missing)} (python manage.py setup_roles makes them).")
                    user.groups.add(*groups)
                    employee.user = user
                elif row.get("roles"):
                    raise RowError("roles", "are a login's; give the username too.")
                employee.save()
                if yes_no(row, "sales_rep", False):
                    from apps.sales.models import SalesRep

                    SalesRep.objects.get_or_create(party=party)
                made[employee_number] = employee
                if password:
                    report.passwords.append((username, password))
                if row.get("manager"):
                    managers.append((number, employee, row["manager"]))
                report.created += 1
        except RowError as error:
            report.refuse(number, error.column, error.message)
        except ValidationError as error:
            report.refuse(number, "", " ".join(error.messages))
    for number, employee, manager_number in managers:
        manager = made.get(manager_number) or Employee.objects.filter(employee_number=manager_number).first()
        if manager is None:
            report.refuse(number, "manager", f"no employee {manager_number!r}, here or already in.")
            continue
        employee.manager = manager
        try:
            with transaction.atomic():
                employee.save()
        except ValidationError as error:
            report.refuse(number, "manager", " ".join(error.messages))


def _opening_stock(rows, report, options):
    """One posted adjustment a warehouse: Dr inventory / Cr the opening-balance account."""
    from apps.inventory.adjustments import AdjustmentReason, StockAdjustment, StockAdjustmentLine
    from apps.inventory.models import Item, Warehouse
    from apps.inventory.tracking import Lot

    reason = AdjustmentReason.objects.filter(code=options["reason"]).first()
    if reason is None:
        report.refuse(0, "--reason", f"no adjustment reason {options['reason']!r}: make one, "
                      "posting to the opening-balance account, first.")
        return
    on = options["date"]
    by_warehouse = {}
    for number, row in rows:
        try:
            with transaction.atomic():
                item = by_code(Item, row, "sku", field_name="sku")
                warehouse = by_code(Warehouse, row, "warehouse")
                quantity = decimal(row, "quantity", places=6, required_=True)
                if quantity <= 0:
                    raise RowError("quantity", "must be above nothing.")
                cost = decimal(row, "unit_cost", places=8, required_=True)
                if cost < 0:
                    raise RowError("unit_cost", "cannot be below nothing.")
                lot = None
                if row.get("lot"):
                    lot, _ = Lot.objects.get_or_create(item=item, code=row["lot"])
                if warehouse.pk not in by_warehouse:
                    by_warehouse[warehouse.pk] = StockAdjustment.objects.create(
                        adjustment_date=on, warehouse=warehouse, reason=reason,
                        memo=options.get("memo") or "Opening stock")
                line = StockAdjustmentLine(
                    adjustment=by_warehouse[warehouse.pk], item=item, uom=item.uom,
                    quantity=quantity, unit_cost=cost, lot=lot)
                clean(line, {"unit_cost": "unit_cost", "lot": "lot"})
                line.save()
                report.created += 1
        except RowError as error:
            report.refuse(number, error.column, error.message)
        except ValidationError as error:
            report.refuse(number, "", " ".join(error.messages))
    if report.errors:
        return
    for adjustment in by_warehouse.values():
        try:
            adjustment.post()
        except ValidationError as error:
            report.refuse(0, "", f"{adjustment.warehouse}: " + " ".join(error.messages))


def _open_document(row, options, side):
    """An invoice still owed, or a bill still owing, against the opening-balance account."""
    from apps.accounting.models import Account
    from apps.core.models import Company, Currency, Party, PaymentTerms

    company = Company.get()
    against = Account.objects.filter(code=options["against"]).first()
    if against is None:
        raise RowError("--against", f"no account {options['against']!r}.")
    role = "customer" if side == "sales" else "vendor"
    party = by_code(Party, row, role, role_assignments__role=role)
    amount = decimal(row, "amount", places=2, required_=True)
    if amount <= 0:
        raise RowError("amount", "must be above nothing; enter a credit as a credit note.")
    on = date(row, "date")
    terms = by_code(PaymentTerms, row, "payment_terms", required_=False) or party.payment_terms
    currency = by_code(Currency, row, "currency", required_=False) or party.default_currency
    reference = required(row, "reference")
    memo = f"Opening balance: {reference}"
    if side == "sales":
        from apps.sales.models import Invoice, InvoiceLine

        account = company.default_receivable_account
        if account is None:
            raise RowError("", "the company has no default receivable account.")
        if Invoice.objects.filter(customer=party, reference=reference).exists():
            raise RowError("reference", f"{party.code} already has an invoice {reference!r}.")
        document = Invoice.objects.create(
            customer=party, invoice_date=on, reference=reference, receivable_account=account,
            currency=currency, payment_terms=terms, is_opening_balance=True)
        InvoiceLine.objects.create(invoice=document, description=memo, quantity=Decimal("1"),
                                   unit_price=amount, revenue_account=against)
    else:
        from apps.purchasing.models import Bill, BillLine

        account = company.default_payable_account
        if account is None:
            raise RowError("", "the company has no default payable account.")
        if Bill.objects.filter(vendor=party, reference=reference).exists():
            raise RowError("reference", f"{party.code} already has a bill {reference!r}.")
        document = Bill.objects.create(
            vendor=party, bill_date=on, reference=reference, payable_account=account,
            currency=currency, payment_terms=terms, is_opening_balance=True)
        BillLine.objects.create(bill=document, description=memo, quantity=Decimal("1"),
                                unit_price=amount, expense_account=against)
    document.post(memo=memo) if side == "sales" else document.post(memo=memo, apply_prepayments=False)


def _opening_balances(rows, report, options):
    """One posted journal entry; it must balance, and leave the documented accounts alone."""
    from apps.accounting.models import Account, JournalEntry, JournalLine
    from apps.core.models import Company

    company = Company.get()
    documented = {
        account.pk: why for account, why in (
            (company.default_receivable_account, "open invoices"),
            (company.default_payable_account, "open bills"),
            (company.default_inventory_account, "opening stock"),
            (company.grni_account, "receipts and bills"),
        ) if account is not None
    }
    entry = JournalEntry.objects.create(date=options["date"], memo=options.get("memo") or "Opening balances")
    debits = credits = Decimal("0")
    for number, row in rows:
        try:
            with transaction.atomic():
                account = by_code(Account, row, "account")
                if account.pk in documented:
                    raise RowError("account", f"{account.code} is brought in as "
                                   f"{documented[account.pk]}, not as a balance.")
                debit = decimal(row, "debit", places=2) or Decimal("0")
                credit = decimal(row, "credit", places=2) or Decimal("0")
                if debit < 0 or credit < 0 or (debit and credit) or not (debit or credit):
                    raise RowError("debit", "give one of debit or credit, above nothing.")
                JournalLine.objects.create(entry=entry, account=account, debit=debit, credit=credit,
                                           description=row.get("description", "")[:255])
                debits += debit
                credits += credit
                report.created += 1
        except RowError as error:
            report.refuse(number, error.column, error.message)
    if report.errors:
        return
    if debits != credits:
        report.refuse(0, "", f"debits come to {debits} and credits to {credits}; "
                      "the opening balances must balance.")
        return
    try:
        entry.post()
    except ValidationError as error:
        report.refuse(0, "", " ".join(error.messages))


def _customer_rep(row, options):
    """Whose customer a party is: a rep sees their own and nobody else's."""
    from apps.core.models import Party, PartyRole
    from apps.hr.models import Employee
    from apps.sales.models import CustomerProfile, SalesRep

    customer = by_code(Party, row, "customer", role_assignments__role=PartyRole.CUSTOMER)
    employee = by_code(Employee, row, "rep", field_name="employee_number")
    if not SalesRep.objects.filter(party=employee.party, is_active=True).exists():
        raise RowError("rep", f"{employee.employee_number} is not a sales rep (sales_rep yes "
                              "in the employees file).")
    profile = CustomerProfile.objects.filter(party=customer).first() or CustomerProfile(party=customer)
    profile.sales_rep = employee.party
    profile.save()


PER_ROW = {
    "parties": _party,
    "items": _item,
    "customer_reps": _customer_rep,
    "open_invoices": lambda row, options: _open_document(row, options, "sales"),
    "open_bills": lambda row, options: _open_document(row, options, "purchasing"),
}
WHOLE_FILE = {"employees": _employees, "opening_stock": _opening_stock,
              "opening_balances": _opening_balances}


def run(kind, text, *, commit=False, **options):
    """Bring in one file of `kind`; a Report of what it did or would do."""
    if kind not in KINDS:
        raise ValueError(f"{kind!r} is not one of {', '.join(KINDS)}.")
    report = Report(kind=kind)
    rows = list(enumerate(read(text), start=2))  # row 1 is the header
    report.rows = len(rows)
    with transaction.atomic():
        if kind in PER_ROW:
            for number, row in rows:
                try:
                    with transaction.atomic():
                        PER_ROW[kind](row, options)
                        report.created += 1
                except RowError as error:
                    report.refuse(number, error.column, error.message)
                except ValidationError as error:
                    report.refuse(number, "", " ".join(error.messages))
        else:
            WHOLE_FILE[kind](rows, report, options)
        if report.errors or not commit:
            transaction.set_rollback(True)
            report.passwords = []  # nothing was kept, so no login has them
        else:
            report.committed = True
    return report
