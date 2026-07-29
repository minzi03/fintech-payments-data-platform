"""Durable, fail-closed recovery for ambiguous stale callback claims."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from portal_api.audit.ledger import AuditLedger
from portal_api.audit.models import AuditEvent, AuditEventType
from portal_api.db.metadata import oidc_login_transactions
from portal_api.db.unit_of_work import local_transaction


class CallbackRecovery:
    def __init__(
        self,
        *,
        engine: Engine,
        audit_ledger: AuditLedger | None = None,
    ) -> None:
        self._engine = engine
        self._audit = audit_ledger or AuditLedger()

    def recover_expired_claims(self, *, limit: int = 100) -> int:
        """Consume expired CLAIMED rows conservatively; never release them for exchange."""
        if limit <= 0:
            raise ValueError("Recovery limit must be positive")
        now = datetime.now(UTC)
        recovered = 0
        with local_transaction(self._engine) as connection:
            claims = (
                connection.execute(
                    select(oidc_login_transactions)
                    .where(
                        oidc_login_transactions.c.status == "CLAIMED",
                        oidc_login_transactions.c.expires_at <= now,
                    )
                    .order_by(oidc_login_transactions.c.expires_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
                .mappings()
                .all()
            )
            for claim in claims:
                result = connection.execute(
                    update(oidc_login_transactions)
                    .where(
                        oidc_login_transactions.c.transaction_id == claim["transaction_id"],
                        oidc_login_transactions.c.status == "CLAIMED",
                        oidc_login_transactions.c.version == claim["version"],
                    )
                    .values(
                        status="CONSUMED",
                        consumed_at=now,
                        updated_at=now,
                        version=claim["version"] + 1,
                    )
                )
                if result.rowcount != 1:
                    raise RuntimeError("Stale callback claim recovery lost its authority")
                self._audit.append(
                    connection,
                    AuditEvent(
                        event_type=AuditEventType.LOGIN_FAILED,
                        actor_type="SYSTEM",
                        action="portal.authenticate",
                        reason_code="OIDC_STALE_CLAIM_AMBIGUOUS",
                        correlation_id=f"recovery-{claim['transaction_id']}",
                        request_id=f"recovery-{claim['transaction_id']}",
                        outcome="DENIED",
                        safe_metadata={
                            "transaction_reference": str(claim["transaction_id"]),
                            "recovery_basis": "durable_expiry",
                        },
                    ),
                )
                recovered += 1
        return recovered
