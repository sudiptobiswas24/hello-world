"""
A vendor's purchasing settings over the API, and a new vendor in one go.

Whether we buy from them (approved, on trial, blocked) and whether their
money is held are controls, not terms: the clerk who keeps a vendor's
freight and lead time does not unblock them or release their payments.
Each takes its own permission, asked when it is set or changed, on the
profile and in the one-step form alike.
"""

from rest_framework import serializers, viewsets
from rest_framework.exceptions import PermissionDenied

from apps.accounting.models import PartyTaxProfile
from apps.accounting.serializers import PartyTaxProfileSerializer
from apps.core.audit import AuditableViewSetMixin
from apps.core.models import PartyRole
from apps.core.views import NewPartyViewSet

from .models import VendorProfile

# The fields a control decides, and the permission that may set them.
CONTROLS = {
    "purchasing.set_vendor_standing": ("standing", "standing_reason"),
    "purchasing.hold_vendor_payments": ("payment_hold", "payment_hold_reason"),
}


class VendorProfileSerializer(serializers.ModelSerializer):
    party_name = serializers.CharField(source="party.name", read_only=True)

    class Meta:
        model = VendorProfile
        fields = ["id", "party", "party_name", "standing", "standing_reason", "payment_hold", "payment_hold_reason",
                  "lead_time_days", "freight_terms", "incoterm", "port_of_loading", "our_account_number"]


def refuse_controls(user, serializer, existing):
    """A control set or changed by someone it is not given to is refused, before anything is saved."""
    data = serializer.validated_data
    for permission, fields in CONTROLS.items():
        if user.has_perm(permission):
            continue
        for field in fields:
            if field not in data:
                continue
            before = getattr(existing, field) if existing is not None else VendorProfile._meta.get_field(field).default
            if data[field] != before and not (existing is None and field.endswith("_reason") and not data[field]):
                words = "approving, trialling or blocking a vendor" if "standing" in field else "holding a vendor's payments"
                raise PermissionDenied(f"{words.capitalize()} is not yours to do.")


class VendorProfileViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = VendorProfile.objects.select_related("party")
    serializer_class = VendorProfileSerializer
    filter_fields = ["party", "standing", "payment_hold"]
    search_fields = ["party__code", "party__name"]

    def perform_create(self, serializer):
        refuse_controls(self.request.user, serializer, None)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        refuse_controls(self.request.user, serializer, serializer.instance)
        super().perform_update(serializer)


class NewVendorViewSet(NewPartyViewSet):
    """
    A new vendor in one go (NewPartyViewSet): who they are, where they
    are, whom to speak to, the bank to pay, their GST, MSME and TDS
    standing, and their purchasing terms.
    """

    role = PartyRole.VENDOR
    ONES = (("tax", PartyTaxProfileSerializer, "accounting.add_partytaxprofile", PartyTaxProfile),
            ("terms", VendorProfileSerializer, "purchasing.add_vendorprofile", VendorProfile))

    def check_section(self, name, serializer, existing):
        if name == "terms":
            refuse_controls(self.request.user, serializer, existing)

