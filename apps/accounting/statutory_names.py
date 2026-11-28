"""
The names the law gives the plant's tax forms, challans and sections, by
the day they apply from: the Income-tax Act 1961's before 1 April 2026, the
Income-tax Act 2025's from it (Form 140 for 26Q, Form 143 for 27EQ, challan
ITNS 281N for 281, and so on). Data, kept in statutory/names.csv at the
repository's root with the source of each row; never text written into a
screen or a print. A screen asks the API, and the API asks name_on() for the
period the document is of.
"""

import csv
import datetime
import functools

from django.conf import settings
from django.core.exceptions import ValidationError

from apps.core.models import to_date

NAMES_FILE = "names.csv"


def statutory_dir():
    return settings.BASE_DIR / "statutory"


@functools.lru_cache(maxsize=1)
def _names():
    rows = {}
    with open(statutory_dir() / NAMES_FILE, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows.setdefault(row["key"], []).append(
                (datetime.date.fromisoformat(row["applies_from"]), row["name"], row["source"]))
    return {key: sorted(found) for key, found in rows.items()}


def name_on(key, day):
    """The name `key` (tds_return, tcs_return, tds_challan, act, ...) carries on `day`."""
    day = to_date(day)
    found = [name for since, name, _source in _names().get(key, ()) if since <= day]
    if not found:
        raise ValidationError(f"statutory/{NAMES_FILE} names no {key.replace('_', ' ')} for {day:%d %b %Y}.")
    return found[-1]


def act_2025_from():
    """The first day the Income-tax Act 2025's names apply."""
    return max(since for since, _name, _source in _names()["act"])


def section_on(section, day):
    """The section of the Act standing on `day` that `section` (a TdsSection or TcsSection) is under."""
    if to_date(day) >= act_2025_from():
        return section.section_2025 or section.code
    return section.section_1961 or section.code


def period_names(tax, day, section=None):
    """
    The names the law gives the TDS (`tax` "tds") or TCS ("tcs") period
    holding `day`: the Act, the quarterly return's form, the challan, and,
    given a section, the section of the Act it is under. The one place a
    period is named: a challan asks it for the month it pays, not the day it
    is paid (March's tax paid on 7 April is the 1961 Act's), a return for its
    quarter, a return row and a collection for their own day.
    """
    day = to_date(day)
    names = {"act": name_on("act", day), "form": name_on(f"{tax}_return", day),
             "challan_form": name_on(f"{tax}_challan", day)}
    if section is not None:
        names["section"] = section.code_on(day)
    return names


def heading(kind, start, end):
    """
    What a TDS (`kind` "tds") or TCS ("tcs") challan or return for the
    period `start` to `end` carries: the company's TAN, refused without it,
    the Act and the form and challan names standing at the period's end.
    """
    from .challans import company_tan

    end = to_date(end)
    return {"tan": company_tan(f"a {kind.upper()} return"), **period_names(kind, end), "from": to_date(start), "to": end}
