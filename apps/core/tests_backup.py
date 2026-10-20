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


class AFailedBackupKeepsTheOlderOnesTests(SimpleTestCase):
    """
    O314: the nightly loop called `dump || echo BACKUP FAILED`, which turns `set -e` off inside dump,
    so a failed pg_dump printed "backup written" and retention deleted every older backup. Run as it
    is, with pg_dump, tar and sleep stubbed: no database is needed to see what a failure does.
    """

    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        self.bin, self.backups, media = root / "bin", root / "backups", root / "media"
        for folder in (self.bin, self.backups, media):
            folder.mkdir()
        (media / "a.txt").write_text("x")
        self.stub("pg_dump", 'for a; do case $a in --file=*) f=${a#--file=};; esac; done; echo dump > "$f"\n')
        # The loop sleeps twice: once before tonight's backup, and is stopped at the second.
        self.stub("sleep", 'n=$(cat "$COUNT" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$COUNT"\n'
                           '[ "$n" -ge 2 ] && kill -9 $PPID\nexit 0\n')
        self.old = self.backups / "erp_2026-09-01_0200.dump"
        self.old.write_text("old")
        os.utime(self.old, (1, 1))
        self.env = os.environ | {"PATH": f"{self.bin}:{os.environ['PATH']}", "BACKUP_DIR": str(self.backups),
                                 "MEDIA_DIR": str(media), "DATABASE_URL": "x", "COUNT": str(root / "count")}

    def stub(self, name, body):
        (self.bin / name).write_text("#!/bin/sh\n" + body)
        (self.bin / name).chmod(0o755)

    def backup(self, *args):
        done = subprocess.run(["sh", str(DEPLOY / "backup.sh"), *args], env=self.env, capture_output=True,
                              text=True, timeout=60)
        return done.returncode, done.stdout + done.stderr

    def test_a_failed_dump_is_reported_failed_and_deletes_no_older_backup(self):
        self.stub("pg_dump", "echo 'pg_dump: connection refused' >&2\nexit 1\n")
        _, said = self.backup()
        self.assertIn("BACKUP FAILED: pg_dump exited 1; older backups kept", said)
        self.assertNotIn("backup written", said)
        self.assertTrue(self.old.exists(), "retention deleted the old backup although tonight's dump failed")
        self.assertEqual(list(self.backups.glob("erp_*.dump")), [self.old])

    def test_an_archive_that_fails_keeps_the_older_backups_and_leaves_no_dump_looking_complete(self):
        real_tar = shutil.which("tar")
        self.stub("tar", f'case "$*" in *-czf*) echo "tar: No space left on device" >&2; exit 2;; esac\n'
                         f'exec {real_tar} "$@"\n')
        _, said = self.backup()
        self.assertIn("BACKUP FAILED: archiving the attachments exited 2", said)
        self.assertNotIn("backup written", said)
        self.assertEqual(list(self.backups.glob("erp_*.dump")), [self.old])

    def test_a_backup_now_that_fails_exits_failed(self):
        self.stub("pg_dump", "exit 1\n")
        status, said = self.backup("now")
        self.assertEqual(status, 1, said)
        self.assertIn("BACKUP FAILED", said)
        self.assertTrue(self.old.exists())

    def stub_hashing(self, body):
        """sha256sum as the manifest's per-file hash calls it (no arguments) is `body`; any other call is the real one."""
        real = shutil.which("sha256sum")
        self.stub("sha256sum", f'if [ $# -eq 0 ]; then\n{body}\nfi\nexec {real} "$@"\n')

    def test_a_hash_that_fails_inside_the_manifest_fails_the_night(self):
        # Review of O314: the manifest's pipe reported sort's status; sha256sum exiting 3 wrote empty hashes,
        # said "backup written" and retention deleted the older set.
        self.stub_hashing("cat > /dev/null; exit 3")
        status, said = self.backup("now")
        self.assertEqual(status, 1, said)
        self.assertIn("BACKUP FAILED: hashing the archived attachments", said)
        self.assertNotIn("backup written", said)
        self.assertTrue(self.old.exists())

    def test_a_manifest_that_does_not_verify_fails_the_night(self):
        self.stub_hashing("cat > /dev/null; echo '0000000000000000000000000000000000000000000000000000000000000000  -'; exit 0")
        status, said = self.backup("now")
        self.assertEqual(status, 1, said)
        self.assertIn("BACKUP FAILED: the manifest does not verify against the archive", said)
        self.assertTrue(self.old.exists())
        self.assertEqual(list(self.backups.glob("erp_*.media.sha256")), [])

    def test_a_backup_that_succeeds_still_retires_the_older_ones(self):
        _, said = self.backup()
        self.assertIn("backup written", said)
        self.assertFalse(self.old.exists())
        self.assertEqual(len(list(self.backups.glob("erp_*.dump"))), 1)
