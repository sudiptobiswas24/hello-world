from rest_framework.routers import DefaultRouter

from .views import AssetCategoryViewSet, DepreciationEntryViewSet, FixedAssetViewSet

router = DefaultRouter()
router.register("categories", AssetCategoryViewSet)
router.register("assets", FixedAssetViewSet)
router.register("depreciation", DepreciationEntryViewSet)

urlpatterns = router.urls
