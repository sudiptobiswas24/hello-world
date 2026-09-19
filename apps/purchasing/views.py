from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin

from .models import Bill, BillLine, PurchaseOrder, PurchaseOrderLine
from .serializers import (
    BillLineSerializer,
    BillSerializer,
    PurchaseOrderLineSerializer,
    PurchaseOrderSerializer,
)


class PurchaseOrderViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PurchaseOrder.objects.prefetch_related("lines")
    serializer_class = PurchaseOrderSerializer


class PurchaseOrderLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PurchaseOrderLine.objects.all()
    serializer_class = PurchaseOrderLineSerializer


class BillViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Bill.objects.prefetch_related("lines")
    serializer_class = BillSerializer

    @action(detail=True, methods=["post"])
    def post_bill(self, request, pk=None):
        bill = self.get_object()
        try:
            bill.post(memo=request.data.get("memo"))
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(bill).data)

    @action(detail=True, methods=["post"])
    def debit_note(self, request, pk=None):
        bill = self.get_object()
        try:
            debit_note = bill.create_debit_note(memo=request.data.get("memo", ""))
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(debit_note).data)


class BillLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = BillLine.objects.all()
    serializer_class = BillLineSerializer
