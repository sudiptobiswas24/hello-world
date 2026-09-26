from rest_framework.routers import DefaultRouter

from .views import DepartmentViewSet, EmployeeViewSet, LeaveRequestViewSet

router = DefaultRouter()
router.register("departments", DepartmentViewSet)
router.register("employees", EmployeeViewSet)
router.register("leave-requests", LeaveRequestViewSet)

urlpatterns = router.urls
