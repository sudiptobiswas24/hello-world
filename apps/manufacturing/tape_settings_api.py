"""What the tape lines ran at, recorded by the floor against a run."""

from rest_framework import serializers, viewsets

from apps.core.audit import AuditableViewSetMixin

from .tape_settings import TapeRunSetting


class TapeRunSettingSerializer(serializers.ModelSerializer):
    work_order_number = serializers.CharField(source="work_order.number", read_only=True)
    machine_code = serializers.CharField(source="machine.code", read_only=True, default="")

    class Meta:
        model = TapeRunSetting
        fields = ["id", "work_order", "work_order_number", "machine", "machine_code", "recorded_at", "draw_ratio",
                  "quench_temperature_c", "oven_temperature_c", "note"]


class TapeRunSettingViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = TapeRunSetting.objects.select_related("work_order", "machine")
    serializer_class = TapeRunSettingSerializer
    filter_fields = ["work_order", "machine"]
    date_field = "recorded_at"
    ordering_fields = ["recorded_at"]
    # What the line ran at then: a change is a new setting, never an edit.
    http_method_names = ["get", "post", "head", "options"]
