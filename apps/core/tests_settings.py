"""
The settings a server starts with, read in a fresh interpreter per case:
they are computed once at import, from the environment.

A developer's machine needs nothing set. The plant's server
(DJANGO_ENV=production) refuses to start without its own secret and its
host names, and is safe by default when it does.
"""

import json
import os
import subprocess
import sys

from django.conf import settings
from django.test import SimpleTestCase

READ = """
import json
from config import settings as s
db = s.DATABASES["default"]
print(json.dumps({
    "debug": s.DEBUG, "engine": db["ENGINE"], "name": str(db["NAME"]),
    "user": db.get("USER"), "password": db.get("PASSWORD"), "host": db.get("HOST"),
    "port": db.get("PORT"), "hosts": s.ALLOWED_HOSTS, "origins": s.CSRF_TRUSTED_ORIGINS,
    "redirect": getattr(s, "SECURE_SSL_REDIRECT", False),
    "secure_cookie": getattr(s, "SESSION_COOKIE_SECURE", False),
    "whitenoise": "whitenoise.middleware.WhiteNoiseMiddleware" in s.MIDDLEWARE,
    "time_zone": s.TIME_ZONE,
    "email": s.EMAIL_BACKEND if hasattr(s, "EMAIL_BACKEND") else "default",
    "email_host": getattr(s, "EMAIL_HOST", None), "email_port": getattr(s, "EMAIL_PORT", None),
}))
"""

SECRET = "f" * 64


def read(**env):
    clean = {key: value for key, value in os.environ.items()
             if not key.startswith(("DJANGO_", "DATABASE_"))}
    clean.update(env)
    done = subprocess.run([sys.executable, "-c", READ], env=clean, capture_output=True,
                          text=True, cwd=settings.BASE_DIR)
    if done.returncode:
        return done.stderr.strip().splitlines()[-1]
    return json.loads(done.stdout)


class ADevelopersMachineTests(SimpleTestCase):
    def test_needs_nothing_set(self):
        found = read()
        self.assertEqual((found["debug"], found["engine"], found["redirect"],
                          found["whitenoise"], found["time_zone"]),
                         (True, "django.db.backends.sqlite3", False, False, "UTC"))


class ThePlantsServerTests(SimpleTestCase):
    def production(self, **env):
        return read(**{"DJANGO_ENV": "production", "DJANGO_SECRET_KEY": SECRET,
                       "DJANGO_ALLOWED_HOSTS": "erp.example.in", **env})

    def test_refuses_without_its_own_secret(self):
        self.assertIn("DJANGO_SECRET_KEY is not set",
                      read(DJANGO_ENV="production", DJANGO_ALLOWED_HOSTS="erp.example.in"))

    def test_nor_an_empty_one(self):
        self.assertIn("DJANGO_SECRET_KEY is not set",
                      read(DJANGO_ENV="production", DJANGO_SECRET_KEY="",
                           DJANGO_ALLOWED_HOSTS="erp.example.in"))

    def test_refuses_the_published_development_key(self):
        from config.settings import DEV_SECRET_KEY

        self.assertIn("DJANGO_SECRET_KEY is not set",
                      read(DJANGO_ENV="production", DJANGO_SECRET_KEY=DEV_SECRET_KEY,
                           DJANGO_ALLOWED_HOSTS="erp.example.in"))

    def test_refuses_without_its_host_names(self):
        self.assertIn("DJANGO_ALLOWED_HOSTS is not set",
                      read(DJANGO_ENV="production", DJANGO_SECRET_KEY=SECRET))

    def test_safe_by_default(self):
        found = self.production(DJANGO_ALLOWED_HOSTS=" erp.example.in, 192.168.1.10 ",
                                DJANGO_CSRF_TRUSTED_ORIGINS="https://erp.example.in")
        self.assertEqual((found["debug"], found["redirect"], found["secure_cookie"],
                          found["whitenoise"], found["hosts"], found["origins"]),
                         (False, True, True, True, ["erp.example.in", "192.168.1.10"],
                          ["https://erp.example.in"]))

    def test_plain_http_only_when_asked(self):
        found = self.production(DJANGO_HTTPS="false")
        self.assertEqual((found["redirect"], found["secure_cookie"]), (False, False))

    def test_refuses_to_email_with_no_mail_server_named(self):
        self.assertEqual(self.production()["email"], "apps.core.mail.NotConfiguredBackend")

    def test_emails_through_the_mail_server_it_is_given(self):
        found = self.production(EMAIL_HOST="smtp.example.in", EMAIL_PORT="465")
        self.assertEqual((found["email"], found["email_host"], found["email_port"]),
                         ("default", "smtp.example.in", 465))

    def test_the_plants_clock(self):
        self.assertEqual(self.production(DJANGO_TIME_ZONE="Asia/Kolkata")["time_zone"],
                         "Asia/Kolkata")


class TheDatabaseTests(SimpleTestCase):
    def test_postgres_from_its_url(self):
        found = read(DATABASE_URL="postgres://erp:p%40ss%2Fword@db.local:6543/plant")
        self.assertEqual((found["engine"], found["name"], found["user"], found["password"],
                          found["host"], found["port"]),
                         ("django.db.backends.postgresql", "plant", "erp", "p@ss/word",
                          "db.local", "6543"))

    def test_nothing_else(self):
        self.assertIn("DATABASE_URL must be postgres://",
                      read(DATABASE_URL="mysql://erp:erp@db/plant"))
