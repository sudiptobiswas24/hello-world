"""
Bank reconciliation through the API: the bookkeeper keys the statement in
and matches its lines, the controller posts what the bank originated and
signs the statement off.

Posting a line is a journal entry, so it takes the right to post one; the
bookkeeper is deliberately without it.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, viewsets

from apps.core.api import ModelSerializer, takes_only
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.core.api import flag, record_or_404
from apps.core.audit import AuditableViewSetMixin
from apps.core.models import Party

from .models import Account, BankCutOffItem, BankStatement, BankStatementLine, JournalEntry, Payment, round_money
from .bank_import import import_lines


def _refused(call):
    try:
        return call()
    except DjangoValidationError as exc:
        raise DRFValidationError(exc.messages)


class BankStatementSerializer(ModelSerializer):
    bank_account_name = serializers.CharField(source="bank_account.name", read_only=True)

    class Meta:
        model = BankStatement
        fields = ["id", "bank_account", "bank_account_name", "reference", "start_date", "end_date",
                  "opening_balance", "closing_balance", "closed", "closed_at"]
        read_only_fields = ["closed", "closed_at"]


class BankStatementLineSerializer(ModelSerializer):
    payment_number = serializers.CharField(source="payment.number", read_only=True, default=None)
    returned_payment_number = serializers.CharField(source="returned_payment.number", read_only=True, default=None)
    cut_off_item_name = serializers.CharField(source="cut_off_item.__str__", read_only=True, default=None)
    resolved = serializers.BooleanField(source="is_resolved", read_only=True)

    class Meta:
        model = BankStatementLine
        fields = ["id", "statement", "date", "description", "reference", "amount", "payment",
                  "payment_number", "returned_payment", "returned_payment_number", "journal_entry",
                  "booked_entry", "cut_off_item", "cut_off_item_name", "resolved"]
        # Set by matching and posting, which check what they set.
        read_only_fields = ["payment", "returned_payment", "journal_entry", "booked_entry", "cut_off_item"]


class BankCutOffItemSerializer(ModelSerializer):
    bank_account_name = serializers.CharField(source="bank_account.name", read_only=True)
    payment_number = serializers.CharField(source="payment.number", read_only=True, default=None)
    shown_on = serializers.SerializerMethodField()

    class Meta:
        model = BankCutOffItem
        fields = ["id", "bank_account", "bank_account_name", "payment", "payment_number", "entry", "date",
                  "reference", "description", "amount", "cancelled_by", "shown_on"]
        # Set by cancel(), which checks the entry it names.
        read_only_fields = ["cancelled_by"]

    def get_shown_on(self, item):
        """The statement whose line shows a plain item, or nothing yet."""
        try:
            return str(item.line.statement)
        except BankStatementLine.DoesNotExist:
            return None


def _payment_row(payment):
    return {"id": payment.pk, "number": payment.number, "party_name": payment.party.name,
            "date": payment.payment_date, "amount": payment.signed_base_amount()}


def _return_row(payment):
    """A voided payment's reversal the bank has not shown yet (O230): the payment's row, the other way."""
    return {**_payment_row(payment), "number": f"Return of {payment.number}", "returned": True,
            "date": payment.voided_entry.date, "amount": -payment.signed_base_amount()}


def _item_row(item):
    """A cheque from before the books, outstanding at the account's cut-off (O615), as a payment's row reads."""
    return {"cut_off_item": item.pk, "number": f"Outstanding at the cut-off: {item.reference or item.pk}",
            "party_name": item.description, "date": item.date, "amount": item.amount}


def _entry_row(entry, amount):
    """Money a document booked through the bank (a TDS challan, a claim paid out), as a payment's row reads."""
    return {"entry": entry.pk, "number": entry.reference or f"JE-{entry.pk}", "party_name": entry.memo,
            "date": entry.date, "amount": amount}


class BankStatementViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = BankStatement.objects.select_related("bank_account")
    serializer_class = BankStatementSerializer
    filter_fields = ["bank_account", "closed"]
    search_fields = ["reference", "bank_account__name", "bank_account__code"]
    date_field = "end_date"
    action_permission_map = {
        "reconciliation": "accounting.view_bankstatement",
        "unmatched_entries": "accounting.view_bankstatement",
        "auto_match": "accounting.change_bankstatementline",
        "match": "accounting.change_bankstatementline",
        "post_line": "accounting.post_journalentry",
        "close": "accounting.close_bankstatement",
        "reopen": "accounting.close_bankstatement",
        "import_lines": "accounting.add_bankstatementline",
    }

    @action(detail=True, methods=["post"])
    def import_lines(self, request, pk=None):
        """The bank's export ({"text"}, CSV), checked whole; its lines kept only with commit=true and no errors."""
        takes_only(request.data, "commit", "text")
        statement = self.get_object()
        text = request.data.get("text") or ""
        if not text.strip():
            raise DRFValidationError({"text": ["Paste the file's rows, headings first."]})
        commit = flag(request.data, "commit", False)
        report = import_lines(statement, text, commit=commit)
        if commit and report["errors"]:
            # Asked to keep it and it cannot be: the refusal, row by row, beside the file.
            raise DRFValidationError({"text": [f"Row {row}{', ' + column if column else ''}: {message}"
                                               for row, column, message in report["errors"]]})
        return Response(report)

    @action(detail=True, methods=["get"])
    def reconciliation(self, request, pk=None):
        """Books to bank, with every reconciling item named."""
        statement = self.get_object()
        report = statement.reconciliation()
        # To the paisa: a sum comes back at the column's scale on
        # PostgreSQL and bare on SQLite.
        return Response({
            "ledger_balance": round_money(report["ledger_balance"]),
            "statement_balance": report["statement_balance"],
            "unpresented": [_payment_row(payment) for payment in report["unpresented"]]
            + [_return_row(payment) for payment in report["unreturned"]]
            + [{**_payment_row(payment), "number": f"Returned by the bank, not yet voided: {payment.number}"}
               for payment in report["returned_unvoided"]]
            + [_entry_row(entry, amount) for entry, amount in report["unpresented_entries"]]
            + [_item_row(item) for item in report["outstanding_at_cut_off"]],
            "unpresented_total": round_money(report["unpresented_total"]),
            "unresolved_lines": len(report["unresolved_lines"]),
            "statement_difference": round_money(report["statement_difference"]),
            "difference": round_money(report["difference"]),
            # Whether close() would sign it off: nothing unexplained, nothing left over.
            "reconciled": statement.is_reconciled(report),
        })

    @action(detail=True, methods=["get"])
    def unmatched_entries(self, request, pk=None):
        """What a document booked through this account by the statement's end that no line is matched to (O230)."""
        statement = self.get_object()
        return Response([{"id": entry.pk, "number": entry.reference or f"JE-{entry.pk}", "date": entry.date,
                          "memo": entry.memo, "amount": amount} for entry, amount in statement.unpresented_entries()])

    @action(detail=True, methods=["post"])
    def auto_match(self, request, pk=None):
        statement = self.get_object()
        not_matched = []
        matched = _refused(lambda: statement.auto_match(not_matched=not_matched))
        # What it could not take, and why, to be matched by hand (O414).
        return Response({"matched": len(matched), "not_matched": [
            {"line": line.pk, "date": line.date, "description": line.description, "amount": line.amount, "why": why}
            for line, why in not_matched]})

    def _line(self, statement, request):
        line = record_or_404(BankStatementLine, request.data.get("line"), "line")
        if line.statement_id != statement.pk:
            raise DRFValidationError({"line": ["That line is on another statement."]})
        return line

    @action(detail=True, methods=["post"])
    def match(self, request, pk=None):
        """
        Say a line ({"line"}) is a payment ({"payment"}), money booked through
        the bank ({"entry"}: a TDS challan, an expense claim paid out, a journal
        keyed by hand), or a cheque outstanding at the account's cut-off
        ({"cut_off_item"}).
        """
        takes_only(request.data, "cut_off_item", "entry", "line", "payment")
        statement = self.get_object()
        line = self._line(statement, request)
        if request.data.get("cut_off_item") not in (None, ""):
            item = record_or_404(BankCutOffItem, request.data.get("cut_off_item"), "cut_off_item")
            _refused(lambda: line.match_cut_off_item(item))
            return Response(self.get_serializer(statement).data)
        if request.data.get("entry") not in (None, ""):
            entry = record_or_404(JournalEntry, request.data.get("entry"), "entry")
            _refused(lambda: line.match_entry(entry))
            return Response(self.get_serializer(statement).data)
        payment = record_or_404(Payment, request.data.get("payment"), "payment")
        _refused(lambda: line.match(payment))
        return Response(self.get_serializer(statement).data)

    @action(detail=True, methods=["post"])
    def post_line(self, request, pk=None):
        """A bank charge, interest, a direct debit nobody recorded: a line ({"line"}) straight to an account."""
        takes_only(request.data, "account", "line", "memo", "party")
        statement = self.get_object()
        line = self._line(statement, request)
        account = record_or_404(Account, request.data.get("account"), "account")
        party = request.data.get("party")
        party = record_or_404(Party, party, "party", optional=True)
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
    queryset = BankStatementLine.objects.select_related("statement", "payment", "returned_payment", "cut_off_item")
    serializer_class = BankStatementLineSerializer
    filter_fields = ["statement", "payment__isnull", "journal_entry__isnull", "returned_payment__isnull",
                     "booked_entry__isnull", "cut_off_item__isnull"]
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
        takes_only(request.data, "date")
        line = self.get_object()
        _refused(lambda: line.reverse_posting(on_date=request.data.get("date") or None))
        return self._answer(line)


class BankCutOffItemViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """
    What was outstanding at a bank account's cut-off (O615), listed once when
    the account's reconciliation starts, then matched as the statements show
    it, or cancelled by the entry that put its money back.
    """

    queryset = BankCutOffItem.objects.select_related("bank_account", "payment", "line__statement")
    serializer_class = BankCutOffItemSerializer
    filter_fields = ["bank_account", "payment__isnull", "entry__isnull", "cancelled_by__isnull", "line__isnull"]
    search_fields = ["reference", "description", "bank_account__name"]
    date_field = "date"
    action_permission_map = {
        "cancel": "accounting.change_bankcutoffitem",
        "uncancel": "accounting.change_bankcutoffitem",
    }

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Taken back by the entry ({"entry"}) that put its money back: a cheque from before the books, cancelled."""
        takes_only(request.data, "entry")
        item = self.get_object()
        entry = record_or_404(JournalEntry, request.data.get("entry"), "entry")
        _refused(lambda: item.cancel(entry))
        item.refresh_from_db()
        return Response(self.get_serializer(item).data)

    @action(detail=True, methods=["post"])
    def uncancel(self, request, pk=None):
        item = self.get_object()
        _refused(item.uncancel)
        item.refresh_from_db()
        return Response(self.get_serializer(item).data)
