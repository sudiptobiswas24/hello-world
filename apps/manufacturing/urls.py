from rest_framework.routers import DefaultRouter

from .views import (
    BagSpecificationViewSet,
    BillOfMaterialsViewSet,
    BomByproductViewSet,
    BomComponentViewSet,
    FabricSpecificationViewSet,
    MaterialIssueLineViewSet,
    MaterialIssueViewSet,
    ProductionByproductViewSet,
    ProductionEntryViewSet,
    TapeSpecificationViewSet,
    WorkCentreViewSet,
    WorkOrderViewSet,
)

router = DefaultRouter()
router.register("tape-specifications", TapeSpecificationViewSet)
router.register("fabric-specifications", FabricSpecificationViewSet)
router.register("bag-specifications", BagSpecificationViewSet)
router.register("boms", BillOfMaterialsViewSet)
router.register("bom-components", BomComponentViewSet)
router.register("bom-byproducts", BomByproductViewSet)
router.register("work-centres", WorkCentreViewSet)
router.register("work-orders", WorkOrderViewSet)
router.register("material-issues", MaterialIssueViewSet)
router.register("material-issue-lines", MaterialIssueLineViewSet)
router.register("production-entries", ProductionEntryViewSet)
router.register("production-byproducts", ProductionByproductViewSet)

urlpatterns = router.urls
