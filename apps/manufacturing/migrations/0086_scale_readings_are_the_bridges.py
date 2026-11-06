"""
O271: a scale reading is posted by the scale's bridge, never by a person's
login. The Station role (the tablet's shared login) and the Production
Supervisor held add_scalereading, so the tablet could post the reading it
then confirmed as "scale": a typed weight with no supervisor's PIN.

On a database that already has the roles, this takes the right off both
and gives it to a "Scale bridge" role, the one setup_roles now defines. A
permission not made yet (a fresh database) is made under the name Django
gives it, so the post-migrate step makes no second one.
"""

from django.db import migrations

TAKEN_FROM = ("Station", "Production Supervisor")
BRIDGE = "Scale bridge"


def _permission(apps):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    kind, _ = ContentType.objects.get_or_create(app_label="manufacturing", model="scalereading")
    permission, _ = Permission.objects.get_or_create(content_type=kind, codename="add_scalereading",
                                                     defaults={"name": "Can add scale reading"})
    return permission


def forward(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    permission = _permission(apps)
    for group in Group.objects.filter(name__in=TAKEN_FROM).order_by("pk"):
        group.permissions.remove(permission)
    bridge, _ = Group.objects.get_or_create(name=BRIDGE)
    bridge.permissions.add(permission)


def backward(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    permission = _permission(apps)
    for group in Group.objects.filter(name__in=TAKEN_FROM).order_by("pk"):
        group.permissions.add(permission)
    Group.objects.filter(name=BRIDGE, user__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("manufacturing", "0085_scale_reading_net_and_key"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(forward, backward)]
