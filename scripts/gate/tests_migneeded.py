"""
python3 -m unittest scripts/gate/tests_migneeded.py     (from the repository root; needs git, not Django)

The decision is a plain function over changed paths, tested as one; the reading of a repository is
tested on small ones made here.
"""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migneeded  # noqa: E402

APPS = ["accounting", "core", "hr", "sales", "web"]
# sales and hr are built on accounting, accounting on core, web on hr; nothing is built on web or sales.
DEPS = {"accounting": {"core"}, "hr": {"accounting", "core"}, "sales": {"accounting", "core", "auth"},
        "web": {"hr", "core"}}
TAGGED = {"apps/sales/tests_tcs.py": "sales", "apps/hr/tests_statutory.py": "hr"}


def decide(changed, **more):
    return migneeded.decide(APPS, changed, TAGGED, DEPS, **more)


class DecisionTests(unittest.TestCase):
    def test_no_migration_changed_needs_nothing(self):
        self.assertEqual(decide(["apps/sales/models.py", "apps/sales/tests.py", "docs/RISKS.md",
                                 "frontend/src/app/registry.ts"]), {})

    def test_an_empty_range_needs_nothing(self):
        self.assertEqual(decide([]), {})

    def test_a_migration_runs_its_app_and_every_dependent(self):
        need = decide(["apps/hr/migrations/0039_timesheet_view_every.py"])
        self.assertEqual(sorted(need), ["hr", "web"])
        self.assertIn("apps/hr/migrations/0039", need["hr"])
        self.assertIn("depend on hr", need["web"])

    def test_dependents_are_followed_all_the_way_down(self):
        self.assertEqual(sorted(decide(["apps/accounting/migrations/0001_initial.py"])),
                         ["accounting", "hr", "sales", "web"])

    def test_a_leaf_app_runs_alone(self):
        self.assertEqual(sorted(decide(["apps/web/migrations/0002_x.py"])), ["web"])

    def test_the_kernel_runs_everything(self):
        self.assertEqual(sorted(decide(["apps/core/migrations/0050_x.py"])), APPS)

    def test_a_removed_or_non_python_file_under_migrations_counts(self):
        self.assertEqual(sorted(decide(["apps/web/migrations/seed.json"])), ["web"])

    def test_a_tagged_test_file_runs_its_own_app_and_no_dependent(self):
        need = decide(["apps/hr/tests_statutory.py"])
        self.assertEqual(sorted(need), ["hr"])
        self.assertIn("holds migration tests", need["hr"])

    def test_a_module_a_migration_imports_runs_the_importers_and_their_dependents(self):
        need = decide(["apps/core/trigram.py"], imported={"apps/core/trigram.py": {"accounting"}})
        self.assertEqual(sorted(need), ["accounting", "hr", "sales", "web"])
        self.assertIn("its migrations import", need["accounting"])

    def test_a_helper_whose_change_reaches_runs_the_importing_app_only(self):
        need = decide(["apps/core/tests_roles.py"], helpers={"apps/core/tests_roles.py": {"hr"}})
        self.assertEqual(sorted(need), ["hr"])

    def test_the_sentinel_settings_requirements_and_the_gate_run_everything(self):
        for path in ("apps/core/lock_order.py", "apps/core/apps.py", "config/settings.py", "requirements.txt",
                     "manage.py", "scripts/gate/lane.sh", "scripts/gate/migneeded.py"):
            with self.subTest(path):
                need = decide([path, "apps/sales/models.py"])
                self.assertEqual(sorted(need), APPS)
                self.assertIn(path, need["web"])

    def test_the_gates_readme_runs_nothing(self):
        self.assertEqual(decide(["scripts/gate/README.md"]), {})

    def test_locking_code_in_the_kernels_models_runs_everything(self):
        self.assertEqual(decide(["apps/core/models.py"]), {})
        self.assertEqual(sorted(decide(["apps/core/models.py"], locking_touched=True)), APPS)

    def test_a_lookalike_path_is_not_a_migration(self):
        self.assertEqual(decide(["apps/hr/migrations_notes.py", "docs/apps/hr/migrations/x.py",
                                 "apps/core/lock_order_notes.py", "configs/x.py"]), {})

    def test_an_app_nobody_listed_still_runs(self):
        self.assertEqual(sorted(decide(["apps/stores/migrations/0001_initial.py"])), ["stores"])


HELPER = textwrap.dedent('''\
    import datetime

    TODAY = datetime.date(2026, 1, 1)


    def build(test):
        return TODAY


    def fixture(test, cls):
        return build(cls)


    class Base:
        pass


    class RaceCase(Base):
        def test_one(self):
            pass


    class Other:
        def test_two(self):
            pass
''')


def line_of(text):
    return HELPER.splitlines().index(text) + 1


class ReachesTests(unittest.TestCase):
    def change(self, text, names=("fixture",)):
        line = line_of(text)
        return migneeded.reaches(HELPER, set(names), [(line, line, False)])

    def test_a_change_inside_another_class_does_not_reach(self):
        self.assertFalse(self.change("    def test_two(self):"))
        self.assertFalse(self.change("    def test_one(self):"))

    def test_a_change_to_the_imported_definition_reaches(self):
        self.assertTrue(self.change("    return build(cls)"))

    def test_a_change_to_what_the_definition_calls_reaches(self):
        self.assertTrue(self.change("    return TODAY"))

    def test_a_change_to_a_base_class_reaches_the_class_built_on_it(self):
        self.assertTrue(self.change("    pass", names=("RaceCase",)))
        self.assertFalse(self.change("    pass", names=("Other",)))

    def test_a_change_outside_every_definition_reaches(self):
        self.assertTrue(self.change("TODAY = datetime.date(2026, 1, 1)"))
        self.assertTrue(self.change("import datetime"))

    def test_a_name_that_is_not_a_definition_here_reaches(self):
        self.assertTrue(self.change("    def test_two(self):", names=("TODAY",)))
        self.assertTrue(self.change("    def test_two(self):", names=("gone",)))

    def test_a_whole_module_import_reaches(self):
        self.assertTrue(self.change("    def test_two(self):", names=(migneeded.WHOLE,)))

    def test_a_removal_inside_another_definition_does_not_reach_and_one_between_definitions_does(self):
        inside = line_of("    def test_two(self):")
        self.assertFalse(migneeded.reaches(HELPER, {"fixture"}, [(inside, inside + 1, True)]))
        between = line_of("    return build(cls)")
        self.assertTrue(migneeded.reaches(HELPER, {"fixture"}, [(between + 1, between + 2, True)]))
        wanted = line_of("def fixture(test, cls):")
        self.assertTrue(migneeded.reaches(HELPER, {"fixture"}, [(wanted, wanted + 1, True)]))

    def test_a_module_that_does_not_parse_reaches(self):
        self.assertTrue(migneeded.reaches("def (:\n", {"fixture"}, [(1, 1, False)]))

    def test_hunks_reads_additions_changes_and_removals(self):
        diff = "@@ -3107,0 +3108,34 @@ class A:\n+x\n@@ -5 +7 @@\n-a\n+b\n@@ -9,2 +10,0 @@ def f():\n-a\n-b\n"
        self.assertEqual(migneeded.hunks(diff), [(3108, 3141, False), (7, 7, False), (10, 11, True)])


class RepositoryTests(unittest.TestCase):
    """The same rule read off a real repository: two apps, sales built on core, one tagged test each."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.write("apps/core/migrations/0001_initial.py", "dependencies = []\n")
        self.write("apps/core/models.py", "def lock_rows():\n    return 1\n\n\ndef name():\n    return 2\n")
        self.write("apps/core/trigram.py", "def trigram_indexes():\n    return []\n")
        self.write("apps/core/tests_numbering.py", '@tag("migration")\nclass T:\n    pass\n')
        self.write("apps/sales/migrations/0001_initial.py",
                   'from apps.core.trigram import trigram_indexes\ndependencies = [\n    ("core", "0001_initial"),\n]\n')
        self.write("apps/sales/tests_base.py", HELPER)
        self.write("apps/sales/tests_tcs.py",
                   'from .tests_base import fixture\n\n\n@tag("migration")\nclass T:\n    pass\n')
        self.write("apps/sales/tests.py", "class Plain:\n    pass\n")
        self.write("apps/web/migrations/0001_initial.py", 'dependencies = [("sales", "0001_initial")]\n')
        self.base = self.commit()

    def write(self, path, text):
        full = os.path.join(self.dir.name, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as out:
            out.write(text)

    def git(self, *args):
        return subprocess.run(["git", "-C", self.dir.name, "-c", "user.name=t", "-c", "user.email=t@example.com",
                               "-c", "commit.gpgsign=false", *args], check=True, capture_output=True,
                              text=True).stdout.strip()

    def commit(self):
        if not os.path.isdir(os.path.join(self.dir.name, ".git")):
            self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "x")
        return self.git("rev-parse", "HEAD")

    def need(self, base=None, tip="HEAD"):
        need, apps, _, _ = migneeded.needed(base or self.base, tip, self.dir.name)
        self.assertEqual(apps, ["core", "sales", "web"])
        return sorted(need)

    def test_application_code_and_untagged_tests_need_nothing(self):
        self.write("apps/sales/models.py", "x = 1\n")
        self.write("apps/sales/tests.py", "class Plain:\n    x = 1\n")
        self.commit()
        self.assertEqual(self.need(), [])

    def test_nothing_changed_needs_nothing(self):
        self.assertEqual(self.need(), [])

    def test_a_new_migration_runs_its_app_and_dependents(self):
        self.write("apps/sales/migrations/0002_more.py", 'dependencies = [("sales", "0001_initial")]\n')
        self.commit()
        self.assertEqual(self.need(), ["sales", "web"])

    def test_a_kernel_migration_runs_all(self):
        self.write("apps/core/migrations/0002_more.py", 'dependencies = [("core", "0001_initial")]\n')
        self.commit()
        self.assertEqual(self.need(), ["core", "sales", "web"])

    def test_a_removed_migration_counts(self):
        os.remove(os.path.join(self.dir.name, "apps/web/migrations/0001_initial.py"))
        self.write("apps/web/migrations/0001_squashed.py", 'dependencies = [("sales", "0001_initial")]\n')
        self.commit()
        self.assertEqual(self.need(), ["web"])

    def test_a_module_a_migration_imports(self):
        self.write("apps/core/trigram.py", "def trigram_indexes():\n    return [1]\n")
        self.commit()
        self.assertEqual(self.need(), ["sales", "web"])

    def test_a_tagged_file_runs_its_app_alone(self):
        self.write("apps/core/tests_numbering.py", '@tag("migration")\nclass T:\n    x = 1\n')
        self.commit()
        self.assertEqual(self.need(), ["core"])

    def test_a_file_that_lost_its_tag_still_runs_its_app(self):
        self.write("apps/core/tests_numbering.py", "class T:\n    pass\n")
        self.commit()
        self.assertEqual(self.need(), ["core"])

    def test_a_test_added_to_a_helper_does_not_reach_and_a_changed_fixture_does(self):
        self.write("apps/sales/tests_base.py", HELPER + "\n\nclass Added:\n    def test_three(self):\n        pass\n")
        added = self.commit()
        self.assertEqual(self.need(), [])
        self.write("apps/sales/tests_base.py", HELPER.replace("return build(cls)", "return build(test)"))
        self.commit()
        self.assertEqual(self.need(added), ["sales"])

    def test_a_removed_helper_runs_its_app(self):
        os.remove(os.path.join(self.dir.name, "apps/sales/tests_base.py"))
        self.commit()
        self.assertEqual(self.need(), ["sales"])

    def test_locking_code_in_the_kernels_models(self):
        self.write("apps/core/models.py", "def lock_rows():\n    return 1\n\n\ndef name():\n    return 3\n")
        other = self.commit()
        self.assertEqual(self.need(), [])
        self.write("apps/core/models.py", "def lock_rows():\n    return 4\n\n\ndef name():\n    return 3\n")
        self.commit()
        self.assertEqual(self.need(other), ["core", "sales", "web"])

    def test_the_gate_and_settings_run_all(self):
        for path in ("scripts/gate/lane.sh", "config/settings.py", "requirements.txt"):
            self.write(path, "x\n")
            before = self.git("rev-parse", "HEAD")
            self.commit()
            self.assertEqual(self.need(before), ["core", "sales", "web"], path)

    def test_a_base_that_cannot_be_read_runs_all(self):
        for base in ("0" * 40, "1234567890123456789012345678901234567890", "no-such-branch"):
            self.assertEqual(self.need(base), ["core", "sales", "web"], base)

    def test_a_base_that_is_not_an_ancestor_runs_all(self):
        self.git("checkout", "-q", "--orphan", "elsewhere")
        self.write("docs/x.md", "x\n")
        self.commit()
        self.assertEqual(self.need(), ["core", "sales", "web"])

    def test_a_tip_that_cannot_be_read_is_refused(self):
        with self.assertRaises(migneeded.Unreadable):
            migneeded.needed(self.base, "no-such-tip", self.dir.name)

    def test_the_report_reads_back(self):
        self.write("apps/sales/migrations/0002_more.py", 'dependencies = [("sales", "0001_initial")]\n')
        tip = self.commit()
        text = migneeded.report(*migneeded.needed(self.base, "HEAD", self.dir.name))
        self.assertIn(f"skip core: no migration change since {self.base[:7]}", text)
        self.assertEqual(migneeded.read(text), (self.base, tip, {"sales", "web"}, {"core"}))
        with self.assertRaises(ValueError):
            migneeded.read("need sales: x\n")


if __name__ == "__main__":
    unittest.main()
