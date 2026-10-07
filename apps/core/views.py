from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.contrib.auth.models import Group
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .api import refused_by_database
from .audit import AuditableViewSetMixin
from .scoping import created, refuse_change, refuse_create, scoped, visible_parties
from .models import (
    Address,
    Company,
    Contact,
    Country,
    Currency,
    ExchangeRate,
    Party,
    PartyBankAccount,
    PartyRole,
    PartyRoleAssignment,
    PartyTag,
    PaymentTerms,
    UnitOfMeasure,
)
from .serializers import (
    AddressSerializer,
    CompanySerializer,
    ContactSerializer,
    CountrySerializer,
    CurrencySerializer,
    ExchangeRateSerializer,
    PartyBankAccountSerializer,
    PartyRoleAssignmentSerializer,
    PartySerializer,
    PartyTagSerializer,
    PaymentTermsSerializer,
    UnitOfMeasureSerializer,
)


class CountryViewSet(viewsets.ModelViewSet):
    queryset = Country.objects.all()
    serializer_class = CountrySerializer
    search_fields = ["code", "name"]


class CurrencyViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Currency.objects.all()
    serializer_class = CurrencySerializer
    search_fields = ["code", "name"]

    @action(detail=True, methods=["get"])
    def rate(self, request, pk=None):
        """Effective rate against the base currency, optionally ?on=YYYY-MM-DD."""
        currency = self.get_object()
        on_date = request.query_params.get("on")
        try:
            rate = currency.rate_on(on_date or None)
        except (DjangoValidationError, ValueError) as exc:
            raise DRFValidationError(getattr(exc, "messages", [str(exc)]))
        return Response({"currency": currency.code, "on": on_date, "rate": rate})


class ExchangeRateViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = ExchangeRate.objects.select_related("currency")
    serializer_class = ExchangeRateSerializer
    filter_fields = ["currency"]
    search_fields = ["currency__code", "currency__name"]
    date_field = "valid_from"
    ordering_fields = ["valid_from"]


class UnitOfMeasureViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = UnitOfMeasure.objects.select_related("base_unit")
    serializer_class = UnitOfMeasureSerializer
    filter_fields = ["category"]
    search_fields = ["code", "name"]


# Given with a new party, the role it is made in, made with it or not at
# all: a customer saved without its role would be in no customer list.
# Trading roles only; making someone an employee is not a clerk's to do
# from a sales screen.
TRADING_ROLES = {PartyRole.CUSTOMER, PartyRole.VENDOR}


def make_party(serializer, role, user):
    """
    A party made from the office, in a trading role or none: asked what
    the login's scope asks of a new one, and handed to it once made.
    """
    if role is not None and role not in TRADING_ROLES:
        raise DRFValidationError({"role": [f"A new party can be made a customer or a vendor here, not {role!r}."]})
    said = refuse_create(user, role)
    if said:
        raise DRFValidationError([said])
    with transaction.atomic():
        # Who made it, as AuditableViewSetMixin would have said: an
        # override had once dropped it for every party made in the office.
        party = serializer.save(created_by=user, updated_by=user)
        if role is not None:
            PartyRoleAssignment.objects.create(party=party, role=role, created_by=user, updated_by=user)
        created(party, role, user)
    return party


class NewPartyViewSet(AuditableViewSetMixin, viewsets.GenericViewSet):
    """
    A new party in its trading role in one go: who they are (`party`),
    where they are (`addresses`), whom to speak to (`contacts`), and the
    sections the module adds (`ONES`: its tax standing, its terms, its
    bank). Made together or not at all, so a refused GSTIN does not leave
    a party with no tax standing behind it, and every refused section is
    named at once. Each section takes the permission its own screen takes;
    once made, the party is changed section by section on its page.
    """

    queryset = Party.objects.all()  # making one takes core.add_party
    serializer_class = PartySerializer
    role = None
    LISTS = (("addresses", AddressSerializer, "core.add_address"),
             ("contacts", ContactSerializer, "core.add_contact"),
             ("bank_accounts", PartyBankAccountSerializer, "core.add_partybankaccount"))
    # (name, serializer, permission, model): a module's one-per-party sections.
    ONES = ()

    def create(self, request):
        user, data = request.user, request.data
        for name, _, needed, *_ in self.LISTS + self.ONES:
            if data.get(name) and not user.has_perm(needed):
                raise PermissionDenied(f"Filling in {name} is not yours to do; leave it empty, and whoever "
                                       f"keeps it fills it in on the {self.role}'s page.")
        party_given = data.get("party")
        if not isinstance(party_given, dict):
            raise DRFValidationError({"party": [f"Say who the {self.role} is."]})
        party_serializer = self.get_serializer(data=party_given)
        if not party_serializer.is_valid():
            raise DRFValidationError({"party": party_serializer.errors})
        errors = {}
        with transaction.atomic():
            party = make_party(party_serializer, self.role, user)
            context = self.get_serializer_context()
            sections = [(f"{name}.{i}", serializer, None, row)
                        for name, serializer, _ in self.LISTS for i, row in enumerate(data.get(name) or [])]
            # A section the party's scope made already (a rep's own customer profile) is filled in, not made twice.
            sections += [(name, serializer, model.objects.filter(party=party).first(), data[name])
                         for name, serializer, _, model in self.ONES if data.get(name)]
            for key, serializer_class, existing, given in sections:
                if not isinstance(given, dict):
                    errors[key] = ["Expected the section's fields."]
                    continue
                serializer = serializer_class(existing, data={**given, "party": party.pk},
                                              partial=existing is not None, context=context)
                if not serializer.is_valid():
                    errors[key] = serializer.errors
                    continue
                try:
                    with transaction.atomic():
                        self.check_section(key.split(".")[0], serializer, existing)
                        serializer.save(**({"updated_by": user} if existing else {"created_by": user, "updated_by": user}))
                except DjangoValidationError as exc:
                    errors[key] = exc.message_dict if hasattr(exc, "error_dict") else {"non_field_errors": exc.messages}
                except IntegrityError as exc:
                    errors[key] = {"non_field_errors": refused_by_database(exc)}
            if errors:
                # Nothing of it stays: the party and every section saved before the refusal go too.
                raise DRFValidationError(errors)
        return Response(self.get_serializer(Party.objects.get(pk=party.pk)).data, status=201)

    def check_section(self, name, serializer, existing):
        """Hook: refuse what this login may not set in a section, beyond the section's own permission."""


class PartyViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    search_fields = ["code", "name", "legal_name", "email", "phone", "tax_id"]
    filter_fields = ["is_active", "role_assignments__role"]
    ordering_fields = ["code", "name"]
    # ?gst=unknown: customers whose GST standing nobody has recorded, or
    # registered with no GSTIN (the morning check's list).
    extra_params = ("gst",)

    queryset = Party.objects.prefetch_related("role_assignments", "addresses", "contacts", "tags")
    serializer_class = PartySerializer

    def get_queryset(self):
        return scoped(super().get_queryset(), self.request.user)

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        if self.request.query_params.get("gst") == "unknown":
            from apps.web.checks import customers_without_gst

            queryset = customers_without_gst(queryset)
        return queryset

    def perform_create(self, serializer):
        make_party(serializer, self.request.data.get("role"), self.request.user)

    def perform_update(self, serializer):
        said = refuse_change(self.request.user, serializer.instance)
        if said:
            raise DRFValidationError([said])
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        said = refuse_change(self.request.user, instance)
        if said:
            raise DRFValidationError([said])
        super().perform_destroy(instance)

    action_permission_map = {"roles": "core.change_party"}

    @action(detail=True, methods=["post", "delete"])
    def roles(self, request, pk=None):
        """
        A vendor who also buys from us made a customer too ({"role"}), or
        a role given by mistake taken back (DELETE ?role=). Asked as a new
        party in that role is asked: a rep makes only customers, and the
        customer is theirs once made.
        """
        party = self.get_object()
        role = request.data.get("role") if request.method == "POST" else request.query_params.get("role")
        if role not in TRADING_ROLES:
            raise DRFValidationError({"role": [f"A party can be made a customer or a vendor here, not {role!r}."]})
        user = request.user
        said = refuse_change(user, party)
        held = party.role_assignments.filter(role=role)
        if request.method == "POST":
            said = said or refuse_create(user, role)
            if not said and held.exists():
                said = f"{party} is a {role} already."
            if said:
                raise DRFValidationError([said])
            with transaction.atomic():
                PartyRoleAssignment.objects.create(party=party, role=role, created_by=user, updated_by=user)
                created(party, role, user)
        else:
            if not said and not held.exists():
                said = f"{party} is not a {role}."
            said = said or _in_use_as(party, role)
            if said:
                raise DRFValidationError([said])
            held.delete()
        return Response(self.get_serializer(Party.objects.get(pk=party.pk)).data)


def _in_use_as(party, role):
    """
    Why `party` cannot stop being a `role`: a document names it as one.
    Found by field name, so the kernel asks the trading modules without
    importing them; taken away, the party would drop out of the customer
    list its orders were made from.
    """
    for relation in Party._meta.related_objects:
        if relation.field.name == role and relation.related_model._base_manager.filter(
                **{role: party}).exists():
            return (f"{party} is the {role} on {relation.related_model._meta.verbose_name_plural}; "
                    f"it stays a {role}.")
    return None


class PartyScopedMixin:
    """
    A record that belongs to a party (an address, a contact): read only
    where the party may be seen, and made or moved only onto one that may
    be changed.
    """

    def get_queryset(self):
        return scoped(super().get_queryset(), self.request.user, "party")

    def _check_party(self, serializer):
        party = serializer.validated_data.get("party") or getattr(serializer.instance, "party", None)
        if party is None:
            return
        limit = visible_parties(self.request.user)
        if limit is not None and not Party.objects.filter(limit, pk=party.pk).exists():
            raise DRFValidationError({"party": ["Not one of the parties you can see."]})
        said = refuse_change(self.request.user, party)
        if said:
            raise DRFValidationError({"party": [said]})

    def perform_create(self, serializer):
        self._check_party(serializer)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self._check_party(serializer)
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        said = refuse_change(self.request.user, instance.party)
        if said:
            raise DRFValidationError([said])
        super().perform_destroy(instance)


class PartyRoleAssignmentViewSet(PartyScopedMixin, viewsets.ReadOnlyModelViewSet):
    # Given and taken through the party (PartyViewSet.roles), which asks
    # what making a party in that role asks; written here, nothing did.
    queryset = PartyRoleAssignment.objects.all()
    serializer_class = PartyRoleAssignmentSerializer
    filter_fields = ["party", "role"]


class AddressViewSet(PartyScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Address.objects.select_related("country", "party")
    serializer_class = AddressSerializer
    filter_fields = ["party", "address_type", "is_active", "is_primary"]


class ContactViewSet(PartyScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Contact.objects.select_related("party")
    serializer_class = ContactSerializer
    filter_fields = ["party", "is_active", "is_primary"]
    search_fields = ["first_name", "last_name", "email", "phone", "mobile"]


class PartyBankAccountViewSet(PartyScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PartyBankAccount.objects.select_related("party", "currency")
    serializer_class = PartyBankAccountSerializer
    filter_fields = ["party", "is_active"]


class PartyTagViewSet(viewsets.ModelViewSet):
    queryset = PartyTag.objects.all()
    serializer_class = PartyTagSerializer
    search_fields = ["name", "description"]


class PaymentTermsViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PaymentTerms.objects.all()
    serializer_class = PaymentTermsSerializer
    filter_fields = ["is_active"]
    search_fields = ["code", "name"]


class CompanyViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Company.objects.all()
    serializer_class = CompanySerializer


# What other modules add to /me/: hr says which employee the login is.
# Registered by them (their apps.py), so core imports none of them.
ME_EXTRAS = []


def register_me_extra(provider):
    if provider not in ME_EXTRAS:
        ME_EXTRAS.append(provider)


class MeView(APIView):
    """
    Who is signed in, and what they may do: what the office application
    reads to decide which screens and buttons to offer.

    Offering is all it decides. Every request is still checked against
    the same permissions by the view that answers it; a screen hidden
    here is a convenience, never the lock.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        answer = {
            "id": user.pk,
            "username": user.get_username(),
            "name": user.get_full_name() or user.get_username(),
            "is_superuser": user.is_superuser,
            "roles": sorted(user.groups.values_list("name", flat=True)),
            "permissions": sorted(user.get_all_permissions()),
            "company": Company.get().name,
            # What "today" means: the screens count the plant's day as timezone.localdate() does.
            "time_zone": settings.TIME_ZONE,
        }
        for provider in ME_EXTRAS:
            answer.update(provider(user))
        return Response(answer)


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Group
        fields = ["id", "name"]


class RoleViewSet(viewsets.ReadOnlyModelViewSet):
    """The roles there are, to choose one: an approval tier names who signs."""

    queryset = Group.objects.order_by("name")
    serializer_class = RoleSerializer
    search_fields = ["name"]
