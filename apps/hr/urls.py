
from rest_framework.routers import DefaultRouter

from .attendance_api import AttendanceDayViewSet
from .pay_api import AdvanceViewSet, FigureViewSet, MyPayslipsView, PayDetailsViewSet, RegisterView, TransferFileViewSet
from .contract_labour_api import ContractWorkerViewSet, LabourContractorViewSet
from .people_api import ApplicantViewSet, AppraisalViewSet, ExpenseClaimViewSet, ExpenseLineViewSet, JobOpeningViewSet

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
router.register("pay-details", PayDetailsViewSet)
router.register("statutory-figures", FigureViewSet)
router.register("statutory-registers", RegisterView, basename="statutory-registers")
router.register("salary-advances", AdvanceViewSet)
router.register("salary-files", TransferFileViewSet)
router.register("my-payslips", MyPayslipsView, basename="my-payslips")
router.register("labour-contractors", LabourContractorViewSet)
router.register("contract-workers", ContractWorkerViewSet)
router.register("attendance", AttendanceDayViewSet)
router.register("expense-claims", ExpenseClaimViewSet)
router.register("expense-lines", ExpenseLineViewSet)
router.register("appraisals", AppraisalViewSet)
router.register("job-openings", JobOpeningViewSet)
router.register("applicants", ApplicantViewSet)

urlpatterns = router.urls
