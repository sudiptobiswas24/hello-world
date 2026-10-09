"""
The purchasing documents that come before an order: a requisition asking
for something, a request for quotation putting it to several vendors,
and a blanket order agreeing a price and volume to be called off. Each
was modelled with its steps, and reachable only from the admin.

Each step is the model's own method; the views read the request, call
it, and answer with the document as it now stands.
"""

from decimal import Decimal

from django.shortcuts import get_object_or_404
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.accounting.serializers import MoneyLineSerializerMixin
from apps.core.api import flag, quantities_by_line
from apps.core.audit import AuditableViewSetMixin
from apps.core.models import Party

from .models import (
    BlanketOrder,
    BlanketOrderLine,
    PurchaseOrder,
    PurchaseRequisition,
    PurchaseRequisitionLine,
    RequestForQuotation,
    RfqInvitation,
    RfqLine,
    RfqQuote,
)


def _item_label(item):
    return f"{item.sku} · {item.name}"


def _quantity(value):
    """A summed quantity at the four places the column holds: PostgreSQL gives a sum at the
    column's scale and SQLite as it falls, and the screens should not tell them apart."""
    return Decimal(value).quantize(Decimal("0.0001"))


def _order_made(request, order):
    """An order made from another document is the clicker's: it is who raised it, and so who does not approve it."""
    PurchaseOrder.objects.filter(pk=order.pk, created_by__isnull=True).update(created_by=request.user)
    return Response({"order": order.pk, "number": order.number}, status=201)


def _rfq_made(rfq):
    return Response({"rfq": rfq.pk, "number": rfq.number}, status=201)


def _named_lines(requisition, data):
    """The requisition's lines `data["lines"]` names, or None for all of them."""
    if not data.get("lines"):
        return None
    wanted = {str(line_id) for line_id in data["lines"]}
    lines = [line for line in requisition.lines.all() if str(line.pk) in wanted]
    if len(lines) != len(wanted):
        raise DRFValidationError({"lines": ["A line named is not on this requisition."]})
    return lines


# --- requisitions

class PurchaseRequisitionLineSerializer(serializers.ModelSerializer):
    item_label = serializers.SerializerMethodField()
    quantity_ordered = serializers.SerializerMethodField()
    quantity_quoting = serializers.SerializerMethodField()
    quantity_open = serializers.SerializerMethodField()
    estimated_value = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseRequisitionLine
        fields = ["id", "requisition", "item", "uom", "quantity", "estimated_price", "expense_account",
                  "suggested_vendor", "notes", "warehouse", "item_label", "quantity_ordered",
                  "quantity_quoting", "quantity_open", "estimated_value"]

    def get_item_label(self, obj):
        return _item_label(obj.item)

    def get_quantity_ordered(self, obj):
        return _quantity(obj.quantity_ordered())

    def get_quantity_quoting(self, obj):
        return _quantity(obj.quantity_quoting())

    def get_quantity_open(self, obj):
        return _quantity(obj.quantity_open())

    def get_estimated_value(self, obj):
        return obj.estimated_value()


class PurchaseRequisitionSerializer(serializers.ModelSerializer):
    lines = PurchaseRequisitionLineSerializer(many=True, read_only=True)
    requested_by_name = serializers.CharField(source="requested_by.name", read_only=True)
    estimated_total = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseRequisition
        fields = ["id", "number", "requested_by", "request_date", "needed_by", "status", "justification",
                  "decided_by", "decided_at", "decision_note", "lines", "requested_by_name",
                  "estimated_total"]
        read_only_fields = ["status"]

    def get_estimated_total(self, obj):
        return obj.estimated_total()


class PurchaseRequisitionViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PurchaseRequisition.objects.select_related("requested_by").prefetch_related(
        "lines__item", "lines__suggested_vendor")
    serializer_class = PurchaseRequisitionSerializer
    filter_fields = ["status", "requested_by"]
    search_fields = ["number", "requested_by__name", "justification"]
    date_field = "request_date"
    ordering_fields = ["request_date", "number"]
    action_permission_map = {
        "submit": "purchasing.change_purchaserequisition",
        "cancel": "purchasing.change_purchaserequisition",
        "approve": "purchasing.decide_purchaserequisition",
        "reject": "purchasing.decide_purchaserequisition",
        "order": "purchasing.add_purchaseorder",
        "rfq": "purchasing.add_requestforquotation",
    }

    def _answer(self, requisition):
        requisition.refresh_from_db()
        return Response(self.get_serializer(requisition).data)

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        requisition = self.get_object()
        requisition.submit()
        return self._answer(requisition)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        requisition = self.get_object()
        requisition.approve(by=request.user, note=str(request.data.get("note", "")))
        return self._answer(requisition)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        requisition = self.get_object()
        requisition.reject(by=request.user, note=str(request.data.get("note", "")))
        return self._answer(requisition)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        requisition = self.get_object()
        requisition.cancel()
        return self._answer(requisition)

    @action(detail=True, methods=["post"])
    def order(self, request, pk=None):
        """{vendor, order_date?, lines?: [ids]}: what is left of it, on one order to one vendor."""
        requisition = self.get_object()
        vendor = get_object_or_404(Party, pk=request.data.get("vendor"))
        return _order_made(request, requisition.create_order(vendor, order_date=request.data.get("order_date"),
                                                    lines=_named_lines(requisition, request.data)))

    @action(detail=True, methods=["post"])
    def rfq(self, request, pk=None):
        """{response_due?, issue_date?, lines?: [ids]}: what is left of it, out for quotes on one request."""
        requisition = self.get_object()
        rfq = requisition.create_rfq(lines=_named_lines(requisition, request.data),
                                     issue_date=request.data.get("issue_date"),
                                     response_due=request.data.get("response_due"))
        return _rfq_made(rfq)


class PurchaseRequisitionLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PurchaseRequisitionLine.objects.select_related("item", "requisition")
    serializer_class = PurchaseRequisitionLineSerializer
    filter_fields = ["requisition", "item"]


# --- requests for quotation

class RfqLineSerializer(serializers.ModelSerializer):
    item_label = serializers.SerializerMethodField()
    requisition_number = serializers.CharField(source="requisition_line.requisition.number", read_only=True, default="")

    class Meta:
        model = RfqLine
        fields = ["id", "rfq", "item", "uom", "quantity", "requisition_line", "notes", "item_label", "requisition_number"]

    def get_item_label(self, obj):
        return _item_label(obj.item)


class RfqQuoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = RfqQuote
        fields = ["id", "line", "unit_price", "lead_time_days", "notes"]


class RfqInvitationSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    quotes = RfqQuoteSerializer(many=True, read_only=True)

    class Meta:
        model = RfqInvitation
        fields = ["id", "rfq", "vendor", "sent_at", "responded_at", "declined", "awarded", "notes",
                  "vendor_name", "quotes"]
        read_only_fields = ["responded_at", "declined", "awarded"]


class RequestForQuotationSerializer(serializers.ModelSerializer):
    lines = RfqLineSerializer(many=True, read_only=True)
    invited = RfqInvitationSerializer(many=True, read_only=True)
    requisition_number = serializers.CharField(source="requisition.number", read_only=True, default="")

    class Meta:
        model = RequestForQuotation
        fields = ["id", "number", "requisition", "requisition_number", "issue_date", "response_due", "currency",
                  "description", "status", "lines", "invited"]
        read_only_fields = ["status"]


class RequestForQuotationViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = RequestForQuotation.objects.select_related("requisition").prefetch_related(
        "lines__item", "lines__requisition_line__requisition", "invited__vendor", "invited__quotes")
    serializer_class = RequestForQuotationSerializer
    filter_fields = ["status", "requisition"]
    search_fields = ["number", "description", "invited__vendor__name"]
    date_field = "issue_date"
    ordering_fields = ["issue_date", "number"]
    action_permission_map = {
        "issue": "purchasing.change_requestforquotation",
        "cancel": "purchasing.change_requestforquotation",
        "quote": "purchasing.change_rfqinvitation",
        "award": "purchasing.add_purchaseorder",
    }

    def _answer(self, rfq):
        return Response(self.get_serializer(self.get_queryset().get(pk=rfq.pk)).data)

    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        rfq = self.get_object()
        rfq.issue()
        return self._answer(rfq)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        rfq = self.get_object()
        rfq.cancel()
        return self._answer(rfq)

    @action(detail=True, methods=["post"])
    def quote(self, request, pk=None):
        """{invitation, line, unit_price, lead_time_days?, notes?}: what one vendor said one line costs."""
        rfq = self.get_object()
        invitation = get_object_or_404(RfqInvitation, pk=request.data.get("invitation"), rfq=rfq)
        line = get_object_or_404(RfqLine, pk=request.data.get("line"), rfq=rfq)
        lead = request.data.get("lead_time_days")
        invitation.quote(line, request.data.get("unit_price"),
                         lead_time_days=int(lead) if lead not in (None, "") else None,
                         notes=str(request.data.get("notes", "")))
        return self._answer(rfq)

    @action(detail=True, methods=["get"])
    def comparison(self, request, pk=None):
        """Each line's quotes side by side, the cheapest flagged and nobody chosen."""
        rfq = self.get_object()
        totals = {invitation.pk: total for invitation, total in rfq.vendor_totals().items()}
        return Response({
            "lines": [{
                "line": row["line"].pk, "item": _item_label(row["item"]), "quantity": row["quantity"],
                "quotes": [{
                    "invitation": quote["invitation"].pk, "vendor": quote["vendor"].name,
                    "unit_price": quote["unit_price"], "total": quote["total"],
                    "lead_time_days": quote["lead_time_days"], "is_cheapest": quote["is_cheapest"],
                } for quote in row["quotes"]],
                "missing": [vendor.name for vendor in row["missing"]],
            } for row in rfq.comparison()],
            # Only a vendor who quoted every line has a total.
            "totals": [{"invitation": invitation.pk, "vendor": invitation.vendor.name,
                        "total": totals.get(invitation.pk)} for invitation in rfq.invited.all()],
        })

    @action(detail=True, methods=["post"])
    def award(self, request, pk=None):
        """{invitation, order_date?, record_prices?}: the business to one vendor, at the prices they quoted."""
        rfq = self.get_object()
        invitation = get_object_or_404(RfqInvitation, pk=request.data.get("invitation"), rfq=rfq)
        return _order_made(request, rfq.award(invitation, order_date=request.data.get("order_date"),
                                     record_prices=flag(request.data, "record_prices", False)))


class RfqLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = RfqLine.objects.select_related("item", "rfq")
    serializer_class = RfqLineSerializer
    filter_fields = ["rfq", "item"]


class RfqInvitationViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = RfqInvitation.objects.select_related("vendor", "rfq").prefetch_related("quotes")
    serializer_class = RfqInvitationSerializer
    filter_fields = ["rfq", "vendor", "declined", "awarded"]
    action_permission_map = {"decline": "purchasing.change_rfqinvitation"}

    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        invitation = self.get_object()
        invitation.decline(str(request.data.get("note", "")))
        return Response(self.get_serializer(invitation).data)


# --- blanket orders

class BlanketOrderLineSerializer(MoneyLineSerializerMixin, serializers.ModelSerializer):
    item_label = serializers.SerializerMethodField()
    quantity_released = serializers.SerializerMethodField()
    quantity_remaining = serializers.SerializerMethodField()

    class Meta:
        model = BlanketOrderLine
        fields = ["id", "blanket", "item", "uom", "quantity", "unit_price", "discount_percent", "taxes",
                  "item_label", "quantity_released", "quantity_remaining",
                  "gross_amount", "discount_amount", "net_amount", "tax_total", "total"]

    def get_item_label(self, obj):
        return _item_label(obj.item)

    def get_quantity_released(self, obj):
        return _quantity(obj.quantity_released())

    def get_quantity_remaining(self, obj):
        return _quantity(obj.quantity_remaining())


class BlanketOrderSerializer(serializers.ModelSerializer):
    lines = BlanketOrderLineSerializer(many=True, read_only=True)
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    total = serializers.SerializerMethodField()

    class Meta:
        model = BlanketOrder
        fields = ["id", "number", "vendor", "reference", "start_date", "end_date", "currency", "status",
                  "lines", "vendor_name", "total"]
        read_only_fields = ["status"]

    def get_total(self, obj):
        return obj.total()


class BlanketOrderViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = BlanketOrder.objects.select_related("vendor", "currency").prefetch_related(
        "lines__item", "lines__taxes")
    serializer_class = BlanketOrderSerializer
    filter_fields = ["status", "vendor"]
    search_fields = ["number", "reference", "vendor__name"]
    date_field = "start_date"
    ordering_fields = ["start_date", "number"]
    action_permission_map = {
        "confirm": "purchasing.change_blanketorder",
        "close": "purchasing.change_blanketorder",
        "release": "purchasing.add_purchaseorder",
    }

    def _answer(self, blanket):
        return Response(self.get_serializer(self.get_queryset().get(pk=blanket.pk)).data)

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        blanket = self.get_object()
        blanket.confirm()
        return self._answer(blanket)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        blanket = self.get_object()
        blanket.close()
        return self._answer(blanket)

    @action(detail=True, methods=["post"])
    def release(self, request, pk=None):
        """{quantities: {"<line id>": "300"}, order_date?, expected_date?}: part of it called off as an order."""
        blanket = self.get_object()
        quantities = quantities_by_line(request.data.get("quantities"), blanket.lines.all(), "agreement")
        if not quantities:
            raise DRFValidationError({"quantities": ["Say how much of which line to call off."]})
        return _order_made(request, blanket.release(quantities, order_date=request.data.get("order_date"),
                                           expected_date=request.data.get("expected_date")))


class BlanketOrderLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = BlanketOrderLine.objects.select_related("item", "blanket").prefetch_related("taxes")
    serializer_class = BlanketOrderLineSerializer
    filter_fields = ["blanket", "item"]
