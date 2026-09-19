from rest_framework.routers import DefaultRouter

from .views import (
    BillLineViewSet,
    BillViewSet,
    PurchaseOrderLineViewSet,
    PurchaseOrderViewSet,
)

router = DefaultRouter()
router.register("purchase-orders", PurchaseOrderViewSet)
router.register("purchase-order-lines", PurchaseOrderLineViewSet)
router.register("bills", BillViewSet)
router.register("bill-lines", BillLineViewSet)

urlpatterns = router.urls
