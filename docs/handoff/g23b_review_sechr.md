# g23b brief: review_sechr, an adversarial, read-only review of fix_sec4 and fix_hrlocks

Read CLAUDE.md and .claude/skills/audit/SKILL.md first, all of both.

**Setup**
- Worktree: `git -C /home/user/hello-world worktree add --detach /home/user/work/review_sechr cd30628`
  (never under /tmp). Link `.venv` and `frontend/node_modules` from /home/user/hello-world.
- Never edit product code. Write probes (tests that assert what SHOULD be true) and commit only those,
  unpushed. Never `git stash`.
- The commit message ends exactly with `Gate: {GATE}`, a blank line, then:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01XQiMmxK5wWbnQzkh2yM2yK`
  Name no model anywhere else.

**What you review:** as integrated in cd30628, fix_sec4's five commits (O255, O317, O318, O348) and
fix_hrlocks's twelve (O262, O360, O270, O258, and seven of O257's shapes). Reports:
/home/user/work/reports/fix_sec4.md and fix_hrlocks.md. Briefs: docs/handoff/g23b_sec.md and
g23b_hrlocks.md. Known; do not re-find: O257's numbering, O371 (lock_rows by name), O372 to O374, O376
(being fixed beside you in fix_g23b2).

**Lead with** what lets the wrong person in, shows money to whoever may not see it, pays a person the
wrong money, or lets two clicks through at once.
1. **O255 the sealed second factor.** A key rotated, a key missing in production, a row sealed under a
   key since dropped, the migration backwards. Is the secret ever logged, served, or kept in clear in a
   backup or the history? Enrolment, sign-in, reset, recovery code, a leaver.
2. **O317 links.** Every encoding and browser quirk you know (`/\`, `/%5c`, `/\t/`, `//`, a scheme in any
   case, a leading space), on the server and on every screen that renders a stored link, Home included.
3. **O318 the number reader.** Every site it replaced and any it missed (grep): NaN, Infinity, -0,
   1e400, too many places, a sign where none is allowed. A 400 beside the field, never a 500.
4. **O348 stock money.** As each role (apps/core/tests_roles.py), list and detail, nested lines,
   reports, exports and the screens: who sees standard_cost and run costs. The Controller and
   Bookkeeper do; nobody else new does.
5. **O262 a leaver's advance**, with and without a final slip: receipt, write-off, part of each, each
   voided; the date rule. Does the receivable foot?
6. **O360**, create, edit and API, as the role.
7. **O270 and O258.** Race a payment from a former employee against the write-off on PostgreSQL. Read the
   O258 survey's output: is the account's rank right for every pair it records?
8. **The seven O257 shapes.** For each, is there really no second path taking those rows in another
   order? Name one if there is, with a race() test.

**Method**
- One probe per claim, stated as a fact, with PASS or FAIL for each check. Number the findings; for
  each give file:line, a scenario with names and rupees, and whether it blocks. A finding needs a
  probe that fails on the tip; a suspicion without one is listed as unproven.
- Races on PostgreSQL with `race()` (`postgres://erp:erp@localhost:5432/review_sechr`; drop its test
  databases first).
- Run only your probes and the modules they touch (fastsettings, --exclude-tag migration), plus
  audit_invariants and makemigrations --check; say what they print.

**Budget and report**
- About 90 tool calls. Report to /home/user/work/reports/review_sechr.md, and send 10 lines back:
  findings by severity, blockers first.
