"""
The Letter of Undertaking and what setup_indian_books seeded, kept from the office (O239): both had the
admin only. An LUT is recorded under the registration in force; what was seeded is listed for the
accountant to review and marked reviewed, by whom and when.
"""

from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin

from .gst import GstSettings, LetterOfUndertaking
from .tcs import SeededDefault


class LetterOfUndertakingSerializer(serializers.ModelSerializer):
    registration = serializers.PrimaryKeyRelatedField(queryset=GstSettings.objects.all(), required=False)

    class Meta:
        model = LetterOfUndertaking
        fields = ["id", "registration", "arn", "financial_year", "filed_on", "valid_from", "valid_to"]

    def to_internal_value(self, data):
        # Filed under the registration in force unless another is named; given before the
        # one-ARN-per-registration check reads it.
        if self.instance is None and not data.get("registration"):
            registration = GstSettings.active()
            if registration is None:
                raise DRFValidationError({"registration": ["There is no GST registration in force to file it under."]})
            data = {**data, "registration": registration.pk}
        return super().to_internal_value(data)


class LetterOfUndertakingViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = LetterOfUndertaking.objects.select_related("registration")
    serializer_class = LetterOfUndertakingSerializer
    filter_fields = ["financial_year"]
    search_fields = ["arn", "financial_year"]
    date_field = "valid_from"


class GstSettingsSerializer(serializers.ModelSerializer):
    """
    The plant's own GST registration, from the office. It had neither a screen nor an API: given once, as a flag to
    setup_indian_books, and changed only in code, while Settings' "GST registrations" edits the parties' (seen on a
    first installation). Its own save() keeps it right: the GSTIN checked and its state read off it, one
    registration, and no new GSTIN once documents carry tax under the old one.
    """

    state_name = serializers.CharField(source="get_state_display", read_only=True)

    class Meta:
        model = GstSettings
        fields = ["id", "gstin", "state", "state_name", "interstate_position", "is_active", "b2cl_limit",
                  "einvoicing_from", "job_work_sac", "eway_bill_limit"]
        read_only_fields = ["state"]

    def to_internal_value(self, data):
        # A limit left blank on the screen arrives as null: it is the limit set by notification, the model's default,
        # not no limit at all (seen on a first installation: the screen's create was refused for both).
        data = {key: value for key, value in data.items()
                if not (key in ("b2cl_limit", "eway_bill_limit") and value in (None, ""))}
        return super().to_internal_value(data)


class GstSettingsViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = GstSettings.objects.select_related("interstate_position")
    serializer_class = GstSettingsSerializer
    search_fields = ["gstin"]
    # Never deleted: every return and every document's place of supply reads it. Made inactive instead.
    http_method_names = ["get", "post", "patch", "head", "options"]


class SeededDefaultSerializer(serializers.ModelSerializer):
    reviewed_by_name = serializers.CharField(source="reviewed_by.username", read_only=True, default=None)

    class Meta:
        model = SeededDefault
        fields = ["id", "label", "model", "object_id", "reviewed_by", "reviewed_by_name", "reviewed_on"]
        read_only_fields = fields


class SeededDefaultViewSet(AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = SeededDefault.objects.select_related("reviewed_by")
    serializer_class = SeededDefaultSerializer
    filter_fields = ["model"]
    search_fields = ["label", "model"]
    action_permission_map = {"review": "accounting.change_seededdefault"}

    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        """Mark it reviewed, by the person asking, today."""
        row = self.get_object()
        if row.reviewed_on:
            raise DRFValidationError([f"{row.label} was reviewed on {row.reviewed_on:%d %b %Y}."])
        row.reviewed_by, row.reviewed_on = request.user, timezone.localdate()
        row.save(update_fields=["reviewed_by", "reviewed_on", "updated_at"])
        return Response(self.get_serializer(row).data)


from .gst import GstReturnFiled  # noqa: E402


class GstReturnFiledSerializer(serializers.ModelSerializer):
    class Meta:
        model = GstReturnFiled
        fields = ["id", "return_name", "period", "filed_on", "arn"]


class GstReturnFiledViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """A GST return as filed on the portal, recorded by whoever filed it: what it reported stays (L21)."""

    queryset = GstReturnFiled.objects.all()
    serializer_class = GstReturnFiledSerializer
    http_method_names = ["get", "post", "head", "options"]
    filter_fields = ["return_name", "period"]
    search_fields = ["period", "arn"]
    date_field = "filed_on"
    ordering_fields = ["period", "filed_on"]


from .models import BooksOpening  # noqa: E402


class BooksOpeningSerializer(serializers.ModelSerializer):
    """
    The day the books opened and the equity account opening balances stand against (O251). It had an admin page
    only, while the asset typed in and the opening-balance import both refuse until it is set. BooksOpening.save()
    keeps it right: an equity account, one row, and no change once anything is posted on or before the day.
    """

    opening_account_name = serializers.SerializerMethodField()

    class Meta:
        model = BooksOpening
        fields = ["id", "opened_on", "opening_account", "opening_account_name"]

    def get_opening_account_name(self, row):
        return f"{row.opening_account.code} {row.opening_account.name}"


class BooksOpeningViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Settings, Books opening: read by whoever keeps the books, set by the Controller."""

    queryset = BooksOpening.objects.select_related("opening_account").order_by("pk")
    serializer_class = BooksOpeningSerializer
    # Set once and corrected while nothing stands on it; never deleted from the office.
    http_method_names = ["get", "post", "patch", "head", "options"]

