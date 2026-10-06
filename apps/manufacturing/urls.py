from rest_framework.routers import DefaultRouter

from . import views
from .station_views import LoomStationViewSet, ScaleReadingViewSet, StationReportViewSet
from .complaint_views import ComplaintViewSet, CorrectiveActionViewSet
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
    CostSheetViewSet,
    TestCertificateViewSet,
    EnergyMeterViewSet,
    BaleViewSet,
    CustomerMaterialReceiptLineViewSet,
    CustomerMaterialReceiptViewSet,
    CustomerMaterialReturnLineViewSet,
    CustomerMaterialReturnViewSet,
    EnergyTariffViewSet,
    MeterReadingViewSet,
    DispatchViewSet,
    MaterialRateViewSet,
    QuotePolicyViewSet,
    StageRateViewSet,
    JobWorkChallanViewSet,
    JobWorkLineViewSet,
    JobWorkLossViewSet,
    LotTraceViewSet,
    OperatorYieldViewSet,
    RoutingOperationViewSet,
    RoutingViewSet,
    ShiftViewSet,
    TapeSpecificationViewSet,
    FilmSpecificationViewSet,
    AlternateRoutingViewSet,
    CrewAssignmentViewSet,
    OrderProfitabilityViewSet,
    LinerSpecificationViewSet,
    TimeBookingViewSet,
    ChangeoverRuleViewSet,
    MachineViewSet,
    SetupFamilyViewSet,
    WorkCentreViewSet,
    WorkOrderViewSet,
)

router = DefaultRouter()
router.register("tape-specifications", TapeSpecificationViewSet)
router.register("film-specifications", FilmSpecificationViewSet)
router.register("liner-specifications", LinerSpecificationViewSet)
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
router.register("alternate-routings", AlternateRoutingViewSet)
router.register("crew-assignments", CrewAssignmentViewSet)
router.register("order-profitability", OrderProfitabilityViewSet,
                basename="order-profitability")
router.register("machines", MachineViewSet)
router.register("setup-families", SetupFamilyViewSet)
router.register("changeover-rules", ChangeoverRuleViewSet)
router.register("work-centres", WorkCentreViewSet)
router.register("maintenance-schedules", MaintenanceScheduleViewSet)
router.register("maintenance-jobs", MaintenanceJobViewSet)
router.register("work-orders", WorkOrderViewSet)
router.register("material-issues", MaterialIssueViewSet)
router.register("material-issue-lines", MaterialIssueLineViewSet)
router.register("production-entries", ProductionEntryViewSet)
router.register("production-byproducts", ProductionByproductViewSet)
router.register("time-bookings", TimeBookingViewSet)
router.register("work-order-operations", views.WorkOrderOperationViewSet)
router.register("shifts", ShiftViewSet)
router.register("downtime-reasons", DowntimeReasonViewSet)
router.register("scrap-reasons", views.ScrapReasonViewSet)
router.register("production-scrap", views.ProductionScrapViewSet)
router.register("operation-reports", views.OperationReportViewSet)
router.register("run-flow", views.RunFlowViewSet, basename="run-flow")
router.register("rebatches", views.RebatchViewSet)
router.register("downtime", DowntimeViewSet)
router.register("operator-yield", OperatorYieldViewSet, basename="operator-yield")
router.register("lot-trace", LotTraceViewSet, basename="lot-trace")
router.register("stations", LoomStationViewSet, basename="station")
router.register("scale-readings", ScaleReadingViewSet, basename="scale-reading")
router.register("job-work-challans", JobWorkChallanViewSet)
router.register("job-work-lines", JobWorkLineViewSet)
router.register("job-work-losses", JobWorkLossViewSet)
router.register("dispatch", DispatchViewSet, basename="dispatch")
router.register("material-rates", MaterialRateViewSet)
router.register("stage-rates", StageRateViewSet)
router.register("quote-policies", QuotePolicyViewSet)
router.register("cost-sheets", CostSheetViewSet)
router.register("test-certificates", TestCertificateViewSet)
router.register("energy-meters", EnergyMeterViewSet)
router.register("bales", BaleViewSet)
router.register("customer-material-receipts", CustomerMaterialReceiptViewSet)
router.register("customer-material-receipt-lines", CustomerMaterialReceiptLineViewSet)
router.register("customer-material-returns", CustomerMaterialReturnViewSet)
router.register("customer-material-return-lines", CustomerMaterialReturnLineViewSet)
router.register("meter-readings", MeterReadingViewSet)
router.register("energy-tariffs", EnergyTariffViewSet)
router.register("station-reports", StationReportViewSet, basename="station-report")
router.register("complaints", ComplaintViewSet)
router.register("corrective-actions", CorrectiveActionViewSet)

urlpatterns = router.urls
