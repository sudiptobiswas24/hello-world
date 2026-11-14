"""How each kind of document is numbered: its prefix, padding and the number it gives next."""

from rest_framework import serializers, viewsets

from .audit import AuditableViewSetMixin
from .models import DocumentSequence


class DocumentSequenceSerializer(serializers.ModelSerializer):
    # What the next document will be called, worked out and not consumed.
    next_value = serializers.SerializerMethodField()

    class Meta:
        model = DocumentSequence
        fields = ["id", "code", "name", "prefix", "suffix", "padding", "next_number", "include_year",
                  "reset_yearly", "next_value"]

    def get_next_value(self, obj):
        return obj.peek()


class DocumentSequenceViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = DocumentSequence.objects.all()
    serializer_class = DocumentSequenceSerializer
    search_fields = ["code", "name", "prefix"]
    # Not deleted: the next document would start it again at 1.
    http_method_names = ["get", "post", "patch", "put", "head", "options"]
