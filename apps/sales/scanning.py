"""
Picking by scan (L9): on a draft delivery, a scanned batch is named on the
line for its item. Refused, in words, if the batch is another item's, if
the release gate holds it, or if first-expired-first-out would take another
first, naming which (apps.inventory.scanning.pick_refusal, the one rule the
issue screen asks too). A batch with less free than the line wants takes
what it has on a line of its own; the rest waits for the next scan. The
delivery is held first and one posted meanwhile is refused. Posting stays
the delivery's own button.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.inventory.scanning import assign_batch, goods_named, hold_draft, once, one_of, pick_refusal, release_batch


def scan_into_delivery(delivery, request, code, key=None):
    from .models import DeliveryLine

    with transaction.atomic():
        hold_draft(delivery, lambda held: held.posted,
                   lambda held: f"{held.number or 'This delivery'} was posted meanwhile; a scan does not change "
                                "a posted delivery.")
        if delivery.reverses_id:
            raise ValidationError("A return is made from the delivery it reverses, not by scan.")

        def work():
            lines = list(delivery.lines.select_related("order_line__item", "order_line__uom", "lot", "warehouse")
                         .order_by("pk"))
            items, lots = goods_named(code, request)
            item, lot = one_of(code, items, lots, {line.order_line.item_id for line in lines if line.order_line_id})
            if lot is None:
                raise ValidationError(f"{item.sku} is an item; a pick names the batch. Scan the batch label.")
            mine = [line for line in lines if line.order_line_id and line.order_line.item_id == lot.item_id]
            if not mine:
                raise ValidationError(f"Batch {lot.code} is {lot.item.sku}; nothing on {delivery.number} is.")
            waiting = [line for line in mine if line.lot_id is None]
            if not waiting:
                raise ValidationError(f"Every line of {lot.item.sku} on {delivery.number} already names its batch.")
            line = waiting[0]
            named = {}
            for other in mine:
                if other.lot_id and other.warehouse_id == line.warehouse_id:
                    named[other.lot_id] = named.get(other.lot_id, Decimal("0")) + lot.item.to_stock_quantity(
                        other.quantity_shipped, other.order_line.uom)
            said = pick_refusal(lot.item, lot, line.warehouse, named, on_date=delivery.delivery_date,
                                action="be shipped")
            if said:
                raise ValidationError(said)
            return assign_batch(
                line, lot, "quantity_shipped", line.order_line.uom, line.warehouse, named,
                lambda held, quantity: DeliveryLine.objects.create(
                    delivery=delivery, order_line=held.order_line, warehouse=held.warehouse, bin=held.bin,
                    lot=lot, quantity_shipped=quantity))

        return once(key, delivery, work)


def unpick(delivery, line_id):
    """A picked batch taken off its line before posting: the reverse of a pick scan (release_batch)."""
    with transaction.atomic():
        hold_draft(delivery, lambda held: held.posted,
                   lambda held: f"{held.number} was posted meanwhile; its return is a customer return.")
        line = delivery.lines.filter(pk=line_id).first()
        if line is None:
            raise ValidationError("That line is not on this delivery.")
        waiting = (delivery.lines.filter(order_line=line.order_line, warehouse=line.warehouse, bin=line.bin,
                                         lot__isnull=True).exclude(pk=line.pk).order_by("pk").first())
        return release_batch(line, "quantity_shipped", waiting)


def delivery_scan_actions(delivery, user):
    return ["pick"] if not delivery.posted and not delivery.reverses_id and user.has_perm(
        "sales.change_deliveryline") else []
