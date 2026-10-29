"""
Reading a CSV somebody saved from a spreadsheet, one row at a time: the
rows as dicts with trimmed, lower-cased headers, and a reader for each
kind of cell that refuses in words naming the column. The import tool
and the punch file both read this way.
"""

import csv
import datetime
import io
import re
from decimal import Decimal, InvalidOperation


class RowError(Exception):
    def __init__(self, column, message):
        super().__init__(message)
        self.column, self.message = column, message


def read(text):
    """Rows of a CSV, as dicts with trimmed keys and values; the header names the columns."""
    if text.startswith("﻿"):  # saved from a spreadsheet
        text = text[1:]
    reader = csv.DictReader(io.StringIO(text))
    return [
        {(key or "").strip().lower(): (value or "").strip() for key, value in row.items()}
        for row in reader
    ]


def says(key, word):
    """Whether a heading says `word`: as a word of its own ("Ref No."), or inside a longer one ("Particulars")."""
    tokens = re.split(r"[^a-z0-9]+", key)
    return word in tokens or (len(word) > 3 and word in key)


# -- reading a cell ------------------------------------------------------


def required(row, column):
    value = row.get(column, "")
    if not value:
        raise RowError(column, "is required.")
    return value


def decimal(row, column, places=None, required_=False):
    value = row.get(column, "")
    if not value:
        if required_:
            raise RowError(column, "is required.")
        return None
    try:
        number = Decimal(value.replace(",", ""))
    except InvalidOperation:
        raise RowError(column, f"{value!r} is not a number.") from None
    if not number.is_finite():
        raise RowError(column, f"{value!r} is not a number.")
    if places is not None and number != number.quantize(Decimal(1).scaleb(-places)):
        raise RowError(column, f"{value!r} has more than {places} decimal places.")
    return number


def date(row, column, required_=True):
    value = row.get(column, "")
    if not value:
        if required_:
            raise RowError(column, "is required.")
        return None
    for shape in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%m-%y", "%d/%m/%y", "%d %b %Y", "%d-%b-%Y", "%d %B %Y"):
        try:
            day = datetime.datetime.strptime(value, shape).date()
        except ValueError:
            continue
        # strptime reads "01/01/26" with %Y as the year 26; a bank's
        # two-digit year is the next shape's to read.
        if day.year >= 1900:
            return day
    raise RowError(column, f"{value!r} is not a date; give it as YYYY-MM-DD or DD-MM-YYYY.")


def yes_no(row, column, default):
    value = row.get(column, "").lower()
    if not value:
        return default
    if value in ("yes", "y", "true", "1"):
        return True
    if value in ("no", "n", "false", "0"):
        return False
    raise RowError(column, f"{value!r} is not yes or no.")
