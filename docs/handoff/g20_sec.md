# g20 brief: fix_sec2 — who may, and actions that answer 500

Read docs/handoff/fix_common.md first; it holds for you, with these changes:
- Base: the commit your prompt names, not ef7c0c3. Worktree:
  `git -C /home/user/hello-world worktree add --detach $SP/fix_sec2 <base>`
  (SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad)
  Link `.venv` and `frontend/node_modules` from /home/user/hello-world.
- Budget: about 110 tool calls.
- Report: $SP/reports/fix_sec2.md.
- Probes and earlier findings: docs/handoff/reports/fix_sec.md,
  review_sec2.md and review_sec2_tests/, probes/audit_perm_*.

## Ids, in order (docs/RISKS.md "Open defects")

The two-person rule (core.RoleProposal, `roles.may_give`, KeptCredential):
- O195: "nobody acts on their own login" compares logins only, so HR acts
  through a login it made. Refuse a proposal whose subject made, or keeps
  the credentials of, the proposer's login; or name the maker on the
  proposal where the confirmer sees it. Decide, and say why in the commit.
- O196: O194 in reverse: a login linked to an employee, then given a role
  the link rule would have refused. One rule read at both moments, link
  and confirmation, so neither order gets round it.
- O198: a keeper-set password lapses when any right is given in the
  admin (a permission of its own, is_staff), not only a role; no copy of
  the keeper-set hash is kept; what predates the rule is said in words.

Reads:
- O129: a warehouse held for a customer names them only to those who may
  read that customer (the rep-scope rule; take it off the "reported"
  list in audit_invariants).
- O131: a deleted leave request's history is read under the same rule as
  the request was.

Lines and their documents:
- O124: the lines in inventory/adjustments.py (603, 853),
  manufacturing/inward.py (117), manufacturing/orders.py (2692, 3240) and
  manufacturing/scrap.py (90) answer to their document through
  `answer_to_its_document()`, with its lock; take each off
  `LINES_REPORTED`.

Crashes:
- O191: an action reading `request.data.get` answers 400 in words, never
  500, when the body is a list or not JSON. Fix it once, where every
  action reads its body (a shared reader in apps.core.api, as `flag` is),
  and add an `audit_invariants` check that reports an action reading
  `request.data.get` directly, with a planted test.

## Shared rules

Yours: the two-person rule's edges, the body reader. Existing, use them:
`roles.may_give`, `core.RoleProposal`, `KeptCredential`,
`check_not_raised_by` and `authors` (apps/core/approvals.py), the rep
scope (`scoped`, `carried_by`, `rep_limit`), `answer_to_its_document()`
and `documents_it_answers_to()`, `apps.core.api.flag`,
`AuditableViewSetMixin` (never replace its writes).

Another agent (fix_cost) is working on purchasing, inventory,
manufacturing and payroll costing at the same time. O124 touches files it
may touch: change only the line save() methods named above.

Test every rule as a person in the role (apps/core/tests_roles.py),
through the API, never as a superuser.
