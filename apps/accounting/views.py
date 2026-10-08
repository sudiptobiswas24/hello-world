from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Prefetch
from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import ActionPermission, RequiredPermission
from rest_framework.response import Response

from apps.core.api import flag
from apps.core.audit import AuditableViewSetMixin

from decimal import Decimal, InvalidOperation

from .analytic import CostCentre
from .budgets import Budget, BudgetLine, budget_report
from .recurring import RecurringJournal, RecurringJournalLine, generate_due_journals
from .reports import balance_sheet, profit_and_loss, trial_balance
from .models import (
    Account,
    AccountingPeriod,
    FiscalPosition,
    FiscalPositionTaxMapping,
    JournalEntry,
    JournalLine,
    Payment,
    PartyTaxProfile,
    Tax,
    TaxGroup,
    compute_taxes,
)
from .serializers import (
    AccountingPeriodSerializer,
    BudgetLineSerializer,
    BudgetSerializer,
    CostCentreSerializer,
    RecurringJournalLineSerializer,
    RecurringJournalSerializer,
    AccountSerializer,
    FiscalPositionSerializer,
    FiscalPositionTaxMappingSerializer,
    JournalEntrySerializer,
    JournalLineSerializer,
    PartyTaxProfileSerializer,
    PaymentSerializer,
    TaxGroupSerializer,
    TaxSerializer,
)


class CostCentreViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """The analytic dimension (analytic.py): the centres, and ?from=&to= `report/` of posted expenses by centre."""

    search_fields = ["code", "name"]
    filter_fields = ["is_active"]
    ordering_fields = ["code", "name"]
    queryset = CostCentre.objects.all()
    serializer_class = CostCentreSerializer
    action_permission_map = {"report": "accounting.view_journalentry"}

    @action(detail=False, methods=["get"])
    def report(self, request):
        from .analytic import costs_by_centre

        return Response(costs_by_centre(request.query_params.get("from") or None, request.query_params.get("to") or None))


class AccountingPeriodViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """The months and years the books are reported for, and which are closed against posting."""

    search_fields = ["name", "note"]
    filter_fields = ["closed"]
    date_field = "end_date"
    ordering_fields = ["start_date", "end_date", "name"]
    queryset = AccountingPeriod.objects.select_related("closed_by")
    serializer_class = AccountingPeriodSerializer
    action_permission_map = {
        "close": "accounting.close_accountingperiod",
        "reopen": "accounting.close_accountingperiod",
    }

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        """Lock the period: nothing further posts into it ({"note"} says why or by whose sign-off)."""
        period = self.get_object()
        period.close(by=request.user, note=str(request.data.get("note") or ""))
        return Response(self.get_serializer(period).data)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        period = self.get_object()
        period.reopen(by=request.user, note=str(request.data.get("note") or ""))
        return Response(self.get_serializer(period).data)


class BudgetViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Budgets (budgets.py), and each one's `report/?as_of=` against what posted."""

    search_fields = ["code", "name"]
    filter_fields = ["is_active"]
    date_field = "start_date"
    ordering_fields = ["start_date", "code", "name"]
    queryset = Budget.objects.prefetch_related("lines__account", "lines__cost_centre")
    serializer_class = BudgetSerializer
    action_permission_map = {"report": "accounting.view_journalentry"}

    @action(detail=True, methods=["get"])
    def report(self, request, pk=None):
        return Response(budget_report(self.get_object(), request.query_params.get("as_of") or None))


class BudgetLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    filter_fields = ["budget", "account", "cost_centre"]
    queryset = BudgetLine.objects.select_related("budget", "account", "cost_centre")
    serializer_class = BudgetLineSerializer


class RecurringJournalViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Schedules of journal entries (recurring.py): `generate/` takes one's next entry, `run/` every entry due."""

    search_fields = ["code", "memo"]
    filter_fields = ["is_active", "interval", "auto_post"]
    date_field = "next_run_date"
    ordering_fields = ["next_run_date", "code"]
    queryset = RecurringJournal.objects.prefetch_related("lines__account", "lines__party", "lines__cost_centre")
    serializer_class = RecurringJournalSerializer
    action_permission_map = {
        "generate": "accounting.add_journalentry",
        "run": "accounting.add_journalentry",
    }

    def _may_post(self, request, schedules):
        # A schedule that posts what it makes posts as whoever runs it.
        if any(schedule.auto_post for schedule in schedules) and not request.user.has_perm(
                "accounting.post_journalentry"):
            raise PermissionDenied("This schedule posts each entry it makes, which takes the right to post "
                                   "journal entries.")

    @action(detail=True, methods=["post"])
    def generate(self, request, pk=None):
        """Take the next entry from this schedule ({"on_date"} dates it other than its due day)."""
        schedule = self.get_object()
        self._may_post(request, [schedule])
        entry = schedule.generate_one(on_date=request.data.get("on_date") or None,
                                      expected=request.data.get("next_run_date") or None)
        return Response(JournalEntrySerializer(entry).data)

    @action(detail=False, methods=["post"])
    def run(self, request):
        """Take every entry now due across the active schedules ({"as_of"} runs as of another day)."""
        as_of = request.data.get("as_of") or None
        self._may_post(request, [s for s in RecurringJournal.objects.filter(is_active=True) if s.is_due(as_of)])
        made, refused = generate_due_journals(as_of=as_of)
        return Response({"made": JournalEntrySerializer(made, many=True).data,
                         "refused": [{"code": code, "why": why} for code, why in refused]})


class RecurringJournalLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    filter_fields = ["schedule"]
    queryset = RecurringJournalLine.objects.select_related("schedule", "account", "party", "cost_centre")
    serializer_class = RecurringJournalLineSerializer


class AccountViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    search_fields = ["code", "name"]
    # holds_money=true&is_active=true: what a "paid from" picker offers.
    filter_fields = ["account_type", "is_active", "parent", "holds_money"]
    ordering_fields = ["code", "name"]

    queryset = Account.objects.all()
    serializer_class = AccountSerializer
    # The chart is reference data every role reads; what was posted to an
    # account is the books, and reading it is reading journal entries.
    action_permission_map = {"ledger": "accounting.view_journalentry"}

    @action(detail=True, methods=["get"])
    def ledger(self, request, pk=None):
        """
        ?from=&to=&page=&page_size=: the account's posted lines, newest
        first, each with the balance after it, under what it opened and
        closed at. The running balance is the database's window over the
        whole period, so page five is as right as page one; the opening is
        everything before `from`, summed in the database too.
        """
        from django.db.models import F, Sum, Value, Window
        from django.db.models.functions import Coalesce

        from apps.core.api import whole_number
        from apps.core.models import to_date

        account = self.get_object()
        params = request.query_params
        start, end = to_date(params.get("from")), to_date(params.get("to"))
        page = whole_number(params, "page", default=1, least=1)
        size = min(whole_number(params, "page_size", default=50, least=1), 500)
        zero = Value(Decimal("0"))

        posted = JournalLine.objects.filter(account=account, entry__posted=True)

        def movement(lines):
            return lines.aggregate(debit=Coalesce(Sum("debit"), zero), credit=Coalesce(Sum("credit"), zero))

        before = movement(posted.filter(entry__date__lt=start)) if start else {"debit": 0, "credit": 0}
        opening = Decimal(before["debit"]) - Decimal(before["credit"])
        period = posted
        if start:
            period = period.filter(entry__date__gte=start)
        if end:
            period = period.filter(entry__date__lte=end)
        totals = movement(period)
        count = period.count()
        rows = period.select_related("entry", "party").annotate(moved=Window(
            Sum(F("debit") - F("credit")),
            order_by=[F("entry__date").asc(), F("entry_id").asc(), F("id").asc()],
        )).order_by("-entry__date", "-entry_id", "-id")[(page - 1) * size: page * size]
        response = Response({
            "account": {"id": account.pk, "code": account.code, "name": account.name,
                        "account_type": account.account_type},
            "from": start, "to": end,
            "opening": opening,
            "debit": totals["debit"], "credit": totals["credit"],
            "closing": opening + totals["debit"] - totals["credit"],
            "lines": [{
                "id": line.pk, "entry": line.entry_id, "date": line.entry.date,
                "reference": line.entry.reference, "memo": line.description or line.entry.memo,
                "party": line.party.name if line.party_id else "",
                "debit": line.debit, "credit": line.credit,
                "balance": opening + line.moved,
            } for line in rows],
        })
        response["X-Total-Count"] = str(count)
        response["X-Page"] = str(page)
        response["X-Page-Size"] = str(size)
        return response


class JournalEntryViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    search_fields = ["reference", "memo"]
    filter_fields = ["posted", "recurring_journal"]
    date_field = "date"
    ordering_fields = ["date"]

    queryset = JournalEntry.objects.prefetch_related(
        Prefetch("lines", queryset=JournalLine.objects.select_related("account", "party")))
    serializer_class = JournalEntrySerializer
    action_permission_map = {
        "post_entry": "accounting.post_journalentry",
        "reverse": "accounting.post_journalentry",
    }

    def get_serializer_context(self):
        # Which document keeps an entry is asked of every model that can: one page's worth, not a list's.
        return {**super().get_serializer_context(), "one": self.action == "retrieve"}

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
            reversal = entry.reverse_by_hand(memo=request.data.get("memo", ""))
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(reversal).data)


class JournalLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    # ?account=…&entry__posted=true&from=…&to=… is an account's ledger,
    # newest first: the entry date index answers a page of it without
    # sorting every line the account ever had.
    filter_fields = ["account", "party", "entry", "entry__posted"]
    date_field = "entry__date"

    queryset = JournalLine.objects.select_related("entry", "account", "party").order_by(
        "-entry__date", "-entry_id", "-id")
    serializer_class = JournalLineSerializer


class TaxGroupViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = TaxGroup.objects.all()
    serializer_class = TaxGroupSerializer
    search_fields = ["code", "name"]


class TaxViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Tax.objects.select_related("group", "collected_account", "paid_account")
    serializer_class = TaxSerializer
    filter_fields = ["group", "scope", "is_active", "gst_head", "computation"]
    search_fields = ["code", "name"]

    @action(detail=False, methods=["post"])
    def preview(self, request):
        """
        Compute tax on an amount without creating a document:
        {"amount": "100.00", "quantity": "1", "tax_ids": [1, 2], "party_id": 5}
        """
        try:
            amount = Decimal(str(request.data.get("amount")))
            quantity = Decimal(str(request.data.get("quantity", "1")))
        except (InvalidOperation, TypeError):
            raise DRFValidationError("amount and quantity must be numbers.")

        taxes = list(Tax.objects.filter(pk__in=request.data.get("tax_ids", [])))

        party_id = request.data.get("party_id")
        if party_id:
            profile = PartyTaxProfile.objects.filter(party_id=party_id).first()
            if profile:
                taxes = profile.applicable_taxes(taxes)

        base, lines, total = compute_taxes(taxes, amount, quantity)
        return Response({
            "base": base,
            "taxes": [
                {"id": tax.pk, "code": tax.code, "name": tax.name, "amount": tax_amount}
                for tax, tax_amount in lines
            ],
            "total": total,
        })


class FiscalPositionViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = FiscalPosition.objects.prefetch_related("tax_mappings__source_tax", "tax_mappings__target_tax")
    serializer_class = FiscalPositionSerializer
    filter_fields = ["is_active", "country"]
    search_fields = ["code", "name"]


class FiscalPositionTaxMappingViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = FiscalPositionTaxMapping.objects.select_related("source_tax", "target_tax")
    serializer_class = FiscalPositionTaxMappingSerializer
    filter_fields = ["fiscal_position", "source_tax"]


class PartyTaxProfileViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PartyTaxProfile.objects.select_related("party", "fiscal_position")
    serializer_class = PartyTaxProfileSerializer
    filter_fields = ["party", "fiscal_position", "tax_exempt"]
    search_fields = ["gstin", "party__name"]


class PaymentViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    search_fields = ["number", "reference", "memo", "party__code", "party__name"]
    filter_fields = ["party", "direction", "posted", "bank_account"]
    date_field = "payment_date"
    ordering_fields = ["payment_date", "number", "amount"]
    extra_params = ("unapplied",)  # ?unapplied=true: posted, standing, with money applied to nothing

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        if flag(self.request.query_params, "unapplied", False):
            from .settlement import unapplied

            queryset = unapplied(queryset.filter(posted=True, voided_entry__isnull=True))
        return queryset

    queryset = Payment.objects.select_related(
        "party", "bank_account", "counterpart_account", "journal_entry"
    ).prefetch_related("journal_entry__reversed_by", "invoice_allocations", "bill_allocations")
    serializer_class = PaymentSerializer
    action_permission_map = {
        "post_payment": "accounting.post_payment",
        "void": "accounting.post_payment",
        "send": "accounting.change_payment",
    }

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        """The remittance advice (money out) or the receipt (money in)."""
        payment = self.get_object()
        response = HttpResponse(payment.render_pdf(), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{payment.number or f"draft-{payment.pk}"}.pdf"'
        return response

    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        """Email it to the party; {"to", "subject", "body"} override the address and the wording."""
        payment = self.get_object()
        try:
            recipient = payment.email_to_party(to=request.data.get("to") or None,
                                               subject=request.data.get("subject") or None,
                                               body=request.data.get("body") or None, user=request.user)
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response({"sent_to": recipient})

    @action(detail=True, methods=["post"])
    def post_payment(self, request, pk=None):
        payment = self.get_object()
        try:
            payment.post()
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(payment).data)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        payment = self.get_object()
        try:
            payment.void(memo=request.data.get("memo", ""), on_date=request.data.get("date") or None)
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(payment).data)


class FinancialStatementViewSet(viewsets.ViewSet):
    """
    The statements. They derived from the ledger and nothing could ask
    for them — a double-entry system that cannot produce a trial balance
    on request can record a year of trading and answer nothing about it.
    """

    permission_classes = [IsAuthenticated, RequiredPermission, ActionPermission]

    required_permission = "accounting.view_journalentry"

    def list(self, request):
        return Response({
            "trial-balance": "trial-balance/",
            "profit-and-loss": "profit-and-loss/",
            "balance-sheet": "balance-sheet/",
        })

    @action(detail=False, methods=["get"], url_path="trial-balance")
    def trial_balance_report(self, request):
        report = trial_balance(
            as_of=request.query_params.get("as_of"),
            start=request.query_params.get("start"),
            include_zero=flag(request.query_params, "include_zero", False),
        )
        return Response({
            "balanced": report["balanced"],
            "total_debit": report["total_debit"],
            "total_credit": report["total_credit"],
            "rows": [
                {
                    "account": row["account"].code,
                    "account_id": row["account"].pk,
                    "name": row["account"].name,
                    "opening": row["opening"],
                    "debit": row["debit"],
                    "credit": row["credit"],
                    "balance": row["balance"],
                    "natural": row["natural"],
                }
                for row in report["rows"]
            ],
        })

    @action(detail=False, methods=["get"], url_path="profit-and-loss")
    def profit_and_loss_report(self, request):
        report = profit_and_loss(
            start=request.query_params.get("start"),
            end=request.query_params.get("end"),
        )
        return Response({
            "income_total": report["income_total"],
            "expense_total": report["expense_total"],
            "net_profit": report["net_profit"],
            "income": [
                {"account": row["account"].code, "account_id": row["account"].pk, "name": row["account"].name,
                 "balance": row["balance"], "natural": row["natural"]}
                for row in report["income"]
            ],
            "expenses": [
                {"account": row["account"].code, "account_id": row["account"].pk, "name": row["account"].name,
                 "balance": row["balance"], "natural": row["natural"]}
                for row in report["expenses"]
            ],
        })

    @action(detail=False, methods=["get"], url_path="balance-sheet")
    def balance_sheet_report(self, request):
        report = balance_sheet(as_of=request.query_params.get("as_of"))
        return Response({
            "balanced": report["balanced"],
            "asset_total": report["asset_total"],
            "total_liabilities_and_equity": report["total_liabilities_and_equity"],
            "retained_brought_forward": report["retained_brought_forward"],
            "profit_for_year": report["profit_for_year"],
            "assets": [
                {"account": row["account"].code, "account_id": row["account"].pk, "name": row["account"].name,
                 "balance": row["balance"], "natural": row["natural"]}
                for row in report["assets"]
            ],
            "liabilities": [
                {"account": row["account"].code, "account_id": row["account"].pk, "name": row["account"].name,
                 "balance": row["balance"], "natural": row["natural"]}
                for row in report["liabilities"]
            ],
            "equity": [
                {"account": row["account"].code, "account_id": row["account"].pk, "name": row["account"].name,
                 "balance": row["balance"], "natural": row["natural"]}
                for row in report["equity"]
            ],
        })
