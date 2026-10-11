"""
Limiting password guesses.

Nothing did: the login page, the admin and the API's basic
authentication would each take guesses for as long as anyone kept
sending them. Every one of them goes through authenticate() and so
through here.

Counted by user name and by the address the guess came from, known name
or not (counting only known names would make the lock say which exist),
inside a quarter of an hour:

- ten failures for one name from one address lock that name from that
  address. Locked by name alone, as this first was, anyone who knew a
  colleague's user name could lock them out from their own desk;
- fifty from one address, whatever the names, lock the address: one
  machine trying name after name;
- a hundred for one name, from anywhere, lock the name everywhere: many
  machines trying one name. Reaching it takes a script, not a grudge.

A lock lifts a quarter of an hour after the failure that made it; a
guess while locked is refused and not counted. A success clears the
name's count.

The address is the caller's own, or behind the reverse proxy the one the
proxy saw (settings.TRUSTED_PROXIES): the last entries of
X-Forwarded-For are the proxies' own, and anything before them the
caller wrote and could have made up.
"""

import datetime

from django.conf import settings
from django.contrib.auth.backends import ModelBackend
from django.utils import timezone

LOCK_AFTER = 10  # one name, one address
ADDRESS_LOCK_AFTER = 50  # one address, any names
NAME_LOCK_AFTER = 100  # one name, any addresses
WINDOW = datetime.timedelta(minutes=15)


def client_address(request):
    """The address a request came from, as the outermost trusted proxy saw it; None without one."""
    if request is None:
        return None
    meta = getattr(request, "META", {}) or {}
    proxies = getattr(settings, "TRUSTED_PROXIES", 0)
    if proxies:
        forwarded = [part.strip() for part in meta.get("HTTP_X_FORWARDED_FOR", "").split(",") if part.strip()]
        if len(forwarded) >= proxies:
            return forwarded[-proxies] or None
    return meta.get("REMOTE_ADDR") or None


def is_locked(username, address=None, now=None):
    from .models import LoginFailure

    now = now or timezone.now()
    recent = LoginFailure.objects.filter(at__gte=now - WINDOW)
    name = username.lower()
    if recent.filter(username=name, address=address).count() >= LOCK_AFTER:
        return True
    if address is not None and recent.filter(address=address).count() >= ADDRESS_LOCK_AFTER:
        return True
    return recent.filter(username=name).count() >= NAME_LOCK_AFTER


class LockoutModelBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        from .models import LoginFailure

        if username is None:
            username = kwargs.get("username")
        if username is None or password is None:
            return None
        name = str(username).lower()
        address = client_address(request)
        if is_locked(name, address):
            # Refused, and not counted: counting it kept a name locked for
            # ever on one guess every ninety seconds. Now the lock ends a
            # quarter of an hour after the failure that made it.
            return None
        user = super().authenticate(request, username=username, password=password, **kwargs)
        if user is None:
            now = timezone.now()
            LoginFailure.objects.create(username=name, address=address, at=now)
            # Nothing older than the window decides anything: kept, the
            # table grew by every typo and every scan, for ever.
            LoginFailure.objects.filter(at__lt=now - WINDOW).delete()
        else:
            LoginFailure.objects.filter(username=name).delete()
        return user
