from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin
from apps.core.models import to_date

from .models import (
    AssetCategory,
    AssetStatus,
    DepreciationEntry,
    FixedAsset,
    asset_register,
)
from .serializers import (
    AssetCategorySerializer,
    DepreciationEntrySerializer,
    FixedAssetSerializer,
)


def _run(callable_, *args, **kwargs):
    """Let a model's refusal reach the caller as the sentence it wrote."""
    try:
        return callable_(*args, **kwargs)
    except DjangoValidationError as exc:
        raise DRFValidationError(exc.messages)


def _date(request, name):
    value = request.data.get(name) or request.query_params.get(name)
    try:
        return to_date(value) if value else None
    except (TypeError, ValueError):
        raise DRFValidationError([f"{name} must be a date."])


class AssetCategoryViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = AssetCategory.objects.all()
    serializer_class = AssetCategorySerializer
    filter_fields = ["is_active", "method"]
    search_fields = ["code", "name"]


class DepreciationEntryViewSet(viewsets.ReadOnlyModelViewSet):
    """Each month charged — a record, made only by depreciating."""

    queryset = DepreciationEntry.objects.select_related("asset").all()
    serializer_class = DepreciationEntrySerializer
    filter_fields = ["asset"]
    date_field = "period_end"


class FixedAssetViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """
    Fixed assets, and the four things that happen to one: going into
    service, being depreciated, being disposed of, and — for a draft
    capitalised from a bill — being un-capitalised.

    Disposing of an asset takes its own permission, because it takes
    value off the balance sheet and books a loss; a clerk who can
    register a lathe is not thereby someone who can write one off.
    """

    queryset = FixedAsset.objects.select_related("category", "vendor").all()
    serializer_class = FixedAssetSerializer
    filter_fields = ["category", "status"]
    search_fields = ["number", "name"]
    date_field = "acquisition_date"
    # Depreciating, capitalising and undoing it all post to the ledger:
    # they take the right to post, not merely the right to add an asset.
    action_permission_map = {
        "dispose": "assets.dispose_fixedasset",
        "depreciate": "accounting.post_journalentry",
        "depreciate_all": "accounting.post_journalentry",
        "place_in_service": "accounting.post_journalentry",
        "uncapitalise": "accounting.post_journalentry",
    }

    def perform_update(self, serializer):
        _run(serializer.save)

    def perform_create(self, serializer):
        _run(serializer.save)

    def perform_destroy(self, instance):
        _run(instance.delete)

    @action(detail=True, methods=["post"], url_path="place-in-service")
    def place_in_service(self, request, pk=None):
        asset = self.get_object()
        _run(asset.place_in_service, on_date=_date(request, "on_date"))
        return Response(self.get_serializer(asset).data)

    @action(detail=True, methods=["post"])
    def depreciate(self, request, pk=None):
        asset = self.get_object()
        made = _run(asset.depreciate, through=_date(request, "through"))
        return Response({
            "charged": [
                {"period_end": row.period_end, "amount": row.amount}
                for row in made
            ],
            "asset": self.get_serializer(asset).data,
        })

    @action(detail=True, methods=["post"])
    def dispose(self, request, pk=None):
        """
        Take it off the books. `proceeds` shows the gain or loss on the
        entry; the sale itself is invoiced to the buyer with the
        category's disposal account as revenue — this banks nothing.
        """
        asset = self.get_object()
        try:
            proceeds = Decimal(str(request.data.get("proceeds") or "0"))
        except InvalidOperation:
            raise DRFValidationError(["proceeds must be a number."])
        _run(
            asset.dispose, on_date=_date(request, "on_date"), proceeds=proceeds,
            memo=request.data.get("memo", ""),
        )
        return Response(self.get_serializer(asset).data)

    @action(detail=True, methods=["post"])
    def uncapitalise(self, request, pk=None):
        asset = self.get_object()
        _run(asset.uncapitalise, on_date=_date(request, "on_date"))
        return Response(self.get_serializer(asset).data)

    @action(detail=False, methods=["post"], url_path="depreciate-all")
    def depreciate_all(self, request):
        """
        The month-end run: every asset in service, charged through
        `through`. All or nothing — a run that stops half-way leaves a
        month charged for some machines and not others.
        """
        through = _date(request, "through")
        charged = []
        with transaction.atomic():
            for asset in FixedAsset.objects.filter(status=AssetStatus.IN_SERVICE):
                for row in _run(asset.depreciate, through=through):
                    charged.append({
                        "asset": asset.number, "period_end": row.period_end,
                        "amount": row.amount,
                    })
        return Response({"charged": charged})

    @action(detail=False, methods=["get"])
    def register(self, request):
        """Cost, depreciation and net book value as they stood on `as_of`."""
        category = None
        code = request.query_params.get("category")
        if code:
            category = AssetCategory.objects.filter(code=code).first()
            if category is None:
                raise DRFValidationError([f"No asset category {code}."])
        rows = _run(asset_register, as_of=_date(request, "as_of"),
                    category=category)
        return Response([
            {
                "asset": row["asset"].number,
                "name": row["asset"].name,
                "category": row["category"].code,
                "cost": row["cost"],
                "accumulated": row["accumulated"],
                "net_book_value": row["net_book_value"],
                "monthly_charge": row["monthly_charge"],
            }
            for row in rows
        ])
