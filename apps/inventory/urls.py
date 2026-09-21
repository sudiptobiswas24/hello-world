from rest_framework.routers import DefaultRouter

from .views import (
    AdjustmentReasonViewSet,
    ItemViewSet,
    LotViewSet,
    StockAdjustmentLineViewSet,
    StockAdjustmentViewSet,
    StockCountLineViewSet,
    StockCountViewSet,
    StockMovementViewSet,
    StockReportViewSet,
    StockReservationViewSet,
    StockTransferLineViewSet,
    StockTransferViewSet,
    StorageBinViewSet,
    WarehouseViewSet,
)

router = DefaultRouter()
router.register("warehouses", WarehouseViewSet)
router.register("items", ItemViewSet)
router.register("stock-movements", StockMovementViewSet)
router.register("lots", LotViewSet)
router.register("bins", StorageBinViewSet)
router.register("adjustment-reasons", AdjustmentReasonViewSet)
router.register("stock-adjustments", StockAdjustmentViewSet)
router.register("stock-adjustment-lines", StockAdjustmentLineViewSet)
router.register("stock-counts", StockCountViewSet)
router.register("stock-count-lines", StockCountLineViewSet)
router.register("stock-transfers", StockTransferViewSet)
router.register("stock-transfer-lines", StockTransferLineViewSet)
router.register("stock-reservations", StockReservationViewSet)
router.register("stock-reports", StockReportViewSet, basename="stock-report")

urlpatterns = router.urls
