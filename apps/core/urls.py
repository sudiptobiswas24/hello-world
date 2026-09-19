from rest_framework.routers import DefaultRouter

from .views import (
    AddressViewSet,
    CompanyViewSet,
    ContactViewSet,
    CountryViewSet,
    CurrencyViewSet,
    ExchangeRateViewSet,
    PartyBankAccountViewSet,
    PartyRoleAssignmentViewSet,
    PartyTagViewSet,
    PartyViewSet,
    PaymentTermsViewSet,
    UnitOfMeasureViewSet,
)

router = DefaultRouter()
router.register("parties", PartyViewSet)
router.register("party-roles", PartyRoleAssignmentViewSet)
router.register("party-tags", PartyTagViewSet)
router.register("addresses", AddressViewSet)
router.register("contacts", ContactViewSet)
router.register("bank-accounts", PartyBankAccountViewSet)
router.register("countries", CountryViewSet)
router.register("currencies", CurrencyViewSet)
router.register("exchange-rates", ExchangeRateViewSet)
router.register("units-of-measure", UnitOfMeasureViewSet)
router.register("payment-terms", PaymentTermsViewSet)
router.register("company", CompanyViewSet)

urlpatterns = router.urls
