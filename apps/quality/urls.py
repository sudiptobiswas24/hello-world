from rest_framework.routers import DefaultRouter

from .settings_api import QualitySettingsViewSet

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
    SupplierCertificateLineViewSet,
    SupplierCertificateViewSet,
)

router = DefaultRouter()
router.register("characteristics", CharacteristicViewSet)
router.register("plans", InspectionPlanViewSet)
router.register("plan-lines", PlanLineViewSet)
router.register("inspections", InspectionViewSet)
router.register("readings", ReadingViewSet)
router.register("supplier-certificates", SupplierCertificateViewSet)
router.register("supplier-certificate-lines", SupplierCertificateLineViewSet)
router.register("lot-status", LotStatusViewSet, basename="lot-status")
router.register("instruments", InstrumentViewSet)
router.register("calibrations", CalibrationViewSet)
router.register("spc", ControlChartViewSet, basename="spc")
router.register("sampling", SamplingPlanViewSet, basename="sampling")

router.register("quality-settings", QualitySettingsViewSet, basename="quality-setting")

urlpatterns = router.urls
