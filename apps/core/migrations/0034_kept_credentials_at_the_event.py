"""
Review F1, F3, F8 (O195, O198): who can sign in as a login is kept at the
event, not read off its password's hash. A kept password is in force
until the next change of the password, so nothing of the hash is kept:
a row whose digest (0033) still matches the login's hash stands, with
its value emptied (and is the confirmer's where its setter confirmed a
role for the login); one that no longer matches was changed since and
goes. `as_confirmer` is added False, a keeper's, as every other row was. Who made each login (LOGIN) is recorded from the API's history,
lapsed where the login already holds what its maker could not give.
"""

from django.db import migrations, models
from django.utils.crypto import salted_hmac

# roles.KEPT_PASSWORD_SALT as 0033 digested with it.
SALT = "apps.core.roles.KeptCredential.password"


def _beyond(maker, login):
    """Whether `login` holds what `maker` could not give it: held_beyond, on the historical models."""
    if maker.is_superuser:
        return False
    if set(login.groups.values_list("pk", flat=True)) - set(maker.groups.values_list("pk", flat=True)):
        return True
    own = set(login.user_permissions.values_list("pk", flat=True))
    held = set(maker.user_permissions.values_list("pk", flat=True)) | set(
        maker.groups.values_list("permissions__pk", flat=True))
    return bool(own - held) or (login.is_staff and not maker.is_staff) or login.is_superuser


def at_the_event(apps, schema_editor):
    User = apps.get_model("auth", "User")
    KeptCredential = apps.get_model("core", "KeptCredential")
    RecordEvent = apps.get_model("core", "RecordEvent")
    ContentType = apps.get_model("contenttypes", "ContentType")
    RoleProposal = apps.get_model("core", "RoleProposal")
    for kept in KeptCredential.objects.filter(kind="password").select_related("user"):
        password = kept.user.password
        if password.startswith("!") or kept.value != salted_hmac(SALT, password, algorithm="sha256").hexdigest():
            kept.delete()
        else:
            kept.value = ""
            # One issued with a confirm (the confirmer decided a role for this login) is the confirmer's.
            kept.as_confirmer = RoleProposal.objects.filter(user=kept.user, decided_by=kept.set_by_id,
                                                            status="confirmed").exists()
            kept.save(update_fields=["value", "as_confirmer"])
    users = ContentType.objects.filter(app_label="auth", model="user").first()
    if users is None:
        return
    for event in RecordEvent.objects.filter(content_type=users, kind="created", who__isnull=False).order_by("at"):
        login = User.objects.filter(pk=event.object_id).first()
        if login is None or login.pk == event.who_id:
            continue
        KeptCredential.objects.get_or_create(user=login, kind="login", defaults={
            "value": "", "set_by_id": event.who_id, "set_at": event.at,
            "lapsed": _beyond(User.objects.get(pk=event.who_id), login)})


def forget_the_makers(apps, schema_editor):
    apps.get_model("core", "KeptCredential").objects.filter(kind="login").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0033_kept_credentials_digested"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.AlterField(
            model_name="keptcredential",
            name="kind",
            field=models.CharField(choices=[("password", "Password"), ("email", "Email"), ("login", "Login made")],
                                   max_length=8),
        ),
        migrations.AlterField(
            model_name="keptcredential",
            name="value",
            field=models.CharField(blank=True, max_length=254),
        ),
        migrations.AddField(
            model_name="keptcredential",
            name="as_confirmer",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(at_the_event, forget_the_makers),
    ]
