from rest_framework import viewsets

from apps.core.audit import AuditableViewSetMixin

from .models import Item, StockMovement, Warehouse
from .serializers import ItemSerializer, StockMovementSerializer, WarehouseSerializer


class WarehouseViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Warehouse.objects.all()
    serializer_class = WarehouseSerializer


class ItemViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Item.objects.all()
    serializer_class = ItemSerializer


class StockMovementViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = StockMovement.objects.all()
    serializer_class = StockMovementSerializer
