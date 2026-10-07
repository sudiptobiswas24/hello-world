from rest_framework.routers import DefaultRouter

from .api import BankStockStatementView, InboxView, SearchView, SummaryView

router = DefaultRouter()
router.register("bank-stock-statement", BankStockStatementView, basename="bank-stock-statement")
router.register("search", SearchView, basename="search")
router.register("inbox", InboxView, basename="inbox")
router.register("summary", SummaryView, basename="summary")

urlpatterns = router.urls
