"""
A record by what is written on it: an invoice or bale number, a batch
code, a lorry's registration, a customer's name. The palette goes to a
screen by name; this goes to the record itself.

Each kind is offered only to a login that may read it, narrowed as its
own list would be (a rep finds only their customers' invoices), and
limited to a few of the latest, because the palette shows a handful.
"""

from dataclasses import dataclass
from typing import Callable

from django.db.models import Q

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.core.scoping import scoped

LIMIT_A_KIND = 5


@dataclass(frozen=True)
class Kind:
    label: str
    permission: str
    queryset: Callable
    fields: tuple
    text: Callable
    href: Callable
    # The party field the login's scope narrows on; "" for the party itself; None for no narrowing.
    scope: str | None = None


def _parties(role):
    return Party.objects.filter(pk__in=PartyRoleAssignment.objects.filter(role=role).values("party"))


def _payment_href(payment):
    from apps.accounting.models import PaymentDirection

    return (f"/purchasing/payments/{payment.pk}" if payment.direction == PaymentDirection.DISBURSEMENT
            else f"/sales/receipts/{payment.pk}")


def kinds():
    from apps.accounting.models import Payment
    from apps.hr.models import Employee
    from apps.inventory.models import Item, Lot
    from apps.manufacturing.bales import Bale
    from apps.manufacturing.complaints import Complaint
    from apps.manufacturing.machines import Machine
    from apps.manufacturing.orders import WorkOrder
    from apps.purchasing.models import Bill, PurchaseOrder
    from apps.sales.models import Delivery, Invoice, SalesOrder

    named = lambda row: f"{row.number} · {row.customer.name}"  # noqa: E731
    return [
        Kind("Sales order", "sales.view_salesorder", lambda: SalesOrder.objects.select_related("customer"),
             ("number",), named, lambda row: f"/sales/orders/{row.pk}", "customer"),
        Kind("Delivery", "sales.view_delivery", lambda: Delivery.objects.select_related("sales_order__customer"),
             ("number", "vehicle_number", "lr_number"),
             lambda row: f"{row.number} · {row.sales_order.customer.name}"
             + (f" · {row.vehicle_number}" if row.vehicle_number else ""),
             lambda row: f"/sales/deliveries/{row.pk}", "sales_order__customer"),
        Kind("Invoice", "sales.view_invoice", lambda: Invoice.objects.select_related("customer"),
             ("number", "reference"), named, lambda row: f"/sales/invoices/{row.pk}", "customer"),
        Kind("Customer", "core.view_party", lambda: _parties(PartyRole.CUSTOMER), ("code", "name"),
             lambda row: f"{row.code} · {row.name}", lambda row: f"/sales/customers/{row.pk}", ""),
        Kind("Vendor", "core.view_party", lambda: _parties(PartyRole.VENDOR), ("code", "name"),
             lambda row: f"{row.code} · {row.name}", lambda row: f"/purchasing/vendors/{row.pk}", ""),
        Kind("Purchase order", "purchasing.view_purchaseorder", lambda: PurchaseOrder.objects.select_related("vendor"),
             ("number",), lambda row: f"{row.number} · {row.vendor.name}", lambda row: f"/purchasing/orders/{row.pk}"),
        Kind("Bill", "purchasing.view_bill", lambda: Bill.objects.select_related("vendor"), ("number", "reference"),
             lambda row: f"{row.number} · {row.vendor.name}" + (f" · {row.reference}" if row.reference else ""),
             lambda row: f"/purchasing/bills/{row.pk}"),
        Kind("Payment", "accounting.view_payment", lambda: Payment.objects.select_related("party"), ("number",),
             lambda row: f"{row.number} · {row.party.name if row.party_id else ''}", _payment_href, "party"),
        Kind("Work order", "manufacturing.view_workorder", lambda: WorkOrder.objects.select_related("item"), ("number",),
             lambda row: f"{row.number} · {row.item.sku}", lambda row: f"/production/work-orders/{row.pk}"),
        Kind("Bale", "manufacturing.view_bale", lambda: Bale.objects.select_related("item"), ("number",),
             lambda row: f"{row.number} · {row.item.sku}", lambda row: f"/production/bales/{row.pk}"),
        Kind("Batch", "inventory.view_lot", lambda: Lot.objects.select_related("item"), ("code",),
             lambda row: f"{row.code} · {row.item.sku}", lambda row: f"/stores/batches/{row.pk}"),
        Kind("Item", "inventory.view_item", lambda: Item.objects.all(), ("sku", "name"),
             lambda row: f"{row.sku} · {row.name}", lambda row: f"/stores/items/{row.pk}"),
        Kind("Employee", "hr.view_employee", lambda: Employee.objects.select_related("party"),
             ("employee_number", "party__name"), lambda row: f"{row.employee_number} · {row.party.name}",
             lambda row: f"/payroll/employees/{row.pk}", "party"),
        Kind("Complaint", "manufacturing.view_complaint", lambda: Complaint.objects.select_related("customer"),
             ("number",), named, lambda row: f"/quality/complaints/{row.pk}", "customer"),
        Kind("Machine", "manufacturing.view_machine", lambda: Machine.objects.all(), ("code", "name"),
             lambda row: f"{row.code} · {row.name}" if row.name else row.code, lambda row: f"/making/machines/{row.pk}"),
    ]


def search(user, typed, limit=LIMIT_A_KIND):
    """[{kind, label, href}] for what the login may read, latest first within each kind."""
    wanted = (typed or "").strip()
    if len(wanted) < 2:
        return []
    hits = []
    for kind in kinds():
        if not user.has_perm(kind.permission):
            continue
        rows = kind.queryset()
        if kind.scope == "":
            rows = scoped(rows, user)
        elif kind.scope is not None:
            rows = scoped(rows, user, kind.scope)
        condition = Q()
        for field in kind.fields:
            condition |= Q(**{f"{field}__icontains": wanted})
        for row in rows.filter(condition).order_by("-pk")[:limit]:
            hits.append({"kind": kind.label, "label": kind.text(row), "href": kind.href(row)})
    return hits
