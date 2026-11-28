"""
A bill keeps the MSME category it was booked under. Bills posted before
read their vendor's as it stood, so that is what they are given: the
MSME report reads the same rows the day after as the day before.
"""

from django.db import migrations, models


def as_their_vendors_say(apps, schema_editor):
    Bill = apps.get_model("purchasing", "Bill")
    PartyTaxProfile = apps.get_model("accounting", "PartyTaxProfile")
    for profile in PartyTaxProfile.objects.exclude(msme_category=""):
        Bill.objects.filter(vendor_id=profile.party_id, posted=True).update(msme_category=profile.msme_category)


class Migration(migrations.Migration):

    dependencies = [
        ("purchasing", "0060_allocation_dated_on_its_own_day"),
        ("accounting", "0019_msme"),
    ]

    operations = [
        migrations.AddField(
            model_name="bill",
            name="msme_category",
            field=models.CharField(
                blank=True,
                choices=[("micro", "Micro"), ("small", "Small"), ("medium", "Medium")],
                editable=False,
                help_text="The vendor's MSME category as its Udyam registration said when the bill posted: whether the Act's 45 days bind it, and section 43B(h) with them. A vendor reclassified later moves no bill.",
                max_length=8,
            ),
        ),
        migrations.RunPython(as_their_vendors_say, migrations.RunPython.noop),
    ]
