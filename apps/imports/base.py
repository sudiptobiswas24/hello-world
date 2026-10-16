"""
What every kind of import shares: the report, the cell readers, and the
two helpers that turn a model's refusal into a row-and-column message.
importer.py holds the opening position's kinds and cutover.py the rest;
both are built on this.
"""

import contextlib
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db.models.signals import pre_save

from apps.core.csvrows import RowError, date, decimal, read, required, yes_no

__all__ = ["Report", "RowError", "by_code", "clean", "codes", "date", "decimal", "read", "required",
           "stamped_by", "whole_number", "yes_no"]


@contextlib.contextmanager
def stamped_by(user):
    """
    Every record made while this holds says `user` made it. The views
    stamp created_by on what they save; an import makes its records in
    code, deep under the kind's handler, so the stamp is put on as each
    one is saved. Nothing with a maker already keeps it.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        yield
        return

    def stamp(sender, instance, **kwargs):
        if hasattr(instance, "created_by_id") and instance._state.adding and instance.created_by_id is None:
            instance.created_by = user
        if hasattr(instance, "updated_by_id") and instance.updated_by_id is None:
            instance.updated_by = user

    pre_save.connect(stamp, weak=False, dispatch_uid="imports.stamped_by")
    try:
        yield
    finally:
        pre_save.disconnect(dispatch_uid="imports.stamped_by")


@dataclass
class Report:
    kind: str
    rows: int = 0
    created: int = 0
    errors: list = field(default_factory=list)  # (row number, column, message)
    committed: bool = False
    # (user name, first password) for each login an employees file made:
    # handed out once, and changed at the first sign-in.
    passwords: list = field(default_factory=list)

    def refuse(self, row, column, message):
        self.errors.append((row, column, str(message)))


def by_code(model, row, column, field_name="code", required_=True, **extra):
    value = row.get(column, "")
    if not value:
        if required_:
            raise RowError(column, "is required.")
        return None
    found = model.objects.filter(**{field_name: value}, **extra).first()
    if found is None:
        raise RowError(column, f"no {model._meta.verbose_name} {value!r}.")
    return found


def clean(instance, columns=None):
    """full_clean(), its complaints put on the columns they came from."""
    try:
        instance.full_clean()
    except ValidationError as error:
        if hasattr(error, "error_dict"):
            name, messages = next(iter(error.error_dict.items()))
            column = (columns or {}).get(name, name if name != "__all__" else "")
            raise RowError(column, " ".join(m for e in messages for m in e.messages)) from None
        raise RowError("", " ".join(error.messages)) from None


def whole_number(row, column, required_=False):
    value = row.get(column, "")
    if value == "":
        if required_:
            raise RowError(column, "is required.")
        return None
    try:
        return int(value)
    except ValueError:
        raise RowError(column, f"{value!r} is not a whole number.") from None


def codes(row, column):
    """Several codes in one cell, split on semicolons: taxes, roles."""
    return [part.strip() for part in row.get(column, "").split(";") if part.strip()]
