# ADR-PORTAL-002-10: Authoritative OIDC Callback Sequencing

- Decision ID: `ADR-PORTAL-002-10`
- Status: **PROPOSED**
- Decision: **MODEL C — CALLBACK DELEGATES TO AN ORCHESTRATION PORT**
- Resolves: `GC-PORTAL-002-01` Blocker 6, Action 5
- Decision date: 2026-07-27
- Production deployment authorization: **NOT GRANTED**
- Runtime implementation: **NOT AUTHORIZED**

## 1. Context and evidence

The accepted PR-PORTAL-002 security design freezes a callback validation order for
`GET /v1/auth/callback`. That order validates state and browser binding, claims a one-use login
transaction, exchanges the authorization code, validates identity, resolves the Portal
principal and entitlements, and creates a rotated server-side session with audit evidence.

The same frozen design requires:

- browser code never receives OIDC tokens;
- browser state never grants access;
- FastAPI remains the only authentication and authorization enforcement point;
- only `ALLOW` authorizes and every other policy outcome fails closed;
- session identifiers are opaque, server-side, rotated, and terminal after revocation or
  replacement;
- absolute session lifetime is never extended;
- authentication state changes and append-only audit evidence commit atomically; and
- production rejects development identity.

`GC-PORTAL-002-01` identified that the frozen callback contract includes session creation,
successful audit evidence, and login-transaction consumption, while the implementation slicing
then under review did not own all three. It therefore left one governance action:

> Decision on callback sequencing model:
>
> - Model A: Callback completed in one integrated slice
> - Model B: Callback route exists but returns safe not-ready until complete
> - Model C: Callback delegates to an orchestration port

Those three lines are the complete repository definitions of the models. The repository does not
previously define their transaction boundaries, external-call boundaries, retry semantics, or
crash behavior. This is an evidence gap, not permission to infer hidden requirements. Sections 3
through 18 introduce the necessary detailed definitions and consistency rules as **new governance
decisions**.

The checked-in current OpenAPI artifact contains the PR-PORTAL-001 foundation surface and does
not yet expose the frozen authentication endpoints. The endpoint evidence for this decision is
therefore the accepted PR-PORTAL-002 design contract. This ADR does not modify OpenAPI,
generated contracts, or runtime behavior.

## 2. Exact problem resolved

This ADR resolves `GC-PORTAL-002-01` Blocker 6: the callback completion boundary.

The unresolved question is not merely where a route is placed. It is which authority owns the
complete callback and when the following effects may become durable or browser-observable:

- validation of state and browser binding;
- lookup, expiry validation, exclusive claim, and terminal consumption of the login transaction;
- authorization-code exchange and ID-token validation;
- principal, entitlement, assurance, tenant, and policy resolution;
- creation or rotation of the server-side session;
- storage or disposal of provider token material;
- append-only success or failure evidence; and
- response of successful callback completion.

Without one authoritative sequence, a callback could consume its transaction before it can
create an auditable session, persist a session without success evidence, claim success before the
session is durable, or repeat an external code exchange after an uncertain failure.

This ADR defines one owner, separates external IdP work from local database atomicity, and makes
session persistence, successful login-transaction consumption, and successful audit evidence one
indivisible local commit.

## 3. Model A, Model B, and Model C definitions

The model labels in this section apply only to the callback models named by
`GC-PORTAL-002-01`. They are unrelated to option labels used by other Portal ADRs.

### 3.1 Model A — callback completed in one integrated slice

Repository wording: **"Callback completed in one integrated slice."**

New governance-level definition for comparison:

- The callback route and all required validation, exchange, identity, authorization, session,
  consumption, and audit capabilities are delivered as one implementation slice.
- The route cannot be exposed as successful until the whole slice is complete.
- Integration into one slice does not make the external IdP exchange part of a local database
  transaction.
- The model expresses a delivery boundary, but by itself does not identify a stable owner for the
  end-to-end use case or distinguish claim, external work, and atomic finalization.

### 3.2 Model B — safe not-ready route until complete

Repository wording: **"Callback route exists but returns safe not-ready until complete."**

New governance-level definition for comparison:

- The route may be deployed before callback completion exists.
- Until every required capability and consistency control is available, every callback returns a
  sanitized unavailable/not-ready outcome.
- While not ready, the route does not exchange a code, create or rotate a session, persist provider
  tokens, claim success, or return an authenticated result.
- The model is a safe transitional exposure rule. It does not, by itself, define the sequencing
  or transaction ownership of the eventual completed callback.

### 3.3 Model C — callback delegates to an orchestration port

Repository wording: **"Callback delegates to an orchestration port."**

New governance-level definition:

- The route is a thin protocol adapter. It parses and bounds transport input, then delegates the
  complete callback use case to one authoritative orchestration boundary.
- The orchestration boundary owns validation, the one-use claim, the external token exchange,
  token and identity validation, authorization, atomic session finalization, login-transaction
  consumption, and audit outcome.
- The route has no alternative path that can create a session, mark callback success, or consume
  a transaction as successful.
- The orchestration port is a logical security contract. This ADR does not freeze a programming
  language interface, function, class, framework dependency, or module layout.

## 4. Comparative analysis

| Property | Model A | Model B | Model C |
| --- | --- | --- | --- |
| Fail-closed behavior | Strong if the integrated slice is withheld until complete; the model does not itself specify internal failure ownership. | Strong while not ready because no authentication work occurs; final behavior remains undefined without another boundary. | Strong throughout because the orchestration result is the only route to success and only a committed `ALLOW` result may succeed. |
| Replay resistance | Can satisfy one-use claim rules, but integrated delivery alone does not separate claim from final consumption. | No exchange or session exists while not ready; replay behavior after readiness is unspecified. | Explicit exclusive claim prevents a second exchange; successful consumption or a terminal failure disposition prevents later reuse. |
| Consumption too early or too late | Possible unless the integrated implementation adds a separate atomicity rule. | No consumption while safely not ready, but eventual timing remains unspecified. | A claim fences concurrency before exchange; successful consumption occurs only in the atomic final commit. A failed or abandoned claim receives a terminal failure disposition and is never reopened. |
| Session without durable audit | Preventable, but not guaranteed merely by slice co-location. | Impossible while not ready; unspecified after readiness. | Forbidden: session persistence and the required success evidence share one commit. |
| Audit claiming success before session persistence | Preventable, but the model does not define commit order. | Impossible while not ready; unspecified after readiness. | Forbidden: success evidence cannot commit independently of the session and successful transaction consumption. |
| Partial authentication state after authorization failure | Depends on internal discipline within a large integrated slice. | None while not ready. | No principal/session/token authority is finalized. The claimed login transaction is terminally consumed as a failed callback with failure evidence. |
| Token exposure and retention | Server-only handling can conform, but a broad integrated slice increases the number of participating components. | No token exchange while not ready. | Token material is confined to the orchestration boundary, retained in memory only as needed, and persisted encrypted only in the successful final transaction when required. |
| Database transaction feasibility | Feasible only if the implementation avoids treating the network exchange as transactional; the model does not make that separation explicit. | No database finalization while not ready; eventual feasibility remains unresolved. | Feasible: one short claim transaction, the external exchange without an open database transaction, then one atomic final transaction. |
| External IdP call boundary | Part of the integrated use case but cannot share local ACID guarantees. | No call while not ready. | Explicitly outside every local database transaction and between claim and finalization. |
| Retry behavior | Requires additional idempotency and commit-unknown rules. | User may retry only after readiness, normally through a new login. | Same callback is never blindly replayed; uncertain local commit is resolved by authoritative re-read, while uncertain exchange requires a new login. |
| Duplicate callback behavior | Safe only if the integrated slice serializes correctly. | Safely rejected while not ready. | Active claim, consumed transaction, invalidated transaction, and expired transaction all prevent a second exchange or session. |
| Crash consistency | Requires extra rules around the external exchange and local commit. | No partial authentication work while not ready. | Crash points are explicit: claims remain fenced; pre-commit crashes create no session; post-commit crashes leave session, consumption, and audit mutually consistent. |
| Operational complexity | Lowest number of delivery units but a large coupled slice and ambiguous recovery seams. | Adds a transitional route and readiness state without solving final orchestration. | Adds a deliberate orchestration boundary and claim recovery, but provides the clearest testing, observability, and incident boundary. |

All three models can be implemented to fail closed. Only Model C directly expresses the authority
and boundaries needed to keep external network work outside local atomicity while preserving one
auditable completion point.

## 5. Selected model and rationale

**Model C is selected. Models A and B are rejected.**

Model C is selected because it:

1. gives the complete callback exactly one authoritative owner;
2. prevents the route, identity mapper, policy evaluator, or session repository from independently
   declaring success;
3. permits a short durable claim before an external call without treating that claim as successful
   consumption;
4. keeps the IdP exchange outside the local database transaction;
5. makes session creation or rotation, successful login-transaction consumption, and successful
   audit evidence one atomic commit;
6. gives duplicate callbacks and crashes one fail-closed recovery contract; and
7. remains an implementation-neutral governance boundary.

Model A is not selected because co-delivery alone does not define the required consistency seams.
Model B is not selected because safe not-ready behavior is useful as a deployment guard but is not
a final callback sequencing model. A deployment may still fail readiness when dependencies are
unsafe, but that behavior does not change the selected model.

## 6. Normative callback sequence

The following sequence is authoritative. "Must" and "must not" are normative.

### 6.1 Before the claim transaction

1. The callback adapter must parse a bounded provider response and reject malformed or duplicate
   parameters.
2. It must obtain the presented state and the browser-binding cookie only as untrusted correlation
   inputs. Neither is authentication or authorization.
3. It must resolve server-owned provider and callback configuration. A browser-supplied issuer,
   token endpoint, redirect target, tenant, role, entitlement, or environment is never authority.
4. It must not exchange the authorization code, decrypt the PKCE verifier, create a principal,
   evaluate the callback as successful, or create/rotate a session at this stage.

These operations may occur without a database transaction because they do not create
authoritative authentication state.

### 6.2 Short claim transaction

5. The orchestration boundary must begin a short local transaction and resolve the presented state
   to exactly one authoritative login-transaction record using the frozen protected-comparison
   contract.
6. Against that same authoritative record, it must validate:
   - state equality;
   - browser-binding equality;
   - record existence;
   - unexpired lifetime;
   - an eligible, unconsumed, non-terminal state;
   - provider and exact callback/redirect binding; and
   - any required transaction version or concurrency precondition.
7. It must acquire an exclusive one-use claim that prevents any other callback from exchanging a
   code for that login transaction.
8. The claim state change and its required safe audit evidence must commit atomically. If the audit
   authority or transaction store is unavailable, the claim must not commit and no exchange may
   occur.
9. The transaction must commit before any external IdP call begins. No database lock or local
   transaction may remain open across that call.

State is considered validated only after the authoritative record has passed the checks in step 6
and the exclusive claim commits. Merely parsing, decoding, hashing, or finding a candidate record
does not validate state.

Browser binding is considered validated only after the presented binding has passed the frozen
constant-time protected comparison against the same claimed, unexpired login transaction. Cookie
presence or possession alone does not validate the binding and never grants access.

### 6.3 External exchange and local evaluation

10. Only after the claim transaction commits may the orchestration boundary decrypt the PKCE
    verifier just in time and exchange the authorization code with the configured IdP.
11. The authorization-code exchange is an external network call. It cannot participate in, and
    must not be represented as participating in, the local database transaction.
12. After a successful exchange, the orchestration boundary must validate the ID token and all
    frozen issuer, signature, audience, authorized-party, nonce, token-type, time, size, and
    authentication-context requirements.
13. It must map the validated external identity to the stable Portal principal, resolve only
    allowlisted roles and environment entitlements, resolve the server-owned tenant and assurance,
    and reject disabled, ambiguous, stale, or invalid identity state.
14. It must evaluate the callback session-creation policy using the validated identity and current
    authoritative policy and capability revisions. Only an explicit `ALLOW` permits finalization.
    `DENY`, `NOT_APPLICABLE`, `INDETERMINATE`, `ERROR`, an unknown result, or an unavailable
    policy or required capability authority must not create or rotate a session.
15. Resolution and evaluation may perform read-only work before finalization. Any principal,
    entitlement snapshot, token envelope, session, revocation, or other authoritative mutation
    must wait for the final transaction.

A principal with no mapped role may receive only the minimal session permitted by the frozen
contract when the callback policy explicitly returns `ALLOW`; that session grants no protected
capability by itself. Browser state and navigation remain non-authoritative.

### 6.4 Atomic final transaction

16. After and only after all validation and an explicit `ALLOW`, the orchestration boundary must
    begin the final local database transaction.
17. It must re-read and lock the claimed login transaction and confirm that the claim is still
    owned by this callback attempt, remains eligible for finalization, and has not expired,
    consumed, invalidated, or been superseded.
18. It must revalidate or bind the principal, entitlement, tenant, assurance, policy, capability,
    and security revisions required by the frozen contracts. A conflicting authoritative change
    must fail closed.
19. Within this one transaction it must:
    - persist any authoritative principal or identity snapshot required for the session;
    - persist provider token material only when required by the frozen lifecycle and only in its
      encrypted server-side envelope;
    - create a new opaque server-side session;
    - rotate, replace, revoke, or terminalize any session identifier or predecessor state required
      by the frozen session-fixation and concurrency contracts;
    - enforce the fixed absolute-lifetime boundary and all session limits;
    - mark the login transaction successfully consumed and permanently non-reusable;
    - append the corresponding successful authentication/session audit evidence; and
    - append the durable archive-outbox evidence required by the frozen audit contract.
20. Every effect in step 19 must commit or roll back together. The internal order of database
    statements is not governance authority; the single commit is the observable completion point.
21. Only after a confirmed successful commit may the callback adapter issue the new opaque session
    cookie, expire the browser-binding cookie, and return or redirect a successful response to the
    stored normalized local path.

The process must not return success while the final commit is failed, unknown, or still in
progress.

## 7. Atomic transaction boundary

The callback has two local transactional boundaries and one non-transactional external interval:

```text
bounded transport checks
        |
        v
[claim transaction]
  validate authoritative state + browser binding + expiry
  acquire exclusive one-use claim
  append required claim evidence
        |
        v
commit claim; close local transaction
        |
        v
[external IdP interval — no open local database transaction]
  PKCE code exchange
  ID-token validation
        |
        v
[local evaluation — no authoritative mutation]
  principal/entitlement resolution
  policy evaluation
        |
        v
[final local transaction]
  revalidate claimed authority and revisions
  persist/rotate session and permitted token envelope
  consume login transaction successfully
  append success audit + archive outbox
        |
        v
commit all effects together
        |
        v
issue opaque cookie and successful redirect
```

The first transaction establishes exclusive processing authority; it does not authenticate the
browser and is not successful login-transaction consumption. Post-exchange resolution may use
short read-only database operations, but no such operation may persist authentication authority
or remain open across the external IdP call.

The final transaction is the sole authoritative callback completion boundary. Successful session
persistence, authoritative login-transaction consumption, and corresponding successful audit
evidence must never become observably inconsistent.

Failure finalization uses a separate short local transaction as described in Sections 13 and 15.
It may atomically consume, invalidate, or expire the claimed login transaction under Section 9
and append failure evidence, but it must never create a session.

## 8. External token-exchange boundary

The authorization-code exchange:

- may occur only after state, browser binding, record existence, expiry, eligibility, provider
  binding, and exclusive claim have been validated and committed;
- must use only server-owned provider configuration and the just-in-time decrypted verifier;
- must run without an open local database transaction;
- must have bounded failure behavior under the frozen outbound OIDC policy;
- must not be retried blindly after a timeout or ambiguous provider response;
- must not cause token material to enter browser state, URLs produced by the Portal, logs, traces,
  metrics, errors, or audit evidence; and
- does not prove Portal authorization or create a Portal session.

If exchange succeeds but any later token, identity, entitlement, assurance, tenant, policy, or
finalization check fails, all transient token material must be discarded, no token envelope may
be committed, no session may be created, and the claimed transaction must be terminally consumed
as a failed callback with sanitized failure evidence when the local audit authority is available.

Provider failure terminology is phase-specific. A provider error received before a token request
is dispatched is a pre-exchange failure. An authoritative token-endpoint rejection after dispatch
is an attempted-exchange failure. A timeout, connection loss, process loss, or other outcome that
does not authoritatively prove that no token request was dispatched is an ambiguous exchange
outcome. Section 9 assigns one terminal state to each class.

## 9. Login-transaction consumption semantics

The lifecycle distinguishes **claim**, **successful consumption**, and **terminal failure
disposition**:

- A claim is an exclusive concurrency fence. It prevents another callback from performing the
  exchange and moves the transaction out of reusable pending state.
- Successful consumption is the irreversible terminal outcome of an authorized callback. It is
  committed only with the new session and success evidence.
- A provider failure conclusively handled before any token request is dispatched makes the claim
  `INVALIDATED`. An independently expired transaction remains `EXPIRED`.
- A dispatched token request that receives an authoritative rejection makes the transaction
  `CONSUMED` as a failed callback.
- A token request that may have been dispatched, or whose completion is not authoritatively known,
  makes the transaction `CONSUMED` as a failed callback.

Consumption is never reversible, whether it records a successful callback or a failed callback
after exchange. Invalidation and expiry are also terminal for reuse. Every terminal failure state
change commits with failure evidence and no session.

Process memory, logs, timing, network assumptions, and operator inference are not authoritative
evidence that exchange did not begin. This ADR does not introduce a new durable exchange-phase
marker. Therefore, after process loss, a stale `CLAIMED` transaction is conservatively
`CONSUMED` as a failed callback unless existing authoritative durable evidence proves that no
token request could have been dispatched. It must never be released for another
authorization-code exchange.

This phase distinction explicitly supersedes only the unqualified transaction-state rule for
"IdP unavailable during callback" in the Portal Failure Matrix. `INVALIDATED` applies when
failure is conclusively pre-dispatch. Once a token request is dispatched, or dispatch cannot be
authoritatively excluded, `CONSUMED` applies as required by ADR-PORTAL-002-09. All other user,
session, audit, retry, and alert behavior in that Failure Matrix row remains unchanged.

A transaction that is missing has no record to consume. A transaction that is already consumed,
expired, or invalidated remains terminal. A browser-binding mismatch for an identifiable
transaction causes fail-closed consumption or invalidation where safe, consistent with the frozen
browser-binding decision. None of these outcomes may create or rotate a session.

## 10. Session creation and rotation semantics

A new session is persisted only in the final transaction after an explicit callback-policy
`ALLOW`.

The callback must:

- generate a new opaque authenticated session identifier and never promote state, browser-binding
  material, a login-intent value, or an attacker-supplied/pre-authentication identifier;
- store only the server-side protected representation required by the frozen session contract;
- rotate or replace any identifier required by login, reauthentication, or session-fixation
  defense;
- terminalize a replaced predecessor within the same final transaction;
- ensure revocation wins over a racing callback;
- preserve the established absolute-expiry ceiling for any successor in an existing session
  family; and
- never make the browser cookie authoritative without the committed server record.

The response cookie is necessarily outside the database transaction. It may be issued only after
commit. If the process or connection fails after commit but before the browser receives a usable
cookie, the durable session, successful consumption, and success audit remain mutually
consistent. The browser receives no assumed success and must begin a new login if it cannot
establish the committed session.

## 11. Authorization and policy-evaluation semantics

Authentication by the IdP is necessary but insufficient for Portal session creation.

Before the final transaction, the orchestration boundary must:

1. validate the external identity and authentication context;
2. resolve stable principal identity from the frozen issuer-and-subject key;
3. map only allowlisted claims to bounded roles and environment entitlements;
4. resolve server-owned tenant and current security, policy, and capability revisions;
5. enforce disabled-principal, production-identity, assurance, and configuration restrictions; and
6. obtain the callback session-creation policy result.

Only `ALLOW` authorizes finalization. Specifically:

- `DENY` creates no session;
- `NOT_APPLICABLE` creates no session;
- `INDETERMINATE` creates no session;
- `ERROR` creates no session;
- unknown or malformed results create no session; and
- an unavailable policy or required capability authority creates no session.

All such outcomes fail closed. Because policy evaluation follows exchange, they terminally
consume the claimed login transaction as a failed callback with failure evidence when possible
and require a new login. Later capability and resource access still requires independent FastAPI
authorization on every protected request; callback success is not a grant to any protected
capability.

## 12. Audit requirements

Audit evidence must be append-only, sanitized, correlation-capable, and compliant with the frozen
Portal audit envelope.

Required evidence includes:

- claim accepted or rejected at the appropriate safe level;
- callback validation failures, including safe reason classification;
- token-exchange or provider failure without code or token content;
- token or nonce validation failure;
- principal, entitlement, assurance, tenant, or production-identity rejection;
- policy `DENY`, `NOT_APPLICABLE`, `INDETERMINATE`, `ERROR`, unknown, or unavailable outcome;
- required capability authority or revision unavailable, stale, unknown, or conflicting;
- duplicate, replay, expired, consumed, invalidated, missing, or conflicting transaction outcome;
- successful login, session creation or rotation, and transaction consumption; and
- commit-unknown or recovery resolution where the frozen audit contract requires evidence.

The claim state change and required claim evidence must commit together. A terminal failure state
change and its required failure evidence must commit together. Most importantly, the successful
login/session evidence, successful transaction consumption, session persistence/rotation, and
archive-outbox entry must commit in the final transaction.

An audit record must never claim successful login, session creation, session rotation, or
successful transaction consumption before the corresponding session state is durable. If the
audit ledger cannot accept required evidence, the associated authentication state change must
roll back or remain unfinalized and the callback fails closed.

## 13. Duplicate callback and replay behavior

A duplicate or replayed callback never performs a second code exchange and never creates a second
session.

- If the transaction is actively claimed, another callback must fail closed without taking over
  the claim.
- If it is successfully consumed, the duplicate must return sanitized callback failure and leave
  the committed session unchanged.
- If it is expired or invalidated, it remains terminal and the duplicate fails.
- If the transaction does not exist or state cannot be validated, the callback fails without
  revealing whether a candidate record exists.
- Concurrent duplicates have one possible claim winner. Every other attempt fails closed.

Duplicate/replay evidence must contain only protected references and safe reason classifications.
The user recovery is a new login transaction, not reuse of the callback, code, state, binding, or
PKCE verifier.

## 14. Crash and retry behavior

### 14.1 Crash before claim commit

No claim is assumed, no code is exchanged, and no session exists. A retry may succeed only if the
authoritative login transaction remains pending, unexpired, correctly bound, and otherwise
eligible.

### 14.2 Crash after claim commit around the exchange boundary

The transaction remains exclusively claimed. Recovery must not infer from process memory, logs,
timing, or operator observation that the token request was not dispatched. This ADR introduces no
new durable exchange-phase marker.

- If existing authoritative durable evidence proves that no token request could have been
  dispatched, recovery invalidates the claim with safe failure evidence.
- Without that proof, the stale claim is an ambiguous exchange outcome and recovery consumes it as
  a failed callback with safe failure evidence.

In both cases the transaction remains terminal, the user starts a new login, and no other request
may take over or exchange the code.

### 14.3 Authoritative rejection or ambiguous outcome during exchange

A dispatched token request that receives an authoritative rejection consumes the transaction as a
failed callback. A timeout, connection loss, process loss, or unknown completion is ambiguous and
also consumes the transaction as a failed callback. Neither outcome permits a session.

The Portal must not retry the exchange blindly and must not release the claim for reuse. Failure
consumption commits with safe evidence and requires a new login.

### 14.4 Crash after successful exchange but before final commit

No session, successful consumption, token envelope, or success audit may be assumed. Transient
tokens must not be recovered from logs or browser state. The claim remains fenced and is
consumed as a failed callback by failure handling or stale-claim recovery. The user starts a new
login.

### 14.5 Final commit failure or unknown result

The callback must not return success or issue an assumed-valid cookie. It must resolve an unknown
commit by authoritative re-read using safe correlation/idempotency evidence rather than repeating
the external exchange.

- If the final commit is confirmed absent, no session or success evidence exists; the claim is
  consumed as a failed callback with failure evidence and the user starts a new login.
- If the final commit is confirmed present, the session, successful consumption, and success
  evidence are already consistent. No second finalization occurs.
- If the result cannot be resolved safely, the callback fails closed and operations must reconcile
  the authoritative store before any retry behavior is allowed.

### 14.6 Crash after final commit but before response

The durable authentication state is complete and internally consistent, but the browser must not
be told that an unconfirmed response succeeded. A duplicate callback remains invalid and cannot
create another session. The user may need to begin a new login.

Retries are state-aware recovery operations. They are never blind repetition of code exchange or
session creation.

## 15. Failure matrix

| Failure point | Authoritative state | Session/token outcome | Audit and recovery |
| --- | --- | --- | --- |
| Malformed or duplicate callback parameters | No new state | No exchange, token, or session | Sanitized rejection; safe telemetry/evidence |
| State missing, mismatched, or unresolved | No valid claim | No exchange or session | Fail closed without existence disclosure |
| Browser binding missing or mismatched | Identifiable transaction is consumed or invalidated where safe | No exchange or session | Sanitized binding failure; expire binding cookie best effort |
| Login transaction missing | Nothing to consume | No exchange or session | Sanitized failure; protected evidence only |
| Login transaction expired | Remains expired/terminal | No exchange or session | Failure evidence; new login |
| Login transaction consumed or invalidated | Remains terminal | No second exchange or session | Duplicate/replay evidence; new login |
| Concurrent claim conflict | Existing claimant remains authoritative | Losing callback performs no exchange or session work | Conflict/replay evidence; no takeover |
| Claim/audit commit fails | No claim is assumed | No exchange or session | Fail closed; retry only after authoritative re-read |
| Provider failure conclusively before token-request dispatch | Claim is terminally invalidated | No exchange, token, or session | Sanitized failure; new login |
| Dispatched token request receives authoritative rejection | Claim is terminally consumed as failed | No session; discard any transient material | Sanitized failure; new login |
| Token-request dispatch or completion is ambiguous | Claim is terminally consumed as failed | No session; discard any transient material | Sanitized failure; new login |
| Stale claim after process loss without durable proof of pre-dispatch failure | Claim is terminally consumed as failed | No exchange retry or session | Safe recovery evidence; new login |
| Stale claim with durable proof that no token request could have been dispatched | Claim is terminally invalidated | No exchange retry or session | Safe recovery evidence; new login |
| ID-token, nonce, issuer, signature, audience, time, size, or assurance validation fails | Claim is terminally consumed as failed | No token persistence or session | Authentication failure evidence |
| Principal disabled, ambiguous, or invalid | Claim is terminally consumed as failed | No session | Identity failure evidence |
| Entitlement, tenant, or production-identity validation fails | Claim is terminally consumed as failed | No session | Sanitized authorization/configuration evidence |
| Policy returns `DENY`, `NOT_APPLICABLE`, `INDETERMINATE`, `ERROR`, unknown, or unavailable | Claim is terminally consumed as failed | No session | Required decision/failure evidence |
| Required capability authority or revision is unavailable, stale, unknown, or conflicting | Claim is terminally consumed as failed | No session | Required authority/revision evidence |
| Final revalidation or revision check conflicts | Claim is terminally consumed as failed | No session | Conflict evidence; new login |
| Session, consumption, or success-audit write fails | Entire final transaction rolls back; resolve then consume claim as failed if absent | No assumed session or token persistence | Fail closed; resolve commit state before recovery |
| Audit ledger unavailable during finalization | Final transaction rolls back; claim stays fenced pending recovery | No session | Alert; consume as failed with evidence after recovery |
| Final commit succeeds but cookie/redirect delivery fails | Session, consumption, and success audit all remain committed | No browser-assumed success; server token remains protected | No replay; user may need a new login |
| Duplicate callback after success | Original committed state unchanged | No second session or rotation | Replay evidence; new login |
| Process crash after exchange and before commit | Claim remains fenced until terminal failure consumption | No session or durable token assumed | Consume as failed with evidence; new login |

Every matrix row fails closed. No cleanup or compensating action may synthesize an `ALLOW`, restore
a transaction to reusable pending state, or create a session after a failed callback.

After every terminal callback outcome, the browser-binding cookie is expired on a best-effort
basis when a browser response can be produced. Cleanup failure does not change the authentication
outcome, reopen the login transaction, create a session, affect replay protection, or grant
authorization. Authoritative server-side terminal state, not browser-cookie cleanup, enforces
one-use behavior.

## 16. Privacy and token-retention requirements

The callback must preserve all frozen token and audit privacy rules:

- the browser never receives an access token, refresh token, or ID token;
- browser state, the binding cookie, and the session cookie never contain provider tokens or
  authoritative entitlement data;
- authorization codes, state values, browser-binding values, nonces, PKCE verifiers, raw claims,
  tokens, token responses, and secrets never enter logs, traces, metrics labels, errors, or audit
  payloads;
- PKCE verifier plaintext and exchanged token material exist in process memory only for the
  shortest required interval;
- transient token material is discarded on every non-success outcome;
- provider tokens are persisted only when the frozen lifecycle requires them, only encrypted
  server-side, and only as part of the successful final transaction; and
- failure recovery does not persist token material merely to make a callback retryable.

Audit uses protected stable references and bounded reason classifications. It must not include
sensitive callback query values or upstream response bodies.

## 17. Forbidden behaviors

The following are forbidden:

- selecting more than one callback model;
- allowing the route to bypass the orchestration boundary;
- treating state, browser binding, return path, navigation, or any browser value as a grant;
- exchanging the authorization code before authoritative state and browser-binding validation
  plus exclusive claim;
- holding a local database transaction or lock across an external IdP call;
- treating a successful token exchange as Portal authorization;
- creating or rotating a session for any result other than explicit `ALLOW`;
- creating a session before final policy evaluation;
- persisting authoritative principal, entitlement, session, or token state on a callback that
  later fails authorization;
- marking successful consumption before the final atomic transaction;
- reversing any consumption or reopening a claimed transaction after exchange may have occurred;
- committing a session without the corresponding success audit and archive-outbox evidence;
- committing success audit before or without the corresponding session;
- returning or redirecting success before session persistence, successful transaction
  consumption, and success audit are durably committed;
- assuming a failed or timed-out local commit succeeded;
- blindly retrying an authorization-code exchange, finalization, or session creation;
- extending an established absolute session lifetime during callback rotation;
- returning OIDC tokens to the browser or retaining transient tokens after failure;
- using development identity in production; or
- interpreting this ADR as runtime or production authorization.

## 18. New governance decisions introduced

Because the repository supplied only one-line model descriptions, this ADR introduces these new
governance decisions:

1. The expanded comparison definitions in Section 3.
2. Selection of Model C as the sole callback model.
3. One logical orchestration boundary owns every authoritative callback outcome.
4. Callback processing uses a short claim transaction, a transaction-free external interval, and
   a final atomic local transaction.
5. State and browser binding become actionable only when their authoritative checks and exclusive
   claim commit.
6. A claim is distinct from consumption; pre-dispatch provider failure invalidates, while an
   authoritative rejection after dispatch or any ambiguous dispatch/completion outcome consumes
   the transaction as failed.
7. Session persistence/rotation, successful login-transaction consumption, success audit, and
   audit outbox commit atomically.
8. Failure finalization consumes, invalidates, or expires a claimed transaction according to the
   exchange boundary, commits with failure evidence, and never creates a session.
9. Only explicit `ALLOW` may create a session; all other and unavailable policy results create
   none.
10. Browser-visible success and session-cookie issuance occur only after confirmed final commit.
11. Commit-unknown recovery uses authoritative re-read and never repeats the external exchange
    blindly.
12. No new durable exchange-phase marker is introduced. After process loss, a stale claim without
    authoritative durable proof of pre-dispatch failure is conservatively consumed as failed.
13. The phase distinction in Section 9 supersedes only the unqualified transaction-state rule for
    "IdP unavailable during callback" in the Portal Failure Matrix; its remaining behavior is
    unchanged, and ADR-PORTAL-002-09 continues to govern attempted token-exchange failure.
14. Browser-binding cleanup is best-effort after every terminal callback outcome and never changes
    authentication, authorization, transaction state, session creation, or replay protection.

These decisions clarify Blocker 6. They do not reopen or replace the governance decisions for
versioned migrations, PKCE-verifier protection, browser binding, or login intent.

## 19. Out-of-scope items

This ADR does not select or freeze:

- concrete Python or FastAPI function names;
- an interface signature, ORM class, database column, or runtime module layout;
- SQL statements or local statement order within an atomic commit;
- new HTTP error identifiers or response models;
- exact provider timeout, retry-count, claim-lease, or cleanup-schedule constants;
- an additional cryptographic algorithm beyond the already frozen contracts;
- a logging, tracing, policy, database, or networking library;
- migration content or runtime database permissions;
- generated OpenAPI or TypeScript changes;
- tests, CI, deployment, infrastructure, production credentials, HA, or scaling;
- refresh, logout, step-up, resource authorization, or capability implementation beyond their
  interaction with the frozen callback invariants;
- reopening Action 1 through Action 4; or
- runtime implementation or production deployment authorization.

Concrete endpoint names already frozen by the PR-PORTAL-002 design may be referenced without
adding them to the current runtime contract.

## 20. Consequences

### Positive

- Callback success has one authoritative owner and one durable completion point.
- The design does not pretend that an IdP network call can join a local ACID transaction.
- Replay and concurrency are fenced before token exchange.
- A session cannot exist without its successful audit evidence.
- Successful audit cannot exist without its session and consumed transaction.
- Policy or identity failure leaves no partial authenticated session state.
- Crash and duplicate behavior are deterministic and fail closed.
- The route remains small and cannot independently become an authentication authority.

### Trade-offs

- A durable claim can make an authorization code unusable after a crash even when the provider
  did not consume it; the safe recovery is a new login.
- Claim recovery and commit-unknown reconciliation require operational evidence and tests.
- A crash after final commit but before cookie delivery can leave a valid, auditable server-side
  session that the browser cannot use.
- The external exchange and local commit cannot provide global exactly-once execution. The
  security property is instead at-most-one exchange attempt per claim and exactly one local
  finalized outcome.

### Required conformance effect

Any future runtime implementation must prove the claim fence, external boundary, atomic final
commit, duplicate behavior, crash behavior, redaction, and fail-closed policy matrix. This
requirement is prospective only; this proposed ADR does not authorize that work.

## 21. Status and runtime authorization statement

**Status: PROPOSED**

**Production deployment authorization: NOT GRANTED**

**Runtime implementation: NOT AUTHORIZED**

Model C becomes the callback sequencing decision only after the required governance review,
approval, and merge. Until then, `GC-PORTAL-002-01` Blocker 6 remains unresolved for runtime
purposes.

This ADR grants no permission to modify runtime code, migrations, generated contracts, tests, CI,
or deployment configuration. It grants no production pilot, credentials, user enablement, or
deployment authority. Runtime implementation must not resume merely because this proposal or its
pull request exists.

## 22. Review evidence

This decision was reviewed against:

- `docs/governance/decisions/GC-PORTAL-002-01-oidc-sequencing-resolution.md`
  - exact Blocker 6 wording and the repository's one-line Model A/B/C definitions;
  - Blocker 7 requirement that session creation and audit evidence be atomic;
- `docs/governance/decisions/ADR-PORTAL-002-08-versioned-database-migrations.md`
  - versioned security-store boundary and separated runtime/migration authority;
- `docs/governance/decisions/GC-PORTAL-002-02-pkce-verifier-protection.md`
  - encrypted verifier lifecycle, just-in-time decryption, and no plaintext persistence;
- `docs/governance/decisions/ADR-PORTAL-002-09-browser-binding.md`
  - selected per-login HttpOnly binding cookie, pre-exchange validation, terminal failure, and
    privacy requirements;
- `docs/governance/decisions/GC-PORTAL-002-04-login-intent-contract.md`
  - login intent ends at login initiation and is not callback authority;
- `docs/portal/pr-portal-002-design-freeze.md`
  - frozen invariants, callback order, session state machines, transactionality, audit, failure,
    implementation-order, and merge-gate requirements;
- `docs/portal/pr-portal-002-authorization-matrix.md`
  - deny-by-default gates and only-`ALLOW` semantics;
- `docs/portal/pr-portal-002-threat-model.md`
  - callback replay, login CSRF, session fixation, token leakage, store outage, and audit-loss
    threats;
- `docs/portal/pr-portal-002-failure-matrix.md`
  - callback/provider failures, write-unknown resolution, duplicate callback, replay, and
    fail-closed recovery;
- `docs/adr/portal-002-01-oidc-pkce-server-token-handling.md`;
- `docs/adr/portal-002-02-server-session-store-lifecycle.md`;
- `docs/adr/portal-002-03-authorization-policy-model.md`;
- `docs/adr/portal-002-04-capability-registry-precedence.md`;
- `docs/adr/portal-002-05-environment-tenant-context.md`;
- `docs/adr/portal-002-06-security-audit-architecture.md`;
- `docs/adr/portal-002-07-cookie-csrf-strategy.md`;
- `docs/portal/api-contract.md` and the checked-in current OpenAPI artifact, as read-only evidence
  of the current foundation surface; and
- `docs/governance/decisions/GD-001-portal-002-implementation-exception.md`, including its
  suspension rule for an unresolved security-critical invariant and its explicit denial of
  production deployment authority.

Conformance review must confirm that this change adds only this governance document, changes no
frozen document or runtime artifact, selects exactly one model, keeps IdP calls outside local
transactions, atomically couples session persistence with successful transaction consumption and
success audit, preserves all fail-closed invariants, leaves Actions 1 through 4 closed, and grants
no runtime or production authorization.
