"""
Leads, opportunities, activities and campaigns through the API, as the
rep who keeps them and the manager who reads them all. A limited rep
reads their own (and leads nobody owns), writes only as themselves, and
opens an opportunity only on a customer they carry.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.core.api import record_or_404
from apps.core.audit import AuditableViewSetMixin
from apps.core.models import Party
from apps.core.scoping import scoped

from .crm import Activity, Campaign, Lead, Opportunity, follow_ups_due, for_rep, owner_for, pipeline


def _run(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except DjangoValidationError as exc:
        if hasattr(exc, "error_dict"):
            raise DRFValidationError({field: messages for field, messages in exc.message_dict.items()})
        raise DRFValidationError(exc.messages)


class OwnedMixin:
    """Rows the login may see, and the owner it may set."""

    unowned_too = False

    def get_queryset(self):
        return for_rep(super().get_queryset(), self.request.user, unowned_too=self.unowned_too)

    def _owner(self, serializer, current=None):
        given = serializer.validated_data.get("owner", current)
        return _run(owner_for, self.request.user, given)

    def perform_create(self, serializer):
        serializer.save(owner=self._owner(serializer))

    def perform_update(self, serializer):
        serializer.save(owner=self._owner(serializer, serializer.instance.owner))


class CampaignSerializer(serializers.ModelSerializer):
    class Meta:
        model = Campaign
        fields = ["id", "code", "name", "channel", "starts_on", "ends_on", "budget", "note"]


class CampaignViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Campaign.objects.all()
    serializer_class = CampaignSerializer
    filter_fields = ["channel"]
    search_fields = ["code", "name"]
    date_field = "starts_on"
    ordering_fields = ["starts_on", "code"]
    action_permission_map = {"results": "sales.view_campaign"}

    @action(detail=True, methods=["get"])
    def results(self, request, pk=None):
        return Response(self.get_object().results())


class LeadSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.name", read_only=True, default="")
    campaign_name = serializers.CharField(source="campaign.name", read_only=True, default="")
    converted_party_name = serializers.CharField(source="converted_party.name", read_only=True, default="")

    class Meta:
        model = Lead
        fields = ["id", "number", "company_name", "contact_name", "phone", "email", "city", "state", "source",
                  "campaign", "campaign_name", "interest", "owner", "owner_name", "status", "converted_party",
                  "converted_party_name", "converted_on", "lost_reason"]
        read_only_fields = ["number", "status", "converted_party", "converted_on", "lost_reason"]


class LeadViewSet(OwnedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Lead.objects.select_related("owner", "campaign", "converted_party")
    serializer_class = LeadSerializer
    unowned_too = True
    filter_fields = ["status", "owner", "campaign", "source"]
    search_fields = ["number", "company_name", "contact_name", "phone", "email", "city"]
    ordering_fields = ["created_at", "company_name"]
    action_permission_map = {"convert": "sales.add_opportunity", "lose": "sales.change_lead",
                             "take": "sales.change_lead"}

    @action(detail=True, methods=["post"])
    def convert(self, request, pk=None):
        """{code, name?}: the customer and its first opportunity."""
        if not request.user.has_perm("core.add_party"):
            raise PermissionDenied("Converting a lead makes a customer, which you may not.")
        lead = self.get_object()
        party, opportunity = _run(lead.convert, request.user, request.data.get("code"), request.data.get("name"),
                                  request.data.get("on_date"))
        return Response({"party": party.pk, "party_code": party.code, "opportunity": opportunity.pk,
                         "opportunity_number": opportunity.number, "lead": self.get_serializer(lead).data})

    @action(detail=True, methods=["post"])
    def lose(self, request, pk=None):
        lead = self.get_object()
        _run(lead.lose, request.data.get("reason", ""))
        return Response(self.get_serializer(lead).data)

    @action(detail=True, methods=["post"])
    def take(self, request, pk=None):
        """A lead nobody owns becomes this rep's."""
        lead = self.get_object()
        if lead.owner_id is not None:
            raise DRFValidationError([f"{lead} is {lead.owner.name}'s already."])
        rep = _run(owner_for, request.user, None)
        if rep is None:
            raise DRFValidationError(["You see every lead; a lead is taken by the rep who will carry it, or given an owner."])
        lead.owner = rep
        _run(lead.save, update_fields=["owner", "updated_at"])
        return Response(self.get_serializer(lead).data)


class OpportunitySerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    owner_name = serializers.CharField(source="owner.name", read_only=True, default="")
    campaign_name = serializers.CharField(source="campaign.name", read_only=True, default="")
    lead_number = serializers.CharField(source="lead.number", read_only=True, default="")
    quotation_number = serializers.CharField(source="quotation.number", read_only=True, default="")
    sales_order_number = serializers.CharField(source="sales_order.number", read_only=True, default="")
    chance = serializers.SerializerMethodField()
    weighted_value = serializers.SerializerMethodField()

    class Meta:
        model = Opportunity
        fields = ["id", "number", "customer", "customer_name", "title", "lead", "lead_number", "campaign",
                  "campaign_name", "owner", "owner_name", "stage", "value", "expected_on", "probability", "chance",
                  "weighted_value", "quotation", "quotation_number", "sales_order", "sales_order_number",
                  "lost_reason", "closed_on"]
        read_only_fields = ["number", "lead", "quotation", "sales_order", "lost_reason", "closed_on"]

    def get_chance(self, obj):
        return obj.chance()

    def get_weighted_value(self, obj):
        return obj.weighted_value()


class OpportunityViewSet(OwnedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Opportunity.objects.select_related("customer", "owner", "campaign", "lead", "quotation", "sales_order")
    serializer_class = OpportunitySerializer
    filter_fields = ["stage", "customer", "owner", "campaign"]
    search_fields = ["number", "title", "customer__name"]
    date_field = "expected_on"
    ordering_fields = ["expected_on", "value", "created_at"]
    action_permission_map = {"quote": "sales.add_quotation", "win": "sales.change_opportunity",
                             "lose": "sales.change_opportunity", "pipeline": "sales.view_opportunity"}

    def _check_customer(self, serializer):
        customer = serializer.validated_data.get("customer")
        if customer is not None and not scoped(Party.objects.filter(pk=customer.pk), self.request.user).exists():
            raise DRFValidationError({"customer": ["Not a customer you carry."]})

    def perform_create(self, serializer):
        self._check_customer(serializer)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self._check_customer(serializer)
        super().perform_update(serializer)

    @action(detail=True, methods=["post"])
    def quote(self, request, pk=None):
        opportunity = self.get_object()
        quotation = _run(opportunity.quote, request.data.get("quotation_date"), request.data.get("valid_until"))
        return Response({"quotation": quotation.pk, "quotation_number": quotation.number,
                         "opportunity": self.get_serializer(opportunity).data}, status=201)

    @action(detail=True, methods=["post"])
    def win(self, request, pk=None):
        from .models import SalesOrder

        opportunity = self.get_object()
        given = request.data.get("sales_order")
        order = record_or_404(SalesOrder, given, "sales_order") if given else None
        _run(opportunity.win, order, request.data.get("on_date"))
        return Response(self.get_serializer(opportunity).data)

    @action(detail=True, methods=["post"])
    def lose(self, request, pk=None):
        opportunity = self.get_object()
        _run(opportunity.lose, request.data.get("reason", ""), request.data.get("on_date"))
        return Response(self.get_serializer(opportunity).data)

    @action(detail=False, methods=["get"])
    def pipeline(self, request):
        return Response(pipeline(request.user))


class ActivitySerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.name", read_only=True, default="")
    about = serializers.SerializerMethodField()

    class Meta:
        model = Activity
        fields = ["id", "kind", "lead", "opportunity", "party", "about", "owner", "owner_name", "summary", "notes",
                  "due_on", "done_on"]
        read_only_fields = ["done_on"]

    def get_about(self, obj):
        return str(obj.subject())


class ActivityViewSet(OwnedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Activity.objects.select_related("lead", "opportunity", "party", "owner")
    serializer_class = ActivitySerializer
    filter_fields = ["kind", "lead", "opportunity", "party", "owner", "done_on__isnull"]
    search_fields = ["summary", "notes"]
    date_field = "due_on"
    ordering_fields = ["due_on", "created_at"]
    action_permission_map = {"done": "sales.change_activity", "due": "sales.view_activity"}

    @action(detail=True, methods=["post"])
    def done(self, request, pk=None):
        activity = self.get_object()
        _run(activity.done, request.data.get("on_date"))
        return Response(self.get_serializer(activity).data)

    @action(detail=False, methods=["get"])
    def due(self, request):
        """How many of the login's follow-ups have fallen due."""
        return Response({"due": follow_ups_due(request.user)})
