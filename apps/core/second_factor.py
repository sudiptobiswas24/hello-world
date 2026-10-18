"""
Two-factor sign-in (L12): the password, then a code from the person's phone.

Time-based one-time codes (RFC 6238) as an authenticator app makes them,
checked with pyotp. A login needs the code when it holds a right listed
in GuardedRight (setup_roles writes them: posting a payment, seeing pay,
keeping logins), when it is a superuser, or when its person chose to
enrol. Everyone else signs in with a password alone.

- Enrolment: a secret shown as a QR code (and in letters, for a phone
  that cannot scan), confirmed with one code, and ten recovery codes
  shown once and kept only as digests. Each recovery code signs in once.
- Sign-in: the password (apps/core/auth.py counts those), then the code.
  Five wrong codes lock the code step for a quarter of an hour.
- A session made by the password alone is no session for what the code
  guards (Gate): every view sees nobody signed in, the API included,
  until the code is given in that session. HTTP basic authentication is a
  password alone, so it is refused for such a login.
- The way back from a lost phone: whoever keeps logins resets the factor
  (users_api), recorded in the login's history, and the login enrols
  again at its next sign-in. Every session the old factor passed ends
  with it, since a session is marked with the factor it passed.
- A login switched off loses its factor; switched on again, it enrols
  afresh.

The secret is kept as it is: checking a code needs it. The recovery codes
are random enough (50 bits each) that a SHA-256 digest is as good as a
slow hash, and needs no salt to look one up.
"""

import datetime
import hashlib
import hmac
import secrets
from urllib.parse import quote

import pyotp
from django.conf import settings
from django.contrib.auth import get_user, login
from django.contrib.auth.models import AnonymousUser, Permission
from django.contrib.auth.views import LoginView
from django.db import models, transaction
from django.db.models import F, Q
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.functional import SimpleLazyObject
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe
from rest_framework.authentication import BasicAuthentication
from rest_framework.exceptions import AuthenticationFailed

SESSION_KEY = "_second_factor"
LOCK_AFTER = 5
LOCKED_FOR = datetime.timedelta(minutes=15)
RECOVERY_CODES = 10
STEP_URL = "/accounts/second-factor/"
ISSUER = "Deccan Polysacks ERP"


class GuardedRight(models.Model):
    """A right that needs the second factor to use (setup_roles.SECOND_FACTOR writes them)."""

    permission = models.OneToOneField(Permission, on_delete=models.CASCADE, related_name="+")

    def __str__(self):
        return f"{self.permission.content_type.app_label}.{self.permission.codename}"


class SecondFactor(models.Model):
    """One login's authenticator: confirmed once a code from it was given."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="second_factor")
    secret = models.CharField(max_length=64)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    # The 30-second step of the last code taken: a code signs in once, never again by whoever saw it typed.
    last_step = models.BigIntegerField(null=True, blank=True)
    failures = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user}'s second factor"


class RecoveryCode(models.Model):
    factor = models.ForeignKey(SecondFactor, on_delete=models.CASCADE, related_name="recovery_codes")
    digest = models.CharField(max_length=64, unique=True)
    used_at = models.DateTimeField(null=True, blank=True)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def required(user):
    """Whether `user` must sign in with a second factor: a superuser, or a holder of a guarded right."""
    if not user.is_active:
        return False
    if user.is_superuser:
        return True
    guarded = {str(row) for row in GuardedRight.objects.select_related("permission__content_type")}
    return bool(guarded & user.get_all_permissions())


def confirmed(user):
    return SecondFactor.objects.filter(user=user, confirmed_at__isnull=False).first()


def needs_code(user):
    """Whether a password alone does not sign `user` in: required, or chosen by enrolling."""
    return bool(user.is_authenticated and (required(user) or confirmed(user)))


def passed(request, user):
    """Whether this session gave a code from `user`'s current factor (a reset ends every session it passed)."""
    factor = confirmed(user)
    return factor is not None and request.session.get(SESSION_KEY) == factor.pk


def half_signed_in(request):
    """The login whose password this session gave and whose code it has not, or None."""
    user = get_user(request)
    if user.is_authenticated and needs_code(user) and not passed(request, user):
        return user
    return None


class Gate:
    """Middleware: a session that gave only the password is nobody, to every view after this one."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.user = SimpleLazyObject(lambda: _through_the_gate(request))
        return self.get_response(request)


def _through_the_gate(request):
    from django.contrib.auth.middleware import get_user as session_user

    user = session_user(request)
    if user.is_authenticated and needs_code(user) and not passed(request, user):
        return AnonymousUser()
    return user


class PasswordOnlyBasicAuthentication(BasicAuthentication):
    """HTTP basic is a password alone: refused for a login that signs in with a second factor."""

    def authenticate_credentials(self, userid, password, request=None):
        user, auth = super().authenticate_credentials(userid, password, request)
        if needs_code(user):
            raise AuthenticationFailed(
                f"{user.get_username()} signs in with a second factor, which a password alone does not give. Sign "
                "in to the office application, or give an integration its own login and API key.")
        return user, auth


def _code_step(factor, code):
    """The step `code` belongs to, if it is one of the three around now; None otherwise."""
    totp = pyotp.TOTP(factor.secret)
    now = totp.timecode(timezone.now())
    code = "".join(code.split())
    for step in (now - 1, now, now + 1):
        if hmac.compare_digest(totp.generate_otp(step), code):
            return step
    return None


def _took_step(factor, step):
    """Take `step` for `factor`, once: two submissions of one code at the same moment take it once."""
    return SecondFactor.objects.filter(pk=factor.pk).filter(
        Q(last_step__isnull=True) | Q(last_step__lt=step)).update(last_step=step) == 1


def _failed(factor):
    """One wrong code; the fifth locks the code step for a while and starts the count again."""
    with transaction.atomic():
        SecondFactor.objects.filter(pk=factor.pk).update(failures=F("failures") + 1)
        factor.refresh_from_db(fields=["failures"])
        if factor.failures >= LOCK_AFTER:
            SecondFactor.objects.filter(pk=factor.pk).update(failures=0, locked_until=timezone.now() + LOCKED_FOR)


def locked(factor):
    return factor.locked_until is not None and factor.locked_until > timezone.now()


def check(factor, code):
    """
    Whether `code` signs in with `factor`: a code from the phone not used
    before, or an unused recovery code (used up by this). Counted when wrong.
    """
    from .history import EventKind, record

    factor.refresh_from_db()
    if locked(factor):
        return False
    code = (code or "").strip()
    step = _code_step(factor, code) if code.isdigit() else None
    ok = step is not None and _took_step(factor, step)
    if not ok and code and not code.isdigit():
        spent = RecoveryCode.objects.filter(factor=factor, digest=digest(code.replace("-", "").upper()),
                                            used_at__isnull=True).update(used_at=timezone.now())
        ok = spent == 1
        if ok:
            left = RecoveryCode.objects.filter(factor=factor, used_at__isnull=True).count()
            record(factor.user, factor.user, EventKind.ACTION, action="recovery_code",
                   summary=f"Signed in with a recovery code; {left} left")
    if ok:
        SecondFactor.objects.filter(pk=factor.pk).update(failures=0)
    else:
        _failed(factor)
    return ok


def new_recovery_codes(factor):
    """Ten new codes, shown once: only their digests are kept, and the old ones go."""
    codes = []
    with transaction.atomic():
        RecoveryCode.objects.filter(factor=factor).delete()
        for _ in range(RECOVERY_CODES):
            code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(10))
            RecoveryCode.objects.create(factor=factor, digest=digest(code))
            codes.append(f"{code[:5]}-{code[5:]}")
    return codes


def reset(user, by):
    """A lost phone: the factor and its codes go, and the login enrols again at its next sign-in."""
    from .history import EventKind, record

    gone, _ = SecondFactor.objects.filter(user=user).delete()
    if gone:
        record(user, by, EventKind.ACTION, action="reset_second_factor",
               summary="Second factor reset: enrols again at the next sign-in")
    return bool(gone)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def _a_leaver_loses_the_factor(sender, instance, created, update_fields=None, **kwargs):
    """A login switched off loses its second factor with the rest; switched on again, it enrols afresh."""
    if not created and not instance.is_active:
        SecondFactor.objects.filter(user=instance).delete()


# The pages: the password (SignInView), then the code or the enrolment (step).


def _next(request):
    wanted = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(wanted, allowed_hosts={request.get_host()},
                                       require_https=request.is_secure()):
        return wanted
    return settings.LOGIN_REDIRECT_URL


def _to_step(request, wanted):
    return redirect(f"{STEP_URL}?next={quote(wanted)}")


class SignInView(LoginView):
    """The password; then, for a login that needs one, the code (step)."""

    def get(self, request, *args, **kwargs):
        if half_signed_in(request):
            return _to_step(request, _next(request))
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        if needs_code(form.get_user()):
            return _to_step(self.request, self.get_success_url())
        return response


def _qr(factor):
    import segno

    uri = pyotp.TOTP(factor.secret).provisioning_uri(name=factor.user.get_username(), issuer_name=ISSUER)
    return mark_safe(segno.make(uri, error="m").svg_inline(scale=5, dark="#16181A", light="#ffffff"))


def step(request):
    """
    The code for a login that has a factor; the enrolment for one that has
    none yet (required, or chosen from the office application's menu).
    """
    user = half_signed_in(request) or (get_user(request) if request.user.is_authenticated else None)
    wanted = _next(request)
    if user is None:
        return redirect(f"{settings.LOGIN_URL}?next={quote(wanted)}")
    factor = confirmed(user)
    context = {"next": wanted, "user": user}
    if factor is not None:
        context["mode"] = "code"
        if request.method == "POST":
            if locked(factor):
                context["error"] = "Five wrong codes: the code step is locked for a quarter of an hour."
            elif check(factor, request.POST.get("code", "")):
                request.session.cycle_key()
                request.session[SESSION_KEY] = factor.pk
                return redirect(wanted)
            else:
                factor.refresh_from_db()
                context["error"] = ("Five wrong codes: the code step is locked for a quarter of an hour."
                                    if locked(factor) else "That code did not match. Try the one your app shows now.")
        return render(request, "second_factor.html", context)
    factor, _ = SecondFactor.objects.get_or_create(user=user, defaults={"secret": pyotp.random_base32()})
    context.update(mode="enrol", qr=_qr(factor), secret=factor.secret)
    if request.method == "POST":
        if locked(factor):
            context["error"] = "Five wrong codes: the code step is locked for a quarter of an hour."
            return render(request, "second_factor.html", context)
        step_taken = _code_step(factor, request.POST.get("code", ""))
        if step_taken is not None and _took_step(factor, step_taken):
            from .history import EventKind, record

            SecondFactor.objects.filter(pk=factor.pk).update(confirmed_at=timezone.now(), failures=0)
            codes = new_recovery_codes(factor)
            record(user, user, EventKind.ACTION, action="enrol_second_factor", summary="Second factor enrolled")
            if not request.user.is_authenticated:
                request.session.cycle_key()
            request.session[SESSION_KEY] = factor.pk
            return render(request, "second_factor.html", {"mode": "codes", "codes": codes, "next": wanted,
                                                          "user": user})
        _failed(factor)
        context["error"] = "That code did not match. Scan the code again, or type the letters, and try once more."
    return render(request, "second_factor.html", context)


class AdminSignInView(LoginView):
    """The admin site signs in where everyone does, so its sign-in asks for the code too."""

    def get(self, request, *args, **kwargs):
        wanted = _next(request) if request.GET.get("next") else "/admin/"
        return redirect(f"{settings.LOGIN_URL}?next={quote(wanted)}")

    post = get
