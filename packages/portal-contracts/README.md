# Portal API contracts

`openapi/portal-api-v1.json` is generated deterministically from the FastAPI application.
`src/generated/` is generated from that checked-in schema and must never be edited by hand.

From the repository root:

```bash
make portal-openapi
make portal-client
make portal-contract-check
```

The contract is additive within `/v1`. Breaking changes require a new API version and an explicit
migration window.
