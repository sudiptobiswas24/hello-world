**g49 to g52 are pushed together (TIP, DATE November 2026), all six lanes green on one tip (gated as
af87315).** Seven commits on 587ef25. g49's gate was folded into g50's, and g50's (ui, sqlite and pg-c passed) into
this one when g51 was ready, and g52 twelve minutes into it (its first tip, 3593f17, failed ist on one test that
held the rule O603 replaced, and was amended; the second, 21617aa, failed pg-c on a race in Receive by
scan, O607, fixed in a seventh commit): g50 changes scripts/gate, so every gate of it runs every app's migration tests (five
hours), and one such run for all five commits beats two. The owner asked for it faster.

g49 (one commit), O599:
- An allocation cut, raised, moved or deleted after its payment earned a settlement discount kept that discount:
  490 cut to 245 held 10.00 where 245 earns 5.00, and a deleted allocation left the document owing what its
  discount had covered. The allocation re-states the discount the way it re-states its exchange difference
  (RealisedOnItsOwnDay.restate_discount, one place for invoices and bills): the payment's standing discount on
  the document it leaves is withdrawn on the day of the change, or the discount's own day when later; still
  applied there, the payment earns again on what it applies now, by O250's running total. Moved to another
  document, it earns there only when someone takes it. A discount taken with no payment named is left alone.
- A payment holds one standing discount a document, not one ever: the unique constraint counts standing rows
  only (sales 0087, purchasing 0094), so a payment whose discount came back can earn again.
- **The model tests passed and the API test of a re-pointed allocation that was already there found a 500.** An
  allocation moved between documents locks both, and an edit over the API locks in the order locked_before_it
  lists them; with the document it leaves appended last, a move from invoice 1 to invoice 2 locked 1 after 2 and
  the sentinel refused. The two are listed in key order now, and a new API test moves one from the earlier
  invoice to the later.

g50 (one commit), O573, O601, O602:
- A key no field takes is refused beside it with a 400 naming what the record takes, not dropped: an account
  sent `hold_money` for `holds_money` was made, 201, as no bank; a follow-up sent `asigned_to` was planned for
  whoever sent it. The rule is on the shared bases (apps.core.api.ModelSerializer and the new
  apps.core.api.Serializer), nested lines included. A read-only field sent back as read (an id, a name through a
  relation) is still taken and ignored, as O565 decided.
- A key the view reads beside its serializer is named where it is read and taken there only: on the viewset,
  body_beside_its_serializer = {action: keys} (a new party's `role`, on its create; a change is refused it), or in
  the context of a serializer an action makes, body_beside (a coating's `lamination_gsm`, the record a follow-up
  is about). audit_invariants reports a key read beside a serializer and not named, a name listed for an action
  that does not read it (body_read_beside_its_serializer), and a plain Serializer written on DRF's.
- The screen check reads record screens now: each RecordScreen's create and save against its serializer, by the
  permission it offers, and a plain ViewSet's own create or change. 583 writes checked, 281 before. It found
  O602: the leave policy screen offered New and Save, and the API answers both with 405; it reads only now.
- **The suite said what the rule would have broken, and it was a test:** the plant-month test sent a `method`
  with every payment. Payment has no such field; the key had been dropped every time. It is gone.
- O601: the screen check took any body parameter named `values` for its fields' keys; a row action's is its whole
  row. It is the declared body's first parameter, and only where there are fields, which ActionForm fills with
  the fields' values and nothing else (held by CountForm.test.tsx). Planted, a row action's `(values) => values`
  is reported; before, it read as sending nothing, and clean.

g51 (three commits):
- O600: a credit or debit note undoing settlement discounts booked the whole of what it undid beside the newest
  discount, so two discounts booked to two accounts (the discount account changed between them) left one over and
  one under; and nothing kept which discount a note had undone, so a payment returned after a half note withdrew
  its discount again (the invoice owed 10.00 the other payment's discount had settled). The same for write-offs:
  the newer one undone and then recovered came off bad debt twice, and the older could not be recovered. Each
  discount row and each write-off keeps undone_amount; a note undoes newest first, each row on its own entry's
  account (settlement.undo_discounts, shared by both notes); a withdrawal or recovery takes only what is on the
  books of its own row. The rows are held in key order before they are written newest first: the lock-order
  sentinel refused the first version. sales 0088 and purchasing 0095 fill undone_amount from the notes posted
  before and name each document whose books may hold the old split.
- Redundant code, asked for by the owner: 196 unused imports and 75 unused local names gone, each application
  import checked against every module reaching it by name or dotted path; three serializer Meta settings that sat
  after a method's return (none had any effect: the fields are editable=False). Twelve local imports repeating a
  models module's re-export stay on purpose. The office application has no dead file or export.
- The gate runs the full suite twice, not three times (owner, 18 November): SQLite under Asia/Kolkata and
