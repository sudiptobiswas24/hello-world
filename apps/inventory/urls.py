from rest_framework.routers import DefaultRouter

from .views import ItemViewSet, StockMovementViewSet, WarehouseViewSet

router = DefaultRouter()
router.register("warehouses", WarehouseViewSet)
router.register("items", ItemViewSet)
router.register("stock-movements", StockMovementViewSet)

urlpatterns = router.urls
