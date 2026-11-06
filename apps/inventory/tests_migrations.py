"""
Inventory's data migrations, each run on rows made before it (O27, O210).

A snapshot is a cache of the replay: 0030 and 0037 drop the ones folded
under a replay that has since changed, and nothing else. 0039 names the
leg each arrival already standing came from, so what is put on the goods
later is followed to where they went.
"""

import datetime
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag
from django.utils import timezone


class MigrationCase(TransactionTestCase):
    before = None
    after = None

    def setUp(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.before)
        self.apps = self.executor.loader.project_state(self.before).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def migrated(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        return executor.loader.project_state(self.after).apps

    def goods(self, sku="TAPE"):
        uom = self.apps.get_model("core", "UnitOfMeasure").objects.get_or_create(code="kg", defaults={"name": "kg"})[0]
        return self.apps.get_model("inventory", "Item").objects.create(sku=sku, name=sku, uom_id=uom.pk)

    def shelf(self, code):
        return self.apps.get_model("inventory", "Warehouse").objects.create(code=code, name=code)

    def snapshot(self, item, warehouse, method):
        return self.apps.get_model("inventory", "StockValuationSnapshot").objects.create(
            item_id=item.pk, warehouse_id=warehouse.pk if warehouse else None, method=method,
            boundary_at=timezone.now(), boundary_id=1, quantity=Decimal("10"), value=Decimal("50"), state={})


# Slow: each unwinds the later migrations and replays them.
@tag("migration")
class CompanyWideSnapshotsDroppedTests(MigrationCase):
    before = [("inventory", "0029_warehouse_held_for")]
    after = [("inventory", "0030_refold_company_wide_valuation")]

    def test_only_the_company_wide_fold_goes(self):
        item = self.goods()
        store = self.shelf("STORE")
        self.snapshot(item, None, "average")
        kept = self.snapshot(item, store, "average")
        snapshots = self.migrated().get_model("inventory", "StockValuationSnapshot")
        self.assertEqual(list(snapshots.objects.values_list("pk", "warehouse_id")), [(kept.pk, store.pk)])


@tag("migration")
class BelowZeroFifoAndSpecificSnapshotsDroppedTests(MigrationCase):
    before = [("inventory", "0036_adjustment_line_keeps_its_value")]
    after = [("inventory", "0037_refold_below_zero_fifo_and_specific")]

    def test_the_average_fold_is_kept(self):
        store = self.shelf("STORE")
        for sku, method in (("FIFO", "fifo"), ("LOT", "specific"), ("AVG", "average"), ("STD", "standard")):
            self.snapshot(self.goods(sku), store, method)
        snapshots = self.migrated().get_model("inventory", "StockValuationSnapshot")
        self.assertEqual(sorted(snapshots.objects.values_list("method", flat=True)), ["average", "standard"])


@tag("migration")
class EachArrivalNamesItsDepartureTests(MigrationCase):
    before = [("inventory", "0038_tcs")]
    after = [("inventory", "0039_moved_from")]

    def test_a_hop_a_clearance_and_a_rebatch(self):
        item = self.goods()
        bay, inspection, store = self.shelf("BAY"), self.shelf("QC"), self.shelf("STORE")
        movement = self.apps.get_model("inventory", "StockMovement")
        moment = timezone.make_aware(datetime.datetime(2026, 9, 1, 10, 0))

        def moved(warehouse, kind, quantity, reference, rate="5"):
            return movement.objects.create(
                item_id=item.pk, warehouse_id=warehouse.pk, movement_type=kind, uom_id=item.uom_id,
                quantity=Decimal(quantity), unit_cost=Decimal(rate), reference=reference, occurred_at=moment)

        moved(bay, "receipt", "10", "GRN-1")
        # A put-away's hop, with its step.
        transfer = self.apps.get_model("inventory", "StockTransfer").objects.create(
            transfer_date=datetime.date(2026, 9, 1), from_warehouse_id=bay.pk, to_warehouse_id=inspection.pk)
        line = self.apps.get_model("inventory", "StockTransferLine").objects.create(
            transfer_id=transfer.pk, item_id=item.pk, uom_id=item.uom_id, quantity=Decimal("10"))
        hop_out = moved(bay, "transfer_out", "-10", "TR-1")
        # Something else written between the two legs: the step says which.
        moved(store, "receipt", "1", "GRN-2")
        hop_in = moved(inspection, "transfer_in", "10", "TR-1")
        self.apps.get_model("inventory", "StockTransferStep").objects.create(
            line_id=line.pk, leg="in", quantity=Decimal("10"), source_id=bay.pk, destination_id=inspection.pk,
            out_movement_id=hop_out.pk, in_movement_id=hop_in.pk, occurred_at=moment)
        # A clearance out of inspection, as move_stock() wrote it: no step.
        cleared_out = moved(inspection, "transfer_out", "-10", "GRN-1")
        cleared_in = moved(store, "transfer_in", "10", "GRN-1")
        # A rebatch on one shelf: pooled, not a move between shelves.
        moved(store, "transfer_out", "-4", "RB-1")
        pooled_in = moved(store, "transfer_in", "4", "RB-1")
        # An arrival at another rate than any departure: nothing to name.
        moved(store, "transfer_out", "-2", "TR-9")
        unmatched = moved(bay, "transfer_in", "2", "TR-9", rate="6")

        after = self.migrated().get_model("inventory", "StockMovement")
        self.assertEqual(
            dict(after.objects.filter(movement_type="transfer_in").values_list("pk", "moved_from_id")),
            {hop_in.pk: hop_out.pk, cleared_in.pk: cleared_out.pk, pooled_in.pk: None, unmatched.pk: None})


@tag("migration")
class EachMovementGivenItsMoneyTests(MigrationCase):
    """
    O269: 0040 gives every movement written before it the money it moved, as the replay valued it then,
    to the paisa; where that was not a whole paisa the row's notes say what it was. Two kilos at 10.005
    and one written off: 20.01, then -10.005 read as -10.01. Under the standard (now 12) a kilo came in
    at 10 and was revalued by 2: 10.00 and 2.00. Snapshots, a cache of the old replay, go.
    """

    before = [("inventory", "0039_moved_from")]
    after = [("inventory", "0040_movement_money")]

    def move(self, item, warehouse, quantity, cost=None, adjustment=None, minute=0):
        uom = self.apps.get_model("core", "UnitOfMeasure").objects.get(code="kg")
        return self.apps.get_model("inventory", "StockMovement").objects.create(
            item_id=item.pk, warehouse_id=warehouse.pk, movement_type="adjustment", uom_id=uom.pk,
            quantity=Decimal(quantity), unit_cost=Decimal(cost) if cost is not None else None,
            value_adjustment=Decimal(adjustment) if adjustment is not None else None,
            occurred_at=timezone.now() + datetime.timedelta(minutes=minute))

    def test_each_row_gets_the_paisa_it_moved_and_says_where_it_rounded(self):
        store = self.shelf("STORE")
        tape = self.goods("TAPE")
        received = self.move(tape, store, "2", "10.005", minute=1)
        written_off = self.move(tape, store, "-1", minute=2)
        frame = self.goods("FRAME")
        frame.costing_method, frame.standard_cost = "standard", Decimal("12")
        frame.save()
        came = self.move(frame, store, "1", "11", minute=1)
        revalued = self.move(frame, store, "0", adjustment="2", minute=2)
        self.snapshot(tape, store, "average")

        apps = self.migrated()
        rows = apps.get_model("inventory", "StockMovement").objects
        self.assertEqual([rows.get(pk=row.pk).value for row in (received, written_off, came, revalued)],
                         [Decimal("20.01"), Decimal("-10.01"), Decimal("10.00"), Decimal("2.00")])
        self.assertIn("from -10.005 as replayed then", rows.get(pk=written_off.pk).notes)
        self.assertEqual(rows.get(pk=received.pk).notes, "")
        self.assertFalse(apps.get_model("inventory", "StockValuationSnapshot").objects.exists())


@tag("migration")
class EachMovementGivenWhatItsDocumentBookedTests(MigrationCase):
    """
    O329 (calc_fix_books3/o329.py): tape, average, on 1200. 2 kg in at 10.005 under GRN-1, whose entry booked
    20.01; 1 kg written off under ADJ-1, whose entry booked 10.00 at the old rounding where the replay read
    -10.005, -10.01. The row takes -10.00 off its entry and the shelf, 10.01, is the books'. A delivery DEL-1
    moving tape and frame on the same account, a row with no reference and one whose reference has no entry
    are named in the summary with why, and keep the replay's paisa. Back to 0039 the notes come off; forward
    again they are written once.
    """

    before = [("inventory", "0039_moved_from")]
    after = [("inventory", "0040_movement_money")]

    def move(self, item, warehouse, quantity, cost=None, minute=0, reference=""):
        uom = self.apps.get_model("core", "UnitOfMeasure").objects.get(code="kg")
        return self.apps.get_model("inventory", "StockMovement").objects.create(
            item_id=item.pk, warehouse_id=warehouse.pk, movement_type="adjustment", uom_id=uom.pk,
            quantity=Decimal(quantity), unit_cost=Decimal(cost) if cost is not None else None, reference=reference,
            occurred_at=timezone.now() + datetime.timedelta(minutes=minute))

    def entry(self, reference, account, amount, against):
        Entry = self.apps.get_model("accounting", "JournalEntry")
        Line = self.apps.get_model("accounting", "JournalLine")
        entry = Entry.objects.create(date=datetime.date(2026, 3, 1), reference=reference, memo=reference, posted=True)
        amount = Decimal(amount)
        Line.objects.create(entry_id=entry.pk, account_id=account.pk, debit=max(amount, Decimal("0")),
                            credit=max(-amount, Decimal("0")))
        Line.objects.create(entry_id=entry.pk, account_id=against.pk, debit=max(-amount, Decimal("0")),
                            credit=max(amount, Decimal("0")))

    def build(self):
        Account = self.apps.get_model("accounting", "Account")
        stock = Account.objects.create(code="1200", name="Inventory", account_type="asset")
        other = Account.objects.create(code="2100", name="GRNI", account_type="liability")
        store = self.shelf("STORE")
        tape, frame = self.goods("TAPE"), self.goods("FRAME")
        for item in (tape, frame):
            item.inventory_account_id = stock.pk
            item.save()
        rows = {
            "in": self.move(tape, store, "2", "10.005", minute=1, reference="GRN-1"),
            "off": self.move(tape, store, "-1", minute=2, reference="ADJ-1"),
            "shared": self.move(tape, store, "1", "4", minute=3, reference="DEL-1"),
            "bare": self.move(tape, store, "1", "4", minute=4),
            "lost": self.move(tape, store, "1", "4", minute=5, reference="ADJ-9"),
        }
        self.move(frame, store, "1", "3", minute=1, reference="DEL-1")
        self.entry("GRN-1", stock, "20.01", other)
        self.entry("ADJ-1", stock, "-10.00", other)
        self.entry("DEL-1", stock, "7.00", other)
        return rows

    def forward(self):
        import io
        from contextlib import redirect_stdout

        printed = io.StringIO()
        with redirect_stdout(printed):
            apps = self.migrated()
        return apps.get_model("inventory", "StockMovement").objects, printed.getvalue()

    def test_a_row_takes_what_its_entry_booked_and_the_rest_are_named(self):
        rows = self.build()
        Row, printed = self.forward()
        values = {name: Row.get(pk=row.pk).value for name, row in rows.items()}
        self.assertEqual(values, {"in": Decimal("20.01"), "off": Decimal("-10.00"), "shared": Decimal("4.00"),
                                  "bare": Decimal("4.00"), "lost": Decimal("4.00")})
        self.assertIn("from its entry under ADJ-1; the replay read -10.005", Row.get(pk=rows["off"].pk).notes)
        self.assertIn("TAPE in warehouse", printed)
        for why, row in (("several movements share its reference", rows["shared"]),
                         ("no reference to read its document by", rows["bare"]),
                         ("no posted entry under its reference", rows["lost"])):
            line = next((text for text in printed.splitlines() if why in text and "TAPE" in text), "")
            self.assertTrue(line.endswith(f": {row.pk}"), printed)

    def test_back_and_forward_again_writes_each_note_once(self):
        rows = self.build()
        self.forward()
        MigrationExecutor(connection).migrate(self.before)
        notes = self.apps.get_model("inventory", "StockMovement").objects.get(pk=rows["off"].pk).notes
        self.assertEqual(notes, "")
        Row, _ = self.forward()
        self.assertEqual(Row.get(pk=rows["off"].pk).notes.count("by inventory 0040"), 1)
        self.assertEqual(Row.get(pk=rows["off"].pk).value, Decimal("-10.00"))

    def test_the_summary_counts_each_drop_not_their_net(self):
        # Already so at 9e9d4d4 (tally[2]): 1 kg in at 10.005 (10.01) and out again (-10.01) drop -0.005 and
        # +0.005; the summary says 0.01 rounded away in all and 0 net, not "0 dropped".
        store = self.shelf("STORE")
        tape = self.goods("TAPE")
        self.move(tape, store, "1", "10.005", minute=1)
        self.move(tape, store, "-1", minute=2)
        _, printed = self.forward()
        self.assertIn("TAPE in warehouse", printed)
        self.assertIn("2 rows, 0.01 rounded away in all, 0 net", printed)
