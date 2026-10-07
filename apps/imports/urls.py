from rest_framework.routers import DefaultRouter

from .api import ImportViewSet

router = DefaultRouter()
router.register("records", ImportViewSet, basename="import-records")

urlpatterns = router.urls
