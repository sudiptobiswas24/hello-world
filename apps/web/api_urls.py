from rest_framework.routers import DefaultRouter

from .api import BankStockStatementView, InboxView, SearchView

router = DefaultRouter()
router.register("bank-stock-statement", BankStockStatementView, basename="bank-stock-statement")
router.register("search", SearchView, basename="search")
router.register("inbox", InboxView, basename="inbox")

urlpatterns = router.urls
