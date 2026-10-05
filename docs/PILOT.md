# The pilot: a fortnight before anything is switched off

Every flow in this system has been driven in a browser by tests, as each
role. None of that says whether the people at Deccan Polysacks can do
their day's work in it. The pilot finds that out while the old system
still carries the business, so a problem costs a day, not a month-end.

## Before it starts

1. Install on the server (RUNBOOK.md) and restore nothing yet.
2. Import a **copy** of the old system's records as they stand on the
   pilot's first day (docs/IMPORT.md), employees and logins included.
3. `python manage.py go_live_check` until it reports no failure, and
   read its warnings aloud to whoever owns each.
4. Pick the people: two or three per desk, the ones who will be asked
   when something goes wrong later.

| Desk | Role to give them | Their flows to run every day |
|---|---|---|
| Sales | Sales Rep, AR Manager | order → delivery → invoice → money received; a credit note; aging |
| Dispatch | Warehouse Staff | ship an order, choosing batches; a customer return |
| Purchasing | Purchasing Clerk | purchase order → confirm; close a short line |
| Stores | Warehouse Staff | goods in with batch numbers; stock on hand and its movements |
| Accounts | AP Manager, Bookkeeper | bill from an order → payment; a journal; trial balance |
| Production | Production Planner, Supervisor | plan → firm → release; the machine schedule |
| Floor | Station | a shift's entries at one loom and one extruder |
| GST | GST Officer | the month's GSTR-1 and 3B, compared with the old system's |
| HR | Payroll Officer, Controller | a pay run worked out, posted, and its statutory dues |

## Every day of the pilot

- Each person enters **the same day's real work** in both systems.
- At the end of the day, one person compares three numbers between the
  old system and this one, and writes them in a shared sheet:
  receivables owed, payables owing, and stock value. A difference that
  is not explained by something the old system does differently is a
  defect: write down the document that caused it.
- Anything that took longer than it should, was refused for a reason
  nobody understood, or could not be done at all goes in the same sheet
  with who, what screen, and what they were trying to do. A screenshot
  is better than a description.

## At the end of the fortnight

The pilot passes when, for the last five working days:

- the three numbers agree with the old system, or every difference is
  explained;
- each desk has done every flow in the table at least once without
  help;
- the month's GSTR-1 and 3B compiled here agree with what the old
  system would have filed;
- a backup was restored into a scratch database and read (RUNBOOK.md,
  "Proving a backup restores").

Then choose the go-live date: a month's first day, so the old system
closes a whole month. On that morning, import the opening position as
it stood at the old system's close, run `go_live_check`, and switch.

## What the pilot cannot tell you

Speed with years of data (the pilot has a fortnight's), and what
happens at a year-end close. Both were measured on generated data and
are in docs/RISKS.md.
