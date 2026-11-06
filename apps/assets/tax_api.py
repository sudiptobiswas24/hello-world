"""Tax depreciation by block over the API (assets.tax)."""

from django.utils.dateparse import parse_date
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin

from .tax import TaxBlock, TaxBlockOpening, TaxBlockRate, TaxDepreciationYear, schedule, tax_year_of, year_end


class TaxBlockSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaxBlock
        fields = ["id", "code", "name"]


class TaxBlockRateSerializer(serializers.ModelSerializer):
    section_code = serializers.CharField(source="section.code", read_only=True)

    class Meta:
        model = TaxBlockRate
        fields = ["id", "section", "section_code", "applies_from", "rate_percent", "authority", "confirmed_by",
                  "confirmed_on"]
        read_only_fields = ["confirmed_by", "confirmed_on"]


class TaxBlockOpeningSerializer(serializers.ModelSerializer):
    block_code = serializers.CharField(source="block.code", read_only=True)

    class Meta:
        model = TaxBlockOpening
        fields = ["id", "block", "block_code", "year_start", "written_down_value"]


class TaxDepreciationYearSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaxDepreciationYear
        fields = ["id", "year_start", "filed_on", "filed_by", "withdrawn_on", "withdrawn_by", "note"]
        read_only_fields = fields


class TaxBlockViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """The Income-tax blocks of assets."""

    queryset = TaxBlock.objects.all()
    serializer_class = TaxBlockSerializer
    search_fields = ["code", "name"]
    ordering_fields = ["code"]


class TaxBlockRateViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """A block's rate from the day it applies; confirm/ is the accountant's word that it is the law's."""

    queryset = TaxBlockRate.objects.select_related("section")
    serializer_class = TaxBlockRateSerializer
    filter_fields = ["section"]
    search_fields = ["section__code", "authority"]
    date_field = "applies_from"
    action_permission_map = {"confirm": "assets.confirm_taxblockrate"}

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        """{section, applies_from, rate_percent}: the figures read."""
        rate = self.get_object()
        seen = {name: request.data.get("section" if name == "section_id" else name) for name in rate.FIGURES}
        rate.confirm(request.user, seen=seen)
        return Response(self.get_serializer(rate).data)


class TaxBlockOpeningViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """A block's written-down value at the start of a tax year, from the return before the system's first."""

    queryset = TaxBlockOpening.objects.select_related("block")
    serializer_class = TaxBlockOpeningSerializer
    filter_fields = ["block"]
    date_field = "year_start"


class TaxDepreciationYearViewSet(AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """
    The tax years filed. `schedule/?year=2025-04-01` is a year's block-wise schedule beside the book
    depreciation (filed figures when filed); `file/` {"year", "note"} keeps it; `withdraw/` takes it back.
    """

    queryset = TaxDepreciationYear.objects.all()
    serializer_class = TaxDepreciationYearSerializer
    date_field = "year_start"
    action_permission_map = {
        "schedule": "assets.view_taxdepreciationyear",
        "file": "assets.file_taxdepreciationyear",
        "withdraw": "assets.file_taxdepreciationyear",
    }

    @staticmethod
    def _year(value):
        day = parse_date(str(value or ""))
        if day is None:
            raise serializers.ValidationError({"year": ["Say a day of the tax year, as 2025-04-01."]})
        return tax_year_of(day)

    @action(detail=False, methods=["get"])
    def schedule(self, request):
        year_start = self._year(request.query_params.get("year"))
        filed = TaxDepreciationYear.standing(year_start)
        rows = []
        totals = {name: 0 for name in ("depreciation", "book_depreciation", "difference")}
        for row in schedule(year_start):
            if not row.has_figures():
                continue
            difference = row.depreciation - row.book_depreciation
            rows.append({
                "block": row.block.pk, "block_code": row.block.code, "block_name": row.block.name,
                "rate_percent": row.rate_percent, "rate_confirmed": bool(row.rate and row.rate.confirmed_on),
                "opening": row.opening, "additions_full": row.additions_full, "additions_half": row.additions_half,
                "sales": row.sales, "depreciation": row.depreciation, "closing": row.closing,
                "capital_gain": row.capital_gain, "ceased": row.ceased, "book_depreciation": row.book_depreciation,
                "difference": difference, "book_value": row.book_value,
                "timing_difference": row.closing - row.book_value,
            })
            totals["depreciation"] += row.depreciation
            totals["book_depreciation"] += row.book_depreciation
            totals["difference"] += difference
        return Response({"year_start": year_start, "year_end": year_end(year_start),
                         "filed": self.get_serializer(filed).data if filed else None, "blocks": rows,
                         "totals": totals})

    @action(detail=False, methods=["post"])
    def file(self, request):
        year = TaxDepreciationYear.file(self._year(request.data.get("year")), by=request.user,
                                        note=str(request.data.get("note") or ""))
        return Response(self.get_serializer(year).data, status=201)

    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        year = self.get_object().withdraw(by=request.user, note=str(request.data.get("note") or ""))
        return Response(self.get_serializer(year).data)
