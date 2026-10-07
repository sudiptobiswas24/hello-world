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
"""

from django.contrib.auth import password_validation
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from .api import required
from .audit import AuditableViewSetMixin


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
        user.groups.set(groups)
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
            user.groups.set(groups)
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

    def perform_destroy(self, instance):
        raise DRFValidationError({"non_field_errors": [
            "A login is deactivated, not deleted: what it did stays signed by it."]})

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
        """Give this login a role ({"role"}: its name or id)."""
        user = self.get_object()
        self._not_myself(request, user, "Giving yourself a role")
        user.groups.add(self._role(request))
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
