from rest_framework.routers import DefaultRouter

from .views import (
    SalesReportViewSet,
    CommissionPlanViewSet,
    CustomerProfileViewSet,
    DeliveryLineViewSet,
    DunningLevelViewSet,
    DunningNoticeViewSet,
    DeliveryViewSet,
    InvoiceLineViewSet,
    InvoicePaymentViewSet,
    InvoiceViewSet,
    PriceListItemViewSet,
    PriceListViewSet,
    QuotationLineViewSet,
    QuotationViewSet,
    RecurringInvoiceLineViewSet,
    RecurringInvoiceViewSet,
    SalesRepViewSet,
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
router.register("quotations", QuotationViewSet)
router.register("quotation-lines", QuotationLineViewSet)
router.register("dunning-levels", DunningLevelViewSet)
router.register("dunning-notices", DunningNoticeViewSet)
router.register("commission-plans", CommissionPlanViewSet)
router.register("sales-reps", SalesRepViewSet)
router.register("sales-reports", SalesReportViewSet, basename="sales-report")
router.register("recurring-invoices", RecurringInvoiceViewSet)
router.register("recurring-invoice-lines", RecurringInvoiceLineViewSet)

urlpatterns = router.urls
