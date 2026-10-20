"""audit2_core probe: the nightly backup loop (deploy/backup.sh) when pg_dump fails."""
import os
import pathlib
import shutil
import subprocess
import tempfile

from django.test import SimpleTestCase

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "deploy" / "backup.sh"


class NightlyBackupFailureTests(SimpleTestCase):
    def test_a_failed_dump_is_reported_failed_and_deletes_no_older_backup(self):
        """
        The loop runs `dump || echo BACKUP FAILED`; a shell ignores `set -e` inside a function called that
        way, so a pg_dump that fails (database down) goes on: it prints 'backup written', and the retention
        step deletes backups older than BACKUP_KEEP_DAYS with no new dump made.
        """
        if shutil.which("sh") is None:
            self.skipTest("no sh")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            (tmp / "bin").mkdir(); (tmp / "backups").mkdir(); (tmp / "media").mkdir()
            (tmp / "media" / "a.txt").write_text("x")
            (tmp / "bin" / "pg_dump").write_text("#!/bin/sh\necho 'pg_dump: connection refused' >&2\nexit 1\n")
            (tmp / "bin" / "sleep").write_text(
                "#!/bin/sh\nn=$(cat \"$COUNT\" 2>/dev/null || echo 0); n=$((n+1)); echo $n > \"$COUNT\"\n"
                "[ \"$n\" -ge 2 ] && kill -9 $PPID\nexit 0\n")
            for name in ("pg_dump", "sleep"):
                (tmp / "bin" / name).chmod(0o755)
            old = tmp / "backups" / "erp_2026-09-01_0200.dump"
            old.write_text("old")
            os.utime(old, (1, 1))
            env = {**os.environ, "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}", "BACKUP_DIR": str(tmp / "backups"),
                   "MEDIA_DIR": str(tmp / "media"), "DATABASE_URL": "x", "COUNT": str(tmp / "count")}
            done = subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
            out = done.stdout + done.stderr
            self.assertIn("BACKUP FAILED", out)
            self.assertNotIn("backup written", out)
            self.assertTrue(old.exists(), "retention deleted the old backup although tonight's dump failed")
