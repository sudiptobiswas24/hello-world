"""
F2 cutover (review_rcm): every reverse-charge tax names where its credit waits until the tax is paid in cash, or
its next bill is refused. Set from the account setup_indian_books makes for it (1319) where the books have one;
the taxes it could not set are named in the summary, for the Controller to set in accounting setup.
"""

from django.db import migrations

WAITING = "1319"


def set_waiting_account(apps, schema_editor, out=print):
    Account = apps.get_model("accounting", "Account")
    Tax = apps.get_model("accounting", "Tax")
    lacking = list(Tax.objects.filter(reverse_charge=True, reverse_charge_waiting_account__isnull=True)
                   .order_by("code"))
    if not lacking:
        return [], []
    account = Account.objects.filter(code=WAITING).first()
    if account is None:
        out(f"accounting 0033: no account {WAITING}; reverse-charge taxes left without a credit waiting account: "
            f"{', '.join(tax.code for tax in lacking)}. The Controller sets it in accounting setup before their "
            "next bill posts.")
        return [], [tax.code for tax in lacking]
    Tax.objects.filter(pk__in=[tax.pk for tax in lacking]).update(reverse_charge_waiting_account=account)
    out(f"accounting 0033: reverse-charge credit waits in {WAITING} for {', '.join(tax.code for tax in lacking)}.")
    return [tax.code for tax in lacking], []


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0032_tax_reverse_charge_waiting_account"),
    ]

    operations = [
        migrations.RunPython(set_waiting_account, migrations.RunPython.noop),
    ]
