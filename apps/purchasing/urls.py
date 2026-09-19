from rest_framework.routers import DefaultRouter

from .views import (
    BillLineViewSet,
    BillViewSet,
    GoodsReceiptLineViewSet,
    GoodsReceiptViewSet,
    PurchaseOrderLineViewSet,
    PurchaseOrderViewSet,
)

router = DefaultRouter()
router.register("purchase-orders", PurchaseOrderViewSet)
router.register("purchase-order-lines", PurchaseOrderLineViewSet)
router.register("bills", BillViewSet)
router.register("bill-lines", BillLineViewSet)
router.register("goods-receipts", GoodsReceiptViewSet)
router.register("goods-receipt-lines", GoodsReceiptLineViewSet)

urlpatterns = router.urls
