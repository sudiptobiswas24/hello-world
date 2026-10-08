from config.settings import *  # noqa: F401,F403

DATABASES["default"].setdefault("TEST", {})["MIGRATE"] = False  # noqa: F405
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
