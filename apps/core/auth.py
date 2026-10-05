"""
Limiting password guesses.

Nothing did: the login page, the admin and the API's basic
authentication would each take guesses for as long as anyone kept
sending them. Every one of them goes through authenticate() and so
through here.

Counted per user name, known or not — counting only known names would
make the lock say which names exist. Ten failures inside a quarter of
an hour and that name is refused, right password or not, until the
quarter of an hour since the latest has passed. A success clears the
count.

The trade: someone can lock a name out by guessing at it. On a plant
network that is a nuisance somebody notices; unlimited guessing is not
noticed at all.
"""

import datetime

from django.contrib.auth.backends import ModelBackend
from django.utils import timezone

LOCK_AFTER = 10
WINDOW = datetime.timedelta(minutes=15)


def is_locked(username, now=None):
    from .models import LoginFailure

    now = now or timezone.now()
    return LoginFailure.objects.filter(
        username=username.lower(), at__gte=now - WINDOW
    ).count() >= LOCK_AFTER


class LockoutModelBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        from .models import LoginFailure

        if username is None:
            username = kwargs.get("username")
        if username is None or password is None:
            return None
        name = str(username).lower()
        if is_locked(name):
            # Refused, and not counted: counting it kept a name locked for
            # ever on one guess every ninety seconds. Now the lock ends a
            # quarter of an hour after the tenth failure, unless ten more
            # come in that time.
            return None
        user = super().authenticate(request, username=username, password=password, **kwargs)
        if user is None:
            now = timezone.now()
            LoginFailure.objects.create(username=name, at=now)
            # Nothing older than the window decides anything: kept, the
            # table grew by every typo and every scan, for ever.
            LoginFailure.objects.filter(at__lt=now - WINDOW).delete()
        else:
            LoginFailure.objects.filter(username=name).delete()
        return user
