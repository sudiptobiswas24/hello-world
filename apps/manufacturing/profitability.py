"""
What an order line was quoted at against what it cost and fetched.

**Quoted** is the cost sheet frozen when the sack was priced, per sack:
material, conversion, by-product credit, overhead, and the price. It is
the line's own, through the quotation line it was accepted from, so a
sheet costed again tomorrow does not move it.

**Actual** is what the runs made for the line consumed, at the values
their documents posted - material, machine time and vendors' work, less
what came back as by-product - over what they made. Read before a run
closes it is a running figure, and the report says whether every run
behind it is closed.

**Fetched** is what the line billed less what was credited, in the base
currency at each document's posted rate. The margin is that, less what
shipped at the actual cost a unit: the same direct cost the quote's
direct cost is compared with, overhead apart on both sides.
"""

from decimal import Decimal

ZERO = Decimal("0")


def run_costs(order):
    """(material, conversion, by-product credit, made in stock units) for one run."""
    from .orders import ProductionByproduct

    credit = ZERO
    for row in ProductionByproduct.objects.filter(
            entry__work_order=order, entry__posted=True,
            entry__voided_at__isnull=True).select_related("item", "uom"):
        credit += (row.unit_value or ZERO) * row.item.to_stock_quantity(row.quantity, row.uom)
    made = order.item.to_stock_quantity(order.quantity_produced(), order.uom)
    return (order.material_cost(), order.conversion_cost() + order.outside_cost(), credit,
            made)


def quoted(line):
    """The frozen cost sheet's figures a sack, or None where the line was not quoted so."""
    from .quoting import CostSheet

    if line.quotation_line_id is None:
        return None
    sheet = CostSheet.objects.filter(quotation_line_id=line.quotation_line_id).first()
    if sheet is None:
        return None
    return {"sheet": sheet, "material": sheet.material, "conversion": sheet.conversion,
            "credit": sheet.credit,
            "direct": sheet.material + sheet.conversion - sheet.credit,
            "overhead": sheet.overhead, "cost": sheet.cost, "price": sheet.quoted_price}


def line_profitability(line):
    from .orders import WorkOrder, WorkOrderStatus

    runs = list(WorkOrder.objects.filter(sales_order_line=line).exclude(
        status=WorkOrderStatus.CANCELLED))
    material = conversion = credit = made = ZERO
    for run in runs:
        m, c, b, q = run_costs(run)
        material, conversion, credit, made = material + m, conversion + c, credit + b, made + q
    actual = None
    if made:
        actual = {"material": material / made, "conversion": conversion / made,
                  "credit": credit / made, "direct": (material + conversion - credit) / made}
    shipped = line.item.to_stock_quantity(line.quantity_shipped(), line.uom) if line.item_id \
        else ZERO
    revenue = line.revenue_in_base()
    cost_of_shipped = actual["direct"] * shipped if actual is not None else None
    margin = revenue - cost_of_shipped if cost_of_shipped is not None else None
    return {
        "line": line, "quoted": quoted(line), "actual": actual,
        "runs": len(runs),
        "final": bool(runs) and all(run.status == WorkOrderStatus.CLOSED for run in runs),
        "made": made, "shipped": shipped, "revenue": revenue,
        "realised_price": revenue / shipped if shipped else None,
        "cost_of_shipped": cost_of_shipped, "margin": margin,
        "margin_percent": (margin / revenue * 100) if margin is not None and revenue else None,
    }
