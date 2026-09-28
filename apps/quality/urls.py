from rest_framework.routers import DefaultRouter

from .views import (
    SamplingPlanViewSet,
    ControlChartViewSet,
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
router.register("spc", ControlChartViewSet, basename="spc")
router.register("sampling", SamplingPlanViewSet, basename="sampling")

urlpatterns = router.urls
