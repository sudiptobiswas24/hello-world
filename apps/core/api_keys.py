"""
API keys for integrations (L12): the scale bridge (deploy/scale_bridge),
a GST service provider and the WhatsApp channel later.

- A key belongs to an Integration: a named login of its own that no
  person signs in as (it has no password) and that holds only the rights
  given to it, as any login is given them (users_api, the two-person
  rule; or one permission of its own in the admin).
- A key is shown once, when issued, and kept only as its SHA-256 digest:
  32 random bytes need no slow hash. Its first characters (the prefix)
  are kept to tell keys apart on a screen.
- A key expires (a year unless asked shorter) and can be revoked; either
  stops it at once. A key is one secret, as a password is, so a login
  holding a right that needs a second factor (second_factor.required) is
  refused with a key, whenever it was issued.
- Every use is recorded (ApiKeyUse): when, the method and path, and the
  address it came from.
- Whoever issues a key learns a credential to the login, so issuing it
  asks what setting its password asks (users_api.check_may_know).

Sent as `Authorization: Api-Key <key>`. The screens' API is api_keys_api.py.
"""

import datetime
import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.exceptions import ValidationError as DRFValidationError

from .models import AuditModel

KEYWORD = "Api-Key"
LONGEST = datetime.timedelta(days=366)
USUAL = datetime.timedelta(days=365)


class Integration(AuditModel):
    """A program that talks to the ERP by itself, as a login of its own that no person signs in as."""

    name = models.CharField(max_length=80, unique=True)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="integration",
                                editable=False)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ApiKey(AuditModel):
    integration = models.ForeignKey(Integration, on_delete=models.PROTECT, related_name="keys")
    label = models.CharField(max_length=80, blank=True, help_text="Where it is kept: the station PC, the GSP's account.")
    prefix = models.CharField(max_length=16, unique=True, editable=False)
    digest = models.CharField(max_length=64, unique=True, editable=False)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True, editable=False)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", editable=False)
    last_used_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.integration}'s key {self.prefix}…"

    def refused(self, now=None):
        """Why this key no longer signs anything in, or None."""
        now = now or timezone.now()
        if self.revoked_at is not None:
            return "This API key was revoked."
        if self.expires_at <= now:
            return "This API key has expired. Ask whoever keeps logins for a new one."
        return None


class ApiKeyUse(models.Model):
    """One request a key signed in: kept, so whoever keeps logins sees what an integration did and when."""

    key = models.ForeignKey(ApiKey, on_delete=models.CASCADE, related_name="uses", editable=False)
    at = models.DateTimeField(default=timezone.now, editable=False)
    method = models.CharField(max_length=8, editable=False)
    path = models.CharField(max_length=255, editable=False)
    address = models.GenericIPAddressField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-at", "-pk"]


def digest(key):
    return hashlib.sha256(key.encode()).hexdigest()


def issue(integration, by, label="", expires_at=None):
    """A new key for `integration`: (the key, shown now and never again; its row)."""
    now = timezone.now()
    expires_at = expires_at or now + USUAL
    if expires_at <= now:
        raise DRFValidationError({"expires_on": ["A key that has already expired would sign nothing in."]})
    if expires_at > now + LONGEST:
        raise DRFValidationError({"expires_on": ["A key lasts a year at most: issue the next one before it ends."]})
    prefix = secrets.token_hex(4)
    key = f"erp_{prefix}_{secrets.token_urlsafe(32)}"
    row = ApiKey.objects.create(integration=integration, label=label, prefix=prefix, digest=digest(key),
                                created_by=by if getattr(by, "pk", None) else None,
                                updated_by=by if getattr(by, "pk", None) else None, expires_at=expires_at)
    return key, row


class ApiKeyAuthentication(BaseAuthentication):
    """`Authorization: Api-Key <key>`: the integration's login, every use recorded."""

    def authenticate_header(self, request):
        return KEYWORD

    def authenticate(self, request):
        from .auth import client_address
        from .second_factor import required

        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != KEYWORD.lower().encode():
            return None
        if len(parts) != 2:
            raise AuthenticationFailed("Send the key as: Authorization: Api-Key <key>.")
        key = (ApiKey.objects.select_related("integration__user")
               .filter(digest=digest(parts[1].decode(errors="replace"))).first())
        if key is None:
            raise AuthenticationFailed("No such API key.")
        said = key.refused()
        if said:
            raise AuthenticationFailed(said)
        user = key.integration.user
        if not user.is_active:
            raise AuthenticationFailed(f"{key.integration} is switched off.")
        if required(user):
            raise AuthenticationFailed(
                f"{key.integration} holds a right that needs a second factor, which a key alone does not give. "
                "An integration holds only what it needs.")
        now = timezone.now()
        ApiKeyUse.objects.create(key=key, at=now, method=request.method[:8], path=request.path[:255],
                                 address=client_address(request))
        ApiKey.objects.filter(pk=key.pk).update(last_used_at=now)
        return user, key
