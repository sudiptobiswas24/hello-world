"""
A sales rep sees their own customers, and nobody else's.

Whose customer a party is lives on its CustomerProfile (`sales_rep`).
Whoever holds `sales.view_every_customer` (every role but Sales Rep) is
not limited at all. Anyone else is limited to the customers carried by
the party their login is (hr says which: core asks), and a login nobody
has linked to an employee sees no customer at all, rather than every
one.

What a limited rep may see of parties that are not customers (vendors,
employees) is not this module's business: they are not limited here.

The limit is applied where the rows are read (each viewset's queryset)
and checked again where one is written: a serializer's foreign key reads
every row, so an order could otherwise be posted against any customer's
id.
"""

from django.db.models import Q
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.core.models import Party, PartyRole
from apps.core.scoping import PartyScope, party_of

UNLIMITED = object()
NOBODY = object()

NOT_LINKED = ("Your login is not linked to an employee, so it is nobody's rep and sees no "
              "customer. HR links it on the employee record.")


def rep_limit(user):
    """UNLIMITED, NOBODY, or the party whose customers this login may see."""
    if user.is_superuser or user.has_perm("sales.view_every_customer"):
        return UNLIMITED
    party = party_of(user)
    return NOBODY if party is None else party


def carried_by(rep, path):
    """A Q over rows whose customer (at `path`) the rep carries."""
    if rep is NOBODY:
        return Q(pk__in=[])
    prefix = f"{path}__" if path else ""
    return Q(**{f"{prefix}customer_profile__sales_rep": rep})


class SalesScope(PartyScope):
    def visible(self, user):
        rep = rep_limit(user)
        if rep is UNLIMITED:
            return None
        customers = Party.objects.filter(role_assignments__role=PartyRole.CUSTOMER)
        return ~Q(pk__in=customers) | carried_by(rep, "")

    def refuse_create(self, user, role):
        rep = rep_limit(user)
        if rep is UNLIMITED:
            return None
        if role != PartyRole.CUSTOMER:
            return "A rep makes customers, not other parties."
        if rep is NOBODY:
            return NOT_LINKED
        from .models import SalesRep

        if not SalesRep.objects.filter(party=rep, is_active=True).exists():
            return (f"{rep} is not set up as an active sales rep, so a customer made here "
                    "would be nobody's. Ask for a sales rep record first.")
        return None

    def created(self, party, role, user):
        rep = rep_limit(user)
        if rep is UNLIMITED or role != PartyRole.CUSTOMER:
            return
        from .models import CustomerProfile

        # What the rep made is theirs, or they could not open it again.
        profile, _ = CustomerProfile.objects.get_or_create(
            party=party, defaults={"sales_rep": rep, "created_by": user, "updated_by": user})
        if profile.sales_rep_id != rep.pk:
            profile.sales_rep = rep
            profile.save()

    def refuse_change(self, user, party):
        rep = rep_limit(user)
        if rep is UNLIMITED:
            return None
        if rep is NOBODY:
            return NOT_LINKED
        if not Party.objects.filter(carried_by(rep, ""), pk=party.pk).exists():
            return "Only your own customers can be changed from here."
        return None


def _hop(value, names):
    for name in names:
        if value is None:
            return None
        value = getattr(value, name, None)
    return value


class CustomerScopedMixin:
    """
    A sales document, its lines, or anything hanging off one: read only
    for customers the login may see, written only against them.
    `customer_path` is where the customer is from the row.
    """

    customer_path = "customer"

    def get_queryset(self):
        queryset = super().get_queryset()
        rep = rep_limit(self.request.user)
        if rep is UNLIMITED:
            return queryset
        return queryset.filter(carried_by(rep, self.customer_path))

    def _customer_written(self, serializer):
        first, *rest = self.customer_path.split("__")
        start = serializer.validated_data.get(first, getattr(serializer.instance, first, None))
        return _hop(start, rest)

    def _check_customer(self, serializer):
        rep = rep_limit(self.request.user)
        if rep is UNLIMITED:
            return
        if rep is NOBODY:
            raise DRFValidationError([NOT_LINKED])
        customer = self._customer_written(serializer)
        if customer is not None and not Party.objects.filter(
                carried_by(rep, ""), pk=customer.pk).exists():
            field = self.customer_path.split("__")[0]
            raise DRFValidationError({field: ["Not one of your customers."]})

    def perform_create(self, serializer):
        self._check_customer(serializer)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self._check_customer(serializer)
        super().perform_update(serializer)
