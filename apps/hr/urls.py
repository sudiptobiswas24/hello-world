from rest_framework.routers import DefaultRouter

from .payroll_views import (
    CompensationViewSet,
    PayComponentViewSet,
    PayRunViewSet,
    PayslipViewSet,
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

urlpatterns = router.urls
