"""
Secrets kept in the database, sealed with a key that is not (O255).

A secret the server must read back (the second factor's, which checking a
code needs) cannot be hashed like a password. It is sealed with Fernet
under settings.FIELD_KEYS, which come from the environment (ERP_FIELD_KEYS)
and never from the database: a copy of the database, a backup included,
holds only sealed text. The first key seals; every key opens (MultiFernet),
so a key is rotated by putting a new one first and running
`manage.py rotate_field_keys`, and the old one is dropped after that.

A sealed value that no key opens (a restore without its key, a key dropped
too soon) raises Unreadable, which a reader turns into words: never a
server error, and never treated as a match.
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class Unreadable(Exception):
    """A sealed value that none of the keys opens."""


@lru_cache(maxsize=4)
def _fernet(keys):
    try:
        return MultiFernet([Fernet(key) for key in keys])
    except (ValueError, TypeError) as error:
        raise ImproperlyConfigured(
            "ERP_FIELD_KEYS holds a key that is not a Fernet key (32 bytes in url-safe base64, as "
            "Fernet.generate_key() prints)."
        ) from error


def fernet():
    keys = tuple(settings.FIELD_KEYS)
    if not keys:
        raise ImproperlyConfigured("ERP_FIELD_KEYS is empty: the database's secrets cannot be sealed.")
    return _fernet(keys)


def seal(text):
    return fernet().encrypt(text.encode()).decode()


def unseal(token):
    try:
        return fernet().decrypt((token or "").encode()).decode()
    except InvalidToken as error:
        raise Unreadable from error


def reseal(token):
    """`token` sealed again under the first key; Unreadable if no key opens it."""
    try:
        return fernet().rotate((token or "").encode()).decode()
    except InvalidToken as error:
        raise Unreadable from error


# Every sealed column, as (app_label.Model, field): what rotate_field_keys seals again.
SEALED_FIELDS = [("core.SecondFactor", "sealed_secret")]
