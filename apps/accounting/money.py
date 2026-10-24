"""
Where money is paid from, or into.

Money moves through a bank, a cash box, a card or an overdraft: an
account marked as holding money (`Account.holds_money`), and still in
use. Nothing else. A claim paid "from" travel expense debited and
credited the same account, and the books said nothing was paid; a
payment "through" the receivable left the customers' ledger disagreeing
with the books for good; inventory, work in progress and the tax
accounts are no better, and an exclusion list misses whichever comes
next. So the mark says what is money, and nothing unmarked is.

The other side holds too. What is owed, what is held and what is taxed
are each kept on an account of their own: an invoice's receivable, a
bill's payable, the company's and the tax settings never name an
account that holds money, or the two would share one balance and
neither could be read.
"""

from django.core.exceptions import ValidationError


def hold_money_account(account):
    """
    `account`, held against being unmarked as money while the caller moves
    money through it, and read again once held (O16).

    Unmarking takes the account FOR UPDATE and then asks whether anything
    is posted to it (Account._check_money); a payment read "holds money"
    unlocked, and an account was unmarked as a payment posted through it:
    both went through, and a payment's money sat on an account that is not
    money. Held FOR SHARE, which payments through one bank take together
    and an unmarking waits for: a payment never waits on another, and the
    second of a payment and an unmarking reads what the first did.
    PostgreSQL only: SQLite takes no row locks, and serialises its writers.
    """
    from django.db import connection, transaction

    if not transaction.get_connection().in_atomic_block:
        # Held outside a transaction, the lock ends with the statement: TdsChallan.pay asked here
        # before its transaction opened, and paid through a bank unmarked meanwhile (review F4).
        raise RuntimeError("hold_money_account() outside a transaction holds nothing.")
    if connection.vendor == "postgresql":
        table = connection.ops.quote_name(account._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT 1 FROM {table} WHERE id = %s FOR SHARE", [account.pk])
    account.refresh_from_db(fields=["holds_money", "is_active"])
    return account


def refuse_as_money_account(account, field="bank_account"):
    """
    Refused, beside `field`, unless money may move through `account`.

    Inside a transaction, where money is moved, asked of the account as held
    (hold_money_account). Outside one, as a draft is saved, only read: that
    decides nothing, and the move asks again inside its own transaction.
    """
    from apps.core.models import in_a_transaction_it_opened

    if account is None:
        return
    if account.pk is not None and in_a_transaction_it_opened():
        hold_money_account(account)
    elif account.pk is not None:
        account.refresh_from_db(fields=["holds_money", "is_active"])
    if not account.holds_money:
        raise ValidationError({field: [
            f"{account} is not a bank, cash or card account; money is paid from one, and into one."]})
    if not account.is_active:
        raise ValidationError({field: [f"{account} is no longer in use."]})


def refuse_money_kept_as(record, *fields, changed_only=True):
    """
    Refused, beside the field, where `record` keeps something other than
    money (what is owed, held or taxed) on an account that holds money.
    `changed_only`: as it is set, not every later save of it. The accounts
    are read afresh: what one is may have changed since it was named.
    """
    from .models import Account

    stored = {}
    if changed_only and record.pk:
        attnames = [record._meta.get_field(name).attname for name in fields]
        stored = type(record).objects.filter(pk=record.pk).values(*attnames).first() or {}
    asked = {}
    for name in fields:
        attname = record._meta.get_field(name).attname
        account_id = getattr(record, attname)
        if account_id is not None and not (changed_only and stored.get(attname) == account_id):
            asked[name] = account_id
    money = {account.pk: account for account in Account.objects.filter(pk__in=asked.values(), holds_money=True)}
    for name, account_id in asked.items():
        if account_id in money:
            raise ValidationError({name: [
                f"{money[account_id]} is a bank, cash or card account; the "
                f"{record._meta.get_field(name).verbose_name} is an account of its own, not where the "
                "money is."]})


def settings_fields(model):
    """`model`'s settings naming an account for something other than money."""
    from .models import Account

    return [field.name for field in model._meta.concrete_fields
            if field.is_relation and field.related_model is Account and field.name != "default_bank_account"]


def settings_models():
    from apps.core.models import Company

    from .models import Tax
    from .tds import TdsSection

    return (Company, TdsSection, Tax)


def purposes_kept_on(account):
    """What the settings keep on `account`, in words ('' for nothing): it is not then money."""
    for model in settings_models():
        for name in settings_fields(model):
            named = model.objects.filter(**{name: account}).first()
            if named is not None:
                return f"{model._meta.get_field(name).verbose_name} of {named}"
    return ""
