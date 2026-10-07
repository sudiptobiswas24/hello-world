from django.urls import path
from rest_framework.routers import DefaultRouter

from .history_api import HistoryView
from .licences_api import LicenceViewSet
from .numbering_api import DocumentSequenceViewSet
from .views import (
    AddressViewSet,
    CompanyViewSet,
    ContactViewSet,
    CountryViewSet,
    CurrencyViewSet,
    ExchangeRateViewSet,
    MeView,
    PartyBankAccountViewSet,
    PartyRoleAssignmentViewSet,
    PartyTagViewSet,
    PartyViewSet,
    PaymentTermsViewSet,
    RoleViewSet,
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
router.register("roles", RoleViewSet)
router.register("document-sequences", DocumentSequenceViewSet)
router.register("licences", LicenceViewSet)

urlpatterns = [path("me/", MeView.as_view(), name="me"), path("history/", HistoryView.as_view({"get": "list"}), name="history"),
               *router.urls]
