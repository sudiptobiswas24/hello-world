"""
Logins and their roles, kept from the office.

Roles were given "in the admin", which a plant without an IT person does
not open: a new clerk waited, a leaver kept signing in. Whoever holds
`auth.change_user` (the HR Admin) makes a login, gives and takes roles,
sets a password and deactivates a leaver here; the controller reads the
list. A login is never deleted — what it did stays signed by it — and
nobody changes their own roles or deactivates themselves, so one person
cannot lock the office out or let themselves in. Superusers are the
admin's and are not shown.

Nor does anyone give more than they hold. Changing another login was
asked only auth.change_user, so an HR Admin made a second login with
the Controller role, and set a real Controller's password and signed in
as them. A role the keeper holds they give; one they do not they only
propose, and it is given when someone who holds it confirms (O142, the
owner's two-person rule: apps/core/roles.py, whose `may_give` is the
one place that says who gives a role on their own). Making, changing,
reactivating or setting the password of a login ask it too: a login
holding a role its keeper may not give, a permission of its own beyond
the keeper's, or the admin site is not theirs (`check_may_administer`).

Taking access away is always the keeper's: deactivating a leaver or
taking a role off a login gives nobody anything, and the HR Admin could
not deactivate a departing rep. Nobody changes their own roles or
standing, and a superuser is asked none of this. The employees import
gives no roles at all and links only a login that holds nothing
(`holds_rights`).
"""

from django.contrib.auth import password_validation
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from .api import required
from .audit import AuditableViewSetMixin
from .roles import ProposalStatus, give_or_propose, may_give


def roles_beyond(actor, groups):
    """The names, sorted, of those of `groups` that `actor` may not give on their own (roles.may_give)."""
    return sorted(group.name for group in groups if not may_give(actor, group))


def _given_or_proposed(actor, user, groups):
    """Each role given, or proposed for someone who holds it to confirm (roles.give_or_propose)."""
    try:
        for group in groups:
            give_or_propose(actor, user, group)
    except DjangoValidationError as refused:
        raise DRFValidationError({"roles": refused.messages})


def _own_permissions(user):
    return {f"{app_label}.{codename}" for app_label, codename in
            user.user_permissions.values_list("content_type__app_label", "codename")}


def check_may_administer(actor, user):
    """
    Refuse changing a login that holds what `actor` could not give it: a
    role (may_give), a permission given to it alone, or the admin site.
    Its password set, its email changed (a reset goes there), its roles
    moved or the login switched back on, it is the actor's to sign in as.
    Taking access away, or giving or proposing a role, is not asked this
    (UserViewSet.KEEPER_GAINS_NOTHING).
    """
    if actor.is_superuser:
        return
    beyond = roles_beyond(actor, user.groups.all())
    if not beyond and _own_permissions(user) - actor.get_all_permissions():
        beyond = ["permissions of its own"]
    if not beyond and user.is_staff and not actor.is_staff:
        beyond = ["the admin site"]
    if beyond:
        raise PermissionDenied(
            f"{user.get_username()} holds {', '.join(beyond)}, which you do not: someone who does keeps that login.")


def holds_rights(user):
    """Whether a login holds anything at all: a role, a permission of its own, the admin site."""
    return bool(user.is_superuser or user.is_staff or user.groups.exists() or user.user_permissions.exists())


class UserSerializer(serializers.ModelSerializer):
    roles = serializers.ListField(child=serializers.CharField(), required=False)
    employee_number = serializers.CharField(source="employee.employee_number", read_only=True, default="")
    employee_name = serializers.CharField(source="employee.party.name", read_only=True, default="")
    # A new login's first password, given once; changed with "Set a password" after.
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email", "is_active", "last_login", "date_joined",
                  "roles", "employee_number", "employee_name", "password"]
        read_only_fields = ["last_login", "date_joined"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["roles"] = sorted(instance.groups.values_list("name", flat=True))
        # Proposed and not yet confirmed by someone who holds them (apps/core/roles.py).
        data["proposed_roles"] = sorted(instance.role_proposals.filter(status=ProposalStatus.PENDING)
                                        .values_list("group__name", flat=True))
        return data

    def validate_roles(self, names):
        groups = list(Group.objects.filter(name__in=names))
        missing = sorted(set(names) - {group.name for group in groups})
        if missing:
            raise serializers.ValidationError(f"No role {', '.join(missing)}.")
        return groups

    def validate_password(self, value):
        if value:
            try:
                password_validation.validate_password(value)
            except DjangoValidationError as refused:
                raise serializers.ValidationError(refused.messages)
        return value

    def create(self, validated_data):
        groups = validated_data.pop("roles", [])
        password = validated_data.pop("password", "")
        if not password:
            raise serializers.ValidationError({"password": ["A new login needs a first password."]})
        user = User.objects.create_user(password=password, **validated_data)
        _given_or_proposed(self.context["request"].user, user, groups)
        return user

    def update(self, instance, validated_data):
        if validated_data.pop("password", ""):
            raise serializers.ValidationError({"password": ["A password is set with 'Set a password', not saved with the rest."]})
        groups = validated_data.pop("roles", None)
        me = self.context["request"].user
        if instance.pk == me.pk and (groups is not None or validated_data.get("is_active") is False):
            raise serializers.ValidationError({"non_field_errors": ["Your own roles and standing are someone else's to change."]})
        user = super().update(instance, validated_data)
        if groups is not None:
            # Taken away at once; given, or proposed, one by one.
            user.groups.remove(*[group for group in user.groups.all() if group not in groups])
            _given_or_proposed(me, user, [group for group in groups if not user.groups.filter(pk=group.pk).exists()])
        return user


class UserViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """The logins (auth.User), each with its roles and the employee it is."""

    queryset = User.objects.filter(is_superuser=False).prefetch_related("groups").select_related("employee__party")
    serializer_class = UserSerializer
    search_fields = ["username", "first_name", "last_name", "email"]
    filter_fields = ["is_active"]
    ordering_fields = ["username", "last_login", "date_joined"]
    action_permission_map = {
        "set_password": "auth.change_user",
        "grant": "auth.change_user",
        "revoke": "auth.change_user",
        "deactivate": "auth.change_user",
        "reactivate": "auth.change_user",
    }

    # A login has no created_by of its own; the history line is still written.
    def perform_create(self, serializer):
        serializer.save()

    def perform_update(self, serializer):
        serializer.save()

    def perform_destroy(self, instance):
        raise DRFValidationError({"non_field_errors": [
            "A login is deactivated, not deleted: what it did stays signed by it."]})

    # What gives the keeper no way to be the login, so a login above them is
    # still theirs to act on: switching it off or taking a role from it (taking
    # access away), and giving it a role they hold or proposing one for its
    # holders to confirm (roles.py). Its password, email or switching it back
    # on would make it theirs (check_may_administer).
    KEEPER_GAINS_NOTHING = ("retrieve", "deactivate", "revoke", "grant")

    def get_object(self):
        """The login, and for what would let the keeper be it, one the signed-in person may keep."""
        user = super().get_object()
        if self.action not in self.KEEPER_GAINS_NOTHING:
            check_may_administer(self.request.user, user)
        return user

    def _not_myself(self, request, user, doing):
        if user.pk == request.user.pk:
            raise DRFValidationError({"non_field_errors": [f"{doing} is someone else's to do for you."]})

    def _role(self, request):
        required(request.data, "role", message="Say which role.")
        value = str(request.data["role"])
        group = Group.objects.filter(pk=value).first() if value.isdigit() else Group.objects.filter(name=value).first()
        if group is None:
            raise DRFValidationError({"role": [f"No role {value!r}."]})
        return group

    @action(detail=True, methods=["post"])
    def set_password(self, request, pk=None):
        """A new password ({"password"}), checked as any password is; the person changes it at their next sign-in if they wish."""
        user = self.get_object()
        required(request.data, "password", message="Give the new password.")
        try:
            password_validation.validate_password(str(request.data["password"]), user)
        except DjangoValidationError as refused:
            raise DRFValidationError({"password": refused.messages})
        user.set_password(str(request.data["password"]))
        user.save(update_fields=["password"])
        return Response(self.get_serializer(user).data)

    @action(detail=True, methods=["post"])
    def grant(self, request, pk=None):
        """Give this login a role ({"role"}: its name or id), or propose one the keeper does not hold (roles.py)."""
        user = self.get_object()
        self._not_myself(request, user, "Giving yourself a role")
        try:
            give_or_propose(request.user, user, self._role(request))
        except DjangoValidationError as refused:
            raise DRFValidationError({"role": refused.messages})
        return Response(self.get_serializer(user).data)

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        user = self.get_object()
        self._not_myself(request, user, "Taking a role from yourself")
        user.groups.remove(self._role(request))
        return Response(self.get_serializer(user).data)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        """A leaver: the login stops working and stays, with everything it signed."""
        user = self.get_object()
        self._not_myself(request, user, "Deactivating your own login")
        user.is_active = False
        user.save(update_fields=["is_active"])
        return Response(self.get_serializer(user).data)

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        user = self.get_object()
        user.is_active = True
        user.save(update_fields=["is_active"])
        return Response(self.get_serializer(user).data)
