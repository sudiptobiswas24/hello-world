from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin

from decimal import Decimal, InvalidOperation

from .reports import balance_sheet, profit_and_loss, trial_balance
from .models import (
    Account,
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


class AccountViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Account.objects.all()
    serializer_class = AccountSerializer


class JournalEntryViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = JournalEntry.objects.prefetch_related("lines")
    serializer_class = JournalEntrySerializer
    action_permission_map = {
        "post_entry": "accounting.post_journalentry",
        "reverse": "accounting.post_journalentry",
    }

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


class TaxGroupViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = TaxGroup.objects.all()
    serializer_class = TaxGroupSerializer


class TaxViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Tax.objects.select_related("group", "collected_account", "paid_account")
    serializer_class = TaxSerializer

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
    queryset = FiscalPosition.objects.prefetch_related("tax_mappings")
    serializer_class = FiscalPositionSerializer


class FiscalPositionTaxMappingViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = FiscalPositionTaxMapping.objects.select_related("source_tax", "target_tax")
    serializer_class = FiscalPositionTaxMappingSerializer


class PartyTaxProfileViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PartyTaxProfile.objects.select_related("party", "fiscal_position")
    serializer_class = PartyTaxProfileSerializer


class PaymentViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Payment.objects.select_related("party", "bank_account", "counterpart_account")
    serializer_class = PaymentSerializer
    action_permission_map = {
        "post_payment": "accounting.post_payment",
        "void": "accounting.post_payment",
    }

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
            payment.void(memo=request.data.get("memo", ""))
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(payment).data)


class FinancialStatementViewSet(viewsets.ViewSet):
    """
    The statements. They derived from the ledger and nothing could ask
    for them — a double-entry system that cannot produce a trial balance
    on request can record a year of trading and answer nothing about it.
    """

    permission_classes = [IsAuthenticated]

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
            include_zero=request.query_params.get("include_zero") == "true",
        )
        return Response({
            "balanced": report["balanced"],
            "total_debit": report["total_debit"],
            "total_credit": report["total_credit"],
            "rows": [
                {
                    "account": row["account"].code,
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
                {"account": row["account"].code, "name": row["account"].name,
                 "balance": row["balance"], "natural": row["natural"]}
                for row in report["income"]
            ],
            "expenses": [
                {"account": row["account"].code, "name": row["account"].name,
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
                {"account": row["account"].code, "name": row["account"].name,
                 "balance": row["balance"], "natural": row["natural"]}
                for row in report["assets"]
            ],
            "liabilities": [
                {"account": row["account"].code, "name": row["account"].name,
                 "balance": row["balance"], "natural": row["natural"]}
                for row in report["liabilities"]
            ],
            "equity": [
                {"account": row["account"].code, "name": row["account"].name,
                 "balance": row["balance"], "natural": row["natural"]}
                for row in report["equity"]
            ],
        })
