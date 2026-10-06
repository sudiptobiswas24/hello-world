"""The office's own endpoints: reports that read across every module at once."""

from decimal import Decimal, InvalidOperation

from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import RequiredPermission

from django.utils import timezone

from .bank import stock_statement
from .checks import inbox
from .search import search


def _margin(params, name, default):
    try:
        value = Decimal(params.get(name) or default)
    except InvalidOperation:
        raise ValidationError({name: ["A margin is a percentage."]}) from None
    if not 0 <= value <= 100:
        raise ValidationError({name: ["A margin is from 0 to 100 percent."]})
    return value


class InboxView(viewsets.ViewSet):
    """What fell due for this login: the morning checks, counted now."""

    permission_classes = [IsAuthenticated]

    def list(self, request):
        return Response({"day": timezone.localdate(), "rows": inbox(request.user)})


class SearchView(viewsets.ViewSet):
    """?q=: records whose number, code or name holds it, among the kinds this login may read."""

    permission_classes = [IsAuthenticated]

    def list(self, request):
        return Response(search(request.user, request.query_params.get("q", "")))


class BankStockStatementView(viewsets.ViewSet):
    """?as_of&stock_margin&debtor_margin: the month's stock statement for the bank, and the drawing power."""

    permission_classes = [IsAuthenticated, RequiredPermission]
    required_permission = "accounting.view_journalentry"

    def list(self, request):
        params = request.query_params
        return Response(stock_statement(as_of=params.get("as_of") or None,
                                        stock_margin=_margin(params, "stock_margin", "25"),
                                        debtor_margin=_margin(params, "debtor_margin", "40")))
