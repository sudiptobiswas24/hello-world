"""
The price variation clause over the API.

price-indices/ with {id}/values/ to publish a value; price-clauses/ on
an order line; price-variation-bills/ — GET preview/?order&start&end
for what a bill would say, POST {order, start, end, receivable_account,
invoice_date?} to write one, {id}/cancel/ while its documents are
drafts.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.accounting.models import Account
from apps.core.audit import AuditableViewSetMixin

from .models import SalesOrder
from .price_variation import (
    PriceIndex,
    PriceIndexValue,
    PriceVariationBill,
    PriceVariationClause,
    bill_variation,
    rows_for,
)
from .scoping import CustomerScopedMixin


def _run(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except DjangoValidationError as exc:
        raise DRFValidationError(exc.messages)


def _dates(data):
    start, end = parse_date(str(data.get("start") or "")), parse_date(str(data.get("end") or ""))
    if start is None or end is None:
        raise DRFValidationError(["start and end are required, as YYYY-MM-DD."])
    return start, end


class PriceIndexSerializer(serializers.ModelSerializer):
    class Meta:
        model = PriceIndex
        fields = ["id", "code", "name"]


class PriceClauseSerializer(serializers.ModelSerializer):
    class Meta:
        model = PriceVariationClause
        fields = ["id", "order_line", "index", "base_value", "polymer_kg_per_unit",
                  "pass_through_percent", "threshold_percent"]


def _row(row):
    return {"delivery": row["delivery"].number, "order_line": row["order_line"].pk,
            "quantity": str(row["quantity"]), "index_value": str(row["index_value"]),
            "variation_per_unit": str(row["variation_per_unit"]), "amount": str(row["amount"])}


def _bill(bill):
    return {
        "id": bill.pk, "number": bill.number, "order": bill.order.number,
        "start": bill.start, "end": bill.end, "total": str(bill.total()),
        "invoice": bill.invoice_id, "credit_note": bill.credit_note_id,
        "cancelled": bill.cancelled_at is not None,
        "lines": [_row({"delivery": line.delivery, "order_line": line.order_line,
                        "quantity": line.quantity, "index_value": line.index_value,
                        "variation_per_unit": line.variation_per_unit, "amount": line.amount})
                  for line in bill.lines.select_related("delivery", "order_line")],
    }


class PriceIndexViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PriceIndex.objects.all()
    serializer_class = PriceIndexSerializer
    http_method_names = ["get", "post", "head", "options"]
    action_permission_map = {"values": "sales.add_priceindexvalue"}

    @action(detail=True, methods=["get", "post"])
    def values(self, request, pk=None):
        index = self.get_object()
        if request.method == "POST":
            valid_from = parse_date(str(request.data.get("valid_from") or ""))
            try:
                value = Decimal(str(request.data.get("value")))
            except (InvalidOperation, TypeError, ValueError):
                value = None
            if valid_from is None or value is None or not value.is_finite() or value <= 0:
                raise DRFValidationError(["valid_from is a date and value a positive number."])
            _run(PriceIndexValue.objects.create, index=index, valid_from=valid_from,
                 value=value)
        return Response([{"valid_from": row.valid_from, "value": str(row.value)}
                         for row in index.values.all()],
                        status=201 if request.method == "POST" else 200)


class PriceClauseViewSet(CustomerScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    customer_path = "order_line__order__customer"
    queryset = PriceVariationClause.objects.select_related("index", "order_line")
    serializer_class = PriceClauseSerializer


class PriceVariationBillViewSet(CustomerScopedMixin, AuditableViewSetMixin, viewsets.GenericViewSet):
    customer_path = "order__customer"
    queryset = PriceVariationBill.objects.select_related("order")
    action_permission_map = {"cancel": "sales.change_pricevariationbill",
                             "preview": "sales.view_pricevariationbill"}

    def list(self, request):
        return Response([_bill(bill) for bill in self.get_queryset()[:100]])

    def retrieve(self, request, pk=None):
        return Response(_bill(self.get_object()))

    def create(self, request):
        data = request.data
        order = get_object_or_404(SalesOrder, pk=data.get("order"))
        account = get_object_or_404(Account, pk=data.get("receivable_account"))
        start, end = _dates(data)
        bill = _run(bill_variation, order, start, end, account,
                    invoice_date=parse_date(str(data.get("invoice_date") or "")))
        return Response(_bill(bill), status=201)

    @action(detail=False, methods=["get"])
    def preview(self, request):
        order = get_object_or_404(SalesOrder, pk=request.query_params.get("order"))
        start, end = _dates(request.query_params)
        return Response([_row(row) for row in _run(rows_for, order, start, end)])

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        bill = self.get_object()
        _run(bill.cancel)
        return Response(_bill(bill))
