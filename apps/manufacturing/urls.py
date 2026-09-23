from rest_framework.routers import DefaultRouter

from .views import (
    BomSubstituteViewSet,
    CostVersionViewSet,
    StandardCostViewSet,
    MaintenanceJobViewSet,
    MaintenanceScheduleViewSet,
    FabricRollViewSet,
    PrintDesignViewSet,
    ToolUsageViewSet,
    ToolViewSet,
    BagSpecificationViewSet,
    BillOfMaterialsViewSet,
    BomByproductViewSet,
    BomComponentViewSet,
    FabricSpecificationViewSet,
    MaterialIssueLineViewSet,
    MaterialIssueViewSet,
    ProductionByproductViewSet,
    ProductionEntryViewSet,
    DowntimeReasonViewSet,
    DowntimeViewSet,
    OperatorYieldViewSet,
    RoutingOperationViewSet,
    RoutingViewSet,
    ShiftViewSet,
    TapeSpecificationViewSet,
    TimeBookingViewSet,
    MachineViewSet,
    WorkCentreViewSet,
    WorkOrderViewSet,
)

router = DefaultRouter()
router.register("tape-specifications", TapeSpecificationViewSet)
router.register("fabric-specifications", FabricSpecificationViewSet)
router.register("bag-specifications", BagSpecificationViewSet)
router.register("boms", BillOfMaterialsViewSet)
router.register("cost-versions", CostVersionViewSet)
router.register("standard-costs", StandardCostViewSet)
router.register("bom-components", BomComponentViewSet)
router.register("bom-byproducts", BomByproductViewSet)
router.register("bom-substitutes", BomSubstituteViewSet)
router.register("fabric-rolls", FabricRollViewSet)
router.register("print-designs", PrintDesignViewSet)
router.register("tools", ToolViewSet)
router.register("tool-usage", ToolUsageViewSet)
router.register("routings", RoutingViewSet)
router.register("routing-operations", RoutingOperationViewSet)
router.register("machines", MachineViewSet)
router.register("work-centres", WorkCentreViewSet)
router.register("maintenance-schedules", MaintenanceScheduleViewSet)
router.register("maintenance-jobs", MaintenanceJobViewSet)
router.register("work-orders", WorkOrderViewSet)
router.register("material-issues", MaterialIssueViewSet)
router.register("material-issue-lines", MaterialIssueLineViewSet)
router.register("production-entries", ProductionEntryViewSet)
router.register("production-byproducts", ProductionByproductViewSet)
router.register("time-bookings", TimeBookingViewSet)
router.register("shifts", ShiftViewSet)
router.register("downtime-reasons", DowntimeReasonViewSet)
router.register("downtime", DowntimeViewSet)
router.register("operator-yield", OperatorYieldViewSet, basename="operator-yield")

urlpatterns = router.urls
