"""The plant's licences through the API: kept, renewed, and the ones to start renewing now."""

from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .audit import AuditableViewSetMixin
from .licences import Licence, licences_due


class LicenceSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    status = serializers.SerializerMethodField()
    renew_from = serializers.DateField(read_only=True)
    renewed_by_number = serializers.CharField(source="renewed_by.licence_number", read_only=True, default="")
    renews = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Licence
        fields = ["id", "kind", "kind_label", "licence_number", "issued_by", "covers", "valid_from", "valid_to",
                  "remind_days", "renew_from", "status", "renewed_by", "renewed_by_number", "renews", "note"]
        read_only_fields = ["renewed_by"]

    def get_status(self, obj):
        return obj.status()


class LicenceViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Licence.objects.select_related("renewed_by", "renews")
    serializer_class = LicenceSerializer
    filter_fields = ["kind", "renewed_by__isnull"]
    search_fields = ["licence_number", "issued_by", "covers"]
    date_field = "valid_to"
    ordering_fields = ["valid_to", "licence_number"]
    action_permission_map = {"renew": "core.add_licence", "due": "core.view_licence"}

    @action(detail=True, methods=["post"])
    def renew(self, request, pk=None):
        licence = self.get_object()
        missing = [name for name in ("licence_number", "valid_from", "valid_to") if not request.data.get(name)]
        if missing:
            raise ValidationError({name: ["Say what the renewed licence reads."] for name in missing})
        renewal = licence.renew(request.data["licence_number"], request.data["valid_from"], request.data["valid_to"],
                                request.data.get("note") or "")
        return Response(self.get_serializer(renewal).data, status=201)

    @action(detail=False, methods=["get"])
    def due(self, request):
        return Response(self.get_serializer(licences_due(), many=True).data)
