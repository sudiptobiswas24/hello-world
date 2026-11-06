"""
O287, decided: an invoice posted before 0078 keeps the B2CL limit as the setting stood when this
migration ran, the nearest record of the limit it was posted under. From here on a change of the
setting moves no invoice between B2CL and B2CS. Says how many it filled.
"""

from django.db import migrations


def fill(apps, schema_editor):
    Invoice = apps.get_model("sales", "Invoice")
    GstSettings = apps.get_model("accounting", "GstSettings")
    settings = GstSettings.objects.filter(is_active=True).order_by("pk").first()
    if settings is None:
        filled = 0
    else:
        filled = Invoice.objects.filter(posted=True, b2cl_limit__isnull=True).update(b2cl_limit=settings.b2cl_limit)
    print(f"\n  sales 0079: kept the B2CL limit {getattr(settings, 'b2cl_limit', None)} on {filled} posted invoice(s).")


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0078_b2cl_limit_kept_at_posting"),
        ("accounting", "0031_books_opening"),
    ]

    operations = [migrations.RunPython(fill, migrations.RunPython.noop)]
