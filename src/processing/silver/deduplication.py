"""Deterministic CDC event deduplication and latest-state ordering."""

from __future__ import annotations

from processing.silver.models import NormalizedCdcEvent, QualityCode


def deduplicate_events(
    events: list[NormalizedCdcEvent],
) -> tuple[list[NormalizedCdcEvent], list[tuple[NormalizedCdcEvent, QualityCode]]]:
    accepted: list[NormalizedCdcEvent] = []
    rejected: list[tuple[NormalizedCdcEvent, QualityCode]] = []
    coordinates: set[tuple[str, int, int]] = set()
    event_ids: set[str] = set()
    for event in sorted(events, key=event_order_key):
        if event.coordinate in coordinates:
            rejected.append((event, QualityCode.DUPLICATE_COORDINATE))
            continue
        if event.event_id in event_ids:
            rejected.append((event, QualityCode.DUPLICATE_EVENT))
            continue
        coordinates.add(event.coordinate)
        event_ids.add(event.event_id)
        accepted.append(event)
    return accepted, rejected


def event_order_key(event: NormalizedCdcEvent) -> tuple[object, ...]:
    return (
        event.kafka_topic,
        event.kafka_partition,
        event.kafka_offset,
        event.source_lsn if event.source_lsn is not None else -1,
        event.source_ts or event.event_time,
        event.connector_ts or event.event_time,
        event.ingested_at,
        event.event_id,
    )


def is_newer_than_state(event: NormalizedCdcEvent, state: dict[str, object]) -> bool:
    prior_topic = str(state.get("kafka_topic", ""))
    prior_partition = int(state.get("kafka_partition", -1))
    prior_offset = int(state.get("kafka_offset", -1))
    if prior_topic == event.kafka_topic and prior_partition == event.kafka_partition:
        return event.kafka_offset > prior_offset
    return not (
        prior_topic
        and prior_topic == event.kafka_topic
        and prior_partition != event.kafka_partition
    )
