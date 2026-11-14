"""
The rights that need a second factor, on file from the first migrate
(review F3, F10), not only once setup_roles has run: a restored database,
a runserver or a one-off command signed a Controller in on a password
alone until it did. The list as it stood when written; setup_roles keeps
it current from setup_roles.SECOND_FACTOR.

A permission not made yet (a fresh database: Django makes them after the
last migration) is made here under the name Django gives it, so the
post-migrate step finds it and makes no second one.
"""

from django.db import migrations

GUARDED = [
    ("accounting", "payment", "post_payment", "Can post and void payments"),
    ("hr", "expenseclaim", "pay_expenseclaim", "Can pay an approved expense claim"),
    ("core", "partybankaccount", "add_partybankaccount", "Can add party bank account"),
    ("core", "partybankaccount", "change_partybankaccount", "Can change party bank account"),
    ("hr", "employeepaydetails", "add_employeepaydetails", "Can add employee pay details"),
    ("hr", "employeepaydetails", "change_employeepaydetails", "Can change employee pay details"),
    ("hr", "salarytransferfile", "add_salarytransferfile", "Can add salary transfer file"),
    ("hr", "salaryadvance", "add_salaryadvance", "Can add salary advance"),
    ("purchasing", "vendorprofile", "hold_vendor_payments", "Can hold or release a vendor's payments"),
    ("hr", "payslip", "view_payslip", "Can view payslip"),
    ("hr", "employeepaydetails", "view_employeepaydetails", "Can view employee pay details"),
    ("auth", "user", "change_user", "Can change user"),
    ("auth", "user", "add_user", "Can add user"),
]


def seed(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    GuardedRight = apps.get_model("core", "GuardedRight")
    for app_label, model, codename, name in GUARDED:
        kind, _ = ContentType.objects.get_or_create(app_label=app_label, model=model)
        permission, _ = Permission.objects.get_or_create(content_type=kind, codename=codename,
                                                         defaults={"name": name})
        GuardedRight.objects.get_or_create(permission=permission)


def unseed(apps, schema_editor):
    GuardedRight = apps.get_model("core", "GuardedRight")
    for app_label, _model, codename, _name in GUARDED:
        GuardedRight.objects.filter(permission__content_type__app_label=app_label,
                                    permission__codename=codename).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0037_apikey_apikeyuse_integration_apikey_integration"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
