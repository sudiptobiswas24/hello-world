"""
The nightly backup takes the attachments with the database, and a restore
puts both back (O221): deploy/backup.sh and deploy/restore.sh, run as they
are, against a scratch database on the PostgreSQL server the plant runs.

Skipped where there is no PostgreSQL client or server to run them against,
as the browser tests skip without a browser.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

SERVER = os.environ.get("BACKUP_TEST_SERVER", "postgres://erp:erp@localhost:5432/postgres")
DEPLOY = Path(settings.BASE_DIR) / "deploy"
TOOLS = all(shutil.which(tool) for tool in ("pg_dump", "pg_restore", "psql", "tar"))


@skipUnless(TOOLS, "no PostgreSQL client tools on this machine")
class ABackupRestoresItsAttachmentsTests(SimpleTestCase):
    def psql(self, url, sql):
        return subprocess.run(["psql", "--no-psqlrc", "-tA", "-v", "ON_ERROR_STOP=1", url, "-c", sql],
                              capture_output=True, text=True, check=True).stdout.strip()

    def setUp(self):
        self.name = f"erp_backup_check_{os.getpid()}"
        try:
            self.psql(SERVER, f"DROP DATABASE IF EXISTS {self.name}")
            self.psql(SERVER, f"CREATE DATABASE {self.name}")
        except (subprocess.CalledProcessError, OSError) as failure:
            self.skipTest(f"no PostgreSQL server to back up at {SERVER}: {failure}")
        self.addCleanup(self.psql, SERVER, f"DROP DATABASE IF EXISTS {self.name} WITH (FORCE)")
        self.url = SERVER.rsplit("/", 1)[0] + "/" + self.name
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        self.backups, self.media = root / "backups", root / "media"
        self.backups.mkdir()
        self.scan = self.media / "attachments" / "2026" / "10" / "lr-4471.pdf"
        self.scan.parent.mkdir(parents=True)
        self.scan.write_bytes(b"%PDF-1.4 LR 4471, 120 bales")
        self.psql(self.url, "CREATE TABLE attached (id int, path text); "
                            "INSERT INTO attached VALUES (1, 'attachments/2026/10/lr-4471.pdf')")
        self.env = os.environ | {"DATABASE_URL": self.url, "BACKUP_DIR": str(self.backups),
                                 "MEDIA_DIR": str(self.media), "RESTORE_CONFIRM": "yes"}

    def run_script(self, *args, env=None):
        return subprocess.run(["sh", *args], capture_output=True, text=True, env=env or self.env)

    def test_a_restore_brings_an_attachment_back(self):
        taken = self.run_script(str(DEPLOY / "backup.sh"), "now")
        self.assertEqual(taken.returncode, 0, taken.stderr)
        (dump,) = self.backups.glob("erp_*.dump")
        (archive,) = self.backups.glob("erp_*.media.tar.gz")
        self.assertEqual(archive.name, dump.name.replace(".dump", ".media.tar.gz"))
        (manifest,) = self.backups.glob("erp_*.media.sha256")
        self.assertIn("./attachments/2026/10/lr-4471.pdf", manifest.read_text())
        self.assertIn("1 files", taken.stdout)

        # Lost after the backup: the scan and its record; and a file attached since, which the restore removes.
        self.scan.unlink()
        self.psql(self.url, "DELETE FROM attached")
        (self.media / "attached-later.pdf").write_bytes(b"later")

        restored = self.run_script(str(DEPLOY / "restore.sh"), str(dump))
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(self.scan.read_bytes(), b"%PDF-1.4 LR 4471, 120 bales")
        self.assertFalse((self.media / "attached-later.pdf").exists())
        self.assertEqual([path.name for path in self.media.iterdir()], ["attachments"])
        self.assertEqual(self.psql(self.url, "SELECT path FROM attached"), "attachments/2026/10/lr-4471.pdf")

    def test_a_dump_without_its_attachments_is_refused(self):
        taken = self.run_script(str(DEPLOY / "backup.sh"), "now")
        self.assertEqual(taken.returncode, 0, taken.stderr)
        (dump,) = self.backups.glob("erp_*.dump")
        next(self.backups.glob("erp_*.media.tar.gz")).unlink()
        self.psql(self.url, "DELETE FROM attached")
        refused = self.run_script(str(DEPLOY / "restore.sh"), str(dump))
        self.assertEqual(refused.returncode, 1)
        self.assertIn("no attachments archive beside it", refused.stderr)
        self.assertEqual(self.psql(self.url, "SELECT count(*) FROM attached"), "0")
        alone = self.run_script(str(DEPLOY / "restore.sh"), str(dump), env=self.env | {"RESTORE_WITHOUT_MEDIA": "yes"})
        self.assertEqual(alone.returncode, 0, alone.stderr)
        self.assertEqual(self.psql(self.url, "SELECT count(*) FROM attached"), "1")


    def test_an_archive_that_does_not_match_its_manifest_restores_nothing(self):
        """Review F15: the restore checks every attachment's content, not only that there is one."""
        taken = self.run_script(str(DEPLOY / "backup.sh"), "now")
        self.assertEqual(taken.returncode, 0, taken.stderr)
        (dump,) = self.backups.glob("erp_*.dump")
        (manifest,) = self.backups.glob("erp_*.media.sha256")
        digest, name = manifest.read_text().split(maxsplit=1)
        manifest.write_text(f"{'0' * len(digest)}  {name}")
        self.psql(self.url, "DELETE FROM attached")
        self.scan.write_bytes(b"after the backup")
        refused = self.run_script(str(DEPLOY / "restore.sh"), str(dump))
        self.assertEqual(refused.returncode, 1)
        self.assertIn("does not match", refused.stderr)
        self.assertEqual((self.scan.read_bytes(), self.psql(self.url, "SELECT count(*) FROM attached")),
                         (b"after the backup", "0"))
        self.assertEqual(sorted(path.name for path in self.media.iterdir()), ["attachments"])


    def test_an_attachment_added_while_the_backup_runs_leaves_it_restorable(self):
        """
        O244: a scan uploaded as the archive is written (its file, then its record) is in neither the dump
        nor the archive, or in both: the night's backup restores, and every record it holds has its file.
        """
        tools = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tools, True)
        real_tar = shutil.which("tar")
        (tools / "tar").write_text(
            "#!/bin/sh\n"
            f'"{real_tar}" "$@" || exit $?\n'
            'case "$*" in *-czf*)\n'
            f'  printf during > "{self.media}/attached-during.pdf"\n'
            f"  psql --no-psqlrc -q '{self.url}' -c \"INSERT INTO attached VALUES (2, 'attached-during.pdf')\" ;;\n"
            "esac\n")
        (tools / "tar").chmod(0o755)
        taken = self.run_script(str(DEPLOY / "backup.sh"), "now", env=self.env | {"PATH": f"{tools}:{os.environ['PATH']}"})
        self.assertEqual(taken.returncode, 0, taken.stderr)
        (dump,) = self.backups.glob("erp_*.dump")
        restored = self.run_script(str(DEPLOY / "restore.sh"), str(dump))
        self.assertEqual(restored.returncode, 0, restored.stderr)
        for path in self.psql(self.url, "SELECT path FROM attached ORDER BY id").splitlines():
            self.assertTrue((self.media / path).exists(), f"{path} is recorded and has no file")
