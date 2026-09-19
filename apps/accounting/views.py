from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin

from .models import Account, JournalEntry, JournalLine
from .serializers import AccountSerializer, JournalEntrySerializer, JournalLineSerializer


class AccountViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Account.objects.all()
    serializer_class = AccountSerializer


class JournalEntryViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = JournalEntry.objects.prefetch_related("lines")
    serializer_class = JournalEntrySerializer

    @action(detail=True, methods=["post"])
    def post_entry(self, request, pk=None):
        entry = self.get_object()
        try:
            entry.post()
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(entry).data)

    @action(detail=True, methods=["post"])
    def reverse(self, request, pk=None):
        entry = self.get_object()
        try:
            reversal = entry.create_reversal(memo=request.data.get("memo", ""))
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(reversal).data)


class JournalLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = JournalLine.objects.all()
    serializer_class = JournalLineSerializer
