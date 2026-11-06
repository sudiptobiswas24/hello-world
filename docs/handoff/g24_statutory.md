# g24 brief: fix_statutory — statutory data seeded, a go-live check, TAN, the 2025 Act's names, bonus

Read docs/handoff/fix_common.md first; it binds you. Then CLAUDE.md, all of it. Then
docs/handoff/reports/odoo19_gap_analysis_oct26.md, items 4, 13, 14 and 15, and HANDOFF section 2
item 3 (what waits on the accountant).

## Setup
- Base: d76ef6f (g23b pushed). Worktree:
  `git -C /home/user/hello-world worktree add --detach /home/user/work/fix_statutory d76ef6f`
  (never under /tmp). Link .venv and frontend/node_modules as fix_common says.
- Budget: about 150 tool calls. Report: /home/user/work/reports/fix_statutory.md.
- You own apps/hr/statutory.py, the statutory seeds, go_live_check, the TDS and TCS sections' names
  and forms, TAN, the bonus rule and the minimum-wage table. fix_audit owns apps/core/history.py and
  audit.py; fix_rcm owns GST reverse charge. Do not touch their files. Shared rules that exist, use
  them: StatutoryFigure and FigureKind (dated figures, one owner: you), TdsRate.rate_on() (refuses
  with no confirmed row), PayComponentSlab with state and effective_from, setup_indian_books.

## Decided (do not reopen)
1. **The figures are data, never code, and carry their source.** One command,
   `setup_statutory`, loads a dated data file kept in the repository (one file per kind, each row
   with its effective date, state where it applies, value and source). It covers:
   - TDS rates and thresholds by section;
   - the TCS rate on scrap;
   - EPF and EPS ceilings;
   - ESI rates and ceiling;
   - West Bengal professional tax from 1 October 2026;
   - West Bengal labour welfare fund, before the December run;
   - the bonus rates and ceilings;
   - the minimum-wage table.
   Each row is loaded as **unconfirmed** unless its source says the accountant confirmed it. An
   unconfirmed figure is used by nothing that posts. The engine refuses with a sentence naming the
   figure and who must confirm it, as TdsRate.rate_on() does today. Confirming is an action for the
   Controller, recorded with who and when. Research each figure from the official notification and
   cite it in its row; where sources disagree or you cannot reach one, leave the row unconfirmed and
   list it in the report for the accountant.
2. **go_live_check** gains a line per engine: every statutory engine the plant uses (TDS, TCS, PF,
   ESI, PT, LWF, bonus, minimum wage) must have a confirmed figure covering today, or the check fails
   and names the missing ones. Test it with a planted gap.
3. **TAN** on the company: validated as four letters, five digits and a letter. It is required before
   a TDS or TCS challan or return is made, and printed on them.
4. **The Income-tax Act 2025's names** (in force from 1 April 2026): each TDS and TCS section carries
   its 2025 Act section and its 1961 Act section, and the screens, challans and returns show the 2025
   names and form numbers for periods from 1 April 2026, the 1961 ones before. That means 393 and
   394, and Forms 138, 140, 143, 144, 130 and 127 as the gap report lists them; verify each against
   the Act and the CBDT notifications and cite them. Data, not text scattered in code: a test asserts
   no screen or print still hard-codes "26Q", "27EQ" or "challan 281".
5. **Bonus:** under the Payment of Bonus Act (or the Code on Wages where it applies; research which
   is in force for this plant and cite it), the calculation ceiling is the higher of 7,000 and the
   minimum wage for the scheduled employment, and eligibility is up to 21,000 a month. Both are dated
   figures. A dated minimum-wage table is held by state, employment and skill, West Bengal's rows
   with their VDA revisions. Replace the flat base_ceiling with them, and keep the old component
   reading as it did for runs already posted (mistake 4: a posted run's figures are facts). Work the
   numbers by hand first.

## Before reporting
Run hr, accounting, sales, purchasing, gst, core, e2e and web (fastsettings, --exclude-tag migration,
browser tests after `npm run build`), and `--tag migration` for anything you migrated. Then
audit_invariants and makemigrations --check. Do not edit docs/RISKS.md.
