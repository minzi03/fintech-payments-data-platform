# GC-PORTAL-002-02 — PKCE Verifier Protection Clarification

- Decision ID: `GC-PORTAL-002-02`
- Status: **PROPOSED**
- Date: 2026-07-26
- Authority: repository governance authority
- Scope owner: Portal security implementation
- Resolves: GC-PORTAL-002-01 Blocker 2
- Production deployment authorization: **NOT GRANTED**

## 1. Context

GC-PORTAL-002-01 Blocker 2 identified that the frozen design (section 4, "Login initiation")
requires the PKCE verifier to be "envelope-encrypted in the transaction store" but does not
explicitly specify:

- whether the PKCE verifier reuses the token-envelope abstraction (AES-256-GCM with
  KMS-wrapped data keys);
- the key hierarchy for verifier-specific encryption;
- whether local/test ephemeral key behavior is acceptable.

The frozen design (section 4, "Token handling") specifies AES-256-GCM with per-record data keys
wrapped by a production KMS/secret-manager key for provider token envelopes. This clarification
determines whether the PKCE verifier follows the same pattern.

The current non-conformant implementation stores the PKCE verifier in plaintext. This
clarification does not authorize that behavior — it defines what the correct behavior must be.

## 2. Governance decision

The PKCE verifier is a short-lived cryptographic secret that must remain confidential between
server creation and code exchange. It is not a token, but it has comparable sensitivity because
possession of the verifier allows token exchange for the corresponding authorization code.

### 2.1 PKCE verifier lifecycle

The PKCE verifier has the following lifecycle:

1. **Creation** — generated as an RFC 7636–compliant cryptographically secure PKCE verifier (S256 method).
2. **Storage** — immediately encrypted at rest in the login transaction record.
3. **Use** — decrypted in memory only during the token exchange step of callback validation.
4. **Destruction** — the encrypted record is marked consumed and the plaintext is eligible for
   secure wipe from process memory.

The verifier must never be persisted in plaintext at any point in its lifecycle.

### 2.2 Encryption requirement

The PKCE verifier must be encrypted at rest using the same envelope encryption abstraction
used for provider token envelopes:

- per-verifier AES-256-GCM data key;
- data key wrapped by the production KMS or secret-manager key;
- authenticated associated data (AAD) binds the data key to the specific login transaction;
- the encrypted form includes the ciphertext, nonce (IV), and wrapped data key.

This is NOT a recommendation — it is a clarification of the existing frozen requirement.

### 2.3 Key hierarchy

The key hierarchy for PKCE verifier encryption is identical to the token-envelope key hierarchy:

```text
KMS master key (production)
  └── per-record data key (AES-256-GCM)
        └── verifier ciphertext + nonce + wrapped data key
```

In local/test environments, an explicit ephemeral test key may be used in place of the KMS
master key. This is a **new governance decision** — the frozen design does not address
local/test key behavior. The ephemeral key must:

- be 256 bits of cryptographic randomness;
- not be derived from configuration values, passwords, or predictable sources;
- be documented as a test-only mechanism.

### 2.4 What is REQUIRED

- PKCE verifier is encrypted at rest immediately upon creation.
- Encryption uses AES-256-GCM with a per-record data key.
- The data key is wrapped by the production KMS key (or ephemeral test key in local/test).
- Authenticated associated data (AAD) includes the transaction identifier.
- The plaintext verifier is never written to logs, audit events, or error responses.
- The plaintext verifier is never returned to the browser or any external system.
- The encrypted verifier is stored in the `oidc_login_transactions` table, column
  `pkce_verifier_encrypted` (or equivalent encrypted column).
- Decryption occurs only during the token exchange step of callback validation.
- After the transaction is consumed, the encrypted verifier record is retained for the
  transaction's audit lifecycle but is never decrypted again.

### 2.5 What is FORBIDDEN

- Storing the PKCE verifier in plaintext at any point in its lifecycle.
- Returning the PKCE verifier to the browser, JavaScript, or any external system.
- Logging the PKCE verifier (plaintext or encrypted) in application logs.
- Including the PKCE verifier in audit events, traces, or metrics.
- Using a static or shared encryption key for multiple verifiers.
- Deriving the verifier encryption key from the verifier itself.
- Decrypting the verifier after the transaction is consumed.
- Using the PKCE verifier for any purpose other than OIDC code exchange.

### 2.6 What is OUT OF SCOPE

This clarification does NOT address:

- the login-intent schema or transport (Blocker 4);
- browser-binding construction (Blocker 3);
- callback sequencing model (Blocker 6);
- the `oidc_login_transactions` table DDL (deferred to ADR-PORTAL-002-08 migration);
- the specific Python library or function used for AES-256-GCM encryption;
- the specific KMS integration or secret-manager configuration.

### 2.7 Relationship to login transaction lifecycle

The PKCE verifier is bound to the login transaction:

- created when the login transaction is created;
- encrypted when the login transaction is persisted;
- decrypted only when the transaction is claimed during callback;
- consumed (marked used) after successful code exchange;
- retained in encrypted form for the transaction's audit lifecycle;
- deleted when the transaction record is purged.

The verifier does not outlive the login transaction. The login transaction has a five-minute
maximum lifetime (frozen in design freeze section 4). The verifier is therefore short-lived
by construction.

### 2.8 Memory handling expectations

- The plaintext verifier should be held in process memory for the minimum duration necessary.
- After code exchange, the plaintext verifier should be eligible for secure wipe from process
  memory.
- The implementation should avoid unnecessary copies of the plaintext verifier.
- The plaintext verifier must not be serialized to any persistence layer in unencrypted form.

These are implementation guidance, not governance requirements. The governance requirement is
that the verifier is never persisted in plaintext (section 2.4).

### 2.9 Replay considerations

- The PKCE verifier is single-use by construction (bound to one login transaction).
- A consumed transaction cannot be reused (frozen: one-use, five-minute).
- Replay of the verifier is prevented by the transaction consumption mechanism.
- The verifier encryption does not need to provide replay protection — that is the
  transaction state machine's responsibility.

### 2.10 Audit expectations

- The creation of the encrypted verifier is recorded as part of the login transaction
  lifecycle.
- The decryption and use of the verifier during callback is recorded as part of the
  callback audit evidence.
- The plaintext verifier itself must never appear in audit events.
- The encrypted verifier may appear in audit events only as a reference to the encrypted
  record, not as decrypted content.

### 2.11 Failure behaviour

- If verifier encryption fails at transaction creation, the transaction must not be created.
  Login fails closed.
- If verifier decryption fails at callback, the callback fails with OIDC_CALLBACK_INVALID.
  No session is created.
- If the KMS key is unavailable, verifier encryption fails and login cannot proceed.
  This is the correct fail-closed behaviour.

## 3. Consequences

- The frozen requirement "envelope-encrypted in the transaction store" is clarified to mean
  AES-256-GCM with per-record data keys, identical to the token-envelope abstraction.
- The non-conformant plaintext storage in commit `a8d3842` remains non-conformant and must
  be remediated.
- No new frozen contracts are introduced by this clarification.
- No runtime implementation is authorized by this document.

## 4. Status

**PROPOSED**

This clarification is a governance document. It does not authorize runtime implementation.

**Runtime implementation remains NOT AUTHORIZED by this clarification.**

## 5. Review evidence

- GC-PORTAL-002-01 Blocker 2 (merged, PR #5, 8a71fc3)
- Design freeze section 4 — Login initiation (verifier is envelope-encrypted)
- Design freeze section 4 — Token handling (AES-256-GCM, KMS-wrapped data keys)
- ADR-PORTAL-002-01 (OIDC, PKCE, server-held tokens — frozen)
- ADR-PORTAL-002-08 (versioned migrations — merged, PR #6, ec56bee)
- Threat model (tokens never logged, audited, or exposed)
