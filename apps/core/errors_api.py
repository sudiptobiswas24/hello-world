"""The errors people hit, listed for whoever keeps the system, and marked dealt with."""

from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .errors import ServerError


class ServerErrorSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()
    resolved_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ServerError
        fields = ["id", "ref", "happened_at", "path", "method", "user", "user_name", "kind", "message",
                  "traceback", "resolved_at", "resolved_by", "resolved_by_name", "note"]
        read_only_fields = ["ref", "happened_at", "path", "method", "user", "kind", "message", "traceback",
                            "resolved_at", "resolved_by", "note"]

    @staticmethod
    def _name(user):
        return (user.get_full_name() or user.username) if user else ""

    def get_user_name(self, row):
        return self._name(row.user)

    def get_resolved_by_name(self, row):
        return self._name(row.resolved_by)


class ServerErrorViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read only: an error is a fact of what happened. The two actions say
    what was done about it, and both take the right to change the record.
    """

    queryset = ServerError.objects.select_related("user", "resolved_by")
    serializer_class = ServerErrorSerializer
    filter_fields = ["resolved_at__isnull", "kind", "user"]
    search_fields = ["ref", "path", "kind", "message"]
    date_field = "happened_at"
    ordering_fields = ["happened_at", "ref"]
    action_permission_map = {"resolve": "core.change_servererror", "reopen": "core.change_servererror"}

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        row = self.get_object()
        row.resolve(by=request.user, note=str(request.data.get("note") or "")[:255])
        return Response(self.get_serializer(row).data)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        row = self.get_object()
        row.reopen()
        return Response(self.get_serializer(row).data)
