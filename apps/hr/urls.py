from rest_framework.routers import DefaultRouter

from .payroll_views import (
    CompensationViewSet,
    PayComponentViewSet,
    PayRunViewSet,
    PayslipViewSet,
    RemittanceViewSet,
    SlabViewSet,
    StatutoryLiabilitiesView,
)
from .views import DepartmentViewSet, EmployeeViewSet, LeaveRequestViewSet

router = DefaultRouter()
router.register("departments", DepartmentViewSet)
router.register("employees", EmployeeViewSet)
router.register("leave-requests", LeaveRequestViewSet)
router.register("pay-components", PayComponentViewSet)
router.register("compensation", CompensationViewSet)
router.register("pay-runs", PayRunViewSet)
router.register("payslips", PayslipViewSet)
router.register("pay-component-slabs", SlabViewSet)
router.register("statutory-remittances", RemittanceViewSet)
router.register("statutory-liabilities", StatutoryLiabilitiesView, basename="statutory-liabilities")

urlpatterns = router.urls
