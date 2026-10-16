"""The contract labour registers through the API, kept by HR."""

from rest_framework import serializers, viewsets

from apps.core.audit import AuditableViewSetMixin

from .contract_labour import ContractWorker, LabourContractor


class LabourContractorSerializer(serializers.ModelSerializer):
    party_name = serializers.CharField(source="party.name", read_only=True)

    class Meta:
        model = LabourContractor
        fields = ["id", "party", "party_name", "licence_number", "licence_valid_to", "work_nature", "max_workers",
                  "is_active"]


class LabourContractorViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = LabourContractor.objects.select_related("party")
    serializer_class = LabourContractorSerializer
    filter_fields = ["is_active"]
    search_fields = ["party__name", "licence_number", "work_nature"]
    # A register is kept, not deleted.
    http_method_names = ["get", "post", "patch", "put", "head", "options"]


class ContractWorkerSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContractWorker
        fields = ["id", "contractor", "name", "gender", "designation", "daily_wage", "joined_on", "left_on"]


class ContractWorkerViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = ContractWorker.objects.select_related("contractor__party")
    serializer_class = ContractWorkerSerializer
    filter_fields = ["contractor", "left_on__isnull"]
    search_fields = ["name", "designation"]
    date_field = "joined_on"
    http_method_names = ["get", "post", "patch", "put", "head", "options"]
