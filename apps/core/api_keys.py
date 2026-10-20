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

from .models import AuditModel, login_ended

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
    # What its issuer could give when they issued it ("*": everything, a superuser): the integration's login is
    # never given more while the key is live (refused_beyond_the_issuers, review F5). None: before it was kept.
    issuer_could_give = models.JSONField(null=True, blank=True, editable=False)
    # When its issuer left or lost the right to issue: the key stands (the plant's weighing must not stop), and
    # the remaining administrators were given a to-do naming it (issuer_out_of_reach).
    issuer_left_at = models.DateTimeField(null=True, blank=True, editable=False)

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
    """
    One request a key signed in, or one it was refused (`refused` says why;
    `key` empty for a key that is no key): kept, so whoever keeps logins
    sees what an integration did and what tried to pass as one. Kept 18
    months (manage.py prune_api_key_uses).
    """

    key = models.ForeignKey(ApiKey, null=True, blank=True, on_delete=models.CASCADE, related_name="uses",
                            editable=False)
    refused = models.CharField(max_length=120, blank=True, editable=False)
    prefix = models.CharField(max_length=16, blank=True, editable=False)
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
    could = None
    if getattr(by, "pk", None):
        could = ["*"] if by.is_superuser else sorted(by.get_all_permissions())
    row = ApiKey.objects.create(integration=integration, label=label, prefix=prefix, digest=digest(key),
                                created_by=by if getattr(by, "pk", None) else None,
                                updated_by=by if getattr(by, "pk", None) else None, expires_at=expires_at,
                                issuer_could_give=could)
    return key, row


def live_keys(**where):
    return ApiKey.objects.filter(revoked_at__isnull=True, expires_at__gt=timezone.now(), **where)


def refused_beyond_the_issuers(user, rights):
    """
    Why `user`, an integration's login, may not be given `rights` (a set of
    "app.codename"), or None (review F5): whoever issued one of its live
    keys saw that key, so the login holds nothing they could not give when
    they issued it. A key does not lapse with its issuer; this is what
    keeps a credential they saw from reaching past them.
    """
    integration = Integration.objects.filter(user_id=user.pk).first()
    if integration is None:
        return None
    for key in live_keys(integration=integration).select_related("created_by"):
        could = key.issuer_could_give
        if could is None or "*" in could:
            continue
        beyond = ["every right (a superuser's standing)"] if "*" in rights else sorted(set(rights) - set(could))
        if beyond:
            return (f"{integration} has a live key {key.created_by or 'someone'} issued ({key.prefix}…), who could not "
                    f"give {', '.join(beyond)} when they issued it, and saw the key. Revoke that key and have someone "
                    "who may give it issue the next one first.")
    return None


def issuer_out_of_reach(setter):
    """
    `setter` left or lost a right: if it may no longer issue keys, the keys
    it issued stand (the scale bridge keeps weighing), are marked
    "issuer left", and every remaining administrator gets one to-do naming
    them, to replace them or keep them (review F5).
    """
    from django.contrib.auth.models import Permission, User
    from django.contrib.contenttypes.models import ContentType
    from django.db.models import Q

    from .chatter import FollowUp

    setter = User.objects.get(pk=setter.pk)
    if setter.is_active and setter.has_perm("core.add_apikey"):
        return
    keys = list(live_keys(created_by=setter, issuer_left_at__isnull=True).select_related("integration"))
    if not keys:
        return
    ApiKey.objects.filter(pk__in=[key.pk for key in keys]).update(issuer_left_at=timezone.now())
    right = Permission.objects.filter(content_type__app_label="core", codename="add_apikey")
    remaining = (User.objects.filter(is_active=True).exclude(pk=setter.pk)
                 .filter(Q(is_superuser=True) | Q(groups__permissions__in=right) | Q(user_permissions__in=right))
                 .distinct().order_by("pk"))
    named = "; ".join(f"{key.integration} {key.prefix}… ({key.label or 'no label'})" for key in keys)
    for admin in remaining:
        FollowUp.objects.create(
            content_type=ContentType.objects.get_for_model(User), object_id=setter.pk,
            summary=f"API keys {setter} issued: replace or keep them"[:255],
            note=(f"{setter} can no longer issue API keys, and saw these when issuing them: {named}. They still "
                  "work. Issue each integration a new key, put it where the old one is kept, and revoke the old one."),
            due_on=timezone.localdate(), assigned_to=admin, created_by=None, updated_by=None)


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
        token = parts[1].decode(errors="replace") if len(parts) == 2 else ""
        prefix = token.split("_")[1][:16] if token.count("_") >= 2 else ""

        def refuse(said, key=None):
            # Every refused attempt is kept too, with what it claimed to be (review).
            ApiKeyUse.objects.create(key=key, refused=said[:120], prefix=prefix, method=request.method[:8],
                                     path=request.path[:255], address=client_address(request))
            raise AuthenticationFailed(said)

        if len(parts) != 2:
            refuse("Send the key as: Authorization: Api-Key <key>.")
        key = ApiKey.objects.select_related("integration__user").filter(digest=digest(token)).first()
        if key is None:
            refuse("No such API key.")
        said = key.refused()
        if said:
            refuse(said, key)
        user = key.integration.user
        if not user.is_active:
            refuse(f"{key.integration} is switched off.", key)
        ended = login_ended(user)
        if ended:
            refuse(f"{key.integration}: {ended}", key)
        if required(user):
            refuse(f"{key.integration} holds a right that needs a second factor, which a key alone does not give. "
                   "An integration holds only what it needs.", key)
        now = timezone.now()
        ApiKeyUse.objects.create(key=key, at=now, method=request.method[:8], path=request.path[:255],
                                 address=client_address(request))
        ApiKey.objects.filter(pk=key.pk).update(last_used_at=now)
        return user, key
