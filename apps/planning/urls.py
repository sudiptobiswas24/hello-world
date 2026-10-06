from rest_framework.routers import DefaultRouter

from .history_api import ShipmentHistoryViewSet
from .views import (
    MasterScheduleViewSet,
    ForecastViewSet,
    TransferRouteViewSet,
    LowLevelCodeViewSet,
    PlanningActionViewSet,
    PromiseViewSet,
    PlannedDemandViewSet,
    PlannedOrderViewSet,
    PlanningRunViewSet,
    PlanningSettingsViewSet,
)

router = DefaultRouter()
router.register("settings", PlanningSettingsViewSet)
router.register("forecasts", ForecastViewSet)
router.register("transfer-routes", TransferRouteViewSet)
router.register("runs", PlanningRunViewSet)
router.register("planned-orders", PlannedOrderViewSet)
router.register("planned-demands", PlannedDemandViewSet)
router.register("actions", PlanningActionViewSet)
router.register("levels", LowLevelCodeViewSet, basename="levels")
router.register("promise", PromiseViewSet, basename="promise")
router.register("master-schedule", MasterScheduleViewSet)
router.register("shipment-history", ShipmentHistoryViewSet)

urlpatterns = router.urls
