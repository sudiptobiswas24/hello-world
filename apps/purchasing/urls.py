from rest_framework.routers import DefaultRouter

from .tds_api import TdsChallanViewSet, TdsDeductionViewSet

from .documents_api import (
    BlanketOrderLineViewSet,
    BlanketOrderViewSet,
    PurchaseRequisitionLineViewSet,
    PurchaseRequisitionViewSet,
    RequestForQuotationViewSet,
    RfqInvitationViewSet,
    RfqLineViewSet,
)
from .masters_api import (
    ApprovalTierViewSet,
    BudgetViewSet,
    PurchaseApprovalPolicyViewSet,
    ReorderRuleViewSet,
    VendorPriceViewSet,
)
from .views import (
    BillPaymentViewSet,
    PurchasingReportViewSet,
    BillLineViewSet,
    BillViewSet,
    GoodsReceiptLineViewSet,
    GoodsReceiptViewSet,
    PurchaseOrderLineViewSet,
    PurchaseOrderViewSet,
)

router = DefaultRouter()
router.register("purchase-orders", PurchaseOrderViewSet)
router.register("purchase-order-lines", PurchaseOrderLineViewSet)
router.register("bills", BillViewSet)
router.register("tds-deductions", TdsDeductionViewSet)
router.register("tds-challans", TdsChallanViewSet)
router.register("bill-payments", BillPaymentViewSet)
router.register(
    "purchasing-reports", PurchasingReportViewSet, basename="purchasing-report"
)
router.register("bill-lines", BillLineViewSet)
router.register("goods-receipts", GoodsReceiptViewSet)
router.register("goods-receipt-lines", GoodsReceiptLineViewSet)
router.register("vendor-prices", VendorPriceViewSet)
router.register("reorder-rules", ReorderRuleViewSet)
router.register("budgets", BudgetViewSet)
router.register("approval-policies", PurchaseApprovalPolicyViewSet)
router.register("approval-tiers", ApprovalTierViewSet)
router.register("requisitions", PurchaseRequisitionViewSet)
router.register("requisition-lines", PurchaseRequisitionLineViewSet)
router.register("rfqs", RequestForQuotationViewSet)
router.register("rfq-lines", RfqLineViewSet)
router.register("rfq-invitations", RfqInvitationViewSet)
router.register("blanket-orders", BlanketOrderViewSet)
router.register("blanket-order-lines", BlanketOrderLineViewSet)

urlpatterns = router.urls
