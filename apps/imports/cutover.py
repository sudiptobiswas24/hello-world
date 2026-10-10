"""
The rest of the old system, at go-live: what the plant makes (its tape,
fabric, bag, film and liner specifications, each building its bill of
materials and inspection plan as it is saved), the order book (open sales
and purchase orders, confirmed as they come in), the asset register with
what was already depreciated, salary structures, and the prices agreed
with customers and vendors.

Same rules as importer.py: every row goes through the model, a file is
kept whole or not at all, and a dry run says what would happen. A
specification's columns are its model's fields, read off the model, so
the template and the record cannot drift apart.
"""

import calendar
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction

from apps.core.models import lock_rows

from .base import RowError, by_code, clean, codes, date, decimal, required, whole_number, yes_no

SPEC_MODELS = {
    "tape_specs": ("manufacturing", "TapeSpecification"),
    "fabric_specs": ("manufacturing", "FabricSpecification"),
    "bag_specs": ("manufacturing", "BagSpecification"),
    "film_specs": ("manufacturing", "FilmSpecification"),
    "liner_specs": ("manufacturing", "LinerSpecification"),
}
# Written by the system as the specification is saved, or not at import.
NOT_COLUMNS = {"bom", "inspection_plan", "is_active"}

ABSENT = object()


def spec_model(kind):
    from django.apps import apps

    return apps.get_model(*SPEC_MODELS[kind])


def _spec_fields(model):
    """The code and name first, the dated window last, the rest as the model declares them."""
    order = {"code": 0, "name": 1, "valid_from": 3, "valid_to": 3}
    fields = [f for f in model._meta.fields if f.editable and not f.primary_key and f.name not in NOT_COLUMNS]
    return sorted(fields, key=lambda f: order.get(f.name, 2))


def cutover_columns(kind):
    if kind in SPEC_MODELS:
        return [f.name for f in _spec_fields(spec_model(kind))]
    return COLUMNS[kind]


def _cell(row, field):
    """One cell read as its field wants it, or ABSENT when empty and allowed to be."""
    column = field.name
    value = row.get(column, "")
    if value == "":
        if field.null or field.blank or field.has_default():
            return ABSENT
        raise RowError(column, "is required.")
    if isinstance(field, models.ForeignKey):
        target = field.remote_field.model
        key = "sku" if target._meta.label == "inventory.Item" else "code"
        found = target.objects.filter(**{key: value}).first()
        if found is None:
            raise RowError(column, f"no {target._meta.verbose_name} {value!r}.")
        return found
    if isinstance(field, models.BooleanField):
        return yes_no(row, column, bool(field.get_default()))
    if isinstance(field, models.DecimalField):
        return decimal(row, column, places=field.decimal_places, required_=True)
    if isinstance(field, models.IntegerField):  # the positive kinds too
        return whole_number(row, column, required_=True)
    if isinstance(field, models.DateField):
        return date(row, column)
    if field.choices:
        keys = [str(key) for key, _ in field.choices]
        if value.lower() not in keys:
            raise RowError(column, f"{value!r} is not one of {', '.join(keys)}.")
        return value.lower()
    return value


def _spec(kind):
    def handler(row, options):
        model = spec_model(kind)
        code = required(row, "code")
        if model.objects.filter(code=code).exists():
            raise RowError("code", f"{code!r} already exists.")
        instance = model()
        for field in _spec_fields(model):
            value = _cell(row, field)
            if value is not ABSENT:
                setattr(instance, field.name, value)
        clean(instance)
        instance.save()  # builds the bill of materials and the inspection plan

    return handler


# -- the order book ------------------------------------------------------


def _open_orders(side):
    """Open orders, one per party and reference across their rows, confirmed once all are in."""

    def handler(rows, report, options):
        from apps.accounting.models import Tax
        from apps.core.models import Currency, Party, PaymentTerms, UnitOfMeasure
        from apps.inventory.models import Item, Warehouse

        if side == "sales":
            from apps.sales.models import SalesOrder as Order
            from apps.sales.models import SalesOrderLine as Line

            who, when = "customer", "delivery_date"
        else:
            from apps.purchasing.models import PurchaseOrder as Order
            from apps.purchasing.models import PurchaseOrderLine as Line

            who, when = "vendor", "expected_date"
        orders = {}  # (party, reference) -> (first row, order)
        for number, row in rows:
            try:
                with transaction.atomic():
                    party = by_code(Party, row, who, role_assignments__role=who)
                    reference = required(row, "reference")
                    key = (party.pk, reference)
                    if key not in orders:
                        if Order.objects.filter(**{who: party, "reference": reference}).exists():
                            raise RowError("reference", f"{party.code} already has an order {reference!r}.")
                        order = Order(**{who: party}, order_date=date(row, "date"), reference=reference,
                                      currency=by_code(Currency, row, "currency", required_=False)
                                      or party.default_currency)
                        if side == "sales":
                            order.payment_terms = (by_code(PaymentTerms, row, "payment_terms", required_=False)
                                                   or party.payment_terms)
                        clean(order, {"order_date": "date"})
                        order.save()
                        orders[key] = (number, order)
                    order = orders[key][1]
                    item = by_code(Item, row, "sku", field_name="sku")
                    quantity = decimal(row, "quantity", places=4, required_=True)
                    if quantity <= 0:
                        raise RowError("quantity", "must be above nothing: what is still to come.")
                    price = decimal(row, "unit_price", places=4, required_=True)
                    if price < 0:
                        raise RowError("unit_price", "cannot be below nothing.")
                    line = Line(order=order, item=item, quantity=quantity, unit_price=price,
                                uom=by_code(UnitOfMeasure, row, "uom", required_=False) or item.uom,
                                description=row.get("description", "")[:255],
                                warehouse=by_code(Warehouse, row, "warehouse", required_=False))
                    setattr(line, when, date(row, when, required_=False))
                    clean(line, {when: when})
                    line.save()
                    wanted = codes(row, "taxes")
                    if wanted:
                        taxes = list(Tax.objects.filter(code__in=wanted))
                        missing = sorted(set(wanted) - {tax.code for tax in taxes})
                        if missing:
                            raise RowError("taxes", f"no tax {', '.join(missing)}.")
                        line.taxes.set(taxes)
                    report.created += 1
            except RowError as error:
                report.refuse(number, error.column, error.message)
            except ValidationError as error:
                report.refuse(number, "", " ".join(error.messages))
        if report.errors:
            return
        # Every order, then every customer, in key order, before the first is confirmed: one
        # transaction confirms them all, and each confirmation takes its order and then its
        # customer, so the second order was taken after the first customer (the lock order).
        lock_rows(*(order for _, order in orders.values()), refresh=False)
        lock_rows(*{order.customer_id: order.customer for _, order in orders.values()
                    if getattr(order, "customer_id", None)}.values(), refresh=False)
        for number, order in orders.values():
            try:
                with transaction.atomic():
                    order.confirm()
            except ValidationError as error:
                report.refuse(number, "", f"{order.reference}: " + " ".join(error.messages))

    return handler


# -- the asset register ---------------------------------------------------


def _month_end(day):
    return datetime.date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])


def _fixed_asset(row, options):
    """
    An asset already in service, with what the old system had depreciated
    by the go-live date. That figure's journal side is in the opening
    balances; here it is recorded on the asset as its opening
    depreciation, and the months after the go-live date are this system's.
    """
    from apps.assets.models import AssetCategory, FixedAsset
    from apps.core.models import Party

    on = options.get("date")
    if on is None:
        raise RowError("--date", "the go-live date the register stands at is needed.")
    category = by_code(AssetCategory, row, "category")
    name = required(row, "name")
    if FixedAsset.objects.filter(name=name, category=category).exists():
        raise RowError("name", f"{category.code} already has an asset {name!r}.")
    cost = decimal(row, "cost", places=2, required_=True)
    salvage = decimal(row, "salvage_value", places=2) or Decimal("0")
    taken = decimal(row, "depreciated_to_date", places=2) or Decimal("0")
    if taken < 0:
        raise RowError("depreciated_to_date", "cannot be below nothing.")
    if taken > cost - salvage:
        raise RowError("depreciated_to_date", f"{taken} is more than the {cost - salvage} there is to depreciate.")
    acquired = date(row, "acquisition_date")
    in_service = date(row, "in_service_date", required_=False) or acquired
    if in_service > on:
        raise RowError("in_service_date", f"{in_service:%d-%m-%Y} is after the go-live date; register it as new instead.")
    asset = FixedAsset(
        name=name, category=category,
        vendor=by_code(Party, row, "vendor", required_=False, role_assignments__role="vendor"),
        acquisition_date=acquired, in_service_date=in_service, cost=cost, salvage_value=salvage,
        life_months=whole_number(row, "life_months") or category.default_life_months,
        depreciated_before=on, opening_depreciation=taken,
    )
    clean(asset, {"acquisition_date": "acquisition_date", "in_service_date": "in_service_date"})
    asset.save()
    asset.place_in_service(in_service)


# -- pay and prices ------------------------------------------------------


def _compensation(row, options):
    from apps.hr.models import Employee
    from apps.hr.payroll import EmployeeCompensation, PayComponent

    employee = by_code(Employee, row, "employee_number", field_name="employee_number")
    component = by_code(PayComponent, row, "component")
    amount = decimal(row, "amount", places=2, required_=True)
    if amount < 0:
        raise RowError("amount", "cannot be below nothing.")
    since = date(row, "effective_from")
    if EmployeeCompensation.objects.filter(employee=employee, component=component, effective_from=since).exists():
        raise RowError("component", f"{employee.employee_number} already has {component.code} from {since:%d-%m-%Y}.")
    pay = EmployeeCompensation(employee=employee, component=component, amount=amount, effective_from=since,
                               effective_to=date(row, "effective_to", required_=False),
                               note=row.get("note", "")[:255])
    clean(pay)
    pay.save()


def _price(row, options):
    from apps.core.models import Currency
    from apps.inventory.models import Item
    from apps.sales.models import PriceList, PriceListItem

    code = required(row, "price_list")
    price_list = PriceList.objects.filter(code=code).first()
    if price_list is None:
        price_list = PriceList(code=code, name=row.get("price_list_name") or code,
                               currency=by_code(Currency, row, "currency", required_=False))
        clean(price_list, {"code": "price_list", "name": "price_list_name"})
        price_list.save()
    item = by_code(Item, row, "sku", field_name="sku")
    entry = PriceListItem(price_list=price_list, item=item,
                          unit_price=decimal(row, "unit_price", places=2, required_=True))
    breaks = decimal(row, "min_quantity", places=4)
    if breaks is not None:
        entry.min_quantity = breaks
    if PriceListItem.objects.filter(price_list=price_list, item=item, min_quantity=entry.min_quantity).exists():
        raise RowError("sku", f"{code} already prices {item.sku} from {entry.min_quantity}.")
    clean(entry)
    entry.save()


def _vendor_price(row, options):
    from apps.core.models import Currency, Party
    from apps.inventory.models import Item
    from apps.purchasing.models import VendorPrice

    vendor = by_code(Party, row, "vendor", role_assignments__role="vendor")
    item = by_code(Item, row, "sku", field_name="sku")
    price = VendorPrice(vendor=vendor, item=item, unit_price=decimal(row, "unit_price", places=2, required_=True),
                        currency=by_code(Currency, row, "currency", required_=False) or vendor.default_currency,
                        vendor_item_code=row.get("vendor_item_code", "")[:64],
                        valid_from=date(row, "valid_from", required_=False),
                        valid_to=date(row, "valid_to", required_=False),
                        is_preferred=yes_no(row, "is_preferred", False))
    breaks = decimal(row, "min_quantity", places=4)
    if breaks is not None:
        price.min_quantity = breaks
    lead = whole_number(row, "lead_time_days")
    if lead is not None:
        price.lead_time_days = lead
    clean(price)
    price.save()


COLUMNS = {
    "open_sales_orders": ["customer", "reference", "date", "currency", "payment_terms", "sku", "description",
                          "quantity", "uom", "unit_price", "delivery_date", "warehouse", "taxes"],
    "open_purchase_orders": ["vendor", "reference", "date", "currency", "sku", "description", "quantity", "uom",
                             "unit_price", "expected_date", "warehouse", "taxes"],
    "fixed_assets": ["name", "category", "vendor", "acquisition_date", "in_service_date", "cost", "salvage_value",
                     "life_months", "depreciated_to_date"],
    "compensation": ["employee_number", "component", "amount", "effective_from", "effective_to", "note"],
    "price_lists": ["price_list", "price_list_name", "currency", "sku", "min_quantity", "unit_price"],
    "vendor_prices": ["vendor", "sku", "unit_price", "currency", "min_quantity", "vendor_item_code",
                      "lead_time_days", "valid_from", "valid_to", "is_preferred"],
}

CUTOVER_PER_ROW = {
    **{kind: _spec(kind) for kind in SPEC_MODELS},
    "fixed_assets": _fixed_asset,
    "compensation": _compensation,
    "price_lists": _price,
    "vendor_prices": _vendor_price,
}
CUTOVER_WHOLE_FILE = {
    "open_sales_orders": _open_orders("sales"),
    "open_purchase_orders": _open_orders("purchasing"),
}
CUTOVER = (*SPEC_MODELS, "open_sales_orders", "open_purchase_orders", "fixed_assets", "compensation",
           "price_lists", "vendor_prices")
