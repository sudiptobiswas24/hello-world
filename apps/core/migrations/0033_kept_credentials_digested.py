"""
O198: what is kept of a password a keeper set is a keyed digest of its
hash, not the hash (roles.fingerprint), and a credential set before
0032 kept who set it is kept now with no setter: said in words to
whoever confirms a role for the login, never lapsed.
"""

from django.conf import settings
from django.db import migrations, models
from django.db.models.deletion import PROTECT
from django.utils.crypto import salted_hmac

# roles.KEPT_PASSWORD_SALT, as it stood when this was written.
SALT = "apps.core.roles.KeptCredential.password"


def digest_and_record(apps, schema_editor):
    User = apps.get_model("auth", "User")
    KeptCredential = apps.get_model("core", "KeptCredential")
    for kept in KeptCredential.objects.filter(kind="password"):
        kept.value = salted_hmac(SALT, kept.value, algorithm="sha256").hexdigest()
        kept.save(update_fields=["value"])
    for user in User.objects.all():
        kinds = set(KeptCredential.objects.filter(user=user).values_list("kind", flat=True))
        if "password" not in kinds and user.password and not user.password.startswith("!"):
            KeptCredential.objects.create(user=user, kind="password", set_by=None,
                                          value=salted_hmac(SALT, user.password, algorithm="sha256").hexdigest())
        if "email" not in kinds and user.email:
            KeptCredential.objects.create(user=user, kind="email", set_by=None, value=user.email)


def forget_unrecorded(apps, schema_editor):
    # The digests cannot be turned back into hashes; what had no setter goes.
    apps.get_model("core", "KeptCredential").objects.filter(set_by__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0032_kept_credentials"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="keptcredential",
            name="set_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=PROTECT, related_name="+",
                                    to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(digest_and_record, forget_unrecorded),
    ]
