# g22 brief: fix_sec — sign-in security (L12, O216, O217)

Read docs/handoff/fix_common.md first; it binds you. Then CLAUDE.md, all of it.

**Setup**
- Base: the commit your prompt names.
- Worktree: `git -C /home/user/hello-world worktree add --detach $SP/fix_sec3 <base>`
  (SP=/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad)
- Budget: about 130 tool calls.
- Report: $SP/reports/fix_sec3.md.

**Why.** The books hold bank details, PAN, pay and the plant's money, and the office application is reached over the internet.

## What to build

### 1. Two-factor sign-in (TOTP, RFC 6238)
- It is needed for every login that holds a right to money, pay or people's details:
  - the Controller;
  - the Payroll Officer;
  - HR;
  - anyone who may post a payment, see pay details or manage logins.
- It is optional for other logins. Which rights make it required is data in setup_roles, not a list in code.
- **Enrolment:** show a secret as a QR, confirm it with one code, and issue ten one-time recovery codes. Keep the codes hashed and show them once.
- **Sign-in:** the password, then the code. Count failures, and lock the login for a while after five.
- **Reverse paths:**
  - a lost phone: an administrator resets the second factor, and the login re-enrols at the next sign-in, recorded in the history;
  - a recovery code is used once;
  - a login that leaves loses its factor with the rest.
- **The API:** a session made by password alone does not reach what the second factor guards.
- **Tests:** test it as the roles. Add a browser test: a Controller enrols, signs out, signs in with a code, and is refused with a wrong one.
- Use a maintained library pinned by version (pyotp, or django-otp). Say which and why in the commit, and add it to requirements.

### 2. Session time-outs
- The session ends after an idle time that is a setting, defaulting to 30 minutes, and in any case at an absolute limit (12 hours).
- The office application tells the person before it ends, and on the next action takes them to sign in again without losing what they typed. A draft is kept in the browser and offered back.
- Browser test with a short setting.

### 3. API keys for integrations
These are for the scale bridge (deploy/, see O225), a GST service provider later, and the WhatsApp channel later.
- A key belongs to a named integration login with only the rights it needs.
- It is shown once and kept hashed.
- It can be revoked, and expires.
- Each use is recorded.
- The scale bridge uses one, if O225's program is in your base. Otherwise say how it will.

### 4. O216 and O217
Read their rows. Both are about which password a keeper knows. Fix them at the root, in the kept-credential rules in apps/core/roles.py, keeping g21's lock order (see the g21 integration commit "a login's kept credentials lapse in key order").

## Shared rules
- **Yours:** the second factor, the session rules, API keys.
- **Existing; use them:**
  - roles and setup_roles;
  - KeptCredential;
  - `AuditableViewSetMixin`;
  - the history mixin;
  - `src/api/client.ts`, which turns a failure into an ApiError;
  - `src/app/registry.ts`.
- Run apps.core, apps.hr, apps.web (whole) and the browser tests.
