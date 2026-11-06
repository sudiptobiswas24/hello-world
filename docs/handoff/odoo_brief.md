<!-- Agent brief used on 8 October 2026. $SP was that session's scratch folder; give agents real paths in a new session. -->
# Depth against Odoo — brief for comparison agents

SP = /tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad

## The job

We are building an ERP for a woven polypropylene sack plant in India. It
runs from tape extrusion through looms, lamination, printing and
conversion to bales of sacks, with GST, TDS, MSME and e-way bills. The
owner wants each module as deep as Odoo's. You compare one module family:
Odoo 19 Community against ours. You produce a ranked list of what is
missing that this plant would actually use.

Depth means:
- the whole lifecycle of each document: create, confirm, partial, correct,
  reverse, cancel and their states;
- the fields people fill and read on the main documents;
- the actions, wizards and reports around them;
- the settings that change behaviour;
- on screen: forms, lists, filters, smart buttons, statuses.

It does not mean every field Odoo has. A field nothing reads is worse
than no field: CLAUDE.md calls it an inert feature.

## Where things are

- Odoo 19 Community source, read-only: $SP/odoo19_src/odoo/addons/<module>/,
  in models/, views/, wizard/, report/ and data/. Enterprise modules
  (payroll, assets, quality, PLM, MPS, shop floor) are not there. Where
  your area needs them, say what Enterprise has from your own knowledge,
  marked "(Enterprise, from knowledge)".
- Ours, read-only: $SP/atree. It is a git worktree of the newest code:
  - backend in apps/<app>/;
  - office app in frontend/src/modules/<area>/;
  - screens registered in frontend/src/app/registry.ts;
  - read CLAUDE.md first for the architecture rules.
- Your output: write $SP/odoo_gaps/<your area>.md. Write nothing else,
  anywhere. Edit no code, run no tests, make no commits. Do not copy Odoo
  code into anything: Odoo is LGPL. Describe features in your own words
  and cite file:line.

## Method

1. Read Odoo's main models for your area: fields, states, action_*
   methods, constraints and onchanges. Read the form and list views of
   the main documents, the wizards and the reports.
2. Read ours for the same documents: models, serializers, views and
   screens.
3. For each capability Odoo has, decide one of:
   (a) we have it, as deep or deeper: one line, so it is not redone;
   (b) we lack it and the plant would use it: a gap;
   (c) we lack it and the plant would not use it: a "do not build" line,
       with the reason.
4. Also list our own shallow spots Odoo exposes:
   - fields our model stores that the screen never shows or lets anyone
     set;
   - actions the API has that no screen offers;
   - lists without the filters the work needs.

## Report format ($SP/odoo_gaps/<area>.md)

Start with a summary of five lines at most: how deep we are against Odoo
here, and the five gaps that matter most.

Then the gaps, ranked by value to this plant. For each:
- **id and title.**
- **Odoo:** what it does, with file:line references.
- **Ours:** what we have, with file:line, or "absent".
- **Why the plant needs it:** a concrete scenario at a sack plant, with
  numbers if it helps.
- **Value:** H, M or L.
- **Size:** S (under a day), M (one to three days) or L (more).
- **Depends on:** what must exist first.
- **Correctness traps:** reverse path, partial, posted-is-immutable, the
  ledger. Name the ones a builder must handle.

Then "We are deeper": one line each. Then "Do not build": one line each,
with the reason. Then "Screen gaps": stored but not shown, API-only
actions, missing filters.

Finish with a final message of under 300 words: the summary and the path
of your report file.

## Reading economy

Tokens are the cost here. Read with grep and line ranges (sed -n 'a,bp',
Read with offset and limit), never whole large files: apps/*/models.py
runs to 6,000 lines. Find the function by name, then read it and what it
calls. Do not re-read a file you have already read. Keep the final report
to the length asked; put detail in files, not in the message.
