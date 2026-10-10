"""
Roles proposed for a login, through the API (apps/core/roles.py): read
by whoever keeps logins and by whoever holds a role proposed, confirmed
or declined by someone who holds it, and withdrawn by whoever proposed
it. Each holder finds the ones waiting for them on "Roles to confirm".
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from .api import flag
from .audit import AuditableViewSetMixin
from .roles import RoleProposal, waiting_for


def _who(user):
    return (user.get_full_name() or user.get_username()) if user else ""


class RoleProposalSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    role = serializers.CharField(source="group.name", read_only=True)
    proposed_by_name = serializers.SerializerMethodField()
    decided_by_name = serializers.SerializerMethodField()
    may_confirm = serializers.SerializerMethodField()
    may_decline = serializers.SerializerMethodField()

    class Meta:
        model = RoleProposal
        fields = ["id", "user", "username", "group", "role", "status", "proposed_by", "proposed_by_name",
                  "proposed_at", "decided_by", "decided_by_name", "decided_at", "may_confirm", "may_decline"]
        read_only_fields = fields

    def get_proposed_by_name(self, proposal):
        return _who(proposal.proposed_by)

    def get_decided_by_name(self, proposal):
        return _who(proposal.decided_by)

    def get_may_confirm(self, proposal):
        return proposal.status == "pending" and proposal.may_confirm(self.context["request"].user) is None

    def get_may_decline(self, proposal):
        return proposal.status == "pending" and proposal.may_decline(self.context["request"].user) is None


class RoleProposalViewSet(AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Proposed roles: every one to whoever keeps logins; to anyone else, those for a role they hold or they proposed."""

    queryset = RoleProposal.objects.select_related("user", "group", "proposed_by", "decided_by")
    serializer_class = RoleProposalSerializer
    filter_fields = ["user", "status", "group"]
    search_fields = ["user__username", "group__name"]
    date_field = "proposed_at"
    ordering_fields = ["proposed_at"]
    # Who of those holding it may confirm or decline one is the proposal's own question (roles.py).
    action_permission_map = {"confirm": "core.confirm_roleproposal", "decline": "core.confirm_roleproposal"}
    # ?waiting=true: those the login could confirm now (the inbox's count).
    extra_params = ("waiting",)

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if user.is_superuser or user.has_perm("auth.view_user"):
            return queryset
        return queryset.filter(Q(group__in=user.groups.all()) | Q(proposed_by=user))

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        if flag(self.request.query_params, "waiting", False):
            queryset = queryset.filter(pk__in=waiting_for(self.request.user))
        return queryset

    def _decided(self, request, verb):
        proposal = self.get_object()
        try:
            getattr(proposal, verb)(request.user)
        except DjangoValidationError as refused:
            raise DRFValidationError(refused.messages)
        return Response(self.get_serializer(proposal).data)

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        """
        Give the proposed role: by someone who holds it, not its proposer,
        not on their own login. A password or email its proposer set no
        longer works once it is given (roles.lapse_on_giving, O157);
        {"password"} issues the login's next one with the confirm, by a
        confirmer who may keep the login (users_api.check_may_know).
        """
        from .users_api import issue_password

        password = str(request.data.get("password") or "")
        with transaction.atomic():
            response = self._decided(request, "confirm")
            if password:
                proposal = RoleProposal.objects.select_related("user").get(pk=response.data["id"])
                issue_password(request.user, proposal.user, password)
        return response

    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        """Leave the role ungiven: by someone who holds it, or withdrawn by whoever proposed it."""
        return self._decided(request, "decline")
