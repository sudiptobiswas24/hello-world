"""
Marking the accounts money already went through as holding money, for
the migrations that do it: accounting's (payments, statements, the
company's bank), hr's (claims paid) and purchasing's (challans paid).
Frozen with them, as a migration is.
"""


def mark(apps, account_ids):
    """
    `account_ids` hold money, but not one a setting keeps something else
    on (what is owed, held or taxed: a payment once put through the
    receivable does not make it a bank), nor one that is not an asset or
    a liability.
    """
    kept = set()
    for label, skip in (("core.Company", {"default_bank_account"}), ("accounting.TdsSection", set()),
                        ("accounting.Tax", set())):
        model = apps.get_model(label)
        names = [field.attname for field in model._meta.concrete_fields
                 if field.is_relation and field.related_model._meta.label_lower == "accounting.account"
                 and field.name not in skip]
        for row in model.objects.values_list(*names):
            kept.update(row)
    apps.get_model("accounting", "Account").objects.filter(
        pk__in=set(account_ids) - kept - {None}, account_type__in=["asset", "liability"],
    ).update(holds_money=True)
