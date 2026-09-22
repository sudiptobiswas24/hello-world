from rest_framework.routers import DefaultRouter

from .views import (
    LowLevelCodeViewSet,
    PlannedDemandViewSet,
    PlannedOrderViewSet,
    PlanningRunViewSet,
    PlanningSettingsViewSet,
)

router = DefaultRouter()
router.register("settings", PlanningSettingsViewSet)
router.register("runs", PlanningRunViewSet)
router.register("planned-orders", PlannedOrderViewSet)
router.register("planned-demands", PlannedDemandViewSet)
router.register("levels", LowLevelCodeViewSet, basename="levels")

urlpatterns = router.urls
