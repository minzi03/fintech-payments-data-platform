"""Non-blocking Silver referential classification for late-arriving CDC rows."""

from __future__ import annotations

from datetime import datetime

from processing.silver.models import NormalizedCdcEvent, UnresolvedReference

REFERENCE_RULES = {
    "accounts": (("customer_id", "customers"),),
    "payment_transactions": (
        ("customer_id", "customers"),
        ("account_id", "accounts"),
        ("merchant_id", "merchants"),
    ),
    "refunds": (("transaction_id", "payment_transactions"),),
}


def classify_unresolved_references(
    event: NormalizedCdcEvent,
    state_row: dict[str, object],
    known_keys: dict[str, set[str]],
    *,
    observed_at: datetime,
) -> list[UnresolvedReference]:
    unresolved = []
    for field, target_entity in REFERENCE_RULES.get(event.entity_name, ()):
        raw_value = state_row.get(field)
        if raw_value in {None, ""} and field == "merchant_id":
            continue
        if raw_value in {None, ""}:
            continue
        value = str(raw_value)
        if value not in known_keys.get(target_entity, set()):
            unresolved.append(
                UnresolvedReference(
                    entity_name=event.entity_name,
                    business_key=event.business_key,
                    reference_entity=target_entity,
                    reference_key=value,
                    reference_field=field,
                    classification="TEMPORARILY_UNRESOLVED",
                    source_event_id=event.event_id,
                    processing_run_id=event.processing_run_id,
                    observed_at=observed_at,
                )
            )
    return unresolved
