"""
The floor's runs as columns: not yet released; released and waiting;
running; output complete and waiting to be closed; closed this week.
Read from what each run has done, never stored.
"""

import datetime

from django.db.models import Q
from django.utils import timezone

from apps.core.models import to_date

from .orders import WorkOrder, WorkOrderStatus

COLUMNS = [
    ("draft", "Not released"),
    ("waiting", "Released, not started"),
    ("running", "Running"),
    ("to_close", "Output complete"),
    ("closed", "Closed this week"),
]


def board(today=None):
    """[{key, label, cards: [{id, number, item, quantity_ordered, quantity_produced, uom, starts, due, late, work_centre, machines, for, status}]}]."""
    today = to_date(today) or timezone.localdate()
    week_ago = today - datetime.timedelta(days=7)
    orders = WorkOrder.objects.filter(
        Q(status__in=[WorkOrderStatus.DRAFT, WorkOrderStatus.RELEASED])
        | Q(status=WorkOrderStatus.CLOSED, closed_at__date__gte=week_ago),
    ).select_related("item", "uom", "work_centre", "sales_order_line__order").prefetch_related("operations__machine")
    columns = {key: [] for key, _ in COLUMNS}
    for order in orders:
        produced = order.quantity_produced()
        if order.status == WorkOrderStatus.DRAFT:
            key = "draft"
        elif order.status == WorkOrderStatus.CLOSED:
            key = "closed"
        elif produced >= order.quantity_ordered:
            key = "to_close"
        elif produced > 0 or order.minutes_booked() > 0 or order.posted_issues().exists():
            key = "running"
        else:
            key = "waiting"
        columns[key].append({
            "id": order.pk, "number": order.number, "status": order.status,
            "item": f"{order.item.sku} · {order.item.name}",
            "quantity_ordered": order.quantity_ordered, "quantity_produced": produced, "uom": order.uom.code,
            "starts": order.scheduled_start, "due": order.scheduled_end,
            "late": order.status != WorkOrderStatus.CLOSED and order.scheduled_end is not None and order.scheduled_end < today,
            "work_centre": order.work_centre.code if order.work_centre_id else "",
            "machines": sorted({operation.machine.code for operation in order.operations.all() if operation.machine_id}),
            "for": order.sales_order_line.order.number if order.sales_order_line_id else "",
        })
    for cards in columns.values():
        cards.sort(key=lambda card: (card["due"] is None, card["due"] or today, card["number"], card["id"]))
    return [{"key": key, "label": label, "cards": columns[key]} for key, label in COLUMNS]
