"""
Receiving by scan (L9): on a draft receipt, a scanned item or batch adds
its line or counts it up. The code is read through the one resolver
(apps.inventory.scanning); the receipt is held first and a receipt posted
meanwhile is refused in words. Posting stays the receipt's own button.
"""

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.inventory.scanning import goods_named, hold_draft, once, one_of, positive_quantity


def scan_into_receipt(receipt, request, code, quantity=None, key=None):
    from .models import GoodsReceiptLine

    quantity = positive_quantity(quantity)
    with transaction.atomic():
        hold_draft(receipt, lambda held: held.posted,
                   lambda held: f"{held.number or 'This receipt'} was posted meanwhile; a scan does not change "
                                "a posted receipt.")
        if receipt.reverses_id:
            raise ValidationError("A return is made from the receipt it reverses, not by scan.")

        def work():
            order = receipt.purchase_order
            order_lines = [line for line in order.lines.select_related("item").order_by("pk")
                           if line.item_id and not line.is_charge()]
            items, lots = goods_named(code, request)
            item, lot = one_of(code, items, lots, {line.item_id for line in order_lines})
            candidates = [line for line in order_lines if line.item_id == item.pk]
            if not candidates:
                raise ValidationError(f"{item.sku} is not on {order.number}.")
            line = (receipt.lines.filter(order_line__in=candidates, lot=lot, reverses_line__isnull=True)
                    .order_by("pk").first())
            if line is not None:
                line.quantity_received += quantity
                line.save()
                return line
            return GoodsReceiptLine.objects.create(receipt=receipt, order_line=candidates[0], lot=lot,
                                                   quantity_received=quantity)

        return once(key, receipt, work)


def receipt_scan_actions(receipt, user):
    return ["receive"] if not receipt.posted and not receipt.reverses_id and user.has_perm(
        "purchasing.add_goodsreceiptline") else []


def order_scan_actions(order, user):
    return ["receive"] if order.status == "confirmed" and user.has_perm("purchasing.add_goodsreceipt") else []
