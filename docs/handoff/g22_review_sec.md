# g22 brief: review_sec (adversarial, read-only security review of fix_sec3)

Read CLAUDE.md and .claude/skills/audit/SKILL.md first, all of both.

SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad

## Setup
- Worktree: `git -C /home/user/hello-world worktree add --detach $SP/review_sec 24fb445`.
- Link `.venv` and `frontend/node_modules` from /home/user/hello-world.
- Never edit product code. Write probes (tests that assert what SHOULD be true) and commit only them, unpushed.
- End your commit message exactly with `Gate: {GATE}`, a blank line, then:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01XQiMmxK5wWbnQzkh2yM2yK`
- Name no model anywhere else.

## What you review
fix_sec3's two commits, 1d19f72..24fb445.
- 4ee0ea3: O216 and O217, the passwords a keeper knows.
- 24fb445: L12, which covers two-factor sign-in (pyotp, segno), session time-outs and API keys.

The author's report is $SP/reports/fix_sec3.md and the brief was docs/handoff/g22_sec.md.

The office application is reached over the internet. It holds bank details, PAN, pay and the plant's money. Lead with what lets someone in who should not be, keeps someone in who should be out, or locks out someone who should be in.

1. **Two-factor.** Try to get past it, or to wear it down:
   - a code used twice: twice in a row, and two at once with `race()` on PostgreSQL (`postgres://erp:erp@localhost:5432/review_sec`; drop test_review_sec first);
   - a code from the previous or next window;
   - a recovery code used twice, or used by another login;
   - brute force, and whether the lock-out also lasts against the API and the admin login;
   - enrolment taken over by someone holding a password-only session;
   - which rights make the second factor required, and is that data in setup_roles as the brief says;
   - a login gaining such a right after it signed in;
   - whether a password-only session reaches anything the factor guards: every API endpoint for money, pay and people, the admin, the export and print endpoints, and file downloads;
   - an administrator's reset of the factor: who may, and is it in the history?
   - a leaver's factor.
2. **Session time-outs.**
   - Idle and absolute limits, on the API as well as the screen.
   - Can a background poll keep a session alive for ever?
   - The draft kept in the browser: whose is it, and could the next person at the same computer read it after sign-out?
3. **API keys.**
   - Shown once, kept hashed, revoked, expired, and each use recorded.
   - Does a key reach only its integration login's rights?
   - Can a key reach the second-factor-guarded endpoints, and should it?
   - The author says a key does not lapse when its issuer is demoted. Is the integration login's own rights the right boundary, or a hole?
   - The scale bridge's key: in logs, in the process list, in the config file's permissions?
4. **O216 and O217.**
   - The author warns that a worker HR made, who changed their own password, is locked out when HR leaves or is demoted. Prove it or refute it. If true, it blocks: a person's own password must never lapse with someone else.
   - Find what tells "set by the maker while signed in as the login" apart from "set by the person themselves". Is it sound?
   - A reset link mailed before its address stopped being trusted is refused. Are other cases refused wrongly?
5. **Every screen** as the roles, in the browser (PLAYWRIGHT_CHROMIUM_EXECUTABLE=/opt/pw-browsers/chromium, `npm run build` first): enrolment, sign-in with a code, a wrong code, the time-out warning, the draft offered back.
6. **The migrations** core 0036 and 0037: run `--tag migration` for core, and test any data step with MigrationExecutor.

## Method
- One probe per claim, stated as a fact about who can do what, with PASS or FAIL for each.
- Number the findings. For each, give file:line, a scenario with the logins involved, and whether it blocks.
- A finding needs a probe that fails on the tip. A suspicion without one is listed as unproven.
- Run audit_invariants and makemigrations --check, and say what they print.

## Budget and report
- About 90 tool calls. Run long jobs in the foreground with a long timeout; restarts are frequent here.
- Report to $SP/reports/review_sec.md.
- Send 10 lines back: findings by severity, blockers first.
