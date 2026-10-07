from django.urls import path
from rest_framework.routers import DefaultRouter

from .attachments_api import AttachmentViewSet
from .chatter_api import FollowUpViewSet, NoteViewSet
from .customfields import CustomFieldViewSet
from .endpoints import EndpointsView
from .errors_api import ServerErrorViewSet
from .history_api import HistoryView
from .licences_api import LicenceViewSet
from .numbering_api import DocumentSequenceViewSet
from .users_api import UserViewSet
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
router.register("users", UserViewSet)
router.register("document-sequences", DocumentSequenceViewSet)
router.register("licences", LicenceViewSet)
router.register("errors", ServerErrorViewSet)
router.register("custom-fields", CustomFieldViewSet)
router.register("attachments", AttachmentViewSet, basename="attachment")
router.register("notes", NoteViewSet, basename="note")
router.register("follow-ups", FollowUpViewSet, basename="follow-up")

urlpatterns = [path("me/", MeView.as_view(), name="me"), path("history/", HistoryView.as_view({"get": "list"}), name="history"),
               path("endpoints/", EndpointsView.as_view(), name="endpoints"),
               *router.urls]
