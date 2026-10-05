"""
The account a document books to when it names none: the company's
default for that purpose, or a refusal that says where to set one.

Before these existed, an order line left without a revenue account was
invoiced into a database error, and every screen raising an invoice had
to make its clerk choose the receivable account again.
"""

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404

from apps.core.models import Company

PURPOSES = {
    # Read as attributes, not looked up by name, so each is plainly read
    # (audit_invariants finds an unread setting by its absence).
    "revenue": lambda company: company.default_revenue_account,
    "receivable": lambda company: company.default_receivable_account,
    "payable": lambda company: company.default_payable_account,
    "bank": lambda company: company.default_bank_account,
}


def default_account(purpose, field=None):
    """
    The company's account for `purpose`, or ValidationError keyed by
    `field` (so a form shows it beside the box) naming the setting.
    """
    account = PURPOSES[purpose](Company.get())
    if account is None:
        message = (f"No {purpose} account: choose one, or set the company's default "
                   f"{purpose} account.")
        raise ValidationError({field: [message]} if field else message)
    return account


def chosen_or_default(data, field, purpose):
    """The account a request names in `field`, else the company default."""
    from .models import Account

    chosen = data.get(field)
    if chosen:
        return get_object_or_404(Account, pk=chosen)
    return default_account(purpose, field)
