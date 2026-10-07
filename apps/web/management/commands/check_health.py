"""
The health checks from the command line: for a cron line whose failure
somebody is paged on, and for the morning after an update. Prints every
probe's answer and exits 1 when anything is wrong.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.web.health import integrity, server

MARK = {True: "ok  ", False: "FAIL", None: "??  "}


class Command(BaseCommand):
    help = "Check the books agree with themselves and the server is well. Reads; changes nothing."

    def handle(self, *args, **options):
        wrong = 0
        for finding in integrity(fresh=True)["findings"]:
            self.stdout.write(f"{MARK[finding['ok']]}  {finding['label']}: {finding['detail']}")
            for row in finding["rows"]:
                self.stdout.write(f"        {row['label']}")
            wrong += finding["ok"] is False
        state = server()
        self.stdout.write(f"{MARK[state['database'] == 'ok']}  Database: {state['database']}")
        if state["migrations_pending"] is not None:
            self.stdout.write(f"{MARK[not state['migrations_pending']]}  Migrations pending: {state['migrations_pending']}")
        self.stdout.write(f"{MARK[not state['disk_low']]}  Disk free: {state['disk_free_mb']} MB")
        kept = state["backups"]
        if kept["configured"]:
            self.stdout.write(f"{MARK[not kept['stale']]}  Newest backup: {kept['newest'] or 'none'}"
                              + (f", {kept['age_hours']} hours old" if kept["newest"] else "")
                              + f" ({kept['directory']})")
        else:
            self.stdout.write("??    Backups: BACKUP_DIR is not set, so their age is not checked")
        wrong += len(state["problems"])
        if wrong:
            raise CommandError(f"{wrong} check(s) failed.")
        self.stdout.write("Everything agrees.")
