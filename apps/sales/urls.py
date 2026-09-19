from rest_framework.routers import DefaultRouter

from .views import (
    DeliveryLineViewSet,
    DeliveryViewSet,
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
router.register("deliveries", DeliveryViewSet)
router.register("delivery-lines", DeliveryLineViewSet)

urlpatterns = router.urls
