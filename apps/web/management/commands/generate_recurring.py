"""
What recurs is made by this command, run each morning with the checks:
every recurring invoice and every recurring journal entry whose day has
come, one per period behind. Nothing recurs by itself otherwise, and the
inbox says what is waiting until it runs.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Issue the recurring invoices and take the recurring journal entries now due."

    def add_arguments(self, parser):
        parser.add_argument("--as-of", help="Run as if today were this date (YYYY-MM-DD).")

    def handle(self, *args, **options):
        from apps.accounting.recurring import generate_due_journals
        from apps.sales.models import generate_due_invoices

        as_of = options["as_of"]
        invoices = generate_due_invoices(as_of=as_of)
        entries, refused = generate_due_journals(as_of=as_of)
        for code, why in refused:
            self.stdout.write(f"{code} not taken: {why}")
        self.stdout.write(f"{len(invoices)} invoice(s) issued, {len(entries)} journal entr"
                          f"{'y' if len(entries) == 1 else 'ies'} taken"
                          f"{f', {len(refused)} schedule(s) refused' if refused else ''}.")
