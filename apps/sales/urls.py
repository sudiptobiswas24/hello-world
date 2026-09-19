from rest_framework.routers import DefaultRouter

from .views import (
    CustomerProfileViewSet,
    DeliveryLineViewSet,
    DeliveryViewSet,
    InvoiceLineViewSet,
    InvoicePaymentViewSet,
    InvoiceViewSet,
    PriceListItemViewSet,
    PriceListViewSet,
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
router.register("price-lists", PriceListViewSet)
router.register("price-list-items", PriceListItemViewSet)
router.register("customer-profiles", CustomerProfileViewSet)

urlpatterns = router.urls
