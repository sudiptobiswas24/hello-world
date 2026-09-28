from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    EInvoiceViewSet,
    EwayBillViewSet,
    Gstr1JsonView,
    Gstr1View,
    Gstr3bView,
    Itc04View,
)

router = DefaultRouter()
router.register("e-invoices", EInvoiceViewSet)
router.register("eway-bills", EwayBillViewSet)

urlpatterns = [
    path("gstr1/", Gstr1View.as_view()),
    path("gstr1/json/", Gstr1JsonView.as_view()),
    path("gstr3b/", Gstr3bView.as_view()),
    path("itc04/", Itc04View.as_view()),
    path("", include(router.urls)),
]
