"""
O255: the second factor's secret sealed at rest (apps/core/sealed.py).

Written by hand: the copy has to run between adding the sealed column and
removing the clear one, and the autodetector puts the removal first.
Backwards, each secret is opened into the clear column again; one that no
key opens is given a fresh random secret, which no phone holds: that
login's phone no longer gives a code that matches, so it signs in only
with a recovery code, or after it is reset and enrols again (never a
server error, never a sign-in on a guess).
"""

from django.db import migrations, models


def seal_every_secret(apps, schema_editor):
    from apps.core.sealed import seal

    SecondFactor = apps.get_model("core", "SecondFactor")
    for factor in SecondFactor.objects.order_by("pk").iterator():
        SecondFactor.objects.filter(pk=factor.pk).update(sealed_secret=seal(factor.secret))


def open_every_secret(apps, schema_editor):
    import pyotp

    from apps.core.sealed import Unreadable, unseal

    SecondFactor = apps.get_model("core", "SecondFactor")
    for factor in SecondFactor.objects.order_by("pk").iterator():
        try:
            secret = unseal(factor.sealed_secret)
        except Unreadable:
            secret = pyotp.random_base32()
        SecondFactor.objects.filter(pk=factor.pk).update(secret=secret)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0040_apikeyuse_prefix_apikeyuse_refused_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="secondfactor",
            name="sealed_secret",
            field=models.TextField(default="", editable=False),
            preserve_default=False,
        ),
        migrations.RunPython(seal_every_secret, open_every_secret),
        # A default for the clear column only so that, backwards, it can be added to rows that stand.
        migrations.AlterField(
            model_name="secondfactor",
            name="secret",
            field=models.CharField(default="", max_length=64),
        ),
        migrations.RemoveField(
            model_name="secondfactor",
            name="secret",
        ),
    ]
