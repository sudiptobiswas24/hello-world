import datetime
from decimal import Decimal

from django.db import migrations

CITATION = ("Customs Valuation (Determination of Value of Imported Goods) Rules, 2007, rule 10(2), "
            "provisos (i) and (ii), as amended by Notification 91/2017-Customs (N.T.) from 26 September 2017")


def load(apps, schema_editor):
    Default = apps.get_model("purchasing", "CustomsValuationDefault")
    Default.objects.get_or_create(valid_from=datetime.date(2017, 9, 26), defaults={
        "freight_percent": Decimal("20"), "insurance_percent": Decimal("1.125"), "citation": CITATION,
        "confirmed": False})


def unload(apps, schema_editor):
    apps.get_model("purchasing", "CustomsValuationDefault").objects.filter(
        valid_from=datetime.date(2017, 9, 26), citation=CITATION).delete()


class Migration(migrations.Migration):
    dependencies = [("purchasing", "0086_imports_and_rate_contracts")]
    operations = [migrations.RunPython(load, unload)]
