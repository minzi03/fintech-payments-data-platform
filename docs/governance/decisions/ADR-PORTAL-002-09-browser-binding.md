# ADR-PORTAL-002-09: Browser-Binding Mechanism for OIDC Login Transactions

- Decision ID: `ADR-PORTAL-002-09`
- Status: **PROPOSED**
- Date: 2026-07-26
- Authority: repository governance authority
- Scope owner: Portal security implementation
- Resolves: GC-PORTAL-002-01 Blocker 3
- Production deployment authorization: **NOT GRANTED**

## 1. Context

The frozen design (section 4, "Login initiation") requires a "browser-binding hash" in the login
transaction and validation at callback (step 2: "Resolve and constant-time validate state plus
browser binding"). The frozen contracts do not define:

- what signals constitute the binding;
- what is stored in the browser versus the server;
- the hash or keyed hash algorithm;
- whether IP is included or excluded;
- the callback validation procedure;
- multi-tab and concurrent-login behavior.

GC-PORTAL-002-01 Blocker 3 identified this as an open design question requiring a new ADR.

This ADR establishes the browser-binding mechanism. The critical governance distinction is:

> **Browser binding is correlation and anti-replay evidence, not authorization.**
> Only an explicit ALLOW decision authorizes access. Browser binding proves that the
> callback originated from the same browser that initiated login — nothing more.

## 2. Threats addressed

| Threat | How binding addresses it |
|---|---|
| Authorization-code interception and replay from another browser | Binding proves callback origin matches initiation origin |
| Login CSRF (attacker initiates, victim completes) | Binding proves the completing browser is the initiating browser |
| Session fixation through callback manipulation | Binding is bound to a specific transaction, not reusable |
| Open-redirect exploitation through callback | Binding is validated before token exchange, fails closed |
| Cross-tab login collision | Each transaction has an independent binding |
| Browser restart during login flow | Binding cookie survives browser restart (persistent cookie) |

## 3. Decision drivers

- frozen design requires browser binding at callback step 2;
- binding must not grant access (frozen invariant 2: browser state never grants access);
- binding must be correlation evidence, not authorization (frozen invariant 4: only ALLOW
  authorizes);
- binding must survive SameSite=Lax top-level OIDC redirect;
- binding must fail closed when missing, mismatched, or expired;
- binding must work with the OIDC state parameter without duplicating its purpose;
- binding must not introduce session fixation risk;
- binding must support concurrent independent login attempts;
- binding must be verifiable in constant time at callback;
- binding must not expose secrets to JavaScript or browser storage;
- binding must be consistent with ADR-PORTAL-002-07 (cookie and CSRF strategy).

## 4. Options considered

### Option A — Server-issued HttpOnly binding cookie

Server generates a high-entropy opaque binding secret per login transaction. Browser receives it
only in a Secure, HttpOnly, SameSite=Lax cookie. Server stores an keyed hash or encrypted
representation bound to the transaction. Callback must present the matching cookie.

**Assessment:**

- Confidentiality: strong — HttpOnly, Secure, SameSite=Lax; JavaScript cannot read it.
- Integrity: strong — cookie is opaque, server validates against stored representation.
- Replay resistance: strong — one-use transaction binding; consumed on callback.
- CSRF resistance: strong — attacker cannot read or forge the cookie value.
- Login-swapping resistance: strong — binding is per-transaction, not per-session.
- Session fixation: no risk — binding cookie is separate from session cookie.
- Multi-tab: each login transaction creates an independent binding cookie.
- Browser storage: cookie only, no JavaScript storage.
- Server state: keyed hash or encrypted representation in login transaction.
- OIDC state interaction: binding complements state (state prevents CSRF; binding proves origin).
- PKCE interaction: independent; both are server-side secrets.
- Fail-closed: missing/mismatched cookie → reject callback.
- Operational complexity: low — standard cookie mechanism.

### Option B — Pre-authentication session binding

Login transaction is bound to an existing anonymous server-side session. Browser presents the
opaque session cookie. Callback validates transaction ownership through that session.

**Assessment:**

- Confidentiality: strong — session cookie is HttpOnly, Secure.
- Integrity: strong — server-side session is authoritative.
- Replay resistance: moderate — session may be reused across login attempts.
- CSRF resistance: moderate — session cookie is always sent; not transaction-specific.
- Login-swapping resistance: weak — session can be shared across tabs; binding is not
  transaction-specific.
- Session fixation: possible risk — pre-auth session may carry state to post-auth session.
- Multi-tab: problematic — same session shared across tabs means ambiguous transaction binding.
- Browser storage: session cookie only.
- Server state: session record with transaction reference.
- OIDC state interaction: state still required; session binding is redundant with state for
  some threats.
- PKCE interaction: independent.
- Fail-closed: missing session → reject; but session may exist without transaction binding.
- Operational complexity: low — uses existing session infrastructure.

**Weakness:** session binding is not transaction-specific, weakening login-swapping and
multi-tab isolation. This option is rejected.

### Option C — Signed or encrypted browser-binding artifact

Server issues a short-lived signed or encrypted artifact to the browser (e.g., in a cookie or
response header). Artifact contains or references the transaction binding. Callback validates
cryptographic integrity and transaction association.

**Assessment:**

- Confidentiality: depends on transport — if cookie, same as Option A; if header, requires
  frontend cooperation.
- Integrity: strong — cryptographic signature.
- Replay resistance: strong — short-lived, one-use.
- CSRF resistance: strong — attacker cannot forge the signed artifact.
- Login-swapping resistance: strong — artifact is per-transaction.
- Session fixation: no risk — artifact is separate from session.
- Multi-tab: each transaction has an independent artifact.
- Browser storage: cookie or requires frontend memory handling.
- Server state: signing key, transaction reference.
- OIDC state interaction: complementary.
- PKCE interaction: independent.
- Fail-closed: invalid signature → reject.
- Operational complexity: moderate — requires signing key management, artifact format.

**Weakness:** adds cryptographic complexity (signing key, artifact format, validation) without
meaningful security benefit over Option A. The opaque cookie provides equivalent security
properties with less complexity. This option is rejected.

### Option D — Browser-binding via OIDC state parameter alone

Rely solely on the OIDC state parameter for browser binding, without an additional binding
mechanism.

**Assessment:**

- The OIDC state parameter is already required and provides CSRF protection.
- However, state is a server-generated random value sent to the browser and returned in the
  callback. It proves that the callback is a response to a specific initiation request.
- State does NOT prove that the callback originated from the same browser that initiated
  login — it proves the callback is correlated to a specific request.
- A different browser could complete the callback if it obtains the state value (e.g., through
  a shared URL, logs, or network interception).
- State alone does not provide browser-binding as required by the frozen design.

**Weakness:** state provides request correlation but not browser-origin proof. This option
does not satisfy the frozen requirement. Rejected.

## 5. Decision

**Select Option A: Server-issued HttpOnly binding cookie.**

Browser binding is implemented as a per-login-transaction Secure, HttpOnly, SameSite=Lax cookie
containing a high-entropy opaque binding secret. The server stores an keyed hash of the binding secret
in the login transaction record. Callback validates the presented cookie against the stored keyed hash.

### 5.1 Browser-binding lifecycle

1. **Creation** — at login initiation, the server generates a cryptographically secure high-entropy cryptographically random
   binding secret.
2. **Distribution** — the binding secret is set as a Secure, HttpOnly, SameSite=Lax cookie
   with a short-lived Max-Age.
3. **Presentation** — the browser automatically includes the binding cookie in the OIDC callback
   redirect (SameSite=Lax allows top-level GET navigation).
4. **Validation** — at callback step 2, the server computes keyed hash of the presented binding
   secret and compares it against the stored keyed hash in the login transaction.
5. **Consumption** — the binding is consumed as part of the one-use transaction claim. The
   cookie is expired (deleted) after callback completion.

### 5.2 Server-generated binding value

The binding secret is:

- cryptographically secure and high-entropy;
- generated per login transaction;
- never derived from the transaction ID, state, nonce, or any predictable value;
- stored only as a keyed integrity value in the login transaction record;
- never logged, audited, or returned to the browser in any form other than the cookie.

### 5.3 What is stored in the browser

| Storage | Content | Lifetime |
|---|---|---|
| Secure, HttpOnly, SameSite=Lax cookie | High-entropy binding secret (opaque) | From login initiation until callback completion (max 5 minutes) |

The cookie value is:

- opaque — not signed, encrypted, or structured;
- inaccessible to JavaScript (HttpOnly);
- inaccessible to cross-site requests (SameSite=Lax);
- inaccessible over non-HTTPS connections (Secure in production).

### 5.4 What is stored on the server

| Storage | Content | Lifetime |
|---|---|---|
| Login transaction — browser-binding column | Cryptographic keyed hash of the binding secret, keyed with a server-side binding key | Lifetime of the login transaction (5 minutes) |

The server stores only the keyed hash, never the plaintext binding secret. The binding key is:

- separate from the token-envelope encryption key;
- separate from the PKCE verifier encryption key;
- cryptographically secure and high-entropy;
- rotated periodically without breaking existing transactions (key version tracking).

### 5.5 Cookie attributes (mandatory)

The binding cookie must use a host-only, Secure, HttpOnly, SameSite=Lax cookie with Path=/ and
no Domain attribute. The Max-Age must match the login transaction lifetime (5 minutes).

Production cookie must use the `__Host-` prefix. Local loopback may use a non-`__Host-` name
without the Secure flag. Startup rejects non-Secure binding cookie in staging/production.

The specific cookie name is an implementation detail and is not specified by this ADR.

### 5.6 Answer to required governance questions

1. **What exact server-generated value binds the browser?**
   A cryptographically secure high-entropy cryptographically random binding secret, stored only as an keyed hash on the server.

2. **What is stored in the browser?**
   The binding secret in a Secure, HttpOnly, SameSite=Lax cookie.

3. **What is stored on the server?**
   cryptographic keyed hash of the binding secret, keyed with a server-side binding key, in the login
   transaction record.

4. **Is the browser value opaque, signed, encrypted, or merely random?**
   Opaque random. Not signed or encrypted — the cookie is a bearer secret validated by
   server-side keyed hash.

5. **Can JavaScript access it?**
   No. HttpOnly prevents JavaScript access.

6. **What cookie attributes are mandatory?**
   Host-only (no Domain), Secure, HttpOnly, SameSite=Lax, Path=/. Max-Age matches login
   transaction lifetime.

7. **Is the value reusable across login attempts?**
   No. Each login transaction generates a fresh binding secret.

8. **How and when is it rotated?**
   The binding secret is not rotated — it is created once per transaction and consumed once.
   The binding key may be rotated periodically (key version in the login transaction).

9. **What is its maximum lifetime?**
   Matches the login transaction lifetime (5 minutes).

10. **What happens when the cookie is missing?**
    Callback fails closed. No session is created.

11. **What happens when it mismatches?**
    Callback fails closed. The login transaction is consumed/invalidated.
    No session is created.

12. **What happens when it is expired?**
    Same as missing — callback rejects. The login transaction is also expired (5-minute limit).

13. **Can two concurrent login transactions originate from one browser?**
    Yes. Each transaction sets a fresh binding cookie, overwriting the previous one. The
    earlier transaction's callback will fail (cookie mismatch or transaction expiry).

14. **Can one login transaction be completed from another browser?**
    No. The other browser does not have the binding cookie. Callback fails.

15. **Does browser binding need to be validated before token exchange?**
    Yes. Binding validation is callback step 2, before token exchange (step 5). Missing or
    invalid binding fails closed before any provider interaction.

16. **Does successful callback rotate from pre-auth binding to authenticated session?**
    Yes. The binding cookie is expired (deleted) after callback completion. The authenticated
    session uses the session cookie (ADR-PORTAL-002-07).

17. **Is browser binding itself an authorization signal?**
    No. Browser binding is correlation and anti-replay evidence. Only an explicit ALLOW
    decision authorizes access (frozen invariant 4).

18. **What audit evidence is recorded without leaking the binding secret?**
    The login transaction records: binding created, binding validated (or failed), binding
    failure reason (missing/mismatch/expired). The plaintext binding secret never appears in
    audit events.

### 5.7 Relationship to OIDC state

State and browser binding serve complementary purposes:

- **State** prevents CSRF by proving the callback is a response to a specific server-initiated
  request. State is validated against the server-held transaction record.
- **Browser binding** proves the callback originated from the same browser that initiated
  login. Binding is validated against the server-held keyed hash.

Both must be validated at callback. Neither is sufficient alone:
- State without binding: an attacker who obtains the state value (e.g., from logs) could
  complete the callback from a different browser.
- Binding without state: an attacker could replay a valid binding cookie across different
  transactions.

### 5.8 Relationship to PKCE

PKCE protects the authorization code from interception and replay by a different party. Browser
binding protects the callback from being completed by a different browser. They are independent
mechanisms with different threat models. Both must be validated.

### 5.9 Relationship to login transaction

Browser binding is part of the login transaction:

- created at transaction creation;
- keyed hash stored in the transaction record;
- validated at callback step 2;
- consumed when the transaction is claimed;
- does not outlive the transaction (5-minute lifetime).

### 5.10 Relationship to final authenticated session

Browser binding does not carry over to the authenticated session. After successful callback:

1. The binding cookie is expired (deleted).
2. The authenticated session uses the session cookie (ADR-PORTAL-002-07).
3. Browser binding evidence is recorded in the audit trail.

### 5.11 Multi-tab and concurrent-login behavior

The security requirement is that each login transaction has an independent binding, and that a
callback cannot succeed without the matching binding. This is satisfied by the per-transaction
binding cookie.

The UX behavior when multiple login transactions are pending in the same browser is a **product
decision**, not a security governance decision. This ADR does not prescribe whether:

- the most recent login wins;
- all concurrent logins may complete independently;
- earlier logins are cancelled or allowed to expire.

The security invariant is: no login transaction completes without its matching binding cookie.
How concurrent transactions are handled is deferred to product and implementation.

### 5.12 Rotation and expiration

- The binding secret is not rotated — it is single-use per transaction.
- The keyed hash key may be rotated periodically. The login transaction records the key version
  used at creation. At callback, the server tries the current key version and, if it fails,
  the previous key version (bounded key rotation window).
- The binding cookie expires after 5 minutes (Max-Age=300), matching the login transaction
  lifetime.

### 5.13 Audit requirements

The following events are recorded without leaking the binding secret:

| Event | Audit data |
|---|---|
| Binding created | transaction_id, timestamp |
| Binding validated | transaction_id, timestamp |
| Binding missing | transaction_id, timestamp, failure_reason=missing |
| Binding mismatch | transaction_id, timestamp, failure_reason=mismatch |
| Binding expired | transaction_id, timestamp, failure_reason=expired |

The plaintext binding secret never appears in audit events, logs, traces, or metrics.

### 5.14 Privacy considerations

- The binding cookie contains no principal, session, role, environment, or identity
  information.
- The binding cookie is a random bearer secret with no PII.
- The binding cookie is short-lived (5 minutes) and deleted after use.
- The binding cookie is not transmitted to third parties (SameSite=Lax, Secure, Path=/).

### 5.15 Forbidden behaviors

- Using the browser binding cookie as an authorization signal.
- Returning the binding secret to JavaScript or browser storage.
- Logging the binding secret in application logs.
- Including the binding secret in audit events, traces, or metrics.
- Reusing the binding secret across login transactions.
- Validating binding after token exchange (binding must be validated first).
- Creating a session from a callback with invalid binding.
- Relaxing SameSite, Secure, or HttpOnly in production.

### 5.16 Failure behaviour

| Failure | Response | Transaction state | Session state |
|---|---|---|---|
| Binding cookie missing | Fail closed — no session created | Consumed or invalidated | No session |
| Binding cookie mismatch | Fail closed — no session created | Consumed or invalidated | No session |
| Binding cookie expired | Fail closed — no session created | Expired | No session |
| Key unavailable | Fail closed — no session created | Consumed or invalidated | No session |
| Token exchange fails after binding valid | Fail closed — no session created | Consumed | No session |

In all cases: no session is created, no tokens are stored, and audit evidence is recorded.
The specific HTTP status codes and error codes are implementation details.

### 5.17 Consequences

- The frozen requirement for "browser-binding hash" is resolved as a per-transaction keyed hash
  validated via an HttpOnly cookie.
- Login transactions now include a browser-binding column (migration required via
  ADR-PORTAL-002-08).
- The binding cookie is a new cookie alongside the session cookie (ADR-PORTAL-002-07).
- Multi-tab login is supported with correct isolation semantics.
- No frozen contracts are modified by this ADR.
- No runtime implementation is authorized by this document.

### 5.18 New governance decisions introduced

This ADR introduces the following new governance decisions not previously defined in frozen
contracts:

- **HttpOnly binding cookie as the browser-binding mechanism** — the frozen design required
  binding but did not specify the mechanism.
- **Keyed hash-based server-side validation** — the frozen design did not specify how binding is
  validated.
- **Separate binding key from token-envelope and PKCE verifier keys** — the frozen design
  did not define the key hierarchy for binding.
- **Binding cookie attributes and lifetime** — the frozen design did not specify
  cookie details.

These are new governance decisions, not clarifications of existing frozen behavior.

### 5.19 Required follow-up work

1. Add browser-binding column to `oidc_login_transactions` (via ADR-PORTAL-002-08 migration).
2. Implement binding cookie set/expiry in login initiation endpoint.
3. Implement binding validation in callback endpoint (step 2).
4. Add binding failure audit events.
5. Update OpenAPI spec for binding cookie.
6. Add binding validation tests (missing, mismatch, expired, multi-tab).

### 5.20 Out of scope

- Login-intent schema and transport (Blocker 4, Action 4).
- Callback sequencing model (Blocker 6, Action 5).
- Session cookie and CSRF strategy (frozen in ADR-PORTAL-002-07).
- Specific Python or FastAPI implementation code.
- Database migration scripts.
- CI pipeline changes.

## 6. Status

**PROPOSED**

This ADR is a governance proposal. It does not authorize runtime implementation.

**Runtime implementation remains NOT AUTHORIZED by this ADR proposal.**

This ADR becomes EFFECTIVE only after:

1. Governance review and approval.
2. Merge into the protected default branch.
3. All other required governance actions from GC-PORTAL-002-01 are also approved and merged.

## 7. Review evidence

- GC-PORTAL-002-01 Blocker 3 (merged, PR #5, 8a71fc3)
- GC-PORTAL-002-02 — PKCE verifier protection (merged, PR #7, 6f77b68)
- ADR-PORTAL-002-01 — OIDC, PKCE, server-held tokens (frozen)
- ADR-PORTAL-002-07 — Cookie and CSRF strategy (frozen)
- ADR-PORTAL-002-08 — Versioned migrations (merged, PR #6, ec56bee)
- Design freeze section 4 — Login initiation (browser-binding hash required)
- Design freeze section 4 — Callback validation (step 2: state plus browser binding)
- Design freeze section 4 — oidc_login_transactions table (hashes for browser binding)
- Threat model — authorization-code interception, login CSRF, session fixation
