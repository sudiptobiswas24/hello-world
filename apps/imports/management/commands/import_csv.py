"""
python manage.py import_csv <kind> <file.csv> [--commit] [--date ...] [--reason ...] [--against ...]
python manage.py import_csv <kind> <file.csv> --template

A dry run unless told --commit: every problem is listed by row and
column, and nothing is kept. --template writes a blank file of the kind,
its columns in the header. docs/IMPORT.md gives each kind's columns.
"""

import csv
import datetime
import os

from django.core.management.base import BaseCommand, CommandError

from apps.imports.importer import KINDS, run
from apps.imports.importer import template as blank_file


def _date(value):
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise CommandError(f"--date {value!r} is not YYYY-MM-DD.") from None


class Command(BaseCommand):
    help = "Bring the old system's records in from a CSV file: dry run unless --commit."

    def add_arguments(self, parser):
        parser.add_argument("kind", choices=KINDS)
        parser.add_argument("path")
        parser.add_argument("--commit", action="store_true", help="Keep it, if every row is clean.")
        parser.add_argument("--date", type=_date, help="The go-live date the opening position is at.")
        parser.add_argument("--reason", default="OPENING",
                            help="Opening stock: the adjustment reason (its account is the other side).")
        parser.add_argument("--against", default="",
                            help="Open invoices and bills: the opening-balance account code.")
        parser.add_argument("--memo", default="")
        parser.add_argument("--template", action="store_true",
                            help="Write a blank file of this kind to the path instead.")
        parser.add_argument("--passwords-out", default="",
                            help="Employees: where to write each new login's first password.")

    def handle(self, *args, kind, path, commit, date, reason, against, memo, template: bool = False,
               passwords_out="", **options):
        if template:
            if os.path.exists(path):
                raise CommandError(f"{path} exists; a template is never written over a file.")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(blank_file(kind))
            self.stdout.write(f"{kind}: a blank file is at {path}.")
            return
        if kind == "employees" and commit and not passwords_out:
            raise CommandError("employees needs --passwords-out: the file each new login's first "
                               "password is written to, to hand out and then destroy.")
        if kind in ("opening_stock", "opening_balances") and date is None:
            raise CommandError(f"{kind} needs --date: the day the opening position stands at.")
        if kind in ("open_invoices", "open_bills") and not against:
            raise CommandError(f"{kind} needs --against: the opening-balance account code.")
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except (OSError, UnicodeDecodeError) as error:
            raise CommandError(f"Cannot read {path}: {error}") from None
        report = run(kind, text, commit=commit, date=date, reason=reason, against=against, memo=memo)
        for row, column, message in report.errors:
            where = f"row {row}" if row else "file"
            self.stdout.write(f"{where}{', ' + column if column else ''}: {message}")
        if report.errors:
            raise CommandError(f"{len(report.errors)} problem(s) in {report.rows} row(s); nothing was kept.")
        if report.committed:
            if report.passwords:
                # Readable by its owner only: it holds first passwords.
                descriptor = os.open(passwords_out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(["username", "first_password"])
                    writer.writerows(report.passwords)
                self.stdout.write(f"{len(report.passwords)} new login(s); their first passwords are in "
                                  f"{passwords_out}. Hand them out, then delete the file.")
            self.stdout.write(self.style.SUCCESS(f"{kind}: {report.created} brought in from {report.rows} row(s)."))
        else:
            self.stdout.write(f"{kind}: {report.rows} row(s) are clean. Dry run: nothing was kept; "
                              "run again with --commit.")
