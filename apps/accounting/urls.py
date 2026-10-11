from rest_framework.routers import DefaultRouter

from .tds_api import TdsSectionViewSet

from .banking_api import BankStatementLineViewSet, BankStatementViewSet
from .charges_api import ChargeTypeViewSet

from .views import (
    AccountingPeriodViewSet,
    BudgetLineViewSet,
    BudgetViewSet,
    CostCentreViewSet,
    RecurringJournalLineViewSet,
    RecurringJournalViewSet,
    FinancialStatementViewSet,
    AccountViewSet,
    FiscalPositionTaxMappingViewSet,
    FiscalPositionViewSet,
    JournalEntryViewSet,
    JournalLineViewSet,
    PartyTaxProfileViewSet,
    PaymentViewSet,
    TaxGroupViewSet,
    TaxViewSet,
)

router = DefaultRouter()
router.register("accounts", AccountViewSet)
router.register("cost-centres", CostCentreViewSet)
router.register("periods", AccountingPeriodViewSet)
router.register("budgets", BudgetViewSet)
router.register("budget-lines", BudgetLineViewSet)
router.register("recurring-journals", RecurringJournalViewSet)
router.register("recurring-journal-lines", RecurringJournalLineViewSet)
router.register("journal-entries", JournalEntryViewSet)
router.register("journal-lines", JournalLineViewSet)
router.register("taxes", TaxViewSet)
router.register("tax-groups", TaxGroupViewSet)
router.register("fiscal-positions", FiscalPositionViewSet)
router.register("fiscal-position-tax-mappings", FiscalPositionTaxMappingViewSet)
router.register("party-tax-profiles", PartyTaxProfileViewSet)
router.register("payments", PaymentViewSet)
router.register(
    "financial-statements", FinancialStatementViewSet, basename="financial-statement"
)

router.register("charge-types", ChargeTypeViewSet)
router.register("tds-sections", TdsSectionViewSet)
router.register("bank-statements", BankStatementViewSet)
router.register("bank-statement-lines", BankStatementLineViewSet)

urlpatterns = router.urls
