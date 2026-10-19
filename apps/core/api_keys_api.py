"""The API for integrations and their keys (apps/core/api_keys.py says the rules)."""

import datetime

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from .api_keys import ApiKey, ApiKeyUse, Integration, issue
from .audit import AuditableViewSetMixin



class IntegrationSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    user = serializers.IntegerField(source="user.pk", read_only=True)

    class Meta:
        model = Integration
        fields = ["id", "name", "description", "user", "username", "created_by", "created_at", "updated_by",
                  "updated_at"]
        read_only_fields = ["created_by", "created_at", "updated_by", "updated_at"]


class ApiKeySerializer(serializers.ModelSerializer):
    integration_name = serializers.CharField(source="integration.name", read_only=True)
    state = serializers.SerializerMethodField()
    issuer = serializers.CharField(source="created_by.username", read_only=True, default="")
    issuer_left = serializers.SerializerMethodField()

    class Meta:
        model = ApiKey
        fields = ["id", "integration", "integration_name", "label", "prefix", "created_by", "created_at",
                  "updated_by", "updated_at", "expires_at", "revoked_at", "revoked_by", "last_used_at", "state",
                  "issuer", "issuer_left", "issuer_left_at", "issuer_could_give"]
        read_only_fields = ["integration", "expires_at"]

    def get_issuer_left(self, key):
        """Its issuer left or may no longer issue keys: it still works, and is theirs to replace (review F5)."""
        return key.issuer_left_at is not None

    def get_state(self, key):
        return "revoked" if key.revoked_at else "expired" if key.expires_at <= timezone.now() else "active"


class ApiKeyUseSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiKeyUse
        fields = ["id", "key", "at", "method", "path", "address"]


class IntegrationViewSet(AuditableViewSetMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin,
                         viewsets.ReadOnlyModelViewSet):
    """
    Made with a login of its own that has no password: nobody signs in as
    it. Its rights are given on the Logins screen, as any login's are.
    """

    queryset = Integration.objects.select_related("user")
    action_permission_map = {"issue_key": "core.add_apikey"}
    serializer_class = IntegrationSerializer
    search_fields = ["name", "description"]

    def perform_create(self, serializer):
        from .roles import KeptCredential, record_credential

        name = serializer.validated_data["name"]
        username = f"integration-{'-'.join(name.lower().split())}"[:150]
        if User.objects.filter(username=username).exists():
            raise DRFValidationError({"name": [f"There is already a login named {username}."]})
        with transaction.atomic():
            user = User.objects.create_user(username, first_name=name[:150])  # no password: nobody signs in as it
            # Who made it is kept, as for any login (roles.KeptCredential).
            record_credential(user, KeptCredential.LOGIN, self.request.user)
            super().perform_create(serializer, user=user)

    @action(detail=True, methods=["post"])
    def issue_key(self, request, pk=None):
        """A new key ({"label", "expires_on"}), shown in this answer and never again."""
        from .users_api import check_may_know

        integration = self.get_object()
        if not request.user.has_perm("core.add_apikey"):
            raise PermissionDenied("Issuing an API key needs the right to add API keys.")
        check_may_know(request.user, integration.user)
        from .second_factor import required

        if required(integration.user):
            raise DRFValidationError(f"{integration} holds a right that needs a second factor, which a key alone "
                                     "does not give: take it away first. An integration holds only what it needs.")
        expires_at = None
        if request.data.get("expires_on"):
            day = serializers.DateField().to_internal_value(request.data["expires_on"])
            expires_at = timezone.make_aware(datetime.datetime.combine(day, datetime.time.max))
        with transaction.atomic():
            key, row = issue(integration, request.user, str(request.data.get("label", ""))[:80], expires_at)
        answer = ApiKeySerializer(row).data
        answer["key"] = key
        return Response(answer, status=201)


class ApiKeyViewSet(AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = ApiKey.objects.select_related("integration")
    action_permission_map = {"revoke": "core.change_apikey"}
    serializer_class = ApiKeySerializer
    filter_fields = ["integration"]

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        """Stops the key at once; it stays listed, with who revoked it."""
        key = self.get_object()
        if not request.user.has_perm("core.change_apikey"):
            raise PermissionDenied("Revoking an API key needs the right to change API keys.")
        revoked = ApiKey.objects.filter(pk=key.pk, revoked_at__isnull=True).update(
            revoked_at=timezone.now(), revoked_by=request.user)
        if not revoked:
            raise DRFValidationError("This key was already revoked.")
        key.refresh_from_db()
        return Response(self.get_serializer(key).data)

    @action(detail=True, methods=["get"])
    def uses(self, request, pk=None):
        """What the key signed in, newest first."""
        key = self.get_object()
        page = self.paginate_queryset(ApiKeyUse.objects.filter(key=key))
        return self.get_paginated_response(ApiKeyUseSerializer(page, many=True).data)


