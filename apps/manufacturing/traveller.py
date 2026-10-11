"""
The job card that travels with a run: printed at release, carried from
machine to machine, and read by people who have no screen.

Everything on it is what the run was released against — its steps and
their planned times, its materials with their allowances — because
those were frozen at release, and a card that disagreed with the run
would be worse than none. The specification's limits are the ones the
run's output is checked against: the inspection plan's, and on a
laminated sack the coater's coating window.

Each step has blank columns for what the paper is for: good count,
scrap, who, when. The barcode is the run's number, which is what the
stations scan.

A draft has neither steps nor materials yet, and a cancelled run must
not be on the floor at all: neither is printed.
"""

from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.utils import timezone

from apps.core.api import plain

from . import barcode
from .orders import WorkOrderStatus


def _at(value, places):
    return None if value is None else str(Decimal(value).quantize(Decimal(1).scaleb(-places)))


def specification_of(order):
    """The specification the run's bill was computed from, if it was."""
    for name in ("bag_specification", "fabric_specification", "tape_specification"):
        try:
            return getattr(order.bom, name)
        except ObjectDoesNotExist:
            continue
    return None


def _limits(spec):
    plan = getattr(spec, "inspection_plan", None) if spec is not None else None
    if plan is None:
        return []
    rows = []
    for line in plan.lines.select_related("characteristic__uom").order_by("line_number", "id"):
        places = line.characteristic.decimal_places
        rows.append({
            "characteristic": line.characteristic.name,
            "unit": line.characteristic.uom.code if line.characteristic.uom_id else "",
            "target": _at(line.target, places),
            "lower": _at(line.lower_limit, places),
            "upper": _at(line.upper_limit, places),
            "sample_size": line.sample_size,
        })
    return rows


def traveller(order):
    from .station_coat import COATING_SAMPLES, coating_limits
    from .woven import BagSpecification

    if order.status == WorkOrderStatus.DRAFT:
        raise ValidationError(f"{order} is a draft; its steps and materials are fixed when "
                              "it is released. Release it, then print its card.")
    if order.status == WorkOrderStatus.CANCELLED:
        raise ValidationError(f"{order} is cancelled; a card for it must not be on the floor.")
    spec = specification_of(order)
    coating = None
    if isinstance(spec, BagSpecification) and spec.is_laminated:
        target, lower, upper = coating_limits(spec)
        coating = {"target": _at(target, 2), "lower": _at(lower, 2), "upper": _at(upper, 2),
                   "samples": COATING_SAMPLES}
    customer_order = None
    if order.sales_order_line_id:
        line = order.sales_order_line
        so = line.order
        # As the order recorded them, so the floor packs what the order promised.
        customer_order = {"number": so.number, "customer": str(so.customer),
                          "sacks_per_bale": so.sacks_per_bale, "marking": so.marking}
    return {
        "number": order.number,
        "barcode": barcode.svg(order.number),
        "status": order.get_status_display(),
        "item": {"sku": order.item.sku, "name": order.item.name},
        "quantity_ordered": plain(order.quantity_ordered),
        "quantity_to_start": plain(order.quantity_to_start),
        "uom": order.uom.code,
        "warehouse": order.warehouse.code,
        "scheduled_start": order.scheduled_start,
        "scheduled_end": order.scheduled_end,
        "customer_order": customer_order,
        "rework_of": order.rework_of.code if order.rework_of_id else None,
        "tools": [tool.code for tool in order.tools.order_by("code")],
        "specification": str(spec) if spec is not None else None,
        "construction": spec.construction() if isinstance(spec, BagSpecification) else None,
        "limits": _limits(spec),
        "coating": coating,
        "steps": [{
            "sequence": step.sequence,
            "name": step.name,
            "where": ("Outside" if step.is_outside
                      else step.work_centre.code if step.work_centre_id else ""),
            "machine": step.machine.code if step.machine_id else "",
            "setup_minutes": plain(step.setup_minutes),
            "planned_minutes": plain(step.planned_minutes),
            "planned_start": step.planned_start,
        } for step in order.operations.select_related("work_centre", "machine")],
        "materials": [{
            "sku": line.item.sku,
            "name": line.item.name,
            # To the gramme: the card is read by a storeman with a scale.
            "quantity": plain(Decimal(line.quantity_required).quantize(Decimal("0.001"))),
            "uom": line.uom.code,
            "waste_percent": plain(line.waste_percent),
        } for line in order.components.select_related("item", "uom")],
        "printed_at": timezone.now(),
    }
