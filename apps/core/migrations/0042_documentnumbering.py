"""
O257: the company's one numbering row, held before any sequence row by every
transaction that takes a document number (DocumentSequence.next_value).

Made here, so that two first numbers on a new database do not both insert it;
DocumentNumbering.hold() makes it again where a test flushed it.
"""

from django.db import migrations, models


def make_the_row(apps, schema_editor):
    apps.get_model("core", "DocumentNumbering").objects.get_or_create(pk=1)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0041_secondfactor_sealed_secret"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentNumbering",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
            ],
            options={
                "verbose_name": "document numbering",
            },
        ),
        migrations.RunPython(make_the_row, migrations.RunPython.noop),
    ]
