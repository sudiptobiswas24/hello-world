"""The restatement of foreign balances at the closing rate, over the API (accounting.restatement)."""

from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin
from apps.core.models import to_date

from .restatement import Restatement, RestatementLine, RestatementRate


class RestatementRateSerializer(serializers.ModelSerializer):
    currency_code = serializers.CharField(source="currency.code", read_only=True)

    class Meta:
        model = RestatementRate
        fields = ["id", "currency", "currency_code", "rate", "rate_from"]
        read_only_fields = fields


class RestatementLineSerializer(serializers.ModelSerializer):
    currency_code = serializers.CharField(source="currency.code", read_only=True)
    account_code = serializers.CharField(source="account.code", read_only=True)
    party_name = serializers.CharField(source="party.name", read_only=True, default="")

    class Meta:
        model = RestatementLine
        fields = ["id", "item_type", "item_id", "label", "party", "party_name", "account", "account_code",
                  "currency", "currency_code", "side", "open_amount", "rate", "carried_base", "restated_base",
                  "difference"]
        read_only_fields = fields


class RestatementSerializer(serializers.ModelSerializer):
    lines = RestatementLineSerializer(many=True, read_only=True)
    rates = RestatementRateSerializer(many=True, read_only=True)

    class Meta:
        model = Restatement
        fields = ["id", "number", "as_of", "reverses_on", "gain_account", "loss_account", "journal_entry",
                  "reversal_entry", "voided_on", "void_entry", "void_reversal_entry", "memo", "lines", "rates",
                  "created_at"]
        read_only_fields = fields


class RestatementViewSet(AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """
    Foreign balances restated at the closing rate. `restate/` {"as_of", "rates": {code: rate}, "memo"} makes
    one; `preview/?as_of=` shows what it would restate; `void/` {"date"} takes one back.
    """

    search_fields = ["number", "memo"]
    filter_fields = []
    date_field = "as_of"
    ordering_fields = ["as_of", "number"]
    queryset = Restatement.objects.select_related("journal_entry").prefetch_related(
        "lines__currency", "lines__account", "lines__party", "rates__currency")
    serializer_class = RestatementSerializer
    action_permission_map = {
        "restate": "accounting.post_restatement",
        "void": "accounting.post_restatement",
        "preview": "accounting.post_restatement",
    }

    @action(detail=False, methods=["post"])
    def restate(self, request):
        made = Restatement.make(request.data.get("as_of") or None, by=request.user,
                                rates=request.data.get("rates") or None, memo=str(request.data.get("memo") or ""))
        return Response(self.get_serializer(self.get_queryset().get(pk=made.pk)).data, status=201)

    @action(detail=False, methods=["get"])
    def preview(self, request):
        as_of = to_date(request.query_params.get("as_of")) if request.query_params.get("as_of") else None
        if as_of is None:
            raise serializers.ValidationError({"as_of": ["Say the day to restate as at."]})
        rows = []
        for item in Restatement.preview(as_of):
            row = item.currency.rates.filter(valid_from__lte=as_of).order_by("-valid_from").first()
            rows.append({"label": item.label, "item_type": item.item._meta.label_lower, "item_id": item.item.pk,
                         "account_code": item.account.code, "party_name": item.party.name if item.party else "",
                         "currency_code": item.currency.code, "side": item.side, "open_amount": item.open_amount,
                         "carried_base": item.side * item.carried,
                         "rate": row.rate if row else None})
        return Response({"as_of": as_of, "items": rows})

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        restatement = self.get_object()
        restatement.void(on_date=request.data.get("date") or None, by=request.user)
        return Response(self.get_serializer(self.get_queryset().get(pk=restatement.pk)).data)
