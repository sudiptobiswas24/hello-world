from rest_framework.routers import DefaultRouter

from .tds_api import CustomerTdsViewSet

from .writeoffs_api import InvoiceWriteOffViewSet

from .call_off_views import CallOffViewSet
from .price_variation_views import (
    PriceClauseViewSet,
    PriceIndexViewSet,
    PriceVariationBillViewSet,
)
from .crm_api import ActivityViewSet, CampaignViewSet, LeadViewSet, OpportunityViewSet
from .views import (
    ThirdPartyReleaseViewSet,
    SuppliedItemViewSet,
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
router.register("supplied-items", SuppliedItemViewSet)
router.register("third-party-releases", ThirdPartyReleaseViewSet)
router.register("invoices", InvoiceViewSet)
router.register("invoice-lines", InvoiceLineViewSet)
router.register("invoice-payments", InvoicePaymentViewSet)
router.register("invoice-write-offs", InvoiceWriteOffViewSet)
router.register("customer-tds", CustomerTdsViewSet)
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
router.register("call-offs", CallOffViewSet)
router.register("price-indices", PriceIndexViewSet)
router.register("price-clauses", PriceClauseViewSet)
router.register("price-variation-bills", PriceVariationBillViewSet, basename="price-variation-bill")
router.register("leads", LeadViewSet)
router.register("opportunities", OpportunityViewSet)
router.register("activities", ActivityViewSet)
router.register("campaigns", CampaignViewSet)

urlpatterns = router.urls
