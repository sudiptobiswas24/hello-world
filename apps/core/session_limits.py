"""
Session time-outs (L12): a session ends after SESSION_IDLE_SECONDS with
no request from the person (a setting, 30 minutes unless
DJANGO_SESSION_IDLE_MINUTES says otherwise), and in any case
SESSION_ABSOLUTE_SECONDS (12 hours) after it signed in.

Checked before anything reads who is signed in, so an ended session is
nobody to every view: the office application finds out on its next
request, keeps what was being typed in the browser and takes the person
to sign in (frontend/src/api/client.ts, forms/useDraft.ts). It warns
before the end from what /api/core/me/ says (session).

The idle clock is not in the session (review, the sweep): writing it there
saved the session on requests that changed nothing else. On SQLite two
such saves at once collided and a GET answered 400; on PostgreSQL the
later save wrote back the whole session it had loaded, over a key another
request had just set (the code-passed mark, a rotated token): a lost
update. It is a signed cookie instead (IDLE_COOKIE): a TimestampSigner
value of its own salt, binding a digest of the session key (never the
key), set on the response to any request that is not a poll, at most once
every _resolution(). Nothing writes the session on an ordinary request;
the sign-in moment (SIGNED_IN_AT) is written at sign-in, for the absolute
limit.

A cookie that is missing, does not unsign or names another session is no
request seen: the clock then runs from the sign-in, so such a session
ends once it is idle_seconds() old. A request carrying
`X-Session-Check: 1` (a poll, the application asking how long is left) is
not a sign that the person is there, so moves no clock.
"""

import hashlib
import hmac
import time

from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.contrib.auth.signals import user_logged_in
from django.core import signing
from django.dispatch import receiver

SIGNED_IN_AT = "_signed_in_at"
CHECK_HEADER = "HTTP_X_SESSION_CHECK"
IDLE_COOKIE = "erp_idle"


class _IdleSigner(signing.TimestampSigner):
    """The idle clock's signer: its own salt, and this module's clock (so a test's clock is the cookie's too)."""

    def timestamp(self):
        return signing.b62_encode(int(time.time()))


SIGNER = _IdleSigner(salt="apps.core.session_limits.idle-clock")


def _binding(session_key):
    return hashlib.sha256(f"idle-clock:{session_key}".encode()).hexdigest()[:32]


def seen_at(request):
    """When the idle cookie says this session last saw its person, or None: missing, forged, or another session's."""
    value, key = request.COOKIES.get(IDLE_COOKIE), getattr(request.session, "session_key", None)
    if not value or not key:
        return None
    try:
        bound, stamp = signing.Signer.unsign(SIGNER, value).rsplit(SIGNER.sep, 1)
        stamp = signing.b62_decode(stamp)
    except (signing.BadSignature, ValueError):
        return None
    return stamp if hmac.compare_digest(bound, _binding(key)) else None


def idle_seconds():
    return int(getattr(settings, "SESSION_IDLE_SECONDS", 1800))


def absolute_seconds():
    return int(getattr(settings, "SESSION_ABSOLUTE_SECONDS", 12 * 3600))


def _resolution():
    """How stale the idle clock may be: ten seconds, or a tenth of a short setting."""
    return min(10, idle_seconds() / 10)


def _last_seen(request, began):
    seen = seen_at(request)
    return began if seen is None else max(seen, began)


def ended(request, now=None):
    """Why this signed-in session has ended ("idle", "absolute"), or None."""
    now = time.time() if now is None else now
    began = request.session.get(SIGNED_IN_AT)
    if began is None or now - began >= absolute_seconds():
        # No sign-in moment on file: a session from before the rule, of unknown age.
        return "absolute"
    if now - _last_seen(request, began) >= idle_seconds():
        return "idle"
    return None


def remaining(request, now=None):
    """What /api/core/me/ tells the application: the idle limit, what is left of it, and the absolute end."""
    now = time.time() if now is None else now
    began = request.session.get(SIGNED_IN_AT, now)
    return {"idle_seconds": idle_seconds(), "ends_in_seconds": max(0, int(began + absolute_seconds() - now)),
            # As the server counts it: another tab may have kept it open.
            "idle_left_seconds": max(0, int(_last_seen(request, began) + idle_seconds() - now))}


class SessionLimits:
    """
    Middleware, after the session and before authentication: an ended
    session is flushed, so nobody; a request from the person moves the idle
    cookie on its response.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        session = getattr(request, "session", None)
        why = None
        if session is not None and session.get(SESSION_KEY):
            why = ended(request)
            if why:
                session.flush()
        response = self.get_response(request)
        if why:
            response["X-Session-Ended"] = why
            response.delete_cookie(IDLE_COOKIE, path="/", domain=settings.SESSION_COOKIE_DOMAIN,
                                   samesite=settings.SESSION_COOKIE_SAMESITE)
        elif session is not None and session.get(SESSION_KEY) and request.META.get(CHECK_HEADER) != "1":
            now = time.time()
            seen = seen_at(request)
            # The session key may have changed in this request (a sign-in, the code step): bind the one it has now.
            if session.session_key and (seen is None or now - seen >= _resolution()):
                response.set_cookie(
                    IDLE_COOKIE, SIGNER.sign(_binding(session.session_key)), max_age=absolute_seconds(), path="/",
                    domain=settings.SESSION_COOKIE_DOMAIN, secure=settings.SESSION_COOKIE_SECURE,
                    httponly=settings.SESSION_COOKIE_HTTPONLY, samesite=settings.SESSION_COOKIE_SAMESITE)
        return response


@receiver(user_logged_in)
def _the_clock_starts(sender, request, user, **kwargs):
    """The sign-in moment, written with the sign-in itself: the absolute limit, and the idle clock's start."""
    if getattr(request, "session", None) is not None:
        request.session[SIGNED_IN_AT] = time.time()
