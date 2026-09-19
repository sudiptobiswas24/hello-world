from rest_framework.routers import DefaultRouter

from .views import AccountViewSet, JournalEntryViewSet, JournalLineViewSet

router = DefaultRouter()
router.register("accounts", AccountViewSet)
router.register("journal-entries", JournalEntryViewSet)
router.register("journal-lines", JournalLineViewSet)

urlpatterns = router.urls
