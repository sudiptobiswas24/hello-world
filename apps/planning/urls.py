from rest_framework.routers import DefaultRouter

from .views import (
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

urlpatterns = router.urls
