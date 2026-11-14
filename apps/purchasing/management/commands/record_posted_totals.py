"""
Record the posted figures on invoices and bills posted before they were
kept: each document's total, and each line's amount before tax.

New postings record them themselves. Documents posted earlier have none,
and reports work them out again on every run; this writes them once.
Safe to run again: it only fills what is empty. A posted document cannot
change, so what it works out now is what it was when it posted.

Here rather than in sales: purchasing may import sales, not the reverse.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.purchasing.models import BILL_FIGURES, Bill, BillLine
from apps.sales.models import INVOICE_FIGURES, Invoice, InvoiceLine


class Command(BaseCommand):
    help = "Fill posted_total on posted invoices and bills, and posted_net on their lines."

    def handle(self, *args, **options):
        for model, figures in ((Invoice, INVOICE_FIGURES), (Bill, BILL_FIGURES)):
            done = 0
            missing = model.objects.filter(posted=True, posted_total__isnull=True)
            for document in missing.prefetch_related(*figures).iterator(chunk_size=500):
                with transaction.atomic():
                    model.objects.filter(pk=document.pk, posted_total__isnull=True).update(
                        posted_total=document.total()
                    )
                done += 1
            self.stdout.write(f"{model.__name__}: recorded {done}.")

        for model, document in ((InvoiceLine, "invoice"), (BillLine, "bill")):
            done = 0
            missing = model.objects.filter(**{f"{document}__posted": True}, posted_net__isnull=True)
            batch = []
            for line in missing.only("pk", "quantity", "unit_price", "discount_percent").iterator(
                    chunk_size=2000):
                line.posted_net = line.net_amount()
                batch.append(line)
                if len(batch) == 1000:
                    done += model.objects.bulk_update(batch, ["posted_net"])
                    batch = []
            done += model.objects.bulk_update(batch, ["posted_net"]) if batch else 0
            self.stdout.write(f"{model.__name__}: recorded {done}.")
