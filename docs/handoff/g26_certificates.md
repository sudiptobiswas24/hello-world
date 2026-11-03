# g26 brief: fix_coa — the supplier's certificate of analysis checked before release (L28)

Read docs/handoff/fix_common.md first; it binds you. Then CLAUDE.md, all of it. Then L28 in
docs/RISKS.md "Later items", apps/quality/release.py (the one release rule), apps/quality/models.py
(InspectionPlan, PlanLine, Inspection, Disposition and its decision fields), and how a goods receipt
line names its lot (apps/purchasing/models.py GoodsReceiptLine, apps/inventory lots).

## Setup
- Base: e51eef0 (g25 as pushed, then O429). Worktree:
  `git -C /home/user/hello-world worktree add --detach /home/user/work/fix_coa e51eef0`
  (never under /tmp). Link .venv and frontend/node_modules as fix_common says.
- Budget: about 150 tool calls; commit as you go. Report: /home/user/work/reports/fix_coa.md.
- You are the only agent running. Shared rules that exist; use them, never a second copy:
  - apps/quality/release.py is the only place that answers "may this lot go"; the certificate
    joins it there, not in each document that asks;
  - @serialised and lock_rows, correction_date(), in_a_transaction_it_opened, DocumentSequence;
  - the edit log (every model logged unless its class says why not); the deletable-posted check;
  - AuditableViewSetMixin (never replace perform_create/perform_update); apps.core.api.flag;
  - the role tests (apps/core/tests_roles.py) and the screen registry (frontend/src/app/registry.ts).

## Decided (do not reopen)
1. **What a certificate is.** A supplier's certificate of analysis for one received lot: the
   vendor, the supplier's certificate number and date, and one value per characteristic the
   item's inspection plan names. It is recorded by quality, posted, and immutable once posted;
   a mistake is corrected by voiding it (dated by correction_date()) and recording another.
2. **Against what.** The plan in force on the receipt's date (release.plan_for), and its lines'
   limits are copied onto the certificate's lines when it posts: the comparison is a fact of
   that day (mistake 4), not re-read from a plan edited later. Each line says within or outside.
   A value for a characteristic the plan does not name is refused; a plan characteristic left
   without a value is a missing value, and a certificate with one is not "within".
3. **Who.** The certificate's vendor must be the vendor of the receipt that brought the lot in.
   A lot not received from a vendor (made here, a customer return) takes no certificate.
   One standing (posted, not voided) certificate per lot; a second is refused until the first is
   voided.
4. **What it decides.** An inspection plan gains `certificate_required`, added at False for
   existing plans (the old meaning, CLAUDE.md migrations). Where it is set:
   - no standing certificate: the lot is not released, and release says why in words;
   - a standing certificate with any line outside or missing: not released, until quality
     accepts it through the existing decision on an inspection (a concession, with who and why,
     as Disposition already records); a passed inspection alone does not override a failed
     certificate;
   - a certificate within: the lot still needs whatever its own inspection already needed. A
     certificate never replaces the plant's own inspection in this round (skip-lot is later:
     put it in your report).
   Voiding the certificate hands the question back (derived, never stored on the lot).
5. **Screens and API.** Quality records and posts a certificate from the lot or the receipt;
   purchasing reads it. Drive it in the browser as the quality inspector and as the stores
   clerk who tries to issue the held lot. Release's refusal reaches every place that already
   asks release (issue to production, dispatch): find them through release.py's callers, do not
   add a check per document.
6. **Demo.** load_demo gains one polymer lot with a certificate within and one outside, the
   second held, so the plant shows both. Read release's answer for each on the demo.

## Every path to name and test
- forward: record, post, release asks it;
- reverse: void (and release goes back to "not released" where required); a return of the lot
  to the vendor leaves the certificate as a record;
- edit: a posted certificate refuses edits; the plan's limits edited after posting do not move a
  posted certificate's verdict;
- delete: a posted certificate refuses delete (audit_invariants' deletable-posted check);
- two at once: two people posting a certificate for one lot at once: one wins (race() on
  PostgreSQL);
- the refusals first in your scenario table: wrong vendor, a lot made here, a second standing
  certificate, a value for an unplanned characteristic, a void dated before the post.
