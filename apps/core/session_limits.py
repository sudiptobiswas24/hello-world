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

A request carrying `X-Session-Check: 1` (the application asking how
long is left) is not a sign that the person is there, so does not keep
the session open.
"""

import time

from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

SIGNED_IN_AT = "_signed_in_at"
LAST_SEEN = "_last_seen"
CHECK_HEADER = "HTTP_X_SESSION_CHECK"


def idle_seconds():
    return int(getattr(settings, "SESSION_IDLE_SECONDS", 1800))


def absolute_seconds():
    return int(getattr(settings, "SESSION_ABSOLUTE_SECONDS", 12 * 3600))


def _resolution():
    """How stale the idle clock may be: ten seconds, or a tenth of a short setting."""
    return min(10, idle_seconds() / 10)


def ended(session, now=None):
    """Why this signed-in session has ended ("idle", "absolute"), or None."""
    now = time.time() if now is None else now
    began, seen = session.get(SIGNED_IN_AT), session.get(LAST_SEEN)
    if began is not None and now - began >= absolute_seconds():
        return "absolute"
    if seen is not None and now - seen >= idle_seconds():
        return "idle"
    return None


def remaining(session, now=None):
    """What /api/core/me/ tells the application: the idle limit, and when the session ends regardless."""
    now = time.time() if now is None else now
    began, seen = session.get(SIGNED_IN_AT, now), session.get(LAST_SEEN, now)
    return {"idle_seconds": idle_seconds(), "ends_in_seconds": max(0, int(began + absolute_seconds() - now)),
            # What is left of the idle time as the server counts it: another tab may have kept it open.
            "idle_left_seconds": max(0, int(seen + idle_seconds() - now))}


class SessionLimits:
    """Middleware, after the session and before authentication: an ended session is flushed, so nobody."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        session = getattr(request, "session", None)
        why = None
        if session is not None and session.get(SESSION_KEY):
            now = time.time()
            why = ended(session, now)
            if why:
                session.flush()
            else:
                session.setdefault(SIGNED_IN_AT, now)
                # Written at most every few seconds, not on every request: each write saves the session, and a
                # request still in flight when another signed out found its session gone and answered 400.
                seen = session.get(LAST_SEEN)
                if request.META.get(CHECK_HEADER) != "1" and (seen is None or now - seen >= _resolution()):
                    session[LAST_SEEN] = now
        response = self.get_response(request)
        if why:
            response["X-Session-Ended"] = why
        return response


@receiver(user_logged_in)
def _the_clock_starts(sender, request, user, **kwargs):
    if getattr(request, "session", None) is not None:
        now = time.time()
        request.session[SIGNED_IN_AT] = now
        request.session[LAST_SEEN] = now
