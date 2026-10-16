from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    EInvoiceViewSet,
    EwayBillViewSet,
    Gstr1JsonView,
    Gstr1View,
    Gstr2bMatchView,
    Gstr2bStatementViewSet,
    Gstr3bView,
    Itc04View,
)

router = DefaultRouter()
router.register("e-invoices", EInvoiceViewSet)
router.register("eway-bills", EwayBillViewSet)
router.register("gstr2b", Gstr2bStatementViewSet)

urlpatterns = [
    path("gstr1/", Gstr1View.as_view()),
    path("gstr1/json/", Gstr1JsonView.as_view()),
    path("gstr3b/", Gstr3bView.as_view()),
    # Before the router's gstr2b/<pk>/, which would take "match" for a key.
    path("gstr2b/match/", Gstr2bMatchView.as_view()),
    path("itc04/", Itc04View.as_view()),
    path("", include(router.urls)),
]
