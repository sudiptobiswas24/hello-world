"""
Scanning in stores (L9): what a code is, and what a scan does to a draft.

One resolver answers what a code is: an item (by its code, or its GTIN),
a bin, a batch, or a document's number. Every scan screen asks it; none
parses a code itself. It answers only what the asker may read: each kind
is looked up through the viewset that serves it, so the permission and
the row scoping (a rep's own customers) are that viewset's own, never a
second copy here.

A scan never posts. It adds or counts up a line on a draft, or names the
batch a line draws, and the document's own button posts it with its own
checks. Every scan holds the document first (lock_rows) and refuses in
words one posted meanwhile: two clerks scanning into one draft both land,
and a scan does not reach a posted document.

Documents of other modules (receipts, deliveries, material issues) are
registered by those modules (register_scan_document), because inventory
may not import them.
"""

from dataclasses import dataclass, field as dc_field
from decimal import Decimal
from typing import Callable

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.html import escape

from apps.core.models import lock_rows, to_date


# --- GTIN -------------------------------------------------------------------------------------------------------

GTIN_LENGTHS = (8, 12, 13, 14)


def gtin_check_digit(body):
    """The GS1 check digit for `body` (every digit but the last): weights 3 and 1 from the right."""
    total = sum(int(digit) * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(body)))
    return (10 - total % 10) % 10


def gtin_refusal(code):
    """Why `code` is not a GTIN, in words, or "" for one that is."""
    if not code.isdigit() or len(code) not in GTIN_LENGTHS:
        return f"{code} is not a GTIN: one is 8, 12, 13 or 14 digits."
    expected = gtin_check_digit(code[:-1])
    if int(code[-1]) != expected:
        return f"{code} has check digit {code[-1]}; its digits make {expected}. Check it was typed as printed."
    return ""


# --- Who may read what ------------------------------------------------------------------------------------------

def readable(viewset_class, request):
    """
    What `request`'s person may read through `viewset_class`, as that
    viewset's own list would answer it, or None when they may read none.
    The viewset's own permission and its own get_queryset: a rep reads
    only their customers' deliveries here exactly as on the list.
    """
    from rest_framework.exceptions import APIException
    from rest_framework.request import clone_request

    # Asked as a read, whatever the scan was sent as: a POST asked add_item of an item the clerk only reads.
    request = clone_request(request, "GET")
    view = viewset_class()
    view.request, view.args, view.kwargs, view.format_kwarg = request, (), {}, None
    view.action = "list"
    view.headers = {}
    try:
        view.check_permissions(request)
    except APIException:
        return None
    return view.get_queryset()


def may(user, permission):
    return bool(user and user.has_perm(permission))


@dataclass
class ScanDocument:
    """A document a scan may name: `kind`, read through `viewset()`, found by `field`."""

    kind: str
    noun: str
    viewset: Callable
    actions: Callable  # (document, user) -> [action]
    open: dict = dc_field(default_factory=lambda: {"posted": False})
    describe: Callable = dc_field(default=lambda document: "")
    field: str = "number"
    state: Callable = dc_field(default=lambda document: "posted" if getattr(document, "posted", False) else "draft")

    def label(self, document):
        """Its number; a draft, which is numbered only when it posts, by its noun, id and what it is for."""
        number = getattr(document, self.field, "")
        said = self.describe(document)
        return (f"{self.noun.capitalize()} {number}" if number
                else f"Draft {self.noun} #{document.pk}") + (f", {said}" if said else "")


SCAN_DOCUMENTS = []


def register_scan_document(document):
    if all(each.kind != document.kind for each in SCAN_DOCUMENTS):
        SCAN_DOCUMENTS.append(document)


def _inventory_documents():
    from .views import StockCountViewSet, StockTransferViewSet

    return [
        ScanDocument(
            "stock_transfer", "transfer", lambda: StockTransferViewSet,
            lambda transfer, user: ["transfer"] if transfer.status == "draft" and may(
                user, "inventory.add_stocktransferline") else [],
            open={"status": "draft"},
            describe=lambda transfer: f"{transfer.from_warehouse} to {transfer.to_warehouse}",
            state=lambda transfer: transfer.status),
        ScanDocument(
            "stock_count", "count", lambda: StockCountViewSet,
            lambda count, user: ["count"] if not count.posted and may(user, "inventory.add_stockcountline") else [],
            describe=lambda count: f"{count.warehouse} on {count.count_date:%d-%m-%Y}"),
    ]


def resolve(code, request):
    """
    [{kind, id, label, ...}]: everything `code` names that `request`'s
    person may read. Empty for a code that names nothing, and for one
    that names only what they may not read: the answer does not say which.
    """
    from .views import ItemViewSet, LotViewSet, StorageBinViewSet

    code = (code or "").strip()
    if not code:
        return []
    found = []
    items = readable(ItemViewSet, request)
    if items is not None:
        for item in items.filter(models.Q(sku__iexact=code) | models.Q(gtin=code)).order_by("pk"):
            found.append({"kind": "item", "id": item.pk, "code": item.sku, "label": f"{item.sku} {item.name}",
                          "by": "gtin" if item.gtin == code and item.sku.lower() != code.lower() else "code",
                          "tracking": item.tracking, "uom": item.uom_id})
    bins = readable(StorageBinViewSet, request)
    if bins is not None:
        for row in bins.filter(code__iexact=code).select_related("warehouse").order_by("pk"):
            found.append({"kind": "bin", "id": row.pk, "code": row.code, "warehouse": row.warehouse_id,
                          "label": f"{row.code} in {row.warehouse}"})
    lots = readable(LotViewSet, request)
    if lots is not None:
        for lot in lots.filter(code__iexact=code).select_related("item").order_by("pk"):
            found.append({"kind": "lot", "id": lot.pk, "code": lot.code, "item": lot.item_id,
                          "label": f"Batch {lot.code} of {lot.item.sku}",
                          "expires_on": lot.expires_on})
    for document in [*_inventory_documents(), *SCAN_DOCUMENTS]:
        rows = readable(document.viewset(), request)
        if rows is None:
            continue
        for row in rows.filter(**{f"{document.field}__iexact": code}).exclude(**{document.field: ""}).order_by("pk"):
            found.append(_document_row(document, row, request.user))
    for row in found:
        row.setdefault("actions", [])
    return found


def _document_row(document, row, user):
    return {"kind": document.kind, "id": row.pk, "code": getattr(row, document.field) or "",
            "label": document.label(row), "state": document.state(row), "actions": document.actions(row, user)}


def open_to_scan(request, limit=25):
    """
    [{kind, id, label, actions}]: the drafts the person may scan into, newest
    first. A draft has no number until it posts, so the scan screens are
    reached from here (or from the draft's own page), not from its number.
    """
    found = []
    for document in [*_inventory_documents(), *SCAN_DOCUMENTS]:
        rows = readable(document.viewset(), request)
        if rows is None:
            continue
        for row in rows.filter(**document.open).order_by("-pk")[:limit]:
            answer = _document_row(document, row, request.user)
            if answer["actions"]:
                found.append(answer)
    return found


def goods_named(code, request, warehouse=None):
    """
    (items, lots) `code` names that the person may read: what a scan into
    a document reads, through the one resolver. A bin's code is not goods.
    """
    from .models import Item, Lot

    found = resolve(code, request)
    items = list(Item.objects.filter(pk__in=[row["id"] for row in found if row["kind"] == "item"]).order_by("pk"))
    lots = list(Lot.objects.filter(pk__in=[row["id"] for row in found if row["kind"] == "lot"])
                .select_related("item").order_by("pk"))
    return items, lots


def bin_named(code, request, warehouse):
    """The bin of `warehouse` coded `code` that the person may read, or a refusal in words."""
    from .bins import StorageBin

    ids = [row["id"] for row in resolve(code, request) if row["kind"] == "bin" and row["warehouse"] == warehouse.pk]
    found = StorageBin.objects.filter(pk__in=ids).first()
    if found is None:
        raise ValidationError(f"{warehouse} has no bin coded {code}.")
    return found


def one_of(code, items, lots, wanted_items=None):
    """
    (item, lot) for a scanned code: a batch if the code is one, else an
    item. Where the code is a batch of several items, the one the document
    wants (`wanted_items`, item pks); refused in words when it is nothing,
    or still two things.
    """
    if wanted_items is not None and lots:
        mine = [lot for lot in lots if lot.item_id in wanted_items]
        lots = mine or lots
    if wanted_items is not None and items:
        mine = [item for item in items if item.pk in wanted_items]
        items = mine or items
    if len(lots) == 1 and all(item.pk == lots[0].item_id for item in items):
        # A batch is the more particular answer: batch B of item B is batch B.
        return lots[0].item, lots[0]
    if len(items) == 1 and not lots:
        return items[0], None
    if not items and not lots:
        raise ValidationError(f"Nothing you may read is coded {code}.")
    named = [f"item {item.sku}" for item in items] + [f"batch {lot.code} of {lot.item.sku}" for lot in lots]
    raise ValidationError(f"{code} is {' and '.join(named)}; type which.")


# --- Holding a draft --------------------------------------------------------------------------------------------

def hold_draft(document, fixed, refused):
    """
    Hold `document` (lock_rows, which re-reads it) and refuse in words if
    `fixed(document)` now says it takes no change: a scan into a receipt
    posted while the clerk was scanning is refused, never applied.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("hold_draft() outside a transaction holds nothing.")
    lock_rows(document)
    if fixed(document):
        raise ValidationError(refused(document) if callable(refused) else refused)


class ScanKey(models.Model):
    """
    A scan's key, made on the device, recorded once: a scan sent twice (a
    retry after the network came back, a second tab) is answered with what
    the first did and counts once. Kept with the document and the line the
    scan wrote, so the repeat answers the same line.
    """

    key = models.CharField(max_length=64, unique=True)
    document = models.CharField(max_length=64, help_text="app_label.model of the document scanned into.")
    document_id = models.BigIntegerField()
    line = models.CharField(max_length=64, blank=True)
    line_id = models.BigIntegerField(null=True, blank=True)
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        app_label = "inventory"
        ordering = ["id"]

    def __str__(self):
        return self.key

    def delete(self, *args, **kwargs):
        raise ValidationError("A scan's key is kept: deleted, the scan it stands for would count again when resent.")


def once(key, document, work):
    """
    Run `work()` -> line once for `key` on `document`, already held by
    the caller. A key seen before answers the line it wrote then (None if
    it has gone since) and does nothing; a key seen on another document is
    refused. No key: the scan is applied (receive, pick, issue, transfer
    are online and their screens send none).
    """
    if not key:
        return work(), False
    key = str(key)[:64]
    label = document._meta.label_lower
    seen = ScanKey.objects.filter(key=key).first()
    if seen is not None:
        if (seen.document, seen.document_id) != (label, document.pk):
            raise ValidationError("This scan was already recorded on another document.")
        line = None
        if seen.line:
            app_label, model_name = seen.line.split(".")
            from django.apps import apps

            line = apps.get_model(app_label, model_name).objects.filter(pk=seen.line_id).first()
        return line, True
    line = work()
    ScanKey.objects.create(key=key, document=label, document_id=document.pk,
                           line=line._meta.label_lower if line is not None else "",
                           line_id=line.pk if line is not None else None)
    return line, False


# --- Which batch may be picked ----------------------------------------------------------------------------------

def shown(value):
    return format(Decimal(value).normalize(), "f")


def pick_refusal(item, lot, warehouse, named=None, on_date=None, let_through=None, action="ship"):
    """
    Why `lot` may not be the batch a draft line of `item` draws from
    `warehouse`, in words, or "". In this order: a batch of another item;
    a batch the release gate holds (apps.quality.release, through
    tracking.lot_held); a batch expired; a batch with nothing free; a
    batch first-expired-first-out would not take yet, naming the one it
    would. `named` ({lot pk: stock quantity}) is what the document's other
    lines already draw, so FEFO looks past what they took.

    The same primitives allocate() reads: free_lots_at, lot_held, has_expired.
    """
    from .tracking import free_lots_at, lot_held

    if lot.item_id != item.pk:
        return f"Batch {lot.code} is {lot.item.sku}, not {item.sku}."
    said = lot_held(item, lot, warehouse, action=action)
    if said and not (let_through and let_through(item, lot)):
        return said
    on_date = to_date(on_date) or timezone.localdate()
    if lot.has_expired(on_date):
        return f"Batch {lot.code} expired on {lot.expires_on:%d-%m-%Y}."
    named = named or {}
    first, free_here = None, Decimal("0")
    for each, free, _promised in free_lots_at(item, warehouse):
        free -= named.get(each.pk, Decimal("0"))
        if each.pk == lot.pk:
            free_here = free
        if free <= 0 or each.has_expired(on_date):
            continue
        if each.pk != lot.pk and lot_held(item, each, warehouse, action=action):
            continue
        if first is None:
            first = each
    if free_here <= 0:
        return f"Batch {lot.code} has nothing free in {warehouse}."
    if first is not None and first.pk != lot.pk and first.expires_on != lot.expires_on:
        when = f"expires {first.expires_on:%d-%m-%Y}" if first.expires_on else "came in first"
        mine = f"expires {lot.expires_on:%d-%m-%Y}" if lot.expires_on else "does not expire"
        return f"First expired, first out: take batch {first.code} first ({when}); {lot.code} {mine}."
    return ""


def free_for_pick(lot, warehouse, named=None):
    """What of `lot` in `warehouse` nothing else holds, less what this document's other lines name."""
    from .tracking import committed_elsewhere

    return lot.on_hand_at(warehouse) - committed_elsewhere(lot, warehouse) - (named or {}).get(lot.pk, Decimal("0"))


def assign_batch(line, lot, quantity_field, uom, warehouse, named, split):
    """
    Name `lot` on draft `line`, which draws `quantity_field` in `uom`. All
    of it if the batch holds enough; else, where the line's unit is the
    stocking unit, the batch's free quantity is split onto a line of its
    own (`split(line, quantity)` makes it) and the rest stays unnamed for
    the next scan. Returns the line that now names the batch.
    """
    item = lot.item
    wanted = getattr(line, quantity_field)
    wanted_stock = item.to_stock_quantity(wanted, uom)
    free = free_for_pick(lot, warehouse, named)
    if free >= wanted_stock:
        line.lot = lot
        line.save()
        return line
    if uom.pk != item.uom_id:
        raise ValidationError(
            f"Batch {lot.code} has {shown(free)} {item.uom} free in {warehouse}; the line wants "
            f"{shown(wanted)} {uom}. Split the line, then scan.")
    setattr(line, quantity_field, wanted - free)
    line.save()
    return split(line, free)


def release_batch(line, quantity_field, waiting):
    """
    The reverse of assign_batch, before posting: `line` names no batch
    again. Where a scan split it off `waiting` (the line of the same goods
    that still names none), its quantity goes back there and it goes;
    otherwise it keeps its quantity and loses the batch. Removing the line
    instead would have taken its quantity off the document with it.
    """
    if line.lot_id is None:
        raise ValidationError("This line names no batch.")
    if waiting is not None:
        # Both rows held in the lock order before either is written: the sentinel saw the later line
        # deleted before the earlier one was saved.
        lock_rows(line, waiting)
        setattr(waiting, quantity_field, getattr(waiting, quantity_field) + getattr(line, quantity_field))
        line.delete()
        waiting.save()
        return waiting
    line.lot = None
    line.save()
    return line


# --- Inventory's own documents ----------------------------------------------------------------------------------

def scan_into_count(count, request, code, quantity, bin_code=None, key=None):
    """
    Count what was scanned in a bin: a line for the item (its batch, the
    bin) counted up by `quantity`, or put on the sheet with it.
    """
    from .adjustments import StockCountLine

    quantity = positive_quantity(quantity)
    with transaction.atomic():
        hold_draft(count, lambda held: held.posted, lambda held: f"{held.number} was posted meanwhile; "
                   "a scan does not change a posted count.")

        def work():
            items, lots = goods_named(code, request)
            item, lot = one_of(code, items, lots)
            storage_bin = bin_named(bin_code, request, count.warehouse) if bin_code else None
            line = StockCountLine.objects.filter(count=count, item=item, lot=lot, bin=storage_bin).first()
            if line is None:
                return count.add(item, quantity, lot=lot, storage_bin=storage_bin)
            line.counted_quantity += quantity
            line.save()
            return line

        return once(key, count, work)


def scan_into_transfer(transfer, request, code, stage, quantity=None, from_bin=None, key=None):
    """
    A transfer by scan: `stage` "from" reads the bin it leaves, "goods" a
    batch or an item counted onto a line from that bin, "to" the bin it
    goes to, which every line scanned without one takes.
    """
    from .transfers import StockTransferLine, TransferStatus

    with transaction.atomic():
        hold_draft(transfer, lambda held: held.status != TransferStatus.DRAFT,
                   lambda held: f"{held.number} has moved stock since; a scan does not change it.")
        if stage == "from":
            return {"bin": bin_named(code, request, transfer.from_warehouse)}
        if stage == "to":
            to_bin = bin_named(code, request, transfer.to_warehouse)
            lines = list(transfer.lines.filter(to_bin__isnull=True).order_by("pk"))
            if not lines:
                raise ValidationError(f"Every line on {transfer.number} already names where it goes.")
            for line in lines:
                line.to_bin = to_bin
                line.save()
            return {"bin": to_bin, "lines": lines}
        quantity = positive_quantity(quantity)
        source = bin_named(from_bin, request, transfer.from_warehouse) if from_bin else None

        def work():
            items, lots = goods_named(code, request)
            item, lot = one_of(code, items, lots)
            if item.tracking != "none" and lot is None:
                raise ValidationError(f"{item} is kept by batch; scan the batch label.")
            line = StockTransferLine.objects.filter(transfer=transfer, item=item, lot=lot, from_bin=source,
                                                    to_bin__isnull=True).order_by("pk").first()
            if line is None:
                return StockTransferLine.objects.create(transfer=transfer, item=item, lot=lot, from_bin=source,
                                                        quantity=quantity)
            line.quantity += quantity
            line.save()
            return line

        line, _again = once(key, transfer, work)
        return {"line": line}


def positive_quantity(quantity):
    try:
        quantity = Decimal(str(quantity if quantity not in (None, "") else "1"))
    except Exception:
        raise ValidationError(f"{quantity} is not a quantity.")
    if quantity <= 0:
        raise ValidationError("A scan counts a quantity above nothing.")
    return quantity


# --- Labels -----------------------------------------------------------------------------------------------------

def label_sheet(title, rows):
    """
    A printable page of Code 128 labels, one per (code, caption), drawn by
    apps.core.barcode on the server so a label prints with the internet
    down.
    """
    from apps.core import barcode

    cells = "".join(
        f'<figure class="label">{barcode.svg(code, height=50)}<figcaption><b>{escape(code)}</b>'
        f'<span>{escape(caption)}</span></figcaption></figure>' for code, caption in rows)
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>" + escape(title) + "</title><style>"
        "body{font-family:sans-serif;margin:8mm}h1{font-size:12pt}"
        ".sheet{display:grid;grid-template-columns:repeat(3,1fr);gap:4mm}"
        ".label{border:1px dashed #999;margin:0;padding:3mm;break-inside:avoid}"
        ".label svg{width:100%;height:16mm}figcaption{display:flex;flex-direction:column;font-size:9pt}"
        "</style></head><body><h1>" + escape(title) + "</h1><div class=\"sheet\">" + cells
        + ("" if rows else "<p>No labels to print.</p>") + "</div></body></html>")
