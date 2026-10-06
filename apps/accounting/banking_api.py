"""
Bank reconciliation through the API: the bookkeeper keys the statement in
and matches its lines, the controller posts what the bank originated and
signs the statement off.

Posting a line is a journal entry, so it takes the right to post one; the
bookkeeper is deliberately without it.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.core.api import record_or_404
from apps.core.audit import AuditableViewSetMixin
from apps.core.models import Party

from .models import Account, BankStatement, BankStatementLine, Payment, round_money


def _refused(call):
    try:
        return call()
    except DjangoValidationError as exc:
        raise DRFValidationError(exc.messages)


class BankStatementSerializer(serializers.ModelSerializer):
    bank_account_name = serializers.CharField(source="bank_account.name", read_only=True)

    class Meta:
        model = BankStatement
        fields = ["id", "bank_account", "bank_account_name", "reference", "start_date", "end_date",
                  "opening_balance", "closing_balance", "closed", "closed_at"]
        read_only_fields = ["closed", "closed_at"]


class BankStatementLineSerializer(serializers.ModelSerializer):
    payment_number = serializers.CharField(source="payment.number", read_only=True, default=None)
    resolved = serializers.BooleanField(source="is_resolved", read_only=True)

    class Meta:
        model = BankStatementLine
        fields = ["id", "statement", "date", "description", "reference", "amount", "payment",
                  "payment_number", "journal_entry", "resolved"]
        # Set by matching and posting, which check what they set.
        read_only_fields = ["payment", "journal_entry"]


def _payment_row(payment):
    return {"id": payment.pk, "number": payment.number, "party_name": payment.party.name,
            "date": payment.payment_date, "amount": payment.signed_base_amount()}


class BankStatementViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = BankStatement.objects.select_related("bank_account")
    serializer_class = BankStatementSerializer
    filter_fields = ["bank_account", "closed"]
    search_fields = ["reference", "bank_account__name", "bank_account__code"]
    date_field = "end_date"
    action_permission_map = {
        "reconciliation": "accounting.view_bankstatement",
        "auto_match": "accounting.change_bankstatementline",
        "match": "accounting.change_bankstatementline",
        "post_line": "accounting.post_journalentry",
        "close": "accounting.close_bankstatement",
        "reopen": "accounting.close_bankstatement",
    }

    @action(detail=True, methods=["get"])
    def reconciliation(self, request, pk=None):
        """Books to bank, with every reconciling item named."""
        report = self.get_object().reconciliation()
        # To the paisa: a sum comes back at the column's scale on
        # PostgreSQL and bare on SQLite.
        return Response({
            "ledger_balance": round_money(report["ledger_balance"]),
            "statement_balance": report["statement_balance"],
            "unpresented": [_payment_row(payment) for payment in report["unpresented"]],
            "unpresented_total": round_money(report["unpresented_total"]),
            "unresolved_lines": len(report["unresolved_lines"]),
            "statement_difference": round_money(report["statement_difference"]),
            "difference": round_money(report["difference"]),
        })

    @action(detail=True, methods=["post"])
    def auto_match(self, request, pk=None):
        statement = self.get_object()
        matched = _refused(statement.auto_match)
        return Response({"matched": len(matched)})

    def _line(self, statement, request):
        line = record_or_404(BankStatementLine, request.data.get("line"), "line")
        if line.statement_id != statement.pk:
            raise DRFValidationError({"line": ["That line is on another statement."]})
        return line

    @action(detail=True, methods=["post"])
    def match(self, request, pk=None):
        """Say a line ({"line"}) is a payment ({"payment"})."""
        statement = self.get_object()
        line = self._line(statement, request)
        payment = record_or_404(Payment, request.data.get("payment"), "payment")
        _refused(lambda: line.match(payment))
        return Response(self.get_serializer(statement).data)

    @action(detail=True, methods=["post"])
    def post_line(self, request, pk=None):
        """A bank charge, interest, a direct debit nobody recorded: a line ({"line"}) straight to an account."""
        statement = self.get_object()
        line = self._line(statement, request)
        account = record_or_404(Account, request.data.get("account"), "account")
        party = request.data.get("party")
        party = record_or_404(Party, party, "party") if party not in (None, "") else None
        _refused(lambda: line.post_to(account, party=party, memo=request.data.get("memo", "")))
        return Response(self.get_serializer(statement).data)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        statement = self.get_object()
        _refused(statement.close)
        return Response(self.get_serializer(statement).data)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        statement = self.get_object()
        _refused(statement.reopen)
        return Response(self.get_serializer(statement).data)


class BankStatementLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = BankStatementLine.objects.select_related("statement", "payment")
    serializer_class = BankStatementLineSerializer
    filter_fields = ["statement", "payment__isnull", "journal_entry__isnull"]
    search_fields = ["description", "reference"]
    # Matched and posted through the statement, which asks which line;
    # undone here, where the line is all there is to say.
    action_permission_map = {
        "unmatch": "accounting.change_bankstatementline",
        "reverse_posting": "accounting.post_journalentry",
    }

    def _answer(self, line):
        line.refresh_from_db()
        return Response(self.get_serializer(line).data)

    @action(detail=True, methods=["post"])
    def unmatch(self, request, pk=None):
        line = self.get_object()
        _refused(line.unmatch)
        return self._answer(line)

    @action(detail=True, methods=["post"])
    def reverse_posting(self, request, pk=None):
        line = self.get_object()
        _refused(lambda: line.reverse_posting(on_date=request.data.get("date") or None))
        return self._answer(line)
