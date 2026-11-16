# Agent and review reports

Each report here is a round's record: what an agent or a reviewer found, fixed or left, as the register
(docs/RISKS.md) and the handoff (docs/HANDOFF.md) cite it.

## The probe and test copies that sat beside them

Until 16 November 2026 the audit rounds' probe files and the reviewers' test copies were kept here as `.py`
files (39 of them, 9,308 lines: `probes/` and the `review_*_tests/` folders). None was ever run by a gate lane,
and they went stale as the code they called moved on. The owner chose to remove them (round g44, after the
redundant-code audit). Every one is still in git, as it was, at commit 565c724 (pushed):

    git show 565c724:docs/handoff/reports/probes/audit_pur_purchasing_tests_probe_audit.py
    git ls-tree -r --name-only 565c724 -- docs/handoff/reports | grep '\.py$'

A report that names one of them (review_stat2.md's `tests_rv2_o160.py`, review_sub.md's probe files and so on)
means that file at that commit. What a probe found that still matters is a regression test in apps/, named after
the fact it protects.
