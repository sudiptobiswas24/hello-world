from rest_framework.routers import DefaultRouter

from .views import (
    AccountViewSet,
    FiscalPositionTaxMappingViewSet,
    FiscalPositionViewSet,
    JournalEntryViewSet,
    JournalLineViewSet,
    PartyTaxProfileViewSet,
    PaymentViewSet,
    TaxGroupViewSet,
    TaxViewSet,
)

router = DefaultRouter()
router.register("accounts", AccountViewSet)
router.register("journal-entries", JournalEntryViewSet)
router.register("journal-lines", JournalLineViewSet)
router.register("taxes", TaxViewSet)
router.register("tax-groups", TaxGroupViewSet)
router.register("fiscal-positions", FiscalPositionViewSet)
router.register("fiscal-position-tax-mappings", FiscalPositionTaxMappingViewSet)
router.register("party-tax-profiles", PartyTaxProfileViewSet)
router.register("payments", PaymentViewSet)

urlpatterns = router.urls
