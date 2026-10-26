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

HARD_CODED = re.compile(r"\b(26Q|27EQ|27Q|24Q)\b|challan\s*(no\.?\s*)?281", re.IGNORECASE)


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

    def test_the_pattern_catches_what_it_is_for(self):
        for line in ('title="TDS return (26Q)"', "Form 27EQ", "keywords: tds challan 281", "Challan No. 281"):
            self.assertTrue(HARD_CODED.search(line), line)
        self.assertFalse(HARD_CODED.search("Challan ITNS 281N from the API"))
