"""
ESI is taken at the dated ESI rates and covered up to the dated ESI ceiling
(StatutoryFigure), not at a rate typed per person or a ceiling on the
component: one source. A component marked ESI loses its own coverage
ceiling, and each person's row of it becomes 1 ("insured"), as overtime's
is. Posted slips keep the rate they were paid at on their lines. Reversed,
nothing is restored: the rates typed before were the duplicate.
"""

from django.db import migrations

ESI = ("esi", "esi_employer")


def to_the_dated_rates(apps, schema_editor):
    PayComponent = apps.get_model("hr", "PayComponent")
    EmployeeCompensation = apps.get_model("hr", "EmployeeCompensation")
    PayComponent.objects.filter(statutory__in=ESI).update(coverage_ceiling=None)
    EmployeeCompensation.objects.filter(component__statutory__in=ESI).update(amount=1)


class Migration(migrations.Migration):
    dependencies = [("hr", "0033_alter_statutoryfigure_options_and_more")]

    operations = [migrations.RunPython(to_the_dated_rates, migrations.RunPython.noop)]
