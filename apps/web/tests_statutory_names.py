"""
No screen or print writes a tax form's or challan's name itself: they are
data (statutory/names.csv, accounting.statutory_names), named for the period
a document is of. A screen or print still saying "26Q", "27EQ" or "challan
281" names the 1961 Act's form on a period the 2025 Act governs (g24 brief
fix_statutory, decision 4).
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

def form_numbers():
    """{key: [number, ...]}: the form and challan numbers statutory/names.csv names, the Acts left out."""
    from apps.accounting.statutory_names import _names

    return {key: [name.split()[-1] for _since, name, _source in rows] for key, rows in _names().items()
            if key != "act"}


def hard_coded():
    """
    Every form or challan number names.csv holds, written as "Form 140",
    "Challan ITNS 281", "ITNS 281N" or bare where it has letters ("26Q"):
    derived from the file, so a key added there is scanned for too.
    """
    numbers = sorted({number for found in form_numbers().values() for number in found} | {"27Q", "24Q"},
                     key=len, reverse=True)
    lead = r"(?:form|challan|itns)\s*(?:no\.?\s*)?(?:itns\s*)?"
    alternatives = [lead + re.escape(number) + r"\b" for number in numbers]
    alternatives += [r"\b" + re.escape(number) + r"\b" for number in numbers if re.search("[A-Za-z]", number)]
    return re.compile("|".join(alternatives), re.IGNORECASE)


HARD_CODED = hard_coded()


def screens_and_prints():
    root = Path(settings.BASE_DIR)
    screens = [path for path in (root / "frontend" / "src").rglob("*")
               if path.suffix in (".ts", ".tsx") and ".test." not in path.name]
    prints = [path for path in (root / "apps").rglob("*")
              if path.suffix == ".py" and (path.name == "documents.py" or path.parent.name == "templates")
              or path.suffix == ".html"]
    return screens + prints


class NoScreenOrPrintNamesAFormItselfTests(SimpleTestCase):
    def test_no_screen_or_print_hard_codes_a_form_or_challan_name(self):
        files = screens_and_prints()
        self.assertGreater(len(files), 100)
        found = [f"{path.relative_to(settings.BASE_DIR)}:{number}: {line.strip()}"
                 for path in files for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                 if HARD_CODED.search(line)]
        self.assertEqual(found, [])

    def test_the_scan_sees_every_key_names_csv_holds(self):
        for key, numbers in form_numbers().items():
            for number in numbers:
                self.assertTrue(HARD_CODED.search(f"Form {number}") or HARD_CODED.search(f"Challan ITNS {number}"),
                                (key, number))

    def test_the_pattern_catches_what_it_is_for(self):
        for line in ('title="TDS return (26Q)"', "Form 27EQ", "keywords: tds challan 281", "Challan No. 281"):
            self.assertTrue(HARD_CODED.search(line), line)
        for line in ("Form 127: not collected", "27C before April", "Challan ITNS 281N"):
            self.assertTrue(HARD_CODED.search(line), line)
        self.assertFalse(HARD_CODED.search("if (code < 32 || code === 127) return null;"))
