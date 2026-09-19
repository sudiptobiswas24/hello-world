from rest_framework.routers import DefaultRouter

from .views import (
    InvoiceLineViewSet,
    InvoicePaymentViewSet,
    InvoiceViewSet,
    SalesOrderLineViewSet,
    SalesOrderViewSet,
)

router = DefaultRouter()
router.register("sales-orders", SalesOrderViewSet)
router.register("sales-order-lines", SalesOrderLineViewSet)
router.register("invoices", InvoiceViewSet)
router.register("invoice-lines", InvoiceLineViewSet)
router.register("invoice-payments", InvoicePaymentViewSet)

urlpatterns = router.urls
