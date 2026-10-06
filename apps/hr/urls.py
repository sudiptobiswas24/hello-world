from rest_framework.routers import DefaultRouter

from .attendance_api import AttendanceDayViewSet
from .contract_labour_api import ContractWorkerViewSet, LabourContractorViewSet

from .payroll_views import (
    CompensationViewSet,
    PayComponentViewSet,
    PayRunViewSet,
    PayslipViewSet,
    RemittanceViewSet,
    SlabViewSet,
    GratuityView,
    StatutoryLiabilitiesView,
)
from .views import DepartmentViewSet, EmployeeViewSet, LeavePolicyViewSet, LeaveRequestViewSet

router = DefaultRouter()
router.register("departments", DepartmentViewSet)
router.register("employees", EmployeeViewSet)
router.register("leave-requests", LeaveRequestViewSet)
router.register("leave-policies", LeavePolicyViewSet)
router.register("pay-components", PayComponentViewSet)
router.register("compensation", CompensationViewSet)
router.register("pay-runs", PayRunViewSet)
router.register("payslips", PayslipViewSet)
router.register("pay-component-slabs", SlabViewSet)
router.register("statutory-remittances", RemittanceViewSet)
router.register("statutory-liabilities", StatutoryLiabilitiesView, basename="statutory-liabilities")
router.register("gratuity", GratuityView, basename="gratuity")
router.register("labour-contractors", LabourContractorViewSet)
router.register("contract-workers", ContractWorkerViewSet)
router.register("attendance", AttendanceDayViewSet)

urlpatterns = router.urls
