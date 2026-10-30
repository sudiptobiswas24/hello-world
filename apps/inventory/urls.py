from rest_framework.routers import DefaultRouter

from .views import (
    AdjustmentReasonViewSet,
    ItemUnitViewSet,
    ItemAttributeValueViewSet,
    ItemAttributeViewSet,
    ItemTemplateViewSet,
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

from .gate_api import GatePassLineViewSet, GatePassViewSet, GateVisitViewSet, WeighingViewSet  # noqa: E402

router = DefaultRouter()
router.register("warehouses", WarehouseViewSet)
router.register("items", ItemViewSet)
router.register("item-units", ItemUnitViewSet)
router.register("item-templates", ItemTemplateViewSet)
router.register("item-attributes", ItemAttributeViewSet)
router.register("item-attribute-values", ItemAttributeValueViewSet)
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
router.register("gate-visits", GateVisitViewSet)
router.register("weighings", WeighingViewSet)
router.register("gate-passes", GatePassViewSet)
router.register("gate-pass-lines", GatePassLineViewSet)
router.register("stock-reports", StockReportViewSet, basename="stock-report")

urlpatterns = router.urls
