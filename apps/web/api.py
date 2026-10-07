"""The office's own endpoints: reports that read across every module at once."""

from decimal import Decimal, InvalidOperation

from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.api import flag
from apps.core.permissions import RequiredPermission

from django.utils import timezone

from .bank import stock_statement
from .checks import inbox
from .health import integrity, server
from .search import search
from .summary import groupings, list_view, summarise


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


class HealthView(viewsets.ViewSet):
    """
    Whether the system agrees with itself: the books against themselves
    (health.integrity), the server (health.server) and every morning
    check this login may read, with its count. `?fresh=true` runs the
    probes again rather than serving the last ten minutes' answer.
    """

    permission_classes = [IsAuthenticated, RequiredPermission]
    required_permission = "core.check_health"

    def list(self, request):
        day = timezone.localdate()
        report = integrity(day, fresh=flag(request.query_params, "fresh", False))
        return Response({"day": day, "checked_at": report["checked_at"], "books": report["findings"],
                         "server": server(), "checks": inbox(request.user, day, everything=True)})


class SummaryView(viewsets.ViewSet):
    """
    ?endpoint=/api/sales/invoices/&by=customer, with the list's own
    narrowing: each group's count and sums. Without `by`, what the list
    can be grouped by. The list's view reads, with its permission and
    its scoping; this groups.
    """

    permission_classes = [IsAuthenticated]

    def list(self, request):
        endpoint = request.query_params.get("endpoint", "")
        if not endpoint.startswith("/api/"):
            raise ValidationError({"endpoint": ["Name the list as its API path."]})
        view, queryset = list_view(request, endpoint, request.query_params)
        by = request.query_params.get("by", "")
        if not by:
            return Response({"by": groupings(view, queryset.model)})
        return Response(summarise(view, queryset, by))


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
