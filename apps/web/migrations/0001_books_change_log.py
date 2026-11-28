"""
The books' own change log on PostgreSQL (apps/core/ledger_log.py): the append-only table, its guards, and a
trigger on each table of the books of account. Here, not in core, because it reaches every module's books
tables and core depends on none of them. Idempotent; the reverse drops the triggers and functions it made, and
the log only while it holds no row. SQLite: nothing.
"""

from django.db import migrations

# The tables as they stood when this was written: a later migration adds a table by calling create() with it.
TABLES = (
    "accounting_journalentry", "accounting_journalline", "accounting_accountingperiod", "accounting_payment",
    "sales_invoice", "sales_invoiceline", "sales_invoicelinetax", "sales_invoicepayment", "sales_invoicewriteoff",
    "purchasing_bill", "purchasing_billline", "purchasing_billlinetax", "purchasing_billpayment",
    "inventory_stockmovement",
    "hr_payrun", "hr_payslip", "hr_payslipline",
    "assets_fixedasset", "assets_depreciationentry",
)


def forwards(apps, schema_editor):
    from apps.core.ledger_log import create

    create(schema_editor.connection, TABLES)


def backwards(apps, schema_editor):
    from apps.core.ledger_log import drop

    drop(schema_editor.connection, TABLES)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0043_edit_log"),
        ("accounting", "0031_books_opening"),
        ("sales", "0080_approved_figures_in_base"),
        ("purchasing", "0081_approved_figures_in_base"),
        ("inventory", "0041_stock_value_permission"),
        ("hr", "0032_settled_without_a_slip"),
        ("assets", "0007_what_each_asset_stands_on"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
