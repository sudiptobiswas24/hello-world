from rest_framework.routers import DefaultRouter

from .tax_api import TaxBlockOpeningViewSet, TaxBlockRateViewSet, TaxBlockViewSet, TaxDepreciationYearViewSet
from .views import AssetCategoryViewSet, DepreciationEntryViewSet, FixedAssetViewSet

router = DefaultRouter()
router.register("categories", AssetCategoryViewSet)
router.register("assets", FixedAssetViewSet)
router.register("depreciation", DepreciationEntryViewSet)
router.register("tax-blocks", TaxBlockViewSet)
router.register("tax-block-rates", TaxBlockRateViewSet)
router.register("tax-block-openings", TaxBlockOpeningViewSet)
router.register("tax-years", TaxDepreciationYearViewSet)

urlpatterns = router.urls
