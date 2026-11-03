"""
Issuing by scan (L9): on a draft material issue, a scanned batch is named
on the line for its item, by the rule picking asks
(apps.inventory.scanning.pick_refusal): another item's batch, one the
release gate holds, or one first-expired-first-out would not take yet is
refused in words. A rework run may draw the batch it reworks, as the line
itself allows. Posting stays the issue's own button.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.inventory.scanning import assign_batch, goods_named, hold_draft, once, one_of, pick_refusal, release_batch


def scan_into_issue(issue, request, code, key=None):
    from .orders import MaterialIssueLine

    with transaction.atomic():
        hold_draft(issue, lambda held: held.posted,
                   lambda held: f"{held.number or 'This issue'} was posted meanwhile; a scan does not change "
                                "a posted issue.")
        if issue.direction != "issue":
            raise ValidationError("A return to stores is matched to the line it returns, not picked by scan.")

        def work():
            lines = list(issue.lines.select_related("item", "uom", "lot").order_by("pk"))
            items, lots = goods_named(code, request)
            item, lot = one_of(code, items, lots, {line.item_id for line in lines})
            if lot is None:
                raise ValidationError(f"{item.sku} is an item; an issue by scan names the batch. Scan the batch label.")
            mine = [line for line in lines if line.item_id == lot.item_id]
            if not mine:
                raise ValidationError(f"Batch {lot.code} is {lot.item.sku}; nothing on {issue.number} is.")
            waiting = [line for line in mine if line.lot_id is None]
            if not waiting:
                raise ValidationError(f"Every line of {lot.item.sku} on {issue.number} already names its batch.")
            line = waiting[0]
            named = {}
            for other in mine:
                if other.lot_id:
                    named[other.lot_id] = named.get(other.lot_id, Decimal("0")) + other.stock_quantity()
            order = issue.work_order
            said = pick_refusal(
                lot.item, lot, issue.warehouse, named, on_date=issue.issue_date, action="go into a run",
                let_through=lambda _item, held: bool(order.rework_of_id) and held.pk == order.rework_of_id)
            if said:
                raise ValidationError(said)
            return assign_batch(
                line, lot, "quantity", line.uom, issue.warehouse, named,
                lambda held, quantity: MaterialIssueLine.objects.create(
                    issue=issue, item=held.item, uom=held.uom, bin=held.bin, lot=lot, quantity=quantity))

        return once(key, issue, work)


def unpick(issue, line_id):
    """A scanned batch taken off its line before posting: the reverse of an issue scan (release_batch)."""
    with transaction.atomic():
        hold_draft(issue, lambda held: held.posted,
                   lambda held: f"{held.number} was posted meanwhile; what it drew comes back by its void or a return.")
        line = issue.lines.filter(pk=line_id).first()
        if line is None:
            raise ValidationError("That line is not on this issue.")
        waiting = (issue.lines.filter(item=line.item, uom=line.uom, bin=line.bin, lot__isnull=True,
                                      returns_line__isnull=True).exclude(pk=line.pk).order_by("pk").first())
        return release_batch(line, "quantity", waiting)


def issue_scan_actions(issue, user):
    return ["issue"] if not issue.posted and issue.direction == "issue" and user.has_perm(
        "manufacturing.add_materialissueline") else []
