from rest_framework.routers import DefaultRouter

from .views import (
    CalibrationViewSet,
    InstrumentViewSet,
    CharacteristicViewSet,
    InspectionPlanViewSet,
    InspectionViewSet,
    LotStatusViewSet,
    PlanLineViewSet,
    ReadingViewSet,
)

router = DefaultRouter()
router.register("characteristics", CharacteristicViewSet)
router.register("plans", InspectionPlanViewSet)
router.register("plan-lines", PlanLineViewSet)
router.register("inspections", InspectionViewSet)
router.register("readings", ReadingViewSet)
router.register("lot-status", LotStatusViewSet, basename="lot-status")
router.register("instruments", InstrumentViewSet)
router.register("calibrations", CalibrationViewSet)

urlpatterns = router.urls
